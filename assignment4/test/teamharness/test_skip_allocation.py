"""Public package and real lifecycle witnesses for optional fixed skip count.

Set TEAMHARNESS_SKIP_EVIDENCE to a fresh directory to retain all packages, HTTP
requests, original sessions, subprocess logs, candidate and grading receipts.
These scripted public repairs establish mechanics, not model repair quality.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from lab.adapters.teamharness.build import build


ROOT = Path(__file__).resolve().parents[2]


class SkipAllocationPackage(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get('TEAMHARNESS_SKIP_EVIDENCE')
        if retained:
            self.root = Path(retained).resolve() / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(prefix='team-skip-')
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name)

    def test_public_builder_freezes_skip_and_preserves_count_and_default_modes(self):
        receipt = build(self.root / 'skip', skip_tasks=13)
        campaign = json.loads((self.root / 'skip/src/campaign.json').read_text())
        self.assertEqual(campaign['allocation'], {'skip_count': 13, 'planning_workers': 3})
        self.assertTrue(campaign['planning_boundary']['pause_after_planning'])
        self.assertEqual(receipt['campaign'], campaign)
        with tarfile.open(self.root / 'skip/src/team-runtime.tar.gz') as archive:
            self.assertEqual(json.load(archive.extractfile('src/campaign.json')), campaign)
        for relative, expected in receipt['files'].items():
            self.assertEqual(hashlib.sha256((self.root / 'skip' / relative).read_bytes()).hexdigest(), expected)
        self.assertEqual((self.root / 'skip/src/teamharness/allocation.py').read_bytes(),
                         (ROOT / 'src/teamharness/allocation.py').read_bytes())
        count = build(self.root / 'count', select_tasks=13)['campaign']
        self.assertEqual(count['allocation'], {'count': 13, 'planning_workers': 3})
        self.assertTrue(count['planning_boundary']['pause_after_planning'])
        default = build(self.root / 'default')['campaign']
        self.assertNotIn('allocation', default)
        self.assertFalse(default['planning_boundary'].get('pause_after_planning', False))

    def test_invalid_skip_or_ambiguous_mode_has_no_package_effect(self):
        values = [{'skip_tasks': value} for value in (-1, True, 1.5)]
        values += [{'skip_tasks': 13, 'select_tasks': 13}, {'skip_tasks': 13, 'planning_boundary': False},
                   {'skip_tasks': 13, 'planning_workers': 0}]
        for ordinal, value in enumerate(values):
            destination = self.root / str(ordinal)
            with self.assertRaises(ValueError):
                build(destination, **value)
            self.assertFalse(destination.exists(), value)
        destination = self.root / 'ambiguous-cli'
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'lab/adapters/teamharness/build.py'),
            '--output', str(destination), '--skip-tasks', '13', '--select-tasks', '13'],
            cwd=ROOT, capture_output=True, text=True)
        (self.root / 'ambiguous-cli.log').write_text(result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(destination.exists())

    def lifecycle(self, name, *, size, skip=None, fail=None, model_slots=None):
        package = self.root / (name + '-package')
        if skip is None:
            build(package, select_tasks=2, planning_workers=2)
        else:
            # Exercise the public CLI as well as the builder function. The
            # original package allowance remains unchanged across plan/admit.
            command = [sys.executable, '-B', str(ROOT / 'lab/adapters/teamharness/build.py'),
                       '--output', str(package), '--skip-tasks', str(skip)]
            if model_slots is not None:
                command += ['--model-slots', str(model_slots), '--planning-workers', '5']
            result = subprocess.run(command, cwd=ROOT,
                capture_output=True, text=True)
            (self.root / (name + '-build.log')).write_text(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = self.root / (name + '-evidence')
        command = [sys.executable, '-B', str(ROOT / 'lab/adapters/teamharness/check_allocation.py'),
                   '--package', str(package), '--output', str(evidence), '--task-count', str(size)]
        if skip is not None:
            command += ['--skip-count', str(skip)]
        if fail is not None:
            command += ['--fail-selected-finalization', str(fail)]
        log = self.root / (name + '-lifecycle.log')
        with log.open('w') as stream:
            result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
        self.assertEqual(result.returncode, 0, log.read_text()[-16000:])
        report = json.loads((evidence / 'CHECK.json').read_text())
        self.assertTrue(report['passed'])
        self.assertEqual(report['tasks_finished'], size)
        self.assertTrue(report['same_session_resumed'])
        self.assertTrue(report['deterministic_legacy_ranking_preserved'])
        self.assertTrue(report['discarded_edits_not_submitted'])
        self.assertTrue(report['no_automatic_backfill'])
        self.assertTrue(report['all_requests_reconstructed'])
        return report

    def test_twenty_six_tasks_skip_thirteen_without_failure_backfill(self):
        report = self.lifecycle('skip13', size=26, skip=13, fail=0, model_slots=3)
        self.assertEqual(set(report['selected']), set(range(13)))
        self.assertEqual(report['target'], 13)
        self.assertEqual(report['nonselected_empty_submissions'], 13)
        self.assertEqual({row['task_id'] for row in report['recorded_failures']}, {0})
        self.assertEqual(len(report['selected_identities']), 13)
        self.assertEqual(len({row['base'] for row in report['selected_identities']}), 13)
        self.assertEqual(set(report['finalizations_each_once'].values()), {1})
        self.assertEqual(report['scheduler']['cap'], 3)
        self.assertEqual(report['scheduler']['persisted_peak_inflight'], 3)
        self.assertEqual(report['scheduler']['observed_provider_peak'], 3)
        self.assertTrue(report['scheduler']['all_task_request_body_identities_match'])

    def test_small_queue_clamps_to_zero_without_implementation(self):
        report = self.lifecycle('small', size=5, skip=13)
        self.assertEqual(report['selected'], [])
        self.assertEqual(report['target'], 0)
        self.assertEqual(report['nonselected_empty_submissions'], 5)
        self.assertEqual(report['selected_identities'], [])
        self.assertEqual(report['recorded_failures'], [])

    def test_existing_select_tasks_real_package_remains_compatible(self):
        report = self.lifecycle('count', size=3)
        self.assertEqual(report['selected'], [0, 1])
        self.assertTrue(report['completion_takeover_after_allocation'])


if __name__ == '__main__':
    unittest.main()
