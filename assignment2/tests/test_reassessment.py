"""Fresh completion-review mechanics, not evidence of live-model solving gains.

The actual Agent, context compiler, tools and cleanup run throughout. Scripted
provider replies choose the actions; the wire cases additionally use the real
Provider/SDK with mock HTTP, including its fork links and Course projection.
No assignment diagnosis, evaluator material or task-specific hints are used.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

from mads.agent import Agent, main as agent_main
from mads.course_logs import project
from mads.jobs import Jobs
from mads.review import GitReviewError
from mads.store import Store, dump
from mads.tools import validate
from test_course_compat import Fixture
from scripted_provider import Scripted


def call(tool_name, **arguments):
    # A tool such as environment legitimately has a `name` argument.
    return {'id':tool_name, 'type':'function',
            'function':{'name':tool_name, 'arguments':dump(arguments)}}


TASK = 'Implement the requested public behavior.\r\nPreserve literal "quotes" and café.\n'
PROPOSAL = 'Previously implemented "the helper".\r\nA public consumer may remain untested.\n'
FINAL = 'Fresh owner repaired source; a failing check remains explicitly unresolved.\n'


def messages(request):
    return request.get('messages', request.get('input', []))


def user_texts(request):
    return [item['content'] for item in messages(request) if item.get('role') == 'user']


def strings(value):
    """Find exact literals without depending on an unspecified packet nesting."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


class ScriptedTree(Scripted):
    """Only the provider is scripted; unexpected automatic children are errors."""
    def __init__(self, store, replies, children=(), observe=None):
        super().__init__(store, replies)
        self.planned_children = iter(children)
        self.children = []
        self.observe = observe

    def complete(self, request, context_id, **kwargs):
        if self.observe is not None:
            self.observe(self, request)
        return super().complete(request, context_id, **kwargs)

    def fork(self, store):
        child = ScriptedTree(store, **next(self.planned_children))
        child.config = dict(self.config)
        self.children.append(child)
        return child


class ReassessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.serial = 0
        env = patch.dict(os.environ, {
            'PATH': os.environ.get('PATH', os.defpath),
            'CS2680_API_KEY': 'offline-fixture-only',
            'MADS_TEST_KEY': 'offline-fixture-only',
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))

    def workspace(self):
        self.serial += 1
        root = self.root/str(self.serial)
        repo = root/'repo'
        repo.mkdir(parents=True)
        return repo, root/'run'

    def agent(self, replies, children=(), **options):
        repo, run = self.workspace()
        store = Store(run/'journal')
        provider = ScriptedTree(store, replies, children=children)
        agent = Agent(repo, TASK, run, provider, store, **options)
        self.addCleanup(agent.tools.jobs.close)
        return agent, provider

    def completed(self, result, claim):
        self.assertEqual(result['harness_outcome']['status'], 'completed', result)
        self.assertEqual(result['cleanup_outcome']['status'], 'completed', result)
        self.assertEqual(result['agent_outcome']['status'], 'completed', result)
        self.assertEqual(result['agent_outcome']['claim'], claim)

    def report(self, agent):
        paths = list(agent.run_root.glob('reviewer/report.json'))
        self.assertEqual(len(paths), 1, paths)
        path = paths[0]
        report = json.loads(path.read_text())
        for key in ('outcome', 'input', 'journal', 'before_review', 'after_review'):
            self.assertIn(key, report)
        self.assertEqual(Path(report['input']).resolve(), path.parent/'input.json')
        self.assertTrue(Path(report['journal']).is_file())
        packet = json.loads(Path(report['input']).read_text())
        self.assertTrue(packet['information_boundary'])
        # Task authority must not be replaced by the review request or summary.
        self.assertEqual(json.loads((path.parent/'task.json').read_text()), {'task': TASK})
        parent_manifest = json.loads((agent.run_root/'run-manifest.json').read_text())
        child_manifest = json.loads((path.parent/'run-manifest.json').read_text())
        self.assertEqual(child_manifest['task_sha256'], parent_manifest['task_sha256'])
        return path, report, packet

    def receipt(self, agent, tool_name, index=0):
        rows = [row['result'] for row in agent.store.events
                if row['event'] == 'tool_result' and row['result']['tool_name'] == tool_name]
        return rows[index]

    def test_default_has_one_fresh_final_owner_with_rich_prompt_and_exact_summary(self):
        from mads.prompting import CODING, REVIEW_RICH
        agent, provider = self.agent([
            [call('finish', summary=PROPOSAL), call('write', path='skipped', content='bad')],
            [call('write', path='must-not-resume', content='bad')]],
            children=[dict(replies=[
                [call('write', path='main.py', content='print("review edit")\n')],
                [call('finish', summary=FINAL), call('write', path='child-skipped', content='bad')]])])
        result = agent.run()
        self.completed(result, FINAL)
        self.assertEqual(result['solver_outcome'], {'status':'proposed_completion','claim':PROPOSAL})
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(provider.children), 1)
        child = provider.children[0]
        self.assertEqual(child.children, [])
        self.assertTrue(child.closed)
        self.assertEqual((agent.tools.repo/'main.py').read_text(), 'print("review edit")\n')
        for name in ('skipped','must-not-resume','child-skipped'):
            self.assertFalse((agent.tools.repo/name).exists())
        first = child.requests[0]
        self.assertEqual(user_texts(first)[0], TASK)
        self.assertIn(CODING, messages(first)[0]['content'])
        self.assertTrue(any(REVIEW_RICH in text and PROPOSAL in text for text in user_texts(first)))
        self.assertFalse(any(item.get('role') in ('assistant','tool') for item in messages(first)))
        _, report, packet = self.report(agent)
        self.assertEqual(packet['previous_summary'], PROPOSAL)
        self.assertTrue(packet['final_owner'])
        self.assertEqual(report['outcome']['agent_outcome']['claim'], FINAL)
        self.assertEqual(result['completion_origin']['kind'], 'fresh_session_review')
        self.assertEqual(json.loads((agent.run_root/'solver-result.json').read_text())['agent_outcome']['claim'], PROPOSAL)

    def test_final_owner_does_not_start_another_review(self):
        agent, provider = self.agent([[call('finish', summary=FINAL)]], session_role='reviewer')
        self.completed(agent.run(), FINAL)
        self.assertEqual(provider.children, [])
        self.assertFalse((agent.run_root/'reviewer').exists())

    def test_no_model_review_bypass_or_recursive_spawning_in_either_session(self):
        agent, provider = self.agent([
            [call('finish', summary='bypass', review='off')],
            [call('collaborate', role='spawn', instructions='do the task')],
            [call('finish', summary=PROPOSAL)]], children=[dict(replies=[
                [call('collaborate', role='spawn', instructions='another reviewer')],
                [call('finish', summary='another review', review='handoff')],
                [call('finish', summary=FINAL)]])])
        self.completed(agent.run(), FINAL)
        self.assertEqual(len(provider.children), 1)
        self.assertEqual(provider.children[0].children, [])
        self.assertEqual(self.receipt(agent, 'finish')['status'], 'rejected')
        self.assertEqual(self.receipt(agent, 'collaborate')['status'], 'rejected')
        for p in (provider, provider.children[0]):
            for request in p.requests:
                names = {t['function']['name']: t['function']['parameters'] for t in request['tools']}
                self.assertNotIn('collaborate', names)
                self.assertEqual(set(names['finish']['properties']), {'summary'})
        with self.assertRaises(ValueError): validate('finish', {'summary':'x', 'review':'off'})
        with self.assertRaises(ValueError): validate('collaborate', {'role':'x','instructions':'x'})

    def test_original_corrections_survive_but_private_predecessor_context_is_not_replayed(self):
        private = self.root/'private.txt'; private.write_text('PRIVATE_TOOL_OUTPUT')
        corrections = ['Literal later instruction\r\ncafé.\n']
        agent, provider = self.agent([
            [call('read', path=str(private))],
            [call('context_restart', account='PRIVATE_ACCOUNT')],
            [call('finish', summary=PROPOSAL)]], user_messages=corrections,
            children=[dict(replies=[[call('context_restart', account='reviewer account')],
                                   [call('finish', summary=FINAL)]])])
        corrections.append('CALLER_LIST_MUTATION')
        agent.add_user_message('Second explicit correction', source='user-message-2')
        agent.tools.task(action='note', key='early', source_ids=['T1'], claim='PRIVATE_EARLY_NOTE')
        agent.tools.verification(action='plan', id='early', requirement='Implement the requested public behavior.',
            entrypoint='program', scenario='input', expected='PRIVATE_EXPECTATION', command='true')
        self.completed(agent.run(), FINAL)
        for request in provider.children[0].requests:
            self.assertEqual(user_texts(request)[:3], [TASK, corrections[0], 'Second explicit correction'])
            for value in ('PRIVATE_TOOL_OUTPUT','PRIVATE_ACCOUNT','PRIVATE_EARLY_NOTE','PRIVATE_EXPECTATION','CALLER_LIST_MUTATION'):
                self.assertNotIn(value, dump(request))
            self.assertIn(str(agent.store.root), dump(request))
        self.assertEqual(self.report(agent)[2]['user_messages'], corrections[:1]+['Second explicit correction'])

    def test_review_repairs_and_finishes_despite_honestly_reported_failing_check(self):
        agent, provider = self.agent([[call('finish', summary=PROPOSAL)]], children=[dict(replies=[
            [call('write', path='main.py', content='print("actual edited source")\n')],
            [call('bash', command=shlex.quote(sys.executable)+' main.py; printf "failing check\\n"; exit 7')],
            [call('finish', summary=FINAL)]])])
        self.completed(agent.run(), FINAL)
        path, report, _ = self.report(agent)
        self.assertEqual((path.parent/'artifacts/jobs/job-1.out').read_text(), 'actual edited source\nfailing check\n')
        self.assertEqual(report['outcome']['cleanup_outcome']['jobs'][0]['process']['shell_exit_code'], 7)

    def test_terminal_review_failure_retains_partial_edits_and_never_returns_solver(self):
        agent, provider = self.agent([[call('finish', summary=PROPOSAL)],
                                      [call('write', path='bad-resume', content='bad')]],
            children=[dict(replies=[[call('write', path='partial', content='retained')],
                                   RuntimeError('terminal review transport failure')])])
        result = agent.run()
        self.assertEqual(result['harness_outcome']['status'], 'error')
        self.assertEqual(result['agent_outcome']['status'], 'not_completed')
        self.assertEqual(result['cleanup_outcome']['status'], 'completed')
        self.assertEqual((agent.tools.repo/'partial').read_text(), 'retained')
        self.assertFalse((agent.tools.repo/'bad-resume').exists())
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(provider.children), 1)
        self.assertIn('terminal review transport failure', dump(result))
        self.assertEqual(self.report(agent)[1]['outcome']['agent_outcome']['status'], 'not_completed')

    def test_provider_fork_failure_is_terminal_but_existing_work_can_be_exported(self):
        agent, provider = self.agent([[call('finish', summary=PROPOSAL)]])
        with patch.object(provider, 'fork', side_effect=RuntimeError('fork unavailable')) as fork:
            result = agent.run()
        self.assertEqual(result['harness_outcome']['status'], 'error')
        self.assertEqual(result['agent_outcome']['status'], 'not_started')
        self.assertEqual(result['cleanup_outcome']['status'], 'completed')
        self.assertEqual(fork.call_count, 1)
        self.assertEqual(len(provider.requests), 1)
        self.assertIn('fork unavailable', dump(result))

    def test_review_setup_failure_does_not_spawn_again_or_claim_reviewed(self):
        agent, provider = self.agent([[call('finish', summary=PROPOSAL)]])
        (agent.run_root/'reviewer').mkdir()  # Conflicting prior artifacts must not be overwritten.
        result = agent.run()
        self.assertEqual(result['harness_outcome']['status'], 'error')
        self.assertEqual(result['agent_outcome']['status'], 'not_completed')
        self.assertEqual(provider.children, [])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(result['review_outcome']['status'], 'failed_before_outcome')

    def test_review_cleanup_failure_preserves_claim_separately_and_blocks_clean_export(self):
        agent, provider = self.agent([[call('finish', summary=PROPOSAL)]],
            children=[dict(replies=[[call('write', path='partial', content='retained')],
                                   [call('finish', summary=FINAL)]])])
        original = Jobs.close
        def close(jobs):
            rows = original(jobs)
            if agent.run_root/'reviewer' in jobs.root.parents:
                rows.append({'job_id':'reported-failure','errors':['fixture cleanup failure'],
                             'process':{'state':'exited','capture_status':'complete'}})
            return rows
        with patch.object(Jobs, 'close', close): result = agent.run()
        self.assertEqual(result['agent_outcome']['claim'], FINAL)
        self.assertEqual(result['harness_outcome']['status'], 'error')
        self.assertEqual(result['cleanup_outcome']['status'], 'failed')
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual((agent.tools.repo/'partial').read_text(), 'retained')

    def test_parent_job_remains_available_during_review_and_is_cleaned_at_end(self):
        observed = []
        def observe(provider, request):
            observed.append(agent.tools.jobs.state(agent.tools.jobs.jobs[job])['state'])
        agent, provider = self.agent([[call('finish', summary=PROPOSAL)]],
            children=[dict(replies=[[call('finish', summary='proposed before shutdown')],
                                   [call('finish', summary=FINAL)]], observe=observe)])
        job = agent.tools.bash('exec sleep 60', wait_seconds=0)['process']['job_id']
        self.completed(agent.run(), FINAL)
        self.assertEqual(observed, ['running','exited'])
        self.assertIn(job, dump(provider.children[0].requests[0]))
        state = agent.tools.jobs.state(agent.tools.jobs.jobs[job])
        self.assertEqual(state['state'], 'exited')
        self.assertEqual(state['capture_status'], 'complete')
        self.assertEqual(state['termination_reason'], 'cleanup')

    def test_current_execution_routes_and_context_policies_carry_not_account(self):
        target = self.root/'other directory'; target.mkdir()
        agent, provider = self.agent([
            [call('environment', action='set', name='alternate', argv_prefix=[], cwd=str(target))],
            [call('environment', action='default', name='alternate')],
            [call('context_configure', reasoning_replay='none', output_tokens=8192,
                  mask_at_tokens=0, keep_recent=2, preview_head_percent=75)],
            [call('finish', summary=PROPOSAL)]], children=[dict(replies=[
                [call('bash', command='printf "inherited" > child-location')],
                [call('finish', summary=FINAL)]])])
        self.completed(agent.run(), FINAL)
        self.assertEqual((target/'child-location').read_text(), 'inherited')
        store = Store(agent.run_root/'reviewer/journal')
        for manifest in [r for r in store.events if r['event']=='context_manifest']:
            for key,value in dict(reasoning_replay='none', output_tokens=8192, mask_at_tokens=0,
                                  keep_recent=2, preview_head_percent=75).items():
                self.assertEqual(manifest[key], value)

    def test_no_git_does_not_prevent_review_or_repairs(self):
        agent, provider = self.agent([[call('finish', summary=PROPOSAL)]], children=[dict(replies=[
            [call('write', path='fixed', content='yes')], [call('finish', summary=FINAL)]])])
        with patch('mads.tools.Tools.review', side_effect=GitReviewError('git unavailable')):
            result = agent.run()
        self.completed(result, FINAL)
        self.assertEqual((agent.tools.repo/'fixed').read_text(), 'yes')
        self.assertIn('git unavailable', dump(self.report(agent)[1]))


    def test_native_protocols_real_cli_default_review_edit_export_and_course_projection(self):
        import shutil, subprocess
        from mads.runner import export
        from mads.prompting import CODING, REVIEW_RICH
        for protocol in ('chat_completions','responses'):
            with self.subTest(protocol=protocol):
                repo, run = self.workspace()
                subprocess.run(['git','init','-q',str(repo)], check=True)
                (repo/'source.py').write_text('value = "before"\n')
                subprocess.run(['git','-C',str(repo),'add','.'],check=True)
                subprocess.run(['git','-C',str(repo),'-c','user.name=fixture','-c','user.email=fixture@example.invalid',
                                'commit','-qm','base'],check=True)
                base=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD']).decode().strip()
                task=run.parent/'task.txt';task.write_bytes(TASK.encode())
                correction=run.parent/'later.txt';correction.write_bytes(b'Explicit correction\r\n')
                logdir=run.parent/'logs'
                f=Fixture([[call('write',path='source.py',content='value = "solver"\n')],
                           [call('finish',summary=PROPOSAL)],
                           [call('write',path='source.py',content='value = "reviewer"\n')],
                           [call('finish',summary=FINAL)]],protocol=protocol)
                cls=f.provider()
                route=(['--provider','openai','--model','gpt-5.4'] if protocol=='responses' else [])
                with patch.dict(os.environ, {'OPENAI_API_KEY':'offline-fixture-only'}), \
                        patch('mads.agent.Provider',cls), patch('mads.provider.Provider',cls), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code=agent_main([str(repo),'--task',str(task),'--user-message-file',str(correction),
                                     '--run-dir',str(run),'--log','--log-dir',str(logdir),*route])
                self.assertEqual(code,0)
                self.assertEqual(len(f.requests),4)
                for request in f.requests:
                    self.assertEqual(user_texts(request)[:2],[TASK,'Explicit correction\r\n'])
                    self.assertIn(CODING,messages(request)[0]['content'])
                self.assertTrue(any(REVIEW_RICH in text and PROPOSAL in text for text in user_texts(f.requests[2])))
                self.assertFalse(any(m.get('role') in ('assistant','tool') or m.get('type')=='function_call_output'
                                     for m in messages(f.requests[2])))
                self.assertEqual(export(repo,base,run),0)
                self.assertIn('+value = "reviewer"',(run/'patch.diff').read_text())
                self.assertNotIn('+value = "solver"',(run/'patch.diff').read_text())
                outcome=json.loads((run/'result.json').read_text())
                self.assertFalse(outcome['export_outcome']['partial_work'])
                rows,manifest=project(run)
                self.assertEqual(len([r for r in rows if r['event']=='api_request']),4)
                # Portable log copy must preserve both explicitly linked journals.
                copied=next((logdir/'evidence').iterdir())
                self.assertEqual(len([r for r in project(copied)[0] if r['event']=='api_request']),4)
                child_events=Store(run/'reviewer/journal').events
                self.assertEqual(len([r for r in Store(run/'journal').events if r['event']=='provider_fork']),1)
                self.assertFalse(any(r['event']=='provider_fork' for r in child_events))

    def test_real_http_terminal_review_failure_is_not_retried_or_exported_as_success(self):
        repo,run=self.workspace()
        f=Fixture([[call('write',path='solver-work',content='retained')],
                   [call('finish',summary=PROPOSAL)],402])
        cls=f.provider()
        with patch('mads.agent.Provider',cls),patch('mads.provider.Provider',cls), \
                contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            code=agent_main([str(repo),'-p',TASK,'--run-dir',str(run)])
        self.assertEqual(code,1)
        self.assertEqual(len(f.requests),3)
        self.assertEqual((repo/'solver-work').read_text(),'retained')
        result=json.loads((run/'result.json').read_text())
        self.assertEqual(result['agent_outcome']['status'],'not_completed')
        self.assertEqual(result['harness_outcome']['status'],'error')
        self.assertEqual(result['cleanup_outcome']['status'],'completed')
        self.assertEqual(len([r for r in Store(run/'journal').events if r['event']=='provider_fork']),1)

    def test_inherited_handles_poll_send_cancel_and_launch_attribution_are_stable(self):
        agent, provider = self.agent([[call('finish', summary=PROPOSAL)]], children=[dict(replies=[
            [call('poll', job_id='job-1', wait_seconds=0)],
            [call('send', job_id='job-1', text='hello\n')],
            [call('poll', job_id='job-1', wait_seconds=.15)],
            [call('bash', command='printf reviewer-output')],
            [call('poll', job_id='job-1', wait_seconds=0)],
            [call('cancel', job_id='job-1')],
            [call('finish', summary=FINAL)]])])
        code = 'import sys,time; print("ready",flush=True); print(sys.stdin.readline().strip(),flush=True); time.sleep(60)'
        first=agent.tools.bash('exec '+shlex.quote(sys.executable)+' -u -c '+shlex.quote(code),wait_seconds=.15)
        pid=first['process']['initial_pid']
        self.completed(agent.run(), FINAL)
        child=Store(agent.run_root/'reviewer/journal')
        rows=[r['result'] for r in child.events if r['event']=='tool_result']
        polls=[r for r in rows if r['tool_name']=='poll']
        self.assertTrue(all(r['status'] in ('running','completed') for r in polls))
        self.assertTrue(all(r['process']['initial_pid']==pid for r in polls))
        self.assertTrue(all(r['process']['launch_origin']['journal']==str(agent.store.root) for r in polls))
        self.assertTrue(all(r['process']['controller_journal']==str(child.root) for r in polls))
        launched=next(r for r in rows if r['tool_name']=='bash')
        self.assertEqual(launched['process']['job_id'],'job-2')
        self.assertEqual(launched['process']['launch_origin']['journal'],str(child.root))
        self.assertNotEqual(launched['process']['initial_pid'],pid)
        # Observations and exact blobs are local to the observer, even for inherited jobs.
        for r in polls:
            self.assertEqual(child.history(r['observation_record'])['artifact_id'],r['output']['artifact_id'])
        self.assertTrue(any('hello' in Path(r['output']['path']).read_text() for r in polls))
        self.assertEqual(agent.tools.jobs.state(agent.tools.jobs.jobs['job-1'])['termination_reason'],'cancelled')

    def test_teardown_mutation_returns_to_same_reviewer_before_final_claim_and_export(self):
        import subprocess
        from mads.runner import export
        agent, provider = self.agent([[call('finish', summary=PROPOSAL)]], children=[dict(replies=[
            [call('write', path='result.txt', content='reviewed correct output')],
            [call('bash', command='test "$(cat result.txt)" = "reviewed correct output"')],
            [call('finish', summary='Verified before shutdown')],
            [call('read', path='result.txt')],
            [call('write', path='result.txt', content='reviewed correct output')],
            [call('bash', command='test "$(cat result.txt)" = "reviewed correct output"')],
            [call('finish', summary='Verified after shutdown effects')]])])
        repo=agent.tools.repo
        (repo/'result.txt').write_text('baseline')
        subprocess.run(['git','init','-q',str(repo)],check=True)
        subprocess.run(['git','-C',str(repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(repo),'-c','user.name=fixture','-c','user.email=fixture@example.invalid',
                        'commit','-qm','base'],check=True)
        base=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD']).decode().strip()
        server='import signal,time\nfrom pathlib import Path\ndef stop(*a):\n Path("result.txt").write_text("stale solver output")\n raise SystemExit(0)\nsignal.signal(signal.SIGTERM,stop)\nprint("ready",flush=True)\nwhile True: time.sleep(.05)'
        agent.tools.bash('exec '+shlex.quote(sys.executable)+' -u -c '+shlex.quote(server),wait_seconds=.2)
        result=agent.run()
        self.completed(result,'Verified after shutdown effects')
        self.assertEqual(len(provider.requests),1)
        self.assertEqual(len(provider.children),1)
        self.assertEqual(provider.children[0].children,[])
        child=Store(agent.run_root/'reviewer/journal')
        receipts=[r['result'] for r in child.events if r['event']=='tool_result']
        proposed=next(r for r in receipts if r['tool_name']=='finish')
        self.assertEqual(proposed['completion'],'deferred_for_process_settlement')
        read=next(r for r in receipts if r['tool_name']=='read')
        self.assertEqual(Path(read['output']['path']).read_text(),'stale solver output')
        self.assertEqual((repo/'result.txt').read_text(),'reviewed correct output')
        self.assertEqual(export(repo,base,agent.run_root),0)
        patch=(agent.run_root/'patch.diff').read_text()
        self.assertIn('+reviewed correct output',patch)
        self.assertNotIn('+stale solver output',patch)

    def test_user_arrival_during_response_skips_stale_calls_in_same_reviewer(self):
        agent, provider = self.agent([[call('finish', summary=PROPOSAL)]],children=[dict(replies=[
            [call('write',path='outdated',content='must not execute'),call('finish',summary='outdated')],
            [call('write',path='corrected',content='current task'),call('finish',summary=FINAL)]])])
        original=agent.new_session
        def new_session(*args,**kwargs):
            child=original(*args,**kwargs)
            complete=child.provider.complete
            def receive(*a,**k):
                response=complete(*a,**k)
                if len(child.provider.requests)==1:
                    child.add_user_message('Explicit correction during response',source='fixture-user')
                return response
            child.provider.complete=receive
            return child
        agent.new_session=new_session
        self.completed(agent.run(),FINAL)
        self.assertEqual(len(provider.children),1)
        child=provider.children[0]
        self.assertEqual(len(child.requests),2)
        self.assertIn('Explicit correction during response',user_texts(child.requests[1]))
        self.assertFalse((agent.tools.repo/'outdated').exists())
        self.assertEqual((agent.tools.repo/'corrected').read_text(),'current task')


if __name__ == '__main__':
    unittest.main()
