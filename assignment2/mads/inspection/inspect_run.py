"""Read-only journal inspection. No provider calls, credentials, or implicit replay.

python mads/inspection/inspect_run.py RUN_DIRECTORY summary
python mads/inspection/inspect_run.py RUN_DIRECTORY timeline --last 20
python mads/inspection/inspect_run.py RUN_DIRECTORY request --record latest --out DEST
python mads/inspection/inspect_run.py RUN_DIRECTORY accounts
python -m mads inspect RUN_DIRECTORY decision --record latest --item 1

A live journal is one observed prefix, not an atomic filesystem/process snapshot.
Exports are inspection artifacts, NOT resumable continuations.
"""
import argparse
from collections import Counter
import difflib
import hashlib
import json
from pathlib import Path
import sys
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mads.runs import resolve_run
from mads.store import dump


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Journal:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.run = resolve_run(self.root)
        self.journal = self.run / 'journal'
        data = (self.journal / 'events.jsonl').read_bytes()
        self.observed_bytes = len(data)
        self.incomplete_tail_bytes = len(data) - (data.rfind(b'\n') + 1)
        complete = data[:len(data) - self.incomplete_tail_bytes] if self.incomplete_tail_bytes else data
        self.observed_sha256 = sha(data)
        self.events, self.record_ranges = [], {}
        position = 0
        for line in complete.splitlines(keepends=True):
            event = json.loads(line)
            self.events.append(event)
            self.record_ranges[event['id']] = [position, position + len(line)]
            position += len(line)
        self.by_id = {e['id']: e for e in self.events}
        if len(self.by_id) != len(self.events):
            raise ValueError('Duplicate journal record IDs')

    def blob(self, key):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('Invalid blob identity')
        path = self.journal / 'blobs' / key
        data = path.read_bytes()
        if sha(data) != key:
            raise ValueError(f'Blob integrity failure: {path}')
        return data

    def payload(self, event):
        return json.loads(self.blob(event['blob'])) if 'blob' in event else event

    def identity(self):
        files = {}
        for name in ('source-manifest.json', 'workspace-manifest.json', 'runner-manifest.json',
                     'case/task.txt', 'case/handoff.txt', 'case/account.txt', 'case/config.json',
                     'case/original-task.txt', 'case/initial-account.txt', 'run-manifest.json'):
            p = self.root / name
            if p.is_file():
                files[name] = {'path': str(p), 'sha256': sha(p.read_bytes())}
        manifest = self.run/'run-manifest.json'
        if manifest.is_file():
            files['run-manifest.json']={'path':str(manifest),'sha256':sha(manifest.read_bytes())}
        return {'run_directory': str(self.root), 'journal': str(self.journal / 'events.jsonl'),
                'journal_observed_bytes': self.observed_bytes,
                'journal_head': self.events[-1]['id'] if self.events else None,
                'incomplete_tail_bytes': self.incomplete_tail_bytes, 'files': files,
                'scope': 'observed journal prefix; not atomic with workspace or process state'}

    def summary(self):
        counts = Counter(e['event'] for e in self.events)
        tools = Counter(e.get('tool_name', e.get('name', '?')) for e in self.events
                        if e['event'] == 'invocation')
        usage = Counter()
        responses_without_usage = 0
        for event in self.events:
            if event['event'] != 'response':
                continue
            body = self.payload(event)
            values = body.get('usage')
            if not values:
                responses_without_usage += 1
            else:
                usage.update({k: v for k, v in values.items() if type(v) is int})
        manifests = [e for e in self.events if e['event'] == 'context_manifest']
        result_file = self.run / 'result.json'
        def outcome(path):
            if not path.exists(): return None
            try:
                value=json.loads(path.read_bytes())
                if not isinstance(value,dict): raise ValueError('Outcome must be an object')
                return value
            except (OSError,ValueError) as exc:
                return {'inspection_error':str(exc),'path':str(path)}
        host_file=self.run.parent/'result.json'
        return {**self.identity(), 'events': dict(counts), 'tools': dict(tools),
                'recorded_response_usage': dict(usage), 'responses_without_usage': responses_without_usage,
                'latest_context': manifests[-1] if manifests else None,
                'last_recorded_at':self.events[-1].get('timestamp') if self.events else None,
                'host_result':outcome(host_file) if self.run.name=='run' else None,
                'result_path': str(result_file) if result_file.exists() else None,
                'result':outcome(result_file)}


    def timeline(self, last=20):
        if last < 0:
            raise ValueError('last must be nonnegative')
        rows = []
        for event in self.events:
            kind = event['event']
            if kind == 'invocation':
                arguments = event.get('raw_arguments', '')
                rows.append({**{k: v for k, v in event.items() if k != 'raw_arguments'},
                             'arguments_chars': len(arguments), 'arguments_preview': arguments[:400],
                             'arguments_complete': len(arguments) <= 400, 'detail_record': event['id']})
            elif kind in ('tool_result', 'run_end', 'context_restart', 'context_configuration', 'context_policy',
                          'capacity_correction','capacity_retry','api_retry','api_error'):
                result = event.get('result', event)
                rows.append({'id': event['id'], 'event': kind, 'timestamp':event.get('timestamp'),
                             **{k: result[k] for k in ('tool_name', 'status', 'effects', 'error',
                                'context_change', 'account_update', 'summary', 'applied', 'arguments',
                                'agent_outcome','harness_outcome','cleanup_outcome','export_outcome',
                                'attempt','delay_seconds','next_attempt','previous','proposed') if k in result},
                             'detail_record': event['id']})
        return rows[-last:] if last else rows

    def accounts(self):
        rows, previous = [], ''
        for event in self.events:
            if event['event'] != 'context_manifest':
                continue
            key = Path(event['account_read']['path']).name if event.get('account_read') else None
            if rows and (rows[-1]['sha256'], rows[-1]['account_state']) == (key,event.get('account_state')):
                continue
            text = self.blob(key).decode('utf-8') if key else ''
            rows.append({'context_record': event['id'], 'account_record': event.get('account_record'),
                         'account_state':event.get('account_state'), 'recovery_reason':event.get('account_recovery'),
                         'sha256': key, 'path': str(self.journal / 'blobs' / key) if key else None,
                         'diff': ''.join(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n' for line in difflib.unified_diff(previous.splitlines(True), text.splitlines(True),
                                             fromfile='previous injected account', tofile='injected account'))})
            previous = text
        return rows

    def decision(self, record='latest', item=None, view='wire', preview_chars=240, offset=0, limit=20):
        from mads.inspection.decision_inspection import inspect_decision
        return inspect_decision(self, record, item, view, preview_chars, offset, limit)

    def activity(self, last=3, preview_chars=1200, after_record=None):
        from mads.inspection.activity_inspection import inspect_activity
        return inspect_activity(self, last, preview_chars, after_record)

    def request(self, record='latest', out=None, view='wire'):
        requests = [e for e in self.events if e['event'] == ('http_request' if view=='wire' else 'request')]
        if record == 'latest' and not requests:
            raise ValueError(f'No {view} request recorded yet')
        try:
            event = requests[-1] if record == 'latest' else self.by_id[int(record)]
        except KeyError as exc:
            raise ValueError(f'Unknown request record: {record}') from exc
        if event['event'] not in ('request', 'http_request'):
            raise ValueError('Select a request or http_request record')
        compiled = self.by_id[event['request_record']] if event['event'] == 'http_request' else event
        manifest = self.by_id[compiled['context_id']]
        raw = self.blob(event['blob'])
        body = json.loads(raw)
        items = body.get('input', body.get('messages', []))
        if not isinstance(items, list):
            items = [items]
        inventory = []
        for n, item in enumerate(items):
            if not isinstance(item, dict):
                inventory.append({'index': n, 'kind': type(item).__name__})
                continue
            encoded = dump(item).encode()
            inventory.append({'index': n, 'type': item.get('type', 'message'), 'role': item.get('role'),
                              'bytes': len(encoded), 'call_id': item.get('call_id')})
        summary = {**self.identity(), 'request_record': event['id'], 'context_record': manifest['id'],
                   'request_sha256': event['blob'], 'request_path': str(self.journal / 'blobs' / event['blob']),
                   'settings': {k: body[k] for k in ('model', 'reasoning', 'store', 'service_tier', 'include',
                                'max_output_tokens', 'max_tokens') if k in body},
                   'tool_names': [t.get('name', t.get('function', {}).get('name')) for t in body.get('tools', [])],
                   'items': inventory, 'manifest': manifest,
                   'warning': 'Included content is not proof of understanding; export is not continuation replay.'}
        if out:
            dest = Path(out)
            dest.mkdir(parents=True, exist_ok=False)
            (dest / 'request.json').write_bytes(raw)
            (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            (dest / 'inspection.json').write_text(json.dumps(summary, indent=2) + '\n')
            if manifest.get('account_read'):
                key = Path(manifest['account_read']['path']).name
                (dest / 'account.md').write_bytes(self.blob(key))
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('summary')
    timeline = commands.add_parser('timeline')
    timeline.add_argument('--last', type=int, default=20)
    commands.add_parser('accounts')
    activity = commands.add_parser('activity', help='Compact latest actions and captured observations; no semantic summary')
    activity.add_argument('--last', type=int, default=3, help='Assistant turns (1..20)')
    activity.add_argument('--preview-chars', type=int, default=1200, help='Characters per preview (0..8000)')
    activity.add_argument('--after-record', type=int, default=None,
                          help='Non-negative journal record id; return only turns with activity after this id')
    decision = commands.add_parser('decision', help='Bounded causal request/response and decision evidence')
    decision.add_argument('--record', default='latest', help='Record ID, or latest request/HTTP/response/assistant')
    decision.add_argument('--item', type=int, help='Preview one zero-based request item without replay dumping')
    decision.add_argument('--view', choices=('wire', 'compiled'), default='wire')
    decision.add_argument('--preview-chars', type=int, default=240, help='Characters per preview (0..4000)')
    decision.add_argument('--offset', type=int, default=0, help='Collection page offset')
    decision.add_argument('--limit', type=int, default=20, help='Collection page size (1..100)')
    request = commands.add_parser('request')
    request.add_argument('--record', default='latest')
    request.add_argument('--out')
    request.add_argument('--view',choices=('wire','compiled'),default='wire')
    formatting=request.add_mutually_exclusive_group()
    formatting.add_argument('--raw',action='store_true',help='Write exact selected request bytes')
    formatting.add_argument('--readable',action='store_true',help='Pretty-print the entire request JSON; a rendering, not exact wire bytes')
    args = parser.parse_args(argv)
    try:
        journal = Journal(args.run)
        if args.command == 'timeline':
            result = journal.timeline(args.last)
        elif args.command == 'activity':
            result = journal.activity(args.last, args.preview_chars, args.after_record)
        elif args.command == 'decision':
            result = journal.decision(args.record, args.item, args.view, args.preview_chars, args.offset, args.limit)
        elif args.command == 'request':
            result = journal.request(args.record, args.out,args.view)
            if args.raw:
                import sys
                sys.stdout.buffer.write(journal.blob(result['request_sha256']))
                return 0
            if args.readable:
                print(json.dumps(json.loads(journal.blob(result['request_sha256'])),indent=2,ensure_ascii=True))
                return 0
        else:
            result = getattr(journal, args.command)()
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    except (ValueError,OSError,KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
