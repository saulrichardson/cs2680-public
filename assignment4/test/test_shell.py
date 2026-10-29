"""Real processes and terminal I/O; no provider or command mocks."""
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.bridge import Workspace
from src.workspace import socket_path


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'files'
        self.repo.mkdir()
        self.ws = Workspace(str(self.repo), str(self.root/'state'), capture_patch=False)
        self.jobs = []
        self.addCleanup(self.stop_jobs)

    def call(self, op, **args):
        result = self.ws.effect(op, dict(workspace=self.ws.facts(), **args))
        if op == 'shell' and 'id' in result:
            self.jobs.append(result['id'])
        return result

    def stop_jobs(self):
        for ident in self.jobs:
            state = self.call('observe', id=ident)
            if state['state'] != 'exited':
                self.call('job', id=ident, action='kill', yield_time_ms=1000)

    def ready(self, result, marker):
        for _ in range(20):
            if marker in result['output']['content']:
                return result
            self.assertEqual(result['state'],'running',result)
            result=self.call('job',id=result['id'],offset=0,yield_time_ms=100)
        self.fail('Fixture process did not become ready')

    def python(self, code):
        return shlex.quote(sys.executable) + ' -u -c ' + shlex.quote(code)

    def test_short_command_returns_output_exit_and_full_paged_bytes_once(self):
        code = "import time; time.sleep(.12); print('A'*20000); print('err',file=__import__('sys').stderr)"
        result = self.call('shell', command=self.python(code), max_output_bytes=31)
        self.assertEqual(result['state'], 'exited', result)
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['output']['content'], 'A'*31)
        self.assertFalse(result['output']['complete'])
        rest = self.call('job', id=result['id'], max_output_bytes=65536)
        raw = (self.root/'state/jobs'/result['id']/'output').read_bytes()
        self.assertEqual(raw, b'A'*20000+b'\nerr\n')
        self.assertEqual((result['output']['content']+rest['output']['content']).encode(), raw)
        self.assertEqual(self.call('job', id=result['id'])['output']['content'], '')
        reread = self.call('job', id=result['id'], offset=0, max_output_bytes=5)
        self.assertEqual(reread['output']['content'], 'AAAAA')
        self.assertEqual(self.call('job', id=result['id'])['output']['content'], '')

    def test_pipe_input_eof_and_wait_do_not_kill_process(self):
        code = "import sys,time; print('ready'); text=sys.stdin.read(); time.sleep(.1); print('got:'+text)"
        first = self.call('shell', command=self.python(code), yield_time_ms=200)
        self.assertEqual(first['state'], 'running')
        first=self.ready(first,'ready')
        sent = self.call('job', id=first['id'], action='input', chars='héllo', yield_time_ms=0)
        self.assertEqual(sent['control']['bytes_written'], len('héllo'.encode()))
        self.assertEqual(sent['state'], 'running')
        done = self.call('job', id=first['id'], action='close_stdin')
        self.assertEqual(done['state'], 'exited', done)
        self.assertIn('got:héllo', done['output']['content'])
        ended = self.call('job', id=first['id'], action='input', chars='do not replay')
        self.assertFalse(ended['control']['executed'])

    def test_terminal_is_interactive_and_persistent(self):
        code = "import os; print('tty',os.isatty(0),os.isatty(1)); x=input('name? '); print('hello',x)"
        first = self.call('shell', command=self.python(code), tty=True, yield_time_ms=200)
        self.assertEqual(first['state'], 'running')
        first=self.ready(first,'tty True True')
        done = self.call('job', id=first['id'], action='input', chars='Ada\n')
        self.assertEqual(done['state'], 'exited', done)
        self.assertIn('hello Ada', done['output']['content'])
        self.assertTrue((self.root/'state/jobs'/first['id']/'output').read_bytes())

    def test_interrupt_and_kill_have_truthful_exit_and_isolated_scope(self):
        first = self.call('shell', command='sleep 30', yield_time_ms=100)
        other = self.call('shell', command='sleep 30', yield_time_ms=100)
        ended = self.call('job', id=first['id'], action='interrupt')
        self.assertTrue(ended['control']['executed'])
        self.assertEqual(ended['state'], 'exited', ended)
        self.assertNotEqual(ended['exit_code'], 0)
        self.assertEqual(self.call('observe', id=other['id'])['state'], 'running')
        killed = self.call('job', id=other['id'], action='kill')
        self.assertEqual(killed['state'], 'exited', killed)
        stranger = Workspace(str(self.repo), str(self.root/'other-state'), capture_patch=False)
        result = stranger.effect('job', dict(workspace=stranger.facts(), id=first['id'], action='kill'))
        self.assertIn('Unknown job', result['error'])

    def test_cwd_shell_failures_and_invalid_controls_have_no_side_effects(self):
        directory = self.repo / 'nested'
        directory.mkdir()
        first = self.call('shell', command='printf checked > result; pwd; exit 7', cwd='nested', shell='/bin/bash')
        self.assertEqual(first['exit_code'], 7)
        self.assertEqual(first['output']['content'].strip(), str(directory.resolve()))
        self.assertEqual((directory/'result').read_text(), 'checked')
        bad = self.call('shell', command='touch forbidden', yield_time_ms=-1)
        self.assertIn('error', bad)
        self.assertFalse((self.repo/'forbidden').exists())
        active = self.call('shell', command='sleep 30', yield_time_ms=100)
        bad = self.call('job', id=active['id'], action='kill', chars='invalid')
        self.assertIn('error', bad)
        self.assertEqual(self.call('observe', id=active['id'])['state'], 'running')

    def test_disconnected_control_client_does_not_destroy_job(self):
        first = self.call('shell', command=self.python("print('ready'); print(input())"), yield_time_ms=200)
        first=self.ready(first,'ready')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.connect(str(socket_path(first['id'])))
            conn.sendall(b'not valid json\n')
        time.sleep(.05)
        self.assertEqual(self.call('observe', id=first['id'])['state'], 'running')
        done = self.call('job', id=first['id'], action='input', chars='still alive\n')
        self.assertEqual(done['exit_code'], 0, done)
        self.assertIn('still alive', done['output']['content'])

    def test_project_environment_survives_without_harness_credentials(self):
        variables = {'PROJECT_TOKEN':'project-value', 'PASSWORD_STYLE':'literal',
                     'A4_API_KEY':'private-harness-key', 'CS2680_API_KEY':'private-course-key',
                     'OPENROUTER_API_KEY':'private-provider-key'}
        with patch.dict(os.environ, variables):
            result = self.call('shell', command=self.python("import os,json; print(json.dumps({k:os.environ.get(k) for k in " + repr(list(variables)) + "}))"))
        received = json.loads(result['output']['content'])
        self.assertEqual(received['PROJECT_TOKEN'], 'project-value')
        self.assertEqual(received['PASSWORD_STYLE'], 'literal')
        for name in ('A4_API_KEY', 'CS2680_API_KEY', 'OPENROUTER_API_KEY'):
            self.assertIsNone(received[name])

if __name__ == '__main__':
    unittest.main()
