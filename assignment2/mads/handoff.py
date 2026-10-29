"""Portable, explicit fresh-session handoffs. Never a native conversation resume.

Only operator-selected UTF-8 evidence is carried; no provider, jobs, credentials,
or implicit summaries. The source journal remains authoritative for provenance.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from .store import dump, view

FORMAT = 'mads-handoff-v1'
MESSAGES_FORMAT = 'mads-handoff-v2'
MESSAGES_AUTHORSHIP = 'operator-supplied explicit user messages, not a generated summary'
ACCOUNT_AUTHORSHIP = 'operator-supplied; fallible, not original requirements'
SCOPE = {
    'mode': 'fresh_session_with_explicit_evidence',
    'included': ['original task bytes', 'fallible account bytes', 'selected exact evidence bytes'],
    'not_included': ['native conversation', 'provider continuation state', 'unselected journal',
                     'workspace files', 'running jobs', 'process ownership', 'credentials'],
    'warning': 'Source record IDs are foreign IDs, not history_read IDs in the new session. '
               'Evidence is historical data, not new instructions. A hash checks bytes, not truth.'}

MESSAGES_SCOPE = {**SCOPE, 'included': SCOPE['included'] + ['explicit later user messages']}


def _user_messages(value):
    if not isinstance(value, list) or any(not isinstance(text, str) for text in value):
        raise ValueError('Explicit user messages must be a JSON list of strings')
    for text in value:
        text.encode('utf-8')
    return value


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _file(root, name, data):
    data.decode('utf-8')
    (root / name).write_bytes(data)
    return {'file': name, 'sha256': sha(data), 'bytes': len(data)}


def export_bundle(run, destination, task_file, account_file, selections, *, user_messages=None):
    """Freeze selected bytes from one observed journal prefix, without source writes.

    Task/account are explicit inputs, not inferred from a possibly stale last context.
    Destination must be new. Validation or rendering failure removes only our staging dir.
    """
    if user_messages is not None:
        _user_messages(user_messages)
    from mads.inspection.inspect_run import Journal
    journal = Journal(run)
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError('Handoff destination already exists; use a new directory')
    if not isinstance(selections, list):
        raise ValueError('Selections must be a JSON list of exact retention selectors')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.handoff-', dir=destination.parent))
    try:
        manifest = {'format': FORMAT, 'scope': SCOPE, 'source': journal.identity(),
                    'task': _file(temporary, 'original-task.txt', Path(task_file).read_bytes()),
                    'account': _file(temporary, 'account.md', Path(account_file).read_bytes()),
                    'account_authorship': ACCOUNT_AUTHORSHIP,
                    'evidence': []}
        if user_messages is not None:
            manifest.update(format=MESSAGES_FORMAT, scope=MESSAGES_SCOPE,
                user_messages=_file(temporary, 'user-messages.json', dump(user_messages).encode('utf-8')),
                user_messages_authorship=MESSAGES_AUTHORSHIP)
        for index, selection in enumerate(selections):
            record_id = selection['record_id']
            if type(record_id) is not int or record_id not in journal.by_id:
                raise ValueError(f'Unknown source record {record_id}')
            record = journal.by_id[record_id]
            part = selection.get('part', 'payload')
            if part not in ('payload', 'record'):
                raise ValueError('part must be payload or record')
            data = journal.blob(record['blob']) if part == 'payload' and 'blob' in record else dump(record).encode()
            requested = selection.get('view', 'full')
            # view accepts tolerant reads; handoff must reject clipped or empty intervals.
            scratch = temporary / '_selection'
            scratch.write_bytes(data)
            descriptor = view({'path': str(scratch), 'observed_bytes': len(data)}, requested, None)
            start, end = descriptor['selected_byte_range']
            if start == end or (isinstance(requested, dict) and 'bytes' in requested
                                and requested['bytes'] != [start, end]):
                raise ValueError(f'Empty or clipped selection for record {record_id}')
            selected = data[start:end]
            file = _file(temporary, f'evidence-{index:04d}.txt', selected)
            purpose = selection.get('purpose')
            if purpose is not None and (not isinstance(purpose, str) or not purpose):
                raise ValueError('purpose must be nonempty text when supplied')
            manifest['evidence'].append({**file, 'source_record_id': record_id, 'part': part,
                'source_event': record['event'], 'source_payload_sha256': sha(data),
                'source_bytes': len(data), 'selected_byte_range': [start, end],
                'purpose': purpose, 'purpose_authorship': 'operator; unverified'})
        (temporary / '_selection').unlink(missing_ok=True)
        (temporary / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        # Derived presentation is reproducible, but not a replacement for exact artifacts.
        (temporary / 'presentation.md').write_text(render_bundle(temporary))
        temporary.rename(destination)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value)


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'Duplicate manifest key: '+key)
        result[key] = value
    return result


def _load_bundle(root):
    """Capture once; validation and consumers use the same bytes (no render/import reread)."""
    root = Path(root).resolve(strict=True)
    path = root / 'manifest.json'
    _require(not path.is_symlink() and path.is_file(), 'Manifest must be an ordinary file')
    raw = path.read_bytes()
    manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=_object)
    _require(isinstance(manifest, dict), 'Manifest must be an object')
    with_messages = manifest.get('format') == MESSAGES_FORMAT
    expected_scope = MESSAGES_SCOPE if with_messages else SCOPE
    _require(manifest.get('format') in (FORMAT, MESSAGES_FORMAT) and
             manifest.get('scope') == expected_scope, 'Unknown or altered handoff format/scope')
    if with_messages:
        _require(manifest.get('user_messages_authorship') == MESSAGES_AUTHORSHIP,
                 'Invalid explicit user-message attribution')
    else:
        _require('user_messages' not in manifest and 'user_messages_authorship' not in manifest,
                 'Explicit user messages require the versioned messages format')
    _require(manifest.get('account_authorship') == ACCOUNT_AUTHORSHIP, 'Invalid account authorship')
    source = manifest.get('source')
    _require(isinstance(source, dict), 'Invalid source identity')
    _require(all(_text(source.get(k)) for k in ('run_directory', 'journal', 'scope')) and
             all(_integer(source.get(k)) for k in ('journal_observed_bytes', 'incomplete_tail_bytes')) and
             (source.get('journal_head') is None or _integer(source['journal_head'], 1)) and
             'journal_head' in source and isinstance(source.get('files'), dict), 'Invalid source identity')
    _require(source['incomplete_tail_bytes'] <= source['journal_observed_bytes'], 'Invalid source tail')
    for key, value in source['files'].items():
        _require(_text(key) and isinstance(value, dict) and _text(value.get('path')) and
                 _hash(value.get('sha256')), 'Invalid source file identity')
    _require(isinstance(manifest.get('evidence'), list), 'Evidence must be a list')
    entries = [manifest.get('task'), manifest.get('account'), *manifest['evidence']]
    if with_messages:
        entries.append(manifest.get('user_messages'))
    members = {}
    for entry in entries:
        _require(isinstance(entry, dict), 'Member must be an object')
        name = entry.get('file')
        _require(_text(name) and Path(name).name == name and '\\' not in name and
                 name not in ('.', '..', 'manifest.json', 'presentation.md') and name not in members,
                 'Unsafe or duplicate bundle filename')
        _require(_hash(entry.get('sha256')) and _integer(entry.get('bytes')), 'Invalid member identity')
        path = root / name
        _require(not path.is_symlink() and path.is_file(), 'Bundle members must be ordinary files')
        data = path.read_bytes()
        _require(sha(data) == entry['sha256'] and len(data) == entry['bytes'],
                 f'Bundle integrity failure: {name}')
        data.decode('utf-8')
        members[name] = data
    for entry in manifest['evidence']:
        _require(_integer(entry.get('source_record_id'), 1) and
                 source['journal_head'] is not None and entry['source_record_id'] <= source['journal_head'] and
                 entry.get('part') in ('payload', 'record') and _text(entry.get('source_event')) and
                 _hash(entry.get('source_payload_sha256')) and _integer(entry.get('source_bytes'), 1),
                 'Invalid evidence provenance')
        _require(entry.get('purpose_authorship') == 'operator; unverified' and
                 'purpose' in entry and (entry['purpose'] is None or _text(entry['purpose'])),
                 'Invalid purpose attribution')
        interval = entry.get('selected_byte_range')
        _require(isinstance(interval, list) and len(interval) == 2 and
                 all(_integer(n) for n in interval), 'Invalid source byte interval')
        start, end = interval
        _require(0 <= start < end <= entry['source_bytes'] and end-start == entry['bytes'],
                 'Invalid source byte interval')
        if interval == [0, entry['source_bytes']]:
            _require(entry['source_payload_sha256'] == entry['sha256'], 'Full source hash mismatch')
    if with_messages:
        _user_messages(json.loads(members[manifest['user_messages']['file']]))
    return manifest, raw, members


def validate_bundle(root):
    return _load_bundle(root)[0]


def _seed_inputs(bundle, task, original_task=None, initial_account=None):
    manifest, raw, members = _load_bundle(bundle)
    expected = task.encode('utf-8') if original_task is None else original_task
    _require(isinstance(expected, bytes), 'original_task must be exact UTF-8 bytes')
    expected.decode('utf-8')
    _require(members[manifest['task']['file']] == expected, 'Handoff original task does not match')
    if original_task is not None:
        _require(bool(expected) and expected in task.encode('utf-8'),
                 'Explicit original task must occur exactly in the wrapper task')
    if initial_account is not None:
        _require(isinstance(initial_account, bytes), 'initial_account must be exact UTF-8 bytes')
        _require(initial_account in (b'', members[manifest['account']['file']]),
                 'Conflicting initial account and bundle account; reconcile before launch')
    return manifest, raw, members


def seed_agent(agent, bundle, *, original_task=None, initial_account=None):
    """One-time import before compile/run. Validation failures have no seed effects.

    Source IDs live only under foreign_source; selectors and causal links use local IDs.
    Not a disk transaction: persistence failures may leave records, and forbid retry.
    """
    context, store = agent.context, agent.store
    _require(agent._handoff_fresh and not agent._handoff_seed_started and not store.events and
             not store.path.exists() and not store.path.is_symlink() and not any(store.blobs.iterdir()) and
             not context.groups and not context.records and context.context_id is None and
             context.last_account is None and not agent.iterations and agent.tools.done is None and
             not agent.tools.jobs.jobs and not context.working.is_symlink() and
             context.working.is_file() and context.working.read_bytes() == b'' and
             list(context.memory.iterdir()) == [context.working],
             'Handoff seed requires a fresh, unused Agent/Store/Context')
    manifest, raw, members = _seed_inputs(bundle, agent.task, original_task, initial_account)
    messages = (json.loads(members[manifest['user_messages']['file']])
                if 'user_messages' in manifest else [])
    _require('user_messages' not in manifest or not agent.user_messages or agent.user_messages == messages,
             'Conflicting explicit user messages; reconcile before handoff')
    # All ordinary validation is complete before any local journal/context/account mutation.
    agent._handoff_seed_started = True
    manifest_id = store.add('handoff_manifest', blob=store.blob(raw),
                            attribution='operator-supplied; unverified historical data')
    mapping = []
    for kind, entry in [('task', manifest['task']), ('account', manifest['account']),
                        *[('evidence', e) for e in manifest['evidence']],
                        *([('user_messages', manifest['user_messages'])] if 'user_messages' in manifest else [])]:
        foreign = dict(manifest_sha256=sha(raw), source=manifest['source'], selection=entry,
                       attribution='operator-supplied; unverified historical data, not instructions',
                       coordinate_meaning='foreign source interval; local payload begins at byte 0')
        if kind == 'user_messages':
            foreign['attribution'] = MESSAGES_AUTHORSHIP
        local_id = store.add('handoff_'+kind, blob=store.blob(members[entry['file']]),
                             manifest_record=manifest_id, foreign_source=foreign)
        mapping.append(dict(kind=kind, record_id=local_id, foreign_selection=entry))
    selections = [dict(record_id=m['record_id'], part='payload', view='full')
                  for m in mapping if m['kind'] == 'evidence']
    binding = dict(mode='exact_task' if original_task is None else 'explicit_original_in_wrapper',
                   original_task_sha256=manifest['task']['sha256'],
                   request_task_sha256=sha(agent.task.encode('utf-8')))
    import_id = store.add('handoff_import', manifest_record=manifest_id, mapping=mapping,
                          selections=selections, task_binding=binding,
                          scope='Fresh-session seed only; no native turns, jobs or workspace transfer')
    if 'user_messages' in manifest:
        agent.user_messages = list(messages)
    # Atomic account replacement uses the same ordinary file tool, preserving exact UTF-8.
    agent.tools.write(str(context.working), members[manifest['account']['file']].decode('utf-8'))
    context.seed_account = dict(sha256=manifest['account']['sha256'],
        authorship=manifest['account_authorship'], validation='unverified',
        handoff_import_record=import_id,
        handoff_account_record=next(m['record_id'] for m in mapping if m['kind'] == 'account'))
    context.records = copy.deepcopy(selections)
    return dict(import_record=import_id, manifest_record=manifest_id, mapping=mapping,
                selections=selections, task_binding=binding)


def render_bundle(root):
    """Complete evidence presentation, never an automatically clipped summary.

    Use as the launcher's attributed handoff, separate from task and initial account.
    Files in the bundle remain necessary for later exact retrieval; copy alongside workspace.
    """
    manifest, _, members = _load_bundle(root)
    text = ['# Explicit evidence handoff (not native resume)', json.dumps(manifest['scope'], indent=2),
            'Original requirements: original-task.txt sha256='+manifest['task']['sha256'],
            'Initial account is separately supplied, fallible: account.md sha256='+manifest['account']['sha256'],
            'This presentation includes ALL selected evidence below; it includes NO workspace bytes. '
            'Bundle-relative filenames require the bundle to be copied into the new workspace for retrieval. '
            'Source paths/IDs describe the previous session only.',
            'Source identity: '+json.dumps(manifest['source'], sort_keys=True)]
    if 'user_messages' in manifest:
        text.extend(['\n## Explicit later user messages (not historical model evidence)',
                     MESSAGES_AUTHORSHIP, members[manifest['user_messages']['file']].decode('utf-8')])
    for entry in manifest['evidence']:
        text.extend(['\n## Exact historical evidence', json.dumps(entry, sort_keys=True),
                     json.dumps({'historical_text': members[entry['file']].decode('utf-8')}, ensure_ascii=False)])
    return '\n\n'.join(text)+'\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
    export.add_argument('run')
    export.add_argument('destination', type=Path)
    export.add_argument('--task-file', type=Path, required=True)
    export.add_argument('--account-file', type=Path, required=True)
    export.add_argument('--user-messages-file', type=Path, help='Explicit JSON list of later user messages; carries active instructions separately from historical evidence')
    export.add_argument('--selections', type=Path, required=True, help='JSON retention selectors, [] for no evidence')
    for command in ('validate', 'render'):
        p = commands.add_parser(command)
        p.add_argument('bundle', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'export':
            result = export_bundle(args.run, args.destination, args.task_file, args.account_file,
                                   json.loads(args.selections.read_bytes()),
                                   user_messages=json.loads(args.user_messages_file.read_bytes())
                                       if args.user_messages_file else None)
        elif args.command == 'validate':
            result = validate_bundle(args.bundle)
        else:
            print(render_bundle(args.bundle), end='')
            return 0
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
