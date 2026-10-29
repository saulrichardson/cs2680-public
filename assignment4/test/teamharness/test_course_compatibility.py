"""Focused course lifecycle and actual HTTP request/effect witnesses."""
import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from src import bridge
from src.teamharness.agents import AgentManager
from src.teamharness.context import APPEND_ONLY
from src.teamharness.provider import HTTPProvider
from src.teamharness.session import presented_messages
from test_main_flow import repository, http_script, finish, call, last_tool, audit_requests
from test_session import answer


class CourseCompatibility(unittest.TestCase):
    def test_worker_acquires_until_exhausted_and_shutdown_preserves_completed_work(self):
        class Shutdown(Exception): pass
        dispatcher_module = types.SimpleNamespace(DispatcherShutdown=Shutdown)
        with patch.dict(sys.modules, {'lab.adapters.teamharness.bridge': bridge, 'dispatcher': dispatcher_module}):
            adapter = importlib.import_module('lab.adapters.teamharness.agentic_loop')
            for ending in (None, Shutdown()):
                items = iter([{'task': 1}, {'task': 2}, ending])
                class Dispatcher:
                    def next_task(self):
                        item = next(items)
                        if isinstance(item, Exception): raise item
                        return item
                agent = adapter.Agent(Dispatcher())
                solved = []
                agent.solve = lambda task, config: solved.append(task['task'])
                agent.worker({})
                self.assertEqual(solved, [1, 2])
                facts = agent.runtime_facts()
                self.assertNotIn('run_cap_seconds', facts)
                self.assertNotIn('shared_gpu_count', facts)
                self.assertGreaterEqual(facts['elapsed_seconds'], 0)

    def test_legacy_late_system_projection_preserves_original_messages(self):
        state = {'config': {'context_policy': APPEND_ONLY}, 'messages': [
            {'role': 'system', 'content': 'Foundation'}, {'role': 'user', 'content': 'Assignment'},
            {'role': 'system', 'content': 'Dated execution observation'}]}
        original = copy.deepcopy(state)
        projected = presented_messages(state, Path('/unused'))
        self.assertEqual([i for i,m in enumerate(projected) if m['role']=='system'], [0])
        self.assertIn('Dated execution observation', projected[-1]['content'])
        self.assertEqual(state, original)

    def test_http_exact_edit_read_failure_compaction_and_dependent_check(self):
        case = self
        assignment = 'Inspect and repair the answer implementation; preserve its signature.'
        public = 'Return each integer unchanged.'
        class Model:
            def __init__(self): self.requests=[]; self.errors=[]; self.step=0
            def reply(self, request):
                case.assertEqual([i for i,m in enumerate(request['messages']) if m['role']=='system'], [0])
                if 'response_format' in request:
                    return answer(json.dumps({'continuation': 'My interpretation: all checks passed.'}))
                n=self.step; self.step+=1
                if n==0: return call('read_file', {'path':'missing.py'})
                receipt=last_tool(request) if n<=4 else None
                if n==1:
                    case.assertEqual(receipt['execution_state'], 'failed')
                    case.assertTrue(receipt['replay_safe'])
                    rendered=next(m['content'] for m in reversed(request['messages']) if m['role']=='tool')
                    case.assertNotIn('action may have run', rendered)
                    return call('edit_file', {'path':'answer.py','replacements':[
                        {'old':'return -1', 'new':'return n'}, {'old':'MISSING', 'new':'unused'}]})
                if n==2:
                    case.assertFalse(receipt['applied'])
                    case.assertEqual((repo/'answer.py').read_text(), 'def answer(n):\n    return -1\n')
                    return call('edit_file', {'path':'answer.py','replacements':[{'old':'return -1','new':'return n'}]})
                if n==3:
                    case.assertTrue(receipt['applied'])
                    for key in ('before_ref','after_ref'):
                        artifact=receipt['_artifacts'][key]
                        case.assertTrue(artifact['path'])
                    # This response follows the completed edit receipt.
                    return call('exec_command', {'command':"python3 -c 'from answer import answer; assert answer(7)==7; print(7)'"})
                if n==4:
                    case.assertEqual(receipt['exit_code'],0)
                    return call('compact', {'retain_recent_exchanges':1})
                case.assertIn(assignment, str(request['messages']))
                case.assertIn('WORKSPACE ASSIGNMENT', str(request['messages']))
                case.assertIn(public, str(request['messages']))
                case.assertIn('My interpretation: all checks passed.', str(request['messages']))
                return finish('Exact edit and later real check completed.')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); repo=repository(root); model=Model()
            with http_script(model) as env, patch.dict(os.environ, env):
                manager=AgentManager(root/'team',HTTPProvider)
                try:
                    ident=manager.create('investigator',assignment,repo,public_task=public)
                    manager.start(ident); result=manager.wait(ident,30)
                    self.assertEqual(model.errors,[])
                    self.assertEqual(result['status'],'completed',result)
                    descriptor=manager.descriptor(ident)
                    self.assertEqual(audit_requests(Path(descriptor['root'])/'session.sqlite3'),model.requests)
                    self.assertEqual((repo/'answer.py').read_text(),'def answer(n):\n    return n\n')
                    # Immutable original edit bytes remain retrievable.
                    receipt=last_tool(model.requests[3])
                    before=receipt['_artifacts']['before_ref']; after=receipt['_artifacts']['after_ref']
                    self.assertEqual(Path(before['path']).read_text(),'def answer(n):\n    return -1\n')
                    self.assertEqual(Path(after['path']).read_text(),'def answer(n):\n    return n\n')
                    destination=os.environ.get('TEAMHARNESS_COMPAT_WIRE')
                    if destination:
                        out=Path(destination);out.mkdir(parents=True,exist_ok=False)
                        for i,request in enumerate(model.requests):
                            (out/f'request-{i:03}.json').write_text(json.dumps(request))
                finally: manager.shutdown()
