"""Real provider/relay effects: stable retries retrieve once; unknown work stays owned."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from src.api import Provider
from lab.adapters.teamharness import request_scheduler
from test_course_request_compatibility import ModelRelay


@contextmanager
def server(handler):
    service = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    service.daemon_threads = True
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{service.server_port}'
    finally:
        service.shutdown(); service.server_close(); thread.join()


class ModelAdmissionIntegration(unittest.TestCase):
    def exercise(self, *, error_status=None, drop_reply=False, partial_upstream=False,
                 error_message='upstream service failed'):
        upstream_calls, proxy_ids = [], []
        request = {'model': 'standard', 'messages': [
            {'role': 'assistant', 'content': 'Earlier work', 'reasoning_content': 'original reasoning'},
            {'role': 'user', 'content': 'Read the current result.'}],
            'tools': [], 'stream': False, 'temperature': .6, 'parallel_tool_calls': True}
        answer = ({'error': {'code': 503, 'message': error_message}} if error_status is not None else
                  {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'observed'}}],
                   'usage': {'prompt_tokens': 19, 'completion_tokens': 3, 'total_tokens': 22}})
        raw_answer = json.dumps(answer).encode()

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                upstream_calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(error_status or 200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw_answer)))
                self.end_headers()
                if partial_upstream:
                    self.wfile.write(raw_answer[:7]); self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close(); self.close_connection = True
                else:
                    self.wfile.write(raw_answer)

        with tempfile.TemporaryDirectory() as tmp, server(Upstream) as url:
            root = Path(tmp)
            scheduler = request_scheduler.RequestScheduler(url+'/v1', 'fixture-only', 1, root/'scheduler')
            with patch.dict(sys.modules, {'src.request_scheduler': request_scheduler}):
                relay = ModelRelay('http://127.0.0.1:1', url+'/v1', 'fixture-only',
                    audit_root=root/'adaptations', scheduler=scheduler, task_id='task-a')
                class Proxy(BaseHTTPRequestHandler):
                    def log_message(self, *_): pass
                    def do_POST(self):
                        raw = self.rfile.read(int(self.headers['Content-Length']))
                        ident = self.headers.get('X-A4-Request-ID'); proxy_ids.append(ident)
                        forwarded = urllib.request.Request(relay.url+'/chat/completions', data=raw,
                            headers={'Authorization':'Bearer '+relay.token, 'Content-Type':'application/json',
                                     'X-A4-Request-ID':ident})
                        with urllib.request.urlopen(forwarded) as response:
                            body = response.read()
                            self.send_response(response.status)
                            self.send_header('Content-Type', 'application/json')
                            self.send_header('Content-Length', str(len(body)))
                            if response.headers.get('X-A4-No-Retry'):
                                self.send_header('X-A4-No-Retry', response.headers['X-A4-No-Retry'])
                            self.end_headers()
                            if len(proxy_ids) == 1:
                                self.wfile.write(body[:7]); self.wfile.flush()
                                self.connection.shutdown(socket.SHUT_RDWR)
                                self.connection.close(); self.close_connection = True
                            else:
                                self.wfile.write(body)
                try:
                    with server(Proxy) as proxy:
                        endpoint = proxy+'/v1' if drop_reply else relay.url
                        with patch.dict(os.environ, {'A4_MODEL':'standard', 'A4_BASE_URL':endpoint,
                            'A4_API_KEY':relay.token, 'A4_API_PROTOCOL':'chat_completions'}):
                            provider = Provider(root/'provider')
                            try:
                                uncertain = error_status is not None or partial_upstream
                                if not uncertain:
                                    result = provider.call({'request':request, 'event':'integration', 'branch':'main'})
                                    self.assertEqual(result['raw_response'], answer)
                                else:
                                    with self.assertRaises(RuntimeError):
                                        provider.call({'request':request, 'event':'integration', 'branch':'main'})
                                receipts = [json.loads(row[0]) for row in provider.store.db.execute(
                                    "SELECT body FROM objects WHERE kind='transport_request' ORDER BY id")]
                                self.assertEqual(len(receipts), 2 if drop_reply else 1)
                                self.assertEqual(len({r['logical_request_id'] for r in receipts}), 1)
                                self.assertTrue(all(r['request'] == request for r in receipts))
                                responses = [json.loads(row[0]) for row in provider.store.db.execute(
                                    "SELECT body FROM objects WHERE kind='transport_response' ORDER BY id")]
                                if uncertain:
                                    self.assertEqual(responses[-1]['usage_state'], 'unknown')
                                    self.assertEqual(responses[-1]['relay_state'], 'uncertain')
                                    self.assertTrue(responses[-1]['relay_no_retry'])
                                if partial_upstream:
                                    self.assertEqual(responses[-1]['status'], 409)
                            finally:
                                provider.store.db.close()
                    self.assertEqual(len(upstream_calls), 1)
                    self.assertEqual(upstream_calls[0], {k:v for k,v in request.items() if k != 'parallel_tool_calls'})
                    snap = scheduler.snapshot()
                    self.assertEqual(snap['request_count'], 1)
                    self.assertEqual(snap['unknown_count'], int(uncertain))
                    self.assertEqual(snap['inflight'], int(uncertain))
                    drain = scheduler.finish_task('task-a')
                    self.assertEqual(drain['safe_to_refill'], not uncertain)
                    if drop_reply:
                        self.assertEqual(len(proxy_ids), 2)
                        self.assertEqual(proxy_ids[0], proxy_ids[1])
                finally:
                    relay.close(); scheduler.close()

    def test_lost_local_reply_retrieves_original_generation_and_preserves_request(self):
        self.exercise(drop_reply=True)

    def test_upstream_503_is_archived_once_without_regeneration_or_capacity_release(self):
        self.exercise(error_status=503)

    def test_http_200_error_envelope_is_not_retried_or_counted_as_freed_capacity(self):
        self.exercise(error_status=200)

    def test_partial_upstream_retains_unknown_usage_under_local_409_status(self):
        self.exercise(partial_upstream=True)

    def test_uncertain_error_mentioning_context_is_not_a_capacity_rejection(self):
        self.exercise(error_status=200, error_message='upstream failed while checking context window')


if __name__ == '__main__':
    unittest.main()
