"""Small offline validation-gate checks; never provision or invoke a model."""
import json
import hashlib
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab.adapters.teamharness.aws_preflight.validation_runner import (Validation, sha, verify_wire, packaged_check_spec,
    verify_packaged_check, run_packaged_mechanics)
from src.store import Store
from src.teamharness.presentation import session_facts
from src.teamharness.prompts import freeze_guides, system_prompt
from src.teamharness.session import CONTROL_TOOLS
from src.teamharness.tools import TOOLS


class AwsPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def main_receipt(self):
        return {'passed': True, 'platform': 'linux', 'workflow': 'main',
                'interactive_grades': 2, 'failed_counts': [3, 0], 'automatic_reviews': 0,
                'original_base_replay_verified': True, 'final_grade_reused': True,
                'selection_verified': True, 'returned_checkpoint_fallback_verified': True,
                'owned_jobs_stopped_at_course_finalization': True,
                'candidate_sha256': 'a' * 64, 'model_quality_measured': False}

    def test_packaged_selection_accepts_only_explicit_main(self):
        package = self.root / 'package'
        (package / 'src').mkdir(parents=True)
        (package / 'src/campaign.json').write_text(json.dumps({'workflow': 'main'}))
        self.assertEqual(packaged_check_spec(package), ('main', 'lab/adapters/teamharness/check_main.py'))
        for campaign in ({}, {'workflow': 'staged'}, {'workflow': None}, {'workflow': 'unknown'}):
            (package / 'src/campaign.json').write_text(json.dumps(campaign))
            with self.assertRaisesRegex(ValueError, 'Only main packages'):
                packaged_check_spec(package)

    def test_main_receipt_requires_complete_actual_coverage(self):
        main = self.main_receipt()
        self.assertTrue(verify_packaged_check(main, 'main')['scripted_mechanics_verified'])
        with self.assertRaisesRegex(ValueError, 'Only main'):
            verify_packaged_check(main, 'staged')
        for key, value in (('passed', False), ('original_base_replay_verified', False),
                           ('owned_jobs_stopped_at_course_finalization', False),
                           ('model_quality_measured', True), ('failed_counts', [3, 1]),
                           ('candidate_sha256', 'missing'), ('interactive_grades', 2.0),
                           ('automatic_reviews', True), ('failed_counts', [3, False])):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'complete passing coverage'):
                verify_packaged_check({**main, key: value}, 'main')
        with self.assertRaisesRegex(RuntimeError, 'did not run on Linux'):
            verify_packaged_check({**main, 'platform': 'darwin'}, 'main', require_linux=True)

    def test_mechanics_only_reports_no_live_validation_or_course_readiness(self):
        repo, package, output = self.root / 'repo', self.root / 'package', self.root / 'result'
        repo.mkdir()
        (package / 'src').mkdir(parents=True)
        (package / 'src/campaign.json').write_text(json.dumps({'workflow': 'main'}))
        def fixture(command, **kwargs):
            target = Path(command[command.index('--output') + 1])
            target.mkdir()
            (target / 'CHECK.json').write_text(json.dumps(self.main_receipt()))
            return type('Process', (), {'returncode': 0})()
        with patch('lab.adapters.teamharness.aws_preflight.validation_runner.subprocess.run', side_effect=fixture) as run:
            result = run_packaged_mechanics(repo, package, output)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(run.call_args.args[0][1].endswith('check_main.py'))
        self.assertTrue(result['mechanics_passed'])
        self.assertFalse(result['ready_for_original_runner'])
        self.assertFalse(result['full_preflight_passed'])
        self.assertFalse(result['model_quality_measured'])
        self.assertEqual(result['live_model_validation'], 'not_run')
        self.assertFalse(result['original_course_task_started'])
        self.assertEqual(json.loads((output / 'RESULT.json').read_text()), result)

    def test_mechanics_failure_retains_honest_result(self):
        repo, package, output = self.root / 'repo', self.root / 'package', self.root / 'result'
        repo.mkdir()
        (package / 'src').mkdir(parents=True)
        (package / 'src/campaign.json').write_text(json.dumps({'workflow': 'main'}))
        with patch('lab.adapters.teamharness.aws_preflight.validation_runner.subprocess.run') as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(RuntimeError, 'did not pass'):
                run_packaged_mechanics(repo, package, output)
        result = json.loads((output / 'RESULT.json').read_text())
        self.assertFalse(result['mechanics_passed'])
        self.assertFalse(result['ready_for_original_runner'])
        self.assertEqual(result['exit_code'], 1)

    def test_main_host_runner_checks_main_receipt_and_stops_before_live_probe(self):
        v = Validation.__new__(Validation)
        v.source = self.root / 'source'
        bundle = v.source / 'dev/teamharness-validation'
        (bundle / 'repository').mkdir(parents=True)
        (bundle / 'package/src').mkdir(parents=True)
        (bundle / 'package/src/campaign.json').write_text(json.dumps({'workflow': 'main'}))
        package = self.root / 'arm.zip'
        package.write_bytes(b'fixture')
        from lab.adapters.teamharness.aws_preflight.validation_runner import inventory
        v.config = {'repository_inventory': {}, 'package_inventory': inventory(bundle / 'package'),
                    'support_hashes': {}, 'task_ids': ['key32'], 'package_zip_sha256': sha(package)}
        v.plan = {'tasks': ['key32'], 'arms': [{'zip': str(package)}]}
        v.output = self.root / 'evidence'
        v.output.mkdir()
        def phase(name, command, *args, **kwargs):
            if name == 'native-suite':
                (v.output / 'suite.json').write_text(json.dumps({'passed': True, 'skipped': [], 'tests_run': 1}))
            elif name == 'packaged-check':
                self.assertTrue(command[1].endswith('check_main.py'))
                (v.output / 'packaged').mkdir()
                (v.output / 'packaged/CHECK.json').write_text(json.dumps(self.main_receipt()))
            else:
                self.fail('Unexpected live or course phase: ' + name)
        with patch.object(v, 'run_phase', side_effect=phase) as phases, \
                patch('lab.adapters.teamharness.aws_preflight.validation_runner.subprocess.run'):
            with self.assertRaisesRegex(RuntimeError, 'live main-workflow validation is not implemented'):
                v.execute()
        self.assertEqual(phases.call_count, 2)
        self.assertTrue(json.loads((v.output / 'packaged-proof.json').read_text())['scripted_mechanics_verified'])
        self.assertFalse((v.output / 'RESULT.json').exists())

    def test_failed_required_phase_prevents_live_and_course_handoff(self):
        v = Validation.__new__(Validation)
        v.source = self.root / 'source'
        bundle = v.source / 'dev/teamharness-validation'
        for name in ('repository', 'package'):
            (bundle / name).mkdir(parents=True)
        package = self.root / 'arm.zip'
        package.write_bytes(b'fixture')
        v.config = {'repository_inventory': {}, 'package_inventory': {}, 'support_hashes': {},
                    'task_ids': ['key32'], 'package_zip_sha256': sha(package)}
        v.plan = {'tasks': ['key32'], 'arms': [{'zip': str(package)}]}
        v.output = self.root / 'evidence'
        v.output.mkdir()
        with patch.object(v, 'run_phase', side_effect=RuntimeError('required Linux test failed')) as phase, \
                patch('lab.adapters.teamharness.aws_preflight.validation_runner.subprocess.run'):
            with self.assertRaisesRegex(RuntimeError, 'required Linux test failed'):
                v.execute()
        self.assertEqual(phase.call_count, 1)
        self.assertEqual(phase.call_args.args[0], 'native-suite')
        self.assertFalse((v.output / 'RESULT.json').exists())

    def test_failed_validation_emits_null_original_outcome_before_done(self):
        v = Validation.__new__(Validation)
        v.data, v.source, v.output = self.root / 'data', self.root / 'source', self.root / 'evidence'
        v.output.mkdir()
        v.source.mkdir()
        (v.source / 'manifest.json').write_text(json.dumps({'32': {'instance_id': 'key32', 'repo': 'repo'}}))
        v.plan = {'manifest': 'manifest.json', 'tasks': ['key32'], 'arms': [{'name': 'teamharness'}], 'host': 'owned-host'}
        v.phase = 'native-suite'
        with patch.object(v, 'archive') as archive, patch.object(v, 'status'):
            v.fail(RuntimeError('fixture assertion failed'))
        archive.assert_called_once()
        row = json.loads((v.data / 'results/teamharness/key32.json').read_text())
        self.assertIsNone(row['solved'])
        self.assertFalse(row['model_task_started'])
        self.assertEqual(row['lane_status'], 'validation_failed')
        self.assertEqual(row['reason'], 'infrastructure_error')
        self.assertFalse(json.loads((v.data / 'DONE.json').read_text())['validation_passed'])

    def wire_fixture(self, label='live'):
        live = self.root / label
        manager = live / 'workflow/run/agents'
        repository = Path(__file__).resolve().parents[2]
        guide_hashes = freeze_guides(manager / 'guides')
        agent = manager / ('agents/' + 'a' * 32)
        agent.mkdir(parents=True)
        workspace = self.root / (label + '-workspace')
        workspace.mkdir()
        facts = {'agent_id': 'a' * 32, 'parent': None, 'archive_root': str(agent),
                 'workspace_mode': 'private', 'max_agents': 8, 'task_deadline_unix': None}
        system = system_prompt('investigator', workspace, guide_root=manager / 'guides')
        system += '\n\nSession facts (runtime):\n' + session_facts(facts)
        descriptor = {**facts, 'id': 'a' * 32, 'role': 'investigator', 'workspace': str(workspace),
                      'system': system, 'tools': TOOLS + CONTROL_TOOLS, 'guide_hashes': guide_hashes}
        (agent / 'agent.json').write_text(json.dumps(descriptor))
        database = Store(agent / 'session.sqlite3')
        self.addCleanup(database.db.close)
        request = {'model': 'standard', 'messages': [{'role': 'system', 'content': system},
                   {'role': 'user', 'content': 'A genuine Unicode fixture: café'}], 'tools': TOOLS + CONTROL_TOOLS}
        database.put('model_request', 'session', {'purpose': 'agent', 'request': request})
        return live, repository, agent, database, request

    def transport(self, agent, request, raw=None, include_raw=True):
        with Store(agent / 'context.sqlite3') as store:
            ref = store.put('transport_request', 'team', {'request': request})
            if include_raw:
                # Deliberately use a different serialization from src.api.py.
                # Verification must hash original bytes and compare decoded
                # values, never invent a supposedly original serialization.
                raw = raw if raw is not None else json.dumps(request, indent=2, ensure_ascii=False).encode()
                store.put_blob('raw_request', 'team', raw,
                               links=[{'relation': 'transport_request', 'ref': ref}])
        return raw

    def test_wire_requires_actual_frozen_prompt_tools_and_guides(self):
        live, repository, agent, database, request = self.wire_fixture()
        self.transport(agent, request)
        self.assertTrue(verify_wire(live, repository)['verified'])
        database.put('model_request', 'session', {'purpose': 'agent', 'request': {**request, 'tools': []}})
        with self.assertRaisesRegex(RuntimeError, 'tool schemas differ'):
            verify_wire(live, repository)

    def test_original_bytes_retries_and_no_tools_compaction_are_accounted(self):
        live, repository, agent, database, request = self.wire_fixture()
        first = self.transport(agent, request)
        self.transport(agent, request, json.dumps(request, separators=(',', ':')).encode())
        compact = {'model': 'standard', 'messages': request['messages'] + [
                   {'role': 'user', 'content': 'Summarize without tools'}],
                   'response_format': {'type': 'json_object'}}
        database.put('model_request', 'session', {'purpose': 'compaction', 'request': compact})
        self.transport(agent, compact)
        paths = [agent / 'session.sqlite3', agent / 'context.sqlite3']
        original_hashes = [sha(path) for path in paths]
        proof = verify_wire(live, repository)
        row = proof['sessions'][0]
        self.assertEqual(row['requests'], {'agent': 1, 'compaction': 1})
        self.assertEqual(row['transport']['session_requests'], 2)
        self.assertEqual(row['transport']['transport_attempts'], 3)
        attempts = row['transport']['attempts']
        self.assertEqual(attempts[0]['raw_request_sha256'], hashlib.sha256(first).hexdigest())
        self.assertEqual(attempts[0]['session_request_ref'], attempts[1]['session_request_ref'])
        self.assertEqual(attempts[2]['purpose'], 'compaction')
        self.assertEqual([sha(path) for path in paths], original_hashes)

    def test_missing_transport_database_attempt_or_raw_bytes_fails(self):
        for missing in ('database', 'attempt', 'raw'):
            with self.subTest(missing=missing):
                live, repository, agent, _, request = self.wire_fixture(missing)
                if missing == 'attempt':
                    with Store(agent / 'context.sqlite3'):
                        pass
                elif missing == 'raw':
                    self.transport(agent, request, include_raw=False)
                with self.assertRaisesRegex(RuntimeError, 'lacks original transport|lacks exactly one original raw'):
                    verify_wire(live, repository)

    def test_changed_transport_or_original_wire_bytes_fails(self):
        for changed in ('transport', 'raw'):
            with self.subTest(changed=changed):
                live, repository, agent, _, request = self.wire_fixture(changed)
                wrong = {**request, 'tools': []}
                if changed == 'transport':
                    self.transport(agent, wrong)
                else:
                    self.transport(agent, request, json.dumps(wrong).encode())
                with self.assertRaisesRegex(RuntimeError, 'differs from its .* request'):
                    verify_wire(live, repository)

    def test_earlier_retry_cannot_cover_a_later_unsent_identical_request(self):
        live, repository, agent, database, request = self.wire_fixture()
        self.transport(agent, request)
        self.transport(agent, request)
        database.put('model_request', 'session', {'purpose': 'agent', 'request': request})
        with self.assertRaisesRegex(RuntimeError, 'Session request lacks original transport evidence'):
            verify_wire(live, repository)

    def test_orphan_raw_request_is_not_ignored(self):
        live, repository, agent, _, request = self.wire_fixture()
        self.transport(agent, request)
        with Store(agent / 'context.sqlite3') as store:
            store.put_blob('raw_request', 'team', json.dumps(request).encode())
        with self.assertRaisesRegex(RuntimeError, 'Unaccounted original raw request evidence'):
            verify_wire(live, repository)

    def test_compaction_transport_cannot_silently_add_tools(self):
        live, repository, agent, database, request = self.wire_fixture()
        self.transport(agent, request)
        compact = {'model': 'standard', 'messages': request['messages'],
                   'response_format': {'type': 'json_object'}}
        database.put('model_request', 'session', {'purpose': 'compaction', 'request': compact})
        self.transport(agent, {**compact, 'tools': TOOLS + CONTROL_TOOLS})
        with self.assertRaisesRegex(RuntimeError, 'Original transport request differs'):
            verify_wire(live, repository)

    def test_relocated_archive_uses_original_prompt_paths_without_rewriting(self):
        live, repository, agent, database, request = self.wire_fixture()
        self.transport(agent, request)
        moved = self.root / 'derived'
        shutil.copytree(live, moved)
        before = {str(p.relative_to(moved)): sha(p) for p in moved.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(RuntimeError, 'prompt/tool contract differs'):
            verify_wire(moved, repository)
        result = verify_wire(moved, repository,
            original_guide_root=live / 'workflow/run/agents/guides')
        self.assertTrue(result['verified'])
        self.assertEqual({str(p.relative_to(moved)): sha(p) for p in moved.rglob('*') if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
