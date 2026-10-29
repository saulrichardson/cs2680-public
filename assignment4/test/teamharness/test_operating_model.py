"""Current operating model through real HTTP, Git, tools, grading and recovery."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from src.teamharness.agents import AgentManager
from src.teamharness.candidates import Grader
from src.teamharness.main import Investigation
from src.teamharness.provider import HTTPProvider
from test_completion_gate import GRADE
from test_main_flow import repository, call, finish, facts, last_tool, http_script, audit_requests
from test_planning_boundary import Model, EDIT


PLAN = '''Required behavior: answer(n) must return n for negative, zero and positive integers.
Repository investigation: inspected answer.py, answer(n), and its direct Python consumer; the function owns the returned value.
Diagnosis and uncertainty: source returns -1 for every input. No additional caller or persistence state was found in this fixture.
Next work: replace the constant with the input; inspect any unexpected test failure before expanding the change.
Verification: invoke answer through an import on -7, 0 and 9; checking only -1 would accept an incomplete repair.'''


class OperatingModel(unittest.TestCase):
    def test_fifteen_parallel_exploration_responses_then_one_narrow_decision_and_real_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = repository(root)
            def read(_request):
                result = call('read_file', {'path': 'answer.py'})
                extra = call('read_file', {'path': 'answer.py', 'start_line': 2, 'line_count': 1})
                extra['choices'][0]['message']['tool_calls'][0]['id'] += '-second'
                result['choices'][0]['message']['tool_calls'] += extra['choices'][0]['message']['tool_calls']
                return result
            def decide(request):
                self.assertIn('return -1', str(request['messages'][-3:]))
                self.assertEqual([t['function']['name'] for t in request['tools']], ['repair_plan'])
                self.assertEqual(request['tool_choice'], 'required')
                self.assertIs(request['parallel_tool_calls'], False)
                from src.teamharness.session import context_status, request_bytes
                self.assertEqual(context_status(Path(facts(request)['archive_root']))['active_request_bytes'], request_bytes(request))
                props = request['tools'][0]['function']['parameters']['properties']
                self.assertEqual(set(props), {'action','plan','reason'})
                return call('repair_plan', {'action':'continue','plan':PLAN})
            model = Model([read] * 15 + [decide, call('exec_command', EDIT), finish()])
            with http_script(model) as env, patch.dict(os.environ, env):
                manager = AgentManager(root/'run/agents', HTTPProvider, deadline=time.time()+60)
                try:
                    flow = Investigation(manager, root/'run', planning_boundary=True, completion_gate=True)
                    result = flow.run('Repair answer(n).', repo, Grader(GRADE))
                    self.assertEqual(model.errors, [])
                    self.assertEqual(result['state'], 'completed', result)
                    ident = result['agents']['main']; d = manager.descriptor(ident)
                    state = manager._session(ident).inspect()
                    self.assertEqual(state['planning']['turns_used'], 15)
                    self.assertTrue(state['planning']['decision_response_used'])
                    self.assertEqual(len(model.requests), 18)
                    for request in model.requests[:15]:
                        names = {t['function']['name'] for t in request['tools']}
                        self.assertNotIn('repair_plan', names); self.assertNotIn('finish_role', names)
                        self.assertTrue(request['parallel_tool_calls'])
                    groups = list((Path(d['root'])/'effects/parallel-groups').glob('native-*/group.json'))
                    self.assertEqual(len(groups), 15)
                    gate = result['completion_attempts'][result['latest_completion']]
                    self.assertNotIn('executable_test_plan', gate['checks'])
                    self.assertIn('verification_gap', gate)
                    self.assertTrue(gate['checks']['configured_grader']['passed'])
                    self.assertNotIn('handoff', result)
                    self.assertEqual((repo/'answer.py').read_text(), 'def answer(n):\n    return n\n')
                    audit_requests(Path(d['root'])/'session.sqlite3')
                finally: manager.shutdown()

    def test_final_decision_rejects_out_of_contract_calls_and_reopen_does_not_renew_it(self):
        mixed=call('repair_plan',{'action':'continue','plan':PLAN})
        extra=call('exec_command',{'command':'touch forbidden-write'})
        extra['choices'][0]['message']['tool_calls'][0]['id']+='-extra'
        mixed['choices'][0]['message']['tool_calls']+=extra['choices'][0]['message']['tool_calls']
        attempts=[call('exec_command',{'command':'touch forbidden-write'}),
                  call('repair_plan',{'action':'set','plan':PLAN}),
                  call('repair_plan',{'action':'continue','plan':PLAN,'checks':[]}),mixed]
        for attempted in attempts:
            with self.subTest(attempt=attempted),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);repo=repository(root)
                model=Model([call('read_file',{'path':'answer.py'}),attempted])
                manager=AgentManager(root/'run/agents',lambda _:model,deadline=time.time()+30)
                try:
                    flow=Investigation(manager,root/'run',planning_boundary={'max_turns':1},completion_gate=True)
                    result=flow.run('Investigate answer.',repo)
                    self.assertEqual(result['state'],'planning_timeout')
                    self.assertFalse((repo/'forbidden-write').exists())
                    agent_root=Path(manager.descriptor(result['agents']['main'])['root'])
                    self.assertFalse((agent_root/'planning-decision.json').exists())
                    self.assertFalse((agent_root/'repair-plans/current.json').exists())
                    self.assertEqual(flow.resume()['state'],'planning_timeout')
                    self.assertEqual(len(model.requests),2)
                    audit_requests(agent_root/'session.sqlite3')
                finally:manager.shutdown()

    def test_interactive_failure_stays_owned_then_failing_finish_transfers_without_rescreening(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=repository(root);case=self
            class Script:
                def __init__(self):self.steps={};self.requests=[];self.errors=[];self.original=None
                def reply(self,request):
                    f=facts(request);ident=f['agent_id'];n=self.steps.get(ident,0);self.steps[ident]=n+1
                    self.original=self.original or ident
                    if ident==self.original:
                        if n==0:return call('read_file',{'path':'answer.py'})
                        if n==1:return call('repair_plan',{'action':'continue','plan':PLAN})
                        if n==2:return call('exec_command',{'command':"printf 'def answer(n):\\n    return n+1\\n' > answer.py"})
                        if n==3:return call('evaluate_candidate',{})
                        case.assertEqual(last_tool(request)['grade']['receipt']['tests_failed'],3)
                        return finish('Ready for completion, despite the observed failing grade.')
                    case.assertNotIn('planning_boundary',f)
                    case.assertTrue(f['review_takeover'])
                    case.assertNotIn('Ready for completion, despite',str(request['messages']))
                    if n==0:
                        case.assertIn('return n+1',(Path(json.loads((Path(f['archive_root'])/'agent.json').read_text())['workspace'])/'answer.py').read_text())
                        return call('repair_plan',{'action':'set','plan':PLAN,'checks':[{'name':'actual consumer','argv':list(GRADE[:-1])+['from answer import answer; assert answer(9)==9']}]})
                    if n==1:return call('exec_command',EDIT)
                    return finish('Independent diagnosis and implementation complete.')
            model=Script()
            with http_script(model) as env,patch.dict(os.environ,env):
                manager=AgentManager(root/'run/agents',HTTPProvider,deadline=time.time()+60)
                try:
                    result=Investigation(manager,root/'run',planning_boundary={'max_turns':1},completion_gate=True).run('Repair answer(n).',repo,Grader(GRADE))
                    self.assertEqual(model.errors,[])
                    self.assertEqual(result['state'],'completed',result)
                    self.assertEqual(len(result['handoff_history']),1)
                    old,new=result['agents']['original_main'],result['agents']['main']
                    self.assertEqual(model.steps,{old:5,new:3})
                    first=list(result['completion_attempts'].values())[0]
                    self.assertTrue(first['checks']['configured_grader']['result']['reused'])
                    self.assertEqual(result['handoff_history'][0]['starting_candidate']['sha256'],first['candidate']['sha256'])
                    self.assertNotIn('planning_boundary',manager.descriptor(new))
                    for ident in (old,new):audit_requests(Path(manager.descriptor(ident)['root'])/'session.sqlite3')
                finally:manager.shutdown()

    def test_constrained_allocation_decision_pauses_and_admits_the_same_owner(self):
        for choice in ('continue','skip'):
            with self.subTest(choice=choice),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);repo=repository(root)
                estimate={'readiness':'experiment','effort':'small'}
                def decide(request):
                    schema=request['tools'][0]['function']['parameters']
                    self.assertIn('assessment',schema['required'])
                    args={'action':choice,'assessment':estimate}
                    args.update({'plan':PLAN} if choice=='continue' else {'reason':'The caller needs one more distinguishing experiment.'})
                    return call('repair_plan',args)
                model=Model([call('read_file',{'path':'answer.py'}),decide,
                             call('repair_plan',{'action':'set','plan':PLAN}),call('exec_command',EDIT),finish()])
                manager=AgentManager(root/'run/agents',lambda _:model,deadline=time.time()+30)
                try:
                    flow=Investigation(manager,root/'run',planning_boundary={'max_turns':1,'pause_after_planning':True},completion_gate=True)
                    paused=flow.run('Repair answer.',repo)
                    self.assertEqual(paused['state'],'planning_complete')
                    self.assertEqual(paused['main_return']['planning']['decision']['assessment'],estimate)
                    self.assertEqual(model.step,2)
                    ident=paused['agents']['main'];session_id=paused['main_return']['session_id']
                    resumed=flow.admit('Selected for implementation from the frozen allocation.')
                    self.assertEqual(resumed['state'],'completed')
                    self.assertEqual(resumed['agents']['main'],ident)
                    self.assertEqual(resumed['main_return']['session_id'],session_id)
                    self.assertEqual(resumed['main_return']['planning']['decision']['decision'],choice)
                    self.assertEqual(resumed['main_return']['planning']['turns_used'],1)
                    audit_requests(Path(manager.descriptor(ident)['root'])/'session.sqlite3')
                finally:manager.shutdown()

    def test_published_recovery_notification_survives_loss_without_replaying_checks(self):
        from test_completion_gate import RepairModel
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=repository(root);counter=root/'execution-count'
            model=RepairModel(self,same_owner=True)
            command={'name':'actual-consumer','argv':[sys.executable,'-B','-c',
                f'from pathlib import Path; p=Path({str(counter)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x"); from answer import answer; assert answer(9)==9']}
            manager=AgentManager(root/'run/agents',lambda _:model,deadline=time.time()+30)
            flow=Investigation(manager,root/'run',completion_gate={'checks':[command]})
            original=manager.observation
            def lost(*args,**kwargs):
                original(*args,**kwargs)
                raise SystemExit('Lost acknowledgement after recovery control publication')
            try:
                with patch.object(manager,'observation',lost):
                    with self.assertRaises(SystemExit):flow.run('Repair answer.',repo)
                self.assertEqual(counter.read_text(),'x')
                result=Investigation(manager,root/'run').resume()
                self.assertEqual(result['state'],'completed',result)
                self.assertEqual(counter.read_text(),'xx')
                self.assertNotIn('handoff',result)
                self.assertEqual(model.counts[model.first],4)
                # One failed check and one new-candidate check: no old effect replay.
                audit_requests(Path(manager.descriptor(model.first)['root'])/'session.sqlite3')
            finally:manager.shutdown()

    def test_frozen_completion_policy_keeps_its_original_non_grade_handoff(self):
        from test_completion_gate import RepairModel
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=repository(root);model=RepairModel(self)
            manager=AgentManager(root/'run/agents',lambda _:model,deadline=time.time()+30)
            command={'name':'consumer','argv':[sys.executable,'-B','-c','from answer import answer; assert answer(9)==9']}
            try:
                result=Investigation(manager,root/'run',completion_gate={'version':1,'checks':[command]}).run('Repair answer.',repo)
                self.assertEqual(result['state'],'completed')
                self.assertNotIn('version',result['completion_gate'])
                self.assertEqual(len(result['handoff_history']),1)
                self.assertNotIn('review_takeover',manager.descriptor(result['agents']['main']))
                self.assertIn('return n+1',(repo/'answer.py').read_text())
            finally:manager.shutdown()

    def test_lost_grade_returns_uncertainty_to_same_owner_without_regrading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=repository(root);seen=[]
            def grade(candidate):
                seen.append(candidate['sha256']);raise ConnectionError('Lost grade reply after submission')
            def observe(request):
                self.assertIn('completion_needs_work',str(request['messages']))
                self.assertIn('unknown',str(request['messages']))
                return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':None,'refusal':'Unresolved external operation requires observation.'}}]}
            model=Model([call('exec_command',EDIT),finish(),observe])
            manager=AgentManager(root/'run/agents',lambda _:model,deadline=time.time()+30)
            try:
                result=Investigation(manager,root/'run',feedback_grader=grade,completion_gate=True).run('Repair answer.',repo)
                self.assertEqual(result['state'],'refused')
                self.assertEqual(len(seen),1)
                self.assertNotIn('handoff',result)
                self.assertEqual(len(manager.list_agents()['agents']),1)
                self.assertNotIn('candidate',result)
            finally:manager.shutdown()
