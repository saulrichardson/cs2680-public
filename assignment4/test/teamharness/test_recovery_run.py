"""Offline recovery supervisor checks; no Docker, network or model calls."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / 'lab/adapters/teamharness/recovery_run.py'
spec = importlib.util.spec_from_file_location('recovery_supervisor_fixture', SOURCE)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class RecoverySupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'recovery'
        self.state = self.root / 'state'
        (self.state / 'operator-recovery').mkdir(parents=True)
        (self.state / 'run/agents').mkdir(parents=True)
        self.deadline = time.time() + 900
        self.container = 'owned-recovery-fixture'
        self.manifest = self.base / 'assignment3/dev/tasks/hard51.json'
        self.manifest.parent.mkdir(parents=True)
        self.entry = {'instance_id': 'instance-fixture', 'base_commit': 'frozenbase', 'docker_image': 'fixture:image'}
        supervisor.save(self.manifest, {'4': self.entry})
        supervisor.save(self.state / 'operator-recovery/manifest.json', {'key': '4', **self.entry,
            'container': self.container, 'deadline': self.deadline})
        supervisor.save(self.state / 'config.json', {'state': '/work/teamharness-0', 'deadline': self.deadline,
            'relay_token': 'fixture-secret-never-export', 'task': {}})
        supervisor.save(self.state / 'run/agents/manager.json', {'deadline': self.deadline})
        scripts = self.base / 'assignment3/starter_code/evaluation_scripts'
        scripts.mkdir(parents=True)
        # Real local shell performs a grader-like replay receipt. The production
        # path still copies its original evaluate_one.sh rather than this fixture.
        (scripts / 'evaluate_one.sh').write_text('''#!/bin/bash
set -euo pipefail
test "$A3_CACHE" = /data/a3cache
test "$SWEBENCH_PRO_OS" = /data/a3cache/SWE-bench_Pro-os
test "$A3_GRADING_ENVIRONMENT" = stock
test "$A3_DOCKER" = 'python3 /opt/a3fleet/assignment3/dev/grading_docker.py'
test "$1" = 4
mkdir -p "$3/replay"
cp "$2" "$3/replay/model.patch"
echo 1 > "$3/reward.txt"
echo 'Required tests: 1; Required tests that passed: 1'
''')
        self.calls = []
        self.worker_exited = True
        self.real_run = subprocess.run

    def output(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[-1].startswith('if [ -e'):
            return '/opt/agentpy/bin/python3'
        return json.dumps({'pid': 1234, 'process_state': None if self.worker_exited else 'S',
                           'exited': self.worker_exited})

    def popen(self, argv, **kwargs):
        self.calls.append(argv)
        self.assertIn('resume', argv[-1])
        self.assertNotIn('course_worker run ', argv[-1])
        (self.state / 'operator-recovery/resume-worker.pid').write_text('1234\n')
        return SimpleNamespace(pid=999, returncode=0, poll=lambda: 0)

    def run_command(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == 'bash':
            return self.real_run(argv, **kwargs)
        if 'finalize' in argv:
            candidate = self.state / 'candidate.patch'
            candidate.write_bytes(b'fixture exact candidate\n')
            supervisor.save(self.state / 'candidate.json', {'sha256': supervisor.digest(candidate)})
            with tarfile.open(self.state / 'evidence.tar.gz', 'w:gz') as archive:
                archive.add(candidate, arcname='candidate.patch')
            supervisor.save(self.state / 'transfer.json', {'bytes': (self.state / 'evidence.tar.gz').stat().st_size,
                'sha256': supervisor.digest(self.state / 'evidence.tar.gz')})
            return SimpleNamespace(returncode=0)
        if argv[:3] == ['docker', 'rm', '-f']:
            self.assertTrue((self.root / 'recovery-evidence.tar.gz').is_file())
            return SimpleNamespace(returncode=0, stdout=self.container, stderr='')
        raise AssertionError('Unexpected effect ' + repr(argv))

    def execute(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(supervisor.subprocess, 'check_output', self.output))
            stack.enter_context(patch.object(supervisor.subprocess, 'Popen', self.popen))
            stack.enter_context(patch.object(supervisor.subprocess, 'run', self.run_command))
            return supervisor.supervise(self.root, self.container, self.manifest)

    def test_real_local_grade_path_patch_triplet_and_secret_exclusion(self):
        # Restore Popen only for the actual local grader subprocess; its call is
        # recorded above, while every Docker operation remains mocked.
        real_popen = subprocess.Popen
        old = self.real_run
        def local_run(*args, **kwargs):
            with patch.object(supervisor.subprocess, 'Popen', real_popen):
                return old(*args, **kwargs)
        self.real_run = local_run
        result = self.execute()
        self.assertEqual(result['state'], 'complete')
        self.assertTrue(result['grade']['identity_verified'])
        self.assertEqual(result['grade']['reward'], '1')
        self.assertEqual(result['original_deadline'], self.deadline)
        self.assertEqual(result['original_outer_deadline'], self.deadline + 1800)
        intent = supervisor.read(self.root / 'grade.intent.json')
        self.assertLessEqual(intent['timeout_seconds'], self.deadline + 1800 - intent['started_at'] - 29)
        with tarfile.open(self.root / 'recovery-evidence.tar.gz') as archive:
            self.assertFalse(any('config.json' in m.name for m in archive.getmembers()))
            for member in archive.getmembers():
                if member.isfile():
                    self.assertNotIn(b'fixture-secret-never-export', archive.extractfile(member).read())
        self.assertEqual(sum('finalize' in c for c in self.calls), 1)
        starts = len(self.calls)
        with self.assertRaisesRegex(RuntimeError, 'durable intent'):
            self.execute()
        self.assertEqual(len(self.calls), starts)

    def test_renewed_or_expired_original_deadline_launches_nothing(self):
        config = supervisor.read(self.state / 'config.json')
        supervisor.save(self.state / 'config.json', {**config, 'deadline': self.deadline + 100})
        with self.assertRaisesRegex(ValueError, 'cannot renew'):
            self.execute()
        supervisor.save(self.state / 'config.json', config)
        with patch.object(supervisor.time, 'time', return_value=self.deadline + 1):
            with self.assertRaisesRegex(TimeoutError, 'already expired'):
                self.execute()
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / 'resume.intent.json').exists())

    def test_live_worker_after_client_exit_does_not_finalize_or_remove_container(self):
        self.worker_exited = False
        with self.assertRaisesRegex(RuntimeError, 'still exists'):
            self.execute()
        self.assertFalse(any('finalize' in c or c[:3] == ['docker', 'rm', '-f'] for c in self.calls))
        self.assertTrue((self.root / 'resume.intent.json').is_file())
        self.assertFalse((self.root / 'finalize.intent.json').exists())

    def test_grade_deadline_keeps_collection_headroom(self):
        self.assertEqual(supervisor.grade_seconds(1000, now=1100), 1670)
        self.assertEqual(supervisor.grade_seconds(5000, now=0), 3300)
        with self.assertRaises(TimeoutError):
            supervisor.grade_seconds(1000, now=2770)


if __name__ == '__main__':
    unittest.main()
