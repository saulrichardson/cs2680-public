"""Bind a new course finalization to an empty submission after prior evaluations.

The provided controller preserves t.last across continue_task(). Its done path
then reuses that old grade when the new patch is empty. Instrument only new
frozen controller copies; the original empty-patch evaluator remains authoritative.
"""
import hashlib
from pathlib import Path


BOUNDARY = '''            self.finish_task(t)
            return self.respond(n, {"ok": True, "tests_failed": t.last[1], "tests_total": t.last[2]})'''
REPLACEMENT = '''            # A continued task's empty final patch is a new submission. t.last
            # still describes the preceding evaluation; finish_task must grade
            # the empty bytes through the original evaluator instead of reusing it.
            if not t.evaluated and patch_size(t.k) == 0:
                t.last = None
''' + BOUNDARY


def install(source):
    return install_controller(Path(source) / 'starter_code/evaluation_scripts/run_all.py')


def install_controller(path):
    path = Path(path)
    before = path.read_bytes()
    text = before.decode()
    if text.count(BOUNDARY) != 1 or 'if not t.evaluated and patch_size(t.k) == 0:' in text:
        raise ValueError('Unrecognized original course final-submission boundary')
    text = text.replace(BOUNDARY, REPLACEMENT)
    compile(text, str(path), 'exec')
    path.write_text(text)
    return {'before_sha256': hashlib.sha256(before).hexdigest(),
            'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'change': 'Grade new empty final bytes instead of reusing a preceding interactive grade',
            'original_evaluator_unchanged': True}
