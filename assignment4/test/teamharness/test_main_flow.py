"""Continuing main through real HTTP, source, jobs, independent checks and grade.

This scripted provider establishes runtime mechanics, not Qwen repair quality.
Set TEAMHARNESS_MAIN_EVIDENCE to a new directory to keep the full local CLI run.
"""
from contextlib import closing, contextmanager
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from receipt_fixture import original_receipt, session_facts
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from src import workspace as effects
from src.teamharness.agents import AgentManager
from src.teamharness.candidates import Grader
from src.teamharness.main import Investigation
from src.teamharness.session import Session
from src.teamharness.tools import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]
TASK = 'Implement answer(n): return n unchanged for all integers. Keep the original public signature.'
FOLLOWUP = 'Continue the same main. Preserve the chosen repair and check the still-running original job.'


def call(name, args):
    return {'choices': [{'finish_reason': 'tool_calls', 'message': {
        'role': 'assistant', 'content': None, 'reasoning_content': 'Use the observed result.',
        'tool_calls': [{'id': 'call-' + str(time.time_ns()), 'type': 'function',
                        'function': {'name': name, 'arguments': json.dumps(args)}}]}}]}


def finish(report='Returned with observed evidence.', **extra):
    return call('finish_role', {'outcome': 'complete', 'report': report, **extra})


def last_tool(request):
    return original_receipt(next(m['content'] for m in reversed(request['messages']) if m['role'] == 'tool'))


def facts(request):
    return session_facts(request)


def repository(root):
    repo = root / 'repo'
    repo.mkdir(parents=True)
    (repo / 'answer.py').write_text('def answer(n):\n    return -1\n')
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@local', 'commit', '-qm', 'base'], check=True)
    return repo


def audit_requests(database):
    """Read-only full projection from saved states; never constructs a session."""
    requests = []
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        for ident, raw in db.execute("SELECT id,body FROM objects WHERE kind='model_request' ORDER BY id").fetchall():
            event = json.loads(raw)
            request = event['request']
            if event['purpose'] == 'agent':
                state = json.loads(db.execute("SELECT body FROM objects WHERE kind='session_state' AND id<? ORDER BY id DESC LIMIT 1", (ident,)).fetchone()[0])
                renderer = SimpleNamespace(provider=SimpleNamespace(model=request['model']), database=Path(database))
                assert Session._request(renderer, state) == request, 'Saved state does not reconstruct complete request'
            requests.append(request)
    return requests


