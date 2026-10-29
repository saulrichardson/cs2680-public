"""Offline plumbing: real shared Agent/Provider, fake HTTP responses, no live model."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import httpx
from mads.agent import Agent, main
from mads.course_logs import project, export_course_log
from mads.provider import Provider
from mads.store import Store, dump

SCHEMA = {
    'run_start': {'model_id','workdir'},
    'api_request': {'iteration','prompt_tokens','completion_tokens','total_tokens'},
    'api_retry': {'iteration','error','backoff_s'},
    'tool_call': {'iteration','tool_name','arguments'},
    'tool_result': {'iteration','tool_name','result','is_error'},
    'run_end': {'reason','num_iterations'},
}


def call(name, arguments=None, raw=None, id=None):
    return {'id':id or name,'type':'function',
            'function':{'name':name,'arguments':raw if raw is not None else dump(arguments or {})}}


class Fixture:
    def __init__(self, replies, protocol='chat_completions'):
        self.replies = iter(replies)
        self.requests = []
        self.urls = []
        self.protocol = protocol

    def handle(self, request):
        self.urls.append(str(request.url))
        self.requests.append(json.loads(request.content))
        calls = next(self.replies)
        if isinstance(calls, int):
            return httpx.Response(calls,json={'error':{'message':'fixture transient' if calls==503 else 'billing stopped'}})
        usage = dict(prompt_tokens=11, completion_tokens=7, total_tokens=18)
        if self.protocol == 'responses':
            usage = dict(input_tokens=11, output_tokens=7, total_tokens=18)
            value = dict(id='resp-'+str(len(self.requests)),status='completed',
                output=[dict(type='function_call',id='fc-'+c['id'],call_id=c['id'],**c['function']) for c in calls],usage=usage)
        else:
            value = dict(id='fake',object='chat.completion',created=0,model='fixture',usage=usage,
                choices=[dict(index=0,finish_reason='tool_calls',message=dict(role='assistant',content='',tool_calls=calls))])
        return httpx.Response(200,json=value)

    def provider(self):
        fixture = self
        class P(Provider):
            def __init__(self, *a, **kw):
                kw['transport'] = httpx.MockTransport(fixture.handle)
                super().__init__(*a, **kw)
            def fork(self, store):
                with patch('mads.provider.Provider', P):
                    return super().fork(store)
        return P


class CourseCompatibility(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.repo = self.root/'repo'; self.repo.mkdir()
        (self.repo/'text').write_text('VISIBLE-CONTENT\n'*500)
        self.run = self.root/'run'
        self.store = Store(self.run/'journal')
        self.env = patch.dict(os.environ, {'CS2680_API_KEY':'offline-fixture-only'},clear=True)
        self.env.start(); self.addCleanup(self.env.stop)

    def execute(self, fixture, **agent_options):
        cls = fixture.provider()
        config = dict(provider='course',model='qwen3.6-35b-a3b',key_env='CS2680_API_KEY',base_url='http://fixture.invalid/v1',protocol=fixture.protocol)
        with patch('mads.provider.Provider', cls), cls(self.store, config=config, retries=1) as provider:
            outcome = Agent(self.repo,'Read the fixture and finish.',self.run,provider,self.store,**agent_options, session_role='reviewer').run()
        self.assertEqual(outcome['harness_outcome']['status'],'completed',outcome)
        return project(self.run)

    def assert_schema_and_wire(self, rows, manifest, fixture):
        for row in rows:
            self.assertEqual(set(row), SCHEMA[row['event']] | {'timestamp','event'})
        self.assertEqual([r['timestamp'] for r in rows],sorted(r['timestamp'] for r in rows))
        # Independent walk of captured HTTP requests; no exporter helper is used.
        expected = []
        for body in fixture.requests:
            for item in body.get('messages',body.get('input',[])):
                if item.get('role') == 'tool': expected.append(item['content'])
                elif item.get('type') == 'function_call_output': expected.append(item['output'])
                elif item.get('role') == 'user' and str(item.get('content','')).startswith('<tool_response>REJECTED TOOL CALL'):
                    expected.append(item['content'])
        self.assertEqual([r['result'] for r in rows if r['event']=='tool_result'],expected)
        self.assertTrue(manifest['unpresented_invocations']) # terminal finish was not fed back

    def test_wire_mask_restore_restart_malformed_and_portability(self):
        f = Fixture([
            [call('read',{'path':'text','output_chars':2500}),call('read',raw='{',id='bad')],
            [call('context_configure',{'mask_at_tokens':1,'keep_recent':0})],
            [call('context_configure',{'mask_at_tokens':0})],
            [call('context_restart',{'account':'keep this','keep_recent_exchanges':0,'keep_records':[]})],
            [call('finish',{'summary':'fixture complete'})],
        ])
        rows, manifest = self.execute(f)
        self.assert_schema_and_wire(rows,manifest,f)
        reads = [r['result'] for r in rows if r['event']=='tool_result' and r['tool_name']=='read' and not r['is_error']]
        self.assertEqual(len(reads),3)
        self.assertIn('VISIBLE-CONTENT',reads[0]); self.assertNotIn('VISIBLE-CONTENT',reads[1]); self.assertIn('VISIBLE-CONTENT',reads[2])
        self.assertTrue(any(r.get('is_error') for r in rows))
        self.assertEqual(sum(r['total_tokens'] for r in rows if r['event']=='api_request'),90)
        self.assertEqual(rows[-1]['num_iterations'],5)
        dest = self.root/'relocated'; shutil.copytree(self.run,dest)
        self.assertEqual(project(dest)[0],rows)
        export_course_log(dest,self.root/'logs/run.jsonl')
        self.assertEqual(json.loads((self.root/'logs/run.manifest.json').read_text())['native_root'],'../relocated')


    def test_responses_shared_projection(self):
        f=Fixture([[call('read',{'path':'text','output_chars':10})],[call('finish',{'summary':'done'})]],'responses')
        rows,manifest=self.execute(f)
        self.assert_schema_and_wire(rows,manifest,f)
        self.assertEqual([r['prompt_tokens'] for r in rows if r['event']=='api_request'],[11,11])


    def test_retry_missing_usage_is_unknown_not_zero(self):
        f=Fixture([503,[call('finish',{'summary':'done'})]])
        with patch('mads.provider.time.sleep'):
            rows,manifest=self.execute(f)
        usage=[r for r in rows if r['event']=='api_request']
        self.assertEqual([r['iteration'] for r in usage],[1,1])
        self.assertIsNone(usage[0]['total_tokens']);self.assertEqual(usage[1]['total_tokens'],18)
        retry=next(r for r in rows if r['event']=='api_retry')
        self.assertEqual(retry['backoff_s'],1);self.assertIn('fixture transient',retry['error'])
        self.assertEqual(rows[-1]['num_iterations'],1)

    def test_billing_is_not_retried(self):
        f=Fixture([429]);cls=f.provider()
        config=dict(provider='course',model='fixture',key_env='CS2680_API_KEY',base_url='http://fixture.invalid/v1')
        with cls(self.store,config=config,retries=2) as p:
            outcome=Agent(self.repo,'task',self.run,p,self.store, session_role='reviewer').run()
        self.assertEqual(outcome['harness_outcome']['status'],'error')
        self.assertEqual(len(f.requests),1)
        rows,_=project(self.run)
        self.assertEqual(rows[-1]['reason'],'error')
        self.assertFalse(any(r['event']=='api_retry' for r in rows))

    def test_original_cli_logging_off_and_on_stdout_clean(self):
        for logging in (False,True):
            cwd=self.root/str(logging);cwd.mkdir()
            f=Fixture([[call('finish',{'summary':'proposal'})], [call('finish',{'summary':'reviewed'})]])
            with patch('mads.agent.Provider',f.provider()), contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()), contextlib.chdir(cwd):
                rc=main(['-p','Only finish the fixture.',*(['--log'] if logging else []),str(self.repo)])
            self.assertEqual(rc,0);self.assertEqual(out.getvalue(),'')
            logs=cwd/'madsLoop_logs'
            self.assertEqual(logs.exists(),logging)
            if logging:
                trace=next(logs.glob('*.jsonl'))
                manifest=json.loads(trace.with_suffix('.manifest.json').read_text())
                native=logs/manifest['native_root']
                self.assertTrue((native/'journal/events.jsonl').exists())
                self.assertEqual([r['event'] for r in map(json.loads,trace.read_text().splitlines())],['run_start','api_request','tool_call','api_request','tool_call','run_end'])

    def test_cli_from_target_cwd_and_startup_error(self):
        f=Fixture([[call('finish',{'summary':'proposal'})], [call('finish',{'summary':'reviewed'})]])
        with patch('mads.agent.Provider',f.provider()), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), contextlib.chdir(self.repo):
            self.assertEqual(main(['-p','task','--log','.']),0)
        trace=next((self.repo/'madsLoop_logs').glob('*.jsonl'))
        manifest=json.loads(trace.with_suffix('.manifest.json').read_text())
        self.assertFalse(Path(manifest['original_native_root']).is_relative_to(self.repo))
        self.assertTrue((trace.parent/manifest['native_root']/'journal/events.jsonl').exists())
        badlogs=self.root/'badlogs'
        with patch('mads.agent.Provider',side_effect=ValueError('fixture startup error')), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['-p','task','--log','--log-dir',str(badlogs),str(self.repo)]),1)
        rows=list(map(json.loads,next(badlogs.glob('*.jsonl')).read_text().splitlines()))
        self.assertEqual([r['event'] for r in rows],['run_start','run_end'])
        self.assertEqual(rows[-1]['reason'],'error'); self.assertEqual(rows[-1]['num_iterations'],0)

    def test_nested_log_destination_rejected_before_provider(self):
        with patch('mads.agent.Provider') as provider, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['-p','task','--log','--run-dir',str(self.run),'--log-dir',str(self.run/'logs'),str(self.repo)])
        provider.assert_not_called()

    def test_optional_base_url_preserves_course_and_reaches_provider(self):
        from mads.routes import route
        self.assertEqual(route()['base_url'],'https://api.cs2680.com/v1')
        f=Fixture([[call('finish',{'summary':'proposal'})], [call('finish',{'summary':'reviewed'})]])
        with patch.dict(os.environ,{'MADS_BASE_URL':'http://relay.invalid/v1'}), patch('mads.agent.Provider',f.provider()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(route()['provider'],'course')
            self.assertEqual(route()['model'],'qwen3.6-35b-a3b')
            self.assertEqual(route()['key_env'],'CS2680_API_KEY')
            self.assertEqual(main(['-p','task','--run-dir',str(self.root/'route-run'),str(self.repo)]),0)
        self.assertEqual(f.urls,['http://relay.invalid/v1/chat/completions'] * 2)

    def test_runner_forwards_base_url_by_environment_name(self):
        from mads.runner import run
        import subprocess
        checkout=self.root/'checkout'; checkout.mkdir()
        source=Path(__file__).resolve().parents[1]
        from mads.runner import stage_source
        stage_source(source, checkout)
        tasks=checkout/'tasks.json'
        tasks.write_text(json.dumps({'fixture':dict(instance_id='fixture',base_commit='abc',docker_image='owned-fixture')}))
        env={'MADSLOOP_REPO':str(checkout),'TASKS_JSON':str(tasks),'MADS_BASE_URL':'http://relay.invalid/v1'}
        with patch.dict(os.environ,env), patch('mads.runner.subprocess.call',return_value=1) as launch, patch('mads.runner.subprocess.run',return_value=subprocess.CompletedProcess([],0,stderr='')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(0),1) # mocked container never produced a patch
        args=launch.call_args.args[0]
        self.assertEqual(args[args.index('MADS_BASE_URL')-1],'-e')
        self.assertNotIn(env['MADS_BASE_URL'],args)

    def test_unused_fork_is_evidence_not_an_api_call(self):
        f=Fixture([[call('finish',{'summary':'done'})]])
        self.execute(f)
        with patch('mads.provider.Provider',f.provider()), f.provider()(self.store) as p:
            with p.fork(Store(self.run/'unused/journal')):
                pass
        rows,manifest=project(self.run)
        self.assertEqual(len(manifest['journals']),2)
        self.assertEqual(len([r for r in rows if r['event']=='api_request']),1)

if __name__=='__main__': unittest.main()
