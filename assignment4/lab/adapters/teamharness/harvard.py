"""Frozen Python experiments on the course Slurm/Nix environment.

The portal/SSH transport is external. Slurm owns allocation lifetime; this adapter
runs original course setup/launch checks and the existing team runtime. Native
screens have declared external checks, never implicit original benchmark grades.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def save(path, value):
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def verify(bundle):
    value = json.loads((bundle / 'inputs.json').read_text())
    for name, expected in value['files'].items():
        if sha(bundle / name) != expected:
            raise ValueError('Changed frozen input: ' + name)
    for name in ('experiment', 'tools', 'screens'):
        if {name + '/' + n: h for n, h in inventory(bundle / name).items()} != {
                n: h for n, h in value['files'].items() if n.startswith(name + '/')}:
            raise ValueError('Changed frozen inventory: ' + name)
    return value


def prepare(args):
    experiment = args.experiment.resolve()
    sys.path.insert(0, str(experiment / 'tools'))
    from iteration import verify as verify_experiment
    frozen = verify_experiment(experiment)
    # Initial native path is deliberately calibrated only for the stock engine.
    engine = json.loads((experiment / 'course/engine/engine.json').read_text())
    if engine['engine'] != 'sglang' or (experiment / 'course/engine/sglang.patch').exists():
        raise ValueError('Native screening currently supports unpatched SGLang; original course launch checks remain authoritative')
    screen_spec = json.loads((args.screens / 'screens.json').read_text())
    if not screen_spec or not isinstance(screen_spec, list):
        raise ValueError('screens.json must be a nonempty list')
    seen = set()
    for row in screen_spec:
        name = row['name']
        if not isinstance(name, str) or not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name) or name in seen:
            raise ValueError('Unique simple screen names required')
        seen.add(name)
        if type(row['seconds']) is not int or row['seconds'] < 1:
            raise ValueError('Each native screen declares its actual allowance')
        for path in ('source', 'task.txt', 'check.py'):
            if not (args.screens / row['fixture'] / path).exists():
                raise ValueError('Missing screen source/task/check: ' + path)
        if Path(row['fixture']).is_absolute() or '..' in Path(row['fixture']).parts:
            raise ValueError('Fixture must be inside screens')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(experiment, output / 'experiment', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(args.screens, output / 'screens', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git'))
    tools = output / 'tools'; tools.mkdir()
    shutil.copy2(__file__, tools / 'harvard.py')
    shutil.copy2(Path(__file__).resolve().parents[2] / 'replica/processes.py', tools / 'processes.py')
    value = {'format': 1, 'experiment_manifest_sha256': sha(experiment / 'experiment.json'),
             'source_commit': frozen['source_commit'], 'submission_sha256': frozen['submission_sha256'],
             'cohort_sha256': sha(experiment / 'cohort/agent_task_input.json'),
             'screens': screen_spec, 'files': inventory(output),
             'scope': 'Original course launch qualification and native screens; original benchmark grading is unavailable here'}
    save(output / 'inputs.json', value)
    archive = output.with_suffix('.tar.gz')
    with tarfile.open(archive, 'x:gz') as tar:
        tar.add(output, arcname=output.name)
    return {'bundle': str(output), 'archive': str(archive), 'archive_sha256': sha(archive),
            'inputs_sha256': sha(output / 'inputs.json'), 'resources_allocated': False}


def submit(args):
    bundle = args.bundle.resolve(); verify(bundle)
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    name = 'a4-python-' + sha(bundle / 'inputs.json')[:10]
    script = '#!/bin/bash\nset -euo pipefail\numask 077\nexec ' + shlex.join([
        'python3', str(bundle / 'tools/harvard.py'), 'run', '--bundle', str(bundle),
        '--course-root', str(args.course_root.resolve()), '--output', str(output)]) + '\n'
    (output / 'job.sh').write_text(script)
    argv = ['sbatch', '--parsable', '--job-name', name, '--partition', args.partition,
            '--gres', 'gpu:1', '--cpus-per-task', '8', '--mem', '28G', '--time', args.time,
            '--output', str(output / 'slurm-%j.log')]
    if args.exclude:
        argv.extend(['--exclude', args.exclude])
    intent = {'state': 'dispatching', 'started_at_unix': time.time(), 'argv': argv,
              'inputs_sha256': sha(bundle / 'inputs.json'), 'job_script_sha256': sha(output / 'job.sh')}
    save(output / 'submission.json', intent)
    # No automatic retries: sbatch might have accepted the job before transport failed.
    try:
        result = subprocess.run(argv, input=script, text=True, capture_output=True, timeout=45)
        intent.update(stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)
        job = result.stdout.strip().split(';')[0]
        if result.returncode == 0 and job.isdecimal():
            intent.update(state='submitted', job_id=job)
        else:
            intent['state'] = 'dispatch_unknown'
    except (OSError, subprocess.TimeoutExpired) as error:
        intent.update(state='dispatch_unknown', error=str(error))
    save(output / 'submission.json', intent)
    return intent


def command(argv, output, name, env=None):
    start = time.time()
    with (output / (name + '.log')).open('wb') as stream:
        result = subprocess.run(argv, env=env, stdout=stream, stderr=subprocess.STDOUT)
    receipt = {'argv': argv, 'started_at_unix': start, 'finished_at_unix': time.time(),
               'exit_code': result.returncode}
    save(output / (name + '.json'), receipt)
    if result.returncode:
        raise RuntimeError(name + ' failed; inspect retained log')
    return receipt


def run(args):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('GPU work requires a Slurm allocation; do not run on a login node')
    bundle = args.bundle.resolve(); value = verify(bundle); output = args.output.resolve()
    with (output / 'execution-start.json').open('x') as out:
        json.dump({'job_id': os.environ['SLURM_JOB_ID'], 'started_at_unix': time.time(),
                   'inputs_sha256': sha(bundle / 'inputs.json')}, out)
    course = args.course_root.resolve()
    local = Path('/var/tmp') / (os.environ['USER'] + '-a4-research-' + os.environ['SLURM_JOB_ID'])
    if local.exists():
        raise ValueError('Allocation-local directory exists; observe the prior execution instead of repeating effects')
    env = dict(os.environ, A4=str(course), A4_LOCAL=str(local), PYTHONDONTWRITEBYTECODE='1')
    result = {'state': 'running', 'original_benchmark_grade': None,
              'inputs_sha256': sha(bundle / 'inputs.json'), 'stages': []}
    save(output / 'result.json', result)
    try:
        observed = {'scheduler_job': os.environ['SLURM_JOB_ID'], 'platform': sys.platform,
                    'tools': {n: shutil.which(n) for n in ('docker', 'podman', 'apptainer', 'singularity', 'sbatch')},
                    'course_scripts': {n: sha(course / 'bin' / n) for n in ('setup.sh', 'np.sh', 'check-submission.sh', 'a4-serve', 'cleanup.sh')}}
        for label, argv in [('gpu', ['nvidia-smi', '--query-gpu=name,uuid,driver_version,memory.total,power.limit', '--format=csv']),
                            ('allocation', ['scontrol', 'show', 'job', os.environ['SLURM_JOB_ID'], '-o']),
                            ('storage', ['df', '-h', '/var/tmp', str(output)])]:
            r = subprocess.run(argv, capture_output=True, text=True)
            observed[label] = {'exit_code': r.returncode, 'stdout': r.stdout, 'stderr': r.stderr}
        save(output / 'environment.json', observed)
        result['stages'].append(command(['bash', str(course / 'bin/setup.sh')], output, 'setup', env))
        submission = output / 'launch-submission'; submission.mkdir()
        for name in ('engine', 'src'):
            shutil.copytree(bundle / 'experiment/course' / name, submission / name)
        result['stages'].append(command(['bash', str(course / 'bin/check-submission.sh'), '--launch', str(submission)], output, 'course-launch-check', env))
        save(output / 'result.json', result)
        shell = 'source "$A4/bin/np.sh" && a4sh python "$@"'
        result['stages'].append(command(['bash', '-c', shell, 'harvard-screen', str(bundle / 'tools/harvard.py'),
            'screen', '--bundle', str(bundle), '--course-root', str(course), '--output', str(output)], output, 'native-screen', env))
        result['native_screens'] = json.loads((output / 'screens.json').read_text())
        result['state'] = 'completed'
    except Exception as error:
        result.update(state='infrastructure_failure', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        result['finished_at_unix'] = time.time()
        # Every child is within this Slurm job; screen closes its owned server
        # before returning. Shared weights and unrelated account files stay intact.
        save(output / 'result.json', result)
        if local.exists():
            try:
                result['cleanup'] = command(['bash', str(course / 'bin/cleanup.sh')], output, 'cleanup', env)
            except Exception as error:
                result['cleanup_error'] = type(error).__name__ + ': ' + str(error)
                raise
            finally:
                save(output / 'result.json', result)


def screen(args):
    from processes import start, stop_and_wait, wait
    bundle = args.bundle.resolve(); value = verify(bundle); output = args.output.resolve()
    course = args.course_root.resolve(); local = Path(os.environ['A4_LOCAL'])
    identity = json.loads((bundle / 'experiment/course/engine/engine.json').read_text())
    repo, revision = identity['model'].split('@')
    model_path = local / 'hf/hub' / ('models--' + repo.replace('/', '--')) / 'snapshots' / revision
    if not model_path.is_dir():
        raise ValueError('Exact model snapshot unavailable after original setup')
    runtime = bundle / 'experiment/course'
    key = secrets.token_urlsafe(32); key_file = local / 'screen.key'; key_file.write_text(key); key_file.chmod(0o600)
    native_home = local / 'screen-home'; native_home.mkdir()
    env = dict(os.environ, HOME=str(native_home), PYTHONPATH=str(local / 'sglang-v0.5.20/python'),
        PATH=str(course / 'bin') + ':' + os.environ['PATH'], A4_ENGINE='sglang',
        A4_ENGINE_DIR=str(runtime / 'engine'), A4_MODEL_PATH=str(model_path),
        A4_SERVED_MODEL_NAME='qwen3.6-35b-a3b-fp4', A4_HOST='127.0.0.1', A4_PORT='30000',
        A4_KEY_FILE=str(key_file), MAX_JOBS='1')
    command([sys.executable, '-c', 'import json,torch,sglang,flashinfer; print(json.dumps({"torch":torch.__version__,"sglang":sglang.__version__,"flashinfer":flashinfer.__version__}))'], output, 'software', env)
    server_log = (output / 'server.log').open('wb')
    server = start(['bash', str(runtime / 'engine/launch.sh')], env=env, stdout=server_log, stderr=subprocess.STDOUT)
    launched = time.time(); rows = []
    try:
        while True:
            if os.waitid(os.P_PID, server.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
                raise RuntimeError('Native serving process ended before readiness')
            try:
                with urllib.request.urlopen('http://127.0.0.1:30000/health', timeout=5) as response:
                    if response.status == 200: break
            except OSError:
                pass
            if time.time() - launched > 45 * 60:
                raise RuntimeError('Native serving exceeded the course startup allowance')
            time.sleep(1)
        save(output / 'serving.json', {'launched_at_unix': launched, 'ready_at_unix': time.time(),
            'pid': server.pid, 'model': identity, 'engine_files': inventory(runtime / 'engine')})
        provider_env = dict(os.environ, PYTHONPATH=str(runtime), A4_BASE_URL='http://127.0.0.1:30000/v1',
                            A4_API_KEY=key, A4_MODEL='qwen3.6-35b-a3b-fp4', A4_API_PROTOCOL='chat_completions')
        for row in value['screens']:
            target = output / 'screens' / row['name']; target.mkdir(parents=True)
            fixture = bundle / 'screens' / row['fixture']
            work = target / 'workspace'; shutil.copytree(fixture / 'source', work)
            subprocess.run(['git', 'init', '-q', str(work)], check=True)
            subprocess.run(['git', '-C', str(work), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(work), '-c', 'user.name=Qualification', '-c', 'user.email=qualification@localhost', 'commit', '-qm', 'Frozen native screen'], check=True)
            reset = urllib.request.Request('http://127.0.0.1:30000/flush_cache', method='POST', headers={'Authorization': 'Bearer ' + key})
            with urllib.request.urlopen(reset, timeout=30) as response:
                save(target / 'cache-reset.json', {'status': response.status, 'at_unix': time.time(), 'fresh_rng': False})
            began = time.time(); deadline = began + row['seconds']
            argv = [sys.executable, str(bundle / 'tools/harvard.py'), 'native-task',
                    '--bundle', str(bundle), '--fixture', str(fixture), '--workspace', str(work),
                    '--output', str(target / 'run'), '--deadline', str(deadline)]
            # A Slurm step owns the runtime and its detached shell children.
            # The explicit screen allowance also bounds an in-flight HTTP wait.
            # Slurm starts outside nix-portable's mount namespace. Re-enter the
            # original course environment there before invoking its Python.
            step = ['srun', '--exact', '--nodes=1', '--ntasks=1', '--cpus-per-task=2',
                    '--gres=none', '--quit-on-interrupt', '--kill-on-bad-exit=1',
                    '--time=' + str((row['seconds'] + 59) // 60), 'bash', '-c',
                    'source "$A4/bin/np.sh" && a4sh python "$@"', 'native-runtime', *argv[1:]]
            save(target / 'launch.json', {'argv':step, 'started_at_unix':began, 'deadline_unix':deadline})
            expired = False
            with (target / 'runtime.log').open('wb') as log:
                process = start(step, cwd=runtime, env=provider_env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                        if time.time() >= deadline:
                            expired = True
                            stop_and_wait(process)
                            break
                        time.sleep(.1)
                    if process.returncode is None:
                        code, abandoned = wait(process)
                    else:
                        code, abandoned = process.returncode, False
                finally:
                    stop_and_wait(process)
            workflow_path = target / 'run/workflow.json'
            workflow = json.loads(workflow_path.read_text()) if workflow_path.exists() else {}
            rows.append({'name': row['name'], 'started_at_unix': began, 'deadline_unix': deadline,
                         'finished_at_unix': time.time(), 'exit_code': code, 'deadline_reached':expired,
                         'abandoned_process_group':abandoned, 'original_benchmark_grade': None,
                         'workflow_state': workflow.get('state'),
                         'workflow_recorded': workflow_path.exists()})
            save(output / 'screens.json', rows)
            if not workflow_path.exists():
                raise RuntimeError('Native runtime did not create its workflow; inspect ' + str(target / 'runtime.log'))
    finally:
        stop_and_wait(server); server_log.close(); key_file.unlink(missing_ok=True)
    return rows


def native_task(args):
    """Apply the frozen campaign policy through the existing investigation owner."""
    bundle = args.bundle.resolve(); verify(bundle)
    # a4sh owns PYTHONPATH. Import this verified package explicitly after entering
    # its environment, regardless of the launcher script's directory or cwd.
    sys.path.insert(0, str(bundle / 'experiment/course'))
    from src.teamharness.agents import AgentManager
    from src.teamharness.candidates import Grader
    from src.teamharness.main import Investigation
    from src.teamharness.provider import HTTPProvider
    campaign = json.loads((bundle / 'experiment/course/src/campaign.json').read_text())
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    manager = AgentManager(output / 'agents', HTTPProvider,
        max_agents=campaign.get('max_agents'), deadline=args.deadline)
    try:
        workflow = Investigation(manager, output,
            completion_gate=campaign['completion_gate'], planning_boundary=campaign['planning_boundary'])
        grader = Grader((sys.executable, '-B', str(args.fixture / 'check.py'), '{workspace}'),
                        'native-screen-check', False)
        result = workflow.run((args.fixture / 'task.txt').read_text(), args.workspace, grader)
        if result['state'] == 'planning_complete':
            # These declared fixture screens all receive implementation. Preserve
            # the recorded recommendation and assessment, as course admission does.
            result = workflow.admit('Selected by the frozen native-screen list; no original benchmark grade is implied.')
    finally:
        manager.shutdown()
    result = workflow.settled_report()
    print(json.dumps(result, indent=2))
    if result['state'] != 'completed':
        raise SystemExit(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--experiment', type=Path, required=True); p.add_argument('--screens', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('native-task')
    for name in ('bundle', 'fixture', 'workspace', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--deadline', type=float, required=True)
    for action in ('submit', 'run', 'screen'):
        p = sub.add_parser(action)
        p.add_argument('--bundle', type=Path, required=True); p.add_argument('--course-root', type=Path, required=True)
        p.add_argument('--output', type=Path, required=True)
        if action == 'submit':
            p.add_argument('--partition', default='gpu-g7-reserved'); p.add_argument('--time', default='04:00:00')
            p.add_argument('--exclude', help='Observed unsuitable Slurm nodes; retained in the submission receipt')
    args = parser.parse_args()
    result = globals()[args.action.replace('-', '_')](args)
    if result is not None: print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
