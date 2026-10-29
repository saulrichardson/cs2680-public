"""Public task command: preparation precedes dispatch, preflight follows it once."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import iteration
import readiness


class Engine(BaseHTTPRequestHandler):
    flushes = 0

    def log_message(self, *_):
        pass

    def do_GET(self):
        assert self.path == '/health'
        self.send_response(200); self.end_headers()

    def do_POST(self):
        assert self.path == '/flush_cache'
        Engine.flushes += 1
        self.send_response(200); self.end_headers()


def await_file(path, child):
    deadline = time.monotonic() + 10
    while not path.exists():
        if child.poll() is not None or time.monotonic() >= deadline:
            raise AssertionError('Actual preparation did not finish')
        time.sleep(.02)


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Engine)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / '000'; root = job / 'iteration'
            scripts = root / 'course/evaluation_scripts'; scripts.mkdir(parents=True)
            (root / 'course/dispatcher').mkdir()
            (scripts / 'prepare_images.sh').write_text('python3 -c "from pathlib import Path; Path(\'prepared\').touch()"\n')
            (scripts / 'run_all.py').write_text('''import json,sys,time
from pathlib import Path
assert Path('prepared').exists()
with Path('invocations.jsonl').open('a') as out:
 out.write(json.dumps({'argv':sys.argv,'at':time.time()})+'\\n')
''')
            iteration.write(root / 'experiment.json', {'files': iteration.hashes(root),
                'input_roots': ['course'], 'environment': {'A3_RUN_LIMIT_S': '14400', 'A4_WORKERS': '1'},
                'runner': ['python3', 'evaluation_scripts/run_all.py', '--limit', '1']})
            key = job / 'key'; key.write_text('fixture-only')
            argv = [sys.executable, str(Path(iteration.__file__)), 'task', '--experiment', str(root),
                    '--base-url', 'http://127.0.0.1:' + str(server.server_port), '--key-file', str(key),
                    '--cache', str(job / 'cache'), '--first-name', 'Fixture', '--last-name', 'Only']
            with (job / 'execution.log').open('wb') as log:
                child = subprocess.Popen(argv, env=dict(os.environ, A4_WAIT_FOR_MODEL_LAUNCH='1'), stdout=log, stderr=subprocess.STDOUT)
                try:
                    work = root / 'execution/course'
                    await_file(work / 'prepared', child)
                    time.sleep(.1)
                    assert not (work / 'invocations.jsonl').exists(), 'Healthy endpoint bypassed accepted launch'
                    receipt = readiness.record_launch(job)
                    original = (job / 'model-launch.json').read_bytes()
                    try:
                        readiness.record_launch(job)
                    except FileExistsError:
                        pass
                    else:
                        raise AssertionError('Duplicate dispatch receipt reset startup clock')
                    assert (job / 'model-launch.json').read_bytes() == original
                    assert child.wait(timeout=10) == 0, (job / 'execution.log').read_text()
                finally:
                    if child.poll() is None: child.terminate(); child.wait(timeout=5)
            stages = json.loads((root / 'task-stages.json').read_text())
            calls = [json.loads(line) for line in (work / 'invocations.jsonl').read_text().splitlines()]
            assert [row['stage'] for row in stages] == ['prepare', 'preflight', 'task']
            assert stages[0]['finished_at_unix'] < receipt['launched_at_unix'] <= calls[0]['at']
            assert [row['argv'][-1] for row in calls] == ['0', '1']
            assert Engine.flushes == 1
            assert subprocess.run(argv, capture_output=True).returncode != 0
            assert len((work / 'invocations.jsonl').read_text().splitlines()) == 2
            iteration.verify(root)
            print(json.dumps({'state': 'passed', 'claim': 'Preparation before accepted launch; preflight after launch; unchanged startup clock and inputs; duplicate task effects refused', 'stages': stages}))
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
