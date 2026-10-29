"""Live assigned source preparation uses real Git state and exact replay."""
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

from src.teamharness.candidates import snapshot, capture, grade_candidate, Grader
from src.teamharness.workspaces import prepare_live_workspace, bind_capture_base
from src.teamharness.agents import snapshot as private_snapshot


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout


def commit(root):
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@local.invalid', 'commit', '-qm', 'fixture')


class LiveWorkspaces(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        git(self.source, 'init', '-q')
        (self.source / 'main.txt').write_text('original\n')
        (self.source / '.gitignore').write_text('*.cache\n')
        commit(self.source)

    def prepare(self):
        self.frozen = snapshot(self.source, self.root / 'baseline', self.root / 'freeze-evidence')
        return prepare_live_workspace(self.source, self.frozen, self.root / 'live-evidence')

    def test_subdirectory_commands_keep_cwd_and_capture_the_whole_repository(self):
        from src.teamharness.agents import AgentManager
        from src.teamharness.tools import ToolRuntime
        nested = self.source/'package'
        nested.mkdir(); (nested/'nested.txt').write_text('starting\n'); commit(self.source)
        frozen = snapshot(nested, self.root/'baseline', self.root/'freeze')
        live = prepare_live_workspace(nested, frozen, self.root/'live')
        self.assertEqual(live['workspace'], str(self.source))
        manager = AgentManager(self.root/'agents', lambda _: None)
        try:
            ident = manager.create('main', 'Work in the package', Path(live['workspace']),
                capture_base=live['base'], working_directory=nested)
            runtime = ToolRuntime(manager, ident, manager.descriptor(ident))
            result = runtime.execute('exec_command', {'command': 'pwd; printf changed > ../main.txt'}, 'a'*64)
            self.assertEqual(result['exit_code'], 0)
            self.assertIn(str(nested), result['output']['content'])
            candidate = capture(self.source, self.root/'nested.patch', live['base'])
            self.assertIn(b'changed', Path(candidate['patch_path']).read_bytes())
            child = manager.create('investigator', 'Observe the package', self.source, private=True, parent=ident)
            descriptor = manager.descriptor(child)
            self.assertEqual(Path(descriptor['working_directory']), Path(descriptor['workspace'])/'package')
            child_runtime = ToolRuntime(manager, child, descriptor)
            observed = child_runtime.execute('exec_command', {'command': 'pwd'}, 'b'*64)
            self.assertIn(descriptor['working_directory'], observed['output']['content'])
        finally:
            manager.shutdown()

    def test_dirty_start_preserves_head_index_and_replays_without_double_applying(self):
        (self.source / 'main.txt').write_text('staged\n')
        git(self.source, 'add', 'main.txt')
        (self.source / 'main.txt').write_text('unstaged\n')
        (self.source / 'untracked.txt').write_text('untracked starting source\n')
        (self.source / 'installed.cache').write_text('mutable installed bytes')
        (self.source / 'external').symlink_to(self.root / 'outside')
        head = git(self.source, 'rev-parse', 'HEAD')
        index = (self.source / '.git/index').read_bytes()
        refs = git(self.source, 'show-ref')
        fetch_head = self.source / '.git/FETCH_HEAD'
        fetch_head.write_bytes(b'pre-existing fetch receipt\n')
        config = (self.source / '.git/config').read_bytes()
        spec = self.prepare()
        self.assertEqual(spec['workspace'], str(self.source))
        self.assertEqual(git(self.source, 'rev-parse', 'HEAD'), head)
        self.assertEqual((self.source / '.git/index').read_bytes(), index)
        self.assertEqual(git(self.source, 'show-ref'), refs)
        self.assertEqual(fetch_head.read_bytes(), b'pre-existing fetch receipt\n')
        self.assertEqual((self.source / '.git/config').read_bytes(), config)
        self.assertEqual(spec['starting_candidate']['bytes'], 0)
        self.assertEqual(git(self.source, 'show', ':main.txt'), b'staged\n')
        (Path(spec['workspace']) / 'main.txt').write_text('candidate\n')
        self.assertEqual((self.source / 'main.txt').read_text(), 'candidate\n')
        candidate = capture(self.source, self.root / 'candidate.patch', spec['base'])
        self.assertNotIn(b'untracked starting source', Path(candidate['patch_path']).read_bytes())
        receipt = grade_candidate(Path(self.frozen['workspace']), spec['base'], candidate,
            Grader(('python3', '-c', 'from pathlib import Path; assert Path("main.txt").read_text() == "candidate\\n"; assert Path("untracked.txt").read_text() == "untracked starting source\\n"')), self.root / 'grade')
        self.assertEqual(receipt['exit_code'], 0)
        self.assertTrue(receipt['source_unchanged'])
        self.assertEqual((self.source / '.git/index').read_bytes(), index)
        self.assertEqual((self.root / 'baseline/main.txt').read_text(), 'unstaged\n')
        self.assertNotEqual((self.source / 'installed.cache').stat().st_ino,
                            (self.root / 'baseline/installed.cache').stat().st_ino)
        self.assertEqual((self.root / 'baseline/external').readlink(), self.root / 'outside')

    def test_private_child_can_import_unreachable_starting_base(self):
        (self.source / 'initial.txt').write_text('initial dirty\n')
        spec = self.prepare()
        (self.source / 'main.txt').write_text('main later\n')
        private = private_snapshot(self.source, self.root / 'child/workspace')
        child = Path(private['workspace'])
        bind_capture_base(child, self.source, spec['base'])
        candidate = capture(child, self.root / 'child.patch', spec['base'])
        self.assertIn(b'main later', Path(candidate['patch_path']).read_bytes())
        (child / 'main.txt').write_text('private child\n')
        self.assertEqual((self.source / 'main.txt').read_text(), 'main later\n')
        # Deliberately shared helpers simply keep this same live directory/base.
        self.assertEqual(capture(self.source, self.root / 'shared.patch', spec['base'])['sha256'], candidate['sha256'])

    def test_preparation_does_not_reset_changes_observed_after_freeze(self):
        frozen = snapshot(self.source, self.root / 'baseline', self.root / 'evidence')
        (self.source / 'main.txt').write_text('concurrent change\n')
        with self.assertRaisesRegex(RuntimeError, 'source changed since baseline'):
            prepare_live_workspace(self.source, frozen, self.root / 'live')
        self.assertEqual((self.source / 'main.txt').read_text(), 'concurrent change\n')

    def test_running_watcher_sees_edits_in_assigned_directory(self):
        # A pre-existing process watches the original pathname through preparation.
        watcher = subprocess.Popen(['python3', '-u', '-c',
            'from pathlib import Path; import sys; print(Path("main.txt").read_text(), flush=True); sys.stdin.readline(); print(Path("main.txt").read_text(), flush=True)'],
            cwd=self.source, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(watcher.stdout.readline(), 'original\n')
            self.assertEqual(watcher.stdout.readline(), '\n')
            spec = self.prepare()
            (Path(spec['workspace']) / 'main.txt').write_text('seen by watcher\n')
            output, _ = watcher.communicate('\n', timeout=5)
            self.assertIn('seen by watcher', output)
        finally:
            if watcher.poll() is None:
                watcher.kill()
                watcher.communicate()

    def test_initialized_and_absent_dependencies_preserve_original_git_state(self):
        dependency = self.root / 'dependency'
        dependency.mkdir()
        git(dependency, 'init', '-q')
        (dependency / 'lib.txt').write_text('dependency source\n')
        commit(dependency)
        git(self.source, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(dependency), 'vendor/lib')
        git(self.source, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(dependency), 'vendor/absent')
        commit(self.source)
        shutil.rmtree(self.source / 'vendor/absent')
        initialized = self.source / 'vendor/lib'
        gitdir = Path(git(initialized, 'rev-parse', '--absolute-git-dir').decode().strip())
        original_index = (gitdir / 'index').read_bytes()
        original_head = (gitdir / 'HEAD').read_bytes()
        original_gitfile = (initialized / '.git').read_bytes()
        spec = self.prepare()
        self.assertEqual((gitdir / 'index').read_bytes(), original_index)
        self.assertEqual((gitdir / 'HEAD').read_bytes(), original_head)
        self.assertEqual((initialized / '.git').read_bytes(), original_gitfile)
        self.assertFalse((self.source / 'vendor/absent').exists())
        child = Path(private_snapshot(self.source, self.root / 'child/workspace')['workspace'])
        bind_capture_base(child, self.source, spec['base'])
        self.assertEqual(capture(child, self.root / 'child.patch', spec['base'])['bytes'], 0)
        (child / 'vendor/lib/lib.txt').write_text('child mutation\n')
        self.assertEqual((initialized / 'lib.txt').read_text(), 'dependency source\n')
        with self.assertRaisesRegex(ValueError, 'superproject patch'):
            capture(child, self.root / 'unrepresentable.patch', spec['base'])

    def test_live_opaque_dependency_changes_are_concrete_capture_limits(self):
        dependency = self.root / 'dependency'
        dependency.mkdir()
        git(dependency, 'init', '-q')
        (dependency / 'lib.txt').write_text('dependency source\n')
        commit(dependency)
        git(self.source, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(dependency), 'vendor/lib')
        commit(self.source)
        (self.source / 'vendor/lib/.git').unlink()
        spec = self.prepare()
        (self.source / 'vendor/lib/installed.cache').write_text('cache')
        self.assertEqual(capture(self.source, self.root / 'cache.patch', spec['base'])['bytes'], 0)
        (self.source / 'vendor/lib/lib.txt').write_text('changed nested source\n')
        with self.assertRaisesRegex(ValueError, 'changed nested submodule/dependency'):
            capture(self.source, self.root / 'nested.patch', spec['base'])
        self.assertEqual((self.source / 'vendor/lib/lib.txt').read_text(), 'changed nested source\n')


if __name__ == '__main__':
    unittest.main()
