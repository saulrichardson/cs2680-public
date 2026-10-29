from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from src.store import Store
from src.teamharness.agents import AgentManager
from src.teamharness.history import search_history
from src.teamharness.presentation import clock
from src.teamharness.tools import ToolRuntime


def answer(text):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': text}}]}


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        (self.repo / 'main.txt').write_text('before\n')
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=T', '-c', 'user.email=t@local', 'commit', '-qm', 'base'], check=True)
        self.manager = AgentManager(self.root / 'run/agents', lambda _: lambda q: answer('done'), deadline=time.time() + 3600)

    def test_deadline_is_identical_for_child_and_resume_and_visible_on_wire(self):
        parent = self.manager.create('planner', 'plan', self.repo)
        child = self.manager.create('investigator', 'look', self.repo, parent=parent)
        for ident in (parent, child):
            d = self.manager.descriptor(ident)
            self.assertEqual(d['task_deadline_unix'], self.manager.deadline)
            self.assertIn('\nSession facts (runtime):\n', d['system'])
            self.assertIn('\ntask deadline: ' + clock(self.manager.deadline), d['system'])
        resumed = AgentManager(self.manager.root, lambda _: None)
        self.assertEqual(resumed.deadline, self.manager.deadline)
        with self.assertRaisesRegex(ValueError, 'cannot be renewed'):
            AgentManager(self.manager.root, lambda _: None, deadline=self.manager.deadline + 1)
        status = resumed.deadline_status()
        self.assertLessEqual(status['remaining_seconds'], 3600)
        self.assertGreater(status['remaining_seconds'], 3500)

    def test_any_all_wait_and_timeout_never_stop_agents(self):
        slow = threading.Event()
        self.addCleanup(slow.set)
        def factory(path):
            task = json.loads((path / 'agent.json').read_text())['task']
            def respond(q):
                if task == 'slow':
                    slow.wait(10)
                return answer(task)
            return respond
        self.manager.provider_factory = factory
        a = self.manager.create('investigator', 'fast', self.repo)
        b = self.manager.create('investigator', 'slow', self.repo)
        self.manager.start(a); self.manager.start(b)
        try:
            any_result = self.manager.wait_agents([a, b], 'any', 5)
            self.assertTrue(any_result['wait_satisfied'])
            self.assertIn(a, any_result['ready_agent_ids'])
            all_result = self.manager.wait_agents([a, b], 'all', .05)
            self.assertTrue(all_result['timed_out'])
            self.assertEqual(self.manager.inspect(b)['status'], 'running')
            self.assertFalse(all_result['agents_cancelled'])
            slow.set()
            self.assertTrue(self.manager.wait_agents([a, b], 'all', 5)['wait_satisfied'])
            roster = self.manager.list_agents()['agents']
            self.assertTrue(all(x['report_available'] and x['patch'] for x in roster))
        finally:
            slow.set(); self.manager.shutdown()

    def test_checkpoint_is_immutable_and_does_not_finish_or_assert_quality(self):
        ident = self.manager.create('implementer', 'edit', self.repo)
        runtime = ToolRuntime(self.manager, ident, self.manager.descriptor(ident))
        (self.repo / 'main.txt').write_text('first candidate\n')
        first = runtime('checkpoint', {'label': 'before review'}, 'checkpoint-first')
        self.assertTrue(first['captured'])
        raw = Path(first['candidate']['patch_path']).read_bytes()
        (self.repo / 'main.txt').write_text('second candidate\n')
        second = runtime('checkpoint', {}, 'checkpoint-second')
        self.assertNotEqual(first['candidate']['sha256'], second['candidate']['sha256'])
        self.assertEqual(Path(first['candidate']['patch_path']).read_bytes(), raw)
        self.assertEqual(runtime('checkpoint', {'label': 'before review'}, 'checkpoint-first'), first)
        self.assertEqual(self.manager.inspect(ident)['status'], 'idle')
        self.assertIsNone(first['tests_passed']); self.assertIsNone(first['review_accepted'])
        status = runtime('task_status', {}, 'observe-status')
        self.assertEqual(status['latest_candidate']['checkpoint_id'], second['checkpoint_id'])
        self.assertEqual(status['deadline']['deadline_unix'], self.manager.deadline)
        self.assertNotIn('repair_ready', status)

    def test_history_finds_compacted_messages_calls_results_and_handoff(self):
        ident = self.manager.create('investigator', 'investigate', self.repo)
        db = Path(self.manager.descriptor(ident)['root']) / 'session.sqlite3'
        messages = [{'role': 'user', 'content': 'original sentinel requirement'},
                    {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'c', 'function': {'name': 'exec_command', 'arguments': '{"command":"sentinel shell"}'}}]},
                    {'role': 'tool', 'tool_call_id': 'c', 'content': 'sentinel observed result'}]
        store = Store(db)
        try:
            store.put('session_state', 'session', {'epoch': 0, 'messages': messages[:1]})
            old = store.put('session_state', 'session', {'epoch': 0, 'messages': messages})
            store.put('session_state', 'session', {'epoch': 1, 'messages': [{'role': 'assistant', 'content': 'model sentinel handoff'}]})
            store.put('model_response', 'session', {'response': {'malformed_tool': 'sentinel malformed original'}})
        finally:
            store.db.close()
        before = db.read_bytes()
        found = search_history(self.manager.root, 'sentinel', limit=2)
        self.assertEqual(len(found['matches']), 2)
        self.assertTrue(found['has_more'])
        self.assertEqual(found['matches'][0]['ref'], old)
        next_page = search_history(self.manager.root, 'sentinel', offset=found['next_offset'])
        self.assertTrue(any('handoff' in x['excerpt'] for x in next_page['matches']))
        self.assertTrue(any('observed result' in x['excerpt'] for x in next_page['matches']))
        self.assertTrue(any('malformed original' in x['excerpt'] for x in next_page['matches']))
        runtime = ToolRuntime(self.manager, ident, self.manager.descriptor(ident))
        result = runtime('read_archive', found['matches'][0]['read_archive'], 'read-original')
        self.assertIn('original sentinel requirement', result['content'])
        self.assertEqual(db.read_bytes(), before)

    def test_wait_rejects_unknown_or_duplicate_ids_before_waiting(self):
        ident = self.manager.create('investigator', 'task', self.repo)
        for values in ([ident, ident], [], ['f' * 32]):
            with self.assertRaises(ValueError):
                self.manager.wait_agents(values, 'all', 0)
        self.assertTrue(self.manager.wait_agents([ident], 'any', 0)['timed_out'])

    def test_read_archive_array_pointer_requires_canonical_ascii_index(self):
        ident = self.manager.create('investigator', 'inspect archive', self.repo)
        descriptor = self.manager.descriptor(ident)
        store = Store(Path(descriptor['root']) / 'session.sqlite3')
        try:
            ref = store.put('fixture', 'session', {'items': ['zero', 'one']})
        finally:
            store.db.close()
        runtime = ToolRuntime(self.manager, ident, descriptor)
        args = {'path': 'session.sqlite3', 'ref': ref, 'json_pointer': '/body/items/1'}
        self.assertEqual(json.loads(runtime('read_archive', args, 'canonical-index')['content']), 'one')
        for index in ('١', '１', '01', '-1', '+1', '1.0'):
            result = runtime('read_archive', dict(args, json_pointer='/body/items/' + index), 'invalid-index-' + index)
            self.assertIn('JSON pointer array index must be canonical', result['error'])
            self.assertNotIn('content', result)

    def test_cli_observation_rejects_unrecorded_directory_without_creating_campaign(self):
        empty = self.root / 'unrecorded'
        empty.mkdir()
        (empty / 'unrelated.txt').write_text('preserve me\n')
        before = {str(path.relative_to(empty)): path.read_bytes() for path in empty.rglob('*') if path.is_file()}
        for command in ('status', 'list', 'inspect'):
            argv = [sys.executable, '-m', 'src.teamharness', command, '--output', str(empty)]
            if command == 'inspect':
                argv += ['--agent', 'f' * 32]
            result = subprocess.run(argv, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('Existing recorded team required', result.stderr)
            self.assertFalse((empty / 'agents').exists())
            self.assertEqual({str(path.relative_to(empty)): path.read_bytes() for path in empty.rglob('*') if path.is_file()}, before)
        ident = self.manager.create('investigator', 'not started', self.repo)
        result = subprocess.run([sys.executable, '-m', 'src.teamharness', 'status', '--output', str(self.root / 'run')],
                                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['agents'][0]['agent_id'], ident)
        self.assertFalse((Path(self.manager.descriptor(ident)['root']) / 'provider-config.json').exists())


if __name__ == '__main__':
    unittest.main()
