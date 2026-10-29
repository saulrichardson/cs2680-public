"""Exact candidate capture and real external grading contracts."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from src.teamharness.candidates import Grader, grade_candidate, snapshot, capture
from src.workspace import git


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        git(self.source, 'init', '-q')
        (self.source / 'value.py').write_text('value = 1\n')
        git(self.source, 'add', '.')
        git(self.source, '-c', 'user.name=Test', '-c', 'user.email=test@local.invalid',
            'commit', '-qm', 'base')
        (self.source / 'value.py').write_text('value = 2\n')
        (self.source / 'untracked.txt').write_text('starting file\n')
        self.frozen = snapshot(self.source, self.root / 'baseline', self.root / 'freeze')
        self.work = self.root / 'candidate'
        shutil.copytree(self.root / 'baseline', self.work)
        (self.work / 'value.py').write_text('value = 3\n')
        self.candidate = capture(self.work, self.root / 'candidate.patch', self.frozen['revision'])

    def grade(self, args, name):
        return grade_candidate(self.root / 'baseline', self.frozen['revision'], self.candidate,
                               Grader(tuple(args)), self.root / name)

    def test_dirty_start_and_exact_replay(self):
        self.assertEqual((self.root / 'baseline/value.py').read_text(), 'value = 2\n')
        self.assertEqual((self.root / 'baseline/untracked.txt').read_text(), 'starting file\n')
        result = self.grade([sys.executable, '-B', '-c', 'import value; assert value.value == 3'], 'passed')
        self.assertEqual(result['exit_code'], 0)
        self.assertTrue(result['source_unchanged'])
        self.assertEqual(result['before_sha256'], self.candidate['sha256'])
        self.assertFalse(result['official'])
        self.assertEqual((self.source / 'value.py').read_text(), 'value = 2\n')

    def test_external_cache_success_is_not_portable_candidate_evidence(self):
        # Model-local tests can see a shared cache that is absent from the patch.
        # A deliberately dependency-controlled replay exposes the omission.
        cache = self.root/'shared-cache'
        cache.mkdir()
        (cache/'needed_dependency.py').write_text('VALUE = 42\n')
        (self.work/'value.py').write_text('from needed_dependency import VALUE\nvalue = VALUE\n')
        script = 'import sys; sys.path[:0] = sys.argv[1:]; import value; assert value.value == 42'
        subprocess.run([sys.executable, '-I', '-B', '-c', script, str(self.work), str(cache)], check=True)
        self.candidate = capture(self.work, self.root/'cache-dependent.patch', self.frozen['revision'])
        args = [sys.executable, '-I', '-B', '-c', script, '{workspace}']
        rejected = self.grade(args, 'fresh-missing-dependency')
        self.assertNotEqual(rejected['exit_code'], 0)
        self.assertIn('needed_dependency', Path(rejected['stderr_path']).read_text())
        self.assertEqual(rejected['before_sha256'], self.candidate['sha256'])
        (self.work/'needed_dependency.py').write_bytes((cache/'needed_dependency.py').read_bytes())
        self.candidate = capture(self.work, self.root/'portable.patch', self.frozen['revision'])
        accepted = self.grade(args, 'fresh-portable-dependency')
        self.assertEqual(accepted['exit_code'], 0)
        self.assertEqual(accepted['before_sha256'], self.candidate['sha256'])
        self.assertTrue(accepted['source_unchanged'])

    def test_nonzero_is_executed_not_infrastructure(self):
        result = self.grade([sys.executable, '-c', 'raise SystemExit(7)'], 'failed')
        self.assertEqual((result['state'], result['exit_code']), ('exited', 7))

    def test_missing_command_is_launch_error(self):
        result = self.grade(['/nonexistent/teamharness-grader'], 'missing')
        self.assertEqual(result['state'], 'launch_error')
        self.assertNotIn('exit_code', result)

    def test_checker_mutation_is_visible(self):
        result = self.grade([sys.executable, '-c',
                             'from pathlib import Path; Path("value.py").write_text("value = 4\\n")'], 'mutated')
        self.assertEqual(result['exit_code'], 0)
        self.assertFalse(result['source_unchanged'])
        self.assertNotEqual(result['before_sha256'], result['after_sha256'])

    def test_patch_tamper_rejected_before_execution(self):
        Path(self.candidate['patch_path']).write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.grade([sys.executable, '-c', 'raise SystemExit(0)'], 'tampered')

    def test_existing_grade_not_replayed(self):
        args = [sys.executable, '-c', 'raise SystemExit(0)']
        self.grade(args, 'once')
        with self.assertRaises(FileExistsError):
            self.grade(args, 'once')
