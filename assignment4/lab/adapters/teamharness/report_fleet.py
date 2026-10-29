"""Read native team sessions and original course grades without altering evidence.

Execution, original grading, and evidence integrity have separate fields. A
missing archive, interrupted workflow, or unknown patch identity is never
converted into a semantic repair failure. This reporter does not classify the
cause of a failed test from model prose or command-output keywords.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile


NATIVE = 'scaffold/.tasks/trace/madsServe_logs/0/teamharness/'
SUBMITTED = 'scaffold/model_patch_0.diff'
GRADED = 'scaffold/pro_eval/0/replay/model.patch'
RESULTS = 'scaffold/pro_eval/eval_results.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


@contextmanager
def archive_stream(path):
    """Stream compressed evidence; never extract archive-controlled paths."""
    if path.name.endswith('.zst'):
        process = subprocess.Popen(['zstd', '-dc', str(path)], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                yield archive
            if process.wait() != 0:
                raise ValueError('Archive decompression failed')
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
            process.wait()
            process.stderr.close()
    else:
        with tarfile.open(path, mode='r|*') as archive:
            yield archive


@contextmanager
def sqlite_snapshot(path):
    """Recover/backup only derived copies; original database and sidecars stay raw.

    A hard task cutoff can interrupt a rollback-journal transaction. Opening the
    archived database alone loses the recovery evidence; opening it read/write
    in place changes the evidence. Keep all present sidecars with a writable
    temporary copy, then analyze a coherent backup of the committed state.
    """
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix='team-sqlite-') as directory:
        directory = Path(directory)
        working = directory / 'working.sqlite3'
        inputs = {}
        for suffix in ('', '-journal', '-wal', '-shm'):
            source = Path(str(path) + suffix)
            if source.exists():
                if not source.is_file() or source.is_symlink():
                    raise ValueError('SQLite evidence must be a regular file')
                inputs[path.name + suffix] = file_digest(source)
                shutil.copyfile(source, Path(str(working) + suffix))
        if path.name not in inputs:
            raise ValueError('SQLite database is missing')
        coherent = directory / 'coherent.sqlite3'
        with sqlite3.connect(working) as source, sqlite3.connect(coherent) as destination:
            source.backup(destination)
        evidence = {'raw_components_sha256': inputs, 'coherent_snapshot_sha256': file_digest(coherent),
                    'recovery_on_copy_only': True, 'sidecars_present': sorted(set(inputs) - {path.name})}
        yield coherent, evidence


def session_metrics(path):
    """Read committed native records, including epochs removed by compaction."""
    with sqlite_snapshot(path) as (coherent, evidence):
        result = _session_metrics(coherent)
        result['sqlite_evidence'] = evidence
        return result


def shell_receipts(name, receipt):
    """Read actual shell observations, including later job completion."""
    if not isinstance(receipt, dict):
        return []
    candidates = []
    if name in ('exec_command', 'job'):
        candidates.append(receipt)
    elif name in ('parallel', 'inspect_parallel', 'cancel_parallel'):
        members = receipt.get('members')
        for member in members if isinstance(members, list) else []:
            if isinstance(member, dict) and member.get('name') == 'exec_command':
                candidates.extend((member.get('result'), member.get('job_observation')))
    return [row for row in candidates if isinstance(row, dict)
            and isinstance(row.get('id'), str) and row['id'] and row.get('state') in ('running', 'exited')]


def shell_job_ids(name, receipt):
    """Count actual shell receipts, never a group request or predicted job ID."""
    return {row['id'] for row in shell_receipts(name, receipt)}


def deadline_evidence(sessions, expected):
    missing = [s.get('session_id', s.get('id')) for s in sessions if s.get('task_deadline_unix') is None]
    different = [s.get('session_id', s.get('id')) for s in sessions
                 if expected is not None and s.get('task_deadline_unix') is not None and s['task_deadline_unix'] != expected]
    available = bool(sessions) and expected is not None and not missing
    return {'expected': expected, 'observed_sessions': len(sessions), 'missing': missing,
            'mismatched': different, 'verified': available and not different,
            'state': 'mismatched' if different else ('matching' if available else 'unavailable')}


def result_reference(text):
    """This view's own reference: a trailing evidence block, or a leading one in older views."""
    return (re.search(r'\n\nEvidence references:\nResult obj:(\d+)\nOriginal JSON: [^\n]*\nFull readable result: [^\n]*\n\Z', text)
            or re.match(r'Result obj:(\d+)\n', text))


