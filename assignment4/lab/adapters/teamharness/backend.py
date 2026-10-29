"""Course sandbox transport and task-scoped relay; no model workflow policy."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shlex
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .bridge import post


def course_request(body):
    """Keep the course wire surface explicit without disabling native execution.

    The course endpoint rejects the optional generation hint parallel_tool_calls.
    Multiple returned tool calls remain intact and execute under session policy.
    """
    request = json.loads(body)
    if not isinstance(request, dict):
        raise ValueError('Chat Completions request must be an object')
    omitted = []
    if 'parallel_tool_calls' in request:
        if type(request['parallel_tool_calls']) is not bool:
            raise ValueError('parallel_tool_calls must be boolean')
        request.pop('parallel_tool_calls')
        omitted.append('parallel_tool_calls')
    forwarded = json.dumps(request, ensure_ascii=False).encode() if omitted else body
    return forwarded, {'omitted_parameters': omitted,
        'original_sha256': hashlib.sha256(body).hexdigest(),
        'forwarded_sha256': hashlib.sha256(forwarded).hexdigest(),
        'meaning': 'Course wire compatibility only; native multi-tool responses and execution are unchanged.'}


class ModelRelay:
    """Expose Chat Completions and opt-in exact-patch grading using a task-local token.

    The course agent belongs to task and inference networks. The sandbox belongs
    only to the task network. Upstream credentials never enter the sandbox.
    """
    def __init__(self, sandbox_url, upstream, key, grade_callback=None, facts_callback=None, audit_root=None,
                 *, scheduler=None, task_id=None):
        self.token = secrets.token_urlsafe(32)
        self.scheduler, self.task_id = scheduler, task_id
        if scheduler is not None:
            scheduler.register_task(task_id)
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                if self.path not in ('/v1/chat/completions', '/v1/grade', '/v1/runtime-facts') or (self.path == '/v1/grade' and grade_callback is None):
                    self.send_error(404)
                    return
                if self.headers.get('Authorization') != 'Bearer ' + owner.token:
                    self.send_error(401)
                    return
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                if self.path in ('/v1/grade', '/v1/runtime-facts'):
                    try:
                        result = (grade_callback(json.loads(body)) if self.path == '/v1/grade' else
                                  facts_callback() if facts_callback else {'available': False})
                        raw, status = json.dumps(result).encode(), 200
                    except Exception as error:
                        raw, status = json.dumps({'error': type(error).__name__ + ': ' + str(error)}).encode(), 409
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    try:
                        self.wfile.write(raw)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                try:
                    forwarded, adaptation = course_request(body)
                    if audit_root is not None and adaptation['omitted_parameters']:
                        directory = Path(audit_root)
                        directory.mkdir(parents=True, exist_ok=True)
                        (directory / (uuid.uuid4().hex + '.json')).write_text(json.dumps(adaptation, indent=2))
                except (ValueError, TypeError):
                    self.send_error(400, 'Invalid course Chat Completions request')
                    return
                if scheduler is not None:
                    from .request_scheduler import RequestRejected
                    try:
                        response = scheduler.submit(task_id, self.headers.get('X-A4-Request-ID'), forwarded).result()
                    except RequestRejected as error:
                        response = error
                    self.send_response(response.status)
                    for name, value in response.headers.items():
                        if name.lower() not in ('content-length', 'connection', 'transfer-encoding'):
                            self.send_header(name, value)
                    self.send_header('Content-Length', str(len(response.body)))
                    self.end_headers()
                    try:
                        self.wfile.write(response.body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                req = urllib.request.Request(upstream.rstrip('/') + '/chat/completions',
                    data=forwarded, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
                try:
                    with urllib.request.urlopen(req) as response:
                        status, raw, content_type = response.status, response.read(), response.headers.get('Content-Type', 'application/json')
                except urllib.error.HTTPError as error:
                    with error:
                        status, raw, content_type = error.code, error.read(), error.headers.get('Content-Type', 'application/json')
                except (OSError, ValueError) as error:
                    status, raw, content_type = 502, json.dumps({'error': {'type': type(error).__name__,
                        'message': 'Course model relay transport failed'}}).encode(), 'application/json'
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
        self.server.daemon_threads = True
        host = urllib.parse.urlsplit(sandbox_url).hostname
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((socket.gethostbyname(host), 9))
            address = sock.getsockname()[0]
        self.url = f'http://{address}:{self.server.server_port}/v1'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class Remote:
    def __init__(self, workspace, archive):
        self.workspace, self.archive = workspace, Path(archive)
        self.archive.mkdir(parents=True, exist_ok=True)

    def write(self, path, value):
        post(self.workspace.url + '/write', {'path': str(path), 'content': value, 'cwd': self.workspace.root})

    def read_json(self, path):
        return json.loads(post(self.workspace.url + '/read', {'path': str(path), 'cwd': self.workspace.root})['content'])

    def command(self, command, deadline=None):
        ident = uuid.uuid4().hex
        intent = {'id': ident, 'command': command, 'deadline': deadline, 'started_at': time.time()}
        (self.archive / (ident + '.intent.json')).write_text(json.dumps(intent))
        chunks = []
        try:
            result = self.workspace.effect('shell', {'workspace': self.workspace.facts(), 'command': command,
                'shell': '/bin/bash', '_job_id': ident, 'yield_time_ms': 1000, 'max_output_bytes': 65536})
            while True:
                chunks.append(result.get('output', {}).get('content', ''))
                if result.get('state') != 'running':
                    break
                if deadline is not None and time.time() >= deadline:
                    result = self._stop(ident, chunks)
                    result['cutoff'] = True
                    break
                result = self.workspace.effect('job', {'workspace': self.workspace.facts(), 'id': ident,
                    'yield_time_ms': 1000, 'max_output_bytes': 65536})
        except Exception as error:
            # The durable identity exists before launch. Read/stop that same job;
            # never replay a possibly executed command after a lost reply.
            result = {'id': ident, 'state': 'unknown', 'transport_error': str(error)}
            try:
                observed = self.workspace.effect('observe', {'workspace': self.workspace.facts(), 'id': ident})
                if observed.get('state') == 'running':
                    observed = self._stop(ident, chunks)
                result.update(observed, transport_error=str(error))
            except Exception as cleanup_error:
                result['cleanup_error'] = str(cleanup_error)
        (self.archive / (ident + '.output')).write_text(''.join(chunks))
        receipt = {'id': ident, 'command': command, **result}
        (self.archive / (ident + '.json')).write_text(json.dumps(receipt))
        return receipt

    def _stop(self, ident, chunks):
        result = self.workspace.effect('job', {'workspace': self.workspace.facts(), 'id': ident,
            'action': 'kill', 'yield_time_ms': 1000, 'max_output_bytes': 65536})
        chunks.append(result.get('output', {}).get('content', ''))
        cleanup_end = time.monotonic() + 10
        while result.get('state') == 'running' and time.monotonic() < cleanup_end:
            result = self.workspace.effect('job', {'workspace': self.workspace.facts(), 'id': ident,
                'yield_time_ms': 1000, 'max_output_bytes': 65536})
            chunks.append(result.get('output', {}).get('content', ''))
        return result

    def checked(self, command):
        result = self.command(command)
        if result.get('exit_code') != 0 or result.get('state') != 'exited':
            raise RuntimeError('Course preparation/finalization command failed; see command receipts')
        return result

    def download(self, remote_path, destination, size, expected):
        started = time.time()
        digest = hashlib.sha256()
        count = 0
        # The finalized archive is immutable. Four independent range reads keep
        # memory bounded and amortize course /exec overhead without replaying any
        # effects. 512 KiB encodes below the 1,000,000-byte response cap.
        chunk = 524288
        def read_range(offset):
            length = min(chunk, size - offset)
            response = self.workspace.effect('patch', {'workspace': self.workspace.facts(),
                'path': str(remote_path), 'offset': offset, 'bytes': length})
            raw = base64.b64decode(response['base64'], validate=True)
            if (len(raw) != length or response.get('offset') != offset
                    or response.get('next_offset') != offset + length
                    or response.get('total_bytes') != size):
                raise RuntimeError('Evidence range identity mismatch')
            return raw
        with Path(destination).open('wb') as out, ThreadPoolExecutor(max_workers=4) as pool:
            for start in range(0, size, 4 * chunk):
                for raw in pool.map(read_range, range(start, min(size, start + 4 * chunk), chunk)):
                    out.write(raw)
                    digest.update(raw)
                    count += len(raw)
        if count != size or digest.hexdigest() != expected:
            raise RuntimeError('Evidence transfer identity mismatch')
        return {'started_at': started, 'finished_at': time.time(), 'bytes': count,
                'sha256': digest.hexdigest(), 'parallel_reads': 4, 'range_bytes': chunk}

    def install(self, runtime_tar, state, config, python):
        state = str(state)
        self.write(state + '/runtime.b64', base64.b64encode(Path(runtime_tar).read_bytes()).decode())
        self.write(state + '/config.json', json.dumps(config))
        script = ('import base64,pathlib,tarfile,io,os; p=pathlib.Path(' + repr(state) + '); '
                  'os.chmod(p/"config.json",0o600); '
                  'raw=base64.b64decode((p/"runtime.b64").read_bytes()); '
                  'tarfile.open(fileobj=io.BytesIO(raw)).extractall(p/"runtime"); '
                  '(p/"runtime.b64").unlink()')
        self.checked(shlex.quote(python) + ' -c ' + shlex.quote(script))
