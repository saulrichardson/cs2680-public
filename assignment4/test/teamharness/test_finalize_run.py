"""Offline postprocessing tests: no AWS, subprocesses, model calls or real waits."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import hashlib

MODULE = Path(__file__).resolve().parents[2] / 'lab/adapters/teamharness/finalize_run.py'
spec = importlib.util.spec_from_file_location('finalize_fixture', MODULE)
finalize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finalize)


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / 'run'
        self.run.mkdir()
        (self.run / 'src.zip').write_bytes(b'frozen test package')
        package_sha = hashlib.sha256((self.run / 'src.zip').read_bytes()).hexdigest()
        self.sweep = self.root / 'evidence/fixture'
        self.sweep.mkdir(parents=True)
        self.manifest = self.run / 'fleet-source/dev/tasks/hard51.json'
        self.manifest.parent.mkdir(parents=True)
        entries = {str(i): {'instance_id': f'task-{i}'} for i in range(51)}
        self.ids = [e['instance_id'] for e in entries.values()]
        finalize.save(self.manifest, entries)
        self.ledger = self.root / 'budget.json'
        finalize.save(self.ledger, {'commitments': [{'id': 'this-leaf', 'run': str(self.run)}]})
        finalize.save(self.run / 'launch-plan.json', {'run': str(self.run), 'sweep': 'fixture', 'package_sha256': package_sha,
            'manifest': 'dev/tasks/hard51.json', 'manifest_sha256': hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            'task_ids': self.ids, 'max_minutes': 120, 'environment': {'A3_FLEET_EVIDENCE': str(self.root / 'evidence')}})
        finalize.save(self.run / 'launch.json', {'run': str(self.run), 'package_sha256': package_sha, 'pid': 999,
            'launch_plan_sha256': hashlib.sha256((self.run / 'launch-plan.json').read_bytes()).hexdigest(),
            'started_at': 1000, 'budget_state': str(self.ledger), 'commitment_id': 'this-leaf'})
        finalize.save(self.sweep / 'sweep.json', {'sweep': 'fixture', 'tasks': self.ids, 'arms': [{'name': 'teamharness', 'sha256': package_sha}],
            'hosts': [{'instance_id': 'i-01234', 'region': 'us-east-1'}]})
        self.api_calls, self.budget_calls, self.report_calls = [], [], []
        self.host_state = 'terminated'
        self.volumes = []
        self.now = 2000
        self.complete_count = 51

    def aws(self, region, operation, arguments):
        self.api_calls.append((region, operation, arguments))
        self.assertIn(operation, ('describe-instances', 'describe-volumes'))
        if operation == 'describe-volumes':
            return {'Volumes': self.volumes}
        return {'Reservations': [{'Instances': [{'InstanceId': 'i-01234', 'State': {'Name': self.host_state},
                                                'Tags': [{'Key': 'a3fleet-sweep', 'Value': 'fixture'}]}]}]}

    def budget(self, path, ident, now, cleaned):
        self.budget_calls.append((path, ident, now, cleaned))
        return {'id': ident, 'run': str(self.run), 'cleanup_verified_at': now, 'estimated_exposure_usd': 1.0}

    def report(self, sweep, manifest, arm, output):
        self.report_calls.append((sweep, manifest, arm, output))
        result = {'expected': 51, 'collected': self.complete_count, 'unknown': 51-self.complete_count,
                  'evidence_errors': 0, 'solved': 15, 'graded_failed': self.complete_count-15,
                  'tasks': [{'instance_id': ident} for ident in self.ids]}
        finalize.save(output, result)
        return result

    def finish(self, **overrides):
        options = dict(report=self.report, observe_budget=self.budget, aws=self.aws,
                       processes=lambda *_: {'state': 'exited'}, clock=lambda: self.now)
        options.update(overrides)
        return finalize.finish(self.run, **options)

    def test_completed_results_exact_cleanup_closes_only_matching_leaf(self):
        result = self.finish()
        self.assertEqual(result['state'], 'complete')
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(self.budget_calls, [(self.ledger, 'this-leaf', 2000, True)])
        self.assertEqual(len(self.api_calls), 3)
        self.assertIn('--instance-ids', self.api_calls[0][2])
        self.assertIn('Name=tag:a3fleet-sweep,Values=fixture', self.api_calls[-1][2])

    def test_live_host_is_not_cleaned_and_budget_is_not_closed(self):
        self.host_state = 'running'
        result = self.finish()
        self.assertEqual(result['state'], 'incomplete')
        self.assertFalse(result['cleanup_verified'])
        self.assertEqual(self.budget_calls, [])
        receipt = finalize.read(result['cleanup_receipt'])
        self.assertEqual(receipt['regions']['us-east-1']['instance_states'], {'i-01234': 'running'})

    def test_sample_reports_selected_five_without_changing_frozen_manifest(self):
        original = self.manifest.read_bytes()
        keys = ['25', '18', '28', '42', '48']
        ids = [f'task-{key}' for key in keys]
        plan = finalize.read(self.run / 'launch-plan.json')
        plan.update(profile='sample5', selected_task_keys=keys, task_ids=ids)
        finalize.save(self.run / 'launch-plan.json', plan)
        receipt = finalize.read(self.run / 'launch.json')
        receipt['launch_plan_sha256'] = hashlib.sha256((self.run / 'launch-plan.json').read_bytes()).hexdigest()
        finalize.save(self.run / 'launch.json', receipt)
        metadata = finalize.read(self.sweep / 'sweep.json')
        metadata['tasks'] = ids
        finalize.save(self.sweep / 'sweep.json', metadata)
        def sample_report(sweep, manifest, arm, output):
            selected = finalize.read(manifest)
            self.assertEqual(Path(manifest), self.manifest)
            self.assertEqual(len(selected), 51)
            self.assertEqual(hashlib.sha256(Path(manifest).read_bytes()).hexdigest(), plan['manifest_sha256'])
            return {'expected': 5, 'collected': 5, 'unknown': 0, 'evidence_errors': 0,
                    'solved': 2, 'graded_failed': 3, 'tasks': [{'instance_id': ident} for ident in ids]}
        result = self.finish(report=sample_report)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertEqual(len(self.budget_calls), 1)

    def test_sample3_real_reporter_accepts_original_manifest_identity(self):
        from lab.adapters.teamharness.report_fleet import report
        keys = ['18', '25', '28']
        ids = ['task-' + key for key in keys]
        plan = finalize.read(self.run / 'launch-plan.json')
        plan.update(profile='sample3', selected_task_keys=keys, task_ids=ids)
        finalize.save(self.run / 'launch-plan.json', plan)
        receipt = finalize.read(self.run / 'launch.json')
        receipt['launch_plan_sha256'] = hashlib.sha256((self.run / 'launch-plan.json').read_bytes()).hexdigest()
        finalize.save(self.run / 'launch.json', receipt)
        metadata = finalize.read(self.sweep / 'sweep.json')
        metadata.update(tasks=ids, manifest_sha256=plan['manifest_sha256'])
        finalize.save(self.sweep / 'sweep.json', metadata)
        _, _, manifest, sweep = finalize.inputs(self.run)
        result = report(sweep, manifest)
        self.assertEqual(result['expected'], 3)
        self.assertEqual(result['collected'], 0)
        self.assertEqual({r['source_key'] for r in result['tasks']}, set(keys))

    def test_permission_rejection_is_error_not_absence(self):
        def forbidden(*args):
            raise RuntimeError('UnauthorizedOperation: missing ec2:DescribeInstances permission')
        result = self.finish(aws=forbidden)
        receipt = finalize.read(result['cleanup_receipt'])
        self.assertFalse(result['cleanup_verified'])
        self.assertIn('UnauthorizedOperation', receipt['errors'][0])
        self.assertEqual(self.budget_calls, [])

    def test_missing_instance_is_not_assumed_terminated(self):
        result = self.finish(aws=lambda *args: {'Volumes': []} if args[1] == 'describe-volumes' else {'Reservations': []})
        self.assertFalse(result['cleanup_verified'])
        self.assertEqual(self.budget_calls, [])

    def test_remaining_tagged_volume_prevents_closure(self):
        self.volumes = [{'VolumeId': 'vol-01234', 'State': 'available'}]
        result = self.finish()
        self.assertFalse(result['cleanup_verified'])
        self.assertEqual(self.budget_calls, [])

    def test_running_controller_prevents_postprocessing_and_cloud_calls(self):
        result = self.finish(processes=lambda *_: {'state': 'running'})
        self.assertEqual(result['state'], 'controller_still_running')
        self.assertEqual((self.report_calls, self.api_calls, self.budget_calls), ([], [], []))

    def test_wait_is_finite_and_does_not_change_controller(self):
        self.now = 9990 # launched1000 +120min +1800 => deadline10000
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            self.now += seconds
        result = self.finish(processes=lambda *_: {'state': 'running'}, wait=True, sleep=sleep)
        self.assertEqual(result['state'], 'controller_still_running')
        self.assertEqual(sleeps, [10])
        self.assertEqual(self.api_calls, [])

    def test_incomplete_results_remain_incomplete_after_verified_cleanup(self):
        self.complete_count = 50
        result = self.finish()
        self.assertEqual(result['state'], 'incomplete')
        self.assertFalse(result['results_complete'])
        self.assertTrue(result['cleanup_verified'])
        self.assertTrue(result['budget_closed'])
        self.assertEqual(result['summary']['unknown'], 1)

    def test_wrong_budget_leaf_never_observed(self):
        finalize.save(self.ledger, {'commitments': [{'id': 'other-leaf', 'run': str(self.run)}]})
        result = self.finish()
        self.assertTrue(result['cleanup_verified'])
        self.assertFalse(result['budget_closed'])
        self.assertIn('single campaign leaf', result['budget_error'])
        self.assertEqual(self.budget_calls, [])

    def test_report_failure_still_verifies_cleanup_without_inventing_results(self):
        def broken(*args, **kwargs):
            raise ValueError('Corrupt candidate archive')
        result = self.finish(report=broken)
        self.assertFalse(result['results_complete'])
        self.assertTrue(result['cleanup_verified'])
        self.assertIn('Corrupt candidate archive', result['reporting_error'])

    def test_reused_pid_does_not_archive_unrelated_arguments(self):
        output = ('999 S unrelated --secret=never-store-this\n'
                  f'1000 S python {self.run}/fleet-source/dev/fleet/fleet.py sweep --sweep fixture\n')
        with patch.object(finalize.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=output, stderr='')):
            result = finalize.controller_processes(self.run, {'pid': 999}, {'sweep': 'fixture'})
        self.assertEqual(result['state'], 'running')
        self.assertTrue(result['recorded_pid_reused'])
        self.assertNotIn('never-store-this', json.dumps(result))

    def test_mismatched_instance_ownership_tag_cannot_close_budget(self):
        def wrong_scope(region, operation, arguments):
            data = self.aws(region, operation, arguments)
            if operation == 'describe-instances':
                data['Reservations'][0]['Instances'][0]['Tags'][0]['Value'] = 'another-sweep'
            return data
        result = self.finish(aws=wrong_scope)
        self.assertFalse(result['cleanup_verified'])
        self.assertEqual(self.budget_calls, [])


if __name__ == '__main__':
    unittest.main()
