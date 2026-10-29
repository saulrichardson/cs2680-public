"""Allocation properties and paused investigators through real files and HTTP."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from hypothesis import example, given, settings, strategies as st

from lab.adapters.teamharness.build import build
from src.teamharness.agents import AgentManager
from src.teamharness.allocation import select
from src.teamharness.main import Investigation
from src.teamharness.provider import HTTPProvider
from test_main_flow import repository, call, finish, http_script, audit_requests
from test_planning_boundary import Model, PLAN, EDIT
from test_session import answer


def record(ident, value, recommendation='skip', eligible=True):
    return {'task_id': ident, 'fingerprint': hashlib.sha256(str(ident).encode()).hexdigest(),
            'assessment': value, 'recommendation': recommendation, 'eligible': eligible}


ESTIMATE = st.one_of(st.none(), st.fixed_dictionaries({
    'readiness': st.sampled_from(['repair', 'experiment', 'search', 'blocked']),
    'effort': st.sampled_from(['small', 'medium', 'large'])}))


class SelectionProperties(unittest.TestCase):
    @settings(max_examples=150, deadline=None, derandomize=True)
    @given(values=st.lists(st.tuples(ESTIMATE, st.sampled_from(['continue', 'skip', None]), st.booleans()), max_size=250),
           count=st.integers(0, 300), seed=st.integers(0, 2**32-1))
    def test_partition_target_permutation_prefix_and_immutable_input(self, values, count, seed):
        rows = [record(i, *value) for i, value in enumerate(values)]
        original = deepcopy(rows)
        result = select(rows, count)
        chosen, reserve = result['selected'], result['reserve']
        self.assertEqual(len(chosen), min(count, sum(row['eligible'] for row in rows)))
        self.assertEqual({row['task_id'] for row in chosen + reserve}, set(range(len(rows))))
        self.assertEqual(len(chosen + reserve), len(rows))
        self.assertTrue(all(row['eligible'] for row in chosen))
        shuffled = list(rows); random.Random(seed).shuffle(shuffled)
        self.assertEqual(select(shuffled, count), result)
        self.assertEqual(select(rows, count+1)['selected'][:len(chosen)], chosen)
        self.assertEqual(rows, original)
        # Ineligible records cannot crowd out an eligible task.
        self.assertEqual(select([row for row in rows if row['eligible']], count)['selected'], chosen)

    @settings(max_examples=80, deadline=None)
    @given(size=st.integers(0, 1000), count=st.integers(0, 1200))
    def test_every_planner_can_decline_without_reducing_the_target(self, size, count):
        rows = [record(i, {'readiness': 'search', 'effort': 'large'}) for i in range(size)]
        result = select(rows, count)
        self.assertEqual(len(result['selected']), min(size, count))
        self.assertTrue(all(row['recommendation'] == 'skip' for row in result['selected']))

    def test_large_cohort_and_validation(self):
        rows = [record(i, {'readiness': 'experiment', 'effort': 'small'}) for i in range(10000)]
        result = select(reversed(rows), 1300)
        self.assertEqual(len(result['selected']), 1300)
        self.assertEqual(len(result['reserve']), 8700)
        for count in (-1, True, 1.2):
            with self.assertRaises(ValueError): select(rows, count)
        with self.assertRaises(ValueError): select([rows[0], rows[0]], 1)


class PausedInvestigation(unittest.TestCase):
    @settings(max_examples=9, deadline=None, derandomize=True)
    @example(choice='skip', limit=1, prefix=0, reopen=True)
    @example(choice='continue', limit=2, prefix=1, reopen=False)
    @example(choice='timeout', limit=1, prefix=0, reopen=True)
    @given(choice=st.sampled_from(['continue', 'skip', 'timeout']), limit=st.integers(1, 4),
           prefix=st.integers(0, 3), reopen=st.booleans())
    def test_pause_admit_preserves_session_decision_source_limits_and_full_requests(self, choice, limit, prefix, reopen):
        prefix = min(prefix, limit-1)
        estimate = {'readiness': 'experiment', 'effort': 'small'}
        actions = [answer('Inspecting the actual contract.')] * (limit if choice == 'timeout' else prefix)
        if choice != 'timeout':
            args = {'action': choice, 'assessment': estimate}
            args.update(PLAN if choice == 'continue' else {'reason': 'I found an uncertainty worth a focused experiment.'})
            actions.append(call('repair_plan', args))
        actions += [call('repair_plan', {'action': 'set', **PLAN}), call('exec_command', EDIT), finish()]
        model = Model(actions)
        with tempfile.TemporaryDirectory() as tmp, http_script(model) as env:
            from unittest.mock import patch
            import os
            with patch.dict(os.environ, env):
                root = Path(tmp); repo = repository(root)
                deadline = time.time() + 120
                manager = AgentManager(root/'run/agents', HTTPProvider, deadline=deadline)
                try:
                    flow = Investigation(manager, root/'run', completion_gate=True,
                        planning_boundary={'version':1,'max_turns': limit, 'pause_after_planning': True})
                    paused = flow.run('Repair answer(n).', repo)
                    self.assertEqual(paused['state'], 'planning_complete', paused)
                    ident = paused['agents']['main']; agent_root = Path(manager.descriptor(ident)['root'])
                    descriptor = (agent_root/'agent.json').read_bytes()
                    decision_path = agent_root/'planning-decision.json'
                    decision = decision_path.read_bytes() if decision_path.exists() else None
                    used = limit if choice == 'timeout' else prefix+1
                    self.assertEqual(model.step, used)
                    self.assertEqual(paused['main_return']['planning']['turns_used'], used)
                    self.assertEqual(paused['main_return']['source_capture']['reason'], 'planning_pause')
                    self.assertNotIn('candidate', paused)
                    self.assertFalse(paused['completion_attempts'])
                    self.assertEqual((repo/'answer.py').read_text(), 'def answer(n):\n    return -1\n')
                    # Neither explicit ordinary reopening nor automatic helper evidence admits work.
                    flow.resume()
                    manager.return_observation(ident, {'event': 'late_helper', 'author': 'fixture helper'}, 'late-helper')
                    self.assertEqual(manager.wait(ident)['status'], 'planning_complete')
                    self.assertEqual(model.step, used)
                    if reopen:
                        manager.shutdown()
                        manager = AgentManager(root/'run/agents', HTTPProvider, deadline=deadline)
                        flow = Investigation(manager, root/'run')
                    completed = flow.admit('Selected for a bounded public-workload experiment.')
                    self.assertEqual(completed['state'], 'completed', completed)
                    self.assertEqual(completed['agents']['main'], ident)
                    self.assertEqual(completed['main_return']['session_id'], paused['main_return']['session_id'])
                    self.assertEqual(completed['main_return']['planning']['turns_used'], used)
                    self.assertEqual(completed['task_deadline_unix'], deadline)
                    self.assertEqual((agent_root/'agent.json').read_bytes(), descriptor)
                    self.assertEqual(decision_path.read_bytes() if decision_path.exists() else None, decision)
                    self.assertEqual((repo/'answer.py').read_text(), 'def answer(n):\n    return n\n')
                    before = model.step
                    self.assertEqual(flow.admit('Selected for a bounded public-workload experiment.')['state'], 'completed')
                    self.assertEqual(model.step, before)
                    with self.assertRaises(ValueError): flow.admit('A conflicting allocation')
                    requests = audit_requests(agent_root/'session.sqlite3')
                    self.assertEqual(len(requests), len(model.requests))
                    self.assertIn('planning_admitted', str(requests[-1]['messages']))
                    self.assertFalse(model.errors)
                finally:
                    manager.shutdown()

    def test_expired_paused_investigation_does_not_get_a_new_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=repository(root)
            model=Model([call('repair_plan', {'action':'skip', 'reason':'Uncertain path.',
                'assessment':{'readiness':'search','effort':'large'}})])
            manager=AgentManager(root/'run/agents',lambda _:model,deadline=time.time()+120)
            try:
                flow=Investigation(manager,root/'run',planning_boundary={'version':1,'pause_after_planning':True})
                flow.run('Repair answer.',repo)
                from unittest.mock import patch
                with patch('src.teamharness.main.time.time',return_value=manager.deadline+1):
                    with self.assertRaisesRegex(RuntimeError,'deadline'): flow.admit('Selected')
                self.assertEqual(model.step,1)
                self.assertFalse(list((root/'run').rglob('planning-admission.json')))
            finally: manager.shutdown()


    def test_lost_admission_publication_is_observed_without_restarting_the_investigation(self):
        from src.teamharness import planning
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=repository(root)
            model=Model([call('repair_plan', {'action':'skip','reason':'Uncertain path.',
                'assessment':{'readiness':'experiment','effort':'small'}}),
                call('exec_command',EDIT),finish()])
            manager=AgentManager(root/'run/agents',lambda _:model)
            try:
                flow=Investigation(manager,root/'run',planning_boundary={'version':1,'pause_after_planning':True})
                paused=flow.run('Repair answer.',repo)
                save=planning.save
                def lost_reply(path,value):
                    save(path,value)
                    if path.name=='planning-admission.json': raise OSError('Lost reply after durable admission')
                with patch.object(planning,'save',lost_reply):
                    with self.assertRaises(OSError): flow.admit('Selected')
                self.assertEqual(model.step,1)
                agent_root=Path(manager.descriptor(paused['agents']['main'])['root'])
                original=(agent_root/'planning-admission.json').read_bytes()
                completed=flow.admit('Selected')
                self.assertEqual(completed['state'],'completed')
                self.assertEqual(completed['agents']['main'],paused['agents']['main'])
                self.assertEqual((agent_root/'planning-admission.json').read_bytes(),original)
                self.assertEqual(model.step,3)
                audit_requests(agent_root/'session.sqlite3')
            finally: manager.shutdown()


class CourseControllerProperties(unittest.TestCase):
    @settings(max_examples=25, deadline=None, derandomize=True)
    @given(size=st.integers(0, 75), target=st.integers(0, 85),
           planners=st.integers(1, 6),
           seed=st.integers(0, 2**32-1), fail=st.booleans())
    def test_planning_pool_and_all_selected_admission_never_replay_uncertain_work(self, size, target, planners, seed, fail):
        from src import bridge
        # The builder installs this shared module alongside the course adapter.
        with patch.dict(sys.modules, {'lab.adapters.teamharness.bridge': bridge}):
            from lab.adapters.teamharness import agentic_loop as adapter
        course=types.ModuleType('dispatcher')
        class DispatcherShutdown(Exception): pass
        course.DispatcherShutdown=DispatcherShutdown
        lock=threading.Lock(); active={'run':0,'admit':0}; peak=dict(active)
        effects=[]; finalized={}; closed=[]
        rows=[{'task':i,'problem_statement':f'Synthetic public task {i}'} for i in range(size)]
        random.Random(seed).shuffle(rows)
        class Dispatcher:
            calls=0
            def next_task(self):
                self.calls+=1
                return rows.pop() if rows else None
        class Attempt:
            def __init__(self, owner, task, config):
                self.task=task; self.ident=task['task']; self.state='state-'+str(self.ident)
                self.deadline=time.time()+120; self.remote=self
                self.archive=Path('task-'+str(self.ident)); self.archive.mkdir()
            def prepare(self): return True
            def execute(self, operation):
                with lock:
                    effects.append((self.ident,operation))
                    active[operation]+=1; peak[operation]=max(peak[operation],active[operation])
                try:
                    time.sleep(.0001)
                    if fail and operation=='admit' and self.ident%7==0:
                        raise OSError('Reply lost after dispatch; operation may have executed')
                    return {'state':'exited','exit_code':0}
                finally:
                    with lock: active[operation]-=1
            def read_json(self, path):
                return {'state':'planning_complete','deadline':self.deadline,
                    'planning':{'decision':{'decision':'skip',
                        'assessment':{'readiness':'experiment','effort':'small'}}}}
            def finish(self, *, discard=False):
                with lock:
                    if self.ident in finalized: raise AssertionError('Duplicate course finalization')
                    finalized[self.ident]=discard
            def close(self): closed.append(self.ident)
        config={'workflow':'main','planning_boundary':{'pause_after_planning':True},
                'allocation':{'count':target,'planning_workers':planners}}
        with tempfile.TemporaryDirectory() as tmp:
            before=Path.cwd()
            try:
                import os
                os.chdir(tmp)
                dispatcher=Dispatcher(); agent=adapter.Agent(dispatcher)
                with patch.dict(sys.modules,{'dispatcher':course}),patch.object(adapter,'TaskAttempt',Attempt):
                    agent.allocate(config)
                    calls=dispatcher.calls
                    with self.assertRaises(FileExistsError): agent.allocate(config)
                    self.assertEqual(dispatcher.calls,calls)
                selected=json.loads(Path('madsServe_logs/allocation/selection.json').read_text())['selected']
                result=json.loads(Path('madsServe_logs/allocation/result.json').read_text())
            finally: os.chdir(before)
        chosen={row['task_id'] for row in selected}
        self.assertEqual(len(chosen),min(size,target))
        self.assertEqual(sorted(i for i,op in effects if op=='run'),list(range(size)))
        self.assertEqual(sorted(i for i,op in effects if op=='admit'),sorted(chosen))
        self.assertLessEqual(peak['run'],planners)
        self.assertLessEqual(peak['admit'],len(chosen))
        self.assertEqual(sorted(closed),list(range(size)))
        uncertain={i for i in chosen if fail and i%7==0}
        self.assertEqual({row['task_id'] for row in result['failures']},uncertain)
        self.assertEqual(set(finalized),set(range(size))-uncertain)
        self.assertTrue(all(discard==(i not in chosen) for i,discard in finalized.items()))

    def test_every_selected_owner_is_admitted_before_another_can_spend_its_deadline(self):
        from src import bridge
        with patch.dict(sys.modules, {'lab.adapters.teamharness.bridge': bridge}):
            from lab.adapters.teamharness import agentic_loop as adapter
        course=types.ModuleType('dispatcher')
        class DispatcherShutdown(Exception): pass
        course.DispatcherShutdown=DispatcherShutdown
        clock=[1000.0]; admitted=[]; finalized={}
        # The first wave cannot finish before all 13 owners have entered. A
        # reintroduced five-owner pool breaks this barrier and expires the rest.
        barrier=threading.Barrier(13, action=lambda:clock.__setitem__(0,4601.0))
        class Dispatcher:
            next=0
            def next_task(self):
                if self.next==26:return None
                ident=self.next; self.next+=1
                return {'task':ident,'problem_statement':f'Independent fixture {ident}'}
        class Attempt:
            def __init__(self,owner,task,config):
                self.task=task; self.ident=task['task']; self.remote=self; self.state=str(self.ident)
                self.archive=Path('tasks')/str(self.ident); self.archive.mkdir(parents=True)
            def prepare(self):self.deadline=clock[0]+3600;return True
            def execute(self,operation):
                if operation=='admit':
                    admitted.append(self.ident)
                    try:barrier.wait(timeout=3)
                    except threading.BrokenBarrierError:
                        clock[0]=4601.0
                        raise AssertionError('Selected owner waited behind another implementation')
                return {'state':'exited','exit_code':0}
            def read_json(self,path):
                return {'state':'planning_complete','deadline':self.deadline,'planning':{'decision':{
                    'decision':'continue','assessment':{'readiness':'repair','effort':'small'}}}}
            def finish(self,discard=False):finalized[self.ident]=discard
            def close(self):pass
        config={'workflow':'main','planning_boundary':{'pause_after_planning':True},
                'allocation':{'skip_count':13,'planning_workers':3}}
        with tempfile.TemporaryDirectory() as tmp:
            before=Path.cwd()
            try:
                import os
                os.chdir(tmp)
                with patch.dict(sys.modules,{'dispatcher':course}),patch.object(adapter,'TaskAttempt',Attempt),patch.object(adapter,'time',types.SimpleNamespace(time=lambda:clock[0])):
                    adapter.Agent(Dispatcher()).allocate(config)
                selection=json.loads(Path('madsServe_logs/allocation/selection.json').read_text())
                result=json.loads(Path('madsServe_logs/allocation/result.json').read_text())
            finally:os.chdir(before)
        selected={row['task_id'] for row in selection['selected']}
        self.assertEqual(len(selected),13)
        self.assertEqual(set(admitted),selected)
        self.assertFalse(result['failures'])
        self.assertEqual({i for i,discard in finalized.items() if not discard},selected)
        self.assertEqual(clock[0],4601.0)


class AllocationPackage(unittest.TestCase):
    def test_configuration_is_opt_in_and_invalid_input_has_no_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            build(root/'legacy')
            legacy=json.loads((root/'legacy/src/campaign.json').read_text())
            self.assertNotIn('allocation', legacy)
            self.assertEqual(legacy['planning_boundary'], {'enabled':True,'max_turns':15,'version':2})
            build(root/'allocated', select_tasks=13)
            value=json.loads((root/'allocated/src/campaign.json').read_text())
            self.assertEqual(value['allocation'], {'count':13,'planning_workers':3})
            self.assertTrue(value['planning_boundary']['pause_after_planning'])
            for target in (-1, True):
                with self.assertRaises(ValueError): build(root/'invalid',select_tasks=target)
                self.assertFalse((root/'invalid').exists())

    def test_packaged_multi_task_course_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            build(root/'package', select_tasks=2, planning_workers=2)
            command=[sys.executable, '-B', 'lab/adapters/teamharness/check_allocation.py',
                     '--package', str(root/'package'), '--output', str(root/'evidence')]
            result=subprocess.run(command, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
            receipt=json.loads((root/'evidence/CHECK.json').read_text())
            self.assertTrue(receipt['passed'])
            self.assertEqual(receipt['selected'],[0,1])


if __name__ == '__main__':
    unittest.main()