def original_tool_receipt(db, text, name):
    """Readable messages reference original typed receipts; prose is not JSON."""
    match = result_reference(text)
    if match:
        row = db.execute("SELECT kind,body,sha256 FROM objects WHERE id=?", (int(match[1]),)).fetchone()
        if row is None or row[0] != 'tool_result' or digest(row[1].encode()) != row[2]:
            raise ValueError('Readable tool result has no intact original receipt')
        event = json.loads(row[1])
        if event['name'] != name:
            raise ValueError('Readable result refers to another tool')
        return event['receipt']
    return json.loads(text)  # Historical JSON-rendered messages remain supported.


def _session_metrics(path):
    result = dict(requests=0, responses=0, transport_errors=0, input_tokens=0,
                  output_tokens=0, response_cost_usd=0, missing_usage=0,
                  missing_cost=0, compactions=0, failed_compactions=0,
                  tool_results=0, shell_results=0, requests_by_purpose={},
                  provider_observations={})
    purposes, observations, epochs = Counter(), Counter(), {}
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id,kind,body,sha256,created FROM objects ORDER BY id')
        for ident, kind, raw, expected_hash, created in rows:
            if digest(raw.encode()) != expected_hash:
                raise ValueError(f'Session object {ident} has invalid content hash')
            if kind not in {'model_request', 'model_response', 'model_transport_error',
                            'provider_observation', 'compaction_result', 'session_state'}:
                continue
            body = json.loads(raw)
            if kind == 'model_request':
                result['requests'] += 1
                purposes[body['purpose']] += 1
                result.setdefault('first_request_at', created)
                result['last_request_at'] = created
            elif kind == 'model_response':
                result['responses'] += 1
                result['last_response_at'] = created
                usage = body.get('response', {}).get('usage') or {}
                result['missing_usage'] += int(not usage)
                result['missing_cost'] += int(usage.get('cost') is None)
                result['input_tokens'] += usage.get('prompt_tokens') or 0
                result['output_tokens'] += usage.get('completion_tokens') or 0
                result['response_cost_usd'] += usage.get('cost') or 0
            elif kind == 'model_transport_error':
                result['transport_errors'] += 1
            elif kind == 'provider_observation':
                observations[body['kind']] += 1
            elif kind == 'compaction_result':
                result['compactions' if body['status'] == 'compacted' else 'failed_compactions'] += 1
            elif kind == 'session_state':
                epochs[body['epoch']] = body
        if not epochs:
            raise ValueError('Session journal has no recorded state')
        # Each epoch's last state contains its complete append-only message list.
        # Taking every checkpoint would count the same tool result many times.
        observed_jobs = {}
        for state in epochs.values():
            tools = {}
            for message in state['messages']:
                if message.get('role') == 'assistant':
                    for call in message.get('tool_calls') or []:
                        tools[call['id']] = call['function']['name']
                elif message.get('role') == 'tool':
                    result['tool_results'] += 1
                    name = tools.get(message['tool_call_id'])
                    for job in shell_receipts(name, original_tool_receipt(db, message['content'], name)):
                        previous = observed_jobs.get(job['id'], {})
                        if (previous.get('state') != 'exited' or job.get('state') == 'exited'):
                            observed_jobs[job['id']] = job
        last = epochs[max(epochs)]
        result.update(role=last['config']['role'], status=last['status'], outcome=last.get('outcome'),
                      epoch=last['epoch'], session_id=last['session_id'],
                      shell_results=len(observed_jobs), shell_job_ids=sorted(observed_jobs))
        result['shell_timing'] = {
            'completed_process_seconds': sum(job['elapsed_seconds'] for job in observed_jobs.values()
                if job.get('state') == 'exited' and job.get('elapsed_seconds') is not None),
            'unfinished_observed_jobs': sum(job.get('state') != 'exited' for job in observed_jobs.values()),
            'missing_elapsed': sum(job.get('elapsed_seconds') is None for job in observed_jobs.values()),
            'scope': 'Unique shell jobs with observed completion; parallel processes overlap. Other tools and unobserved completion time are not included.'}
        result['shell_observations'] = [{key: job.get(key) for key in
            ('id', 'state', 'started_at', 'finished_at', 'elapsed_seconds')} for job in observed_jobs.values()]
        metadata = last['config'].get('metadata', {})
        result.update(task_deadline_unix=metadata.get('task_deadline_unix', metadata.get('shared_solver_deadline_unix')),
                      context_limit_tokens=metadata.get('context_limit_tokens'),
                      context_limit_source=metadata.get('context_limit_source'))
    result['requests_by_purpose'] = dict(purposes)
    result['provider_observations'] = dict(observations)
    return result


