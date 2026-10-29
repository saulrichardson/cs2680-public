"""Regression checks for launcher termination, portable staging and task selection.

Real signals, staging, CLI, bundler and batch scripts; Docker/model/grader boundaries
are local fixtures. No network, provider request or official grading is performed.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from mads.runner import stage_source

SOURCE = Path(__file__).resolve().parents[1]


class AssignmentPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = dict(PATH=os.environ.get('PATH', os.defpath),
                        PYTHONDONTWRITEBYTECODE='1', CS2680_API_KEY='offline-fixture-only')

    def run_command(self, argv, env=None):
        return subprocess.run(argv, cwd=self.root, env=env or self.env,
                              capture_output=True, text=True, timeout=30)

    def test_host_sigterm_cleans_container_and_finalizes_receipt(self):
        checkout = self.root/'checkout'
        stage_source(SOURCE, checkout)
        tasks = self.root/'tasks.json'
        tasks.write_text(json.dumps({'fixture':dict(instance_id='fixture', base_commit='abc', docker_image='unused')}))
        binary = self.root/'bin'; binary.mkdir()
        docker = binary/'docker'
        docker.write_text('#!'+sys.executable+'\n'+'''
import os, pathlib, sys
root=pathlib.Path(os.environ['FIXTURE_ROOT'])
if sys.argv[1]=='run':
    (root/'started').touch()
    os.execv('/bin/sleep', ['sleep','60'])
elif sys.argv[1:3]==['rm','-f']:
    (root/'removed').write_text(sys.argv[3])
else:
    raise SystemExit(2)
''')
        docker.chmod(0o755)
        env = dict(self.env, PATH=str(binary)+os.pathsep+self.env['PATH'],
                   PYTHONPATH=str(SOURCE), MADSLOOP_REPO=str(checkout), TASKS_JSON=str(tasks),
                   FIXTURE_ROOT=str(self.root))
        script = """import pathlib, signal, sys
from mads.runner import run
before={s:signal.getsignal(s) for s in (signal.SIGTERM,signal.SIGINT)}
status=run(0)
assert all(signal.getsignal(s)==h for s,h in before.items())
pathlib.Path('restored').touch()
sys.exit(status)
"""
        process = subprocess.Popen([sys.executable, '-c', script], cwd=self.root,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic()+10
            while not (self.root/'started').exists() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue((self.root/'started').exists(), 'launcher did not reach Docker boundary')
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 128+signal.SIGTERM, stdout+stderr)
            self.assertTrue((self.root/'removed').read_text().startswith('mads-'))
            self.assertTrue((self.root/'restored').exists())
            receipt = json.loads((checkout/'.runs/latest-attempts/fixture.json').read_text())
            self.assertEqual(receipt['status'], 'interrupted')
            self.assertEqual(receipt['container_cleanup']['status'], 'completed')
            self.assertNotEqual(receipt['export_outcome']['status'], 'completed')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def test_staged_cli_checks_and_audit_work_outside_source_checkout(self):
        stage = self.root/'agent'
        stage_source(SOURCE, stage)
        env = dict(self.env, PYTHONPATH=str(stage))
        output = self.root/'checks'
        result = self.run_command([sys.executable, '-m', 'mads', 'check', 'responses_audit',
                                   'workspace_snapshot', '--output', str(output)], env)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        # Audit a real captured fixture journal through the staged CLI as well.
        summary = json.loads((output/'summary.json').read_text())
        artifacts = Path(next(row['artifact_dir'] for row in summary['checks']
                              if row['name'] == 'responses_audit'))
        result = self.run_command([sys.executable, '-m', 'mads', 'audit',
                                   str(artifacts/'golden'), '--strict'], env)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertTrue(json.loads(result.stdout)['passed'])
        # These inspect commands lazily import helpers that must also be staged.
        for action in ('activity', 'decision'):
            with self.subTest(action=action):
                result = self.run_command([sys.executable, '-m', 'mads', 'inspect',
                                           str(artifacts/'golden'), action], env)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                self.assertIsInstance(json.loads(result.stdout), dict)

    def test_custom_task_flows_through_real_batch_bundle_and_evaluation(self):
        scripts = self.root/'evaluation_scripts'; scripts.mkdir()
        for name in ('run_all.sh', 'make_patches.py', 'evaluate.sh'):
            shutil.copy2(SOURCE/'evaluation_scripts'/name, scripts/name)
        # The solver boundary writes a hash-attested patch for the selected task.
        (scripts/'run_task.sh').write_text('''python3 - "$1" <<'PY'
import hashlib,json,os,pathlib,sys
task=list(json.load(open(os.environ['TASKS_JSON'])).values())[int(sys.argv[1])]
iid=task['instance_id']; p=pathlib.Path(os.environ['MADS_BATCH_DIR'])/(iid+'.diff')
p.write_text('fixture patch')
(p.parent/(iid+'.json')).write_text(json.dumps(dict(instance_id=iid,patch=str(p),
    patch_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),export_outcome={'status':'completed'})))
PY
''')
        tasks = self.root/'selected tasks.json'
        tasks.write_text(json.dumps({'selected':dict(instance_id='selected',repo='fixture',base_commit='abc')}))
        metadata = self.root/'metadata.json'
        metadata.write_text(json.dumps({'selected':dict(before_repo_set_cmd='',selected_test_files_to_run='',fail_to_pass='',pass_to_pass='')}))
        (scripts/'agent_task_input.json').write_text(json.dumps({'wrong-default':{}}))
        (scripts/'task_test.json').write_text('{}')
        evaluator = self.root/'evaluator'; evaluator.mkdir()
        (evaluator/'swe_bench_pro_eval.py').write_text('''import csv,json,pathlib,sys
args=dict(arg[2:].split('=',1) for arg in sys.argv[1:] if '=' in arg)
ids=[r['instance_id'] for r in csv.DictReader(open(args['raw_sample_path']))]
assert ids==['selected']
assert [r['instance_id'] for r in json.load(open(args['patch_path']))]==ids
(pathlib.Path(args['output_dir'])/'eval_results.json').write_text(json.dumps({i:True for i in ids}))
''')
        binary = self.root/'bin'; binary.mkdir()
        python = binary/'python3'
        python.write_text('#!'+sys.executable+'\nimport os,sys\n'
                          "if sys.argv[1:3]==['-m','pip']: sys.exit(0)\n"
                          'os.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n')
        python.chmod(0o755)
        env = dict(self.env, PATH=str(binary)+os.pathsep+self.env['PATH'],
                   MADSLOOP_REPO=str(self.root), TASKS_JSON=tasks.name, TESTS_JSON=metadata.name,
                   SWEBENCH_PRO_OS=str(evaluator))
        result = self.run_command(['bash', str(scripts/'run_all.sh'), '--workers', '1'], env)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        batch = next(self.root.glob('batch-*'))
        manifest = json.loads((batch/'batch-manifest.json').read_text())
        self.assertEqual(manifest['tasks'], ['selected'])
        self.assertEqual(manifest['task_input_sha256'], hashlib.sha256(tasks.read_bytes()).hexdigest())
        self.assertEqual([p['instance_id'] for p in json.loads((batch/'patches.json').read_text())], ['selected'])
        self.assertIn('1/1 passed', (batch/'run_all_results.md').read_text())
        env.update(TESTS_JSON=str(scripts/'task_test.json'), MADS_OUTPUT_ROOT=str(batch))
        result = self.run_command(['bash', str(scripts/'evaluate.sh')], env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing evaluation metadata for selected tasks: selected', result.stderr)
