"""Real HTTP stalls must not gate course task/run retirement.

The task witness deliberately uses the not-installed finalization branch. Its
dispatcher records empty submission/done calls; it supplies no repair or grade
claim. A second witness exercises the real Agent.run finally block directly.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from src import bridge
from lab.adapters.teamharness.request_scheduler import RequestScheduler

# The submission builder installs the shared bridge beside these adapter files.
with patch.dict(sys.modules, {'lab.adapters.teamharness.bridge': bridge}):
    adapter = importlib.import_module('lab.adapters.teamharness.agentic_loop')


BODY = json.dumps({'model': 'fixture', 'messages': [{'role': 'user', 'content': 'fixture request'}],
                   'stream': False}).encode()


@contextmanager
def upstream_stall():
    entered, release = threading.Event(), threading.Event()
    arrivals = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            arrivals.append(self.rfile.read(int(self.headers['Content-Length'])))
            entered.set()
            # A fixture latch, not a model or production request timeout.
            release.wait(10)
            raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': 'fixture complete'}}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', entered, release, arrivals
    finally:
        release.set()
        server.shutdown(); server.server_close(); worker.join(2)


@contextmanager
def in_directory(path):
    before = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)


def run_in_thread(operation):
    ended = threading.Event(); errors = []
    def run():
        try:
            operation()
        except BaseException as error:
            errors.append(error)
        finally:
            ended.set()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, ended, errors


class SchedulerRetirement(unittest.TestCase):
    def test_task_finish_closes_course_task_while_sent_request_still_occupies_slot(self):
        with tempfile.TemporaryDirectory() as tmp, in_directory(tmp), upstream_stall() as (url, entered, release, arrivals):
            calls = []
            class Dispatcher:
                def submit_patch(self, ident, patch_text):
                    calls.append(('submit_patch', ident, patch_text))
                def done(self, ident, reason, count):
                    calls.append(('done', ident, reason, count))
                    return {'fixture': 'not-installed task retirement only'}
            owner = adapter.Agent(Dispatcher())
            owner.model_scheduler = RequestScheduler(url, 'fixture-key', 1, Path('scheduler'))
            scheduler = owner.model_scheduler
            attempt = adapter.TaskAttempt(owner, {'task': 7}, {})
            # Real relay registration/close; no sandbox preparation or fabricated
            # source capture. Model work enters the scheduler's public API.
            attempt.relay = adapter.ModelRelay('http://127.0.0.1:9', url, 'fixture-key',
                                                scheduler=scheduler, task_id='7')
            worker = None
            try:
                active = scheduler.submit('7', 'active', BODY)
                self.assertTrue(entered.wait(2), 'Upstream request did not enter fixture')
                queued = scheduler.submit('7', 'same-task-queued', BODY)
                scheduler.register_task('8')
                other = scheduler.submit('8', 'other-task-queued', BODY)
                worker, ended, errors = run_in_thread(attempt.finish)
                self.assertTrue(ended.wait(2), 'Task finish blocked on the stalled upstream')
                self.assertFalse(errors, errors)
                self.assertFalse(release.is_set())
                self.assertEqual(calls, [('submit_patch', 7, ''), ('done', 7, 'sandbox_unavailable', 0)])
                self.assertNotIn(7, owner.active_tasks)
                self.assertIsNone(attempt.relay)
                self.assertFalse((attempt.archive / 'candidate.json').exists())
                self.assertFalse((attempt.archive / 'native-evidence.tar.gz').exists())
                drain = json.loads((attempt.archive / 'model-drain.json').read_text())
                self.assertFalse(drain['safe_to_refill'])
                self.assertEqual(drain['active'], [active.record['sequence']])
                cancelled = queued.result(timeout=1)
                self.assertEqual(cancelled.receipt['reason'], 'task_closed_before_dispatch')
                self.assertIsNone(cancelled.receipt['started_at'])
                self.assertFalse(other.done.is_set())
                self.assertEqual(len(arrivals), 1)
                self.assertEqual(scheduler.snapshot()['inflight'], 1)
                self.assertEqual(scheduler.snapshot()['active_upstreams'], 1)
                release.set()
                self.assertEqual(active.result(timeout=2).receipt['state'], 'completed')
                self.assertEqual(other.result(timeout=2).receipt['state'], 'completed')
                self.assertEqual(len(arrivals), 2)
                self.assertEqual(scheduler.snapshot()['inflight'], 0)
            finally:
                release.set()
                if worker is not None: worker.join(3)
                attempt.close()
                scheduler.close(wait=True, timeout=3)

    def test_agent_run_retires_queue_and_persists_active_capacity_without_waiting(self):
        with tempfile.TemporaryDirectory() as tmp, in_directory(tmp), upstream_stall() as (url, entered, release, arrivals):
            package = Path(tmp) / 'package'; package.mkdir()
            (package / 'campaign.json').write_text(json.dumps({'workers': 1, 'model_slots': 1}))
            class FixtureAgent(adapter.Agent):
                def worker(self, config):
                    self.model_scheduler.register_task('fixture-task')
                    self.active = self.model_scheduler.submit('fixture-task', 'active', BODY)
                    if not entered.wait(2):
                        raise AssertionError('Upstream request did not enter fixture')
                    self.queued = self.model_scheduler.submit('fixture-task', 'queued', BODY)
            agent = FixtureAgent(None); worker = None
            try:
                with patch.object(adapter, '__file__', str(package / 'agentic_loop.py')), patch.dict(os.environ, {
                    'CS2680_BASE_URL': url, 'CS2680_API_KEY': 'fixture-key'}):
                    worker, ended, errors = run_in_thread(agent.run)
                    self.assertTrue(ended.wait(2), 'Agent.run finally blocked on the stalled upstream')
                    self.assertFalse(errors, errors)
                self.assertFalse(release.is_set())
                root = Path('madsServe_logs/model-scheduler')
                retirement = json.loads((root / 'retirement.json').read_text())
                snapshot = json.loads((root / 'final.json').read_text())
                self.assertFalse(retirement['safe_to_refill'])
                self.assertEqual(retirement['active'], [agent.active.record['sequence']])
                self.assertTrue(snapshot['closed'])
                self.assertEqual((snapshot['queued'], snapshot['inflight'], snapshot['active_upstreams']), (0, 1, 1))
                cancelled = agent.queued.result(timeout=1)
                self.assertEqual(cancelled.receipt['reason'], 'task_closed_before_dispatch')
                self.assertIsNone(cancelled.receipt['started_at'])
                self.assertEqual(len(arrivals), 1)
                release.set()
                self.assertEqual(agent.active.result(timeout=2).receipt['state'], 'completed')
                self.assertEqual(agent.model_scheduler.snapshot()['inflight'], 0)
                self.assertEqual(len(arrivals), 1)
                # Retirement is an honest point-in-time snapshot; later known
                # completion updates the request receipt, not prior evidence.
                self.assertEqual(json.loads((root / 'final.json').read_text()), snapshot)
                request = root / 'requests' / f"{agent.active.record['sequence']:06d}" / 'receipt.json'
                self.assertEqual(json.loads(request.read_text())['state'], 'completed')
            finally:
                release.set()
                if worker is not None: worker.join(3)
                if agent.model_scheduler is not None:
                    agent.model_scheduler.close(wait=True, timeout=3)


if __name__ == '__main__':
    unittest.main()
