"""Failed setup closes real submissions; reserve retirement cannot wait for solvers."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from src import bridge


class AllocationFailures(unittest.TestCase):
    def run_in(self, operation):
        course = types.ModuleType('dispatcher')
        course.DispatcherShutdown = type('DispatcherShutdown', (Exception,), {})
        with tempfile.TemporaryDirectory() as temporary:
            before = Path.cwd()
            try:
                os.chdir(temporary)
                with patch.dict(sys.modules, {'dispatcher': course,
                        'lab.adapters.teamharness.bridge': bridge}):
                    adapter = importlib.import_module('lab.adapters.teamharness.agentic_loop')
                    operation(adapter)
            finally:
                os.chdir(before)

    def test_setup_failures_close_empty_submissions_and_surface_original_error(self):
        def scenario(adapter):
            class Dispatcher:
                pending = iter([{'task': 0}, {'task': 1}])
                def next_task(self): return next(self.pending, None)
                def submit_patch(self, ident, text):
                    Path(f'patch-{ident}').write_text(text)
                def done(self, ident, reason, calls):
                    assert Path(f'patch-{ident}').read_bytes() == b''
                    assert reason == 'preparation_failed' and calls == 0
                    Path(f'done-{ident}.json').write_text(json.dumps({'reason': reason}))
                    return {'tests_failed': 10, 'tests_total': 10}
            config = {'workflow': 'main', 'planning_boundary': {'pause_after_planning': True},
                      'allocation': {'count': 1, 'planning_workers': 2}}
            with patch.object(adapter.TaskAttempt, 'prepare', side_effect=OSError('original bootstrap failure')):
                with self.assertRaisesRegex(RuntimeError, 'No usable planning results'):
                    adapter.Agent(Dispatcher()).allocate(config)
            result = json.loads(Path('madsServe_logs/allocation/result.json').read_text())
            self.assertEqual(result['state'], 'planning_failed')
            self.assertEqual(len(result['failures']), 2)
            for ident in (0, 1):
                root = Path(f'madsServe_logs/{ident}/teamharness')
                self.assertIn('original bootstrap failure', (root/'planning-error.txt').read_text())
                self.assertTrue(Path(f'done-{ident}.json').is_file())
                self.assertEqual(json.loads((root/'grade.json').read_text())['tests_failed'], 10)
        self.run_in(scenario)

    def test_reserve_is_finished_before_selected_owner_can_return(self):
        def scenario(adapter):
            reserve_closed = threading.Event()
            class Dispatcher:
                pending = iter([{'task': 0}, {'task': 1}])
                def next_task(self): return next(self.pending, None)
            class Attempt:
                def __init__(self, owner, task, config):
                    self.ident = task['task']; self.task = task
                    self.deadline = time.time()+30
                    self.state = f'state-{self.ident}'; self.remote = self
                    self.archive = Path(f'task-{self.ident}'); self.archive.mkdir()
                def prepare(self): return True
                def execute(self, operation):
                    if operation == 'admit':
                        if not reserve_closed.wait(3):
                            raise RuntimeError('Selected owner waited on an unclosed reserve')
                    return {'state': 'exited', 'exit_code': 0}
                def read_json(self, path):
                    return {'state': 'planning_complete', 'deadline': self.deadline,
                        'planning': {'decision': {'decision': 'continue',
                            'assessment': {'readiness': 'repair' if self.ident == 0 else 'search', 'effort': 'small'}}}}
                def finish(self, *, discard=False):
                    Path(f'finished-{self.ident}').write_text('empty' if discard else 'candidate')
                    if discard: reserve_closed.set()
                def close(self): pass
            config = {'workflow': 'main', 'planning_boundary': {'pause_after_planning': True},
                      'allocation': {'count': 1, 'planning_workers': 2}}
            with patch.object(adapter, 'TaskAttempt', Attempt):
                adapter.Agent(Dispatcher()).allocate(config)
            result = json.loads(Path('madsServe_logs/allocation/result.json').read_text())
            self.assertEqual(result['selected'], [0])
            self.assertEqual(result['failures'], [])
            self.assertEqual(Path('finished-0').read_text(), 'candidate')
            self.assertEqual(Path('finished-1').read_text(), 'empty')
        self.run_in(scenario)
