"""Advertised contracts, rejected effects and successful correction over real HTTP."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from src.teamharness.agents import AgentManager
from src.teamharness.contracts import check_schema, validate
from src.teamharness.main import Investigation
from src.teamharness.provider import HTTPProvider
from src.teamharness.session import CONTROL_TOOLS, Session
from src.teamharness.tools import TOOLS, EVALUATION_TOOL
from test_main_flow import repository, http_script, call, finish, audit_requests
from test_session import tools, Script
from receipt_fixture import original_receipt


class ToolContracts(unittest.TestCase):
    def test_ambiguous_or_malformed_json_has_no_effect_and_a_correct_call_can_follow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executed = []
            invalid = []
            for raw in ('{"command":"touch forbidden","command":"touch also_forbidden"}',
                        '{"command":"touch forbidden","yield_time_ms":NaN}',
                        '{"command":'):
                response = call('exec_command', {})
                response['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = raw
                invalid.append(response)
            provider = Script([*invalid, call('exec_command', {'command': 'printf x >> once'}), finish()])

            def execute(name, arguments, identity):
                executed.append(identity)
                result = subprocess.run(['/bin/sh', '-c', arguments['command']], cwd=root, capture_output=True, text=True)
                return {'state': 'exited', 'exit_code': result.returncode, 'stdout': result.stdout}

            session = Session(root / 'session', 'implementer', 'system', 'Observe actual effects.', provider,
                [t for t in TOOLS if t['function']['name'] == 'exec_command'], execute, root)
            self.assertEqual(session.run()['status'], 'completed')
            self.assertEqual(len(executed), 1)
            self.assertEqual((root / 'once').read_text(), 'x')
            self.assertFalse((root / 'forbidden').exists())
            self.assertFalse((root / 'also_forbidden').exists())
            observations = str(provider.requests[3]['messages'])
            self.assertIn('Duplicate tool argument field: command', observations)
            self.assertIn('finite JSON numbers', observations)
            self.assertIn('column', observations)
            audit_requests(session.database)

    def test_contracts_examples_composition_and_no_argument_rewriting(self):
        for tool in TOOLS + [EVALUATION_TOOL] + CONTROL_TOOLS:
            schema = tool['function']['parameters']
            with self.subTest(tool=tool['function']['name']):
                check_schema(schema)
                for example in schema['examples']:
                    original = copy.deepcopy(example)
                    validate(example, schema)
                    self.assertEqual(example, original)
        # Composition was silently ignored by the old subset validator.
        schema = {'type': 'object', 'properties': {'kind': {'enum': ['read', 'write']},
                  'payload': {'type': 'array', 'items': {'type': 'integer'}}},
                  'required': ['kind'], 'additionalProperties': False,
                  'if': {'properties': {'kind': {'const': 'write'}}},
                  'then': {'required': ['payload']}}
        for invalid in ({'kind': 'write'}, {'kind': 'write', 'payload': '[1]'},
                        {'kind': 'write', 'payload': [True]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate(invalid, schema)
        value = {'kind': 'write', 'payload': [1]}
        validate(value, schema)
        value = {}
        validate(value, {'type': 'object', 'properties': {'x': {'type': 'integer', 'default': 7}}})
        self.assertEqual(value, {})
        with self.assertRaisesRegex(ValueError, 'local'):
            check_schema({'$ref': 'https://example.invalid/schema'})

    def test_http_invalid_arguments_do_not_execute_and_corrections_use_the_actual_menu(self):
        case = self
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = repository(root)
            menu = [copy.deepcopy(t) for t in TOOLS if t['function']['name'] == 'read_file']
            # Exercise a model-selected constraint, not only the maintained menu.
            menu[0]['function']['parameters']['properties']['line_count']['maximum'] = 1
            menu += [copy.deepcopy(t) for t in CONTROL_TOOLS if t['function']['name'] == 'finish_role']

            class Model:
                def __init__(self):
                    self.requests, self.errors, self.step = [], [], 0

                def reply(self, request):
                    step = self.step
                    self.step += 1
                    if step == 0:
                        self.original_schema = next(t['function']['parameters'] for t in request['tools']
                                                    if t['function']['name'] == 'exec_command')
                        return tools(('exec_command', {'command': 'touch forbidden', 'typo': True}),
                                     ('exec_command', {'command': 'printf accepted > observed'}))
                    receipts = [original_receipt(m['content']) for m in request['messages'] if m['role'] == 'tool']
                    if step == 1:
                        case.assertFalse((repo / 'forbidden').exists())
                        case.assertEqual((repo / 'observed').read_text(), 'accepted')
                        case.assertIs(receipts[-2]['executed'], False)
                        recovery = receipts[-2]['recovery']
                        case.assertEqual(recovery['expected_parameters'], self.original_schema)
                        case.assertEqual(recovery['unknown_fields'], ['typo'])
                        return call('configure_session', {'reason': 'Read one line at a time.', 'tools': menu})
                    if step == 2:
                        case.assertEqual([t['function']['name'] for t in request['tools']], ['read_file', 'finish_role'])
                        for tool in request['tools']:
                            for example in tool['function']['parameters'].get('examples', []):
                                validate(example, tool['function']['parameters'])
                        return tools(('read_file', {'path': 'answer.py', 'line_count': 2}),
                                     ('read_file', {'path': 'answer.py', 'line_count': 1}))
                    if step == 3:
                        case.assertIs(receipts[-2]['executed'], False)
                        expected = request['tools'][0]['function']['parameters']
                        recovery = receipts[-2]['recovery']
                        case.assertEqual(recovery['expected_parameters'], expected)
                        for example in recovery['examples']:
                            validate(example, expected)
                        case.assertEqual(receipts[-1]['content'], 'def answer(n):\n')
                        return finish('')
                    if step == 4:
                        case.assertIs(receipts[-1]['executed'], False)
                        case.assertEqual(receipts[-1]['recovery']['expected_parameters'], request['tools'][1]['function']['parameters'])
                        return finish('Observed the actual source after correcting rejected calls.')
                    raise AssertionError('Unexpected extra inference')

            model = Model()
            with http_script(model) as environment, patch.dict(os.environ, environment):
                manager = AgentManager(root / 'agents', HTTPProvider, deadline=time.time() + 30)
                try:
                    ident = manager.create('main', 'Inspect the public operation.', repo)
                    manager.start(ident)
                    result = manager.wait(ident, 20)
                    self.assertEqual(model.errors, [])
                    self.assertEqual(result['status'], 'completed', result)
                    database = Path(manager.descriptor(ident)['root']) / 'session.sqlite3'
                    self.assertEqual(audit_requests(database), model.requests)
                finally:
                    manager.shutdown()

    def test_decision_rejection_describes_only_its_advertised_contract(self):
        case = self
        for surplus in ({'checks': '[]'}, {'assessment': '{"readiness":"repair","effort":"small"}'}, {}):
            with self.subTest(surplus=surplus), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo = repository(root)

                class Model:
                    def __init__(self):
                        self.requests, self.errors = [], []

                    def reply(self, request):
                        if len(self.requests) == 1:
                            return call('read_file', {'path': 'answer.py'})
                        case.assertEqual(len(self.requests), 2)
                        self.schema = request['tools'][0]['function']['parameters']
                        args = {'action': 'continue', **surplus}
                        if surplus:
                            args['plan'] = 'Trace answer.py and its real consumer; implement and check the identity operation.'
                        return call('repair_plan', args)

                model = Model()
                with http_script(model) as environment, patch.dict(os.environ, environment):
                    manager = AgentManager(root / 'run/agents', HTTPProvider)
                    try:
                        flow = Investigation(manager, root / 'run', planning_boundary={'max_turns': 1})
                        result = flow.run('Repair the identity operation.', repo)
                        self.assertEqual(model.errors, [])
                        self.assertEqual(result['state'], 'planning_timeout')
                        ident = result['agents']['main']
                        archive = Path(manager.descriptor(ident)['root'])
                        with sqlite3.connect(archive / 'session.sqlite3') as db:
                            receipt = json.loads(db.execute("SELECT body FROM objects WHERE kind='tool_result' ORDER BY id DESC LIMIT 1").fetchone()[0])['receipt']
                        self.assertIs(receipt['executed'], False)
                        recovery = receipt['recovery']
                        self.assertEqual(recovery['expected_parameters'], model.schema)
                        self.assertEqual(set(model.schema['properties']), {'action', 'plan', 'reason'})
                        for example in recovery['examples']:
                            validate(example, model.schema)
                        self.assertFalse((archive / 'planning-decision.json').exists())
                        self.assertEqual((repo / 'answer.py').read_text(), 'def answer(n):\n    return -1\n')
                        audit_requests(archive / 'session.sqlite3')
                    finally:
                        manager.shutdown()
