"""Read-only startup observation checks; no SSH, AWS, or model calls."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile

from lab.adapters.teamharness.observe import (CONTAINER_CODE, HOST_CODE, fingerprint,
    frozen_expectations, guide_inventory, lifetime_peak, live_summary, merge_lifetimes, public_task,
    request_configuration)
from lab.adapters.teamharness.report_fleet import file_digest


class ObservationTests(unittest.TestCase):
    def test_recorded_configuration_verifies_evolved_requests_without_relabeling_initial_descriptor(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'session.sqlite3'
            config = {'system': 'Explicitly updated instructions', 'tools': [], 'settings': {'temperature': 0.2}}
            request = {'messages': [{'role': 'system', 'content': config['system']}],
                       'tools': [], 'model': 'available-model', 'temperature': 0.2}
            event = {'request': request, 'config_version': 2, 'config_ref': 'obj:1',
                     'provider_configuration': {'model': 'available-model'}}
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE objects(id INTEGER PRIMARY KEY,scope TEXT,kind TEXT,body TEXT)')
                db.execute('INSERT INTO objects VALUES(1,?,?,?)', ('session', 'session_configuration',
                    json.dumps({'version': 2, 'config': config})))
            before = file_digest(path)
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
                self.assertTrue(request_configuration(db, event))
                self.assertFalse(request_configuration(db, dict(event, config_version=3)))
                self.assertFalse(request_configuration(db, dict(event, config_ref='obj:9')))
                self.assertIsNone(request_configuration(db, {'request': request}))
                request['temperature'] = 0.7
                self.assertFalse(request_configuration(db, event))
            self.assertEqual(before, file_digest(path))
            session = {'id': 'main', 'model_requests': 1, 'wire_system_matches_descriptor': False,
                'wire_tools_match_descriptor': False, 'descriptor_matches_frozen_template': True,
                'descriptor_guides_match': True, 'wire_configuration_verified': True,
                'wire_request_matches_session': True}
            expected = {'problem_statement': 'Repair', 'base_commit': 'base'}
            native = {'guide_manifest_matches': True, 'guides': {}, 'runtime_files': {},
                'role_template_sha256': {}, 'public_task_sha256': fingerprint(public_task(expected)),
                'sessions': [session]}
            row = {'instance_id': 'fixture', 'native': native, 'launch': {'course_base': 'base'},
                'public_task_sha256': fingerprint(public_task(expected))}
            self.assertTrue(live_summary(row, expected, {}, {}, {}, 'model')['checks']['all_observed_wire_contracts_match'])
            session['wire_configuration_verified'] = False
            self.assertFalse(live_summary(row, expected, {}, {}, {}, 'model')['checks']['all_observed_wire_contracts_match'])

    def test_recursive_guidance_preserves_relative_identity_and_reports_missing_or_linked_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'guides'
            self.assertTrue(guide_inventory(root)[1])
            nested = root / 'mechanisms/.repair-memory'
            nested.mkdir(parents=True)
            (root / 'README.md').write_text('Library index')
            (root / 'provenance.json').write_text('{"author":"guide writer"}')
            (nested / 'reference.py').write_text('VALUE = 1\n')
            (nested / '__pycache__').mkdir()
            (nested / '__pycache__/reference.pyc').write_bytes(b'generated')
            (root / 'manifest.json').write_text('{}')
            files, errors = guide_inventory(root)
            self.assertEqual(errors, [])
            self.assertEqual(set(files), {'README.md', 'provenance.json', 'mechanisms/.repair-memory/reference.py'})
            (nested / 'reference.py').write_text('VALUE = 2\n')
            self.assertNotEqual(files, guide_inventory(root)[0])
            (nested / 'reference.py').unlink()
            self.assertNotIn('mechanisms/.repair-memory/reference.py', guide_inventory(root)[0])
            secret = Path(temp) / 'external-secret'
            secret.write_text('must never be inventoried through a link')
            (nested / 'reference.py').symlink_to(secret)
            files, errors = guide_inventory(root)
            self.assertNotIn('mechanisms/.repair-memory/reference.py', files)
            self.assertEqual(errors, [{'path': 'mechanisms/.repair-memory/reference.py', 'error': 'Unverified symbolic link'}])
    def test_public_identity_ignores_private_runtime_and_is_key_order_independent(self):
        first = {'requirements': 'Preserve exact names', 'problem_statement': 'Repair it',
                 'sandbox_url': 'http://private', 'relay_token': 'private'}
        second = {'problem_statement': 'Repair it', 'requirements': 'Preserve exact names'}
        self.assertEqual(fingerprint(public_task(first)), fingerprint(public_task(second)))
        self.assertNotEqual(fingerprint(public_task(first)), fingerprint({'problem_statement': 'Other task'}))

    def test_overlap_uses_confirmed_intervals_and_retains_evidence_after_collection(self):
        rows = [{'instance_id': 'a', 'first_model_at': 1, 'confirmed_alive_through': 6},
                {'instance_id': 'b', 'first_model_at': 2, 'confirmed_alive_through': 5},
                {'instance_id': 'c', 'first_model_at': 6, 'confirmed_alive_through': 9},
                {'instance_id': 'unstarted'}]
        history = merge_lifetimes({}, rows, 'first-observation')
        self.assertEqual(lifetime_peak(history.values()), 2)
        merge_lifetimes(history, [{'instance_id': 'b', 'state': 'container_unavailable'}], 'next-observation')
        self.assertEqual(lifetime_peak(history.values()), 2)

    def test_container_reader_uses_real_wire_and_tools_without_reading_secrets_or_mutating_db(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = root / 'run/agents'
            agent_id = 'a' * 32
            agent = manager / 'agents' / agent_id
            agent.mkdir(parents=True)
            guides = manager / 'guides'
            guides.mkdir()
            (guides / 'contracts.md').write_text('Fixture advice')
            (guides / 'languages').mkdir()
            (guides / 'languages/python.md').write_text('Nested fixture advice')
            (guides / 'provenance.json').write_text('{"source":"authored"}')
            hashes, errors = guide_inventory(guides)
            self.assertEqual(errors, [])
            (guides / 'manifest.json').write_text(json.dumps({'files': hashes}))
            template = 'Stable role. Workspace /__TEAM_WORKSPACE__'
            (manager / 'role-prompts.json').write_text(json.dumps({'planner': template}))
            facts = {'agent_id': agent_id, 'parent': None, 'archive_root': str(agent),
                     'workspace_mode': 'private', 'max_agents': 8, 'task_deadline_unix': 1000.0}
            workspace = str(agent / 'workspace')
            system = template.replace('/__TEAM_WORKSPACE__', workspace) + '\n\nSession facts (runtime): ' + json.dumps(facts, sort_keys=True)
            tools = [{'type': 'function', 'function': {'name': 'exec_command'}}]
            descriptor = dict(facts, id=agent_id, role='planner', system=system, workspace=workspace,
                              tools=tools, guide_hashes=hashes, created_at=1.0)
            (agent / 'agent.json').write_text(json.dumps(descriptor))
            (agent / 'runner.json').write_text(json.dumps({'status': 'running'}))
            task = {'problem_statement': 'Fixture task', 'requirements': 'Exact behavior'}
            (root / 'run/workflow.json').write_text(json.dumps({'state': 'running', 'task': json.dumps(task)}))
            secret = 'never-return-this-relay-token'
            (root / 'config.json').write_text(json.dumps({'relay_token': secret}))
            runtime = root / 'runtime/src/teamharness'
            runtime.mkdir(parents=True)
            for module in ('session.py', 'parallel.py', 'patching.py', 'course_worker.py', '__init__.py'):
                (runtime / module).write_text('# actual frozen module ' + module)
            (runtime / 'ignored.pyc').write_bytes(b'not source')
            messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': 'task'},
                        {'role': 'assistant', 'tool_calls': [{'id': 'tool-1', 'function': {'name': 'exec_command'}}]},
                        {'role': 'tool', 'tool_call_id': 'tool-1', 'content': json.dumps({'id': 'real-job', 'state': 'exited', 'exit_code': 0})}]
            request = {'model': 'standard', 'messages': messages[:2], 'tools': tools}
            group = {'members': [
                {'name': 'exec_command', 'job_id': 'not-yet-executed', 'state': 'pending'},
                {'name': 'exec_command', 'result': {'id': 'real-job', 'state': 'exited'}},
                {'name': 'exec_command', 'result': {'id': 'parallel-job', 'state': 'running'},
                 'job_observation': {'id': 'parallel-job', 'state': 'exited'}}]}
            def exchange(call, name):
                return [{'role': 'assistant', 'tool_calls': [{'id': call, 'function': {'name': name}}]},
                        {'role': 'tool', 'tool_call_id': call, 'content': json.dumps(group)}]
            messages += exchange('parallel-1', 'parallel')
            messages[-1]['content'] = 'Result obj:50\nReadable archived parallel result'
            following = exchange('inspect-1', 'inspect_parallel') + exchange('cancel-1', 'cancel_parallel')
            state = {'status': 'running', 'epoch': 0, 'messages': messages,
                     'config': {'metadata': {'task_deadline_unix': 1000.0}}}
            for name, scope, entries in (
                ('session.sqlite3', 'session', [('model_request', {'purpose': 'agent', 'request': request}),
                    ('model_response', {'response': {}}), ('session_state', state), ('session_state', state),
                    ('session_state', dict(state, epoch=1, messages=following))]),
                ('context.sqlite3', 'team', [('transport_request', {'request': request}), ('transport_response', {'response': {}})])):
                with sqlite3.connect(agent / name) as db:
                    db.execute('CREATE TABLE objects(id INTEGER PRIMARY KEY,scope TEXT,kind TEXT,body TEXT,created REAL,sha256 TEXT)')
                    for i, (kind, body) in enumerate(entries):
                        db.execute('INSERT INTO objects(scope,kind,body,created) VALUES(?,?,?,?)', (scope, kind, json.dumps(body), 2.0+i))
                    if scope == 'session':
                        raw = json.dumps({'name': 'parallel', 'receipt': group})
                        db.execute('INSERT INTO objects(id,scope,kind,body,created,sha256) VALUES(?,?,?,?,?,?)',
                                   (50, scope, 'tool_result', raw, 1.0, hashlib.sha256(raw.encode()).hexdigest()))
            before = {p.name: file_digest(p) for p in agent.glob('*.sqlite3')}
            code = CONTAINER_CODE.replace("Path('/work/teamharness-0')", 'Path(' + repr(str(root)) + ')')
            answer = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
            self.assertNotIn(secret, answer.stdout + answer.stderr)
            self.assertEqual(before, {p.name: file_digest(p) for p in agent.glob('*.sqlite3')})
            row = json.loads(answer.stdout)
            self.assertEqual(row['public_task_sha256'], fingerprint(task))
            session = row['sessions'][0]
            self.assertEqual((session['transport_requests'], session['transport_responses'], session['latest_epoch_shell_results']), (1, 1, 2))
            self.assertEqual(session['shell_job_ids'], ['parallel-job', 'real-job'])
            self.assertEqual((session['all_epoch_tool_results'], session['all_epoch_shell_results']), (4, 2))
            self.assertEqual(session['task_deadline_unix'], 1000.0)
            self.assertTrue(session['wire_system_matches_descriptor'])
            self.assertTrue(session['wire_tools_match_descriptor'])
            self.assertTrue(session['descriptor_matches_frozen_template'])
            self.assertTrue(row['guide_manifest_matches'])
            self.assertEqual(row['runtime_files'], {p.name: file_digest(p) for p in runtime.glob('*.py')})

    def test_live_deadlines_are_separate_from_wire_contract_and_shells_deduplicate(self):
        expected = {'problem_statement': 'Repair', 'base_commit': 'base'}
        session = {'id': 'parent', 'task_deadline_unix': 1000, 'model_requests': 1,
                   'wire_system_matches_descriptor': True, 'wire_tools_match_descriptor': True,
                   'descriptor_matches_frozen_template': True, 'descriptor_guides_match': True,
                   'shell_job_ids': ['job-a', 'job-b']}
        native = {'sessions': [session, dict(session, id='child', shell_job_ids=['job-b'])],
                  'public_task_sha256': fingerprint(public_task(expected)), 'guides': {},
                  'guide_manifest_matches': True, 'role_template_sha256': {}, 'runtime_files': {}}
        row = {'instance_id': 'fixture', 'native': native,
               'public_task_sha256': fingerprint(public_task(expected)),
               'launch': {'course_base': 'base', 'deadline': 1000}}
        result = live_summary(row, expected, {}, {}, {}, 'qwen/example')
        self.assertEqual(result['shell_results_lower_bound'], 2)
        self.assertTrue(result['checks']['all_observed_wire_contracts_match'])
        self.assertTrue(result['checks']['all_observed_session_deadlines_match'])
        native['sessions'][1]['task_deadline_unix'] = 1001
        self.assertEqual(live_summary(row, expected, {}, {}, {}, 'qwen/example')['deadline_evidence']['mismatched'], ['child'])
        for observed in native['sessions']:
            observed.pop('task_deadline_unix')
        old = live_summary(row, expected, {}, {}, {}, 'qwen/example')
        self.assertTrue(old['checks']['all_observed_wire_contracts_match'])
        self.assertEqual(old['deadline_evidence']['state'], 'unavailable')

    def test_removed_container_is_unknown_observation_not_solver_failure(self):
        row = {'instance_id': 'fixture', 'native_observation': 'container_unavailable_or_exec_failed'}
        summary = live_summary(row, {}, {}, {}, {}, 'qwen/example')
        self.assertIsNone(summary['state'])
        self.assertIsNone(summary['worker_active'])
        self.assertEqual(summary['model_requests'], 0)
        self.assertFalse(summary['checks']['all_observed_wire_contracts_match'])

    def test_remote_snippets_compile_with_public_inputs(self):
        compile(CONTAINER_CODE, '<container-observer>', 'exec')
        compile(HOST_CODE.replace('__EXPECTED__', repr(['instance-fixture'])).replace('__INNER__', repr(CONTAINER_CODE)), '<host-observer>', 'exec')

    def test_contract_expectations_come_from_exact_frozen_package(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            source = run / 'fleet-source'
            source.mkdir()
            manifest = source / 'tasks.json'
            manifest.write_text(json.dumps({'0': {'instance_id': 'fixture', 'base_commit': 'base', 'problem_statement': 'Repair'}}))
            (source / 'route.json').write_text(json.dumps({'routes': {'standard': {'model': 'qwen/fixture'}}}))
            with zipfile.ZipFile(run / 'src.zip', 'w') as archive:
                archive.writestr('src/teamharness/prompts.py', 'from .repair_prompts import VALUE\nfrom pathlib import Path\ndef role_names(): return ("planner",)\ndef system_prompt(role,workspace,guide_root): return VALUE+str(workspace)+" "+str(guide_root)+" "+(Path(guide_root)/"contracts.md").read_text()\n')
                archive.writestr('src/teamharness/repair_prompts.py', 'VALUE = "frozen-value "\n')
                archive.writestr('src/teamharness/guides/contracts.md', 'frozen advice')
                for name in ('tools.py', 'session.py', 'agents.py', 'main.py', 'candidates.py', 'workspaces.py', 'provider.py',
                             'parallel.py', 'patching.py', 'history.py', 'types.py', '__main__.py', 'course_worker.py'):
                    archive.writestr('src/teamharness/' + name, '# frozen fixture\n')
                archive.writestr('src/teamharness/guides/not-a-runtime-module.py', '# excluded nested source\n')
                archive.writestr('src/teamharness/guides/provenance.json', '{"source":"authored"}')
                archive.writestr('src/teamharness/guides/languages/go.md', 'nested advice')
                archive.writestr('src/teamharness/guides/__pycache__/sample.pyc', 'generated bytecode')
                archive.writestr('src/teamharness/guides/manifest.json', '{}')
            metadata = {'manifest': 'tasks.json', 'manifest_sha256': file_digest(manifest), 'tasks': ['fixture'],
                        'arms': [{'name': 'teamharness', 'sha256': file_digest(run / 'src.zip')}], 'route': 'route.json'}
            expected, guides, runtime, templates, model = frozen_expectations(run, source, metadata)
            self.assertEqual(templates, {'planner': fingerprint('frozen-value /__TEAM_WORKSPACE__ /work/teamharness-0/run/agents/guides frozen advice')})
            self.assertEqual(model, 'qwen/fixture')
            self.assertIn('contracts.md', guides)
            self.assertEqual(set(guides), {'contracts.md', 'not-a-runtime-module.py', 'provenance.json', 'languages/go.md'})
            self.assertEqual(expected['fixture']['source_key'], '0')
            self.assertEqual(len(runtime), 15)
            self.assertTrue({'parallel.py', 'patching.py', 'course_worker.py'} <= runtime.keys())
            self.assertNotIn('not-a-runtime-module.py', runtime)
            metadata['arms'][0]['sha256'] = 'wrong'
            with self.assertRaisesRegex(ValueError, 'package differs'):
                frozen_expectations(run, source, metadata)


if __name__ == '__main__':
    unittest.main()
