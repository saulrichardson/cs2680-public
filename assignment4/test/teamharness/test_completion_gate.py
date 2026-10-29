"""Completion verification, immutable candidates and new-conversation ownership."""
import importlib.util
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
from test_main_flow import repository, http_script, facts, call, finish, audit_requests, last_tool


GRADE = (sys.executable, '-B', '-c',
    'import json; from answer import answer; print(json.dumps({"tests_failed":sum(answer(n)!=n for n in [-2,0,3]),"tests_total":3}))')


class RepairModel:
    model = 'fixture'
    def __init__(self, case, *, empty=False, pregrade=False, diagnose=False, same_owner=False):
        self.case, self.empty, self.pregrade = case, empty, pregrade
        self.diagnose = diagnose
        self.same_owner = same_owner
        self.requests, self.errors, self.counts = [], [], {}
        self.first = None
        self.first_fresh_request = None
    def complete(self, request):
        self.requests.append(request)
        return self.reply(request)
    def reply(self, request):
        f = facts(request); ident = f['agent_id']
        if self.first is None:self.first = ident
        step = self.counts.get(ident, 0); self.counts[ident] = step + 1
        if ident == self.first:
            actions = ([] if self.empty else [call('exec_command', {'command':
                "printf 'def answer(n):\\n    return n+1\\n' > answer.py"})])
            if self.pregrade:
                actions += [call('evaluate_candidate', {}), call('exec_command', {'command':
                    "printf 'def answer(n):\\n    return n\\n' > answer.py"}), call('evaluate_candidate', {})]
            actions += [finish('OLD_PRIVATE_CONCLUSION')]
            if self.same_owner:
                actions += [call('exec_command', {'command': "python3 -B -c \"from pathlib import Path; p=Path('answer.py'); p.write_text(p.read_text().replace('return -1', 'return n').replace('return n+1', 'return n'))\""}), finish('Repaired after completion feedback.')]
            response = actions[step]
            response['choices'][0]['message']['reasoning_content'] = 'OLD_PRIVATE_REASONING'
            return response
        if step == 0:
            self.first_fresh_request = request
            self.case.assertIsNone(f['parent'])
            self.case.assertNotIn('OLD_PRIVATE_REASONING', str(request['messages']))
            self.case.assertNotIn('OLD_PRIVATE_CONCLUSION', str(request['messages']))
            d = json.loads((Path(f['archive_root'])/'agent.json').read_text())
            before = (Path(d['workspace'])/'answer.py').read_text()
            self.case.assertIn('return -1' if self.empty else 'return n+1', before)
            if self.diagnose:
                self.case.assertIn('NEW OWNER ASSIGNMENT (authored diagnosis-and-repair guidance)', str(request['messages']))
                self.case.assertIn('You are not a read-only', str(request['messages']))
                self.case.assertIn('Do not invent hidden assertions.', str(request['messages']))
                return call('exec_command', {'command': 'python3 -B -c "from answer import answer; assert answer(3)==3"'})
            return call('exec_command', {'command': "printf 'def answer(n):\\n    return n\\n' > answer.py"})
        if self.diagnose and step == 1:
            self.case.assertNotEqual(last_tool(request)['exit_code'], 0)
            return call('exec_command', {'command': "printf 'def answer(n):\\n    return n\\n' > answer.py"})
        return finish('Repaired the exact inherited candidate and checked its public contract.')


