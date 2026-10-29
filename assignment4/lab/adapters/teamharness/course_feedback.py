"""Task-scoped interactive grading: immutable patches, counts only, no retries."""
import hashlib
import json
from pathlib import Path
import threading
import time


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as out:
        json.dump(value, out, indent=2)


class CourseGrades:
    def __init__(self, dispatcher, task, root, deadline):
        self.dispatcher, self.task = dispatcher, task
        self.root, self.deadline = Path(root), deadline
        self.root.mkdir(parents=True, exist_ok=False)
        self.lock = threading.Lock()
        self.last = None
        self.uncertain = False
        self.attempts = {}

    def evaluate(self, request):
        with self.lock:
            if self.uncertain:
                raise RuntimeError('Prior course operation is uncertain; no automatic retry')
            if time.time() >= self.deadline:
                raise TimeoutError('Shared solving deadline expired before grading')
            raw = request['patch'].encode('utf-8')
            sha = digest(raw)
            if sha != request['course_sha256'] or not raw:
                raise ValueError('Nonempty exact course patch required')
            key = request['candidate_sha256']
            if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
                raise ValueError('Invalid candidate identity')
            if key in self.attempts:
                prior = self.attempts[key]
                if prior['course_sha256'] != sha:
                    raise ValueError('Candidate identity mapped to different course bytes')
                return {**prior, 'reused': True}
            index = len(self.attempts)
            folder = self.root / str(index)
            folder.mkdir(exist_ok=False)
            (folder / 'submitted.patch').write_bytes(raw)
            save(folder / 'intent.json', {'candidate_sha256': key, 'course_sha256': sha,
                'deadline': self.deadline, 'started_at': time.time(), 'feedback': 'counts_only'})
            # From this point a lost result cannot authorize replay of effects.
            self.uncertain = True
            if self.last is not None:
                self.dispatcher.continue_task(self.task)
            self.dispatcher.submit_patch(self.task, raw.decode('utf-8'))
            grade = self.dispatcher.evaluate(self.task)
            failed, total, attempt = (grade.get(k) for k in ('tests_failed', 'tests_total', 'attempt'))
            if any(type(v) is not int for v in (failed, total, attempt)) or not 0 <= failed <= total or attempt != index:
                raise ValueError('Invalid interactive course grade receipt')
            result = {'candidate_sha256': key, 'course_sha256': sha,
                'receipt': {'tests_failed': failed, 'tests_total': total, 'attempt': attempt},
                'passed': total > 0 and failed == 0, 'finished_at': time.time(),
                'after_deadline': time.time() >= self.deadline, 'feedback': 'counts_only'}
            save(folder / 'receipt.json', result)
            self.last = result
            self.attempts[key] = result
            self.uncertain = False
            return result

    def final_submit(self, raw):
        """Order final submission after any already-running grade finishes."""
        with self.lock:
            if self.uncertain:
                raise RuntimeError('Uncertain course evaluation; final submission cannot overwrite it')
            if self.last is not None:
                if digest(raw) == self.last['course_sha256']:
                    return  # done() retains this exact original grade.
                self.uncertain = True
                self.dispatcher.continue_task(self.task)
            self.dispatcher.submit_patch(self.task, raw.decode('utf-8'))
            self.uncertain = False
