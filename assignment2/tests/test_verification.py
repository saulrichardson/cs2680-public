"""Contract tests exercise shared Agent, tools, real source execution and restart.
These establish mechanics, not a live model's coverage or understanding.
"""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from mads.agent import Agent
from mads.store import Store, dump
from scripted_provider import Scripted, call

TASK='The application must print requested. Preserve other behavior.'
PLAN=dict(id='public',requirement='The application must print requested.',entrypoint='python main.py',scenario='Start the program',expected='stdout requested',command='python main.py')

class IntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.repo=self.root/'repo'; self.repo.mkdir()
        (self.repo/'main.py').write_text('print("original")\n')
        self.run=self.root/'run'; self.store=Store(self.run/'journal')
    def agent(self,p):
        return Agent(self.repo,TASK,self.run,p,self.store,session_role='reviewer')
    def test_real_execution_revision_and_replacement(self):
        p=Scripted(self.store,[
            [call('verification',action='plan',**PLAN)],
            [call('verification',action='run',id='public')],
            [call('verification',action='interpret',id='public',assessment='contradicted',evidence='captured job-1 stdout',reason='original is not requested')],
            [call('write',path='main.py',content='print("requested")\n')],
            [call('context_restart',account='Replacement account, not a test verdict',keep_records=[])],
            [call('verification',action='run',id='public')],
            [call('verification',action='interpret',id='public',assessment='supported',evidence='job-2 stdout',reason='Actual program now prints requested')],
            [call('finish',summary='done')]])
        a=self.agent(p); result=a.run()
        self.assertEqual(result['harness_outcome']['status'],'completed')
        self.assertEqual(result['cleanup_outcome']['status'],'completed')
        self.assertIn('stdout requested',dump(p.requests[5]))
        self.assertIn('contradicted',dump(p.requests[5]))
        self.assertEqual((self.run/'artifacts/jobs/job-1.out').read_text(),'original\n')
        self.assertEqual((self.run/'artifacts/jobs/job-2.out').read_text(),'requested\n')
        state=json.loads((self.run/'verification/current.json').read_text())
        self.assertEqual(len(state['intentions'][0]['runs']),2)
        self.assertEqual(state['intentions'][0]['interpretation']['assessment'],'supported')
        self.assertEqual(len(a.tools.intentions.packet()['intentions'][0]['runs']),1)
        self.assertEqual(a.tools.intentions.packet()['intentions'][0]['execution_count'],2)
    def test_grounding_and_revision_never_erase_old_expectation(self):
        p=Scripted(self.store,[]); a=self.agent(p); t=a.tools
        with self.assertRaises(ValueError): t.verification(action='plan',**{**PLAN,'requirement':'invented'})
        t.verification(action='plan',**PLAN)
        with self.assertRaises(ValueError): t.verification(action='revise',**PLAN)
        with self.assertRaises(ValueError): t.verification(action='interpret',id='public',assessment='supported',evidence='none',reason='belief')
        with self.assertRaises(ValueError): t.verification(action='run',id='public',command='echo cheating')
        t.verification(action='revise',reason='Add newline precision, preserving task expectation',**{**PLAN,'expected':'requested followed by newline'})
        history=[json.loads(line) for line in (self.run/'verification/events.jsonl').read_text().splitlines()]
        self.assertEqual(history[-1]['previous']['expected'],'stdout requested')
        self.assertEqual(history[-1]['current']['version'],2)
        t.jobs.close()
    def test_uncited_task_text_survives_context_replacement(self):
        task='The application must print requested.\nPreserve other behavior.'
        p=Scripted(self.store,[[call('verification',action='plan',**PLAN)],
            [call('context_restart',account='implemented helper',keep_records=[])],
            [call('finish',summary='pending')]])
        a=Agent(self.repo,task,self.run,p,self.store,session_role='reviewer')
        a.run()
        self.assertEqual(a.tools.intentions.uncited_lines(),[{'line':2,'text':'Preserve other behavior.'}])
        self.assertIn('uncited_original_task_lines',dump(p.requests[-1]))
        self.assertIn('Preserve other behavior.',dump(p.requests[-1]))
    def test_pending_is_not_all_green_gate(self):
        p=Scripted(self.store,[[call('verification',action='plan',**PLAN)],[call('finish',summary='unresolved')]])
        result=self.agent(p).run()
        self.assertEqual(result['agent_outcome']['claim'],'unresolved')

    def test_source_ids_preserve_text_and_accept_identical_run_command(self):
        task = 'Preserve the CLI.\n\nThe application must print requested.\n'
        plan = {**PLAN, 'source_ids': ['T2'], 'requirement': 'Observe public stdout'}
        p = Scripted(self.store, [
            [call('verification', action='plan', **plan)],
            [call('verification', action='run', id='public', command=PLAN['command'])],
            [call('context_restart', account='Output is still wrong', keep_records=[])],
            [call('finish', summary='The public output remains unresolved')]])
        agent = Agent(self.repo, task, self.run, p, self.store, session_role='reviewer')
        result = agent.run()
        self.assertEqual(result['harness_outcome']['status'], 'completed')
        row = agent.tools.intentions.rows['public']
        self.assertEqual(row['requirement'], 'Observe public stdout')
        self.assertEqual(row['requirement_source']['source_ids'], ['T2'])
        self.assertEqual(row['requirement_source']['text'], 'The application must print requested.\n')
        self.assertEqual(agent.tools.intentions.uncited_lines(), [{'line': 1, 'text': 'Preserve the CLI.'}])
        self.assertEqual((self.run/'artifacts/jobs/job-1.out').read_text(), 'original\n')
        self.assertEqual(len(row['runs']), 1)
        self.assertIn('Observe public stdout', dump(p.requests[-1]))
        self.assertIn('The application must print requested.', dump(p.requests[-1]))

    def test_source_ids_validate_before_mutation_and_need_no_transcription(self):
        agent = self.agent(Scripted(self.store, []))
        self.addCleanup(agent.tools.jobs.close)
        plan = {k: v for k, v in PLAN.items() if k != 'requirement'}
        for ids in ([], ['missing'], ['T1', 'T1']):
            with self.assertRaises(ValueError):
                agent.tools.verification(action='plan', source_ids=ids, **plan)
            self.assertEqual(agent.tools.intentions.rows, {})
        agent.tools.verification(action='plan', source_ids=['T1'], **plan)
        self.assertEqual(agent.tools.intentions.rows['public']['requirement'], TASK)
        before = json.loads((self.run/'verification/current.json').read_text())
        with self.assertRaises(ValueError):
            agent.tools.verification(action='run', id='public', command='touch SHOULD_NOT_EXIST')
        self.assertFalse((self.repo/'SHOULD_NOT_EXIST').exists())
        self.assertEqual(json.loads((self.run/'verification/current.json').read_text()), before)
        agent.tools.verification(action='revise', source_ids=['T1'], reason='Clarify the observed line',
                                 **{**plan, 'expected': 'requested followed by newline'})
        event = agent.tools.intentions.events[-1]
        self.assertEqual(event['previous']['version'], 1)
        self.assertEqual(event['current']['version'], 2)
        self.assertEqual(event['previous']['expected'], PLAN['expected'])

    def test_partial_revision_keeps_source_and_command_but_not_old_execution(self):
        agent = self.agent(Scripted(self.store, []))
        self.addCleanup(agent.tools.jobs.close)
        tools = agent.tools
        tools.verification(action='plan', source_ids=['T1'], **PLAN)
        tools.verification(action='run', id=PLAN['id'])
        tools.verification(action='interpret', id=PLAN['id'], assessment='contradicted',
                           evidence='Captured output was original', reason='Task requests requested')
        tools.verification(action='revise', id=PLAN['id'], expected='requested followed by newline',
                           reason='Clarify the expected stdout without dropping the task')
        event = tools.intentions.events[-1]
        old, new = event['previous'], event['current']
        for key in ('requirement', 'requirement_source', 'command', 'entrypoint', 'scenario', 'target'):
            self.assertEqual(new[key], old[key])
        self.assertEqual(new['version'], 2)
        self.assertEqual(old['interpretation']['assessment'], 'contradicted')
        self.assertEqual(len(old['runs']), 1)
        self.assertEqual(new['runs'], [])
        self.assertIsNone(new['interpretation'])
        tools.verification(action='revise', id=PLAN['id'], command='printf requested',
                           reason='Exercise a different command explicitly')
        self.assertEqual(tools.intentions.rows[PLAN['id']]['command'], 'printf requested')