def _member_name(name, instance_id):
    parts = PurePosixPath(name).parts
    if not parts or name.startswith('/') or '..' in parts:
        raise ValueError('Unsafe archive member name')
    if parts[0] != instance_id:
        raise ValueError('Archive root does not match expected task identity')
    return '/'.join(parts[1:])


def _json(files, name, default=None):
    return json.loads(files[name]) if name in files else default


def verify_selection(files, workflow, candidate):
    """Bind the native selected artifact to the final original-base capture.

    Reviewed patches can be relative to a synthetic frozen commit. The adapter
    replays initial dirty source and then the selected patch, so their hashes need
    not equal the final normalized original-base diff. Preserve that distinction.
    """
    selection, final = _json(files, 'selection.json'), _json(files, 'finalization.json')
    if selection is None or final is None:
        return False
    if (final.get('course_base') != candidate['base'] or final.get('candidate_sha256') != candidate['sha256']
            or final.get('replayed_sha256') != candidate['sha256'] or final.get('exact_course_capture') is not True
            or final.get('selection_kind') != selection.get('kind')
            or final.get('selected_sha256') != selection.get('selected_sha256')
            or final.get('reviewed') is not selection.get('reviewed')):
        raise ValueError('Finalization does not bind selection to captured course bytes')
    kind = selection.get('kind')
    if kind in ('main_candidate', 'main_returned_candidate'):
        ident = selection.get('source_agent_id')
        runner = _json(files, 'run/agents/agents/' + str(ident) + '/runner.json') or {}
        selected = workflow.get('candidate', {}) if kind == 'main_candidate' else runner.get('patch', {})
        # Capture paths are local to the producing worker. Resolve the archived
        # suffix rather than trusting a workstation's similarly named path.
        raw = next((body for name, body in files.items()
                    if str(selected.get('patch_path', '')).endswith('/' + name)), None)
        initial = files.get('run/source/source-before.patch')
        if (workflow.get('workflow') != 'main' or (kind == 'main_candidate' and workflow.get('state') != 'completed')
                or selection.get('reviewed') is not False or selection.get('source_role') != 'main'
                or ident != workflow.get('agents', {}).get('main')
                or runner.get('status') != 'completed' or runner.get('patch') != selected
                or raw is None or digest(raw) != selected.get('sha256')
                or selected.get('sha256') != selection.get('selected_sha256')
                or selected.get('base') != selection.get('selected_base')
                or selected.get('base') != workflow.get('frozen', {}).get('revision')
                or initial is None or digest(initial) != workflow.get('frozen', {}).get('source_patch_sha256')
                or digest(initial) != final.get('initial_patch_sha256')):
            raise ValueError('Main selection does not match its recorded returned bytes')
        if not initial and candidate['sha256'] != selected['sha256']:
            raise ValueError('Clean-source main patch changed during course replay')
        components = [digest(initial), digest(raw)]
    elif kind == 'reviewed_candidate':
        selected = workflow.get('candidate', {})
        raw = files.get('run/candidate.patch')
        initial = files.get('run/source/source-before.patch')
        if (workflow.get('state') != 'completed' or selection.get('reviewed') is not True
                or raw is None or initial is None or digest(raw) != selected.get('sha256')
                or selected.get('sha256') != selection.get('selected_sha256')
                or selected.get('sha256') != workflow.get('reviewed_sha256')
                or selected.get('base') != selection.get('selected_base')
                or selected.get('base') != workflow.get('frozen', {}).get('revision')
                or digest(initial) != workflow.get('frozen', {}).get('source_patch_sha256')
                or digest(initial) != final.get('initial_patch_sha256')
                or selection.get('source_agent_id') != workflow.get('agents', {}).get('integrator')):
            raise ValueError('Reviewed selection does not match original workflow artifacts')
        if not initial and candidate['sha256'] != selected['sha256']:
            raise ValueError('Clean-source reviewed patch changed during course replay')
        components = [digest(initial), digest(raw)]
    elif kind == 'graded_fallback':
        best = workflow.get('best_graded', {})
        selected, grade = best.get('candidate', {}), best.get('grade', {})
        raw = files.get('run/best-graded/' + str(selected.get('sha256')) + '.patch')
        initial = files.get('run/source/source-before.patch')
        if (selection.get('reviewed') is not False or raw is None or initial is None
                or digest(raw) != selected.get('sha256') or selected.get('sha256') != selection.get('selected_sha256')
                or grade.get('candidate_sha256') != selected.get('sha256')
                or grade.get('course_sha256') != candidate.get('sha256')
                or selection.get('grade') != grade or selection.get('source_agent_id') != best.get('agent_id')
                or selected.get('base') != workflow.get('frozen', {}).get('revision')
                or selection.get('selected_base') != selected.get('base')
                or digest(initial) != workflow.get('frozen', {}).get('source_patch_sha256')
                or digest(initial) != final.get('initial_patch_sha256')):
            raise ValueError('Retained graded fallback identities do not match')
        components = [digest(initial), digest(raw)]
    elif kind == 'unreviewed_fallback':
        role = selection.get('source_role')
        if (selection.get('reviewed') is not False or role not in {'main', 'implementer', 'integrator'}
                or selection.get('source_agent_id') != workflow.get('agents', {}).get(role)
                or selection.get('selected_base') != candidate['base']
                or selection.get('selected_sha256') != candidate['sha256']):
            raise ValueError('Fallback selection does not match captured source')
        components = [candidate['sha256']]
    elif kind == 'empty':
        if (selection.get('reviewed') is not False or candidate['bytes'] != 0
                or selection.get('selected_base') != candidate['base']
                or selection.get('selected_sha256') != digest(b'')):
            raise ValueError('Empty selection is not an empty original-base candidate')
        components = []
    else:
        raise ValueError('Unrecognized candidate selection kind')
    if final.get('replay_components') != components:
        raise ValueError('Replay component order or identity differs from selected source')
    return True


