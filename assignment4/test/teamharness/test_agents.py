import json
import os
import sqlite3
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src.teamharness.agents import AgentManager
from src.teamharness.tools import ToolRuntime


def answer(text):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': text}}]}


class Agents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        (self.repo / 'file').write_text('original')
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test', '-c', 'user.email=test@local', 'commit', '-qm', 'base'], check=True)

    def test_provider_factory_sqlite_error_retains_extended_diagnostics(self):
        calls = []
        def factory(_root):
            calls.append('constructed')
            db = sqlite3.connect(':memory:')
            try:
                db.execute('SELECT * FROM missing_factory_table')
            finally:
                db.close()
        manager = AgentManager(self.root / 'diagnostic-run', factory)
        self.addCleanup(manager.shutdown)
        ident = manager.create('investigator', 'Observe a provider construction failure.', self.repo)
        manager.start(ident)
        returned = manager.wait(ident, 5)
        manager.shutdown()
        self.assertEqual(returned['status'], 'error', returned)
        self.assertEqual(returned['sqlite_errorcode'], sqlite3.SQLITE_ERROR)
        self.assertEqual(returned['sqlite_errorname'], 'SQLITE_ERROR')
        self.assertEqual(returned['error_type'], 'OperationalError')
        self.assertIn('missing_factory_table', returned['error'])
        self.assertIn('SELECT * FROM missing_factory_table', returned['error_traceback'])
        self.assertEqual(calls, ['constructed'])

    def test_completion_publication_during_inspect_is_not_interruption(self):
        import fcntl
        from src import workspace
        manager = AgentManager(self.root/'run', lambda _: None)
        ident = manager.create('integrator', 'task', self.repo, private=True)
        path = manager._path(ident)
        workspace.dump(path/'runner.json', {'status': 'running'})
        lease = (path/'runner.lock').open('a')
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        read, published = threading.Event(), threading.Event()
        def complete():
            if read.wait(5):
                workspace.dump(path/'runner.json', {'status': 'completed', 'outcome': 'complete'})
                lease.close()
                published.set()
        worker = threading.Thread(target=complete)
        worker.start()
        original_read = Path.read_text
        first = True
        def stale_read(p, *args, **kwargs):
            nonlocal first
            value = original_read(p, *args, **kwargs)
            if p == path/'runner.json' and first:
                first = False
                read.set()
                self.assertTrue(published.wait(5))
            return value
        try:
            with patch.object(Path, 'read_text', stale_read):
                observation = manager.inspect(ident)
            self.assertEqual(observation['status'], 'running')  # safe stale observation
            self.assertEqual(manager.wait(ident, 1)['status'], 'completed')
        finally:
            read.set()
            worker.join(5)
            lease.close()

    def test_absent_executor_is_still_interrupted_without_replay(self):
        from src import workspace
        manager = AgentManager(self.root/'run', lambda _: self.fail('must not launch'))
        ident = manager.create('integrator', 'task', self.repo)
        workspace.dump(manager._path(ident)/'runner.json', {'status': 'running'})
        self.assertEqual(manager.wait(ident, 1)['status'], 'interrupted')

    def test_leases_capacity_cross_handle_stop_and_frozen_start_identity(self):
        entered, release = threading.Event(), threading.Event()
        class Provider:
            model = 'fixture'
            def complete(self, q):
                entered.set()
                release.wait(5)
                return answer('done')
        manager = AgentManager(self.root/'run', lambda _: Provider(), max_agents=1)
        first = manager.create('investigator', 'first', self.repo, private=True)
        second = manager.create('investigator', 'second', self.repo, private=True)
        try:
            manager.start(first)
            self.assertTrue(entered.wait(3))
            self.assertEqual(manager.start(first)['start'], 'already_running')
            self.assertEqual(manager.start(second)['start'], 'capacity_unavailable')
            other = AgentManager(self.root/'run', lambda _: Provider())
            self.assertEqual(other.max_agents, 1)
            other.stop(first)
            release.set()
            self.assertEqual(manager.wait(first, 5)['status'], 'stopped')
            self.assertEqual(manager.start(second)['start'], 'started')
            self.assertEqual(manager.wait(second, 5)['status'], 'completed')
            with self.assertRaises(ValueError):
                manager.create('investigator', 'first', self.repo, private=False, identity=first)
        finally:
            release.set()
            manager.shutdown()

    def test_send_during_capture_is_not_stranded(self):
        entered, release = threading.Event(), threading.Event()
        class Provider:
            model = 'fixture'
            def __init__(self): self.calls = 0
            def complete(self, q):
                self.calls += 1
                return answer('first done' if self.calls == 1 else 'follow-up done')
        provider = Provider()
        manager = AgentManager(self.root/'run', lambda _: provider)
        ident = manager.create('investigator', 'task', self.repo)
        from src import workspace
        real_capture = workspace.capture
        def delayed(*args, **kwargs):
            if 'captures' in str(args[1]) and not entered.is_set():
                entered.set()
                release.wait(5)
            return real_capture(*args, **kwargs)
        try:
            with patch('src.teamharness.agents.effects.capture', delayed):
                manager.start(ident)
                self.assertTrue(entered.wait(3))
                manager.send(ident, 'new work')
                self.assertEqual(manager.start(ident)['start'], 'already_running')
                release.set()
                result = manager.wait(ident, 5)
                self.assertEqual(result['report'], 'follow-up done')
                self.assertEqual(provider.calls, 2)
        finally:
            release.set()
            manager.shutdown()

    def test_checkpoint_precedes_worker_and_immediate_followup(self):
        entered, release = threading.Event(), threading.Event()
        observed = {}
        class Provider:
            model = 'fixture'
            def complete(self, q):
                observed['messages'] = q['messages']
                return answer('follow-up handled')
        def factory(root):
            observed['checkpoint_exists'] = (root / 'session.json').exists()
            entered.set()
            release.wait(5)
            return Provider()
        manager = AgentManager(self.root/'run', factory)
        ident = manager.create('investigator', 'task', self.repo)
        try:
            manager.start(ident)
            self.assertTrue(entered.wait(3))
            manager.send(ident, 'immediate follow-up')
            release.set()
            self.assertEqual(manager.wait(ident, 5)['status'], 'completed')
            self.assertTrue(observed['checkpoint_exists'])
            self.assertIn({'role': 'user', 'content': 'immediate follow-up'}, observed['messages'])
        finally:
            release.set()
            manager.shutdown()

    def test_send_and_resume_preserve_recorded_control_schemas_after_maintenance(self):
        from src.teamharness import session
        requests = []
        class Provider:
            model = 'fixture'
            def complete(self, request):
                requests.append(json.dumps(request))
                return answer('original turn' if len(requests) == 1 else 'follow-up handled')
        manager = AgentManager(self.root / 'frozen-controls', lambda _: Provider())
        ident = manager.create('investigator', 'inspect source', self.repo)
        original_tools = manager.descriptor(ident)['tools']
        try:
            manager.start(ident)
            self.assertEqual(manager.wait(ident, 5)['status'], 'completed')
            changed = json.loads(json.dumps(session.CONTROL_TOOLS))
            for tool in changed:
                tool['function']['description'] += ' Changed in maintained code after the first turn.'
            with patch.object(session, 'CONTROL_TOOLS', changed):
                manager.send(ident, 'Continue from the recorded session')
                manager.start(ident)
                self.assertEqual(manager.wait(ident, 5)['report'], 'follow-up handled')
            self.assertEqual(len(requests), 2)
            for serialized in requests:
                self.assertEqual(json.loads(serialized)['tools'], original_tools)
            self.assertIn({'role': 'user', 'content': 'Continue from the recorded session'}, json.loads(requests[1])['messages'])
        finally:
            manager.shutdown()

    def test_effect_identity_and_original_archive_read(self):
        class Provider:
            model = 'fixture'
            def complete(self, q): return answer('recorded original response')
        manager = AgentManager(self.root/'run', lambda _: Provider())
        ident = manager.create('investigator', 'task', self.repo)
        manager.start(ident)
        manager.wait(ident, 5)
        runtime = ToolRuntime(manager, ident, manager.descriptor(ident))
        a = {'patch': '*** Begin Patch\n*** Add File: new.txt\n+first\n\\ No newline at end of file\n*** End Patch\n'}
        result = runtime('apply_patch', a, 'effect-one')
        self.assertTrue(result['applied'])
        self.assertEqual(runtime('apply_patch', a, 'effect-one'), result)
        self.assertFalse(runtime('apply_patch', {'patch': a['patch'].replace('+first', '+changed')}, 'effect-one')['executed'])
        self.assertEqual((self.repo/'new.txt').read_text(), 'first')
        records = runtime('read_archive', {'path': 'session.sqlite3', 'kind': 'model_response'}, 'read-one')
        ref = json.loads(records['content'])['objects'][0]['ref']
        original = runtime('read_archive', {'path': 'session.sqlite3', 'ref': ref}, 'read-two')
        self.assertIn('recorded original response', original['content'])
        manager.shutdown()

    def test_stop_and_external_deadline_interrupt_capture_wait_without_stopping_effect(self):
        from src.teamharness import patching
        original_patch = patching.apply_patch
        for cause in ('operator_stop', 'task_deadline'):
            with self.subTest(cause=cause):
                entered, release = threading.Event(), threading.Event()
                name = 'late-' + cause
                def delayed(*args, **kwargs):
                    entered.set()
                    release.wait(5)
                    return original_patch(*args, **kwargs)
                class Provider:
                    model = 'fixture'
                    calls = 0
                    def complete(self, request):
                        self.calls += 1
                        if self.calls == 1:
                            tool, args = 'parallel', {'yield_time_ms': 0, 'calls': [{'name': 'apply_patch',
                                'arguments': {'patch': '*** Begin Patch\n*** Add File: ' + name + '\n+late effect\n*** End Patch\n'}}]}
                        else:
                            tool, args = 'finish_role', {'outcome': 'complete', 'report': 'Model returned its role.'}
                        return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                            'tool_calls': [{'id': tool, 'type': 'function', 'function': {'name': tool, 'arguments': json.dumps(args)}}]}}]}
                provider = Provider()
                deadline = time.time() + 1 if cause == 'task_deadline' else None
                manager = AgentManager(self.root / cause, lambda _: provider, deadline=deadline)
                ident = manager.create('investigator', 'Fixture delayed write', self.repo)
                groups = manager.parallel_groups(ident)
                try:
                    with patch('src.teamharness.patching.apply_patch', delayed):
                        manager.start(ident)
                        self.assertTrue(entered.wait(2))
                        state_path = Path(manager.descriptor(ident)['root']) / 'session.json'
                        limit = time.monotonic() + 2
                        while json.loads(state_path.read_text())['status'] != 'completed':
                            self.assertLess(time.monotonic(), limit)
                            time.sleep(.01)
                        self.assertEqual(manager.inspect(ident)['status'], 'running')
                        if cause == 'operator_stop':
                            manager.stop(ident)
                        result = manager.wait(ident, 2)
                        self.assertEqual(result['status'], 'stopped')
                        self.assertIsNone(result['outcome'])
                        self.assertIsNone(result['patch'])
                        self.assertEqual(result['source_capture']['reason'], cause)
                        self.assertTrue(result['source_capture']['callbacks_active'])
                        self.assertFalse(result['source_capture']['effects_terminated'])
                        self.assertEqual(result['session_return']['outcome'], 'complete')
                        self.assertEqual(json.loads(state_path.read_text())['status'], 'completed')
                        self.assertEqual(provider.calls, 2)
                        self.assertFalse((self.repo / name).exists())
                        if cause == 'task_deadline':
                            shutdown = manager.shutdown()
                            self.assertFalse(shutdown['wait_completed'])
                            self.assertTrue(shutdown['callbacks_active'])
                            self.assertFalse(shutdown['effects_terminated'])
                            self.assertTrue(Path(shutdown['receipt']).is_file())
                        release.set()
                        groups.shutdown()
                        self.assertEqual((self.repo / name).read_text(), 'late effect\n')
                        self.assertIsNone(manager.inspect(ident)['patch'])
                finally:
                    release.set()
                    groups.shutdown()
                    manager.shutdown()


if __name__ == '__main__':
    unittest.main()