class CompletionGate(unittest.TestCase):
    def test_interrupted_handoff_preparation_resumes_exact_candidate(self):
        from src.teamharness import main
        for boundary in ('staged_copy', 'legacy_copy', 'published'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve(); repo = repository(root); model = RepairModel(self)
                manager = AgentManager(root / 'run/agents', lambda _: model, deadline=time.time() + 60)
                workflow = Investigation(manager, root / 'run', completion_gate=True)
                copies = []; copytree = main.shutil.copytree; rename = Path.rename
                def copy(source, destination, *args, **kwargs):
                    result = copytree(source, destination, *args, **kwargs)
                    destination = Path(destination)
                    if destination.parent.parent == root / 'run/takeover':
                        copies.append(destination)
                        if len(copies) == 1 and boundary != 'published':
                            if boundary == 'legacy_copy':
                                # Earlier runtimes copied directly into workspace.
                                destination.rename(destination.parent / 'workspace')
                            raise SystemExit('Interrupted after source copy, before patch application')
                    return result
                def publish(path, destination):
                    result = rename(path, destination)
                    if path.name.startswith('preparing-') and Path(destination).name == 'workspace':
                        raise SystemExit('Interrupted after atomic workspace publication')
                    return result
                try:
                    with patch.object(main.shutil, 'copytree', copy):
                        with patch.object(Path, 'rename', publish if boundary == 'published' else rename):
                            with self.assertRaises(SystemExit):
                                workflow.run('Repair answer.', repo, Grader(GRADE))
                        recorded = json.loads((root / 'run/workflow.json').read_text())
                        handoff = recorded['handoff']; candidate = handoff['candidate']
                        original_patch = Path(candidate['patch_path']).read_bytes()
                        folder = root / 'run/takeover' / handoff['completion_id']
                        self.assertEqual(handoff['state'], 'pending')
                        self.assertEqual((folder / 'workspace').exists(), boundary != 'staged_copy')
                        resumed = Investigation(manager, root / 'run', completion_gate=True)
                        result = resumed.resume()
                    self.assertEqual(result['state'], 'completed')
                    self.assertEqual(len(result['handoff_history']), 1)
                    self.assertEqual(len(result['evaluations']), 2)
                    self.assertEqual(len(copies), 1 if boundary == 'published' else 2)
                    self.assertEqual(Path(candidate['patch_path']).read_bytes(), original_patch)
                    self.assertEqual(result['handoff']['starting_candidate']['sha256'], candidate['sha256'])
                    if boundary != 'published':
                        preserved = list(folder.glob('incomplete-*' if boundary == 'legacy_copy' else 'preparing-*'))
                        self.assertEqual(len(preserved), 1)
                        self.assertIn('return -1', (preserved[0] / 'answer.py').read_text())
                    for owner in model.counts:
                        audit_requests(Path(manager.descriptor(owner)['root']) / 'session.sqlite3')
                finally:
                    manager.shutdown()

    def test_cached_failure_transfers_once_but_new_candidate_can_transfer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = repository(root); owners = []; counts = {}; requests = []
            class Model:
                model = 'fixture'
                def complete(self, request):
                    requests.append(request)
                    ident = facts(request)['agent_id']
                    if ident not in owners:
                        owners.append(ident)
                    step = counts.get(ident, 0); counts[ident] = step + 1
                    def edit(old, new):
                        return call('edit_file', {'path': 'answer.py', 'replacements': [{'old': old, 'new': new}]})
                    actions = [
                        [edit('return -1', 'return n+1'), call('evaluate_candidate', {}), finish()],
                        [finish(), edit('return n+1', 'return n+2'), finish()],
                        [edit('return n+2', 'return n'), finish()],
                    ]
                    return actions[owners.index(ident)][step]
            manager = AgentManager(root / 'run/agents', lambda _: Model(), deadline=time.time() + 60)
            try:
                result = Investigation(manager, root / 'run', completion_gate=True).run('Repair answer.', repo, Grader(GRADE))
                self.assertEqual(result['state'], 'completed')
                self.assertEqual([counts[owner] for owner in owners], [3, 3, 2])
                handoffs = result['handoff_history']
                self.assertEqual([(h['previous_main'], h['new_main']) for h in handoffs], list(zip(owners, owners[1:])))
                attempts = list(result['completion_attempts'].values())
                self.assertEqual([a['state'] for a in attempts], ['failed', 'failed', 'failed', 'passed'])
                self.assertEqual(attempts[0]['candidate']['sha256'], attempts[1]['candidate']['sha256'])
                self.assertNotEqual(attempts[1]['candidate']['sha256'], attempts[2]['candidate']['sha256'])
                self.assertTrue(attempts[0]['checks']['configured_grader']['result']['reused'])
                self.assertTrue(attempts[1]['checks']['configured_grader']['result']['reused'])
                self.assertEqual(len(result['evaluations']), 3)
                notice = attempts[1]['recovery_observation']
                self.assertEqual(notice['previous_grade_handoff']['new_main'], owners[1])
                self.assertIn('already transferred ownership', str(requests[4]['messages']))
                for owner in owners:
                    audit_requests(Path(manager.descriptor(owner)['root']) / 'session.sqlite3')
            finally:
                manager.shutdown()

    def test_same_owner_corrects_failed_command_and_keeps_human_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=repository(root);sub=repo/'sub';sub.mkdir()
            (sub/'value').write_text('base')
            counts={};first=[None];requests=[]
            class Model:
                model='fixture'
                def complete(self,request):
                    requests.append(request);f=facts(request);ident=f['agent_id']
                    first[0]=first[0] or ident;n=counts.get(ident,0);counts[ident]=n+1
                    if ident!=first[0] and n==0:
                        self_case.assertIn('Preserve the public value filename.',str(request['messages']))
                        self_case.assertNotIn('BIASED_WORKING_BRIEF',str(request['messages']))
                        d=json.loads((Path(f['archive_root'])/'agent.json').read_text())
                        self_case.assertEqual(Path(d['working_directory']).relative_to(d['workspace']),Path('sub'))
                        self_case.assertEqual((Path(d['working_directory'])/'value').read_text(),'first')
                    if n in (0,2):return call('exec_command',{'command':'printf '+('first' if n==0 else 'solved')+' > value'})
                    return finish()
            self_case=self;manager=AgentManager(root/'run/agents',lambda _:Model())
            class Workflow(Investigation):
                def advance(self,*args,**kwargs):
                    if not hasattr(self,'prepared'):
                        self.prepared=True;ident=self.report['agents']['main']
                        manager.send(ident,'Preserve the public value filename.')
                        manager._session(ident).set_task_brief('BIASED_WORKING_BRIEF',author='model:'+ident)
                    return super().advance(*args,**kwargs)
            check={'name':'value','argv':[sys.executable,'-B','-c',"from pathlib import Path; assert Path('sub/value').read_text()=='solved'"]}
            try:
                result=Workflow(manager,root/'run',completion_gate={'checks':[check]}).run('Repair value.',sub)
                self.assertEqual(result['state'],'completed')
                self.assertEqual((sub/'value').read_text(),'solved')
                self.assertNotIn('handoff_history',result)
                self.assertEqual(len(counts),1)
                self.assertIn('Preserve the public value filename.',str(requests[-1]))
                for d in (root/'run/agents/agents').glob('*/session.sqlite3'):audit_requests(d)
            finally:manager.shutdown()

    def test_invalid_candidate_cannot_be_accepted_or_silently_handed_off(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=repository(root);model=RepairModel(self)
            manager=AgentManager(root/'run/agents',lambda _:model)
            workflow=Investigation(manager,root/'run',completion_gate=True)
            original=workflow.verify_completion
            def corrupt(ident,returned):
                Path(returned['patch']['patch_path']).write_text('corrupted artifact')
                return original(ident,returned)
            try:
                with patch.object(workflow,'verify_completion',corrupt):
                    result=workflow.run('Repair answer.',repo)
                self.assertEqual(result['state'],'completion_blocked')
                self.assertNotIn('handoff',result)
                self.assertEqual((repo/'answer.py').read_text(),'def answer(n):\n    return n+1\n')
                self.assertNotIn('candidate',result)
            finally:manager.shutdown()

    def test_first_failed_completion_gets_new_http_conversation_and_exact_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); repo=repository(root); model=RepairModel(self,diagnose=True)
            deadline=time.time()+60
            with http_script(model) as env, patch.dict(os.environ,env):
                manager=AgentManager(root/'run/agents', HTTPProvider,deadline=deadline)
                workflow=Investigation(manager,root/'run',completion_gate=True)
                try:
                    result=workflow.run('Implement answer(n): return n unchanged.',repo,Grader(GRADE))
                    self.assertEqual(model.errors,[])
                    self.assertEqual(result['state'],'completed')
                    self.assertEqual(len(result['handoff_history']),1)
                    old,new=result['agents']['original_main'],result['agents']['main']
                    self.assertNotEqual(old,new)
                    self.assertEqual(model.counts,{old:2,new:3})
                    self.assertEqual((repo/'answer.py').read_text(),'def answer(n):\n    return n+1\n')
                    self.assertEqual((Path(manager.descriptor(new)['workspace'])/'answer.py').read_text(),'def answer(n):\n    return n\n')
                    history=result['handoff_history'][0]
                    self.assertEqual(history['starting_candidate']['sha256'],history['candidate']['sha256'])
                    attempts=list(result['completion_attempts'].values())
                    self.assertEqual([a['state'] for a in attempts],['failed','passed'])
                    self.assertEqual(attempts[0]['checks']['configured_grader']['result']['grade']['receipt']['tests_failed'],3)
                    self.assertEqual(attempts[1]['checks']['configured_grader']['result']['grade']['receipt']['tests_failed'],0)
                    sessions=[]
                    for ident in (old,new):
                        d=manager.descriptor(ident);requests=audit_requests(Path(d['root'])/'session.sqlite3')
                        sessions.append(json.loads((Path(d['root'])/'session.json').read_text())['session_id'])
                        self.assertEqual(facts(requests[0])['task_deadline_unix'],deadline)
                    self.assertNotEqual(*sessions)
                    self.assertEqual(workflow.settled_report()['state'],'completed')
                    worker_path=Path(__file__).resolve().parents[2]/'lab/adapters/teamharness/course_worker.py'
                    spec=importlib.util.spec_from_file_location('src.teamharness.course_worker',worker_path)
                    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
                    config={'state':str(root),'course_base':result['candidate']['base']}
                    selection,_=worker.choose_candidate(config,allow_live=False)
                    self.assertEqual(selection['source_agent_id'],new)
                    # Derived projections exercise recovery without changing original records.
                    projected=json.loads(json.dumps(result));projected['state']='interrupted'
                    projected.pop('candidate')
                    read=worker.read
                    with patch.object(worker,'read',lambda path,default=None: projected if Path(path).name=='workflow.json' else read(path,default)):
                        selection,_=worker.choose_candidate(config,allow_live=False)
                        self.assertEqual(selection['kind'],'main_returned_candidate')
                        projected['completion_attempts'][projected['latest_completion']]['state']='failed'
                        self.assertIsNone(worker.choose_candidate(config,allow_live=False))
                    # A late helper result stays evidence and cannot reopen the old owner.
                    manager.return_observation(old,{'event':'late finding'},'late-after-handoff')
                    self.assertEqual(manager.inspect(old)['status'],'completed')
                    self.assertEqual(model.counts[old],2)
                finally:manager.shutdown()

    def test_initial_dirty_source_is_not_a_solver_diff_and_custom_command_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=repository(root)
            initial=(repo/'answer.py').read_text()+'# Initial unique work\n';(repo/'answer.py').write_text(initial)
            model=RepairModel(self,empty=True,same_owner=True);manager=AgentManager(root/'run/agents',lambda _:model)
            check={'name':'public-contract','argv':[sys.executable,'-B','-c','from answer import answer; assert answer(9)==9']}
            workflow=Investigation(manager,root/'run',completion_gate={'checks':[check]})
            try:
                result=workflow.run('Repair answer; preserve the pre-existing source.',repo)
                attempts=list(result['completion_attempts'].values())
                self.assertFalse(attempts[0]['checks']['nonempty_solver_diff']['passed'])
                self.assertEqual(attempts[0]['candidate']['bytes'],0)
                self.assertNotIn('original_source_replay',attempts[0]['checks'])
                self.assertTrue(attempts[1]['checks']['command:public-contract']['passed'])
                self.assertFalse(attempts[1]['grading']['available'])
                self.assertEqual((repo/'answer.py').read_text(),initial.replace('return -1','return n'))
                self.assertNotIn('handoff_history',result)
                self.assertEqual(result['state'],'completed')
            finally:manager.shutdown()

    def test_interactive_failure_does_not_handoff_and_completion_reuses_grade(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=repository(root);model=RepairModel(self,pregrade=True)
            manager=AgentManager(root/'run/agents',lambda _:model)
            try:
                result=Investigation(manager,root/'run',completion_gate=True).run('Repair answer.',repo,Grader(GRADE))
                self.assertEqual(result['state'],'completed')
                self.assertNotIn('handoff',result)
                self.assertEqual(len(result['evaluations']),2)
                gate=result['completion_attempts'][result['latest_completion']]
                self.assertTrue(gate['checks']['configured_grader']['result']['reused'])
            finally:manager.shutdown()

    def test_interrupted_check_resumes_same_owner_without_replaying_hook(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=repository(root);model=RepairModel(self,same_owner=True)
            counter=root/'checks.txt'
            command={'name':'contract','argv':[sys.executable,'-B','-c',
                f'from pathlib import Path; p=Path({str(counter)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x"); from answer import answer; assert answer(2)==2']}
            config={'checks':[command]};manager=AgentManager(root/'run/agents',lambda _:model)
            workflow=Investigation(manager,root/'run',completion_gate=config)
            from src.teamharness import main
            original=main.grade_candidate
            def lost_receipt(*args,**kwargs):
                result=original(*args,**kwargs)
                raise SystemExit('Simulate controller loss after the actual command')
            try:
                with patch.object(main,'grade_candidate',lost_receipt):
                    with self.assertRaises(SystemExit):workflow.run('Repair answer.',repo)
                self.assertEqual(counter.read_text(),'x')
                resumed=Investigation(manager,root/'run',completion_gate=config)
                result=resumed.resume()
                self.assertEqual(result['state'],'completed')
                self.assertEqual(counter.read_text(),'xx')
                self.assertEqual(model.counts[model.first],4)
                self.assertNotIn('handoff_history',result)
            finally:manager.shutdown()
