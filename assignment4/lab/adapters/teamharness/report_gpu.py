"""Read a collected replica batch with cohort denominators and exact original grades.

Uses the fleet reporter's candidate/grade/session validation through temporary
per-task archive views. Original members remain unchanged and are hash-linked;
missing work is unknown, never an inferred semantic failure.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import tempfile

from report_fleet import read_attempt, digest, file_digest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'replica'))
from startup_report import Archive


NATIVE_KEEP = {'launch.json', 'stop.json', 'candidate.json', 'candidate.patch', 'grade.json',
    'selection.json', 'finalization.json', 'completed.json', 'error.txt', 'finalization-error.txt',
    'workflow.json', 'run/workflow.json', 'run/candidate.patch', 'run/source/source-before.patch'}


def native_wanted(name):
    return (name in NATIVE_KEEP or bool(re.fullmatch(r'run/best-graded/[0-9a-f]{64}\.patch', name))
            or bool(re.fullmatch(r'run/agents/agents/[0-9a-f]{32}/(?:runner\.json|(?:captures|checkpoints)/[^/]+\.patch|session\.sqlite3(?:-journal|-wal|-shm)?)', name)))


def report_member(name):
    """Retain report inputs, excluding bulk dependencies and captured source trees."""
    match = re.fullmatch(r'jobs/[^/]+/iteration/(.+)', name)
    if not match:
        return bool(re.fullmatch(r'jobs/[^/]+/server-reuse\.json', name))
    relative = match[1]
    if relative in ('experiment.json', 'course/evaluation_scripts/agent_task_input.json',
                    'task-stages.json', 'serving-ready.json'):
        return True
    prefix = 'execution/course/'
    if not relative.startswith(prefix): return False
    relative = relative[len(prefix):]
    if relative == 'pro_eval/eval_results.json' or re.fullmatch(
            r'model_patch_[^/]+\.diff|pro_eval/[^/]+/(?:replay/model\.patch|grading-environment\.json)', relative):
        return True
    prefix = '.tasks/trace/madsServe_logs/'
    if not relative.startswith(prefix): return False
    relative = relative[len(prefix):]
    if relative in ('allocation/policy.json', 'allocation/selection.json', 'allocation/result.json'):
        return True
    if re.fullmatch(r'model-scheduler/requests/[^/]+/receipt\.json', relative): return True
    match = re.fullmatch(r'[^/]+/teamharness/(.+)', relative)
    return bool(match and (native_wanted(match[1]) or match[1] in
        ('planning.json', 'task.json', 'transfer-receipt.json') or
        re.fullmatch(r'interactive-grades/[^/]+/(intent|receipt)\.json', match[1])))


def add(archive, name, raw):
    member = tarfile.TarInfo(name); member.size = len(raw)
    archive.addfile(member, io.BytesIO(raw))


def distribution(values):
    values = sorted(x for x in values if x is not None)
    if not values: return {'count': 0, 'sum_s': None, 'p50_s': None, 'p95_s': None}
    return {'count': len(values), 'sum_s': sum(values), 'p50_s': values[(len(values)-1)//2],
            'p95_s': values[min(len(values)-1, int(.95*len(values)))]}


def requests(rows):
    def durations(a, b):
        return [r[b] - r[a] for r in rows if r.get(a) is not None and r.get(b) is not None]
    return {'states': dict(Counter(r['state'] for r in rows)), 'count': len(rows),
        'queue': distribution(durations('queued_at', 'started_at')),
        'service': distribution([r['finished_at'] - r['sent_at'] for r in rows
                                if r['state'] == 'completed' and r.get('sent_at') is not None
                                and r.get('finished_at') is not None]),
        'uncertain_observation': distribution([r['finished_at'] - r['sent_at'] for r in rows
                                if r['state'] == 'uncertain' and r.get('sent_at') is not None
                                and r.get('finished_at') is not None]),
        'completion_tokens': sum(r.get('completion_tokens') or 0 for r in rows),
        'prompt_tokens': sum(r.get('prompt_tokens') or 0 for r in rows),
        'unknown_usage': sum(not r.get('usage_known') for r in rows),
        'generation_only_s': None,
        'timing_limit': 'Nonstream response service includes engine scheduling/prefill/generation; first_body_at is not time to first generated token. Overlapping request durations are not elapsed cohort time.'}


def cell(archive, job):
    prefix = 'jobs/' + job['id'] + '/iteration/'
    raw = archive.read(prefix + 'experiment.json')
    if raw is None or digest(raw) != job['manifest_sha256']:
        raise ValueError('Archived experiment differs from the frozen batch plan')
    frozen = json.loads(raw)
    manifest_name = 'course/evaluation_scripts/agent_task_input.json'
    raw_manifest = archive.read(prefix + manifest_name)
    if digest(raw_manifest) != frozen['files'][manifest_name]:
        raise ValueError('Archived cohort differs from frozen input')
    tasks = json.loads(raw_manifest)
    course = prefix + 'execution/course/'
    trace = course + '.tasks/trace/madsServe_logs/'
    policy = archive.json(trace + 'allocation/policy.json')
    selection = archive.json(trace + 'allocation/selection.json')
    allocation = archive.json(trace + 'allocation/result.json')
    grades = archive.json(course + 'pro_eval/eval_results.json') or {}
    selected = {str(r['task_id']) for r in selection['selected']} if selection else None
    admission = {str(r['task_id']): r for group in ('selected', 'reserve')
                 for r in (selection or {}).get(group, [])}
    scheduler = defaultdict(list); planning = {}; original_members = defaultdict(dict)
    interactive = defaultdict(dict)
    task_inputs = {}
    rows = []
    with tempfile.TemporaryDirectory(prefix='python-gpu-report-') as temporary:
        paths = {key: Path(temporary) / (key + '.tar') for key in tasks}
        views = {key: tarfile.open(path, 'w') for key, path in paths.items()}
        try:
            for key, expected in tasks.items():
                root = expected['instance_id'] + '/'
                add(views[key], root + 'tasks.json', json.dumps({'0': expected}).encode())
                if key in grades:
                    add(views[key], root + 'scaffold/pro_eval/eval_results.json', json.dumps({'0': grades[key]}).encode())
            # One ordered read of relevant members, avoiding a full extracted tree.
            for name, member in archive.members.items():
                if not member.isfile(): continue
                if name.startswith(trace + 'model-scheduler/requests/') and name.endswith('/receipt.json'):
                    receipt = archive.json(name); scheduler[str(receipt['task_id'])].append(receipt)
                    continue
                match = re.fullmatch(re.escape(trace) + r'([^/]+)/teamharness/(.+)', name)
                key = target = None
                if match and match[1] in tasks:
                    key, relative = match.groups()
                    if relative == 'planning.json': planning[key] = archive.json(name)
                    if relative == 'task.json':
                        task_inputs[key] = archive.json(name)
                        content = archive.read(name)
                        original_members[key][name] = {'sha256':digest(content), 'bytes':len(content)}
                    grade = re.fullmatch(r'interactive-grades/([^/]+)/(intent|receipt).json', relative)
                    if grade:
                        interactive[key].setdefault(grade[1], {})[grade[2]] = archive.json(name)
                    if native_wanted(relative): target = 'scaffold/.tasks/trace/madsServe_logs/0/teamharness/' + relative
                if target is None:
                    for pattern, destination in [
                        (r'model_patch_([^/]+)\.diff', 'scaffold/model_patch_0.diff'),
                        (r'pro_eval/([^/]+)/replay/model.patch', 'scaffold/pro_eval/0/replay/model.patch'),
                        (r'pro_eval/([^/]+)/grading-environment.json', 'scaffold/pro_eval/0/grading-environment.json')]:
                        match = re.fullmatch(re.escape(course) + pattern, name)
                        if match and match[1] in tasks: key, target = match[1], destination; break
                if target is not None:
                    content = archive.read(name)
                    original_members[key][name] = {'sha256': digest(content), 'bytes': len(content)}
                    add(views[key], tasks[key]['instance_id'] + '/' + target, content)
        finally:
            for view in views.values(): view.close()
        for key, expected in tasks.items():
            try:
                binding = task_inputs.get(key)
                if binding is not None and (str(binding.get('task')) != key or
                        any(binding.get(n) != expected[n] for n in ('problem_statement','requirements','interface'))):
                    raise ValueError('Runtime task input differs from frozen public task')
                row = read_attempt(paths[key], expected)
                if row['solved'] is not None and binding is None:
                    raise ValueError('Original grade has no runtime task-input binding')
                row['task_identity'] = 'matching' if binding is not None else 'not_observed'
                row.pop('archive', None)
            except Exception as error:
                row = {'instance_id': expected['instance_id'], 'solved': None, 'execution_state': 'unknown',
                    'grade_state': 'unverified', 'evidence_state': 'error', 'evidence_error': type(error).__name__ + ': ' + str(error)}
            row.update(task_id=key, selected=(key in selected) if selected is not None else None,
                       planning=planning.get(key), scheduler=requests(scheduler[key]),
                       original_members=original_members[key],
                       evidence_transfer=archive.json(trace + key + '/teamharness/transfer-receipt.json'))
            if row.get('execution_state') == 'unstarted' and scheduler[key]:
                row['execution_state'] = 'started_final_capture_unavailable'
            elif row.get('execution_state') == 'unstarted' and row.get('launch'):
                row['execution_state'] = 'launched_final_capture_unavailable'
            grading = list(interactive[key].values())
            row['interactive_grading'] = {'attempts': len(grading),
                'unknown_outcomes': sum('receipt' not in g for g in grading),
                'duration': distribution([g['receipt']['finished_at'] - g['intent']['started_at']
                    for g in grading if 'intent' in g and 'receipt' in g])}
            shell = {}
            for session in row.get('sessions', []):
                for observed in session.get('shell_observations', []):
                    old = shell.get(observed['id'], {})
                    if old.get('state') != 'exited' or observed['state'] == 'exited':
                        shell[observed['id']] = observed
            row['shell_timing'] = {'completed': distribution([j['elapsed_seconds'] for j in shell.values()
                if j['state'] == 'exited' and j['elapsed_seconds'] is not None]),
                'unfinished_observed_jobs': sum(j['state'] != 'exited' for j in shell.values()),
                'missing_elapsed': sum(j['elapsed_seconds'] is None for j in shell.values())}
            plan = admission.get(key, planning.get(key)) or {}
            boundary = (plan.get('planning') or {}).get('planning') or {}
            row['selection_record'] = admission.get(key)
            row['planning_state'] = ('not_observed' if not plan else 'failed' if plan.get('error') else
                                     'expired' if plan.get('ineligible_reason') else
                                     'planning_timeout' if boundary.get('result') == 'planning_timeout' else 'completed')
            row['recommendation'] = plan.get('recommendation')
            row['selection_state'] = ('not_observed' if selected is None else 'selected' if key in selected
                                      else 'ineligible' if not plan.get('eligible') else 'reserve')
            row['remaining_at_selection_s'] = None
            # Selection contains the retained original planning records, but no
            # selection timestamp in this runtime. Do not invent that measurement.
            rows.append(row)
    all_requests = [r for group in scheduler.values() for r in group]
    chosen = [r for r in rows if r['selected']]
    result = {'job': job['id'], 'name': frozen['name'], 'source_commit': frozen['source_commit'],
        'submission_sha256': frozen['submission_sha256'], 'manifest_sha256': job['manifest_sha256'],
        'cohort_sha256': digest(raw_manifest), 'settings': frozen.get('experimental_settings', frozen['build_receipt']['campaign']),
        'cohort_count': len(rows), 'selected_count': len(chosen) if selected is not None else None,
        'original_passes': sum(r['solved'] is True for r in rows),
        'original_failures': sum(r['solved'] is False for r in rows),
        'unknown_grades': sum(r['solved'] is None for r in rows),
        'original_controller_outcomes': {
            'passed': sum(grades.get(key) is True for key in tasks),
            'failed': sum(grades.get(key) is False for key in tasks),
            'not_recorded': sum(type(grades.get(key)) is not bool for key in tasks)},
        'selected_passes': sum(r['solved'] is True for r in chosen) if selected is not None else None,
        'planning_states': dict(Counter(r['planning_state'] for r in rows)),
        'selection_states': dict(Counter(r['selection_state'] for r in rows)),
        'planning_recommendations': dict(Counter(r['recommendation'] or 'not_observed' for r in rows)),
        'execution_states': dict(Counter(r['execution_state'] for r in rows)),
        'eligibility_shortage': max(0, selection['requested'] - len(selection['selected'])) if selection else None,
        'selection': selection, 'allocation_result': allocation, 'allocation_policy': policy,
        'task_stages': archive.json(prefix + 'task-stages.json'),
        'serving_ready': archive.json(prefix + 'serving-ready.json'),
        'server_reuse': archive.json('jobs/' + job['id'] + '/server-reuse.json'),
        'execution_complete': 'jobs/' + job['id'] + '/experiment.complete' in archive.members,
        'execution_failed': 'jobs/' + job['id'] + '/experiment.failed' in archive.members,
        'scheduler': requests(all_requests), 'tasks': rows,
        'measurement_limits': ['Shell durations cover observed unique process completions. Non-shell tools and final-only grader durations are not independently instrumented. Interactive grading is timed separately; intervals can overlap.',
                               'Generation-only and exact allocation-admission timings are unavailable in these nonstream receipts.']}
    return result


def report(run, output):
    run = run.resolve()
    events = [json.loads(line) for line in (run / 'controller-events.jsonl').read_text().splitlines()]
    batch = json.loads((run / 'batch.json').read_text())
    archive = Archive(run, 'cpu', events, retain=report_member)
    try:
        result = {'run': run.name, 'archive': archive.proof, 'cells': [cell(archive, job) for job in batch['jobs']],
                  'controller_events': events,
                  'archive_view': {'retained_bytes': sum(size for _, size in archive.offsets.values()),
                                   'retained_members': len(archive.offsets), 'decompression_passes': 1},
                  'interpretation': 'Verified original-grade repairs require matching candidate/source/grade evidence. The separate original controller tally also includes empty submissions for unselected tasks. Unknown verification, infrastructure failures and model assessments stay separate; all cohort and selected-task denominators are retained.'}
        baseline = result['cells'][0]
        before = {r['instance_id']: r for r in baseline['tasks']}
        for current in result['cells'][1:]:
            if current['cohort_sha256'] != baseline['cohort_sha256']:
                raise ValueError('Cannot compare different cohorts')
            current['versus_first'] = [{'instance_id': row['instance_id'], 'before': before[row['instance_id']]['solved'],
                'after': row['solved'], 'selected_before': before[row['instance_id']]['selected'],
                'selected_after': row['selected'], 'completion_tokens_before': before[row['instance_id']]['scheduler']['completion_tokens'],
                'completion_tokens_after': row['scheduler']['completion_tokens']} for row in current['tasks']]
        output.write_text(json.dumps(result, indent=2) + '\n')
        return {'report': str(output), 'cells': [{k: c[k] for k in ('name', 'cohort_count', 'selected_count', 'original_passes', 'original_failures', 'unknown_grades', 'execution_complete', 'execution_failed')} for c in result['cells']]}
    finally:
        archive.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(report(args.run, args.output), indent=2))