@contextmanager
def http_script(script):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            script.requests.append(request)
            try:
                response = script.reply(request)
                code = 200
            except Exception as error:
                script.errors.append(repr(error))
                response, code = {'error': {'message': repr(error), 'code': 400}}, 400
            raw = json.dumps(response).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                # Explicit CLI cancellation may close an in-flight HTTP request.
                pass
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {'A4_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
               'A4_MODEL': 'scripted-local-main', 'A4_API_KEY': 'fixture-local-only',
               'A4_API_PROTOCOL': 'chat_completions'}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class MainScript:
    def __init__(self, public_task=TASK):
        self.public_task = public_task
        self.phase = 0
        self.children = {}
        self.requests, self.errors, self.receipts = [], [], {}
        self.fixed = threading.Event()
        self.main_id = self.test_id = self.review_id = self.job_id = None
        self.partial_checkpoint = self.final_checkpoint = None
        self.compactions = 0
        self.planning_choices = set()

    def planning_choice(self, request):
        f = facts(request)
        if f.get('planning_boundary', {}).get('enabled') and f['agent_id'] not in self.planning_choices:
            if not any(t['function']['name'] == 'repair_plan' for t in request['tools']):
                return call('read_file', {'path': 'answer.py'})
            self.planning_choices.add(f['agent_id'])
            return call('repair_plan', {'action': 'continue',
                'plan': 'Repair the public answer function and verify it through an actual consumer.',
                **({} if f['planning_boundary'].get('version') == 2 else
                   {'checks': [{'name': 'public-consumer', 'argv': [sys.executable, '-B', '-c',
                    'from answer import answer; assert answer(3) == 3']}]})})

    def reply(self, request):
        f = facts(request)
        if 'response_format' in request:
            assert 'tools' not in request and 'tool_choice' not in request
            self.compactions += 1
            return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps({
                'continuation': 'Model interpretation: preserve task, exact candidate checkpoints and running job ' + str(self.job_id) + '. Original inputs are distinct from this summary. Continue using recorded effects.'})}}]}
        decision = self.planning_choice(request)
        if decision is not None:
            return decision
        if f['parent'] is not None:
            stage = self.children.get(f['agent_id'], 0)
            self.children[f['agent_id']] = stage + 1
            if 'Your role is independent test author' in request['messages'][0]['content']:
                if stage == 0:
                    assert 'return n if n < 7' not in str(request['messages'])
                    return call('apply_patch', {'patch': '*** Begin Patch\n*** Add File: check_behavior.py\n+from answer import answer\n+failures = [n for n in range(10) if answer(n) != n]\n+assert not failures, failures\n+print("real consumer passed ten cases")\n*** End Patch\n'})
                if stage == 1:
                    return call('exec_command', {'command': 'python3 -B check_behavior.py'})
                self.receipts['baseline_test'] = last_tool(request)
                assert self.receipts['baseline_test']['exit_code'] != 0
                return finish('Independent original-contract check executed and failed on baseline; artifact is check_behavior.py.')
            self.review_id = f['agent_id']
            if stage == 0:
                assert 'tests failed: ' in request['messages'][1]['content']
                assert 'return n if n < 7' not in request['messages'][1]['content']
                assert self.fixed.wait(15), 'Main did not continue while the reviewer was active'
                return call('exec_command', {'command': 'python3 -B -c "from answer import answer; print([n for n in range(10) if answer(n) != n])"'})
            observed = last_tool(request)
            self.receipts['review_probe'] = observed
            assert observed['exit_code'] == 0 and '[7, 8, 9]' in json.dumps(observed)
            return finish('Observed [7, 8, 9] fail through the real caller on the graded snapshot. Later main edits were not inspected.')
        self.main_id = f['agent_id']
        phase = self.phase
        self.phase += 1
        guide_root = Path(f['archive_root']).parents[1] / 'guides'
        if phase == 0:
            if self.public_task is None:
                self.public_task = request['messages'][1]['content'].split('\n\n', 1)[1]
                assert self.public_task.startswith('PROBLEM STATEMENT\n' + TASK + '\n\n')
            assert 'evaluate_candidate' in {t['function']['name'] for t in request['tools']}
            return call('read_file', {'path': str(guide_root / 'README.md')})
        if phase == 1:
            self.receipts['catalog'] = last_tool(request)
            return call('read_file', {'path': str(guide_root / 'languages/python.md'), 'start_line': 68, 'line_count': 55})
        if phase == 2:
            self.receipts['guide_body'] = last_tool(request)
            return call('spawn_agent', {'role': 'test_author', 'task': TASK + '\nWrite a small independent real-caller regression and execute it. Edit only the new test file.', 'workspace': 'private'})
        if phase == 3:
            self.test_id = last_tool(request)['agent_id']
            return call('wait_agent', {'agent_id': self.test_id, 'timeout_seconds': 15})
        if phase == 4:
            assert last_tool(request)['status'] == 'completed'
            return call('apply_agent_patch', {'agent_id': self.test_id})
        if phase == 5:
            assert last_tool(request)['applied']
            return call('apply_patch', {'patch': '*** Begin Patch\n*** Update File: answer.py\n@@\n def answer(n):\n-    return -1\n+    return n if n < 7 else -1\n*** End Patch\n'})
        if phase == 6:
            return call('exec_command', {'command': 'python3 -B check_behavior.py'})
        if phase == 7:
            self.receipts['partial_test'] = last_tool(request)
            assert self.receipts['partial_test']['exit_code'] != 0
            return call('exec_command', {'command': shlex.quote(sys.executable) + ' -B -c "import time; print(\'persistent-job\', flush=True); time.sleep(120)"', 'yield_time_ms': 0})
        if phase == 8:
            self.job_id = last_tool(request)['id']
            return call('compact', {})
        if phase == 9:
            assert self.public_task in [m.get('content') for m in request['messages'] if m['role'] == 'user']
            return call('list_jobs', {})
        if phase == 10:
            self.receipts['jobs_after_compaction'] = last_tool(request)
            assert self.job_id in json.dumps(self.receipts['jobs_after_compaction'])
            return call('evaluate_candidate', {})
        if phase == 11:
            receipt = self.receipts['partial_grade'] = last_tool(request)
            assert {k: receipt['grade']['receipt'][k] for k in ('tests_failed', 'tests_total')} == {'tests_failed': 3, 'tests_total': 10}
            assert not receipt['handoff_requested']
            self.partial_checkpoint = receipt['checkpoint_id']
            return call('evaluate_candidate', {'checkpoint_id': self.partial_checkpoint})
        if phase == 12:
            self.receipts['reused_grade'] = last_tool(request)
            assert self.receipts['reused_grade']['reused']
            return call('apply_patch', {'patch': '*** Begin Patch\n*** Update File: answer.py\n@@\n def answer(n):\n-    return n if n < 7 else -1\n+    return n\n*** End Patch\n'})
        if phase == 13:
            assert last_tool(request)['applied']
            self.fixed.set()
            return call('exec_command', {'command': 'python3 -B check_behavior.py'})
        if phase == 14:
            self.receipts['fixed_test'] = last_tool(request)
            assert self.receipts['fixed_test']['exit_code'] == 0
            return call('task_status', {})
        if phase == 15:
            return call('task_status', {})
        if phase == 16:
            self.receipts['task_status'] = last_tool(request)
            return call('evaluate_candidate', {})
        if phase == 17:
            self.receipts['passing_grade'] = last_tool(request)
            assert self.receipts['passing_grade']['grade']['passed'] is True
            assert self.receipts['passing_grade']['review_agent_id'] is None
            self.final_checkpoint = self.receipts['passing_grade']['checkpoint_id']
            return call('exec_command', {'command': "printf 'later working notes' > working-notes.txt; printf 'continued after passing grade\\n'"})
        if phase == 18:
            self.receipts['after_pass'] = last_tool(request)
            assert self.receipts['after_pass']['exit_code'] == 0
            return finish('Real consumer and exact grader passed; selected checked bytes, preserving later live notes and original running job.', checkpoint_id=self.final_checkpoint)
        if phase == 19:
            assert FOLLOWUP in [m.get('content') for m in request['messages'] if m['role'] == 'user']
            return call('compact', {})
        if phase == 20:
            users = [m.get('content') for m in request['messages'] if m['role'] == 'user']
            assert users.count(self.public_task) == users.count(FOLLOWUP) == 1
            return call('list_jobs', {})
        if phase == 21:
            self.receipts['resumed_jobs'] = last_tool(request)
            assert self.job_id in json.dumps(self.receipts['resumed_jobs'])
            return finish('Same conversation, task, follow-up, chosen candidate and original job preserved.', checkpoint_id=self.final_checkpoint)
        raise AssertionError('Unexpected main phase ' + str(phase))


