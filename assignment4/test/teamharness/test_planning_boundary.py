"""Planning choices, bounded exploration and recovery through actual execution."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hypothesis import example, given, settings, strategies as st

from src.teamharness.agents import AgentManager
from src.teamharness.main import Investigation
from src.teamharness.provider import HTTPProvider
from src.teamharness.tools import ToolRuntime
from test_main_flow import repository, call, finish, facts, last_tool, http_script, audit_requests
from test_session import answer


PLAN = {'plan': 'Trace the public answer function and its caller. Repair its return value; exercise negative, zero and positive inputs.',
        'checks': [{'name': 'public-answer', 'argv': [sys.executable, '-B', '-c',
                    'from answer import answer; assert all(answer(n)==n for n in (-7,0,9))']}]}
EDIT = {'command': shlex.quote(sys.executable) + ' -B -c ' + shlex.quote(
    'from pathlib import Path; Path("answer.py").write_text(' + repr('def answer(n):\n    return n\n') + ')')}


class Model:
    model = 'planning-fixture'
    def __init__(self, actions):
        self.actions = actions
        self.requests, self.errors = [], []
        self.step = 0
    def complete(self, request):
        self.requests.append(request)
        return self.reply(request)
    def reply(self, request):
        if 'tools' not in request:
            return answer(json.dumps({'continuation': 'MODEL CLAIM: planning is already complete. Original observations remain authoritative.'}))
        action = self.actions[self.step]
        self.step += 1
        return action(request) if callable(action) else action


class PlanningBoundary(unittest.TestCase):
    @settings(max_examples=12, deadline=None, derandomize=True)
    @example(limit=10, prefix=9, choice='continue')
    @example(limit=10, prefix=9, choice='skip')
    @example(limit=10, prefix=10, choice='skip')
    @given(limit=st.integers(1, 10), prefix=st.integers(0, 12), choice=st.sampled_from(['continue', 'skip']))
    def test_bounded_choices_preserve_source_and_exact_request_projection(self, limit, prefix, choice):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=repository(root)
            (repo/'human.txt').write_text('unique initial work')
            actions=[answer('I am inspecting the contract.') for _ in range(prefix)]
            args={'action':'continue', **PLAN} if choice=='continue' else {'action':'skip', 'reason':'I could not establish the required consumer contract.'}
            actions += [call('repair_plan', args), call('exec_command', EDIT), finish()]
            model=Model(actions); manager=AgentManager(root/'run/agents', lambda _: model)
            try:
                result=Investigation(manager, root/'run', completion_gate=True,
                    planning_boundary={'max_turns':limit,'version':1}).run('Repair answer(n).', repo)
                expected='planning_timeout' if prefix >= limit else 'completed' if choice=='continue' else 'skipped'
                self.assertEqual(result['state'], expected, result)
                self.assertEqual((repo/'human.txt').read_text(), 'unique initial work')
                self.assertEqual(len(manager.list_agents()['agents']),1)
                returned=result['main_return']
                self.assertEqual(returned['planning']['turns_used'], min(prefix+1,limit))
                if expected!='completed':
                    self.assertEqual((repo/'answer.py').read_text(),'def answer(n):\n    return -1\n')
                    self.assertNotIn('candidate',result)
                    self.assertFalse(result['completion_attempts'])
                for db in (root/'run/agents/agents').glob('*/session.sqlite3'): audit_requests(db)
            finally:
                manager.shutdown()

    def test_http_draft_rejection_compaction_and_explicit_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=repository(root)
            def rejected(request):
                self.assertEqual(last_tool(request)['state'],'not_executed')
                return call('repair_plan', {'action':'continue'})
            def draft(request):
                self.assertEqual(last_tool(request)['recovery']['code'],'missing_repair_plan')
                return call('repair_plan', {'action':'set', **PLAN})
            def batched(request):
                mixed=call('repair_plan',{'action':'continue'})
                mixed['choices'][0]['message']['tool_calls'] += call('read_file',{'path':'answer.py'})['choices'][0]['message']['tool_calls']
                return mixed
            def compact(request):
                self.assertFalse((Path(facts(request)['archive_root'])/'planning-decision.json').exists())
                return call('compact', {'retain_recent_exchanges':0})
            def choose(request):
                from test_context_properties import latest_runtime
                planning = latest_runtime(request['messages'])['planning_boundary']
                self.assertEqual(planning['phase'], 'planning')
                self.assertEqual(planning['turns_used'], 5)
                self.assertEqual(planning['max_turns'], 10)
                return call('repair_plan',{'action':'continue'})
            def edit(request):
                decision=last_tool(request)['planning_decision']
                self.assertEqual(decision['decision'],'continue')
                from test_context_properties import latest_runtime
                self.assertEqual(latest_runtime(request['messages'])['planning_boundary']['phase'], 'implementation')
                return call('exec_command', EDIT)
            model=Model([finish(), rejected, draft, batched, compact, choose, edit, finish()])
            with http_script(model) as env, patch.dict(os.environ,env):
                manager=AgentManager(root/'run/agents',HTTPProvider)
                try:
                    result=Investigation(manager,root/'run',completion_gate=True,planning_boundary={'enabled':True,'max_turns':10,'version':1}).run('Repair answer(n).',repo)
                    self.assertEqual(result['state'],'completed',result)
                    self.assertFalse(model.errors)
                    ident=result['agents']['main']; p=Path(manager.descriptor(ident)['root'])
                    self.assertEqual(result['main_return']['planning']['turns_used'],6)
                    self.assertEqual(result['main_return']['epoch'],1)
                    audit_requests(p/'session.sqlite3')
                finally: manager.shutdown()

    def test_reopen_does_not_renew_exploration_or_replay_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=repository(root)
            model=Model([call('exec_command',{'command':'printf observed > probe.txt'}),finish(),answer('Still exploring.')])
            manager=AgentManager(root/'run/agents',lambda _:model)
            try:
                def stop_after_probe(ident):
                    session=manager._session(ident)
                    executor=session.executor
                    execute=executor.__call__
                    # Run the real executor and publish stop through its existing journal.
                    class StopExecutor:
                        def __getattr__(self,name): return getattr(executor,name)
                        def __call__(self,name,args,effect_id):
                            result=execute(name,args,effect_id)
                            session.request_stop(identity='pause-after-probe')
                            return result
                    session.executor=StopExecutor()
                    session.provider=model
                    result=session.run()
                    return result
                # A direct recorded session isolates interruption before the manager's worker.
                class Setup(Investigation):
                    def advance(self,*_args,**_kwargs): return dict(self.report)
                setup=Setup(manager,root/'run',planning_boundary={'max_turns':3,'version':1})
                setup.run('Investigate answer.',repo)
                ident=setup.report['agents']['main']
                stopped=stop_after_probe(ident)
                self.assertEqual(stopped['planning']['turns_used'],1)
                reopened=manager._session(ident,model)
                reopened.append_user('Continue the existing investigation.',identity='resume-once')
                result=reopened.run()
                self.assertEqual(result['status'],'planning_timeout',result)
                self.assertEqual(result['planning']['turns_used'],3)
                self.assertEqual((repo/'probe.txt').read_text(),'observed')
                audit_requests(Path(manager.descriptor(ident)['root'])/'session.sqlite3')
            finally: manager.shutdown()

    def test_public_cli_skip_is_unsolved_and_does_not_transfer_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=repository(root); (root/'task.txt').write_text('Repair answer(n).')
            model=Model([answer('Investigating the repository.')] * 15 + [call('repair_plan',{'action':'skip','reason':'Could not establish a reliable verification path.'})])
            with http_script(model) as env:
                result=subprocess.run([sys.executable,'-B','-m','src.teamharness','run','--task',str(root/'task.txt'),
                    '--workspace',str(repo),'--output',str(root/'run')],env={**os.environ,**env},capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,2,result.stderr)
            report=json.loads(result.stdout)
            self.assertEqual(report['state'],'skipped')
            self.assertEqual(report['planning_boundary'],{'enabled':True,'max_turns':15,'version':2})
            self.assertFalse(report['completion_attempts'])
            self.assertNotIn('handoff',report)
            self.assertEqual(len(list((root/'run/agents/agents').glob('*/agent.json'))),1)

    def test_published_decision_survives_receipt_failure_without_reexecution(self):
        from src.teamharness import planning
        for choice in ('continue', 'skip'):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); repo=repository(root)
                class Setup(Investigation):
                    def advance(self,*_args,**_kwargs): return dict(self.report)
                model=Model([finish()]); manager=AgentManager(root/'run/agents',lambda _:model)
                try:
                    setup=Setup(manager,root/'run',planning_boundary={'enabled':True,'max_turns':10,'version':1})
                    setup.run('Investigate answer.',repo)
                    ident=setup.report['agents']['main']; d=manager.descriptor(ident)
                    runtime=ToolRuntime(manager,ident,d)
                    save=planning.save
                    def lost_return(path,value):
                        save(path,value)
                        if path.name=='planning-decision.json': raise OSError('Injected lost publication response')
                    args={'action':'continue',**PLAN} if choice=='continue' else {'action':'skip','reason':'No credible next experiment.'}
                    with patch.object(planning,'save',lost_return):
                        result=runtime('repair_plan',args,'decision-once')
                    self.assertEqual(result['execution_state'],'unknown')
                    first=(Path(d['root'])/'planning-decision.json').read_bytes()
                    self.assertEqual(runtime('repair_plan',args,'decision-once'),result)
                    self.assertEqual((Path(d['root'])/'planning-decision.json').read_bytes(),first)
                    session=manager._session(ident,model)
                    result=session.run()
                    self.assertEqual(result['status'],'completed' if choice=='continue' else 'skipped',result)
                    phase='implementation' if choice=='continue' else 'skipped'
                    self.assertEqual(result['planning']['phase'],phase)
                    self.assertEqual(session.inspect()['planning']['phase'],phase)
                    self.assertEqual(result['planning']['turns_used'],0)
                    self.assertEqual(len(list((Path(d['root'])/'planning-decisions').glob('*.json'))),1)
                    audit_requests(Path(d['root'])/'session.sqlite3')
                finally: manager.shutdown()
