"""Course feedback ordering and uncertainty over real patch replay and HTTP."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import urllib.request
import urllib.error

from lab.adapters.teamharness.course_feedback import CourseGrades


class Dispatcher:
    def __init__(self, root):
        self.root = root
        self.attempt = 0
        self.calls = []
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        (root / 'answer').write_text('wrong\n')
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=T', '-c', 'user.email=t@x', 'commit', '-qm', 'base'], check=True)

    def continue_task(self, task):
        self.calls.append('continue')
        self.attempt += 1

    def submit_patch(self, task, patch):
        self.calls.append('submit')
        self.patch = patch

    def evaluate(self, task):
        self.calls.append('evaluate')
        subprocess.run(['git', '-C', str(self.root), 'reset', '--hard', 'HEAD'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'clean', '-fd'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'apply', '-'], input=self.patch.encode(), check=True)
        check = subprocess.run(['sh', '-c', 'test "$(cat answer)" = right'], cwd=self.root)
        return {'tests_failed': int(check.returncode != 0), 'tests_total': 1, 'attempt': self.attempt}


def request(value):
    patch = 'diff --git a/answer b/answer\n--- a/answer\n+++ b/answer\n@@ -1 +1 @@\n-wrong\n+' + value + '\n'
    sha = hashlib.sha256(patch.encode()).hexdigest()
    return {'patch': patch, 'candidate_sha256': sha, 'course_sha256': sha}


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.d = Dispatcher(self.root / 'repo')
        self.g = CourseGrades(self.d, 0, self.root / 'grades', time.time() + 60)

    def test_real_relay_worker_dirty_start_replay(self):
        spec = importlib.util.spec_from_file_location('src.feedback_test_backend',
            Path(__file__).resolve().parents[2] / 'lab/adapters/teamharness/backend.py')
        backend = importlib.util.module_from_spec(spec); spec.loader.exec_module(backend)
        ModelRelay = backend.ModelRelay
        from src.teamharness.candidates import snapshot, capture
        spec = importlib.util.spec_from_file_location('src.teamharness.feedback_test_worker',
            Path(__file__).resolve().parents[2] / 'lab/adapters/teamharness/course_worker.py')
        worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
        source = self.d.root
        base = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        # A dirty starting source must be retained when translating synthetic-base patches.
        (source / 'initial.txt').write_text('preexisting source change')
        state = self.root / 'state'
        frozen = snapshot(source, state / 'run/baseline', state / 'run/source')
        baseline = Path(frozen['workspace'])
        (baseline / 'answer').write_text('right\n')
        candidate = capture(baseline, state / 'review.patch', frozen['revision'])
        relay = ModelRelay('http://127.0.0.1:1', 'http://127.0.0.1:1', 'unused-secret', self.g.evaluate)
        try:
            config = {'state': str(state), 'task': {'workdir': str(source)}, 'course_base': base,
                      'deadline': time.time() + 60, 'base_url': relay.url, 'relay_token': relay.token}
            result = worker.feedback_client(config)(candidate)
            self.assertTrue(result['passed'])
            self.assertEqual(candidate['sha256'], result['candidate_sha256'])
            self.assertNotEqual(candidate['sha256'], result['course_sha256'])
            self.assertEqual('preexisting source change', (source / 'initial.txt').read_text())
            self.assertFalse(list((state / 'interactive-candidates').glob('*/replay')))
            req = urllib.request.Request(relay.url + '/grade', data=b'{}')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req)
            self.assertEqual(401, error.exception.code)
        finally:
            relay.close()

    def test_fail_then_pass_exact_bytes_and_final_reuse(self):
        first = self.g.evaluate(request('still wrong'))
        second = self.g.evaluate(request('right'))
        self.assertFalse(first['passed'])
        self.assertTrue(second['passed'])
        self.assertEqual(1, second['receipt']['attempt'])
        self.assertTrue(self.g.evaluate(request('right'))['reused'])
        self.g.final_submit(request('right')['patch'].encode())
        self.assertEqual(['submit', 'evaluate', 'continue', 'submit', 'evaluate'], self.d.calls)
        for i, value in enumerate(('still wrong', 'right')):
            self.assertEqual(request(value)['patch'], (self.root / f'grades/{i}/submitted.patch').read_text())

    def test_uncertain_result_never_replayed_or_overwritten(self):
        def lost(task):
            self.d.calls.append('evaluate')
            raise ConnectionError('receipt lost')
        self.d.evaluate = lost
        with self.assertRaises(ConnectionError):
            self.g.evaluate(request('right'))
        with self.assertRaises(RuntimeError):
            self.g.evaluate(request('right'))
        with self.assertRaises(RuntimeError):
            self.g.final_submit(b'')
        self.assertEqual(['submit', 'evaluate'], self.d.calls)
        self.assertTrue((self.root / 'grades/0/intent.json').exists())

    def test_deadline_and_hash_reject_before_effects(self):
        r = request('right'); r['course_sha256'] = 'a' * 64
        with self.assertRaises(ValueError):
            self.g.evaluate(r)
        self.g.deadline = time.time() - 1
        with self.assertRaises(TimeoutError):
            self.g.evaluate(request('right'))
        self.assertEqual([], self.d.calls)

    def test_inflight_grade_finishes_after_deadline_without_new_solve_window(self):
        original = self.d.evaluate
        def late(task):
            self.g.deadline = time.time() - 1
            return original(task)
        self.d.evaluate = late
        result = self.g.evaluate(request('right'))
        self.assertTrue(result['after_deadline'])
        self.g.final_submit(request('right')['patch'].encode())
        with self.assertRaises(TimeoutError):
            self.g.evaluate(request('other'))
        self.assertEqual(['submit', 'evaluate'], self.d.calls)

    def test_different_final_patch_starts_new_attempt(self):
        self.g.evaluate(request('still wrong'))
        self.g.final_submit(request('right')['patch'].encode())
        self.assertEqual(['submit', 'evaluate', 'continue', 'submit'], self.d.calls)
        self.assertEqual(0, self.d.evaluate(0)['tests_failed'])
