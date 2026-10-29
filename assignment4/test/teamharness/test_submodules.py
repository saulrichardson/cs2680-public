"""Offline snapshot and exact parent capture over local submodule dependencies."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest

from src.teamharness.candidates import snapshot, capture, grade_candidate, Grader, SUBMODULE_MANIFEST


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout


def commit(root, message='fixture'):
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', message)


class OfflineSubmoduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        git(self.source, 'init', '-q')
        (self.source / 'main.py').write_text('VALUE = 1\n')
        commit(self.source)
        self.dependency = self.root / 'dependency'
        self.dependency.mkdir()
        git(self.dependency, 'init', '-q')
        (self.dependency / 'dependency.py').write_text('VALUE = 3\n')
        (self.dependency / '.gitignore').write_text('__pycache__/\n*.cache\n')
        commit(self.dependency)
        git(self.source, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(self.dependency), 'vendor/lib')
        commit(self.source)

    def freeze(self, name='frozen'):
        destination = self.root / name
        result = snapshot(self.source, destination, self.root / (name + '-evidence'))
        return destination, result

    def test_initialized_gitfile_becomes_private_relocatable_git_directory(self):
        dependency = self.source / 'vendor/lib'
        self.assertTrue((dependency / '.git').is_file())
        (dependency / 'installed.cache').write_text('local dependency artifact')
        destination, result = self.freeze()
        copied = destination / 'vendor/lib'
        self.assertTrue((copied / '.git').is_dir())
        self.assertEqual((copied / 'installed.cache').read_text(), 'local dependency artifact')
        self.assertEqual(git(copied, 'rev-parse', 'HEAD'), git(dependency, 'rev-parse', 'HEAD'))
        self.assertEqual(git(copied, 'status', '--porcelain'), b'')
        self.assertEqual(result['submodules'][0]['state'], 'initialized')
        original_git = Path(git(dependency, 'rev-parse', '--absolute-git-dir').decode().strip())
        self.assertNotEqual((copied / '.git/HEAD').stat().st_ino, (original_git / 'HEAD').stat().st_ino)
        (copied / '.git/config').write_text((copied / '.git/config').read_text() + '\n[test]\n isolated = yes\n')
        self.assertNotIn(b'isolated', (original_git / 'config').read_bytes())
        shutil.rmtree(self.source)
        shutil.rmtree(self.dependency)
        self.assertTrue(git(copied, 'show', 'HEAD:dependency.py'))
        self.assertEqual(capture(destination, self.root / 'unchanged.patch', result['revision'])['bytes'], 0)

    def test_populated_uninitialized_gitlink_keeps_files_without_inventing_git(self):
        (self.source / 'vendor/lib/.git').unlink()
        shutil.rmtree(self.source / '.git/modules')
        destination, result = self.freeze()
        copied = destination / 'vendor/lib'
        self.assertFalse((copied / '.git').exists())
        self.assertEqual((copied / 'dependency.py').read_text(), 'VALUE = 3\n')
        self.assertEqual(result['submodules'][0]['state'], 'opaque')
        self.assertTrue(git(destination, 'submodule', 'status').startswith(b'-'))
        self.assertEqual(capture(destination, self.root / 'unchanged.patch', result['revision'])['bytes'], 0)
        (copied / 'new.cache').write_text('runtime cache')
        self.assertEqual(capture(destination, self.root / 'cache.patch', result['revision'])['bytes'], 0)
        (copied / 'dependency.py').write_text('VALUE = 9\n')
        with self.assertRaisesRegex(ValueError, 'Cannot represent changed nested'):
            capture(destination, self.root / 'omitted.patch', result['revision'])
        self.assertEqual((copied / 'dependency.py').read_text(), 'VALUE = 9\n')

    def test_absent_gitlink_stays_absent(self):
        shutil.rmtree(self.source / 'vendor/lib')
        shutil.rmtree(self.source / '.git/modules')
        destination, result = self.freeze()
        self.assertFalse((destination / 'vendor/lib').exists())
        self.assertEqual(result['source_submodules'][0]['state'], 'absent')
        self.assertEqual(result['submodules'], [])
        self.assertEqual(capture(destination, self.root / 'absent.patch', result['revision'])['bytes'], 0)

    def test_opaque_runtime_caches_obey_parent_ignore_rules(self):
        (self.source / 'vendor/lib/.git').unlink()
        shutil.rmtree(self.source / '.git/modules')
        (self.source / 'vendor/lib/.gitignore').unlink()
        (self.source / '.gitignore').write_text('*.pyc\n')
        commit(self.source)
        destination, result = self.freeze()
        cache = destination / 'vendor/lib/__pycache__'
        cache.mkdir()
        (cache / 'dependency.cpython-311.pyc').write_bytes(b'bytecode cache')
        self.assertEqual(capture(destination, self.root / 'cache.patch', result['revision'])['bytes'], 0)
        (destination / 'vendor/lib/dependency.py').write_text('VALUE = 12\n')
        with self.assertRaisesRegex(ValueError, 'Cannot represent changed nested'):
            capture(destination, self.root / 'changed.patch', result['revision'])

    def test_initialized_nested_source_edits_are_not_silently_omitted(self):
        destination, result = self.freeze()
        (destination / 'vendor/lib/dependency.py').write_text('VALUE = 99\n')
        with self.assertRaisesRegex(ValueError, 'superproject patch'):
            capture(destination, self.root / 'candidate.patch', result['revision'])
        with self.assertRaisesRegex(ValueError, 'superproject patch'):
            snapshot(destination, self.root / 'reviewer', self.root / 'reviewer-evidence')

    def test_dirty_original_dependency_rejected_with_source_preserved(self):
        (self.source / 'vendor/lib/dependency.py').write_text('uncommitted dependency change\n')
        with self.assertRaisesRegex(ValueError, 'dirty or differently checked-out'):
            self.freeze()
        self.assertEqual((self.source / 'vendor/lib/dependency.py').read_text(), 'uncommitted dependency change\n')

    def test_parent_patch_replay_keeps_dependency_and_manifest_exact(self):
        baseline, frozen = self.freeze()
        working = self.root / 'working'
        shutil.copytree(baseline, working)
        (working / 'main.py').write_text('VALUE = 2\n')
        candidate = capture(working, self.root / 'candidate.patch', frozen['revision'])
        grade = grade_candidate(baseline, frozen['revision'], candidate,
            Grader(('python3', '-c', 'from pathlib import Path; assert Path("main.py").read_text() == "VALUE = 2\\n"; assert Path("vendor/lib/dependency.py").read_text() == "VALUE = 3\\n"'), 'dependency-replay'), self.root / 'grade')
        self.assertEqual(grade['exit_code'], 0)
        self.assertTrue(grade['source_unchanged'])
        self.assertEqual(grade['before_sha256'], candidate['sha256'])
        evidence = json.loads((self.root / 'frozen-evidence/snapshot.json').read_text())
        self.assertEqual(evidence['submodules'], frozen['submodules'])
        self.assertTrue((working / '.git' / SUBMODULE_MANIFEST).is_file())

    def test_nested_initialized_gitlinks_have_independent_metadata(self):
        nested = self.root / 'nested'
        nested.mkdir()
        git(nested, 'init', '-q')
        (nested / 'nested.txt').write_text('nested source')
        commit(nested)
        lib = self.source / 'vendor/lib'
        git(lib, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(nested), 'nested')
        commit(lib)
        commit(self.source)
        destination, result = self.freeze()
        copied = destination / 'vendor/lib/nested'
        self.assertTrue((copied / '.git').is_dir())
        self.assertEqual((copied / 'nested.txt').read_text(), 'nested source')
        self.assertEqual(result['submodules'][0]['children'][0]['state'], 'initialized')
        self.assertEqual(capture(destination, self.root / 'nested.patch', result['revision'])['bytes'], 0)


if __name__ == '__main__':
    unittest.main()
