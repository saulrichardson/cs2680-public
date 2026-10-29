"""New final bytes, including empty bytes, cannot inherit an old course grade."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from hypothesis import given, settings, strategies as st

from lab.adapters.teamharness.course_submission import install


# Executable excerpt of the provided controller's done/finish_task contract.
# Docker/process ownership is outside this regression. Grading below reads and
# snapshots real submitted files; the independent oracle is the *new* bytes.
CONTROLLER = '''class Server:
    def finish_task(self, t):
        if t.last is None:
            t.evaluated = False
            start_grading(t)
            t.grading.wait(); finish_grading(t)
        t.live = False

    def respond(self, n, value):
        return value

    def handle(self, n, req):
        t = self.task
        op = req['op']
        if op == "done":
            if t.grading is not None or t.queued:
                return self.respond(n, {"ok": False, "error": "evaluation still running"})
            if patch_size(t.k) > 0 and not t.evaluated:
                start_grading(t, n_done=n)
                return
            self.finish_task(t)
            return self.respond(n, {"ok": True, "tests_failed": t.last[1], "tests_total": t.last[2]})
'''


class CourseSubmission(unittest.TestCase):
    @settings(max_examples=35, deadline=None)
    @given(total=st.integers(1, 5000), fraction=st.floats(0, 1, allow_nan=False),
           raw=st.sampled_from([b'', b'\n', b'exact changed candidate\n']), evaluated=st.booleans())
    def test_new_submission_cannot_reuse_an_old_result(self, total, fraction, raw, evaluated):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'starter_code/evaluation_scripts/run_all.py'
            path.parent.mkdir(parents=True); path.write_text(CONTROLLER)
            original = path.read_bytes()
            record = install(root)
            self.assertEqual(record['before_sha256'], hashlib.sha256(original).hexdigest())
            submitted = root / 'patch.diff'; submitted.write_bytes(raw)
            graded = []
            previous = (1, 0, total) if fraction < .5 else (0, max(1, int(total*fraction)), total)
            task = SimpleNamespace(k=0, last=previous, evaluated=evaluated, queued=False,
                                   live=True, grading=None)

            def start_grading(t, n_done=None):
                # Whitespace is also an empty repair to the original evaluator,
                # but remains distinct bytes and must travel through grading.
                patch = submitted.read_bytes()
                (root / 'graded.patch').write_bytes(patch)
                graded.append((patch, n_done))
                t.grading = SimpleNamespace(wait=lambda: None)

            def finish_grading(t):
                t.last = (0, total, total)
                t.evaluated = True
                t.grading = None

            scope = {'patch_size': lambda _: submitted.stat().st_size,
                     'start_grading': start_grading, 'finish_grading': finish_grading}
            exec(compile(path.read_text(), str(path), 'exec'), scope)
            server = scope['Server'](); server.task = task
            result = server.handle(7, {'op': 'done'})
            if evaluated:
                self.assertEqual([], graded)
                self.assertEqual((result['tests_failed'], result['tests_total']), previous[1:])
            else:
                self.assertEqual(graded, [(raw, None if not raw else 7)])
                self.assertEqual((root / 'graded.patch').read_bytes(), raw)
                if not raw:
                    self.assertEqual(result, {'ok': True, 'tests_failed': total, 'tests_total': total})
                else:
                    self.assertIsNone(result)  # Original asynchronous done path.
            changed = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'Unrecognized'):
                install(root)
            self.assertEqual(path.read_bytes(), changed)

    def test_unknown_controller_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root / 'starter_code/evaluation_scripts/run_all.py'
            path.parent.mkdir(parents=True); path.write_text('print("unrecognized")\n')
            with self.assertRaisesRegex(ValueError, 'Unrecognized'):
                install(root)
            self.assertEqual(path.read_text(), 'print("unrecognized")\n')
