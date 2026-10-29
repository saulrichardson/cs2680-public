"""Real tool/process tests for explicit, persistent parallel groups."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from src.teamharness.parallel import ParallelGroups
from src.teamharness.tools import ToolRuntime, TOOLS
from src import workspace as effects


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.agent = self.root / 'agent'
        self.agent.mkdir()
        self.descriptor = {'root': str(self.agent), 'workspace': str(self.workspace), 'base': None, 'tools': TOOLS}
        self.groups = ParallelGroups(self.agent / 'effects/parallel-groups', self.runtime)
        self.settled_jobs = set()
        self.addCleanup(self.cleanup)

    def runtime(self):
        return ToolRuntime(None, 'a' * 32, self.descriptor)

    def cleanup(self):
        self.until(lambda: not self.groups.callbacks_active())
        self.stop_fixture_jobs(self.root)
        self.groups.shutdown(wait=True)
        self.stop_fixture_jobs(self.root)  # Include any member dispatched during the first pass.

    @staticmethod
    def runner_active(pid):
        """Reap owned children; an orphan zombie cannot write more evidence."""
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            return waited == 0
        except ChildProcessError:
            # The manager-subprocess fixture deliberately leaves its job
            # runner to the OS parent. Observe it; do not signal that recorder.
            status = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                    capture_output=True, text=True, check=False)
            if status.returncode not in (0, 1):
                raise AssertionError('Could not observe fixture job recorder: ' + status.stderr)
            state = status.stdout.strip()
            return bool(state) and not state.startswith('Z')

    def stop_fixture_jobs(self, root):
        # The durable initial callback may precede process.json. Discover the
        # recorder launch too, so cleanup cannot miss a command about to start.
        jobs = {path.parent for pattern in ('launcher.json', 'process.json')
                for path in Path(root).rglob(pattern)} - self.settled_jobs
        for job in jobs:
            deadline = time.monotonic() + 5
            signalled = False
            while True:
                receipt = json.loads((job / 'result.json').read_text()) if (job / 'result.json').exists() else None
                process = job / 'process.json'
                if process.exists() and not signalled and not (receipt and receipt.get('state') == 'exited'):
                    try:
                        os.killpg(json.loads(process.read_text())['process_group'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    signalled = True
                runner_path = job / ('started.json' if (job / 'started.json').exists() else 'launcher.json')
                runner = json.loads(runner_path.read_text())['pid']
                active = self.runner_active(runner)
                if receipt is not None and receipt.get('state') == 'exited' and not active:
                    self.settled_jobs.add(job)
                    break
                self.assertLess(time.monotonic(), deadline,
                    f'Fixture job recorder did not finish: {job}; receipt={receipt!r}, runner_active={active}')
                time.sleep(.01)

    def python(self, script):
        return 'exec ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(script)

    def until(self, condition, seconds=5):
        end = time.monotonic() + seconds
        while not condition():
            if time.monotonic() >= end:
                self.fail('Timed out waiting for actual fixture effect')
            time.sleep(.01)

    def test_real_commands_overlap_and_keep_separate_original_receipts(self):
        calls = []
        for name in ('a', 'b'):
            command = self.python(f'import pathlib,time,json; p=pathlib.Path({name!r}); start=time.time(); time.sleep(.35); p.write_text(json.dumps([start,time.time()])); print({name!r})')
            calls.append({'name': 'exec_command', 'arguments': {'command': command, 'yield_time_ms': 0}})
        self.groups.start(calls, 'overlap')
        result = self.groups.wait('overlap', timeout=5)
        self.assertEqual(result['state'], 'completed')
        spans = [json.loads((self.workspace / name).read_text()) for name in ('a', 'b')]
        self.assertLess(max(s[0] for s in spans), min(s[1] for s in spans))
        self.assertEqual(len({m['job_id'] for m in result['members']}), 2)
        for member in result['members']:
            self.assertEqual(member['job_id'], hashlib.sha256(member['effect_id'].encode()).hexdigest()[:32])
            self.assertEqual(member['job_observation']['exit_code'], 0)
            self.assertEqual(json.loads(Path(member['result_path']).read_text()), member['result'])
        other = ParallelGroups(self.groups.root, self.runtime)
        self.assertEqual(other.inspect('overlap')['state'], 'completed')
        self.assertEqual(other.list()[0]['group_id'], 'overlap')

    def test_malformed_parallel_is_effect_free_then_explicit_example_runs(self):
        runtime = ToolRuntime(SimpleNamespace(parallel_groups=lambda _: self.groups),
                              'a' * 32, self.descriptor)
        malformed = [
            ({'calls': '[{"arguments":{"command":"touch must-not-run"}, {"arguments":{}}]'}, 'string'),
            ({'calls': json.dumps([{'name': 'exec_command', 'arguments': {'command': 'touch must-not-run'}}])}, 'string'),
            ({'calls': [{'arguments': {'name': 'test1', 'command': 'touch must-not-run'}}]}, 'array'),
            ({'calls': [{'name': 'exec_command', 'arguments': {'command': 'touch must-not-run'}},
                        {'arguments': {'command': 'touch must-not-run-either'}}]}, 'array'),
        ]
        for index, (arguments, kind) in enumerate(malformed):
            with self.subTest(arguments=arguments):
                rejected = runtime('parallel', arguments, f'malformed-{index}')
                self.assertIs(rejected['executed'], False)
                self.assertIs(rejected['started'], False)
                self.assertEqual(rejected['received_field'], 'calls')
                self.assertEqual(rejected['received_type'], kind)
                self.assertEqual(self.groups.list(), [])
                self.assertEqual(list((self.agent / 'effects').glob('*.intent.json')), [])
                self.assertEqual(list(self.agent.rglob('request.json')), [])
                self.assertEqual(list(self.workspace.iterdir()), [])
        (self.workspace / 'original.txt').write_text('read-only fixture\n')
        # Use the actual returned example, not a repaired interpretation of any
        # rejected input. The callback now goes through real tools and jobs.
        recovered = runtime('parallel', rejected['example'], 'explicit-corrected-call')
        observed = self.groups.wait(recovered['group_id'], timeout=5)
        self.assertEqual(observed['state'], 'completed')
        self.assertEqual(len(observed['members']), 2)
        for member in observed['members']:
            self.assertEqual(member['name'], 'exec_command')
            self.assertEqual(member['job_observation']['state'], 'exited')
            self.assertEqual(member['job_observation']['exit_code'], 0)
            self.assertTrue(Path(member['result_path']).is_file())
        self.assertEqual(len(list(self.agent.rglob('request.json'))), 2)
        self.assertEqual([p.name for p in self.workspace.iterdir()], ['original.txt'])
        self.assertEqual((self.workspace / 'original.txt').read_text(), 'read-only fixture\n')

    def test_one_rejected_tool_does_not_cancel_successful_sibling(self):
        calls = [{'name': 'apply_patch', 'arguments': {'patch': 'invalid patch text'}},
                 {'name': 'exec_command', 'arguments': {'command': 'sleep .1; printf success > sibling'}}]
        result = self.groups.start(calls, 'independent', yield_ms=2000)
        self.assertEqual(result['state'], 'completed_with_failures')
        self.assertEqual(result['members'][0]['state'], 'failed')
        self.assertEqual(result['members'][1]['state'], 'completed')
        self.assertEqual((self.workspace / 'sibling').read_text(), 'success')
        self.assertFalse((self.workspace / 'never').exists())

    def test_cancel_from_second_manager_stops_running_and_skips_pending(self):
        self.groups = ParallelGroups(self.groups.root, self.runtime, max_workers=1)
        calls = [{'name': 'exec_command', 'arguments': {'command': 'printf ready > ready; exec sleep 60', 'yield_time_ms': 0}},
                 {'name': 'exec_command', 'arguments': {'command': 'touch must-not-run'}},
                 {'name': 'read_file', 'arguments': {'path': 'missing'}}]
        self.groups.start(calls, 'cancel')
        self.until(lambda: (self.workspace / 'ready').exists())
        second = ParallelGroups(self.groups.root, self.runtime)
        result = second.cancel('cancel', yield_ms=2000)
        self.assertEqual(result['state'], 'cancelled')
        first = result['members'][0]
        self.assertEqual(first['job_observation']['state'], 'exited')
        self.assertEqual(first['job_observation']['exit_code'], -signal.SIGTERM)
        self.assertTrue(first['cancellation']['receipt']['control']['executed'])
        self.assertEqual([m['state'] for m in result['members'][1:]], ['cancelled_before_start'] * 2)
        self.assertFalse((self.workspace / 'must-not-run').exists())
        self.assertFalse(Path(result['members'][1]['result_path'] or '/nonexistent').exists())

    def test_cancel_keeps_completed_result_and_does_not_force_kill(self):
        command = self.python('import signal,pathlib,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path("ignoring").touch(); time.sleep(60)')
        calls = [{'name': 'exec_command', 'arguments': {'command': 'printf complete', 'yield_time_ms': 1000}},
                 {'name': 'exec_command', 'arguments': {'command': command, 'yield_time_ms': 0}}]
        self.groups.start(calls, 'truthful')
        self.until(lambda: (self.workspace / 'ignoring').exists())
        self.until(lambda: self.groups.inspect('truthful')['members'][0]['state'] == 'completed')
        before = self.groups.inspect('truthful')['members'][0]['result']
        result = self.groups.cancel('truthful', yield_ms=100)
        self.assertEqual(result['state'], 'cancelling')
        self.assertEqual(result['members'][0]['result'], before)
        self.assertEqual(result['members'][1]['job_observation']['state'], 'running')
        self.assertTrue(result['members'][1]['cancellation']['receipt']['control']['executed'])
        # Only this explicit cleanup action escalates the signal.
        self.runtime()('job', {'id': result['members'][1]['job_id'], 'action': 'kill', 'yield_time_ms': 1000}, 'explicit-test-cleanup')
        settled = self.groups.wait('truthful', timeout=2)
        self.assertEqual(settled['members'][1]['job_observation']['exit_code'], -signal.SIGKILL)

    def test_cancelled_queue_does_not_keep_callbacks_active_behind_external_job(self):
        self.groups = ParallelGroups(self.groups.root, self.runtime, max_workers=1)
        command = self.python('import signal,pathlib,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path("ignoring").touch(); time.sleep(60)')
        calls = [{'name': 'exec_command', 'arguments': {'command': command, 'yield_time_ms': 0}},
                 {'name': 'exec_command', 'arguments': {'command': 'touch never-start'}}]
        self.groups.start(calls, 'cancel-queued')
        self.until(lambda: (self.workspace / 'ignoring').exists())
        self.until(lambda: self.groups.inspect('cancel-queued')['members'][0]['result_path'] is not None)
        self.assertTrue(self.groups.callbacks_active())  # The second member is queued.
        other = ParallelGroups(self.groups.root, self.runtime)
        result = other.cancel('cancel-queued', yield_ms=100)
        self.assertEqual(result['members'][0]['job_observation']['state'], 'running')
        self.assertEqual(result['members'][1]['state'], 'cancelled_before_start')
        self.assertFalse(self.groups.callbacks_active())
        self.assertFalse((self.workspace / 'never-start').exists())

    def test_non_cancelable_effect_waits_for_original_receipt(self):
        entered, release = threading.Event(), threading.Event()
        class Callback:
            workspace = None
            def __call__(self, name, arguments, effect_id):
                entered.set()
                release.wait(5)
                return {'executed': True, 'value': 'actual completion'}
        self.groups = ParallelGroups(self.groups.root, Callback)
        self.groups.start([{'name': 'independent_effect', 'arguments': {}}], 'noncancelable')
        self.assertTrue(entered.wait(2))
        self.assertTrue(self.groups.callbacks_active())
        result = self.groups.cancel('noncancelable', yield_ms=0)
        self.assertEqual(result['members'][0]['state'], 'executing')
        self.assertEqual(result['members'][0]['cancellation']['state'], 'not_cancelable')
        release.set()
        self.assertEqual(self.groups.wait('noncancelable', timeout=2)['members'][0]['result']['value'], 'actual completion')
        self.assertFalse(self.groups.callbacks_active())

    def manager_process(self, mode):
        """Exercise real role completion and process exit, not just a mock owner."""
        script = r'''
import json,pathlib,subprocess,sys,time
from src.teamharness.agents import AgentManager
from src.teamharness import patching
root=pathlib.Path(sys.argv[1]);mode=sys.argv[2];workspace=root/'workspace'
workspace.mkdir(exist_ok=True)
if mode=='write':
 (workspace/'baseline').write_text('unchanged\n')
 subprocess.run(['git','init','-q',str(workspace)],check=True)
 subprocess.run(['git','-C',str(workspace),'add','.'],check=True)
 subprocess.run(['git','-C',str(workspace),'-c','user.name=Fixture','-c','user.email=fixture@local','commit','-qm','base'],check=True)
def tool(name,args):
 return {'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
  'tool_calls':[{'id':name,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}]}
real_patch=patching.apply_patch
def delayed(*args,**kwargs):
 (root/'callback-entered').touch()
 time.sleep(.3)
 return real_patch(*args,**kwargs)
if mode=='write':patching.apply_patch=delayed
class Provider:
 model='fixture'
 def __init__(self):self.calls=0
 def complete(self,request):
  self.calls+=1
  if self.calls==1:
   calls=([{'name':'apply_patch','arguments':{'patch':'*** Begin Patch\n*** Add File: '+name+'\n+saved\n*** End Patch\n'}} for name in ('first','queued')]
      if mode=='write' else [{'name':'exec_command','arguments':{'command':'exec sleep 60','yield_time_ms':0}}])
   return tool('parallel',{'calls':calls,'yield_time_ms':0})
  return tool('finish_role',{'outcome':'complete','report':'Role finished; group effects remain separately observable.'})
manager=AgentManager(root/'run',lambda _:Provider())
ident=manager.create('investigator','Fixture role',workspace)
groups=manager.parallel_groups(ident)
if mode=='write':groups.max_workers=1
manager.start(ident)
role=manager.wait(ident,5)
active_before_shutdown=groups.callbacks_active()
manager.shutdown()
(root/'process-result.json').write_text(json.dumps({'role':role['status'],'groups':groups.list(),
 'role_patch':role.get('patch'),'source_capture':role.get('source_capture'),
 'callbacks_active_at_role_completion':active_before_shutdown,'callbacks_active_at_exit':groups.callbacks_active()}))
'''
        process = subprocess.run([sys.executable, '-c', script, str(self.root), mode],
                                 cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=8)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads((self.root / 'process-result.json').read_text())

    def test_manager_process_shutdown_preserves_delayed_and_queued_patch_receipts(self):
        result = self.manager_process('write')
        self.assertEqual(result['role'], 'completed')
        self.assertFalse(result['callbacks_active_at_role_completion'])
        self.assertFalse(result['callbacks_active_at_exit'])
        for name in ('first', 'queued'):
            self.assertEqual((self.workspace / name).read_text(), 'saved\n')
        members = result['groups'][0]['members']
        self.assertEqual([member['state'] for member in members], ['completed', 'completed'])
        for member in members:
            self.assertTrue(json.loads(Path(member['result_path']).read_text())['applied'])
        patch = Path(result['role_patch']['patch_path']).read_bytes()
        self.assertEqual(hashlib.sha256(patch).hexdigest(), result['role_patch']['sha256'])
        self.assertIn(b'+++ b/first', patch)
        self.assertIn(b'+++ b/queued', patch)
        self.assertEqual(patch.count(b'+saved'), 2)
        self.assertTrue(result['source_capture']['in_process_callbacks_drained'])

    def test_manager_process_shutdown_does_not_wait_for_external_shell_lifetime(self):
        try:
            started = time.monotonic()
            result = self.manager_process('external')
            self.assertLess(time.monotonic() - started, 8)
            self.assertEqual(result['role'], 'completed')
            self.assertFalse(result['callbacks_active_at_exit'])
            group = result['groups'][0]
            def never():
                raise AssertionError('Observation must not dispatch a callback')
            restored = ParallelGroups(Path(group['archive']).parent, never)
            observed = restored.inspect(group['group_id'])
            self.assertFalse(observed['owner_active'])
            self.assertFalse(restored.callbacks_active())
            self.assertEqual(observed['members'][0]['result']['state'], 'running')
            self.assertEqual(observed['members'][0]['job_observation']['state'], 'running')
        finally:
            self.stop_fixture_jobs(self.root / 'run')

    def test_cleanup_waits_for_delayed_exit_receipt_and_recorder_exit(self):
        job = self.root / 'delayed-recorder'
        job.mkdir()
        script = r'''
import json,os,pathlib,subprocess,sys,time
job=pathlib.Path(sys.argv[1])
def save(name,value):
 temporary=job/(name+'.pending');temporary.write_text(json.dumps(value));temporary.replace(job/name)
save('started.json',{'pid':os.getpid()})
while not (job/'release-launch').exists():time.sleep(.01)
process=subprocess.Popen(['sleep','60'],start_new_session=True)
save('process.json',{'pid':process.pid,'process_group':process.pid})
code=process.wait()
time.sleep(.15)
save('result.json',{'state':'exited','exit_code':code})
time.sleep(.15)
(job/'last-write').write_text('recorder finished its writes')
'''
        recorder = subprocess.Popen([sys.executable, '-c', script, str(job)])
        (job / 'launcher.json').write_text(json.dumps({'pid': recorder.pid}))
        try:
            self.until(lambda: (job / 'started.json').exists())
            self.assertFalse((job / 'process.json').exists())
            release = threading.Timer(.15, lambda: (job / 'release-launch').touch())
            release.start()
            started = time.monotonic()
            self.stop_fixture_jobs(job)
            release.join()
            self.assertGreaterEqual(time.monotonic() - started, .4)
            self.assertIsNotNone(recorder.poll())
            self.assertEqual(json.loads((job / 'result.json').read_text())['exit_code'], -signal.SIGKILL)
            self.assertEqual((job / 'last-write').read_text(), 'recorder finished its writes')
        finally:
            if recorder.poll() is None:
                recorder.kill()
                recorder.wait(timeout=5)

    def test_process_death_is_unknown_and_start_never_replays(self):
        group_root = self.root / 'crash-groups'
        marker = self.root / 'executed'
        script = '''
import pathlib,sys,time
from src.teamharness.parallel import ParallelGroups
root,marker=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2])
class Callback:
 workspace=None
 def __call__(self,name,args,effect_id):
  with marker.open('a') as out: out.write('effect\\n')
  time.sleep(60)
  return {'executed':True}
p=ParallelGroups(root,Callback)
p.start([{'name':'independent_effect','arguments':{}}],'crash')
while True: time.sleep(1)
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(group_root), str(marker)],
                                   cwd=Path(__file__).resolve().parents[2])
        def stop_fixture_process():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        self.addCleanup(stop_fixture_process)
        self.until(marker.exists)
        process.terminate()
        process.wait(timeout=5)
        def never():
            raise AssertionError('A persisted group must never redispatch')
        restored = ParallelGroups(group_root, never)
        result = restored.start([{'name': 'independent_effect', 'arguments': {}}], 'crash')
        self.assertEqual(result['state'], 'unknown')
        self.assertFalse(result['owner_active'])
        self.assertEqual(result['members'][0]['state'], 'unknown')
        self.assertFalse(result['members'][0]['replay_safe'])
        self.assertEqual(marker.read_text(), 'effect\n')

    def test_explicit_identity_and_session_boundaries(self):
        for name in ('parallel', 'inspect_parallel', 'cancel_parallel', 'compact', 'finish_role'):
            with self.assertRaises(ValueError):
                self.groups.start([{'name': name, 'arguments': {}}], 'boundary')
        original = [{'name': 'exec_command', 'arguments': {'command': 'printf original'}}]
        self.groups.start(original, 'identity', yield_ms=2000)
        with self.assertRaises(ValueError):
            self.groups.start([{'name': 'exec_command', 'arguments': {'command': 'printf changed'}}], 'identity')


if __name__ == '__main__':
    unittest.main()
