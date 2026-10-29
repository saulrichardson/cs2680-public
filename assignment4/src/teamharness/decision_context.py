"""Read-only projections of grade identities, later effects and existing jobs.

No live-source capture, inferred diagnosis, action replay or workflow gate.
The next request archives these observations through the existing runtime path.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from src import workspace as effects


def grade_context(root: Path) -> dict | None:
    database = Path(root) / 'session.sqlite3'
    if not database.exists():
        return None
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute("SELECT id,body,created FROM objects WHERE kind='tool_result' "
            "AND json_extract(body,'$.name')='evaluate_candidate' ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        latest_attempt = None
        receipt = json.loads(row[1])['receipt']
        facts = receipt.get('grade_facts') or {}
        if not (type(facts.get('tests_failed')) is int and type(facts.get('tests_total')) is int):
            latest_attempt = {'receipt_ref': 'obj:' + str(row[0]), 'archive': str(database),
                'grading_state': receipt.get('grading_state'), 'counts_available': False,
                'candidate_sha256': receipt.get('candidate', {}).get('sha256'),
                'meaning': 'This attempt has no completed count-bearing grade; its outcome remains uncertain.'}
            row = db.execute("SELECT id,body,created FROM objects WHERE kind='tool_result' "
                "AND json_extract(body,'$.name')='evaluate_candidate' "
                "AND json_type(body,'$.receipt.grade_facts.tests_failed')='integer' "
                "AND json_type(body,'$.receipt.grade_facts.tests_total')='integer' ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                return latest_attempt
            receipt = json.loads(row[1])['receipt']
            facts = receipt['grade_facts']
        ident, raw, at = row
        candidate = receipt.get('candidate', {})
        checkpoint_id = receipt.get('checkpoint_id')
        checkpoint_path = Path(root) / 'checkpoints' / (str(checkpoint_id) + '.json')
        checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.is_file() else {}
        captured_at = checkpoint.get('captured_at')
        # An effect before the grade returns can still follow its source capture.
        # These capabilities can write; their presence does not prove different bytes.
        threshold = captured_at if captured_at is not None else at
        later = db.execute("SELECT id,body,created FROM objects WHERE kind='tool_result' AND created>? "
            "AND json_extract(body,'$.name') IN ('apply_patch','edit_file','exec_command','job','parallel','inspect_parallel','apply_agent_patch') "
            "ORDER BY id DESC LIMIT 1", (threshold,)).fetchone()
        write = db.execute("SELECT id,created FROM objects WHERE kind='tool_result' AND created>? "
            "AND json_extract(body,'$.name') IN ('apply_patch','edit_file') "
            "AND (json_extract(body,'$.receipt.applied')=1 OR json_extract(body,'$.receipt.source_writes_started')=1) "
            "ORDER BY id DESC LIMIT 1", (threshold,)).fetchone()
    failed, total = facts.get('tests_failed'), facts.get('tests_total')
    result = {'supplier': facts.get('supplier'), 'candidate_sha256': facts.get('candidate_sha256'),
        'checkpoint_id': checkpoint_id, 'candidate_base': candidate.get('base'),
        'tests_failed': failed, 'tests_total': total, 'counts_available': True,
        'trend': facts.get('trend'), 'previous_candidate': facts.get('previous_candidate'), 'receipt_ref': 'obj:' + str(ident),
        'archive': str(database), 'received_at': at, 'reused': receipt.get('reused'),
        'grader_reported_failure': type(failed) is int and failed > 0,
        'diagnosis_from_grader': 'Counts do not identify the failing checks or their cause.',
        'live_source_comparison': 'not_performed',
        'meaning': 'This grade belongs to the captured bytes above, not to later live edits or a model explanation.'}
    result['evidence_scope'] = receipt.get('evidence_scope', {
        'environment': {'available': False, 'meaning': 'No environment identity was recorded with this historical receipt.'}})
    if latest_attempt:
        result['latest_grading_attempt'] = latest_attempt
    if later:
        result['source_capable_effect_after_graded_capture'] = {
            'tool': json.loads(later[1])['name'], 'ref': 'obj:' + str(later[0]), 'at': later[2],
            'meaning': 'This effect can change source. Whether live bytes still match the graded candidate is unknown.'}
    if write:
        result['source_write_observed_after_graded_capture'] = {'ref': 'obj:' + str(write[0]), 'at': write[1],
            'meaning': 'An edit wrote source after that capture. Its resulting live bytes have no grade established here.'}
    latest = Path(root) / 'latest-checkpoint.json'
    if latest.is_file():
        captured = json.loads(latest.read_text())
        comparison = candidate_comparison(candidate, captured.get('candidate'))
        result['latest_checkpoint'] = {key: captured.get(key) for key in ('checkpoint_id', 'label', 'captured_at')}
        result['latest_checkpoint'].update(candidate_sha256=captured.get('candidate', {}).get('sha256'),
            matches_graded_bytes=True if comparison == 'same' else False if comparison == 'different' else None,
            meaning='A checkpoint identifies captured bytes; later live source has not been compared.')
        result['captured_source_comparison'] = comparison
    else:
        result['captured_source_comparison'] = 'not_compared'
    return result


def candidate_comparison(observed, selected):
    """Compare captured identities, never infer current live bytes from edits."""
    if not observed or not selected or any(not x.get(k) for x in (observed, selected) for k in ('base', 'sha256')):
        return 'not_compared'
    return 'same' if (observed['base'], observed['sha256']) == (selected['base'], selected['sha256']) else 'different'


def source_context(root):
    """Session-local captures and latest journal results; never inspect live bytes.

    Native parallel members are journaled in call order, which does not establish
    execution/completion order or the last writer to the working tree.
    """
    root = Path(root)
    result = {'live_source_comparison': 'not_compared'}
    path = root / 'latest-checkpoint.json'
    if path.is_file():
        value = json.loads(path.read_text())
        result['latest_capture'] = {'candidate': value.get('candidate'), 'captured_at': value.get('captured_at'),
                                    'receipt_path': str(path)}
    database = root / 'session.sqlite3'
    if database.is_file():
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute("SELECT id,body FROM objects WHERE kind='tool_result' AND "
                "json_extract(body,'$.name') IN ('apply_patch','edit_file','apply_agent_patch','exec_command','job','parallel','inspect_parallel') "
                "ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            value = json.loads(row[1]); receipt = value['receipt']
            outcome = 'source_not_compared'
            if receipt.get('state') in ('unknown', 'partial') or receipt.get('execution_state') == 'unknown':
                outcome = 'partial' if receipt.get('state') == 'partial' else 'unknown'
            elif receipt.get('executed') is False or (value['name'] == 'apply_patch' and receipt.get('source_writes_started') is False) or (value['name'] == 'edit_file' and receipt.get('applied') is False):
                outcome = 'no_source_writes'
            elif value['name'] in ('apply_patch', 'edit_file') and receipt.get('applied') is True:
                outcome = 'patch_applied'
            result['last_source_action'] = {'tool': value['name'], 'outcome': outcome,
                'state': receipt.get('state', receipt.get('execution_state')), 'archive': str(database),
                'receipt_ref': 'obj:' + str(row[0])}
    return result


def observed_jobs(workspace: dict) -> list[dict]:
    result = []
    for path in sorted((Path(workspace['state']) / 'jobs').glob('*/request.json')):
        try:
            row = effects.observe(workspace, path.parent.name, count=0)
            result.append(row)
        except (OSError, ValueError) as error:
            result.append({'id': path.parent.name, 'state': 'unknown', 'error': str(error)})
    return result


def running_jobs(workspace: dict) -> list[dict]:
    return [row for row in observed_jobs(workspace) if row.get('state') != 'exited']


def job_observations(workspace, jobs):
    import time
    return [{**{k: row[k] for k in ('id', 'state', 'exit_code', 'finished_at', 'elapsed_seconds',
                'output_path', 'error', 'cancellation') if k in row},
             'observed_at': time.time(),
             'command_preview': row.get('command', '')[:180], 'cwd': row.get('cwd'),
             'request_path': str(Path(workspace['state']) / 'jobs' / row['id'] / 'request.json'),
             'receipt_path': str(Path(workspace['state']) / 'jobs' / row['id'] / 'result.json'),
             'meaning': 'Process state observed from supervisor receipts. Use job to retrieve output; another exec_command starts another process.'}
            for row in jobs]


def job_context(workspace: dict, observations=None) -> list[dict]:
    groups = {}
    for job in (running_jobs(workspace) if observations is None else observations):
        if job.get('state') == 'exited':
            continue
        command = job.get('command', '')
        key = (command, job.get('cwd'), job['state'])
        item = groups.setdefault(key, {'state': job['state'], 'command_preview': command[:180],
            'command_sha256': hashlib.sha256(command.encode()).hexdigest(), 'cwd': job.get('cwd'), 'jobs': [],
            'meaning': 'These are existing processes. Use job(id=..., action="poll") to observe; another exec_command starts another process.'})
        item['jobs'].append({'id': job['id'], 'elapsed_seconds': job.get('elapsed_seconds'),
                            'output_path': job.get('output_path')})
    return list(groups.values())


def verification_context(root, plan):
    """Small exact-candidate check references survive a misleading continuation."""
    if plan is None:
        return None
    root = Path(root)
    checkpoint = root / 'latest-checkpoint.json'
    selected = json.loads(checkpoint.read_text()).get('candidate') if checkpoint.is_file() else None
    runs = []
    for path in (root / 'test-plan-runs').glob('*/receipt.json'):
        receipt = json.loads(path.read_text())
        if receipt.get('plan_id') == plan['id']:
            runs.append((receipt, path))
    result = {'plan_id': plan['id'], 'latest_capture': selected and {k: selected.get(k) for k in ('base', 'sha256')},
              'live_source_comparison': 'not_compared',
              'meaning': 'Checks apply to their plan and captured source. No result for later live edits is inferred.'}
    for target in ('baseline', 'candidate'):
        candidates = [(r, p) for r, p in runs if r.get('target') == target]
        if not candidates:
            result[target] = {'state': 'unavailable', 'meaning': 'No recorded execution for this plan and target.'}
            continue
        receipt, path = max(candidates, key=lambda item: item[0].get('started_at', 0))
        result[target] = {'state': receipt['state'], 'passed': receipt.get('passed'),
            'candidate': {k: receipt.get('candidate', {}).get(k) for k in ('base', 'sha256')},
            'comparison_to_latest_capture': candidate_comparison(receipt.get('candidate'), selected),
            'environment': receipt.get('evidence_scope', {}).get('environment', {'available': False}),
            'receipt_path': str(path),
            'checks': [{**{k: c['execution'].get(k) for k in ('state', 'exit_code', 'source_unchanged', 'stdout_path', 'stderr_path')},
                       'name': c['name'], 'passed': c['assessment']['passed'],
                       'definition_sha256': c.get('definition_sha256')} for c in receipt.get('checks', [])]}
    return result


def uncertain_effects(root):
    result = []
    for path in sorted((Path(root) / 'effects').glob('*.intent.json')):
        receipt = path.with_name(path.name.replace('.intent.json', '.json'))
        value = json.loads(receipt.read_text()) if receipt.exists() else {}
        if not receipt.exists() or value.get('execution_state', value.get('state')) == 'unknown':
            intent = json.loads(path.read_text())
            result.append({'tool': intent['name'], 'effect_id': intent['effect_id'],
                'intent_path': str(path), 'receipt_path': str(receipt) if receipt.exists() else None,
                'state': 'unknown', 'replay_safe': False})
    # Interrupted session/native-batch dispatch can have a journal receipt even
    # when execution never reached ToolRuntime's file-based intent boundary.
    database = Path(root) / 'session.sqlite3'
    if database.exists():
        seen = {row['effect_id'] for row in result}
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            rows = db.execute("SELECT id,body FROM objects WHERE kind='tool_result' AND "
                "(json_extract(body,'$.receipt.state')='unknown' OR json_extract(body,'$.receipt.execution_state')='unknown') ORDER BY id DESC")
            for ident, raw in rows:
                value = json.loads(raw)
                if value['effect_id'] not in seen:
                    result.append({'tool': value['name'], 'effect_id': value['effect_id'],
                        'archive': str(database), 'receipt_ref': 'obj:' + str(ident),
                        'state': 'unknown', 'replay_safe': False})
                    seen.add(value['effect_id'])
    return result
