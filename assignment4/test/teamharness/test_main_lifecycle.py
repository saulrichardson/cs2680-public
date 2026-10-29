"""Real executor/receipt races for continuing-main helper returns."""
from collections import Counter
from contextlib import closing
import json
from receipt_fixture import session_facts
import os
from pathlib import Path
import sqlite3
import shlex
import signal
import subprocess
import sys
import time
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.teamharness.agents import AgentManager
from src.teamharness.main import Investigation
from src.teamharness.session import Session


def finished(text):
    return call('finish_role', {'outcome': 'complete', 'report': text})


def answer(text):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': text}}]}


def call(name, arguments):
    return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
        'tool_calls': [{'id': 'call', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}]}


class Script:
    model = 'fixture'
    def __init__(self):
        self.requests = []
        self.calls = Counter()
        self.lock = threading.Lock()
        self.active = Counter()
        self.maximum = Counter()
        self.block_main = None
        self.entered_main = threading.Event()
        self.child_release = None

    def complete(self, request):
        facts = session_facts(request)
        ident = facts['agent_id']
        with self.lock:
            self.requests.append(request)
            self.active[ident] += 1
            self.maximum[ident] = max(self.maximum[ident], self.active[ident])
            self.calls[ident] += 1
            number = self.calls[ident]
        try:
            if facts['parent']:
                if self.child_release:
                    assert self.child_release.wait(10)
                return answer('Helper interpretation for its assigned source.')
            if number == 1:
                return finished('Initial return; helpers may still run.')
            if self.block_main:
                self.entered_main.set()
                assert self.block_main.wait(10)
            # Runtime observations may be appended after a completed tool result.
            # Decide from the conversation exchange, not an observation heading.
            latest=next(m for m in reversed(request['messages']) if m['role']!='system')
            if latest['role'] != 'tool':
                return call('exec_command', {'command': "printf 'observed\\n' >> observed.txt", 'yield_time_ms': 1000})
            return finished('Continued main observed helper evidence and ran a real command.')
        finally:
            with self.lock:
                self.active[ident] -= 1


class MainLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        (self.repo / 'original').write_text('base\n')
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test', '-c', 'user.email=test@local', 'commit', '-qm', 'base'], check=True)
        self.script = Script()
        self.manager = AgentManager(self.root / 'run/agents', lambda _: self.script)
        self.addCleanup(self.manager.shutdown)
        self.workflow = Investigation(self.manager, self.root / 'run')
        result = self.workflow.run('Original human task.', self.repo)
        self.main = result['agents']['main']
        self.manager.shutdown()
        self.session_id = self.manager._session(self.main).session_id

    def child(self, assignment='Investigate a concrete caller.'):
        return self.manager.create('investigator', assignment, self.repo, parent=self.main, private=True)

    def notifications(self):
        with closing(sqlite3.connect(self.manager._path(self.main) / 'session.sqlite3')) as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM objects WHERE kind='control' AND json_extract(body,'$.observation.event')='agent_returned'")]

    def test_late_general_helper_wakes_same_main_once_and_report_is_source_bound(self):
        child = self.child()
        self.manager.start(child)
        self.manager.shutdown()
        self.assertEqual(self.manager.inspect(self.main)['status'], 'completed')
        self.assertEqual(self.manager._session(self.main).session_id, self.session_id)
        self.assertEqual((self.repo / 'observed.txt').read_text(), 'observed\n')
        notice = self.notifications()[0]
        self.assertTrue(notice['automatic'])
        evidence = notice['observation']
        self.assertEqual(evidence['model_report']['author'], child)
        self.assertEqual(evidence['assignment']['author'], self.main)
        self.assertEqual(evidence['source']['workspace'], self.manager.descriptor(child)['workspace'])
        self.assertEqual(evidence['artifacts']['patch'], self.manager.inspect(child)['patch'])
        self.workflow.agent_returned(child)
        self.workflow.reconcile_returns()
        self.manager.shutdown()
        self.assertEqual(len(self.notifications()), 1)
        self.assertEqual((self.repo / 'observed.txt').read_text(), 'observed\n')
        self.assertEqual(self.workflow.settled_report()['conclusion'], 'Continued main observed helper evidence and ran a real command.')

    def test_busy_main_queues_all_helpers_without_duplicate_executor(self):
        release = threading.Event()
        self.script.block_main = release
        first = self.child('First concrete assignment.')
        second = self.child('Second concrete assignment.')
        try:
            self.manager.start(first)
            self.assertTrue(self.script.entered_main.wait(5))
            self.manager.start(second)
            self.manager.wait(second, 5)
            # Join the child's completion callback, including evidence delivery.
            self.manager.threads[second].join(5)
            self.assertEqual(len(self.notifications()), 2)
            self.assertEqual(self.manager.start(self.main)['start'], 'already_running')
        finally:
            release.set()
            self.manager.shutdown()
        self.assertEqual(self.script.maximum[self.main], 1)
        delivered = json.dumps([r for r in self.script.requests if 'Harness notification' in json.dumps(r)])
        self.assertIn(first, delivered)
        self.assertIn(second, delivered)
        self.assertEqual(len(self.notifications()), 2)

    def test_stop_wins_before_and_after_automatic_evidence(self):
        child = self.child()
        self.manager.stop(self.main)
        self.manager.start(child)
        self.manager.shutdown()
        self.assertEqual(self.script.calls[self.main], 1)
        self.assertEqual(len(self.notifications()), 1)
        # Even explicit executor start does not override the human stop.
        self.manager.start(self.main)
        self.manager.shutdown()
        stopped = self.manager.inspect(self.main)
        self.assertEqual(stopped['status'], 'stopped', json.dumps(stopped, indent=2))
        self.assertEqual(self.script.calls[self.main], 1)
        self.workflow.resume(message='Continue with the preserved helper evidence.')
        self.manager.shutdown()
        self.assertEqual((self.repo / 'observed.txt').read_text(), 'observed\n')
        self.assertEqual(self.manager._session(self.main).session_id, self.session_id)

    def test_stop_during_automatic_inference_archives_response_without_effect(self):
        release = threading.Event()
        self.script.block_main = release
        child = self.child()
        try:
            self.manager.start(child)
            self.assertTrue(self.script.entered_main.wait(5))
            self.manager.stop(self.main)
        finally:
            release.set()
            self.manager.shutdown()
        stopped = self.manager.inspect(self.main)
        self.assertEqual(stopped['status'], 'stopped', json.dumps(stopped, indent=2))
        self.assertFalse((self.repo / 'observed.txt').exists())
        self.assertEqual(len(self.notifications()), 1)

    def test_capacity_deferred_wake_runs_when_other_executor_returns(self):
        # Capacity is an explicit deployment limit. Evidence remains queued.
        limited = AgentManager(self.root / 'limited', lambda _: self.script, max_agents=1)
        self.addCleanup(limited.shutdown)
        main = limited.create('main', 'Main task.', self.repo)
        limited.start(main)
        limited.shutdown()
        release = threading.Event()
        self.script.child_release = release
        blocker = limited.create('investigator', 'Hold actual occupied capacity.', self.repo, parent=main)
        limited.start(blocker)
        try:
            limited.return_observation(main, {'event': 'fixture_return', 'author': blocker}, 'capacity-evidence')
            self.assertEqual(self.script.calls[main], 1)
        finally:
            release.set()
            limited.shutdown()
        self.assertEqual(limited.inspect(main)['status'], 'completed')
        self.assertEqual((self.repo / 'observed.txt').read_text(), 'observed\n')
        limited.return_observation(main, {'event': 'fixture_return', 'author': blocker}, 'capacity-evidence')
        limited.shutdown()
        self.assertEqual((self.repo / 'observed.txt').read_text(), 'observed\n')

    def test_completion_capture_race_never_strands_return(self):
        from src.teamharness import candidates
        entered, release = threading.Event(), threading.Event()
        original = candidates.capture
        first_capture = True
        def capture(workspace, target, base):
            nonlocal first_capture
            if self.main in str(target) and first_capture:
                first_capture = False
                entered.set()
                assert release.wait(10)
            return original(workspace, target, base)
        self.manager.send(self.main, 'Return again while a helper finishes.')
        child = self.child()
        # Block the main after its model return and before completion publication.
        with patch.object(candidates, 'capture', capture):
            try:
                self.manager.start(self.main)
                self.assertTrue(entered.wait(5))
                self.manager.start(child)
                self.manager.wait(child, 5)
                self.manager.threads[child].join(5)
                self.assertEqual(len(self.notifications()), 1)
            finally:
                release.set()
                self.manager.shutdown()
        self.assertEqual(self.manager.inspect(self.main)['status'], 'completed')
        self.assertEqual(self.script.maximum[self.main], 1)
        with closing(sqlite3.connect(self.manager._path(self.main) / 'session.sqlite3')) as db:
            state = json.loads(db.execute("SELECT body FROM objects WHERE kind='session_state' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertIn(child, json.dumps(state['messages']))

    def test_durable_receipt_reconciles_missed_callback_without_replay(self):
        child = self.child()
        with patch.object(self.workflow, 'agent_returned', return_value=None):
            self.manager.start(child)
            self.manager.wait(child, 5)
            self.manager.threads[child].join(5)
        self.assertEqual(self.script.calls[self.main], 1)
        self.workflow.reconcile_returns()
        self.manager.shutdown()
        self.assertEqual(self.script.calls[child], 1)
        self.assertEqual((self.repo / 'observed.txt').read_text(), 'observed\n')
        self.assertEqual(len(self.notifications()), 1)

    def test_two_missed_returns_survive_runner_replacement_and_reconcile_once(self):
        child = self.child()
        with patch.object(self.workflow, 'agent_returned', return_value=None):
            self.manager.start(child)
            self.manager.wait(child, 5)
            self.manager.threads[child].join(5)
            self.manager.send(child, 'A distinct follow-up return.')
            self.manager.start(child)
            self.manager.wait(child, 5)
            self.manager.threads[child].join(5)
        self.assertEqual(self.script.calls[child], 2)
        self.assertEqual(self.script.calls[self.main], 1)
        self.workflow.reconcile_returns()
        self.manager.shutdown()
        self.assertEqual(len(self.notifications()), 2)
        self.workflow.reconcile_returns()
        self.manager.shutdown()
        self.assertEqual(len(self.notifications()), 2)
        self.assertEqual(self.script.calls[child], 2)
        self.assertEqual(self.script.maximum[self.main], 1)

    def test_late_grandchild_routes_original_authorship_and_stable_report_reference(self):
        parent = self.child('Delegate the actual nested assignment.')
        self.manager.start(parent)
        self.manager.shutdown()
        grandchild = self.manager.create('investigator', 'Nested assigned task.', self.repo,
                                        parent=parent, private=True)
        self.manager.start(grandchild)
        self.manager.shutdown()
        notice = next(c['observation'] for c in self.notifications()
                      if c['observation']['agent_id'] == grandchild)
        self.assertEqual(notice['parent_agent_id'], parent)
        self.assertEqual(notice['assignment']['author'], parent)
        self.assertNotIn('text', notice['model_report'])
        reference = notice['model_report']['reference']
        self.assertTrue(reference['ref'].startswith('obj:'))
        with closing(sqlite3.connect(reference['path'])) as db:
            original = json.loads(db.execute('SELECT body FROM objects WHERE id=?',
                (int(reference['ref'].split(':')[1]),)).fetchone()[0])
        self.assertEqual(original['report'], 'Helper interpretation for its assigned source.')
        self.assertEqual(original['patch'], self.manager.inspect(grandchild)['patch'])
        self.assertEqual(self.script.calls[self.main], 5)  # initial plus command/return for each descendant

    def test_cli_waits_for_late_return_then_prints_latest_main_receipt(self):
        from test_main_flow import http_script
        release = threading.Event()
        class LateHTTP:
            def __init__(self):
                self.requests, self.errors, self.steps = [], [], Counter()
                self.main_id = None
            def reply(inner, request):
                facts = session_facts(request)
                ident = facts['agent_id']
                if facts['parent']:
                    assert release.wait(15)
                    return answer('Late specialist finding.')
                inner.main_id = ident
                phase = inner.steps[ident]
                inner.steps[ident] += 1
                if phase == 0:
                    return call('spawn_agent', {'role': 'investigator', 'task': 'Check the caller independently.', 'workspace': 'private'})
                if phase == 1:
                    return finished('Initial main return before specialist.')
                if phase == 2:
                    return call('exec_command', {'command': "printf retained > continued.txt", 'yield_time_ms': 1000})
                return finished('Latest main conclusion after late specialist evidence.')
        script = LateHTTP()
        output = self.root / 'cli-run'
        task = self.root / 'cli-task.txt'
        task.write_text('Investigate the actual caller.')
        with http_script(script) as environment:
            process = subprocess.Popen([sys.executable, '-B', '-m', 'src.teamharness', 'run',
                '--no-completion-gate', '--no-planning-boundary',  # Isolate callback lifetime from screening policy.
                '--workspace', str(self.repo), '--task', str(task), '--output', str(output)],
                cwd=Path(__file__).resolve().parents[2], env={**os.environ, **environment},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                until = time.monotonic() + 10
                initial = None
                while time.monotonic() < until:
                    if script.main_id:
                        runner = output / 'agents/agents' / script.main_id / 'runner.json'
                        if runner.exists():
                            initial = json.loads(runner.read_text())
                            if initial['status'] == 'completed':
                                break
                    time.sleep(.025)
                self.assertIsNotNone(initial)
                self.assertEqual(initial['status'], 'completed')
                self.assertIsNone(process.poll())  # CLI owns still-active callback lifetime.
                release.set()
                stdout, stderr = process.communicate(timeout=20)
                receipts = {str(path.relative_to(output)): json.loads(path.read_text())
                            for path in output.glob('agents/agents/*/runner.json')}
                self.assertEqual(process.returncode, 0, stderr + '\nRunner receipts: ' + json.dumps(receipts, indent=2))
                report = json.loads(stdout)
                self.assertEqual(report['agents']['main'], script.main_id)
                self.assertEqual(report['conclusion'], 'Latest main conclusion after late specialist evidence.')
                self.assertEqual((self.repo / 'continued.txt').read_text(), 'retained')
                self.assertEqual(script.errors, [])
            finally:
                release.set()
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

    def test_cli_keyboard_interrupt_stops_wakes_and_preserves_running_shell(self):
        from test_main_flow import http_script
        from src import workspace as effects
        release, entered = threading.Event(), threading.Event()
        class DelayedHTTP:
            def __init__(inner):
                inner.requests, inner.errors, inner.main_id = [], [], None
            def reply(inner, request):
                inner.main_id = session_facts(request)['agent_id']
                if len(inner.requests) == 1:
                    command = shlex.quote(sys.executable) + " -c 'import time; time.sleep(300)'"
                    return call('exec_command', {'command': command, 'yield_time_ms': 0})
                entered.set()
                assert release.wait(15)
                return call('exec_command', {'command': 'touch must-not-execute'})
        script = DelayedHTTP()
        output = self.root / 'cancel-run'
        task = self.root / 'cancel-task.txt'
        task.write_text('Keep a useful shell job alive.')
        job_workspace, job_id = None, None
        with http_script(script) as environment:
            process = subprocess.Popen([sys.executable, '-B', '-m', 'src.teamharness', 'run',
                '--no-planning-boundary',  # Cancellation is independent of the initial screening policy.
                '--workspace', str(self.repo), '--task', str(task), '--output', str(output)],
                cwd=Path(__file__).resolve().parents[2], env={**os.environ, **environment},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(entered.wait(10))
                agent_root = output / 'agents/agents' / script.main_id
                descriptor = json.loads((agent_root / 'agent.json').read_text())
                job_workspace = {'root': descriptor['workspace'], 'state': str(agent_root / 'workstate'),
                                 'base': descriptor['base'], 'capture_patch': True}
                job_id = next((agent_root / 'workstate/jobs').glob('*/request.json')).parent.name
                self.assertEqual(effects.observe(job_workspace, job_id, count=0)['state'], 'running')
                process.send_signal(signal.SIGINT)
                _stdout, stderr = process.communicate(timeout=5)
                self.assertNotEqual(process.returncode, 0, stderr)
                self.assertFalse((self.repo / 'must-not-execute').exists())
                self.assertEqual(effects.observe(job_workspace, job_id, count=0)['state'], 'running')
                with closing(sqlite3.connect(agent_root / 'session.sqlite3')) as db:
                    stops = [json.loads(row[0]) for row in db.execute("SELECT body FROM objects WHERE kind='control' AND json_extract(body,'$.type')='stop'")]
                    requests = db.execute("SELECT COUNT(*) FROM objects WHERE kind='model_request'").fetchone()[0]
                self.assertEqual(stops[-1]['author'], 'human')
                self.assertEqual(requests, 2)
                class NoInference:
                    model = 'fixture'
                    def complete(inner, request):
                        self.fail('Cancellation must survive executor replacement')
                replacement = AgentManager(output / 'agents', lambda _: NoInference())
                replacement.start(script.main_id)
                replacement.shutdown()
                self.assertEqual(replacement.inspect(script.main_id)['status'], 'stopped')
                replacement.return_observation(script.main_id, {'event': 'late_after_cancellation'}, 'cancelled-late')
                replacement.shutdown()
                self.assertEqual(replacement.inspect(script.main_id)['status'], 'stopped')
            finally:
                release.set()
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
                if job_workspace and job_id:
                    effects.dispatch('job', {'workspace': job_workspace, 'id': job_id,
                                            'action': 'terminate', 'yield_time_ms': 1000})

    def test_helper_error_is_evidence_and_wakes_main_without_usefulness_filter(self):
        child = self.child('Probe the independent caller.')
        class Failure:
            model = 'fixture'
            def complete(inner, request):
                raise RuntimeError('Controlled helper provider failure; prior effects remain uncertain.')
        self.manager.provider_factory = lambda path: Failure() if path.name == child else self.script
        self.manager.start(child)
        self.manager.shutdown()
        self.assertEqual(self.manager.inspect(child)['status'], 'error')
        evidence = self.notifications()[0]['observation']
        self.assertEqual(evidence['status'], 'error')
        self.assertIn('Controlled helper provider failure', json.dumps(evidence['return_receipt']))
        self.assertEqual((self.repo / 'observed.txt').read_text(), 'observed\n')

    def test_reconciled_duplicate_returns_never_attempt_another_insert(self):
        from src.store import Store
        child = self.child()
        self.manager.start(child)
        self.manager.shutdown()
        before = len(self.notifications())
        with patch.object(Store, 'put', side_effect=AssertionError('An already delivered return must not write again')):
            self.workflow.reconcile_returns()
            self.manager.shutdown()
        self.assertEqual(len(self.notifications()), before)
        evidence = self.notifications()[0]
        # A conflicting identity remains an integrity error, never a silent skip.
        with self.assertRaisesRegex(ValueError, 'different contents'):
            self.manager.return_observation(self.main, {'changed': True},
                'agent-return-' + child + '-' + evidence['observation']['return_reference']['fingerprint'])

    def test_callable_http_provider_factory_uses_recorded_explicit_model_on_resume(self):
        from test_main_flow import http_script
        from src.teamharness.provider import HTTPProvider
        class HTTPAnswers:
            def __init__(inner):
                inner.requests, inner.errors = [], []
            def reply(inner, request):
                return finished('Observed model ' + request['model'])
        script = HTTPAnswers()
        output = self.root / 'callable-provider'
        with http_script(script) as environment, patch.dict(os.environ, environment):
            manager = AgentManager(output / 'agents', lambda root: HTTPProvider(root))
            self.addCleanup(manager.shutdown)
            workflow = Investigation(manager, output)
            initial = workflow.run('Continue the same configured main.', self.repo)
            manager.shutdown()
            main = initial['agents']['main']
            root = manager._path(main)
            original_provider = HTTPProvider(root)
            replacement = HTTPProvider(root, configuration={**original_provider.config, 'model': 'explicit-second'})
            manager.reconfigure(main, reason='Explicit host model choice.', provider=replacement)
            self.assertEqual(Session.recorded_config(root)['provider_configuration']['model'], 'explicit-second')
            resumed = workflow.resume(message='Continue with that recorded model.')
            manager.shutdown()
            self.assertEqual(resumed['state'], 'completed')
            self.assertEqual(script.requests[-1]['model'], 'explicit-second')
            self.assertEqual(manager.inspect(main)['report'], 'Observed model explicit-second')
            self.assertEqual(script.errors, [])

    def test_managed_resume_loads_explicit_latest_configuration(self):
        before = Session.recorded_config(self.manager._path(self.main))
        selected = [tool for tool in before['tools']
                    if tool['function']['name'] in ('exec_command', 'finish_role')]
        changed = before['system'] + '\nExplicitly revised investigation framing.'
        self.manager.reconfigure(self.main, reason='Human selected future request settings.',
            system=changed, tools=selected, settings={'temperature': 0.25},
            guidance=[{'author': 'human', 'text': 'Updated authored guidance, distinct from runtime observations.'}])
        self.manager.send(self.main, 'Continue using the explicit configuration.')
        self.manager.start(self.main)
        self.manager.shutdown()
        requests = [request for request in self.script.requests
                    if 'Explicitly revised investigation framing.' in request['messages'][0]['content']]
        self.assertTrue(requests)
        self.assertEqual(requests[0]['temperature'], 0.25)
        self.assertEqual({t['function']['name'] for t in requests[0]['tools']}, {'exec_command', 'finish_role'})
        self.assertEqual(self.manager._session(self.main).session_id, self.session_id)
        self.assertEqual((self.repo / 'observed.txt').read_text(), 'observed\n')

    def test_child_inherits_latest_human_task_and_attributed_brief(self):
        self.manager.send(self.main, 'New literal human correction.')
        self.manager._session(self.main).set_task_brief('Current parent interpretation.', author=self.main)
        child = self.child('Check this selected obligation.')
        descriptor = self.manager.descriptor(child)
        inherited = descriptor['inherited_task_context']
        self.assertIn('New literal human correction.', json.dumps(inherited['human_inputs']))
        self.assertEqual(inherited['working_brief']['author'], self.main)
        self.assertEqual(descriptor['task'], 'Check this selected obligation.')
        self.assertIn('ORIGINAL PUBLIC TASK', descriptor['session_task'])
        self.assertIn('CURRENT TASK CONTEXT', descriptor['session_task'])
        child_context = self.manager._session(child).task_context()
        self.assertIn('New literal human correction.', json.dumps(child_context['human_inputs']))
        self.assertTrue(any('source' in item for item in child_context['human_inputs']))
        self.assertEqual(child_context['working_brief']['author'], self.main)
        self.assertEqual(child_context['working_brief']['text'], 'Current parent interpretation.')
        self.assertEqual(child_context['working_brief']['source']['session_id'], self.session_id)
        class CompactChild:
            model = 'fixture'
            def complete(inner, request):
                if 'response_format' in request:
                    return answer(json.dumps({'continuation': 'Child continuation written by the model.'}))
                return answer('Child finished after compaction.')
        child_session = self.manager._session(child, CompactChild())
        child_session.request_compaction()
        self.assertEqual(child_session.run()['status'], 'completed')
        after = self.manager._session(child).task_context()
        self.assertEqual(after['working_brief']['author'], self.main)
        grandchild = self.manager.create('investigator', 'Nested concrete caller.', self.repo, parent=child)
        nested_context = self.manager._session(grandchild).task_context()
        self.assertEqual(nested_context['working_brief']['author'], self.main)
        self.assertEqual(nested_context['working_brief']['source']['session_id'], child_session.session_id)
        self.assertEqual(nested_context['working_brief']['source']['source']['session_id'], self.session_id)


if __name__ == '__main__':
    unittest.main()
