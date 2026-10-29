"""Retained real HTTP/process boundaries; generated laws live in test_repair_properties."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from src.teamharness import repair
from src.teamharness.agents import AgentManager
from src.teamharness.candidates import Grader, capture, grade_candidate
from src.teamharness.main import Investigation
from src.teamharness.provider import HTTPProvider
from test_main_flow import repository, facts, call, finish, last_tool, http_script, audit_requests


class PlannedRepairTests(unittest.TestCase):
    def test_real_commands_subdirectories_mutation_and_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = repository(root)
            sub = repo / 'nested'; sub.mkdir()
            (sub / 'marker').write_text('yes')
            subprocess.run(['git','-C',str(repo),'add','.'],check=True)
            subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@local','commit','-qm','nested fixture'],check=True)
            base = subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
            candidate = capture(repo, root / 'patch', base)
            result = grade_candidate(repo, base, candidate,
                Grader((sys.executable, '-c', 'from pathlib import Path; assert Path("marker").read_text()=="yes"')),
                root / 'cwd', cwd='nested')
            self.assertTrue(result['source_unchanged']); self.assertEqual(result['exit_code'], 0)
            timed = grade_candidate(repo, base, candidate,
                Grader((sys.executable, '-c', 'import time; time.sleep(30)')),
                root / 'timeout', deadline=time.time()+.3)
            self.assertEqual(timed['state'], 'deadline_exceeded')
            mutated = grade_candidate(repo, base, candidate,
                Grader((sys.executable, '-c', 'from pathlib import Path; Path("answer.py").write_text("changed")')),
                root / 'mutation')
            self.assertFalse(repair.assess({}, mutated)['passed'])
            self.assertEqual((repo / 'answer.py').read_text(), 'def answer(n):\n    return -1\n')

    def test_full_http_plan_baseline_repair_completes_without_review(self):
        self._flow()

    def test_later_edits_require_a_new_exact_candidate_execution(self):
        self._flow(drift=True)

    def _flow(self, drift=False):
        case = self
        class Model:
            def __init__(self): self.requests=[]; self.errors=[]; self.steps={}; self.review=None
            def reply(self, request):
                f = facts(request); ident=f['agent_id']
                n=self.steps.get(ident,0); self.steps[ident]=n+1
                descriptor=json.loads((Path(f['archive_root'])/'agent.json').read_text())
                if descriptor['role']=='reviewer':
                    case.assertNotIn('MAIN_PRIVATE_THEORY', str(request['messages']))
                    if n==0: return call('exec_command', {'command': 'python3 -B -c "from answer import answer; assert answer(15)==15"'})
                    return finish('No counterexample in the actual public function at 15; other domains untested.')
                if n==0:
                    return call('repair_plan', {'action':'set', 'plan':'MAIN_PRIVATE_THEORY: reproduce the real answer consumer, then repair.',
                        'guidance':[{'path':'contracts.md'}],
                        'checks':[{'name':'actual consumer', 'argv':[sys.executable,'-B','-c',
                        'from answer import answer; assert all(answer(n)==n for n in [-5,0,15])']}]})
                if n==1:
                    r=last_tool(request); case.assertTrue(r['authored_guidance'][0]['text'])
                    case.assertIn('An invented example', str(request['messages']))
                    return call('repair_plan', {'action':'run','target':'baseline'})
                if n==2:
                    case.assertNotEqual(last_tool(request)['checks'][0]['execution']['exit_code'],0)
                    return call('exec_command', {'command': "printf 'def answer(n):\\n    return n\\n' > answer.py"})
                if n==3: return call('repair_plan', {'action':'run','target':'candidate'})
                if n==4:
                    case.assertTrue(last_tool(request)['passed'])
                    case.assertIn('comparison to latest capture: same', str(request['messages']))
                    execution=last_tool(request)
                    case.assertTrue(execution['checks'][0]['definition_sha256'])
                    case.assertIn('environment',execution['evidence_scope'])
                    self.tested=last_tool(request)['candidate']['sha256']
                    self.test_receipt=last_tool(request)['receipt_path']
                    if not drift:
                        return finish('Verified the real consumer; no separate review was needed.')
                    if drift:
                        return call('exec_command', {'command': "printf '# later edit\\n' >> answer.py"})
                if drift and n>=5: n-=1
                if n==4:
                    return call('checkpoint', {'review':True})
                if n==5:
                    self.review=last_tool(request)['adversarial_review']['agent_id']
                    return call('wait_agent', {'agent_id':self.review, 'timeout_seconds':10})
                if n==6:
                    case.assertIn('comparison to latest capture: different', str(request['messages']))
                    return finish('Real consumer tests pass on the selected candidate; review checked input 15.')
                raise AssertionError('Unexpected extra main iteration')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=repository(root); model=Model()
            with http_script(model) as env, patch.dict(os.environ,env):
                manager=AgentManager(root/'run/agents',HTTPProvider)
                manager.automatic_wakes_enabled=False
                workflow=Investigation(manager,root/'run',completion_gate=True)
                try:
                    result=workflow.run('Implement answer(n) returning n.',repo)
                    self.assertEqual(model.errors,[])
                    self.assertEqual(result['state'],'completed')
                    completion=result['completion_attempts'][result['latest_completion']]
                    self.assertTrue(completion['checks']['executable_test_plan']['passed'])
                    self.assertNotIn('adversarial_review_delivery',completion['checks'])
                    if drift:
                        self.assertNotEqual(completion['candidate']['sha256'],model.tested)
                        self.assertNotEqual(completion['checks']['executable_test_plan']['result']['receipt_path'],model.test_receipt)
                    else:
                        self.assertEqual(completion['candidate']['sha256'],model.tested)
                        self.assertEqual(completion['checks']['executable_test_plan']['result']['receipt_path'],model.test_receipt)
                    self.assertEqual(completion['checks']['executable_test_plan']['result']['candidate']['sha256'],completion['candidate']['sha256'])
                    self.assertEqual(len(result['reviews']),int(drift))
                    for ident in model.steps:
                        audit_requests(Path(manager.descriptor(ident)['root'])/'session.sqlite3')
                finally: manager.shutdown()


if __name__ == '__main__': unittest.main()
