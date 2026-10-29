"""Append-only epochs, selective evidence and freedom tested at request boundaries."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hypothesis import given, settings, strategies as st, seed
from hypothesis.database import DirectoryBasedExampleDatabase
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant

from src.teamharness.context import APPEND_ONLY, LEGACY, recent_exchanges, runtime_identity
from src.teamharness.presentation import observation
from src.teamharness.session import Session
from src.teamharness.provider import HTTPProvider
from src.teamharness.tools import ToolRuntime
from src.teamharness.agents import AgentManager
from src.teamharness.main import Investigation
from test_session import Script, answer, tools, SHELL
from test_main_flow import audit_requests, http_script, facts, call, finish, last_tool, repository

def latest_runtime(messages):
    # Inspect the original complete snapshot referenced by the delivered delta.
    # This also establishes that the model can retrieve the full observation.
    from src.store import Store
    lines = [line for message in messages for line in (message.get('content') or '').splitlines()
             if line.startswith('Full original observation: ')]
    assert lines, 'No complete runtime observation was delivered to the model'
    path, ref = lines[-1].removeprefix('Full original observation: ').rsplit(' ', 1)
    store = Store(path, readonly=True)
    try:
        return store.get(ref, scope='session')['body']['snapshot']
    finally:
        store.db.close()


TEXT=st.text(alphabet=st.characters(blacklist_categories=['Cs']),max_size=120)
DB=DirectoryBasedExampleDatabase('.local/hypothesis/context')
FAST=settings(max_examples=10,stateful_step_count=10,deadline=None,database=DB)


class ContextLaws(unittest.TestCase):
    @settings(max_examples=80,deadline=None,database=DB)
    @given(text=TEXT.filter(bool), count=st.integers(1,5))
    def test_search_has_one_copy_and_retains_identity_and_coverage(self,text,count):
        rows=[{'path':'/repo/haystack','line':i+1,'byte_offset':i*99,'sha256':'a'*64, 'future_identity':'KEEP_ENTRY_METADATA',
               'text':text+'\n'} for i in range(count)]
        receipt={'search_page':{'version':1,'entries':rows,'next_offset':100,'complete':True,'future_coverage':'KEEP_PAGE_METADATA'},
                 'content':'unnecessary duplicate representation: '+text,
                 'scan_complete':False,'errors':[{'path':'/repo/unreadable','error':'permission denied'}],
                 'matches':count,'_artifacts':{'results_ref':{'path':'/archive/all-matches'}}}
        original=copy.deepcopy(receipt); rendered=observation('search_files',receipt)
        self.assertNotIn('unnecessary duplicate representation:',rendered)
        self.assertIn('/archive/all-matches',rendered)
        self.assertIn('KEEP_ENTRY_METADATA',rendered)
        self.assertIn('KEEP_PAGE_METADATA',rendered)
        self.assertIn('scan complete: false',rendered)
        self.assertIn('permission denied',rendered)
        self.assertIn('source SHA256 '+ 'a'*64,rendered)
        self.assertEqual(receipt,original)
        # New source text remains literal, even if it resembles instructions or JSON.
        for row in rows:self.assertIn(row['text'],rendered)

    @settings(max_examples=60,deadline=None,database=DB)
    @given(text=TEXT, keep=st.integers(0,4), partial=st.booleans())
    def test_only_complete_exchanges_survive_and_selection_is_explicit(self,text,keep,partial):
        history=[]; complete=[]
        for i in range(4):
            a=tools(('exec_command',{'command':str(i)}))['choices'][0]['message']
            a['content']=text
            t={'role':'tool','tool_call_id':'call_0','content':'EVIDENCE-'+str(i)}
            group=[a,t];history.extend(group);complete.append(group)
        if partial:
            history.append(tools(('exec_command',{'command':'unfinished'}))['choices'][0]['message'])
        original=copy.deepcopy(history)
        self.assertEqual(recent_exchanges(history,keep),complete[-keep:] if keep else [])
        self.assertEqual(history,original)

    @settings(max_examples=70,deadline=None,database=DB)
    @given(clock=st.integers(1,100000),elapsed=st.integers(0,99999),failed=st.integers(0,10))
    def test_clock_changes_do_not_erase_execution_changes(self,clock,elapsed,failed):
        initial={'observed_at':0,'task_elapsed_seconds':0,'task_deadline':{
            'deadline_unix':100001,'observed_at':0,'remaining_seconds':100001,'expired':False},
            'run': {'available':True,'observed_at':0,'run_started_at':0,'elapsed_seconds':0,
                    'run_cap_seconds':14400,'remaining_seconds':14400,'active_tasks':1,'shared_gpu_count':1},
            'candidate_evidence':{'tests_failed':failed,'candidate_sha256':'original'}}
        changed=copy.deepcopy(initial)
        changed['observed_at']=clock;changed['task_elapsed_seconds']=elapsed
        changed['task_deadline'].update(observed_at=clock,remaining_seconds=100001-clock)
        changed['run'].update(observed_at=clock,elapsed_seconds=elapsed,remaining_seconds=max(0,14400-elapsed))
        self.assertEqual(runtime_identity(initial),runtime_identity(changed))
        changed['candidate_evidence']['candidate_sha256']='different'
        self.assertNotEqual(runtime_identity(initial),runtime_identity(changed))
        changed=copy.deepcopy(initial);changed['run']['active_tasks']=2
        self.assertNotEqual(runtime_identity(initial),runtime_identity(changed))
        changed=copy.deepcopy(initial);changed['task_deadline']['expired']=True
        self.assertNotEqual(runtime_identity(initial),runtime_identity(changed))


class ContextExecutor:
    def __init__(self,work):
        self.work=work;self.executions=[];self.clock=0;self.failed=2;self.job='running'
    def runtime_facts(self):
        self.clock+=1
        return {'observed_at':self.clock,'task_elapsed_seconds':self.clock,
                'task_deadline':{'deadline_unix':100000,'observed_at':self.clock,
                                 'remaining_seconds':100000-self.clock,'expired':False},
                'candidate_evidence':{'candidate_sha256':'exact-checkpoint','tests_failed':self.failed,
                                      'tests_total':10,'grader_reported_failure':self.failed>0},
                'existing_jobs':[{'state':self.job,'jobs':[{'id':'existing-job','elapsed_seconds':self.clock}]}]}
    def __call__(self,name,args,effect):
        self.executions.append(effect)
        result=subprocess.run(['/bin/bash','-c',args['command']],cwd=self.work,capture_output=True,text=True,check=False)
        return {'exit_code':result.returncode,'output':result.stdout}


class ContextMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__();self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.work=self.root/'repo';self.work.mkdir();self.provider=Script([])
        self.executor=ContextExecutor(self.work)
        self.session=Session(self.root/'session','investigator','Stable instructions','Original human task',
                             self.provider,[SHELL],self.executor,self.work,public_task='Original human task')
        self.previous=None;self.epoch=0;self.effects=0;self.notes=[]
    def run(self,responses):
        self.session.append_user('Continue with the same human requirements.')
        start=len(self.provider.requests);self.provider.responses.extend(responses)
        result=self.session.run();assert result['status']=='completed',result
        for request in self.provider.requests[start:]:
            if self.previous:
                assert request['messages'][:len(self.previous['messages'])]==self.previous['messages']
                assert request['tools']==self.previous['tools']
            assert request['messages'][0]['content']=='Stable instructions'
            assert request['messages'][1]['content']=='Original human task'
            self.previous=copy.deepcopy(request)
        audit_requests(self.session.database)
    @rule(text=TEXT)
    def express(self,text):
        prose='An explanation, uncertainty or proposal: '+text
        self.run([answer(prose)])
        assert self.session._load()['messages'][-1]['content']==prose
    @rule(text=TEXT)
    def revise_note(self,text):
        prose='Working hypothesis: '+text
        self.session.set_task_brief(prose,author='model-owner')
        self.run([answer('Note recorded; it remains my interpretation.')]);self.notes.append(prose)
        assert any(prose in (m.get('content') or '') for m in self.provider.requests[-1]['messages'])
    @rule(text=TEXT)
    def revise_guidance(self,text):
        self.session.reconfigure(reason='Choose useful authored guidance',guidance=[{'author':'model-owner','text':text}])
        self.run([answer('Guidance can change without rewriting the preceding exchanges.')])
    @rule(failed=st.integers(0,10), job=st.sampled_from(['running','exited','unknown']))
    def runtime_change(self,failed,job):
        self.executor.failed=failed;self.executor.job=job
        self.run([answer('Continue from the dated execution evidence.')])
        snapshot=latest_runtime(self.provider.requests[-1]['messages'])
        assert snapshot['candidate_evidence']['tests_failed']==failed
        assert snapshot['existing_jobs'][0]['state']==job
    @rule()
    def execute(self):
        self.run([tools(('exec_command',{'command':'printf verified >> effects; printf "LITERAL_EVIDENCE"'})),answer('Observed real execution.')])
        self.effects+=1
    @rule()
    def reopen(self):
        self.session=Session(root=self.session.root,provider=self.provider,executor=self.executor,
                             **Session.recorded_config(self.session.root))
    @rule(keep=st.integers(0,2))
    def compact(self,keep):
        count=len(self.executor.executions);old_requests=copy.deepcopy(self.provider.requests)
        self.provider.responses.append(answer(json.dumps({'continuation':'Model interpretation: everything looks fine.'})))
        result=self.session.compact(retain_recent_exchanges=keep)
        assert result['status']=='compacted',result
        assert len(self.executor.executions)==count
        assert self.provider.requests[:len(old_requests)]==old_requests
        state=self.session._load()
        assert state['messages'][1]['content']=='Original human task'
        # The independently supplied facts survive even a misleading model summary.
        assert latest_runtime(state['messages'])['candidate_evidence']['tests_failed']==self.executor.failed
        if keep==0:assert not any('Recent complete exchanges retained' in str(m.get('content')) for m in state['messages'])
        self.previous=None;self.epoch+=1
        audit_requests(self.session.database)
    @invariant()
    def source_and_effects_remain_honest(self):
        assert len(self.executor.executions)==self.effects
        path=self.work/'effects'
        assert (path.read_text() if path.exists() else '')=='verified'*self.effects
        assert self.session._load()['epoch']==self.epoch
    def teardown(self):self.temp.cleanup()


if os.environ.get('CONTEXT_PROPERTY_SEED'):
    ContextMachine=seed(int(os.environ['CONTEXT_PROPERTY_SEED']))(ContextMachine)
TestContextSequences=ContextMachine.TestCase
TestContextSequences.settings=FAST


class ActualContext(unittest.TestCase):
    def test_http_needle_retrieval_notes_and_compaction(self):
        case=self
        class Model:
            def __init__(self):self.requests=[];self.errors=[];self.step=0;self.before=None
            def reply(self,request):
                if 'response_format' in request:
                    return answer(json.dumps({'continuation':'Model interpretation: remember the needle evidence.'}))
                n=self.step;self.step+=1
                if self.before is not None and n!=4:
                    case.assertEqual(self.before['messages'],request['messages'][:len(self.before['messages'])])
                self.before=copy.deepcopy(request)
                if n==0:return call('search_files',{'path':'haystack.txt','query':'NEEDLE-7C9'})
                if n==1:
                    rendered=request['messages'][-1]['content'] if request['messages'][-1]['role']=='tool' else next(m['content'] for m in reversed(request['messages']) if m['role']=='tool')
                    case.assertEqual(rendered.count('NEEDLE-7C9 payload=marigold-482'),1)
                    return call('read_file',{'path':'haystack.txt','start_line':799,'line_count':3})
                if n==2:
                    case.assertIn('NEEDLE-7C9 payload=marigold-482',str(request['messages']))
                    return answer('I found a candidate match. This is useful prose; I will verify its context.')
                if n==3:return call('compact',{})
                if n==4:
                    case.assertTrue(any('NEEDLE-7C9 payload=marigold-482' in m.get('content','') for m in request['messages'] if m['role']=='user'))
                    return finish('The marker is at line 800; surrounding lines were observed.')
                raise AssertionError('Unexpected request')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=repository(root)
            lines=['ordinary haystack line\n']*1600;lines[799]='NEEDLE-7C9 payload=marigold-482\n'
            (repo/'haystack.txt').write_text(''.join(lines))
            model=Model()
            with http_script(model) as env,patch.dict(os.environ,env):
                manager=AgentManager(root/'run/agents',HTTPProvider)
                try:
                    result=Investigation(manager,root/'run',completion_gate=False).run('Find the marker and report its line; do not edit.',repo)
                    self.assertEqual(model.errors,[]);self.assertEqual(result['state'],'completed')
                    self.assertEqual(model.step,5, 'Ordinary main prose must not terminate the investigation')
                    d=manager.descriptor(result['agents']['main']);audit_requests(Path(d['root'])/'session.sqlite3')
                    with sqlite3.connect(Path(d['root'])/'session.sqlite3') as db:
                        events=[json.loads(row[0]) for row in db.execute("SELECT body FROM objects WHERE kind='model_request' ORDER BY id")]
                    comparable=[e['prefix_structure'] for e in events if e['purpose']=='agent' and e['prefix_structure']['previous_request']]
                    self.assertTrue(comparable)
                    self.assertTrue(all(e['previous_messages_are_prefix'] and e['tools_unchanged'] for e in comparable))
                    self.assertEqual((repo/'haystack.txt').read_text(),''.join(lines))
                finally:manager.shutdown()

    def test_compaction_keeps_failed_facts_independent_of_favorable_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); executor=ContextExecutor(root)
            provider=Script([answer(json.dumps({'continuation':'My interpretation: everything passed.'}))])
            session=Session(root/'session','investigator','system','Literal task',provider,[SHELL],executor,root,public_task='Literal task')
            result=session.compact(retain_recent_exchanges=0)
            self.assertEqual(result['status'],'compacted')
            state=session._load()
            self.assertEqual(latest_runtime(state['messages'])['candidate_evidence']['tests_failed'],2)
            self.assertTrue(any('tests failed: 2' in (m.get('content') or '') for m in state['messages']))
            self.assertEqual(executor.executions,[])
            self.assertIn('My interpretation: everything passed.',str(state['messages']))
            audit_requests(session.database)

    def test_legacy_session_keeps_recorded_projection_until_explicit_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);provider=Script([answer('original')]);executor=ContextExecutor(root)
            session=Session(root/'session','investigator','system','task',provider,[SHELL],executor,root,context_policy=LEGACY)
            state=session._load();state['config'].pop('context_policy');session._save(state)
            session=Session(root=session.root,provider=provider,executor=executor,**Session.recorded_config(session.root))
            session.run();original=copy.deepcopy(provider.requests[0])
            self.assertNotIn('context_policy',session.state['config'])
            session.reconfigure(reason='Explicitly adopt append-only context',context_policy=APPEND_ONLY)
            session.append_user('Continue.');provider.responses.append(answer('new'))
            session.run();self.assertEqual(provider.requests[0],original)
            audit_requests(session.database)
