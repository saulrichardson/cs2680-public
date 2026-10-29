"""Full packaged course-main flow on local POSIX with a scripted HTTP model.

Uses the real sandbox server, relay, worker subprocess, interactive grading
bridge, final replay, evidence export and read-only selection verifier. No cloud
or live model is used. Run with --package, --repo and a new --output. Linux also
exercises the course's /proc process-group observation; other hosts are identified
in the report and independently verify the owned process groups with killpg(0).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import threading
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-repair-guidance', action='store_true',
                        help='Exercise actual catalog, skill and reference reads before source implementation')
    parser.add_argument('--completion-handoff', action='store_true',
                        help='Exercise first failed completion and new-conversation ownership through the course bridge')
    args = parser.parse_args()
    if args.completion_handoff and args.require_repair_guidance:
        parser.error('Choose one scripted intervention per check')
    package, repo = args.package.resolve(), args.repo.resolve()
    sys.path[:0] = [str(package), str(repo)]
    from src.agentic_loop import Agent
    from src import sandbox_server
    from src.teamharness.course_worker import choose_candidate
    from lab.adapters.teamharness.report_fleet import verify_selection
    spec = importlib.util.spec_from_file_location('main_flow_fixture', repo / 'test/teamharness/test_main_flow.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    MainScript, TASK, http_script, repository = (getattr(fixture, name) for name in
                                               ('MainScript', 'TASK', 'http_script', 'repository'))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.chdir(output)
    campaign = json.loads((package / 'src/campaign.json').read_text())
    assert campaign['workflow'] == 'main' and campaign['grade_feedback']
    source = repository(output)
    base = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    (source / 'initial.txt').write_text('Dirty initial source must survive course replay.\n')
    sandbox_server.ROOT = str(source)
    sandbox_server.WORK_ROOT = str(output / 'task-state')
    sandbox = ThreadingHTTPServer(('127.0.0.1', 0), sandbox_server.Handler)
    threading.Thread(target=sandbox.serve_forever, daemon=True).start()
    class CompletionScript(MainScript):
        def __init__(self):
            super().__init__(public_task=None)
            self.owner_steps, self.first_owner = {}, None
        def reply(self, request):
            f = fixture.facts(request)
            decision = self.planning_choice(request)
            if decision is not None:
                return decision
            ident = f['agent_id']
            self.first_owner = self.first_owner or ident
            step = self.owner_steps.get(ident, 0)
            self.owner_steps[ident] = step + 1
            assert f['parent'] is None
            if step == 0:
                if ident != self.first_owner:
                    assert all(m.get('tool_calls') and all(c['function']['name'] == 'repair_plan' for c in m['tool_calls'])
                               for m in request['messages'] if m['role'] == 'assistant')
                    assert 'OLD_COMPLETION_CONCLUSION' not in str(request['messages'])
                    descriptor = json.loads((Path(f['archive_root']) / 'agent.json').read_text())
                    assert 'return n if n < 7 else -1' in (Path(descriptor['workspace']) / 'answer.py').read_text()
                expression = 'n if n < 7 else -1' if ident == self.first_owner else 'n'
                return fixture.call('exec_command', {'command':
                    "printf 'def answer(n):\\n    return " + expression + "\\n' > answer.py"})
            return fixture.finish('OLD_COMPLETION_CONCLUSION' if ident == self.first_owner else 'Fresh repair completed.')

    class GuidedScript(MainScript):
        def __init__(self):
            super().__init__(public_task=None)
            self.read_steps = {}
        def reply(self, request):
            if 'response_format' in request:
                return super().reply(request)
            system = request['messages'][0]['content']
            assert 'Only finish_role ends your investigation' in system or 'working agent' in system
            assert 'Host instruction for this documentation treatment' in system
            f = fixture.facts(request)
            ident = f['agent_id']
            step = self.read_steps.get(ident, 0)
            root = Path(system.split('Library root: ', 1)[1].splitlines()[0])
            paths = [root / 'SKILLS.md', root / 'skills/verify-contracts-and-tests/SKILL.md',
                     root / 'skills/preserve-representations/references/coercion-and-metadata.md']
            if step:
                if step <= len(paths):
                    receipt = fixture.last_tool(request)
                    # Full, literal tool content must have reached the next request.
                    assert receipt['content'] == paths[step-1].read_text()
            if step < len(paths):
                self.read_steps[ident] = step + 1
                return fixture.call('read_file', {'path': str(paths[step]), 'line_count': 10000,
                                                  'max_output_bytes': 65536})
            self.read_steps[ident] = step + 1
            return super().reply(request)
    script = (CompletionScript() if args.completion_handoff else
              GuidedScript() if args.require_repair_guidance else MainScript(public_task=None))
    task = {'task': 'local-persistent-main', 'workdir': str(source),
            'sandbox_url': f'http://127.0.0.1:{sandbox.server_port}', 'problem_statement': TASK}

    class Dispatcher:
        def __init__(self):
            self.submissions, self.grades, self.continuations = [], [], []
        def submit_patch(self, ident, text):
            assert ident == task['task']
            self.submissions.append(text.encode())
        def continue_task(self, ident):
            self.continuations.append(ident)
        def evaluate(self, ident):
            raw = self.submissions[-1]
            index = len(self.grades)
            target = output / ('original-grader-' + str(index))
            subprocess.run(['git', 'clone', '-q', str(source), str(target)], check=True)
            subprocess.run(['git', '-C', str(target), 'reset', '--hard', base], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(target), 'apply', '--binary', '-'], input=raw, check=True)
            assert (target / 'initial.txt').read_text().startswith('Dirty initial')
            result = subprocess.run([sys.executable, '-B', '-c', 'import json; from answer import answer; print(json.dumps({"tests_failed":sum(answer(n)!=n for n in range(10)),"tests_total":10}))'],
                                    cwd=target, check=True, capture_output=True, text=True)
            grade = {**json.loads(result.stdout), 'attempt': index}
            (output / ('original-graded-' + str(index) + '.patch')).write_bytes(raw)
            self.grades.append({'receipt': grade, 'sha256': hashlib.sha256(raw).hexdigest()})
            return grade
        def done(self, ident, reason, calls):
            assert reason == 'completed'
            assert self.grades[-1]['sha256'] == hashlib.sha256(self.submissions[-1]).hexdigest()
            return {**self.grades[-1]['receipt'], 'candidate_sha256': self.grades[-1]['sha256'], 'reason': reason}
        def logger(self, ident):
            return self
        def api_request(self, *args):
            pass

    dispatcher = Dispatcher()
    try:
        with http_script(script) as env, patch.dict(os.environ, {
                'CS2680_BASE_URL': env['A4_BASE_URL'], 'CS2680_MODEL_STANDARD': env['A4_MODEL'],
                'CS2680_API_KEY': 'fixture-upstream-key'}):
            Agent(dispatcher).solve(task, {**campaign, 'solve_seconds': 90, 'python': sys.executable,
                                          'state_root': str(output / 'task-state')})
    finally:
        sandbox.shutdown()
        sandbox.server_close()
    assert not script.errors, script.errors
    assert len(dispatcher.submissions) == len(dispatcher.grades) == 2
    assert len(dispatcher.continuations) == 1
    assert [g['receipt']['tests_failed'] for g in dispatcher.grades] == [3, 0]
    archive = output / 'madsServe_logs' / task['task'] / 'teamharness'
    files = {str(p.relative_to(archive)): p.read_bytes() for p in archive.rglob('*') if p.is_file()}
    workflow = json.loads(files['run/workflow.json'])
    candidate = json.loads(files['candidate.json'])
    selection = json.loads(files['selection.json'])
    assert workflow['workflow'] == 'main' and workflow['state'] == 'completed'
    assert selection['kind'] == 'main_candidate' and selection['reviewed'] is False
    assert verify_selection(files, workflow, candidate)
    assert candidate['sha256'] != selection['selected_sha256']  # retained dirty initial patch
    assert candidate['sha256'] == dispatcher.grades[-1]['sha256']
    assert len(workflow['reviews']) == 0 and len(workflow['evaluations']) == 2
    assert 'working-notes.txt' not in files['candidate.patch'].decode()
    jobs = json.loads(files['owned-jobs-stop.json'])
    assert jobs and all(j['state'] == 'exited' and j['group_stopped'] for j in jobs)
    for job in jobs:
        if job.get('process_group'):
            try:
                os.killpg(job['process_group'], 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError('Owned process group still exists after course finalization')
    assert not any(p.name in ('workspace', 'baseline') for p in archive.rglob('*'))
    assert all(b'fixture-upstream-key' not in raw for raw in files.values())
    if args.require_repair_guidance or args.completion_handoff:
        from collections import Counter
        archived = [request for path in (archive / 'run/agents/agents').glob('*/session.sqlite3')
                    for request in fixture.audit_requests(path)]
        fingerprint = lambda request: json.dumps(request, sort_keys=True)
        from src.backend import course_request
        projected = [json.loads(course_request(json.dumps(request).encode())[0]) for request in archived]
        assert archived and Counter(map(fingerprint, projected)) == Counter(map(fingerprint, script.requests))
        if args.require_repair_guidance:
            assert all(step > 3 for step in script.read_steps.values())
            assert b'portable-repair' not in files['candidate.patch']
        else:
            assert len(workflow['handoff_history']) == 1
            assert script.owner_steps == {workflow['agents']['original_main']: 2, workflow['agents']['main']: 2}
            assert workflow['agents']['original_main'] != workflow['agents']['main']
            attempts = list(workflow['completion_attempts'].values())
            assert [a['state'] for a in attempts] == ['failed', 'passed']
            assert workflow['handoff_history'][0]['starting_candidate']['sha256'] == attempts[0]['candidate']['sha256']
    # A controller can disappear after the main's durable return but before its
    # derived workflow projection. Preserve its explicitly selected checkpoint,
    # even though later live working-notes.txt still exists. Change only a copy
    # of the projection; original evidence remains intact.
    fallback = output / 'returned-main-fallback'
    shutil.copytree(archive / 'run', fallback / 'run')
    interrupted = copy.deepcopy(workflow)
    interrupted.update(state='interrupted')
    interrupted.pop('candidate')
    (fallback / 'run/workflow.json').write_text(json.dumps(interrupted))
    returned_selection, components = choose_candidate({'state': str(fallback), 'course_base': base})
    assert returned_selection['kind'] == 'main_returned_candidate'
    assert returned_selection['selected_sha256'] == selection['selected_sha256']
    assert b'working-notes.txt' not in components[-1]
    derived = dict(files)
    derived['selection.json'] = json.dumps(returned_selection).encode()
    derived['run/workflow.json'] = json.dumps(interrupted).encode()
    final = json.loads(files['finalization.json'])
    final['selection_kind'] = returned_selection['kind']
    derived['finalization.json'] = json.dumps(final).encode()
    assert verify_selection(derived, interrupted, candidate)
    report = {'passed': True, 'platform': sys.platform, 'workflow': 'main',
        'interactive_grades': len(dispatcher.grades), 'failed_counts': [3, 0],
        'automatic_reviews': len(workflow['reviews']), 'original_base_replay_verified': True,
        'final_grade_reused': True, 'selection_verified': True, 'returned_checkpoint_fallback_verified': True,
        'owned_jobs_stopped_at_course_finalization': True,
        'candidate_sha256': candidate['sha256'], 'model_quality_measured': False}
    if args.require_repair_guidance:
        report['repair_guidance'] = {'catalog_skill_reference_reads_before_implementation': True,
            'sessions': len(script.read_steps), 'read_only_request_reconstruction': True,
            'all_http_requests_match_archives': False, 'all_http_requests_match_declared_course_projection': True, 'library_excluded_from_candidate': True}
    if args.completion_handoff:
        report['completion_gate'] = {'first_failure_transferred': True, 'handoffs': 1,
            'fresh_conversation_verified': True, 'failed_candidate_preserved_exactly': True,
            'all_http_requests_match_archives': False, 'all_http_requests_match_declared_course_projection': True, 'read_only_request_reconstruction': True}
    (output / 'CHECK.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
