"""Focused witnesses for literal views, working interpretations and exchange recovery."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from src.store import Store
from src.teamharness.history import search_history
from src.teamharness.presentation import render_result
from src.teamharness.session import Session
from src.teamharness.tools import ToolRuntime, TOOLS
from test_session import Script, answer, tools


class InformationViews(unittest.TestCase):
    def test_working_brief_updates_reach_requests_and_compaction_without_replacing_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = Script([
                tools(('configure_session', {'reason': 'Initial hypothesis', 'working_brief': 'Cache may be stale.'})),
                tools(('configure_session', {'reason': 'Contrary evidence', 'working_brief': 'Cache disabled still reproduces. Caller correction not yet applied; inspect caller.py.'})),
                tools(('compact', {})),
                answer(json.dumps({'continuation': 'Continue inspecting caller.py; correction remains unapplied.'})),
                answer('done')])
            session = Session(root / 'session', 'investigator', 'System', 'Literal human requirement',
                              provider, [], lambda *args: self.fail('No external effects expected'), root,
                              public_task='Literal human requirement')
            self.assertEqual(session.run()['report'], 'done')
            wires = [json.dumps(request['messages']) for request in provider.requests]
            self.assertIn('Cache may be stale.', wires[1])
            self.assertIn('Cache disabled still reproduces.', wires[2])
            self.assertIn('supersedes the previous working brief', wires[2])
            self.assertIn('Cache disabled still reproduces.', wires[-1])
            self.assertIn('Literal human requirement', wires[-1])
            context = session.task_context()
            self.assertEqual(context['working_brief']['author'], 'model:' + session.session_id)
            self.assertIn('not yet applied', context['working_brief']['text'])
            with Store(session.database, readonly=True) as store:
                self.assertIn('Cache may be stale.', json.dumps([store.get(item['ref']) for item in store.query(kind='control')['objects']]))

    def runtime(self, root):
        agent = root / 'agents' / 'a'
        agent.mkdir(parents=True)
        (agent / 'agent.json').write_text('{}')
        manager = SimpleNamespace(root=root, workflow=None)
        return ToolRuntime(manager, 'a', {'root': str(agent), 'workspace': str(root), 'tools': TOOLS})

    def test_real_command_preview_preserves_literal_ranges_and_poll_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.runtime(root)
            (root / 'emit.py').write_text("print('BEGIN')\nprint('雪' * 9000)\nprint('FINAL SENTINEL')\n")
            import sys
            import shlex
            result = runtime('exec_command', {'command': shlex.quote(sys.executable) + ' emit.py', 'max_output_bytes': 65536}, 'launch')
            self.assertEqual(result['state'], 'exited')
            self.assertTrue(result['output']['complete'])
            raw = Path(result['output_path']).read_bytes()
            preview = result['output_preview']
            for part in preview['ranges']:
                self.assertEqual(raw[part['offset']:part['next_offset']].decode(), part['content'])
            self.assertGreater(preview['omitted_range'][1], preview['omitted_range'][0])
            rendered = render_result(runtime.root, runtime.root / 'session.sqlite3', 'obj:1', 'exec_command', result)
            self.assertIn('BEGIN', rendered)
            self.assertIn('FINAL SENTINEL', rendered)
            self.assertLess(rendered.index('BEGIN'), rendered.index('Evidence references:'))
            self.assertIn('[... bytes %d-%d omitted ...]' % tuple(preview['omitted_range']), rendered)
            # The default cursor continues after the displayed end; the omitted
            # middle remains addressable only by an explicit offset.
            later = runtime('job', {'id': result['id'], 'yield_time_ms': 0}, 'poll')
            self.assertEqual(later['output']['offset'], preview['total_bytes'])
            self.assertNotIn('output_preview', later)
            explicit = runtime('job', {'id': result['id'], 'offset': 0, 'yield_time_ms': 0}, 'explicit')
            self.assertEqual(explicit['output']['offset'], 0)
            self.assertNotIn('output_preview', explicit)
            self.assertEqual(Path(result['output_path']).read_bytes(), raw)

    def test_history_match_recovers_original_complete_exchange_and_rejects_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.runtime(root)
            messages = [{'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'a', 'function': {'name': 'exec_command', 'arguments': '{"command":"echo needle"}'}},
                {'id': 'b', 'function': {'name': 'read_file', 'arguments': '{"path":"file"}'}}]},
                {'role': 'tool', 'tool_call_id': 'a', 'content': 'needle original output'},
                {'role': 'tool', 'tool_call_id': 'b', 'content': 'original source'}]
            database = runtime.root / 'session.sqlite3'
            with Store(database) as store:
                ref = store.put('session_state', 'session', {'epoch': 0, 'messages': messages})
                broken = store.put('session_state', 'session', {'epoch': 1, 'messages': messages[:2]})
            before = database.read_bytes()
            matches = search_history(root, 'needle')['matches']
            match = next(item for item in matches if item['ref'] == ref)
            read = runtime('read_archive', match['read_exchange'], 'recover')
            self.assertIn('epoch 0, messages 0-3', read['content'])
            for text in ('Call a: exec_command\n  command: echo needle', 'Call b: read_file\n  path: file',
                         'Tool result for a:\nneedle original output', 'Tool result for b:\noriginal source'):
                self.assertIn(text, read['content'])
            exact = runtime('read_archive', {**match['read_archive'], 'json_pointer': '/body/messages'}, 'exact')
            self.assertEqual(json.loads(exact['content']), messages)
            self.assertFalse(any(item.get('read_exchange') for item in matches if item['ref'] == broken))
            with self.assertRaisesRegex(ValueError, 'complete tool'):
                runtime.execute('read_archive', {'path': str(database), 'ref': broken,
                    'json_pointer': '/body/messages/1', 'include_exchange': True}, 'bad')
            self.assertEqual(database.read_bytes(), before)
