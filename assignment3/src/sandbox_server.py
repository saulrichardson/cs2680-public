#!/usr/bin/env python3
"""Standalone course-compatible bootstrap server; coding tools run in the solver.

Protocol follows course snapshot 7562690a, src/sandbox_server.py. Fresh stdlib
implementation; preserves /health, /exec, /read, /write bootstrap contracts.
"""
from __future__ import annotations

import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path('/app')
RUNTIME = Path('/tmp/a3')
BOOTSTRAP = Path('/tmp/a3-bootstrap')


def resolve(path: str, cwd: str | None = None) -> Path:
    value = Path(path)
    value = (value if value.is_absolute() else Path(cwd or ROOT) / value).resolve()
    if not any(value == root or root in value.parents for root in (ROOT, RUNTIME, BOOTSTRAP, Path('/work'))):
        raise ValueError('Bootstrap path must be within repository or an owned bootstrap/runtime directory')
    return value


def execute(command: str, cwd: str | None, timeout: int) -> dict:
    timeout = max(1, min(int(timeout), 900))
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(command, shell=True, cwd=cwd or ROOT, stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        size = output.tell()
        output.seek(0)
        data = output.read(32 * 1024 * 1024)
    if len(data) < size:
        raise ValueError('Bootstrap output exceeds 32 MiB; use /archive for large artifacts')
    return {'output': data.decode(errors='replace'), 'exit_code': proc.returncode, 'timed_out': timed_out}


def evidence_files(runtime: Path):
    """Archive observations and snapshots without copying the working checkout.

    The original source snapshot is environment input, not an observation.
    It can be large and is reconstructed by the supplied task image; export
    owner evidence separately before an in-place recovery reset.
    """
    for folder, directories, names in os.walk(runtime, followlinks=False):
        directory = Path(folder)
        if directory == runtime:
            directories[:] = [name for name in directories if name not in ('runtime', 'intake-source')]
        directories.sort()
        for name in sorted(names):
            path = directory / name
            if path.is_file() and not path.is_symlink() and name != 'launch.json':
                yield path


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status: int, payload: dict):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/health':
            self.send_json(200, {'ok': True, 'root': str(ROOT), 'python': sys.executable})
        else:
            self.send_json(404, {'error': 'Unknown bootstrap route'})

    def do_POST(self):
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            cwd = payload.get('cwd', str(ROOT))
            if self.path == '/exec':
                result = execute(payload['command'], cwd, payload.get('timeout_s', 300))
            elif self.path == '/read':
                result = {'content': resolve(payload['path'], cwd).read_text()}
            elif self.path == '/write':
                path = resolve(payload['path'], cwd)
                existed = path.is_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(payload['content'])
                result = {'existed': existed}
            elif self.path == '/deploy':
                RUNTIME.mkdir(parents=True, exist_ok=True)
                archive = io.BytesIO(base64.b64decode(payload['archive'], validate=True))
                with tarfile.open(fileobj=archive, mode='r:gz') as package:
                    for item in package.getmembers():
                        target = (RUNTIME / 'runtime' / item.name).resolve()
                        if RUNTIME / 'runtime' not in target.parents or not item.isfile():
                            raise ValueError('Deployment contains non-regular or escaping member')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(package.extractfile(item).read())
                result = {'deployed': True}
            elif self.path == '/archive':
                with tempfile.TemporaryFile() as output:
                    with tarfile.open(fileobj=output, mode='w:gz') as archive:
                        for path in evidence_files(RUNTIME):
                            archive.add(path, arcname=str(path.relative_to(RUNTIME)), recursive=False)
                    size = output.tell()
                    output.seek(0)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/gzip')
                    self.send_header('Content-Length', str(size))
                    self.end_headers()
                    import shutil
                    shutil.copyfileobj(output, self.wfile)
                return
            else:
                self.send_json(404, {'error': 'Unknown bootstrap route'})
                return
            self.send_json(200, result)
        except Exception as error:
            self.send_json(400, {'error': f'{type(error).__name__}: {error}'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    ROOT = Path(args.root).resolve()
    if not ROOT.is_dir():
        raise SystemExit('Repository root is absent')
    ThreadingHTTPServer(('0.0.0.0', args.port), Handler).serve_forever()