def read_environment(path, expected):
    """Read a separately collected host qualification, never model-authored prose."""
    if not path.is_file():
        return None
    found = []
    with archive_stream(path) as archive:
        for member in archive:
            if member.name == 'environment-checks/RESULT.json':
                if not member.isfile() or member.size > 8_000_000:
                    raise ValueError('Invalid environment qualification member')
                found.append(json.load(archive.extractfile(member)))
    if len(found) != 1 or found[0].get('passed') is not True:
        raise ValueError('Missing, duplicate or failed environment qualification')
    record = found[0]['images'][expected['instance_id']]
    phases = record.get('phases', [])
    if found[0].get('reference_solutions_used') is False and found[0].get('dependency_mode') in ('stock', 'base-dependencies'):
        required = {'git-and-source'}
        if found[0].get('grading_dependencies') == 'network':
            required.add('grader-dependency-network')
        if found[0].get('solver_dependencies') == 'network':
            required.add('solver-dependency-network')
    else:
        required = {'git-and-source', 'pytest-collection' if expected['repo'] == 'qutebrowser/qutebrowser'
                    else 'offline-dependencies-and-compile'}
    if (record.get('passed') is not True
            or record.get('original_grading_image') != expected['docker_image']
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', record.get('image_id', ''))
            or not required.issubset({p.get('name') for p in phases})
            or any(p.get('exit_code') != 0 for p in phases)):
        raise ValueError('Environment qualification does not bind the task and successful checks')
    return dict(record, archive=str(path), archive_sha256=file_digest(path))