class MainFlowTests(unittest.TestCase):
    def test_cli_main_review_fix_compaction_resume_and_exact_checkpoint(self):
        retained = os.environ.get('TEAMHARNESS_MAIN_EVIDENCE')
        temp = None if retained else tempfile.TemporaryDirectory()
        root = Path(retained or temp.name).resolve()
        if retained:
            root.mkdir(parents=True, exist_ok=False)
        script = MainScript()
        repo = repository(root)
        grader = root / 'grader.py'
        grader.write_text('import json\nfrom answer import answer\nprint(json.dumps({"tests_failed": sum(answer(n) != n for n in range(10)), "tests_total": 10}))\n')
        # Invoke the public module in a fresh process, not a test-only loop.
        grade_command = [sys.executable, '-B', '-c', 'exec(compile(open(' + repr(str(grader)) + ').read(), ' + repr(str(grader)) + ', "exec"))']
        task = root / 'task.txt'
        task.write_text(TASK)
        output = root / 'run'
        try:
            with http_script(script) as env:
                env = {**os.environ, **env, 'PYTHONDONTWRITEBYTECODE': '1'}
                start = subprocess.run([sys.executable, '-B', '-m', 'src.teamharness', 'run', '--task', str(task),
                    '--workspace', str(repo), '--output', str(output), '--grader-argv', json.dumps(grade_command)],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
                (root / 'cli-run.stdout').write_text(start.stdout)
                (root / 'cli-run.stderr').write_text(start.stderr)
                self.assertEqual(script.errors, [])
                self.assertEqual(start.returncode, 0, start.stderr + start.stdout[-3000:])
                first = json.loads(start.stdout)
                main_id = first['agents']['main']
                descriptor = json.loads((output / 'agents/agents' / main_id / 'agent.json').read_text())
                before = json.loads((Path(descriptor['root']) / 'session.json').read_text())
                self.assertEqual(first['workflow'], 'main')
                self.assertEqual(first['state'], 'completed')
                self.assertEqual(set(first['agents'].keys()), {'main'})
                self.assertEqual(len(first['evaluations']), 2)
                self.assertEqual(first['candidate']['sha256'], script.receipts['passing_grade']['candidate']['sha256'])
                self.assertNotIn('working-notes.txt', Path(first['candidate']['patch_path']).read_text())
                self.assertTrue((Path(descriptor['workspace']) / 'working-notes.txt').exists())
                self.assertEqual((repo / 'answer.py').read_text(), 'def answer(n):\n    return n\n')
                self.assertEqual(script.receipts['partial_grade']['review_agent_id'], script.receipts['reused_grade']['review_agent_id'])
                self.assertIn('recorded events and attributed model reports', str(script.requests))
                self.assertTrue(any('Configuration reaches' in json.dumps(r) or 'reference library' in json.dumps(r)
                                    for r in [script.receipts['catalog']]))
                self.assertIn('None', json.dumps(script.receipts['guide_body']))
                resumed = subprocess.run([sys.executable, '-B', '-m', 'src.teamharness', 'resume',
                    '--output', str(output), '--message', FOLLOWUP], cwd=ROOT, env=env,
                    capture_output=True, text=True, timeout=30)
                (root / 'cli-resume.stdout').write_text(resumed.stdout)
                (root / 'cli-resume.stderr').write_text(resumed.stderr)
                self.assertEqual(script.errors, [])
                self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout[-3000:])
                second = json.loads(resumed.stdout)
                after = json.loads((Path(descriptor['root']) / 'session.json').read_text())
                self.assertEqual(first['agents'], second['agents'])
                self.assertEqual(first['candidate'], second['candidate'])
                self.assertEqual(before['session_id'], after['session_id'])
                self.assertEqual(after['epoch'], 2)
                self.assertEqual(len(second['evaluations']), 2)
                self.assertEqual(script.compactions, 2)
                self.assertIsNone(json.loads((output / 'agents/manager.json').read_text())['max_agents'])
                with closing(sqlite3.connect(Path(descriptor['root']) / 'session.sqlite3')) as db:
                    originals = [json.loads(row[0]) for row in db.execute("SELECT body FROM objects WHERE kind='task_input'")]
                self.assertEqual(originals, [{'author': 'user', 'text': TASK}])
                saved_requests = [request for path in (output / 'agents/agents').glob('*/session.sqlite3')
                                  for request in audit_requests(path)]
                fingerprint = lambda request: json.dumps(request, sort_keys=True)
                self.assertEqual(Counter(map(fingerprint, saved_requests)), Counter(map(fingerprint, script.requests)))
                self.assertTrue(any(m.get('reasoning_content') == 'Use the observed result.'
                                    for request in script.requests for m in request['messages'] if m['role'] == 'assistant'))
                (root / 'verification.json').write_text(json.dumps({'state': 'passed', 'provider': 'scripted-local-main',
                    'main_id': main_id, 'requests': len(script.requests), 'grade_count': len(second['evaluations']),
                    'review_count': len(second['reviews']), 'compactions': script.compactions,
                    'candidate_sha256': second['candidate']['sha256'], 'same_session': before['session_id'] == after['session_id'],
                    'read_only_request_reconstruction': True, 'all_http_requests_match_archives': True,
                    'limitations': 'Local mechanical validation, not Qwen competence or an original benchmark grade.'}, indent=2))
        finally:
            # The CLI deliberately left the actual shell process alive. Test
            # cleanup explicitly signals it and observes the result.
            if script.main_id and script.job_id:
                d = json.loads((output / 'agents/agents' / script.main_id / 'agent.json').read_text())
                ws = {'root': d['workspace'], 'state': str(Path(d['root']) / 'workstate'), 'base': d['base'], 'capture_patch': True}
                effects.dispatch('job', {'workspace': ws, 'id': script.job_id, 'action': 'kill', 'yield_time_ms': 1000})
            if temp:
                temp.cleanup()


