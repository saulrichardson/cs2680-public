"""Saved-candidate admission uses actual archives and exact byte identities."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from lab.adapters.teamharness.regrade_candidate import candidate_inputs, test_setup


class SavedCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patch = b'saved patch bytes\n'
        self.sha = hashlib.sha256(self.patch).hexdigest()

    def archive(self, submitted=None):
        task = {'instance_id': 'task', 'sandbox_image': 'sha256:original', 'base_commit': 'base'}
        files = {'tasks.json': json.dumps({'0': task}).encode(),
                 'scaffold/model_patch_0.diff': self.patch if submitted is None else submitted,
                 'scaffold/pro_eval/0/replay/model.patch': self.patch,
                 'scaffold/pro_eval/0/grading-environment.json': json.dumps({'image': 'sha256:original'}).encode()}
        raw = self.root / 'source.tar'
        with tarfile.open(raw, 'w') as archive:
            for name, content in files.items():
                info = tarfile.TarInfo('task/' + name); info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        subprocess.run(['zstd', '-q', '-f', str(raw)], check=True)
        return raw.with_suffix('.tar.zst')

    def test_exact_saved_patch_is_copied_without_normalizing_bytes(self):
        task, grade = candidate_inputs(self.archive(), 'task', self.sha, self.root)
        self.assertEqual((self.root / 'candidate.patch').read_bytes(), self.patch)
        self.assertEqual(task['sandbox_image'], grade['image'])

    def test_submitted_and_graded_disagreement_refuses_admission(self):
        with self.assertRaisesRegex(ValueError, 'patch bytes'):
            candidate_inputs(self.archive(b'different bytes\n'), 'task', self.sha, self.root)
        self.assertFalse((self.root / 'candidate.patch').exists())

    def test_wrong_requested_hash_refuses_admission(self):
        with self.assertRaisesRegex(ValueError, 'patch bytes'):
            candidate_inputs(self.archive(), 'task', '0' * 64, self.root)
        self.assertFalse((self.root / 'candidate.patch').exists())

    def test_course_test_reset_keeps_its_logic_without_publishing_a_grade(self):
        script = ('cd /app 2>/dev/null || cd /testbed\ntrap cleanup_and_reward EXIT\n'
                  'git apply /tests/test_patch.patch\n'
                  '# Create temporary files for stdout and stderr\nrun_tests\n')
        setup = test_setup(script)
        self.assertIn('CANDIDATE_ROOT', setup)
        self.assertIn('git apply /tests/test_patch.patch', setup)
        self.assertNotIn('trap cleanup_and_reward EXIT', setup)
        self.assertNotIn('run_tests', setup)
        with self.assertRaisesRegex(ValueError, 'Unrecognized'):
            test_setup('unrecognized script')


if __name__ == '__main__':
    unittest.main()
