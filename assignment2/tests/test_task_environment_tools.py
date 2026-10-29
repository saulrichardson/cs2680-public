import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mads.agent import Agent
from mads.reassessment import run_review
from mads.store import Store, dump
from mads.task_state import TaskState
from mads.tools import Tools, validate
from mads.verification import Intentions
from scripted_provider import Scripted, call


class TaskEnvironmentToolsTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name); self.repo=self.root/'repo'; self.repo.mkdir()
        self.run=self.root/'run'; self.store=Store(self.run/'journal')
        self.tools=Tools(self.repo,self.run,self.store,original_task='FIRST behavioral requirement\n\nSECOND interface')
        self.addCleanup(self.tools.jobs.close)

    def test_source_units_are_lossless_utf8_and_focus_is_not_replacement(self):
        for text in ['', '\n\n# Heading\n\n😄\r\n\r\nlast', 'x', '\n', 'abc\n\n\n```\n\na\n```\n']:
            state=TaskState(text,self.root/'task')
            data=text.encode()
            self.assertEqual(''.join(u['text'] for u in state.units),text)
            for unit in state.units:
                a,b=unit['bytes'];self.assertEqual(data[a:b].decode(),unit['text'])
        s=self.tools.task_state
        s.note('caller',['T1'],'Actual caller not yet traced',evidence='')
        packet=s.focus('focus',['T2'])
        self.assertEqual(packet['omitted_source_ids'],['T1'])
        self.assertIn('caller',packet['notes'])
        packet['notes']['caller']['claim']='tampered'
        self.assertNotEqual(s.notes['caller']['claim'],'tampered')
        self.assertEqual(self.tools.task()['source_units'][0]['id'],'T1')
        with self.assertRaises(ValueError): s.focus('focus',['unknown'])
        self.assertEqual(s.focus_ids,['T2'])

    def test_note_fields_cannot_silently_become_a_read(self):
        self.tools.task(action='note', key='plan', claim='Inspect the handler')
        update = dict(key='plan', claim='Trace the consumer', state='contradicted')
        before = (self.run/'task/state.json').read_bytes()
        for action in ({}, {'action': 'show'}):
            validate('task', {**update, **action})
            with self.assertRaisesRegex(ValueError, 'action="note"'):
                self.tools.task(**update, **action)
            self.assertEqual((self.run/'task/state.json').read_bytes(), before)
        receipt = self.tools.task(action='note', **update)
        self.assertEqual(receipt['receipt'], "Saved task note 'plan'.")
        self.assertEqual(receipt['note']['claim'], update['claim'])
        self.assertEqual(receipt['note']['state'], 'contradicted')
        self.assertEqual(self.tools.task()['notes']['plan']['claim'], update['claim'])
        with self.assertRaises(ValueError):
            self.tools.task(action='remove', key='plan', claim='ignored')
        self.assertIn('plan', self.tools.task()['notes'])

    def test_recovered_note_update_reaches_next_request_and_restart(self):
        task = 'Reject obsolete operations.\n\nPreserve valid operations.'
        p = Scripted(self.store, [
            [call('task', action='note', key='plan', claim='Inspect handler')],
            [call('task', key='plan', claim='Trace public result')],
            [call('task', action='note', key='plan', source_ids=['T1'],
                  claim='Trace public result', state='contradicted', evidence='Consumer drops the result')],
            [call('context_restart', account='Investigate the consumer', keep_records=[])],
            [call('finish', summary='Unresolved consumer behavior')]])
        agent = Agent(self.repo, task, self.run, p, self.store, session_role='reviewer')
        agent.run()
        results = [r['result'] for r in self.store.events
                   if r['event'] == 'tool_result' and r['result']['tool_name'] == 'task']
        self.assertEqual(results[1]['status'], 'rejected')
        self.assertEqual(results[1]['effects'], 'none')
        self.assertEqual(results[2]['note']['claim'], 'Trace public result')
        for request in (p.requests[3], p.requests[4]):
            self.assertEqual(request['messages'][1]['content'], task)
            self.assertIn('Consumer drops the result', request['messages'][-1]['content'])
            self.assertIn('contradicted', request['messages'][-1]['content'])

    def test_environment_previews_do_not_probe_launch_or_change_configuration(self):
        absent=self.root/'missing executable'
        missing_cwd=str(self.root/"not created ' $HOME")
        with patch('subprocess.Popen',side_effect=AssertionError('preview launched a process')) as popen:
            configured=self.tools.environment('set','unchecked',argv_prefix=[str(absent)],cwd=missing_cwd)
            self.assertEqual(configured['default'],'host')
            self.assertEqual(configured['launch_preview']['target'],'unchecked')
            selected=self.tools.environment('default','unchecked')
            self.assertEqual(selected['default'],'unchecked')
            for result in (configured,selected):
                sample=result['launch_preview']
                self.assertTrue(sample['sample_command']);self.assertFalse(sample['executed'])
                self.assertEqual(sample['command'],'pwd')
                self.assertEqual(sample['argv'][:2],[str(absent),'bash'])
                self.assertEqual(sample['target_cwd'],missing_cwd)
            before=self.tools.execution.snapshot();events=list(self.store.events)
            command='printf side-effect > PREVIEW_COMMAND_RAN'
            validate('environment',{'action':'preview','command':command})
            preview=self.tools.environment('preview',command=command)['launch_preview']
            self.assertEqual(preview['target'],'unchecked')
            self.assertEqual(preview['command'],command)
            self.assertFalse(preview['sample_command']);self.assertFalse(preview['executed'])
            self.assertEqual(self.tools.effects,'none')
            host=self.tools.environment('preview','host',command='')['launch_preview']
            self.assertEqual(host['argv'],['bash','-o','pipefail','-c',''])
            self.assertFalse(host['sample_command']);self.assertIsNone(host['target_cwd'])
            self.assertEqual(self.tools.environment()['default'],'unchecked')
            self.assertEqual(self.tools.execution.snapshot(),before)
            self.assertEqual(self.store.events,events)
            popen.assert_not_called()
        self.assertEqual(len(events),2)
        self.assertTrue(all(row['event']=='execution_configuration' for row in events))
        self.assertFalse(self.tools.jobs.jobs)
        self.assertFalse(Path(missing_cwd).exists());self.assertFalse(absent.exists())
        self.assertFalse((self.repo/'PREVIEW_COMMAND_RAN').exists())

    def test_environment_preview_matches_actual_local_bash_and_launch_evidence(self):
        target=self.root/"local ' $(touch CWD_INJECTED)\nπ";target.mkdir()
        self.tools.environment('set','local',argv_prefix=[],cwd=str(target))
        self.tools.environment('default','local')
        command='pwd -P; printf "%s|%s\\n" "$0" "$#"; printf created > ACTUALLY_RAN'
        preview=self.tools.environment('preview',command=command)['launch_preview']
        self.assertEqual(preview['argv'][:4],['bash','-o','pipefail','-c'])
        self.assertEqual(preview['target_cwd'],str(target))
        self.assertEqual(preview['host_cwd'],str(self.repo.resolve()))
        self.assertFalse((target/'ACTUALLY_RAN').exists());self.assertFalse(self.tools.jobs.jobs)
        actual=self.tools.bash(command)
        self.assertEqual(actual['process']['shell_exit_code'],0)
        self.assertEqual(actual['process']['execution_target'],'local')
        self.assertEqual(Path(actual['process']['live_path']).read_text(),str(target.resolve())+'\nbash|0\n')
        self.assertEqual((target/'ACTUALLY_RAN').read_text(),'created')
        self.assertFalse((self.repo/'CWD_INJECTED').exists())
        self.assertFalse((target/'CWD_INJECTED').exists())
        started=next(row for row in self.store.events if row['event']=='job_started')
        for key in ('target','definition','host_cwd','argv','process_scope'):
            self.assertEqual(started['launch'][key],preview[key])

    def test_invalid_preview_arguments_do_not_mutate_routes(self):
        self.tools.environment('set','existing',cwd=str(self.repo))
        before=self.tools.execution.snapshot();events=list(self.store.events)
        for action in ('list','set','default','remove'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError,'only for.*preview'):
                self.tools.environment(action,'existing',command='true')
            self.assertEqual(self.tools.effects,'none')
        with self.assertRaises(ValueError):
            self.tools.environment('preview','unknown')
        with self.assertRaises(ValueError):
            validate('environment',{'action':'preview','command':7})
        self.assertEqual(self.tools.execution.snapshot(),before)
        self.assertEqual(self.store.events,events)
        self.assertFalse(self.tools.jobs.jobs)

    def test_real_tools_route_state_stdin_and_host_override(self):
        target=self.root/"target space's";target.mkdir()
        validate('environment',{'action':'set','name':'build','cwd':str(target)})
        self.tools.environment('set','build',cwd=str(target))
        self.tools.environment('default','build')
        launched=self.tools.bash('pwd; read value; printf "received=%s\\n" "$value"',wait_seconds=0)
        job=launched['process']['job_id']
        self.tools.send(job,'hello\n',close=True)
        got=self.tools.poll(job,wait_seconds=2)
        text=Path(got['process']['live_path']).read_text()
        self.assertIn(str(target),text);self.assertIn('received=hello',text)
        self.assertEqual(got['process']['execution_target'],'build')
        records=[x for x in self.store.events if x['event']=='job_started']
        self.assertEqual(records[0]['launch']['definition']['cwd'],str(target))
        self.tools.environment('set','build',cwd=str(self.repo))
        self.assertEqual(records[0]['launch']['definition']['cwd'],str(target))
        host=self.tools.bash('pwd',target='host')
        self.assertEqual(Path(host['process']['live_path']).read_text().strip(),str(self.repo.resolve()))

    def test_agent_dispatch_exposes_preview_schema_and_read_only_receipt(self):
        def environment_call(**arguments):
            invocation=call('environment')
            invocation['function']['arguments']=dump(arguments)
            return invocation
        p=Scripted(self.store, [
            [environment_call(action='set',name='local',argv_prefix=[],cwd=str(self.repo))],
            [environment_call(action='default',name='local')],
            [environment_call(action='preview',command='touch MUST_NOT_RUN')],
            [call('finish',summary='preview is not an executed probe')]])
        a=Agent(self.repo,'Inspect the launch without executing it',self.run,p,self.store, session_role='reviewer')
        result=a.run()
        self.assertEqual(result['harness_outcome']['status'],'completed')
        schema=next(t['function'] for t in p.requests[0]['tools'] if t['function']['name']=='environment')
        self.assertIn('preview',schema['parameters']['properties']['action']['enum'])
        self.assertIn('command',schema['parameters']['properties'])
        receipts=[row['result'] for row in self.store.events if row['event']=='tool_result'
                  and row['result']['tool_name']=='environment']
        self.assertEqual([r['effects'] for r in receipts],
                         ['execution_configuration_changed','execution_configuration_changed','none'])
        self.assertTrue(all(r['status']=='completed' for r in receipts))
        self.assertTrue(all(not r['launch_preview']['executed'] for r in receipts))
        self.assertEqual([r['launch_preview']['sample_command'] for r in receipts],[True,True,False])
        self.assertEqual(receipts[-1]['launch_preview']['argv'][-1],'touch MUST_NOT_RUN')
        self.assertFalse((self.repo/'MUST_NOT_RUN').exists())
        self.assertFalse(any(row['event']=='job_started' for row in self.store.events))
        self.assertIn('launch_preview',dump(p.requests[-1]))

    def test_compiled_task_lens_near_action_and_survives_restart(self):
        task='Do a behavior\n\nNamed interface'
        p=Scripted(self.store, [[call('task',action='note',key='behavior',source_ids=['T1'],claim='still open')],
                               [call('context_restart',account='Next action',keep_records=[],keep_recent_exchanges=0)],
                               [call('finish',summary='not a correctness certificate')]])
        a=Agent(self.repo,task,self.run,p,self.store, session_role='reviewer')
        result=a.run()
        self.assertEqual(result['harness_outcome']['status'],'completed')
        request=p.requests[-1]
        self.assertEqual(request['messages'][1]['content'],task)
        tail=request['messages'][-1]['content']
        self.assertIn('task_source',tail);self.assertIn('still open',tail)
        self.assertIn('Next action',tail)
        self.assertIn('execution_targets',tail)

    def test_current_route_configuration_inherited_by_same_model_child(self):
        target=self.root/'child-target';target.mkdir()
        p=Scripted(self.store,[],[[call('bash',command='pwd')],[call('finish',summary='done')]])
        a=Agent(self.repo,'TASK',self.run,p,self.store, session_role='reviewer')
        a.tools.environment('set','build',cwd=str(target));a.tools.environment('default','build')
        result=run_review(a,'Inspect the actual current directory')
        self.assertEqual(result['child_outcome']['harness_outcome']['status'],'completed')
        journal=Path(result['child_journal']);rows=[json.loads(x) for x in journal.read_text().splitlines()]
        started=next(x for x in rows if x['event']=='job_started')
        self.assertEqual(started['launch']['target'],'build')
        self.assertEqual(Path(started['output_path']).read_text().strip(),str(target))
        a.tools.jobs.close()

    def test_verification_uses_recorded_target_not_new_default(self):
        target=self.root/'verify-target';target.mkdir()
        self.tools.environment('set','verify',cwd=str(target))
        intentions=Intentions(self.run/'verification',self.tools.original_task)
        intentions.action(self.tools,'plan',id='x',requirement='FIRST behavioral requirement',
                          entrypoint='bash',scenario='cwd',expected=str(target),command='pwd',target='verify')
        got=intentions.action(self.tools,'run',id='x')
        self.assertEqual(Path(got['process']['live_path']).read_text().strip(),str(target))
        self.assertEqual(intentions.rows['x']['runs'][0]['execution_target'],'verify')

if __name__=='__main__':unittest.main()
