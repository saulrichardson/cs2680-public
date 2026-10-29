"""Real HTTP checks for shared admission, identity, disconnect and retirement.

No live model, cloud, generated repair or performance claim. All raw fixture
requests/results and scheduler receipts are retained under a fresh --output.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
from pathlib import Path
import socket
import sys
import threading
import time
import types
import unittest
import urllib.error
import urllib.request


HERE = Path(__file__).resolve().parent
package = types.ModuleType('scheduler_fixture')
package.__path__ = [str(HERE), str(HERE.parents[2] / 'src')]
sys.modules['scheduler_fixture'] = package
module = importlib.import_module('scheduler_fixture.request_scheduler')
RequestScheduler, RequestRejected = module.RequestScheduler, module.RequestRejected


def payload(name, mode='gate'):
    # Deliberate whitespace and unicode catch reserialization at this boundary.
    return json.dumps({'model': 'fixture', 'messages': [{'role': 'user', 'content': 'é ' + name}],
                       'name': name, 'mode': mode, 'max_tokens': 811, 'temperature': 0.7},
                      ensure_ascii=False, indent=1).encode()


def answer(name, finish='stop', usage=None):
    return json.dumps({'id': name, 'choices': [{'index': 0, 'finish_reason': finish,
                        'message': {'role': 'assistant', 'content': 'exact é bytes'}}],
                       'usage': usage if usage is not None else {'prompt_tokens': 17, 'completion_tokens': 9}},
                      ensure_ascii=False, indent=2).encode()


class Upstream:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir()
        self.condition = threading.Condition()
        self.received, self.gates, self.active, self.maximum = [], {}, 0, 0
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                request = json.loads(raw)
                name, mode = request['name'], request['mode']
                with owner.condition:
                    owner.received.append({'name': name, 'raw': raw, 'auth': self.headers.get('Authorization')})
                    owner.active += 1
                    owner.maximum = max(owner.maximum, owner.active)
                    gate = owner.gates.setdefault(name, threading.Event())
                    ordinal = len(owner.received)
                    (directory / f'{ordinal:03d}-request.body').write_bytes(raw)
                    owner.condition.notify_all()
                try:
                    if mode in ('gate', 'gate_503') and not gate.wait(10):
                        raise AssertionError('Test did not release upstream gate')
                    if mode == 'drop':
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        return
                    status, result = 200, answer(name)
                    if mode in ('429', '503', 'gate_503'):
                        status = 503 if mode == 'gate_503' else int(mode)
                        result = b'{"error":{"code":' + str(status).encode() + b',"message":"fixture error"}}'
                    elif mode == 'error200':
                        result = b'{"error":{"code":503,"message":"uncertain backend"}}'
                    elif mode == 'length':
                        result = answer(name, 'length')
                    elif mode == 'partial_usage':
                        result = answer(name, usage={'prompt_tokens': True, 'completion_tokens': 9})
                    elif mode == 'malformed':
                        result = b'{"broken":'
                    (directory / f'{ordinal:03d}-response.body').write_bytes(result)
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(result) + (23 if mode == 'partial' else 0)))
                    self.end_headers()
                    self.wfile.write(result)
                    self.wfile.flush()
                    if mode == 'partial':
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                finally:
                    with owner.condition:
                        owner.active -= 1
                        owner.condition.notify_all()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def wait_received(self, count):
        with self.condition:
            if not self.condition.wait_for(lambda: len(self.received) >= count, timeout=5):
                raise AssertionError(f'Expected {count} upstream requests; saw {len(self.received)}')
            return [r['name'] for r in self.received]

    def release(self, name):
        with self.condition:
            self.gates.setdefault(name, threading.Event()).set()

    def close(self):
        with self.condition:
            for gate in self.gates.values():
                gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class Checks(unittest.TestCase):
    output = None

    def setUp(self):
        self.directory = self.output / self._testMethodName
        self.directory.mkdir()
        self.upstream = Upstream(self.directory / 'upstream')
        self.schedulers, self.relays = [], []

    def tearDown(self):
        self.upstream.close()
        for relay in self.relays:
            relay.close()
        for scheduler in self.schedulers:
            result = scheduler.close(wait=True, timeout=5)
            self.assertFalse(result['active'], 'A test leaked an active upstream worker')
            (scheduler.archive / 'final.json').write_text(json.dumps(scheduler.snapshot(), indent=2) + '\n')

    def scheduler(self, slots=3, tasks=('a', 'b', 'c')):
        scheduler = RequestScheduler(self.upstream.url, 'fixture-upstream-secret', slots,
                                     self.directory / f'scheduler-{len(self.schedulers)}')
        self.schedulers.append(scheduler)
        for task in tasks:
            scheduler.register_task(task)
        return scheduler

    def test_global_cap_exact_bytes_and_retries(self):
        scheduler = self.scheduler()
        handles = [scheduler.submit('abc'[i % 3], f'id-{i}', payload(f'r{i}')) for i in range(9)]
        self.upstream.wait_received(3)
        self.assertEqual(scheduler.snapshot()['active_upstreams'], 3)
        self.assertEqual(scheduler.snapshot()['queued'], 6)
        with ThreadPoolExecutor(max_workers=10) as pool:
            retries = list(pool.map(lambda _: scheduler.submit('a', 'id-0', payload('r0')), range(10)))
        self.assertTrue(all(h is handles[0] for h in retries))
        with self.assertRaises(RequestRejected):
            scheduler.submit('a', 'id-0', payload('changed'))
        for name in [f'r{i}' for i in range(9)]:
            self.upstream.release(name)
        for index, handle in enumerate(handles):
            response = handle.result(5)
            self.assertEqual(response.body, answer(f'r{index}'))
            self.assertEqual(response.status, 200)
            self.assertEqual(response.receipt['state'], 'completed')
            self.assertNotIn('X-A4-No-Retry', response.headers)
            self.assertTrue(response.receipt['usage_known'])
        self.assertLessEqual(self.upstream.maximum, 3)
        self.assertEqual(len(self.upstream.received), 9)
        for request in self.upstream.received:
            self.assertEqual(request['raw'], payload(request['name']))
            self.assertEqual(request['auth'], 'Bearer fixture-upstream-secret')
        self.assertEqual(scheduler.snapshot()['inflight'], 0)
        self.assertEqual(scheduler.submit('a', 'id-0', payload('r0')).result().body, answer('r0'))
        self.assertEqual(len(self.upstream.received), 9)
        # A later deliberate identical generation receives a fresh UUID.
        self.assertEqual(scheduler.submit('a', 'new-id', payload('r0')).result(5).body, answer('r0'))
        self.assertEqual(len(self.upstream.received), 10)
        metadata = '\n'.join(p.read_text() for p in scheduler.archive.rglob('receipt.json'))
        self.assertNotIn('fixture-upstream-secret', metadata)

    def test_round_robin_tasks_and_fifo_owner_queue(self):
        scheduler = self.scheduler(1)
        first = scheduler.submit('a', 'a0', payload('a0'))
        self.upstream.wait_received(1)
        handles = [first]
        for name in ('a1', 'a2', 'a3', 'b0', 'c0'):
            handles.append(scheduler.submit(name[0], name, payload(name)))
        expected = ['a0', 'a1', 'b0', 'c0', 'a2', 'a3']
        for i, name in enumerate(expected):
            self.assertEqual(self.upstream.wait_received(i + 1), expected[:i + 1])
            self.upstream.release(name)
        for handle in handles:
            self.assertEqual(handle.result(5).receipt['state'], 'completed')

    def test_real_relay_disconnect_does_not_release_generation(self):
        # Exercise the actual maintained ModelRelay, not a test reimplementation.
        ModelRelay = importlib.import_module('scheduler_fixture.backend').ModelRelay
        scheduler = self.scheduler(1, ('a', 'b'))
        relay = ModelRelay('http://127.0.0.1:9', self.upstream.url, 'fixture-upstream-secret',
                           scheduler=scheduler, task_id='a')
        self.relays.append(relay)
        raw = payload('disconnected')
        sock = socket.create_connection(('127.0.0.1', relay.server.server_port))
        wire = (f'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {relay.token}\r\n'
                f'X-A4-Request-ID: stable-disconnect\r\nContent-Length: {len(raw)}\r\nConnection: close\r\n\r\n').encode() + raw
        sock.sendall(wire)
        self.upstream.wait_received(1)
        sock.close()
        queued = scheduler.submit('b', 'b0', payload('b0'))
        self.assertEqual(scheduler.snapshot()['inflight'], 1)
        self.assertEqual(scheduler.snapshot()['queued'], 1)
        with self.assertRaises(TimeoutError):
            queued.result(0.01)
        self.assertEqual(scheduler.snapshot()['inflight'], 1)
        self.upstream.release('disconnected')
        self.upstream.wait_received(2)
        self.upstream.release('b0')
        queued.result(5)
        request = urllib.request.Request(f'http://127.0.0.1:{relay.server.server_port}/v1/chat/completions', data=raw,
                                        headers={'Authorization': 'Bearer ' + relay.token,
                                                 'X-A4-Request-ID': 'stable-disconnect'})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertIsNone(response.headers.get('X-A4-No-Retry'))
            self.assertEqual(response.read(), answer('disconnected'))
        self.assertEqual(len(self.upstream.received), 2)
        receipt = scheduler.snapshot()['requests'][0]
        self.assertTrue(receipt['response_complete'])
        self.assertEqual(receipt['state'], 'completed')

    def test_known_rejection_is_terminal_and_does_not_replay(self):
        scheduler = self.scheduler(1)
        raw = payload('rejected', '429')
        response = scheduler.submit('a', 'r', raw).result(5)
        self.assertEqual(response.status, 429)
        self.assertEqual(response.body, b'{"error":{"code":429,"message":"fixture error"}}')
        self.assertEqual(response.headers['X-A4-No-Retry'], '1')
        self.assertEqual(response.receipt['state'], 'failed')
        self.assertEqual(scheduler.snapshot()['inflight'], 0)
        self.assertEqual(scheduler.submit('a', 'r', raw).result().body, response.body)
        self.assertEqual(len(self.upstream.received), 1)
        self.assertTrue(scheduler.finish_task('a')['safe_to_refill'])

    def test_uncertain_5xx_error_envelope_and_lost_transport_keep_capacity(self):
        for mode in ('503', 'error200', 'drop', 'partial', 'malformed'):
            with self.subTest(mode=mode):
                scheduler = self.scheduler(1)
                raw = payload(mode, mode)
                response = scheduler.submit('a', 'uncertain', raw).result(5)
                self.assertEqual(response.receipt['state'], 'uncertain')
                self.assertEqual(scheduler.snapshot()['inflight'], 1)
                self.assertEqual(scheduler.snapshot()['unknown_count'], 1)
                self.assertEqual(response.headers['X-A4-No-Retry'], '1')
                if mode == '503':
                    self.assertEqual(response.status, 503)
                    self.assertEqual(response.body, b'{"error":{"code":503,"message":"fixture error"}}')
                elif mode == 'error200':
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.body, b'{"error":{"code":503,"message":"uncertain backend"}}')
                elif mode == 'partial':
                    self.assertEqual(response.status, 409)
                    self.assertEqual(response.receipt['upstream_status'], 200)
                    self.assertFalse(response.receipt['response_complete'])
                    self.assertEqual((scheduler.archive / 'requests/000001/response.body').read_bytes(), answer(mode))
                self.assertIs(scheduler.submit('a', 'uncertain', raw), scheduler._by_sequence[1])
                with self.assertRaises(RequestRejected):
                    scheduler.submit('a', 'new-id-not-safe', raw)
                with self.assertRaises(RequestRejected) as unavailable:
                    scheduler.submit('b', 'never-sent', payload('never-sent-' + mode))
                self.assertEqual(unavailable.exception.receipt['reason'], 'upstream_capacity_unknown')
                before = time.monotonic()
                drain = scheduler.finish_task('a', wait=True)
                self.assertLess(time.monotonic() - before, 1)
                self.assertFalse(drain['safe_to_refill'])
                self.assertEqual(drain['uncertain'], [1])
                closed = scheduler.close(wait=True)
                self.assertFalse(closed['safe_to_refill'])
                self.assertEqual(scheduler.snapshot()['inflight'], 1)

    def test_exhausted_uncertain_capacity_settles_queued_and_rejects_new_work(self):
        scheduler = self.scheduler(3, tasks=('a', 'b', 'c', 'd'))
        original = [scheduler.submit(task, 'first', payload(task, 'gate_503')) for task in ('a', 'b', 'c')]
        self.upstream.wait_received(3)
        queued = scheduler.submit('d', 'waiting', payload('waiting'))
        self.assertFalse(queued.done.is_set())
        self.assertEqual(scheduler.snapshot()['queued'], 1)
        for task in ('a', 'b', 'c'):
            self.upstream.release(task)
        for handle in original:
            self.assertEqual(handle.result(5).receipt['state'], 'uncertain')
        response = queued.result(5)
        self.assertEqual(response.status, 409)
        self.assertEqual(response.headers['X-A4-No-Retry'], '1')
        self.assertEqual(response.receipt['reason'], 'upstream_capacity_unknown')
        self.assertIsNone(response.receipt['sent_at'])
        self.assertIsNone(response.receipt['started_at'])
        self.assertIn('not dispatched', json.loads(response.body)['error']['message'])
        with self.assertRaises(RequestRejected) as future:
            scheduler.submit('d', 'new-work', payload('new-work'))
        self.assertEqual(future.exception.receipt['reason'], 'upstream_capacity_unknown')
        self.assertIs(scheduler.submit('a', 'first', payload('a', 'gate_503')), original[0])
        snapshot = scheduler.snapshot()
        self.assertEqual((snapshot['active_upstreams'], snapshot['unknown_count'], snapshot['inflight'], snapshot['queued']), (0, 3, 3, 0))
        self.assertEqual(len(self.upstream.received), 3)
        # Original uncertainty and capacity survive task retirement and close.
        scheduler.finish_task('a', wait=False)
        self.assertFalse(scheduler.close(wait=False)['safe_to_refill'])
        self.assertEqual(scheduler.snapshot()['unknown_count'], 3)

    def test_terminal_archive_failure_does_not_strand_undispatched_requests(self):
        scheduler = self.scheduler(1)
        active = scheduler.submit('a', 'active', payload('active'))
        self.upstream.wait_received(1)
        queued = scheduler.submit('b', 'queued', payload('queued'))
        write = scheduler._write
        def lost_terminal(handle):
            if handle.record['state'] == 'completed':
                raise OSError('Injected terminal receipt publication failure')
            write(handle)
        scheduler._write = lost_terminal
        self.upstream.release('active')
        self.assertEqual(active.result(5).receipt['reason'], 'terminal_archive_failed')
        result = queued.result(5)
        self.assertEqual(result.receipt['reason'], 'scheduler_archive_failed')
        self.assertIsNone(result.receipt['sent_at'])
        self.assertTrue(scheduler.snapshot()['archive_error'])
        self.assertEqual(scheduler.snapshot()['unknown_count'], 1)
        self.assertEqual(len(self.upstream.received), 1)

    def test_partial_uncertainty_keeps_known_capacity_usable(self):
        scheduler = self.scheduler(2)
        unknown = scheduler.submit('a', 'unknown', payload('unknown', '503')).result(5)
        self.assertEqual(unknown.receipt['state'], 'uncertain')
        completed = scheduler.submit('b', 'healthy', payload('healthy', 'length')).result(5)
        self.assertEqual(completed.receipt['state'], 'completed')
        self.assertEqual(scheduler.snapshot()['unknown_count'], 1)
        self.assertEqual(scheduler.snapshot()['inflight'], 1)
        self.assertEqual(len(self.upstream.received), 2)

    def test_finish_cancels_queued_but_waits_for_actual_sent_work(self):
        scheduler = self.scheduler(1)
        active = scheduler.submit('a', 'first', payload('first'))
        self.upstream.wait_received(1)
        queued = scheduler.submit('a', 'second', payload('second'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(scheduler.finish_task, 'a', True)
            self.assertEqual(queued.result(5).receipt['reason'], 'task_closed_before_dispatch')
            self.assertFalse(future.done())
            self.assertEqual(scheduler.snapshot()['inflight'], 1)
            with self.assertRaises(RequestRejected):
                scheduler.submit('a', 'after-close', payload('after-close'))
            self.upstream.release('first')
            self.assertTrue(future.result(5)['safe_to_refill'])
        self.assertEqual(active.result().body, answer('first'))
        self.assertEqual([r['name'] for r in self.upstream.received], ['first'])

    def test_close_wait_bound_is_not_a_generation_timeout(self):
        scheduler = self.scheduler(1)
        active = scheduler.submit('a', 'active', payload('active'))
        self.upstream.wait_received(1)
        result = scheduler.close(wait=True, timeout=0.01)
        self.assertFalse(result['safe_to_refill'])
        self.assertEqual(result['active'], [1])
        self.assertEqual(scheduler.snapshot()['inflight'], 1)
        self.upstream.release('active')
        self.assertEqual(active.result(5).receipt['state'], 'completed')
        self.assertTrue(scheduler.close(wait=True)['safe_to_refill'])

    def test_usage_unknown_and_length_are_not_solver_policy(self):
        scheduler = self.scheduler(1)
        length = scheduler.submit('a', 'length', payload('length', 'length')).result(5)
        self.assertEqual(length.body, answer('length', 'length'))
        self.assertEqual(length.receipt['state'], 'completed')
        self.assertEqual(length.receipt['finish_reasons'], ['length'])
        unknown = scheduler.submit('a', 'partial-usage', payload('partial-usage', 'partial_usage')).result(5)
        self.assertEqual(unknown.receipt['state'], 'completed')
        self.assertIsNone(unknown.receipt['prompt_tokens'])
        self.assertEqual(unknown.receipt['completion_tokens'], 9)
        self.assertFalse(unknown.receipt['usage_known'])
        self.assertEqual(scheduler.snapshot()['inflight'], 0)

    def test_empty_archive_and_nonstream_identity_boundary(self):
        scheduler = self.scheduler()
        for identity, body in ((None, payload('missing')), ('stream', b'{"stream":true}'), ('array', b'[]')):
            with self.assertRaises(RequestRejected):
                scheduler.submit('a', identity, body)
        self.assertEqual(scheduler.snapshot()['refusal_count'], 3)
        self.assertEqual(scheduler.snapshot()['request_count'], 0)
        with self.assertRaises(ValueError):
            RequestScheduler(self.upstream.url, 'key', 3, scheduler.archive)
        self.assertEqual(len(self.upstream.received), 0)

    def test_connection_refusal_is_known_before_send(self):
        scheduler = self.scheduler(1)
        # Choose a local unused port, then close the reservation. On macOS a
        # bound, non-listening socket can stall connect instead of refusing it.
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            from urllib.parse import urlsplit
            scheduler._url = urlsplit(f'http://127.0.0.1:{reserved.getsockname()[1]}/v1/chat/completions')
        scheduler._connect_timeout = 0.2
        response = scheduler.submit('a', 'refused', payload('refused')).result(5)
        self.assertEqual(response.status, 502)
        self.assertEqual(response.receipt['state'], 'failed')
        self.assertIsNone(response.receipt['sent_at'])
        self.assertEqual(response.headers['X-A4-No-Retry'], '1')
        self.assertEqual(scheduler.snapshot()['inflight'], 0)
        self.assertTrue(scheduler.finish_task('a')['safe_to_refill'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    Checks.output = args.output.resolve()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    report = {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
              'passed': result.wasSuccessful(), 'source_sha256': hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
              'scope': 'Local stdlib HTTP/thread lifecycle mechanics; no model quality, cloud or performance claim'}
    (Checks.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
