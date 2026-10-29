"""Real processes, saved requests and immutable evidence for model-owned control."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from src import workspace
from src.teamharness.agents import AgentManager
from src.teamharness.main import Investigation
from src.teamharness.session import Session
from src.teamharness.tools import ToolRuntime
from test_session import Script, answer, tools, SHELL
from test_main_flow import repository, finish, audit_requests, last_tool
from receipt_fixture import original_receipt


class ExplicitInvestigation(unittest.TestCase):
    def test_main_prose_empty_and_literal_paged_result_then_explicit_finish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = Script([answer('Narration, not a submission.'), answer(''),
                tools(('exec_command', {'command': "printf 'first\\nsecond\\n'"})),
                tools(('finish_role', {'outcome': 'complete', 'report': 'Explicit return'}))])
            def execute(name, args, identity):
                result = subprocess.run(['/bin/bash', '-c', args['command']], capture_output=True, text=True)
                return {'exit_code': result.returncode, 'output': result.stdout, 'long': 'é' * 20000}
            session = Session(root/'session', 'main', 'system', 'task', script, [SHELL], execute, root)
            result = session.run()
            self.assertEqual(result['report'], 'Explicit return')
            self.assertEqual(len(script.requests), 4)
            content = next(m['content'] for m in script.requests[-1]['messages'] if m['role'] == 'tool')
            self.assertIn('first\nsecond\n', content)
            self.assertIn('Page bytes', content)
            self.assertNotIn('first\\nsecond', content)
            self.assertEqual(original_receipt(content)['long'], 'é' * 20000)
            path = Path(content.splitlines()[2].removeprefix('Full readable result: '))
            self.assertIn('é' * 20000, path.read_text())
            self.assertEqual(len(audit_requests(session.database)), 4)

    def test_job_model_deadline_kills_without_polling_and_default_keeps_running(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ws = {'root': str(root), 'state': str(root/'state')}
            timed = workspace.dispatch('shell', {'workspace': ws, 'command': 'sleep 30 & wait',
                'yield_time_ms': 0, 'kill_after_ms': 150})
            ordinary = workspace.dispatch('shell', {'workspace': ws, 'command': 'sleep 30', 'yield_time_ms': 0})
            try:
                end = time.monotonic()+5
                # Inspect persisted completion, not a control or poll driving cancellation.
                result_path = root/'state/jobs'/timed['id']/'result.json'
                while not result_path.exists() and time.monotonic() < end:
                    time.sleep(.03)
                result = json.loads(result_path.read_text())
                self.assertEqual(result['exit_code'], -9)
                self.assertTrue(result['cancellation']['signal_sent'])
                observed = workspace.observe(ws, timed['id'])
                self.assertGreater(observed['elapsed_seconds'], .1)
                live = workspace.observe(ws, ordinary['id'])
                self.assertEqual(live['state'], 'running')
                self.assertIsNone(live['cancel_at'])
            finally:
                workspace.dispatch('job', {'workspace': ws, 'id': ordinary['id'], 'action': 'kill', 'yield_time_ms': 1000})

    def test_fresh_start_and_checkpoint_preserve_parent_and_clean_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = repository(root)
            (repo/'pyproject.toml').write_text('[project]\nname="fixture"\n')
            scripts = []
            def provider(_):
                script = Script([finish()]); scripts.append(script); return script
            manager = AgentManager(root/'run/agents', provider,
                run_facts_provider=lambda: {'elapsed_seconds': 100, 'run_cap_seconds': 14400,
                    'active_tasks': 26, 'shared_gpu_count': 1})
            workflow = Investigation(manager, root/'run')
            try:
                result = workflow.run('Repair the answer.', repo)
                manager.automatic_wakes_enabled = False
                main = result['agents']['main']
                d = manager.descriptor(main)
                runtime = ToolRuntime(manager, main, d)
                self.assertIn('/languages/python.md', d['system'])
                self.assertIn('active tasks: 26', str(scripts[0].requests))
                (repo/'answer.py').write_text('checkpoint source\n')
                cp = manager.checkpoint(main)
                (repo/'answer.py').write_text('current failed source\n')
                for choice, expected in [('starting', 'def answer(n):\n    return -1\n'), ('checkpoint', 'checkpoint source\n')]:
                    args = {'role': 'investigator', 'task': 'An independent attempt.', 'workspace': 'private', 'source': choice}
                    if choice == 'checkpoint':
                        args['checkpoint_id'] = cp['checkpoint_id']
                    receipt = runtime('spawn_agent', args, choice)
                    child = receipt['agent_id']
                    manager.wait(child)
                    desc = manager.descriptor(child)
                    self.assertEqual((Path(desc['workspace'])/'answer.py').read_text(), expected)
                    self.assertEqual((repo/'answer.py').read_text(), 'current failed source\n')
                    request = audit_requests(Path(desc['root'])/'session.sqlite3')[0]
                    self.assertIn('Repair the answer.', str(request))
                    self.assertNotIn('current failed source', str(request))
                    self.assertNotIn('checkpoint source', str(request))
                # A specialist's checkpoint is based on its own starting snapshot,
                # not necessarily the task baseline. It must be selectable too.
                specialist = manager.create('investigator', 'Private work.', repo, private=True, parent=main)
                sd = manager.descriptor(specialist)
                self.assertNotEqual(sd['base'], workflow.report['frozen']['revision'])
                private = Path(sd['workspace'])
                (private/'answer.py').write_text('specialist checkpoint\n')
                scp = manager.checkpoint(specialist)
                (private/'answer.py').write_text('specialist later\n')
                sr = ToolRuntime(manager, specialist, sd)
                spawned = sr('spawn_agent', {'role':'investigator', 'task':'Independent checkpoint attempt.',
                    'workspace':'private', 'source':'checkpoint', 'checkpoint_id':scp['checkpoint_id']}, 'specialist-checkpoint')
                manager.wait(spawned['agent_id'])
                selected = Path(manager.descriptor(spawned['agent_id'])['workspace'])
                self.assertEqual((selected/'answer.py').read_text(), 'specialist checkpoint\n')
                self.assertEqual((private/'answer.py').read_text(), 'specialist later\n')
                rejected = runtime('spawn_agent', {'role':'investigator','task':'bad','workspace':'shared','source':'starting'}, 'bad')
                self.assertIn('requires a private workspace', rejected['error'])
            finally:
                manager.shutdown()

    def test_grade_trend_and_reuse_reference_exact_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = repository(root)
            script = Script([finish()])
            manager = AgentManager(root/'run/agents', lambda _: script)
            calls = []
            def grade(candidate):
                calls.append(candidate['sha256'])
                return {'candidate_sha256': candidate['sha256'], 'passed':False,
                        'receipt': {'tests_failed': 3 if len(calls)==1 else 1, 'tests_total':10}}
            workflow = Investigation(manager, root/'run', feedback_grader=grade)
            try:
                result = workflow.run('Task', repo)
                main = result['agents']['main']
                (repo/'answer.py').write_text('first\n')
                one = workflow.evaluate(main, 'a'*32)
                (repo/'answer.py').write_text('second\n')
                two = workflow.evaluate(main, 'b'*32)
                reused = workflow.evaluate(main, 'c'*32, two['checkpoint_id'])
                self.assertEqual(two['grade_facts']['trend'], '3/10 failing → 1/10 failing')
                self.assertEqual(two['grade_facts']['previous_candidate']['candidate_sha256'], one['candidate']['sha256'])
                self.assertEqual(two['grade_facts'], reused['grade_facts'])
                self.assertTrue(reused['reused'])
                self.assertEqual(len(calls), 2)
                self.assertNotIn('handoff', workflow.report)
            finally:
                manager.shutdown()

    def test_run_controller_facts_cross_authenticated_relay(self):
        import importlib.util
        import urllib.request
        import urllib.error
        source = Path(__file__).resolve().parents[2]/'lab/adapters/teamharness/backend.py'
        spec = importlib.util.spec_from_file_location('src.runtime_facts_backend', source)
        backend = importlib.util.module_from_spec(spec); spec.loader.exec_module(backend)
        current = {'available': True, 'elapsed_seconds': 8, 'run_cap_seconds':14400,
                   'active_tasks': 26, 'shared_gpu_count':1}
        relay = backend.ModelRelay('http://127.0.0.1:1', 'http://127.0.0.1:1', 'unused',
                                   facts_callback=lambda: dict(current))
        try:
            def get(token):
                req = urllib.request.Request(relay.url+'/runtime-facts', data=b'{}',
                    headers={'Authorization':'Bearer '+token})
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.load(response)
            self.assertEqual(get(relay.token)['active_tasks'], 26)
            current['active_tasks'] = 25
            self.assertEqual(get(relay.token)['active_tasks'], 25)
            with self.assertRaises(urllib.error.HTTPError) as error:
                get('wrong')
            self.assertEqual(error.exception.code, 401)
            error.exception.close()
        finally:
            relay.close()

    def test_language_routing_observes_nearest_repository_manifests(self):
        from src.teamharness.prompts import language_routing
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); nested=root/'package'; nested.mkdir()
            (root/'go.mod').write_text('module fixture\n')
            (nested/'package.json').write_text(json.dumps({'dependencies':{'react':'18'}}))
            routed=language_routing(nested, root/'guides', root)
            self.assertIn('languages/go.md', routed)
            self.assertIn('languages/typescript-javascript.md', routed)
            self.assertIn('languages/react.md', routed)
            self.assertNotIn('languages/python.md', routed)

    def test_long_job_page_keeps_state_and_full_output_reference_visible(self):
        from src.teamharness.presentation import render_result
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            result=render_result(root, root/'archive.sqlite3', 'obj:1', 'exec_command',
                {'id':'job-id', 'state':'running', 'elapsed_seconds':12,
                 'output_path':'/exact/full/output', 'kill_after_ms':600000,
                 'output': {'content':'x'*40000, 'offset':0, 'next_offset':40000,
                            'complete':False, 'total_bytes':50000}})
            self.assertIn('state: running', result)
            self.assertIn('elapsed seconds: 12', result)
            self.assertIn('/exact/full/output', result)
            self.assertIn('next offset: 40000', result)
            self.assertIn('kill after ms: 600000', result)
