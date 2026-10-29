"""Generated contracts, shrinking state sequences and real persisted effects.

The reference assertions describe observable safety, not implementation branches.
Use REPAIR_PROPERTY_PROFILE=extended for more timelines; REPAIR_PROPERTY_SEED replays one.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from hypothesis import given, settings, strategies as st, event, note, example, seed
from hypothesis.database import DirectoryBasedExampleDatabase
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from src.teamharness import repair
from src.teamharness.agents import AgentManager
from src.teamharness.main import Investigation
from src.teamharness.prompts import freeze_guides
from src.teamharness.tools import ToolRuntime
from test_main_flow import repository, finish

DB = DirectoryBasedExampleDatabase(os.environ.get('REPAIR_PROPERTY_DATABASE', '.local/hypothesis/repair'))
# Importing this module must not change generation budgets in unrelated suites.
PROPERTY_SETTINGS = settings(deadline=None, database=DB)
SEQUENCE_BUDGETS = {'fast': (8, 10), 'extended': (100, 40)}
EXAMPLES, STEPS = SEQUENCE_BUDGETS[os.environ.get('REPAIR_PROPERTY_PROFILE', 'fast')]
CASES = st.dictionaries(st.text(alphabet='abcXYZ012_-', min_size=1, max_size=12),
                       st.sampled_from(['pass', 'fail', 'skip']), max_size=20)


@st.composite
def related_cases(draw):
    base=draw(CASES)
    if draw(st.integers(0,3)) == 0:
        return base, draw(CASES)
    candidate=dict(base)
    for action in draw(st.lists(st.sampled_from(['fix','regress','drop','skip','add']), min_size=0,max_size=5)):
        if candidate and action!='add':
            key=draw(st.sampled_from(sorted(candidate)))
            if action=='drop': candidate.pop(key)
            else: candidate[key]={'fix':'pass','regress':'fail','skip':'skip'}[action]
        else:
            candidate['new-'+str(len(candidate))]=draw(st.sampled_from(['pass','fail','skip']))
    return base,candidate


class RepairLaws(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def receipt(self, name, values, *, exit_code=0, state='exited', unchanged=True):
        path = self.root / name
        path.write_text(json.dumps({'cases': values}))
        return dict(state=state, source_unchanged=unchanged, stdout_path=str(path), exit_code=exit_code)

    @settings(PROPERTY_SETTINGS, max_examples=250)
    @given(pair=related_cases())
    @example(pair=({'a':'fail'}, {'b':'fail'}))
    @example(pair=({'a':'pass'}, {'a':'skip'}))
    @example(pair=({'a':'fail'}, {}))
    @example(pair=({'a':'fail'}, {'a':'pass'}))
    @example(pair=({'a':'pass'}, {'a':'fail'}))
    @example(pair=({'a':'pass'}, {'b':'pass'}))
    @example(pair=({'a':'pass'}, {'a':'pass','new':'skip'}))
    def test_case_identity_contract(self, pair):
        base,candidate=pair
        # Independent oracle: executed obligations cannot disappear or worsen.
        valid = lambda values: any(v != 'skip' for v in values.values())
        expected = (valid(base) and valid(candidate)
                    and all(candidate.get(k) in ('pass', 'fail') for k, v in base.items() if v != 'skip')
                    and all(v != 'fail' or base.get(k) == 'fail' for k, v in candidate.items())
                    and all(v != 'skip' or base.get(k) == 'skip' for k, v in candidate.items()))
        observed = repair.assess({'result_format':'cases_json', 'expectation':'no_new_failures'},
                                self.receipt('candidate', candidate), self.receipt('baseline', base))
        event('accepted' if observed['passed'] else 'rejected')
        self.assertEqual(observed['passed'], expected)
        strict = repair.assess({'result_format':'cases_json'}, self.receipt('candidate', candidate))
        self.assertEqual(strict['passed'], bool(candidate) and all(v == 'pass' for v in candidate.values()))

    @settings(PROPERTY_SETTINGS, max_examples=100)
    @given(values=CASES, state=st.sampled_from(['running','unknown','launch_error','deadline_exceeded']),
           unchanged=st.booleans())
    def test_incomplete_effect_never_becomes_test_success(self, values, state, unchanged):
        receipt = self.receipt('candidate', values, state=state, unchanged=unchanged)
        for check in ({}, {'result_format':'cases_json'},
                      {'result_format':'cases_json','expectation':'no_new_failures'}):
            self.assertFalse(repair.assess(check, receipt, self.receipt('baseline', values))['passed'])

    @settings(PROPERTY_SETTINGS, max_examples=60)
    @given(identity=st.text(alphabet='abc012', min_size=1, max_size=10), first=st.sampled_from(['pass','fail','skip']))
    def test_ambiguous_duplicate_case_identity_is_not_evidence(self, identity, first):
        receipt = self.receipt('candidate', {})
        Path(receipt['stdout_path']).write_text('{"cases":{' + json.dumps(identity) + ':' + json.dumps(first)
                                              + ',' + json.dumps(identity) + ':"pass"}}')
        self.assertFalse(repair.assess({'result_format':'cases_json'}, receipt)['passed'])

    @settings(PROPERTY_SETTINGS, max_examples=40)
    @given(advice=st.text(alphabet='abcXYZ αβ012\n', min_size=1, max_size=100),
           noise=st.text(alphabet='lmnop987\n', min_size=1, max_size=100))
    def test_selective_delivery_and_frozen_source_are_independent_of_later_edits(self, advice, noise):
        # A new example uses an independent snapshot; no run rewrites old guidance.
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            root=Path(temp); source=root/'maintained'; source.mkdir(); frozen=root/'frozen'
            (source/'chapter.md').write_text('# Chapter\n## Selected\n' + advice + '\n## Other\nEXCLUDED:' + noise)
            with patch('src.teamharness.prompts.GUIDES', source):
                incomplete=root/'incomplete'; incomplete.mkdir()
                with self.assertRaises(FileNotFoundError): freeze_guides(incomplete)
                hashes=freeze_guides(frozen)
                original=(frozen/'chapter.md').read_bytes()
                selected=repair.selected_guidance(frozen,[{'path':'chapter.md','heading':'Selected'}])[0]
                self.assertIn(advice.strip('\n'), selected['text'])
                self.assertNotIn('EXCLUDED:', selected['text'])
                outside=root/'outside.md'; outside.write_text('Outside the frozen library')
                (frozen/'escape.md').symlink_to(outside)
                for bad_path in ('../outside.md', 'escape.md', 'chapter.md#Selected'):
                    with self.assertRaises(ValueError):
                        repair.selected_guidance(frozen,[{'path':bad_path}])
                (frozen/'escape.md').unlink()
                self.assertEqual(selected['source_sha256'], hashlib.sha256(original).hexdigest())
                (source/'chapter.md').write_text('Later maintained source')
                (source/'new.md').write_text('New material')
                self.assertEqual(freeze_guides(frozen), hashes)
                self.assertEqual((frozen/'chapter.md').read_bytes(), original)
                self.assertFalse((frozen/'new.md').exists())
                # Corruption is detected, not repaired silently from today's source.
                (frozen/'chapter.md').write_bytes(original+b'corruption')
                with self.assertRaises(ValueError): freeze_guides(frozen)
                self.assertEqual((frozen/'chapter.md').read_bytes(), original+b'corruption')


class ReturnProvider:
    model='property-reviewer'
    def complete(self, request):
        return finish('Independent report delivered; this opinion is not a test verdict.')


class PausedInvestigation(Investigation):
    def advance(self, returned=None):
        # Only pause the external model. Source setup, tool effects, private
        # reviewers, SQLite sessions, completion and restart are production code.
        return self.report


class RepairMachine(RuleBasedStateMachine):
    def __init__(self, tracing=True):
        super().__init__()
        self.tracing=tracing
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        repo=repository(self.root)
        self.manager=AgentManager(self.root/'run/agents', lambda _:ReturnProvider())
        self.manager.automatic_wakes_enabled=False
        self.grades=[]
        self.workflow=PausedInvestigation(self.manager,self.root/'run',completion_gate=True, feedback_grader=self.grade)
        self.workflow.run('Implement answer(n) returning n.',repo)
        self.ident=self.workflow.report['agents']['main']
        self.descriptor=self.manager.descriptor(self.ident)
        self.live=Path(self.descriptor['workspace']); self.agent=Path(self.descriptor['root'])
        self.serial=0; self.history=[]; self.executed=0; self.archives={}
        self.plan=None; self.last_effect=None
        self.marker=self.root/'executions'
        self.value=None; self.reviews=set(); self.known={}; self.pending=set()
        self.register_plan()

    def invoke(self, name, args, effect=None):
        self.serial+=1; effect=effect or f'property-{self.serial}'
        result=ToolRuntime(self.manager,self.ident,self.descriptor)(name,args,effect)
        self.history.append((name,args,result)); self.last_effect=(name,args,effect,result)
        if self.tracing: note((name,args,result.get('state',result.get('execution_state'))))
        return result

    def register_plan(self):
        command=('import json; from pathlib import Path; from answer import answer; '
                 f'p=Path({str(self.marker)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x"); '
                 'ok=all(answer(n)==n for n in [-2,0,9]); '
                 'print(json.dumps({"cases":{"public-contract":"pass" if ok else "fail"}})); '
                 'raise SystemExit(0 if ok else 1)')
        result=self.invoke('repair_plan',{'action':'set','plan':'Follow the real consumer; challenge the first theory.',
            'checks':[{'name':'public-contract','argv':[sys.executable,'-B','-c',command], 'result_format':'cases_json'}],
            'guidance':[{'path':'contracts.md'}]})
        assert result.get('registered'), result
        self.plan=result['plan_id']
        path=Path(result['plan_path']); self.archives[path]=path.read_bytes()

    def grade(self, candidate):
        self.grades.append(candidate['sha256'])
        value=self.value
        return {'candidate_sha256':candidate['sha256'], 'receipt':{'tests_failed':0 if value==0 else 1,'tests_total':1}, 'passed':value==0}

    @rule()
    def interactive_grade(self):
        owner=self.workflow.report['agents']['main']
        result=self.invoke('evaluate_candidate',{})
        assert result['grade_facts']['candidate_sha256']==result['candidate']['sha256']
        assert len(self.grades)==len(set(self.grades))
        assert self.workflow.report['agents']['main']==owner
        assert not self.workflow.report.get('handoff')

    def count(self): return len(self.marker.read_text()) if self.marker.exists() else 0

    @rule()
    def revise_plan(self): self.register_plan()

    @rule(offset=st.integers(-2,2))
    def edit(self, offset):
        self.value=offset
        (self.live/'answer.py').write_text(f'def answer(n):\n    return n + ({offset})\n')

    @rule()
    def execute_checks(self):
        result=self.invoke('repair_plan',{'action':'run','target':'candidate'})
        assert result.get('state') == 'finished', result
        self.known[(self.plan,result['candidate']['sha256'])]=result['passed']
        self.executed+=1
        assert result['passed'] is (self.value == 0)

    @rule(after_effect=st.booleans())
    def interrupt_check(self, after_effect):
        original=repair.grade_candidate
        def interrupted(*args,**kwargs):
            if after_effect: original(*args,**kwargs)
            raise RuntimeError('Injected loss before receipt publication')
        before=self.count()
        digest=self.manager.checkpoint(self.ident)['candidate']['sha256']
        with patch.object(repair,'grade_candidate',interrupted):
            result=self.invoke('repair_plan',{'action':'run','target':'candidate'})
        assert result.get('execution_state') == 'unknown', result
        self.pending.add((self.plan,digest))
        self.executed+=int(after_effect)
        assert self.count()==before+int(after_effect)

    @rule()
    def retry_same_effect_identity(self):
        if self.last_effect is None: return
        name,args,effect,result=self.last_effect; before=self.count()
        assert self.invoke(name,args,effect)==result
        assert self.count()==before

    @rule()
    def reject_reused_identity_with_different_input(self):
        if self.last_effect is None: return
        _,_,effect,_=self.last_effect
        before=self.count(); plan=self.plan
        result=self.invoke('repair_plan',{'action':'set','plan':'A different effect must not replace the original.'},effect)
        assert result.get('executed') is False and 'identity reused' in result.get('error','')
        assert self.count()==before and repair.current(self.agent)['id']==plan

    @rule()
    def review(self):
        result=self.invoke('checkpoint',{'review':True})
        reviewer=result['adversarial_review']['agent_id']
        observed=self.manager.wait(reviewer,10)
        assert observed['status']=='completed', observed
        self.reviews.add(result['candidate']['sha256'])

    @rule()
    def restart(self):
        before=self.count()
        self.workflow=PausedInvestigation(self.manager,self.root/'run',feedback_grader=self.grade)
        self.descriptor=self.manager.descriptor(self.ident)
        assert repair.current(self.agent)['id']==self.plan
        assert self.count()==before

    @rule(corrupt=st.booleans())
    def check_completion(self, corrupt):
        self.verify(corrupt)

    def verify(self, corrupt=False):
        captured=self.manager.checkpoint(self.ident)
        assert captured['captured']
        candidate=captured['candidate']; digest=candidate['sha256']; before=self.count()
        state=self.manager._session(self.ident)._load(); state['assistant_counter']+=1
        self.manager._session(self.ident)._save(state)
        if corrupt:
            path=self.root/'corrupt.patch'; path.write_text('wrong bytes')
            candidate={**candidate,'patch_path':str(path)}
        result=self.workflow.verify_completion(self.ident,{'patch':candidate})
        # The spec is independent of the completion implementation's branch layout.
        if result['state']=='passed':
            assert not corrupt
            assert self.value == 0
            tests=result['checks']['executable_test_plan']['result']
            assert tests['plan_id']==self.plan and tests['candidate']['sha256']==digest
            assert tests['state']=='finished' and tests['passed']
        key=(self.plan,digest)
        if not corrupt and candidate['bytes']:
            if key in self.known:
                assert self.count()==before, 'An exact completed check must be reused'
            elif key in self.pending:
                assert self.count()==before, 'An uncertain effect must not be replayed'
            else:
                assert self.count()==before+1, 'New bytes or a revised plan require execution'
                self.known[key]=self.value==0
        expected=(not corrupt and bool(candidate['bytes']) and self.value==0
                  and self.known.get(key) is True)
        assert (result['state']=='passed') is expected
        self.executed+=self.count()-before
        # Reopening the same completion cannot dispatch another effect.
        frozen=json.loads(json.dumps(result)); count=self.count()
        resumed=self.workflow.verify_completion(self.ident,{'patch':candidate})
        assert resumed['state']==frozen['state'] and self.count()==count
        assert resumed['candidate']==frozen['candidate']
        assert resumed['checks']['candidate_identity']['passed']==frozen['checks']['candidate_identity']['passed']
        return result

    @invariant()
    def history_and_starting_source_remain_honest(self):
        assert self.count()==self.executed
        assert (self.workflow.starting_workspace()/'answer.py').read_text()=='def answer(n):\n    return -1\n'
        for path,original in self.archives.items(): assert path.read_bytes()==original

    def teardown(self):
        self.manager.shutdown(); self.temp.cleanup()


if os.environ.get('REPAIR_PROPERTY_SEED'):
    RepairMachine=seed(int(os.environ['REPAIR_PROPERTY_SEED']))(RepairMachine)
TestRepairSequences=RepairMachine.TestCase
TestRepairSequences.settings=settings(PROPERTY_SETTINGS, max_examples=EXAMPLES, stateful_step_count=STEPS)


class RepairReachability(unittest.TestCase):
    """Required sometimes witnesses; random generation alone cannot ensure reachability."""
    def setUp(self):
        self.machine=RepairMachine(tracing=False); self.addCleanup(self.machine.teardown)

    def test_success_and_recovery_are_reachable_without_accepting_stale_evidence(self):
        m=self.machine; m.edit(0); m.execute_checks()
        m.retry_same_effect_identity(); m.reject_reused_identity_with_different_input()
        before=m.count(); first=m.verify(False)
        self.assertEqual(first['state'],'passed'); self.assertEqual(m.count(),before)
        # Same valid behavior, different bytes: earlier tests cannot cover it; rerun them.
        (m.live/'answer.py').write_text('def answer(n):\n    return n\n# different candidate\n')
        changed=m.verify(False)
        self.assertEqual(changed['state'],'passed')
        self.assertNotIn('adversarial_review_delivery',changed['checks'])
        self.assertEqual(m.count(),before+1)
        m.register_plan(); m.interrupt_check(True); m.restart()
        before=m.count(); uncertain=m.verify(False)
        self.assertEqual(uncertain['state'],'failed'); self.assertEqual(m.count(),before)
        m.execute_checks(); m.review()
        self.assertEqual(m.verify(False)['state'],'passed')

    def test_failed_interactive_candidates_keep_owner_and_reuse_exact_grade(self):
        m=self.machine
        for offset in (1,2):
            m.edit(offset); m.interactive_grade(); m.interactive_grade()
        self.assertEqual(len(m.grades),2)
        self.assertFalse(m.workflow.report.get('handoff'))

    def test_reviewer_opinion_cannot_make_a_bad_candidate_pass(self):
        m=self.machine; m.edit(1); m.execute_checks(); m.review()
        result=m.verify(False)
        self.assertNotIn('adversarial_review_delivery',result['checks'])
        self.assertFalse(result['checks']['executable_test_plan']['passed'])
        self.assertEqual(result['state'],'failed')
