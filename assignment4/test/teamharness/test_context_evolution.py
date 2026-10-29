"""Explicit context evolution using wire requests, durable provenance and real effects."""
import copy
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from src.teamharness.tools import ToolRuntime, TOOLS

from src.teamharness.provider import HTTPProvider
from src.teamharness.session import Session, SessionBusy, CONTROL_TOOLS


def answer(text, **native):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': text, **native}}]}


class Evolution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.requests, self.replies, self.effects = [], [], []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append({'path': self.path, 'request': request})
                response = owner.replies.pop(0)
                if callable(response):
                    response = response(request)
                body = json.dumps(response).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = patch.dict('os.environ', {'A4_MODEL': 'first', 'A4_BASE_URL': self.endpoint,
            'A4_API_PROTOCOL': 'chat_completions', 'A4_API_KEY': 'not-archived'})
        self.env.start()

    @property
    def endpoint(self):
        return f'http://127.0.0.1:{self.server.server_port}/v1'

    def tearDown(self):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def execute(self, name, args, ident):
        self.effects.append(ident)
        result = subprocess.run(['/bin/sh', '-c', args['command']], cwd=self.work,
                                capture_output=True, text=True)
        return {'exit_code': result.returncode, 'stdout': result.stdout}

    def session(self):
        return Session(self.root / 'agent', 'investigator', 'initial instructions', 'literal public task',
            HTTPProvider(self.root / 'agent'), [], self.execute, self.work,
            public_task='literal public task', metadata={'task_deadline_unix': None, 'max_agents': 2})

    def records(self, kind):
        with closing(sqlite3.connect(self.root / 'agent/session.sqlite3')) as db:
            return [(f'obj:{i}', json.loads(body)) for i, body in db.execute(
                'SELECT id,body FROM objects WHERE kind=? ORDER BY id', (kind,))]

    def test_explicit_same_identity_changes_actual_wire_without_effect_replay(self):
        self.replies.append(answer('first report'))
        session = self.session()
        identity = session.run()['session_id']
        before = self.records('model_request')
        old_state = copy.deepcopy(session.state)
        provider = HTTPProvider(session.root, configuration={'protocol': 'chat_completions',
                                   'endpoint': self.endpoint + '/new', 'model': 'second'})
        shell = {'type': 'function', 'function': {'name': 'exec_command', 'parameters': {'type': 'object'}}}
        transition = session.reconfigure(reason='Human explicitly changes available setup', system='revised instructions',
            tools=[shell], settings={'temperature': 0.2}, guidance=[{'author': 'guide writer', 'text': 'Try the caller.'}], provider=provider)
        self.assertEqual(identity, transition['session_id'])
        self.assertEqual(2, transition['config_version'])
        self.assertEqual(old_state['status'], session.state['status'])
        session.append_user('Please continue.', identity='human-next')
        self.replies.extend([{'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'write-once', 'type': 'function', 'function': {'name': 'exec_command',
                'arguments': json.dumps({'command': 'printf real > result'})}}]}}]}, answer('second report')])
        self.assertEqual(identity, session.run()['session_id'])
        self.assertEqual('real', (self.work / 'result').read_text())
        self.assertEqual(1, len(self.effects))
        wire = self.requests[1]
        self.assertEqual('/v1/new/chat/completions', wire['path'])
        request = wire['request']
        self.assertEqual('second', request['model'])
        self.assertEqual([shell], request['tools'])
        self.assertEqual(0.2, request['temperature'])
        self.assertEqual('revised instructions', request['messages'][0]['content'])
        self.assertEqual('literal public task', request['messages'][1]['content'])
        self.assertTrue(any('guide writer' in (m.get('content') or '') for m in request['messages'][2:]))
        self.assertEqual(before, self.records('model_request')[:len(before)])
        later = self.records('model_request')[-1][1]
        self.assertEqual(transition['config_ref'], later['config_ref'])
        self.assertEqual(2, later['config_version'])
        config = Session.recorded_config(session.root)
        resumed = Session(root=session.root, provider=HTTPProvider(session.root, configuration=config['provider_configuration']),
                          executor=self.execute, **config)
        self.assertEqual(identity, resumed.session_id)
        self.assertEqual([shell], resumed.state['config']['tools'])
        self.assertNotIn('not-archived', json.dumps(self.records('session_configuration')))
        self.assertEqual(2, resumed.state['config']['metadata']['max_agents'])

    def test_native_continuation_preserved_and_cross_provider_change_specific_error(self):
        self.replies.append(answer('report', reasoning_content='native reasoning', reasoning_details=[{'type': 'reasoning.encrypted', 'data': 'continuation'}]))
        session = self.session()
        session.run()
        original = copy.deepcopy(session.state['messages'])
        session.reconfigure(reason='instructions only', system='new instructions')
        self.assertEqual(original[1:], session.state['messages'][1:])
        provider = HTTPProvider(session.root, configuration={'protocol': 'chat_completions',
                                  'endpoint': self.endpoint, 'model': 'second'})
        before = self.records('session_configuration')
        with self.assertRaisesRegex(ValueError, 'native assistant continuation'):
            session.reconfigure(reason='switch model', provider=provider)
        self.assertEqual(before, self.records('session_configuration'))
        self.replies.append(answer('{"continuation":"Model interpretation of earlier work."}'))
        self.assertEqual('compacted', session.compact()['status'])
        session.reconfigure(reason='switch after explicit compaction', provider=provider)
        self.assertEqual('second', session.provider.model)
        with self.assertRaisesRegex(ValueError, 'Unsupported provider transition'):
            HTTPProvider(session.root, configuration={'protocol': 'openai_responses', 'endpoint': self.endpoint, 'model': 'second'})

    def test_equivalent_endpoint_format_preserves_encrypted_continuation(self):
        session = self.session()
        encrypted = [{'type': 'reasoning.encrypted', 'data': 'exact opaque bytes'}]
        self.replies.append(answer('report', reasoning_details=encrypted))
        session.run()
        original = copy.deepcopy(session.state['messages'][-1])
        equivalent = HTTPProvider(session.root, configuration={**session.provider.config, 'endpoint': self.endpoint + '/'})
        session.reconfigure(reason='Equivalent endpoint spelling', provider=equivalent)
        self.assertEqual(self.endpoint, session.provider.config['endpoint'])
        self.assertEqual(original, session.state['messages'][-1])
        changed = HTTPProvider(session.root, configuration={**session.provider.config, 'endpoint': self.endpoint + '/different'})
        with self.assertRaisesRegex(ValueError, 'native assistant continuation'):
            session.reconfigure(reason='Actually different endpoint', provider=changed)
        self.assertEqual(original, session.state['messages'][-1])
        self.assertEqual(0, session.state['epoch'])

    def test_plain_reasoning_extensions_survive_model_change_without_compaction(self):
        session = self.session()
        native = {'reasoning_content': 'plain reasoning', 'reasoning_details': [{'type': 'reasoning.text', 'text': 'more reasoning'}], 'refusal': None}
        self.replies.append(answer('report', **native))
        session.run()
        original = copy.deepcopy(session.state['messages'][-1])
        provider = HTTPProvider(session.root, configuration={'protocol': 'chat_completions', 'endpoint': self.endpoint, 'model': 'second'})
        session.reconfigure(reason='Choose another available model', provider=provider)
        session.append_user('Continue.')
        self.replies.append(answer('continued'))
        session.run()
        self.assertEqual(0, session.state['epoch'])
        self.assertEqual('second', self.requests[-1]['request']['model'])
        self.assertIn(original, self.requests[-1]['request']['messages'])

    def test_authorship_compaction_and_evolving_task_context(self):
        session = self.session()
        session.append_user('literal later human constraint', identity='human')
        session.append_message('Parent suggests a test.', sender='parent-id', relationship='parent', identity='parent')
        session.append_message('Child reports a passing check.', sender='child-id', relationship='child', identity='child')
        session.append_observation({'state': 'running', 'job': 'job-id'}, identity='runtime')
        session.set_task_brief('Investigate the latest caller constraint.', author='main-model', identity='brief')
        self.replies.append(answer('report'))
        session.run()
        messages = self.requests[0]['request']['messages']
        self.assertIn('parent-id', json.dumps(messages))
        self.assertIn('not human input', json.dumps(messages))
        self.assertIn('not new user instructions', json.dumps(messages))
        task = session.task_context()
        self.assertEqual(['literal public task', 'literal later human constraint'], [x['text'] for x in task['human_inputs']])
        self.assertEqual('main-model', task['working_brief']['author'])
        self.replies.append(answer('{"continuation":"A model-written summary."}'))
        session.compact()
        human = [m['content'] for m in session.state['messages'] if m['role'] == 'user']
        self.assertIn('literal public task', human)
        self.assertIn('literal later human constraint', human)
        self.assertNotIn('Parent suggests a test.', human)
        controls = [body for _, body in self.records('control')]
        self.assertEqual('agent_message', controls[1]['type'])
        self.assertEqual('child', controls[2]['relationship'])
        self.assertEqual('observation', controls[3]['type'])

    def test_inherited_human_followups_survive_child_compaction_with_original_sources(self):
        parent = self.session()
        parent.append_user('Later human constraint.', identity='later')
        inputs = parent.task_context()['human_inputs']
        child_root = self.root / 'child'
        child = Session(child_root, 'implementer', 'child instructions',
            'ORIGINAL PUBLIC TASK literal public task; HUMAN FOLLOWUP Later human constraint.; PARENT ASSIGNMENT inspect caller',
            HTTPProvider(child_root), [], self.execute, self.work, public_task='literal public task',
            inherited_inputs=inputs)
        self.assertEqual(2, len(child.state['input_refs']))
        self.replies.append(answer('{"continuation":"Parent assignment interpreted by child."}'))
        child.compact()
        messages = [m['content'] for m in child.state['messages'] if m['role'] == 'user']
        self.assertEqual(1, messages.count('literal public task'))
        self.assertEqual(1, messages.count('Later human constraint.'))
        inherited = child.task_context()['human_inputs']
        self.assertEqual(inputs[1]['ref'], inherited[1]['source']['ref'])
        self.assertEqual(parent.session_id, inherited[0]['source']['session_id'])
        self.assertEqual(str(parent.database), inherited[1]['source']['archive'])
        config = Session.recorded_config(child_root)
        reopened = Session(root=child_root, provider=HTTPProvider(child_root), executor=self.execute, **config)
        self.assertEqual(child.session_id, reopened.session_id)
        self.assertEqual(inherited, reopened.task_context()['human_inputs'])

    def test_inherited_brief_preserves_author_and_source_through_compaction_and_descent(self):
        parent = self.session()
        parent.set_task_brief('Parent model interpretation of the caller.', author='parent-model', identity='brief')
        original = parent.task_context()['working_brief']
        child_root = self.root / 'brief-child'
        child = Session(child_root, 'implementer', 'child instructions', 'Delegated obligation.',
            HTTPProvider(child_root), [], self.execute, self.work, public_task='literal public task',
            inherited_brief=original)
        selected = child.task_context()['working_brief']
        self.assertEqual('parent-model', selected['author'])
        self.assertEqual(original, selected['source'])
        self.assertEqual(selected['ref'], child.state['working_brief_ref'])
        self.replies.append(answer('{"continuation":"Child interpretation with separate authored brief."}'))
        child.compact()
        self.assertEqual(selected, child.task_context()['working_brief'])
        messages = child._request(child.state)['messages']
        self.assertTrue(any('Parent model interpretation of the caller.' in str(m.get('content', '')) for m in messages))
        grand_root = self.root / 'brief-grandchild'
        grandchild = Session(grand_root, 'reviewer', 'review instructions', 'Check the selected caller.',
            HTTPProvider(grand_root), [], self.execute, self.work, public_task='literal public task',
            inherited_brief=child.task_context()['working_brief'])
        inherited = grandchild.task_context()['working_brief']
        self.assertEqual('parent-model', inherited['author'])
        self.assertEqual(original, inherited['source']['source'])
        self.assertEqual(parent.session_id, inherited['source']['source']['session_id'])
        self.assertEqual(str(parent.database), inherited['source']['source']['archive'])
        config = Session.recorded_config(grand_root)
        reopened = Session(root=grand_root, provider=HTTPProvider(grand_root), executor=self.execute, **config)
        self.assertEqual(inherited, reopened.task_context()['working_brief'])

    def test_automatic_messages_do_not_clear_explicit_stop(self):
        session = self.session()
        session.request_stop('stop')
        self.assertEqual('stopped', session.run()['status'])
        session.append_observation({'return': 'child completed'}, identity='return', resume=True, automatic=True)
        session.append_message('More findings.', sender='child', relationship='child')
        self.assertEqual('stopped', session.run()['status'])
        self.assertEqual([], self.requests)
        session.append_user('Resume with this requirement.', identity='resume')
        self.replies.append(answer('resumed'))
        self.assertEqual('resumed', session.run()['report'])

    def test_model_configures_after_complete_batch_and_changes_next_http_request(self):
        session = self.session()
        shell = copy.deepcopy(next(t for t in TOOLS if t['function']['name'] == 'exec_command'))
        session.reconfigure(reason='Provide supported shell', tools=[shell, *CONTROL_TOOLS])
        args = {'reason': 'Follow the observed caller', 'system': 'model revised instructions',
                'guidance': [{'author': 'main model', 'text': 'Check caller behavior.'}],
                'tools': [], 'settings': {'temperature': 0.4}, 'model': 'second'}
        self.replies.extend([{'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
            'tool_calls': [
                {'id': 'configure', 'type': 'function', 'function': {'name': 'configure_session', 'arguments': json.dumps(args)}},
                {'id': 'write', 'type': 'function', 'function': {'name': 'exec_command', 'arguments': json.dumps({'command': 'printf one > result'})}}]}}]}, answer('done')])
        identity = session.session_id
        self.assertEqual('done', session.run()['report'])
        self.assertEqual(identity, session.session_id)
        self.assertEqual('one', (self.work / 'result').read_text())
        self.assertEqual(1, len(self.effects))
        request = self.requests[1]['request']
        self.assertEqual('second', request['model'])
        self.assertEqual('model revised instructions', request['messages'][0]['content'])
        self.assertEqual([], request['tools'])
        self.assertEqual(0.4, request['temperature'])
        assistant = next(m for m in request['messages'] if m.get('tool_calls'))
        index = request['messages'].index(assistant)
        self.assertEqual(['configure', 'write'], [m['tool_call_id'] for m in request['messages'][index + 1:index + 3]])
        record = self.records('session_configuration')[-1][1]
        self.assertEqual('model:' + identity, record['author'])
        self.assertIn(record['source_ref'], [ref for ref, _ in self.records('model_response')])
        self.assertEqual('configured', self.records('configuration_result')[-1][1]['status'])
        self.assertEqual(self.endpoint, session.provider.config['endpoint'])

    def test_configuration_persistence_failure_stops_without_false_rejection(self):
        session = self.session()
        self.replies.append({'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'configure', 'type': 'function', 'function': {'name': 'configure_session',
                'arguments': json.dumps({'reason': 'explicit change', 'system': 'future instructions'})}}]}}]})
        original_save = session._save
        def save(state):
            if state.get('config_version', 1) > 1:
                raise OSError('simulated persistence failure')
            original_save(state)
        with patch.object(session, '_save', side_effect=save):
            with self.assertRaisesRegex(OSError, 'persistence failure'):
                session.run()
        self.assertEqual([], self.records('configuration_result'))
        self.assertEqual(1, len(self.requests))
        self.assertEqual(1, session._load()['config_version'])

    def test_model_destination_change_and_native_model_switch_are_specific_rejections(self):
        session = self.session()
        def configure(arguments, **native):
            return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'change', 'type': 'function', 'function': {'name': 'configure_session',
                    'arguments': json.dumps(arguments)}}], **native}}]}
        self.replies.extend([configure({'reason': 'new endpoint', 'endpoint': 'https://example.invalid'}),
            configure({'reason': 'new model', 'model': 'second'}, reasoning_details=[{'type': 'reasoning.encrypted', 'data': 'exact'}]), answer('done')])
        session.run()
        self.assertEqual(1, session.state['config_version'])
        self.assertEqual('first', session.provider.model)
        messages = self.requests[-1]['request']['messages']
        self.assertIn('cannot change endpoint', json.dumps(messages))
        self.assertIn('native assistant continuation', json.dumps(messages))
        self.assertEqual([{'type': 'reasoning.encrypted', 'data': 'exact'}], next(m for m in messages if 'reasoning_details' in m)['reasoning_details'])
        self.assertEqual('configuration_rejected', self.records('configuration_result')[-1][1]['status'])

    def test_model_stop_authorship_and_explicit_operator_resume(self):
        session = self.session()
        session.request_stop('model-stop', sender='parent-id')
        session.run()
        self.assertFalse(session.state.get('explicit_stop', False))
        self.assertEqual('model', self.records('control')[-1][1]['author'])
        session.append_message('Continue that test.', sender='parent-id', relationship='parent')
        self.replies.append(answer('model resumed'))
        self.assertEqual('model resumed', session.run()['report'])
        session.request_stop('human-stop')
        session.run()
        session.append_observation({'operator_continuation': True}, identity='operator-resume', resume=True)
        self.replies.append(answer('operator resumed'))
        self.assertEqual('operator resumed', session.run()['report'])
        self.assertFalse(session.state['explicit_stop'])

    def test_tool_schema_transition_updates_actual_runtime_validation(self):
        session = self.session()
        runtime = ToolRuntime(SimpleNamespace(workflow=None), 'main-id', {
            'root': str(session.root), 'workspace': str(self.work), 'tools': []})
        session.executor = runtime
        shell = copy.deepcopy(next(t for t in TOOLS if t['function']['name'] == 'exec_command'))
        shell['function']['parameters']['properties']['command']['enum'] = ['printf constrained > result']
        session.reconfigure(reason='Explicit available tool schema', tools=[shell])
        rejected = runtime('exec_command', {'command': 'touch wrong'}, 'wrong-effect')
        self.assertFalse(rejected['executed'])
        self.assertFalse((self.work / 'wrong').exists())
        runtime('exec_command', {'command': 'printf constrained > result'}, 'real-effect')
        self.assertEqual('constrained', (self.work / 'result').read_text())
        before = self.records('session_configuration')
        unsupported = {'type': 'function', 'function': {'name': 'imaginary_effect', 'parameters': {'type': 'object'}}}
        with self.assertRaisesRegex(ValueError, 'runtime does not provide imaginary_effect'):
            session.reconfigure(reason='Unsupported effect', tools=[unsupported])
        self.assertEqual(before, self.records('session_configuration'))

    def test_specific_incompatible_or_busy_transition_leaves_configuration(self):
        session = self.session()
        before = session.state['config']
        with self.assertRaisesRegex(ValueError, 'Session owns request fields'):
            session.reconfigure(reason='invalid request', settings={'messages': []})
        with self.assertRaisesRegex(ValueError, 'Guidance requires'):
            session.reconfigure(reason='unattributed guidance', guidance=[{'text': 'claim'}])
        state = session._load()
        state['pending'] = {'calls': [], 'index': 0}
        session._save(state)
        with self.assertRaisesRegex(ValueError, 'complete tool results'):
            session.reconfigure(reason='unfinished group', system='changed')
        self.assertEqual(before, session._load()['config'])
