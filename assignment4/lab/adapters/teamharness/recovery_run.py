"""One explicit auxiliary continuation; never starts or controls the original fleet."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import time


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, indent=2) + '\n')
    pending.replace(path)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def grade_seconds(deadline, now=None):
    remaining = int(deadline + 1800 - (time.time() if now is None else now) - 30)
    if remaining <= 0:
        raise TimeoutError('Original outer deadline leaves no grading/collection time')
    return min(3300, remaining)


def supervise(root, container, manifest_path):
    root = Path(root).resolve()
    state = root / 'state'
    manifest = read(state / 'operator-recovery/manifest.json')
    config = read(state / 'config.json')
    manager = read(state / 'run/agents/manager.json')
    key = str(manifest['key'])
    entry = read(manifest_path)[key]
    deadline = manifest['deadline']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]+', container) or manifest['container'] != container:
        raise ValueError('Auxiliary container does not match restored ownership manifest')
    if entry['instance_id'] != manifest['instance_id'] or entry['base_commit'] != manifest['base_commit']:
        raise ValueError('Original task identity/base mismatch')
    if config['deadline'] != deadline or manager['deadline'] != deadline:
        raise ValueError('Recovery cannot renew the original solving deadline')
    if time.time() >= deadline:
        raise TimeoutError('Original solving deadline already expired; no resume launched')
    if (root / 'resume.intent.json').exists():
        raise RuntimeError('Resume already has a durable intent; inspect it, never replay automatically')
    inside_state = config['state']
    inside_config = inside_state + '/config.json'
    python = subprocess.check_output(['docker', 'exec', container, '/bin/sh', '-c',
        'if [ -e /lib/ld-musl-x86_64.so.1 ]; then printf /opt/agentpy_musl/bin/python3; else printf /opt/agentpy/bin/python3; fi'], text=True).strip()
    pidpath = inside_state + '/operator-recovery/resume-worker.pid'
    command = 'echo $$ > ' + shlex.quote(pidpath) + '; exec ' + shlex.join([
        python, '-m', 'src.teamharness.course_worker', 'resume', '--config', inside_config])
    argv = ['docker', 'exec', '-w', inside_state + '/runtime', container, '/bin/sh', '-c', command]
    intent = {'operation': 'resume', 'instance_id': entry['instance_id'], 'key': key,
        'container': container, 'started_at': time.time(), 'original_deadline': deadline,
        'original_outer_deadline': deadline + 1800, 'argv': argv, 'supervisor_pid': os.getpid()}
    with (root / 'resume.intent.json').open('x') as stream:
        json.dump(intent, stream, indent=2)
    status = dict(intent, state='running')
    save(root / 'recovery-status.json', status)
    with (root / 'resume.stdout').open('wb') as out, (root / 'resume.stderr').open('wb') as err:
        process = subprocess.Popen(argv, stdout=out, stderr=err)
        status['docker_exec_pid'] = process.pid
        save(root / 'recovery-status.json', status)
        while process.poll() is None and time.time() < deadline + 5:
            time.sleep(min(1, max(.01, deadline + 5 - time.time())))
        if process.poll() is None:
            # The native deadline normally exits124. If not, signal only this
            # recorded auxiliary worker, then demand an observed terminal exec.
            pidfile = state / 'operator-recovery/resume-worker.pid'
            pid = pidfile.read_text().strip() if pidfile.exists() else ''
            if not pid.isdecimal():
                raise RuntimeError('Worker termination uncertain: no recorded PID; no final capture')
            stopped = subprocess.run(['docker', 'exec', container, '/bin/kill', '-KILL', pid], capture_output=True, timeout=10)
            status['deadline_signal'] = {'pid': int(pid), 'returncode': stopped.returncode}
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                raise RuntimeError('Worker termination remains uncertain; no final capture') from None
        status.update(worker_exit_code=process.returncode, cutoff=process.returncode == 124 or time.time() >= deadline,
                      state='worker_exited', worker_finished_at=time.time())
        save(root / 'recovery-status.json', status)
    pidfile = state / 'operator-recovery/resume-worker.pid'
    pid = pidfile.read_text().strip() if pidfile.exists() else ''
    if not pid.isdecimal():
        raise RuntimeError('No original worker PID receipt; termination is unverified')
    observation = json.loads(subprocess.check_output(['docker', 'exec', container, python, '-c',
        'from pathlib import Path; import json,sys; p=Path("/proc")/sys.argv[1]/"stat"; '
        's=p.read_text().split(") ",1)[1].split()[0] if p.exists() else None; '
        'print(json.dumps({"pid":int(sys.argv[1]),"process_state":s,"exited":s in (None,"Z")}))', pid],
        text=True, timeout=10))
    status['worker_exit_observation'] = observation
    save(root / 'recovery-status.json', status)
    if not observation['exited']:
        raise RuntimeError('Recorded worker still exists; no final capture after docker-client exit')
    # Finalization is invoked once and writes a separate recovery capture. It
    # stops recorded tool groups and exports the native journals before return.
    finalize_argv = ['docker', 'exec', '-w', inside_state + '/runtime', container, python,
        '-m', 'src.teamharness.course_worker', 'finalize', '--config', inside_config]
    save(root / 'finalize.intent.json', {'argv': finalize_argv, 'at': time.time()})
    with (root / 'finalize.stdout').open('wb') as out, (root / 'finalize.stderr').open('wb') as err:
        final = subprocess.run(finalize_argv, stdout=out, stderr=err,
                               timeout=max(1, min(600, deadline + 1800 - time.time() - 30)))
    status['finalize_exit_code'] = final.returncode
    native = state / 'evidence.tar.gz'
    transfer = read(state / 'transfer.json')
    if not native.is_file() or native.stat().st_size != transfer['bytes'] or digest(native) != transfer['sha256']:
        raise RuntimeError('Native evidence transfer receipt does not match collected bytes')
    status['native_evidence'] = {'path': str(native), 'sha256': digest(native), 'bytes': native.stat().st_size}
    if final.returncode == 0:
        candidate = state / 'candidate.patch'
        receipt = read(state / 'candidate.json')
        if digest(candidate) != receipt['sha256']:
            raise RuntimeError('Captured candidate differs from original receipt')
        submission = root / 'submitted.patch'
        with submission.open('xb') as stream:
            stream.write(candidate.read_bytes())
        scripts = root / 'scripts'
        scripts.mkdir(exist_ok=False)
        original_script = Path(manifest_path).resolve().parents[1] / '../starter_code/evaluation_scripts/evaluate_one.sh'
        shutil.copy2(original_script.resolve(), scripts / 'evaluate_one.sh')
        save(scripts / 'agent_task_input.json', {key: entry})
        timeout = grade_seconds(deadline)
        env = dict(os.environ, A3_CACHE='/data/a3cache', SWEBENCH_PRO_OS='/data/a3cache/SWE-bench_Pro-os',
            A3_DOCKER='python3 /opt/a3fleet/assignment3/dev/grading_docker.py',
            A3_GRADING_ENVIRONMENT='stock', A3_EVAL_TIMEOUT=str(timeout))
        grade_argv = ['bash', str(scripts / 'evaluate_one.sh'), key, str(submission), str(root / 'grade')]
        save(root / 'grade.intent.json', {'argv': grade_argv, 'started_at': time.time(),
            'timeout_seconds': timeout, 'original_outer_deadline': deadline + 1800, 'collection_headroom_seconds': 30,
            'script_sha256': digest(scripts / 'evaluate_one.sh'), 'submitted_sha256': digest(submission)})
        with (root / 'grade.stdout').open('wb') as out, (root / 'grade.stderr').open('wb') as err:
            grade = subprocess.run(grade_argv, stdout=out, stderr=err, env=env,
                                   timeout=max(1, deadline + 1800 - time.time() - 20))
        replay = root / 'grade/replay/model.patch'
        reward = root / 'grade/reward.txt'
        hashes = {'candidate': digest(candidate), 'submitted': digest(submission),
                  'graded': digest(replay) if replay.exists() else None}
        identity = hashes['candidate'] == hashes['submitted'] == hashes['graded']
        status['grade'] = {'exit_code': grade.returncode, 'reward': reward.read_text().strip() if reward.exists() else None,
            'patch_hashes': hashes, 'identity_verified': identity, 'empty_patch_not_executed': not candidate.read_bytes().strip()}
        if replay.exists() and not identity:
            raise RuntimeError('Recovered submitted and graded patch bytes differ')
    status.update(state='collected', finished_at=time.time())
    save(root / 'recovery-status.json', status)
    archive = root / 'recovery-evidence.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in ('resume.intent.json', 'resume.stdout', 'resume.stderr', 'finalize.intent.json', 'finalize.stdout',
                     'finalize.stderr', 'grade.intent.json', 'grade.stdout', 'grade.stderr', 'recovery-status.json',
                     'submitted.patch', 'grade', 'scripts', 'state/evidence.tar.gz', 'state/operator-recovery/manifest.json'):
            path = root / name
            if path.exists():
                tar.add(path, arcname=name)
    save(root / 'recovery-evidence.json', {'path': str(archive), 'sha256': digest(archive), 'bytes': archive.stat().st_size})
    removed = subprocess.run(['docker', 'rm', '-f', container], capture_output=True, text=True, timeout=30)
    status.update(auxiliary_cleanup={'returncode': removed.returncode, 'stdout': removed.stdout, 'stderr': removed.stderr},
                  state='complete' if removed.returncode == 0 else 'cleanup_unconfirmed')
    save(root / 'recovery-status.json', status)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--container', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    with (args.root / 'supervisor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = supervise(args.root, args.container, args.manifest)
        except Exception as error:
            save(args.root / 'supervisor-error.json', {'error_type': type(error).__name__, 'error': str(error),
                'at': time.time(), 'no_automatic_replay': True, 'cleanup_confirmed': False})
            raise
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