def read_attempt(path, expected, solver_environment=None):
    row = dict(instance_id=expected['instance_id'], base_commit=expected['base_commit'],
               archive=str(path), solved=None, execution_state='unknown',
               grade_state='missing', evidence_state='valid', sessions=[])
    files, seen, journals = {}, set(), {}
    with tempfile.TemporaryDirectory(prefix='team-report-') as temp:
        with archive_stream(Path(path)) as archive:
            for member in archive:
                name = _member_name(member.name, expected['instance_id'])
                if not member.isfile():
                    continue
                native = name[len(NATIVE):] if name.startswith(NATIVE) else None
                wanted = name in {'tasks.json', RESULTS, SUBMITTED, GRADED,
                                  'scaffold/pro_eval/0/grading-environment.json'}
                wanted |= native in {'launch.json', 'stop.json', 'candidate.json', 'candidate.patch', 'grade.json',
                                     'selection.json', 'finalization.json', 'completed.json',
                                     'error.txt', 'finalization-error.txt', 'workflow.json', 'run/workflow.json',
                                     'run/candidate.patch', 'run/source/source-before.patch'}
                wanted |= bool(native and re.fullmatch(r'run/best-graded/[0-9a-f]{64}\.patch', native))
                wanted |= bool(native and re.fullmatch(
                    r'run/agents/agents/[0-9a-f]{32}/(?:runner\.json|(?:captures|checkpoints)/[^/]+\.patch)', native))
                session = (re.fullmatch(r'run/agents/agents/([0-9a-f]{32})/(session\.sqlite3(?:-journal|-wal|-shm)?)', native)
                           if native is not None else None)
                if not wanted and not session:
                    continue
                if name in seen:
                    raise ValueError('Duplicate evidence member: ' + name)
                seen.add(name)
                content = archive.extractfile(member)
                if session:
                    agent_id, filename = session.groups()
                    directory = Path(temp) / agent_id
                    directory.mkdir(exist_ok=True)
                    local = directory / filename
                    with local.open('wb') as out:
                        shutil.copyfileobj(content, out)
                    if filename == 'session.sqlite3':
                        journals[agent_id] = (local, name)
                else:
                    files[native if native is not None else name] = content.read()
        # Sidecars may follow the database anywhere in the tar stream. Wait
        # until all original components are present before recovering a copy.
        for agent_id, (local, name) in sorted(journals.items()):
            metrics = session_metrics(local)
            metrics['archive_member'] = name
            row['sessions'].append(metrics)
    staged = dict(expected)
    if solver_environment is not None:
        staged['sandbox_image'] = solver_environment['image_id']
        row['solver_environment'] = solver_environment
    if _json(files, 'tasks.json') != {'0': staged}:
        raise ValueError('Archived task differs from frozen manifest')
    for name in ('launch', 'stop', 'candidate', 'grade', 'selection', 'finalization'):
        if name + '.json' in files:
            row[name] = _json(files, name + '.json')
    row['error'] = files.get('error.txt', b'').decode(errors='replace') or None
    row['finalization_error'] = files.get('finalization-error.txt', b'').decode(errors='replace') or None
    workflows = [name for name in files if name == 'workflow.json' or name.endswith('/workflow.json')]
    if len(workflows) > 1:
        raise ValueError('Ambiguous native workflow receipts')
    workflow = _json(files, workflows[0], {}) if workflows else {}
    row['grading_mode'] = 'interactive_counts' if row.get('launch', {}).get('grade_feedback') else 'final_only'
    row['strategy'] = workflow.get('strategy', 'baseline')
    row['tournaments'] = workflow.get('tournaments', {})
    row['early_test_deliveries'] = workflow.get('early_test_deliveries', {})
    row['best_graded'] = workflow.get('best_graded')
    row['interactive_grades'] = workflow.get('interactive_grades', {})
    row['workflow_state'] = workflow.get('state')
    row['workflow_agents'] = workflow.get('agents', {})
    row['reviewed_sha256'] = workflow.get('reviewed_sha256')
    stop = row.get('stop', {})
    if stop.get('cancelled'):
        row['execution_state'] = 'cancelled'
    elif stop.get('cutoff'):
        row['execution_state'] = 'cutoff'
    elif row['finalization_error']:
        row['execution_state'] = 'finalization_error'
    elif row['error'] or stop.get('exit_code') not in (None, 0):
        row['execution_state'] = 'agent_error'
    elif workflow.get('state') == 'completed' and 'completed.json' in files:
        row['execution_state'] = 'completed'
    elif workflow.get('state'):
        row['execution_state'] = 'workflow_' + workflow['state']
    elif not row['sessions']:
        row['execution_state'] = 'unstarted'
    candidate = row.get('candidate', {})
    if candidate and candidate.get('base') != expected['base_commit']:
        raise ValueError('Candidate base differs from course base')
    patches = {name: digest(files[name]) for name in ('candidate.patch', SUBMITTED, GRADED) if name in files}
    row['patch_hashes'] = patches
    row['grading_environment'] = _json(files, 'scaffold/pro_eval/0/grading-environment.json')
    environment = row['grading_environment'] or {}
    if environment.get('mode') == 'aws_dependency_network':
        access = environment.get('dependency_access', {})
        if (access.get('candidate_sha256') != patches.get(GRADED)
                or access.get('network') != 'bridge' or access.get('original_test_scripts') is not True):
            raise ValueError('Grader dependency-access receipt differs from exact graded candidate')
    row['candidate_verified'] = bool(candidate and 'candidate.patch' in files and
        patches['candidate.patch'] == candidate.get('sha256') and len(files['candidate.patch']) == candidate.get('bytes'))
    if candidate and not row['candidate_verified']:
        raise ValueError('Captured candidate bytes do not match candidate receipt')
    row['selection_verified'] = bool(row['candidate_verified'] and verify_selection(files, workflow, candidate))
    row['graded_patch_verified'] = bool(row['candidate_verified'] and len(patches) == 3 and len(set(patches.values())) == 1)
    if len(patches) > 1 and len(set(patches.values())) != 1:
        raise ValueError('Candidate, submitted and original graded bytes differ')
    outcomes = _json(files, RESULTS, {})
    official = outcomes.get('0')
    if official is not None and type(official) is not bool:
        raise ValueError('Original course outcome must be boolean')
    row['original_passed'] = official
    # Stock dispatcher can reject an empty candidate with no submitted diff or
    # a verified empty submitted diff, without creating a grader replay patch.
    # Only an original failing grade may use this exception; a pass always needs
    # all three exact patch artifacts.
    empty = (row['candidate_verified'] and candidate.get('bytes') == 0 and
             row.get('grade', {}).get('ok') is True and official is False and
             not patches.keys() - {'candidate.patch', SUBMITTED} and
             all(files[name] == b'' for name in patches))
    row['empty_candidate_grade_verified'] = bool(empty)
    if official is not None and (row['graded_patch_verified'] or empty) and row['selection_verified']:
        row['solved'] = official
        row['grade_state'] = 'passed' if official else 'failed'
    elif official is not None:
        row['grade_state'] = 'unverified_selection' if (row['graded_patch_verified'] or empty) else 'unverified_patch_identity'
    totals = ('requests', 'responses', 'transport_errors', 'input_tokens', 'output_tokens',
              'response_cost_usd', 'missing_usage', 'missing_cost', 'compactions',
              'failed_compactions', 'tool_results', 'shell_results')
    row.update({key: sum(session[key] for session in row['sessions']) for key in totals})
    row['shell_job_ids'] = sorted({job for session in row['sessions'] for job in session['shell_job_ids']})
    row['shell_results'] = len(row['shell_job_ids'])
    row['deadline_evidence'] = deadline_evidence(row['sessions'], row.get('launch', {}).get('deadline'))
    row['state'] = row['execution_state']
    return row


