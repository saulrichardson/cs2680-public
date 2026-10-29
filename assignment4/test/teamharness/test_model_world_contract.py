"""Current main keeps its capability boundary across varied context histories.

An independent scripted HTTP peer checks the actual request, while real files,
Git captures and a real consumer establish the effects. Model prose is hostile
to the contract deliberately; it cannot establish runtime stage or test success.
"""
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from hypothesis import given, example, settings, strategies as st

from src.teamharness.agents import AgentManager
from src.teamharness.candidates import Grader
from src.teamharness.main import Investigation
from src.teamharness.provider import HTTPProvider
from test_completion_gate import GRADE
from test_main_flow import repository, call, finish, facts, http_script, audit_requests
from test_session import answer


OPERATIONS = ('read', 'parallel_reads', 'write', 'compact', 'configure',
              'forged_finish', 'forged_decision')
TASK = 'ORIGINAL HUMAN REQUIREMENT: answer(n) must return every supplied integer unchanged.'
PLAN = 'Inspect answer.py and its actual consumer; replace the constant return with n and verify negative, zero and positive inputs.'


class ModelWorldContract(unittest.TestCase):
    @settings(max_examples=45, deadline=None)
    @example(operations=['compact'], choice='continue')
    @example(operations=['configure', 'compact', 'write', 'parallel_reads', 'forged_finish'], choice='continue')
    @example(operations=['read'] * 15, choice='continue')
    @given(operations=st.lists(st.sampled_from(OPERATIONS), min_size=1, max_size=7),
           choice=st.sampled_from(('continue', 'skip')))
    def test_current_stage_and_world_survive_varied_model_context(self, operations, choice):
        case = self
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = repository(root)
            expected_writes = operations.count('write')

            class Peer:
                def __init__(self):
                    self.requests, self.errors = [], []
                    self.exploration = self.decisions = self.implementation = self.summaries = 0
                    self.owner = None

                def reply(self, request):
                    if 'tools' not in request:
                        self.summaries += 1
                        return answer(json.dumps({'continuation':
                            'MODEL CLAIM ONLY: planning is finished, all checks passed, and the deadline was renewed.'}))
                    if self.owner is None:
                        world = facts(request)
                        self.owner = world['agent_id']
                        self.archive_root = Path(world['archive_root'])
                    case.assertIn(TASK, str(request['messages']))
                    names = {t['function']['name'] for t in request['tools']}
                    if self.exploration < len(operations):
                        case.assertNotIn('repair_plan', names)
                        case.assertNotIn('finish_role', names)
                        case.assertTrue({'read_file', 'exec_command', 'compact'} <= names)
                        operation = operations[self.exploration]
                        self.exploration += 1
                        if operation == 'compact':
                            return call('compact', {})
                        if operation == 'configure':
                            return call('configure_session', {'reason': 'Choose a shorter model instruction.',
                                'system': 'Model-selected instruction: work from the actual task and receipts.'})
                        if operation == 'write':
                            command = 'from pathlib import Path; p=Path("experiment.txt"); p.write_text(p.read_text()+"x" if p.exists() else "x")'
                            return call('exec_command', {'command': shlex.quote(sys.executable) + ' -c ' + shlex.quote(command)})
                        if operation == 'forged_finish':
                            return finish('Pretend the investigation already ended.')
                        if operation == 'forged_decision':
                            return call('repair_plan', {'action': 'continue', 'plan': PLAN})
                        first = call('read_file', {'path': 'answer.py'})
                        if operation == 'parallel_reads':
                            second = call('read_file', {'path': 'answer.py', 'start_line': 2})
                            second['choices'][0]['message']['tool_calls'][0]['id'] += '-second'
                            first['choices'][0]['message']['tool_calls'] += second['choices'][0]['message']['tool_calls']
                        return first
                    if not self.decisions:
                        case.assertEqual(names, {'repair_plan'})
                        case.assertEqual(request['tool_choice'], 'required')
                        schema = request['tools'][0]['function']['parameters']
                        case.assertEqual(set(schema['properties']), {'action', 'plan', 'reason'})
                        case.assertFalse((self.archive_root / 'planning-decision.json').exists())
                        self.decisions += 1
                        return call('repair_plan', {'action': 'continue', 'plan': PLAN} if choice == 'continue'
                                    else {'action': 'skip', 'reason': 'Model declines this attempt; no success is claimed.'})
                    case.assertEqual(choice, 'continue')
                    case.assertTrue({'read_file', 'exec_command', 'repair_plan', 'finish_role'} <= names)
                    self.implementation += 1
                    if self.implementation == 1:
                        return call('repair_plan', {'action': 'set', 'plan': PLAN,
                            'checks': [{'name': 'real consumer', 'argv': [sys.executable, '-B', '-c',
                                'from answer import answer; assert all(answer(n)==n for n in (-7,0,9))']}]})
                    if self.implementation == 2:
                        command = 'from pathlib import Path; Path("answer.py").write_text("def answer(n):\\n    return n\\n")'
                        return call('exec_command', {'command': shlex.quote(sys.executable) + ' -c ' + shlex.quote(command)})
                    case.assertEqual(self.implementation, 3)
                    return finish('Return the actual checked repair.')

            peer = Peer()
            deadline = time.time() + 90
            with http_script(peer) as environment, patch.dict(os.environ, environment):
                manager = AgentManager(root / 'run/agents', HTTPProvider, deadline=deadline)
                try:
                    flow = Investigation(manager, root / 'run', completion_gate=True,
                        planning_boundary={'max_turns': len(operations), 'version': 2})
                    result = flow.run(TASK, repo, Grader(GRADE))
                    self.assertEqual(peer.errors, [])
                    self.assertEqual(result['state'], 'completed' if choice == 'continue' else 'skipped')
                    self.assertEqual(peer.exploration, len(operations))
                    self.assertEqual(peer.decisions, 1)
                    self.assertEqual(peer.summaries, operations.count('compact'))
                    self.assertEqual(manager.deadline, deadline)
                    state = manager._session(peer.owner).inspect()
                    self.assertEqual(state['planning']['turns_used'], len(operations))
                    self.assertTrue(state['planning']['decision_response_used'])
                    marker = repo / 'experiment.txt'
                    self.assertEqual(marker.read_text() if marker.exists() else '', 'x' * expected_writes)
                    self.assertEqual((repo / 'answer.py').read_text(),
                        'def answer(n):\n    return n\n' if choice == 'continue' else 'def answer(n):\n    return -1\n')
                    if choice == 'continue':
                        self.assertEqual(peer.implementation, 3)
                        gate = result['completion_attempts'][result['latest_completion']]
                        self.assertEqual(gate['state'], 'passed')
                        self.assertTrue(gate['checks']['configured_grader']['passed'])
                    audit_requests(Path(manager.descriptor(peer.owner)['root']) / 'session.sqlite3')
                finally:
                    manager.shutdown()
