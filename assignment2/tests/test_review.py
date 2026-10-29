"""The review uses edited source and real Git, without a provider or grader."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mads.review import capture, GitReviewError
from mads.store import Store, present, EvidenceError
from mads.tools import Tools, TOOLS, validate


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@invalid')
        (self.repo/'tracked.txt').write_text('old\n')
        (self.repo/'.gitignore').write_text('ignored\n')
        self.git('add', '.'); self.git('commit', '-qm', 'initial')
        self.store = Store(self.root/'run/journal')
        self.tools = Tools(self.repo, self.root/'run', self.store, 'Original β requirement\nNot an account.')
        self.addCleanup(self.tools.jobs.close)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args])

    def text(self, result):
        return Path(result['output']['path']).read_text()

    def test_task_tracked_staged_new_binary_and_ignored(self):
        (self.repo/'tracked.txt').write_text('staged\n')
        self.git('add', 'tracked.txt')
        (self.repo/'tracked.txt').write_text('working\n')
        (self.repo/'new.txt').write_text('new implementation\n')
        (self.repo/'bytes').write_bytes(b'\xff\x00')
        (self.repo/'ignored').write_text('must not include')
        status = self.git('status', '--porcelain')
        result = self.tools.review(output_chars=80)
        text = self.text(result)
        self.assertIn('Original β requirement\nNot an account.', text)
        self.assertIn('-old\n+working', text)
        self.assertIn('new implementation', text)
        self.assertIn('Binary/non-UTF-8', text)
        self.assertNotIn('must not include', text)
        self.assertEqual(result['scope']['untracked_total'], 2)
        self.assertIn('next_read', present(result['output'], 80)['observation_scope'])
        self.assertEqual(status, self.git('status', '--porcelain'))
        self.assertEqual(self.tools.effects, 'none')

    def test_literal_paths_and_outside_rejection(self):
        (self.repo/'a[1].txt').write_text('literal only')
        (self.repo/'a1.txt').write_text('not selected')
        text = self.text(self.tools.review(paths=['a[1].txt']))
        self.assertIn('literal only', text)
        self.assertNotIn('not selected', text)
        with self.assertRaisesRegex(ValueError, 'inside'):
            self.tools.review(paths=['../other'])
        with self.assertRaisesRegex(ValueError, 'git review failed'):
            self.tools.review(base='--help')

    def test_untracked_bounds_symlink_and_immutable_view(self):
        (self.repo/'00-link').symlink_to(self.root/'missing')
        for i in range(21):
            (self.repo/f'{i+1:02}.txt').write_text('x'*16001 if i == 0 else str(i))
        result = self.tools.review()
        text = self.text(result)
        self.assertIn('Symlink target', text)
        self.assertIn('File excerpt only', text)
        self.assertIn('Not expanded', text)
        self.assertEqual(result['scope']['untracked_expanded'], 20)
        (self.repo/'01.txt').write_text('newer')
        self.assertEqual(self.text(result), text)
        record = self.store.events[-1]
        self.assertEqual(record['kind'], 'change_review')

    def test_without_git_task_and_selected_snapshots_remain_available(self):
        import shutil
        shutil.rmtree(self.repo/'.git')
        (self.repo/'implementation.py').write_text('if cursor >= head: raise ValueError()\n')
        (self.repo/'subdir').mkdir()
        (self.repo/'outside').symlink_to(self.root, target_is_directory=True)
        result = self.tools.review(paths=['implementation.py', 'subdir'])
        text = self.text(result)
        self.assertIn(self.tools.original_task, text)
        self.assertIn('if cursor >= head', text)
        self.assertIn('Unavailable at sample', text)
        self.assertIn('[Unavailable; not an empty diff]', text)
        self.assertNotIn('[No tracked changes]', text)
        self.assertIsNone(result['base_commit'])
        self.assertFalse(result['scope']['tracked_diff'])
        self.assertIsNone(result['scope']['untracked_total'])
        self.assertEqual(result['scope']['selected_current_files'], 2)
        with self.assertRaisesRegex(ValueError, 'inside'):
            self.tools.review(paths=['outside/private'])
        default = self.tools.review()
        self.assertEqual(default['scope']['selected_current_files'], 0)
        self.assertTrue(default['scope']['task_only'])
        self.assertEqual(default['scope']['current_file_snapshots'], 0)
        self.assertIn('TASK ONLY', default['notice'])
        self.assertIn('explicit', default['suggested_next'])
        self.assertNotIn('Paths: whole repository', self.text(default))
        self.assertIn('TASK ONLY', self.text(default)[:250])
        self.assertFalse(result['scope']['task_only'])
        self.assertEqual(result['scope']['current_file_snapshots'], 1)
        self.assertFalse((self.repo/'.git').exists())
        self.assertEqual(self.tools.effects, 'none')

    def test_bad_base_in_available_git_is_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'git review failed'):
            self.tools.review(base='missing-commit')

    def test_unborn_head_returns_explicit_snapshots_without_inventing_a_diff(self):
        repo = self.root/'new-project'; repo.mkdir()
        subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
        (repo/'new.py').write_text('print("new project")\n')
        tools = Tools(repo, self.root/'new-run', self.store, 'Create a project')
        self.addCleanup(tools.jobs.close)
        review = tools.review(paths=['new.py'])
        self.assertFalse(review['scope']['tracked_diff'])
        self.assertEqual(review['scope']['current_file_snapshots'], 1)
        self.assertIn('HEAD comparison unavailable', review['scope']['git_unavailable'])
        self.assertIn('print("new project")', self.text(review))
        self.assertNotIn('[No tracked changes]', self.text(review))
        self.assertTrue(tools.review()['scope']['task_only'])
        with self.assertRaises(GitReviewError):
            tools.review(base='missing-commit')
        self.assertNotEqual(subprocess.run(['git', '-C', str(repo), 'rev-parse', '--verify', 'HEAD'],
            capture_output=True).returncode, 0)

    def test_automatic_capture_records_git_failure_but_keeps_evidence_errors_visible(self):
        real_run = subprocess.run
        def broken_diff(argv, **kwargs):
            if argv[:4] == ['git', '-C', str(self.repo.resolve()), 'diff']:
                return subprocess.CompletedProcess(argv, 128, b'', b'fixture: comparison unavailable')
            return real_run(argv, **kwargs)
        with patch('mads.review.subprocess.run', side_effect=broken_diff):
            with self.assertRaises(GitReviewError):
                self.tools.review()
            result = capture(self.tools)
        self.assertFalse(result['scope']['tracked_diff'])
        self.assertIn('comparison unavailable', result['scope']['git_unavailable'])
        self.assertEqual(self.store.events[-1]['event'], 'change_review_unavailable')
        with patch.object(self.store, 'blob', side_effect=EvidenceError('cannot save evidence')):
            with self.assertRaisesRegex(EvidenceError, 'cannot save evidence'):
                capture(self.tools)

    def test_schema_and_agent_wiring(self):
        validate('review', {'paths': ['tracked.txt'], 'output_chars': None})
        from mads.agent import Agent
        class Provider:
            config = {'model': 'fixture'}
        agent = Agent(self.repo, 'Exact new task', self.root/'agent-run', Provider(), session_role='reviewer')
        self.addCleanup(agent.tools.jobs.close)
        self.assertIn('Exact new task', self.text(agent.tools.review()))
        self.assertEqual(len([t for t in TOOLS if t['function']['name'] == 'review']), 1)


if __name__ == '__main__':
    unittest.main()