def report(sweep, manifest, arm='teamharness', output=None):
    sweep, manifest = Path(sweep), Path(manifest)
    metadata = json.loads((sweep / 'sweep.json').read_text())
    arms = [entry for entry in metadata['arms'] if entry['name'] == arm]
    if len(arms) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]+', arm):
        raise ValueError('Select exactly one recorded sweep arm')
    if metadata.get('tasks_per_lane', 1) != 1:
        raise ValueError('Native reporter requires one task per fleet lane')
    if digest(manifest.read_bytes()) != metadata['manifest_sha256']:
        raise ValueError('Frozen manifest identity mismatch')
    entries = json.loads(manifest.read_text())
    by_id = {}
    for key, entry in entries.items():
        if entry['instance_id'] in by_id:
            raise ValueError('Duplicate task identity in frozen manifest')
        by_id[entry['instance_id']] = (key, entry)
    tasks = metadata['tasks']
    if len(tasks) != len(set(tasks)) or any(iid not in by_id for iid in tasks):
        raise ValueError('Sweep task identities do not match frozen manifest')
    rows = {iid: dict(instance_id=iid, source_key=by_id[iid][0], state='not_collected',
                      execution_state='not_collected', grade_state='missing', evidence_state='missing',
                      solved=None) for iid in tasks}
    # Legacy lane summaries cannot interpret native sessions or establish a
    # repair result. Their explicit preparation/packaging states still explain
    # absent archives and must remain distinct from pending work.
    sidecars = {}
    for path in sorted(sweep.glob(f'hosts/*/results/{arm}/*.json')):
        iid = path.stem
        if iid not in rows or iid in sidecars:
            raise ValueError('Unexpected or duplicate lane result: ' + str(path))
        lane = json.loads(path.read_text())
        if lane.get('instance_id') != iid:
            raise ValueError('Lane result identity mismatch')
        if lane.get('package_sha256') and lane['package_sha256'] != arms[0]['sha256']:
            raise ValueError('Lane package identity differs from frozen sweep arm')
        sidecars[iid] = {key: lane[key] for key in ('lane_status', 'exit_code', 'error',
                          'package_sha256', 'lane_seconds', 'run_limit_seconds') if key in lane}
        status = 'infrastructure_error' if lane.get('lane_status') in {'image_failed', 'packaging_failed'} else 'missing_archive'
        rows[iid].update(state=status, execution_state=status, lane=sidecars[iid])
    seen = set()
    for path in sorted(sweep.glob(f'hosts/*/results/{arm}/*.tar.zst')):
        iid = path.name.removesuffix('.tar.zst')
        if iid in seen or iid not in rows:
            raise ValueError('Unexpected or duplicate task archive: ' + str(path))
        seen.add(iid)
        try:
            expected = by_id[iid][1]
            environment = read_environment(path.parent.parent / 'validation/environment-checks.tar.zst', expected)
            row = read_attempt(path, expected, environment)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, tarfile.TarError) as error:
            row = dict(instance_id=iid, archive=str(path), state='evidence_error',
                       execution_state='unknown', grade_state='unverified', evidence_state='error',
                       solved=None, evidence_error=f'{type(error).__name__}: {error}')
        rows[iid] = dict(row, source_key=by_id[iid][0])
        if iid in sidecars:
            rows[iid]['lane'] = sidecars[iid]
    result = dict(sweep=metadata['sweep'], arm=arm, expected=len(rows), collected=len(seen),
                  solved=sum(row['solved'] is True for row in rows.values()),
                  graded_failed=sum(row['solved'] is False for row in rows.values()),
                  unknown=sum(row['solved'] is None for row in rows.values()),
                  evidence_errors=sum(row['evidence_state'] == 'error' for row in rows.values()),
                  states=dict(Counter(row['state'] for row in rows.values())),
                  grade_states=dict(Counter(row['grade_state'] for row in rows.values())),
                  tasks=list(rows.values()),
                  interpretation='Original verified grades are independent of execution state. Cutoffs and errors remain explicit; no semantic failure cause is inferred. Provider usage omits unknown/aborted charges.')
    destination = Path(output) if output else sweep / 'teamharness-report.json'
    destination.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sweep', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--arm', default='teamharness')
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    result = report(arguments.sweep, arguments.manifest, arguments.arm, arguments.output)
    print(json.dumps({key: value for key, value in result.items() if key != 'tasks'}, indent=2))
    raise SystemExit(bool(result['evidence_errors']))
