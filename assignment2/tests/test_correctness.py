"""Focused mechanical regression fixtures; no network or model calls."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mads.store import Store, EvidenceError, atomic_json, dump
from mads.runner import export, load_outcome


def preserve_fixture(root, label):
    destination=os.environ.get('MADS_TEST_ARTIFACTS')
    if destination:
        import shutil
        directory=Path(destination)
        directory.mkdir(parents=True,exist_ok=True)
        target=Path(tempfile.mkdtemp(prefix=label+'-',dir=directory))/'fixture'
        shutil.copytree(root,target)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(preserve_fixture,self.root,self._testMethodName)

    def test_surrogate_atomic_result_and_failure(self):
        path = self.root/'result.json'
        claim = {'claim':'finished \ud83d'}
        atomic_json(path, claim)
        original = path.read_bytes()
        self.assertEqual(json.loads(original), claim)
        self.assertIn(b'\\ud83d', original)
        with patch('mads.store.os.replace', side_effect=OSError('injected replace failure')):
            with self.assertRaises(OSError):
                atomic_json(path, {'claim':'new'})
        self.assertEqual(path.read_bytes(), original)
        with patch('mads.store.os.fsync',side_effect=OSError('injected atomic write flush failure')):
            with self.assertRaises(OSError): atomic_json(path, {'claim':'another'})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_missing_malformed_outcomes_and_recorded_recovery(self):
        for data in (None, b'', b'{broken'):
            run = self.root/str(len(list(self.root.iterdir())))
            run.mkdir()
            if data is not None:
                (run/'result.json').write_bytes(data)
            self.assertEqual(export(self.root, 'HEAD', run), 1)
            result = json.loads((run/'result.json').read_bytes())
            self.assertEqual(result['cleanup_outcome']['status'], 'unknown')
            self.assertEqual(result['export_outcome']['status'], 'failed')
        run = self.root/'recorded'; run.mkdir()
        outcome = dict(agent_outcome={'status':'not_completed'}, harness_outcome={'status':'error'},
                       cleanup_outcome={'status':'completed'})
        store = Store(run/'journal'); store.add('run_end', **outcome)
        (run/'result.json').write_bytes(b'')
        recovered = load_outcome(run)
        self.assertEqual(recovered['cleanup_outcome'], outcome['cleanup_outcome'])
        self.assertEqual(recovered['outcome_recovery']['run_end_record'], 1)
        self.assertTrue(list((run/'recovery/blobs').iterdir()))

    def test_append_interrupt_after_complete_disk_write(self):
        store = Store(self.root/'journal')
        store.add('before')
        fsync = os.fsync
        calls = 0
        def interrupt_once(fd):
            nonlocal calls
            calls += 1
            fsync(fd)
            if calls == 1:
                raise KeyboardInterrupt('after flush')
        with patch('mads.store.os.fsync', side_effect=interrupt_once):
            with self.assertRaises(KeyboardInterrupt): store.add('interrupted')
        self.assertEqual(store.add('cleanup'), 3)
        self.assertEqual([e['id'] for e in Store(store.root).events], [1,2,3])

    def test_append_partial_and_before_write(self):
        for partial in (False, True):
            store = Store(self.root/str(partial))
            store.add('before')
            original_open = Path.open
            class BrokenAppend:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def write(self, data):
                    if partial:
                        with original_open(store.path, 'ab') as out: out.write(data[:10].encode())
                    raise KeyboardInterrupt('during write')
            def opened(path, mode='r', *args, **kwargs):
                return BrokenAppend() if path == store.path and mode == 'a' else original_open(path, mode, *args, **kwargs)
            with patch.object(Path, 'open', opened):
                with self.assertRaises(KeyboardInterrupt): store.add('interrupted')
            if partial:
                saved = store.path.read_bytes()
                with self.assertRaises(EvidenceError): store.add('cleanup')
                self.assertEqual(store.path.read_bytes(), saved)
            else:
                self.assertEqual(store.add('cleanup'), 2)

    def test_interrupt_at_memory_commit(self):
        store = Store(self.root/'journal')
        class Interrupted(list):
            def append(self, row): raise KeyboardInterrupt('memory commit')
        store.events = Interrupted()
        with self.assertRaises(KeyboardInterrupt): store.add('interrupted')
        self.assertEqual(store.add('cleanup'), 2)

    def test_host_without_sdk(self):
        subprocess.run([sys.executable,'-S','-c','import mads.runner'], check=True)

    def test_failed_launch_invalidates_old_attempt(self):
        from mads.runner import run
        tasks=self.root/'tasks.json'
        tasks.write_text(json.dumps({'fixture':{'instance_id':'fixture'}}))
        attempts=self.root/'attempts'; attempts.mkdir()
        atomic_json(attempts/'fixture.json',{'instance_id':'fixture','export_outcome':{'status':'completed'},'patch':'old'})
        with patch.dict(os.environ,{'TASKS_JSON':str(tasks),'MADS_BATCH_DIR':str(attempts)},clear=True):
            with self.assertRaisesRegex(ValueError,'CS2680_API_KEY'): run(0)
        self.assertEqual(json.loads((attempts/'fixture.json').read_bytes())['export_outcome']['status'],'not_available')

    def test_stale_patch_not_bundled(self):
        tasks = json.loads(Path('evaluation_scripts/agent_task_input.json').read_text())
        iid = next(iter(tasks))
        (self.root/f'model_patch_{iid}.diff').write_text('OLD PATCH')
        env = {**os.environ,'MADSLOOP_REPO':str(self.root),'MADS_BATCH_DIR':str(self.root/'batch')}
        out = subprocess.run([sys.executable,'evaluation_scripts/make_patches.py'], env=env, capture_output=True)
        self.assertEqual(out.returncode, 1)
        self.assertEqual(json.loads((self.root/'patches.json').read_text()), [])
        # Valid partial-work export is accepted without an agent-completion gate.
        import hashlib
        exported = self.root/'partial.diff'; exported.write_text('CURRENT PARTIAL')
        (self.root/'batch').mkdir()
        atomic_json(self.root/'batch'/f'{iid}.json', dict(instance_id=iid,patch=str(exported),
            patch_sha256=hashlib.sha256(exported.read_bytes()).hexdigest(),export_outcome={'status':'completed'}))
        subprocess.run([sys.executable,'evaluation_scripts/make_patches.py'], env=env, capture_output=True)
        self.assertEqual(json.loads((self.root/'patches.json').read_text())[0]['model_patch'], 'CURRENT PARTIAL')


class CapacityTests(unittest.TestCase):
    def test_rejected_below_ceiling_shrinks_and_controls_stay_valid(self):
        from mads.context import Context
        from mads.store import view
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); store = Store(root/'journal')
            context = Context(store,root/'memory')
            artifact = store.artifact(store.blob('x'*30000))
            group = {'id':store.add('fixture'),'fresh':True,
                'assistant':{'role':'assistant','tool_calls':[{'id':'read-1','type':'function',
                    'function':{'name':'read','arguments':'{}'}}]},
                'results':[{'call_id':'read-1','tool_name':'read','status':'completed',
                            'output':view(artifact, 'full', None)}]}
            def compile(current=None, restart=None):
                return context.compile('system','AUTHORITATIVE TASK',[],'fixture',{},current,restart)
            rejected = compile(group)
            self.assertLess(context.tokens(rejected), context.limit*.8)
            context.reduce_capacity(rejected)
            smaller = compile()
            self.assertLess(len(dump(smaller)), len(dump(rejected)))
            context._validate_config({})
            self.assertLessEqual(context.pressure_at, context.limit)
            current = {'id':store.add('fixture'), 'fresh':True,
                'assistant':{'role':'assistant','tool_calls':[{'id':'restart','type':'function',
                    'function':{'name':'context_restart','arguments':'{}'}}]},
                'results':[{'call_id':'restart','tool_name':'context_restart','status':'completed'}]}
            compile(current, {'index':0,'arguments':{}})
            self.assertEqual(current['results'][0]['context_change'], 'applied')
            context._validate_config({'keep_recent':3})
            self.assertIn('AUTHORITATIVE TASK', dump(smaller))

    def test_irreducible_capacity_is_explicit(self):
        from mads.context import Context, CapacityBlocked
        with tempfile.TemporaryDirectory() as directory:
            context = Context(Store(Path(directory)/'journal'),Path(directory)/'memory')
            request = context.compile('system','x'*30000,[],'fixture',{})
            context.reduce_capacity(request)
            with self.assertRaises(CapacityBlocked):
                context.compile('system','x'*30000,[],'fixture',{})
            context._validate_config({})

class ProcessTests(unittest.TestCase):
    def test_failed_cancellation_survives_agent_boundary(self):
        from mads.agent import Agent
        from mads.jobs import TerminationError
        class Config:
            config={'provider':'course','model':'fixture'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); agent=Agent(root,'task',root/'run',Config(), session_role='reviewer')
            try:
                result=agent.tools.bash('sleep 20',wait_seconds=0)
                call={'id':'cancel','type':'function','function':{'name':'cancel',
                     'arguments':json.dumps({'job_id':result['process']['job_id']})}}
                with patch.object(agent.tools.jobs,'terminate',side_effect=TerminationError('still alive',[{'signal':'SIGKILL','status':'sent'}])):
                    group,_=agent.batch({'role':'assistant','tool_calls':[call]},1)
                observed=group['results'][0]
                self.assertEqual(observed['status'],'failed')
                self.assertEqual(observed['error'],'still alive')
                self.assertEqual(observed['termination_attempts'][0]['signal'],'SIGKILL')
                self.assertEqual(observed['effects'],'termination_requested')
            finally: agent.tools.jobs.close()

    def test_signal_failure_is_reported_with_attempt(self):
        from mads.tools import Tools
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); tools=Tools(root,root/'run',Store(root/'run/journal'))
            try:
                result=tools.bash('sleep 20',wait_seconds=0)
                with patch('mads.jobs.os.killpg',side_effect=PermissionError('injected signal denial')):
                    cancelled=tools.cancel(result['process']['job_id'])
                self.assertEqual(cancelled['status'],'failed')
                self.assertEqual(cancelled['termination_attempts'][0]['status'],'failed')
                self.assertIn('injected signal denial',cancelled['error'])
            finally: tools.jobs.close()

    def test_failed_cancellation_is_an_outcome(self):
        from mads.tools import Tools
        from mads.jobs import TerminationError
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); tools=Tools(root,root/'run',Store(root/'run/journal'))
            try:
                result=tools.bash('sleep 20',wait_seconds=0)
                attempts=[{'signal':'SIGTERM','status':'sent'}, {'signal':'SIGKILL','status':'failed'}]
                with patch.object(tools.jobs,'terminate',side_effect=TerminationError('still alive',attempts)):
                    cancelled=tools.cancel(result['process']['job_id'])
                self.assertEqual(cancelled['status'],'failed')
                self.assertEqual(cancelled['termination_attempts'],attempts)
                self.assertEqual(cancelled['process']['state'],'running')
                self.assertEqual(tools.effects,'termination_requested')
            finally:
                closed=tools.jobs.close()
            self.assertTrue(all(item['process']['capture_status']=='complete' for item in closed))

class PresentationTests(unittest.TestCase):
    def setUp(self):
        from mads.tools import Tools
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.store=Store(self.root/'run/journal')
        self.addCleanup(preserve_fixture,self.root,self._testMethodName)
        self.tools=Tools(self.root,self.root/'run',self.store)
        self.addCleanup(self.tools.jobs.close)

    def shown(self, data, chars):
        from mads.store import present, view
        artifact=self.store.artifact(self.store.blob(data))
        result=present(view(artifact,output_chars=chars),chars)
        self.assertEqual(b''.join(data[a:b] for a,b in result['shown_byte_ranges']),
                         ''.join(result['text_parts']).encode())
        return result

    def test_invalid_read_ahead_keeps_prefix(self):
        result=self.shown(b'valid prefix'+b'\xff',5)
        self.assertEqual(result['text_parts'],['valid'])
        self.assertEqual(result['shown_byte_ranges'],[[0,5]])
        result=self.shown(b'valid prefix'+b'\xff',100)
        self.assertEqual(result['text_parts'],['valid prefix'])
        self.assertIn('decoding_issue',result)
        self.assertEqual(result['omitted_byte_ranges'],[[12,13]])

    def test_split_live_output_and_exact_binary_recovery(self):
        import shlex, time, base64
        from mads.store import present
        code="import os,sys;os.write(1,b'prefix\\xf0\\x9f');sys.stdin.readline();os.write(1,b'\\x99\\x82')"
        result=self.tools.bash(shlex.quote(sys.executable)+' -u -c '+shlex.quote(code),wait_seconds=.1)
        job=result['process']['job_id']
        for _ in range(100):
            if self.tools.jobs.jobs[job]['observed'] >= 8: break
            time.sleep(.01)
        live=self.tools.read(result['process']['live_path'],range='full')
        shown=present(live['output'],100)
        self.assertEqual(shown['text_parts'],['prefix'])
        self.assertEqual(shown['omitted_byte_ranges'],[[6,8]])
        binary=present(self.tools.read(**shown['continuation'])['output'],100)
        self.assertEqual(base64.b64decode(''.join(binary['text_parts'])),b'\xf0\x9f')
        self.tools.send(job,'\n',close=True)
        self.tools.poll(job,2)
        final=present(self.tools.read(result['process']['live_path'],range='full')['output'],100)
        self.assertEqual(final['text_parts'],['prefix🙂'])

    def test_missing_newline_diffs_preserve_files(self):
        for old,new in [('return old','return new'),('return old\n','return new'),('return old','return new\n')]:
            (self.root/'source').write_text(old)
            result=self.tools.edit('source',old,new)
            diff=Path(result['output']['path']).read_text()
            self.assertNotIn('-return old+return new',diff)
            self.assertIn('\\ No newline at end of file\n',diff)
            self.assertEqual((self.root/'source').read_bytes(),new.encode())

    def test_truncated_search_has_separate_pagination(self):
        from mads.store import compact_result
        (self.root/'large').write_text(('match '+'x'*1000+'\n')*5)
        result=self.tools.search('large','match',max_matches=2,output_chars=30)
        compact=compact_result(result)
        self.assertTrue(compact['search_continuation'])
        self.assertTrue(compact['output']['next_read'])
        next_page=self.tools.search(**compact['search_continuation'])
        self.assertGreater(next_page['search_scope']['start_line'],result['search_scope']['start_line'])
        self.assertEqual(json.loads(Path(result['output']['path']).read_bytes())['continuation'],compact['search_continuation'])

class TransportTests(unittest.TestCase):
    def exercise(self, failures, expected_attempts, success=True):
        import httpx
        from mads.provider import Provider
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'journal'); calls=[]
            def handle(request):
                calls.append(json.loads(request.content))
                if len(calls)<=len(failures):
                    failure=failures[len(calls)-1]
                    if isinstance(failure, Exception): raise failure
                    status,body=failure
                    return httpx.Response(status,json=body)
                return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'ok'}}]})
            config={'provider':'course','base_url':'http://fixture.invalid/v1','key_env':'MADS_TEST_KEY','model':'fixture'}
            with patch.dict(os.environ,{'MADS_TEST_KEY':'local-fixture-not-a-key'}), patch('mads.provider.time.sleep'):
                with Provider(store,config=config,transport=httpx.MockTransport(handle)) as provider:
                    if success: provider.complete({'model':'fixture','messages':[]})
                    else:
                        with self.assertRaises(Exception): provider.complete({'model':'fixture','messages':[]})
            self.assertEqual(len(calls),expected_attempts)
            self.assertTrue(all(body==calls[0] for body in calls))
            requests=[e for e in store.events if e['event']=='request']
            attempts=[e for e in store.events if e['event']=='api_attempt']
            self.assertEqual(len(requests),1)
            self.assertEqual(len(attempts),expected_attempts)
            self.assertTrue(all(e['request_record']==requests[0]['id'] for e in attempts))
            self.assertEqual(len([e for e in store.events if e['event']=='api_error']), min(len(failures),expected_attempts))
            preserve_fixture(Path(directory),'transport')

    def test_transient_and_terminal_failures(self):
        import httpx
        self.exercise([(503,{'error':{'code':'unavailable'}})],2)
        self.exercise([httpx.ReadTimeout('local read timeout')],2)
        self.exercise([(429,{'error':{'code':'rate_limit_exceeded'}})],2)
        for failure in [(429,{'error':{'code':'insufficient_quota'}}), (401,{}),(400,{}),
                        (500,{'error':{'code':'billing_error'}}),(429,{})]:
            self.exercise([failure],1,False)
        self.exercise([(503,{})]*3,3,False)

class AgentFlowTests(unittest.TestCase):
    def test_context_recovery_and_surrogate_completion_through_sdk(self):
        import httpx
        from mads.agent import Agent
        from mads.provider import Provider
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); repo=root/'repo'; repo.mkdir()
            (repo/'large').write_bytes(b'important observed data\n'*10000+b'\xff')
            store=Store(root/'run/journal'); requests=[]
            def handle(request):
                requests.append(request.content)
                if len(requests)==2:
                    return httpx.Response(400,json={'error':{'code':'context_length_exceeded'}})
                name,args=('read',{'path':'large','range':'full','output_chars':None}) if len(requests)==1 else ('finish',{'summary':'done \ud83d'})
                return httpx.Response(200,json={'choices':[{'finish_reason':'tool_calls','message':{
                    'role':'assistant','tool_calls':[{'id':'call-'+str(len(requests)),'type':'function',
                    'function':{'name':name,'arguments':json.dumps(args)}}]}}]})
            config={'provider':'course','base_url':'http://fixture.invalid/v1','key_env':'MADS_TEST_KEY','model':'fixture'}
            with patch.dict(os.environ,{'MADS_TEST_KEY':'local-fixture-not-a-key'}):
                with Provider(store,config=config,transport=httpx.MockTransport(handle)) as provider:
                    agent=Agent(repo,'Read the data, then finish.',root/'run',provider,store, session_role='reviewer')
                    result=agent.run()
            self.assertEqual(len(requests),3)
            self.assertLess(len(requests[2]),len(requests[1]))
            self.assertEqual(result['harness_outcome']['status'],'completed')
            self.assertEqual(json.loads((root/'run/result.json').read_bytes())['agent_outcome']['claim'],'done \ud83d')
            self.assertEqual(result['cleanup_outcome']['status'],'completed')
            self.assertEqual(len([e for e in store.events if e['event']=='invocation']),2)
            self.assertTrue(all(e['matches_compiled_request'] for e in store.events if e['event']=='http_request'))
            agent.context._validate_config({})
            from mads.inspection.audit_journal import audit
            audited=audit(store.root,strict=True)
            self.assertTrue(audited['passed'],audited['errors'])
            preserve_fixture(root,'agent-flow')


class InspectionTests(unittest.TestCase):
    def test_state_changes_and_events(self):
        from mads.inspection.inspect_run import Journal
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); store=Store(root/'journal')
            saved=store.artifact(store.blob('account without newline'))
            for state in ('current','stale','absent'):
                store.add('context_manifest', account_state=state,account_record=1,
                          account_read={'path':saved['path']} if state!='absent' else None)
            store.add('context_configuration',applied=True,arguments={'keep_recent':0})
            store.add('context_restart',applied=False,error='too large')
            store.add('run_end',agent_outcome={'status':'not_completed'},harness_outcome={'status':'error'})
            journal=Journal(root)
            self.assertEqual([r['account_state'] for r in journal.accounts()],['current','stale','absent'])
            timeline=journal.timeline()
            self.assertEqual(timeline[0]['event'],'context_configuration')
            self.assertFalse(timeline[1]['applied'])
            self.assertEqual(timeline[2]['harness_outcome']['status'],'error')
            with self.assertRaises(ValueError): journal.request()
            with self.assertRaises(ValueError): journal.timeline(-1)

    def test_sdk_free_cli_and_missing_records(self):
        for args,code in [(['--help'],0),(['assignment','--help'],0),(['inspect','--help'],0),
                          (['logs','/nonexistent-mads-fixture'],2),(['assignment'],2)]:
            result=subprocess.run([sys.executable,'-S','-m','mads',*args],capture_output=True)
            self.assertEqual(result.returncode,code,result.stderr)
            self.assertNotIn(b'Traceback',result.stderr)

    def test_portable_manifest_and_status(self):
        from mads.runs import create_manifest,resolve_run
        from mads.inspection.inspect_run import Journal
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); run=root/'original'; store=Store(run/'journal')
            store.add('run_start')
            manifest=create_manifest(run,'ORIGINAL TASK',{'provider':'course','model':'fixture','key_env':'MADS_TEST_KEY'})
            self.assertNotIn('key_env',dump(manifest))
            moved=root/'moved'; run.rename(moved)
            self.assertEqual(resolve_run(moved),moved.resolve())
            summary=Journal(moved).summary()
            self.assertTrue(summary['last_recorded_at'])
            self.assertIsNone(summary['result'])
            self.assertEqual(json.loads((moved/'task.json').read_bytes())['task'],'ORIGINAL TASK')


if __name__ == '__main__':
    unittest.main()