class GradeScript:
    model = 'fixture'
    def __init__(self, release=None):
        self.steps, self.requests, self.grades = {}, [], []
        self.release = release
    def complete(self, request):
        self.requests.append(request)
        ident = facts(request)['agent_id']
        step = self.steps.get(ident, 0)
        self.steps[ident] = step + 1
        if facts(request)['parent'] is not None:
            if self.release is not None:
                assert self.release.wait(10), 'Test did not release advisory review'
            return finish('Independent advice, not approval.')
        if step < 2:
            if step:
                self.grades.append(last_tool(request))
            return call('evaluate_candidate', {})
        self.grades.append(last_tool(request))
        return finish()


class GradePolicyTests(unittest.TestCase):
    def test_no_grader_main_has_no_evaluation_capability_or_required_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = repository(root)
            requests = []
            class Provider:
                model = 'fixture'
                def complete(self, request):
                    requests.append(request)
                    return finish('A direct main return with no prescribed team sequence.')
            manager = AgentManager(root / 'run/agents', lambda _: Provider())
            self.addCleanup(manager.shutdown)
            result = Investigation(manager, root / 'run').run(TASK, repo)
            self.assertEqual(result['state'], 'completed')
            self.assertEqual(set(result['agents']), {'main'})
            self.assertEqual(result['reviews'], {})
            self.assertNotIn('evaluate_candidate', {t['function']['name'] for t in requests[0]['tools']})

    def test_real_grader_boundary_and_no_invented_counts(self):
        cases = [(3, 10, 0), (1, 4, 0), (4, 10, 0), (1, 3, 0),
                 (0, 10, 0), (0, 0, 0), (True, 10, 0), (-1, 10, 0), (11, 10, 0)]
        with tempfile.TemporaryDirectory() as directory:
            for index, (failed, total, expected_reviews) in enumerate(cases):
                with self.subTest(failed=failed, total=total):
                    root = Path(directory) / str(index)
                    repo = repository(root)
                    script = GradeScript()
                    manager = AgentManager(root / 'run/agents', lambda _: script)
                    self.addCleanup(manager.shutdown)
                    workflow = Investigation(manager, root / 'run')
                    grader = Grader((sys.executable, '-B', '-c', 'print(' + repr(json.dumps({'tests_failed': failed, 'tests_total': total})) + ')'), 'fixture-counts', False)
                    result = workflow.run(TASK, repo, grader)
                    manager.shutdown()
                    self.assertEqual(result['state'], 'completed', result)
                    self.assertEqual(len(result['reviews']), expected_reviews)
                    self.assertEqual(len(result['evaluations']), 1)
                    self.assertTrue(script.grades[1]['reused'])
                    self.assertIs(script.grades[0]['grade']['official'], False)
                    if failed is True or failed < 0 or failed > total:
                        self.assertEqual(script.grades[0]['grade']['counts_status'], 'unavailable')
                    else:
                        self.assertEqual(script.grades[0]['grade']['receipt'], {'tests_failed': failed, 'tests_total': total})

    def test_unknown_grade_not_replayed_and_main_can_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = repository(root)
            calls = []
            def grade(candidate):
                calls.append(candidate)
                raise ConnectionError('Receipt lost after submission; effect may have occurred')
            script = GradeScript()
            manager = AgentManager(root / 'run/agents', lambda _: script)
            self.addCleanup(manager.shutdown)
            workflow = Investigation(manager, root / 'run', feedback_grader=grade)
            result = workflow.run(TASK, repo)
            self.assertEqual(result['state'], 'completed')
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['reviews'], {})
            self.assertTrue(all(g['grading_state'] == 'unknown' and not g['replay_safe'] for g in script.grades))
            manager.shutdown()
            script.steps[result['agents']['main']] = 0
            resumed_manager = AgentManager(root / 'run/agents', lambda _: script)
            self.addCleanup(resumed_manager.shutdown)
            resumed = Investigation(resumed_manager, root / 'run', feedback_grader=grade).resume()
            self.assertEqual(resumed['agents'], result['agents'])
            self.assertEqual(len(calls), 1)

    def test_grader_source_mutation_or_missing_count_output_is_not_eligible_review(self):
        programs = [
            'from pathlib import Path; Path("answer.py").write_text("def answer(n): return 99\\n"); print(\'{"tests_failed":3,"tests_total":10}\')',
            'print("3 failed, 7 passed")']
        with tempfile.TemporaryDirectory() as directory:
            for index, program in enumerate(programs):
                with self.subTest(program=program):
                    root = Path(directory) / str(index)
                    repo = repository(root)
                    script = GradeScript()
                    manager = AgentManager(root / 'run/agents', lambda _: script)
                    self.addCleanup(manager.shutdown)
                    grader = Grader((sys.executable, '-B', '-c', program), 'fixture', False)
                    result = Investigation(manager, root / 'run').run(TASK, repo, grader)
                    manager.shutdown()
                    grade = script.grades[0]['grade']
                    self.assertEqual(result['state'], 'completed')
                    self.assertEqual(result['reviews'], {})
                    self.assertEqual(grade['counts_status'], 'unavailable')
                    self.assertIsNone(grade['passed'])
                    self.assertEqual(grade['execution']['source_unchanged'], index != 0)
                    self.assertEqual((repo / 'answer.py').read_text(), 'def answer(n):\n    return -1\n')



if __name__ == '__main__':
    unittest.main()
