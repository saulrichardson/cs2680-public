"""Exact archive transport through the actual bounded course HTTP interface."""
import hashlib
import importlib
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from src import bridge, sandbox_server


class EvidenceTransfer(unittest.TestCase):
    def test_parallel_ranges_preserve_bytes_through_course_output_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = os.urandom(3 * 1024 * 1024 + 137)
            source = root / 'original.tar.gz'; source.write_bytes(data)
            barrier = threading.Barrier(4)
            lock = threading.Lock()
            observed = {'enabled': False, 'calls': 0, 'active': 0, 'peak': 0}

            class Handler(sandbox_server.Handler):
                def do_POST(self):
                    tracked = observed['enabled'] and self.path == '/exec'
                    if tracked:
                        with lock:
                            number = observed['calls']; observed['calls'] += 1
                            observed['active'] += 1
                            observed['peak'] = max(observed['peak'], observed['active'])
                        if number < 4:
                            barrier.wait(timeout=5)
                    try:
                        super().do_POST()
                    finally:
                        if tracked:
                            with lock: observed['active'] -= 1

            with patch.object(sandbox_server, 'ROOT', str(root)), patch.object(sandbox_server, 'WORK_ROOT', str(root)):
                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                try:
                    workspace = bridge.Workspace(str(root), str(root / 'state'),
                        f'http://127.0.0.1:{server.server_port}', capture_patch=False)
                    with patch.dict('sys.modules', {'lab.adapters.teamharness.bridge': bridge}):
                        remote_type = importlib.import_module('lab.adapters.teamharness.backend').Remote
                    remote = remote_type(workspace, root / 'receipts')
                    observed['enabled'] = True
                    copied = root / 'copied.tar.gz'
                    receipt = remote.download(source, copied, len(data), hashlib.sha256(data).hexdigest())
                    self.assertEqual(copied.read_bytes(), data)
                    self.assertEqual(receipt['sha256'], hashlib.sha256(data).hexdigest())
                    self.assertGreater(observed['peak'], 1)
                    self.assertLessEqual(observed['peak'], 4)
                    self.assertEqual(observed['calls'], 7)
                    # Actual changed bytes are rejected even when all range
                    # metadata remains valid; no successful receipt is returned.
                    source.write_bytes(b'x' * len(data))
                    with self.assertRaisesRegex(RuntimeError, 'transfer identity'):
                        remote.download(source, root / 'corrupt.tar.gz', len(data), receipt['sha256'])
                    source.write_bytes(b'truncated')
                    with self.assertRaisesRegex(RuntimeError, 'range identity'):
                        remote.download(source, root / 'short.tar.gz', len(data), receipt['sha256'])
                finally:
                    server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
