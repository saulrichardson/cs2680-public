"""Finite public-evidence collector that releases the original paused fleet owner.

No model calls, provisioning, controller restart, or remote writes. Only the
three named public recovery artifacts are copied. SIGCONT is sent solely to the
recorded original controller after exact argv identity checks.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time


PUBLIC = ('recovery-evidence.tar.gz', 'recovery-evidence.json', 'recovery-status.json')
TERMINAL = {'complete', 'cleanup_unconfirmed'}


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, indent=2) + '\n')
    pending.replace(path)


def exclusive(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def process_matches(command, run, sweep):
    try:
        argv = shlex.split(command)
        controller = str(Path(run).resolve() / 'fleet-source/dev/fleet/fleet.py')
        position = argv.index(controller)
        return (argv.count(controller) == 1 and argv[position + 1] == 'sweep'
                and argv.count('--sweep') == 1 and argv[argv.index('--sweep') + 1] == sweep)
    except (ValueError, IndexError):
        return False


def ready_to_release(cases, now, hard_end):
    return now >= hard_end or bool(cases) and all(case.get('verified') is True for case in cases.values())


def verify_copy(directory, remote_root):
    directory = Path(directory)
    receipt, status = read(directory / PUBLIC[1]), read(directory / PUBLIC[2])
    archive = directory / PUBLIC[0]
    if receipt.get('path') != remote_root + '/' + PUBLIC[0]:
        raise ValueError('Recovery archive path differs from the owned task root')
    if type(receipt.get('bytes')) is not int or receipt['bytes'] < 0:
        raise ValueError('Invalid recovery archive byte receipt')
    observed = {'bytes': archive.stat().st_size, 'sha256': digest(archive)}
    if observed['bytes'] != receipt['bytes'] or observed['sha256'] != receipt.get('sha256'):
        raise ValueError('Copied recovery archive differs from original bytes/hash receipt')
    return {'verified': True, **observed, 'status': status.get('state'),
            'terminal': status.get('state') in TERMINAL, 'archive': str(archive)}


def release(run, launch, plan, reason):
    pid = launch['pid']
    receipt = {'at': time.time(), 'pid': pid, 'reason': reason, 'signal': 'SIGCONT', 'released': False}
    try:
        observed = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(state='identity_unverified_no_signal', error=str(error))
        try: save(run / 'recovery/release.json', receipt)
        except OSError: pass
        return receipt
    if observed.returncode == 1 and not observed.stdout.strip():
        receipt['state'] = 'original_controller_absent'
    elif observed.returncode or not process_matches(observed.stdout.strip(), run, plan['sweep']):
        receipt['state'] = 'identity_unverified_no_signal'
        receipt['error'] = 'Recorded PID is absent, unreadable, or no longer the exact owned run/sweep controller'
    else:
        receipt['command'] = observed.stdout.strip()
        # Persist intent before the signal. A caller must inspect an interrupted
        # release rather than launch a replacement controller.
        try: save(run / 'recovery/release.intent.json', receipt)
        except OSError as error: receipt['intent_write_error'] = str(error)
        try:
            os.kill(pid, signal.SIGCONT)
            receipt.update(state='signal_sent', released=True)
        except ProcessLookupError:
            receipt['state'] = 'original_controller_exited_before_signal'
        except OSError as error:
            receipt.update(state='signal_failed', error=str(error))
    try: save(run / 'recovery/release.json', receipt)
    except OSError as error: receipt['receipt_write_error'] = str(error)
    return receipt


def inputs(run):
    launch, plan = read(run / 'launch.json'), read(run / 'launch-plan.json')
    if launch.get('run') != str(run) or plan.get('run') != str(run):
        raise ValueError('Run identity differs from original controller intent')
    if launch['launch_plan_sha256'] != digest(run / 'launch-plan.json'):
        raise ValueError('Frozen launch plan hash differs')
    source = run / 'fleet-source/dev/fleet/fleet.py'
    if digest(source) != plan['controller_sha256']:
        raise ValueError('Frozen fleet controller hash differs')
    sweep = Path(plan['environment']['A3_FLEET_EVIDENCE']) / plan['sweep']
    metadata = read(sweep / 'sweep.json')
    if metadata['sweep'] != plan['sweep']:
        raise ValueError('Sweep metadata identity differs')
    manifest = read(run / 'fleet-source' / plan['manifest'])
    hosts = {host['name']: host for host in metadata['hosts']}
    cases = {}
    for row in read(run / 'recovery/supervisor-launches.json'):
        key = row['key']
        if key not in {'4', '18', '21'} or key in cases or row['exit_code'] != 0:
            raise ValueError('Unexpected or duplicate recovery launch')
        launch_receipt = json.loads(row['stdout'])
        iid = manifest[key]['instance_id']
        host = hosts[row['host']]
        if iid not in host['tasks'] or launch_receipt['root'] != '/data/recovery/' + iid:
            raise ValueError('Recovery does not match original assigned task/host')
        cases[key] = {'host': host, 'root': launch_receipt['root'], 'deadline': launch_receipt['original_deadline'], 'verified': False}
    if set(cases) != {'4', '18', '21'}:
        raise ValueError('Expected exactly the three authorized recovery tasks')
    spec = importlib.util.spec_from_file_location('owned_recovery_fleet', source)
    fleet = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fleet
    spec.loader.exec_module(fleet)
    return launch, plan, sweep, fleet, cases


def collect_case(run, sweep, fleet, key, case, hard_end):
    remaining = max(1, hard_end - time.time())
    code = ('from pathlib import Path; import json; r=Path(' + repr(case['root']) + '); '
            's=r/"recovery-status.json"; e=r/"recovery-evidence.json"; '
            'print(json.dumps({"status":json.loads(s.read_text()) if s.exists() else None,'
            '"evidence":json.loads(e.read_text()) if e.exists() else None}))')
    done = fleet.ssh(sweep, case['host'], 'sudo python3 -c ' + shlex.quote(code), timeout=min(30, remaining))
    public = json.loads(done.stdout)
    status = public.get('status') or {}
    case['observed_status'] = status.get('state')
    terminal = status.get('state') in TERMINAL
    # During final collection headroom, preserve any published archive even if
    # auxiliary cleanup is unfinished. It remains partial, never all-complete.
    if not public.get('evidence') or not terminal and time.time() < hard_end - 60:
        return
    destination = run / 'recovery/collected' / key
    destination.mkdir(parents=True, exist_ok=True)
    intent = destination / 'collection.intent.json'
    if not intent.exists():
        exclusive(intent, {'at': time.time(), 'host': case['host']['name'], 'root': case['root'], 'public_files': PUBLIC})
    for name in PUBLIC:
        remaining = hard_end - time.time()
        if remaining <= 0:
            return
        source = f"ubuntu@{case['host']['ip']}:{case['root']}/{name}"
        done = fleet.rsync(sweep, source, str(destination / name), '--rsync-path=sudo rsync', timeout=min(120, remaining))
        if done.returncode:
            raise RuntimeError(f'Public artifact copy failed: {name}, exit {done.returncode}')
    result = verify_copy(destination, case['root'])
    save(destination / 'verified.json', result)
    case.update(result, archive_verified=True, verified=result['terminal'])


def monitor(run):
    run = Path(run).resolve()
    launch, plan, sweep, fleet, cases = inputs(run)
    root = run / 'recovery'
    hard_end = max(case['deadline'] for case in cases.values()) + 1800 + 60
    with (root / 'collection.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        exclusive(root / 'collection.intent.json', {'pid': os.getpid(), 'at': time.time(), 'run': str(run),
            'controller_pid': launch['pid'], 'sweep': plan['sweep'], 'hard_end': hard_end,
            'source_sha256': digest(Path(__file__)), 'fleet_sha256': plan['controller_sha256'],
            'keys': sorted(cases), 'authority': 'public evidence copy and release exact original controller only'})
        while not ready_to_release(cases, time.time(), hard_end):
            for key, case in cases.items():
                if case['verified'] or time.time() >= hard_end:
                    continue
                try:
                    collect_case(run, sweep, fleet, key, case, hard_end)
                    case.pop('error', None)
                except Exception as error:
                    case['error'] = f'{type(error).__name__}: {error}'
                    case['last_error_at'] = time.time()
            try:
                save(root / 'collection-status.json', {'at': time.time(), 'hard_end': hard_end, 'cases': cases})
            except OSError:
                pass  # A reporting failure cannot skip the finite release bound.
            if not ready_to_release(cases, time.time(), hard_end):
                time.sleep(min(30, max(0, hard_end - time.time())))
        return release(run, launch, plan, 'all_recovery_archives_verified' if all(c['verified'] for c in cases.values()) else 'hard_outer_bound_reached_with_missing_evidence')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(monitor(args.run), indent=2))


if __name__ == '__main__':
    main()
