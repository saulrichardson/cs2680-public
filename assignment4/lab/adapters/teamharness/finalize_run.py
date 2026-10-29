"""One-shot reporting after the existing fleet controller exits.

This program cannot launch, collect, terminate, restart, or change a task. AWS
operations are describe-only. A verified cleanup observation closes only the
matching campaign budget commitment; it is not an invoice or budget refund.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, indent=2) + '\n')
    pending.replace(path)


def read(path):
    return json.loads(Path(path).read_text())


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def controller_processes(run, launch, plan):
    done = subprocess.run(['ps', '-axww', '-o', 'pid=', '-o', 'stat=', '-o', 'command='],
                          capture_output=True, text=True, timeout=30)
    if done.returncode:
        return {'state': 'unknown', 'error': done.stderr[-4000:]}
    controller = str(run / 'fleet-source/dev/fleet/fleet.py')
    matches, recorded = [], None
    for line in done.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit():
            continue
        pid, state, command = int(fields[0]), fields[1], fields[2]
        if pid == launch['pid']:
            recorded = {'pid': pid, 'state': state}
        if controller in command and plan['sweep'] in command and not state.startswith('Z'):
            matches.append({'pid': pid, 'state': state, 'command': command})
    # Also find the controller child if the original caffeinate PID disappeared.
    return {'state': 'running' if matches else 'exited', 'matching_processes': matches,
            'recorded_pid': recorded, 'recorded_pid_reused': bool(recorded and not any(p['pid'] == recorded['pid'] for p in matches))}


def aws_describe(region, operation, arguments):
    if operation not in ('describe-instances', 'describe-volumes'):
        raise ValueError('Postprocessing permits only read-only EC2 descriptions')
    done = subprocess.run(['aws', '--region', region, 'ec2', operation, *arguments,
                           '--output', 'json', '--no-cli-pager'],
                          capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise RuntimeError(f'{operation} failed ({done.returncode}): {done.stderr[-4000:]}')
    return json.loads(done.stdout)


def cleanup_observation(metadata, sweep_name, aws=aws_describe):
    result = {'verified': False, 'regions': {}, 'errors': []}
    hosts = metadata.get('hosts', [])
    if not hosts:
        result['errors'].append('No recorded host identities; cleanup cannot be established')
        return result
    groups = {}
    for host in hosts:
        ident, region = host.get('instance_id', ''), host.get('region', '')
        if not re.fullmatch(r'i-[0-9a-f]+', ident) or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d', region):
            result['errors'].append('Invalid or missing exact instance identity/region')
            return result
        groups.setdefault(region, []).append(ident)
    if len({h['instance_id'] for h in hosts}) != len(hosts):
        result['errors'].append('Duplicate recorded instance identity')
        return result
    for region, identities in sorted(groups.items()):
        observation = {'expected_instances': sorted(identities), 'verified': False}
        result['regions'][region] = observation
        try:
            exact = aws(region, 'describe-instances', ['--instance-ids', *sorted(identities)])
            observation['exact_response'] = exact
            instances = [i for r in exact['Reservations'] for i in r['Instances']]
            states = {i['InstanceId']: i['State']['Name'] for i in instances}
            observation['instance_states'] = states
            owned_tags = all({t['Key']: t['Value'] for t in i.get('Tags', [])}.get('a3fleet-sweep') == sweep_name for i in instances)
            observation['exact_ownership_tags_match'] = owned_tags
            exact_terminated = set(states) == set(identities) and all(s == 'terminated' for s in states.values()) and owned_tags
            scope = ['--filters', f'Name=tag:a3fleet-sweep,Values={sweep_name}']
            tagged = aws(region, 'describe-instances', scope)
            observation['tagged_instance_response'] = tagged
            tagged_live = [i['InstanceId'] for r in tagged['Reservations'] for i in r['Instances']
                           if i['State']['Name'] != 'terminated']
            volumes = aws(region, 'describe-volumes', scope)
            observation['tagged_volume_response'] = volumes
            observation['live_tagged_instances'] = tagged_live
            observation['tagged_volumes'] = [v['VolumeId'] for v in volumes['Volumes']]
            observation['verified'] = exact_terminated and not tagged_live and not volumes['Volumes']
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as error:
            observation['error'] = f'{type(error).__name__}: {error}'
            result['errors'].append(f'{region}: {observation["error"]}')
    result['verified'] = bool(result['regions']) and not result['errors'] and all(x['verified'] for x in result['regions'].values())
    return result


def inputs(run):
    launch, plan = read(run / 'launch.json'), read(run / 'launch-plan.json')
    if launch.get('run') != str(run) or plan.get('run') != str(run):
        raise ValueError('Launch identity does not match this run directory')
    if launch.get('launch_plan_sha256') != hashlib.sha256((run / 'launch-plan.json').read_bytes()).hexdigest():
        raise ValueError('Launch plan bytes differ from the recorded launch intent')
    if launch.get('package_sha256') != plan.get('package_sha256'):
        raise ValueError('Launch package differs from prepared package')
    if hashlib.sha256((run / 'src.zip').read_bytes()).hexdigest() != plan['package_sha256']:
        raise ValueError('Frozen package bytes changed')
    manifest_rel = Path(plan['manifest'])
    if manifest_rel.is_absolute() or '..' in manifest_rel.parts:
        raise ValueError('Frozen manifest must be relative to the owned fleet source')
    manifest = run / 'fleet-source' / manifest_rel
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != plan['manifest_sha256']:
        raise ValueError('Frozen manifest bytes changed')
    identities = [row['instance_id'] for row in read(manifest).values()]
    if len(identities) != 51 or len(set(identities)) != 51:
        raise ValueError('Expected exactly the frozen 51 unique task identities')
    if plan.get('profile') in ('sample6', 'sample5', 'sample3', 'sample2'):
        sample_count = {'sample2': 2, 'sample3': 3, 'sample6': 6}.get(plan['profile'], 5)
        entries = read(manifest)
        keys = plan.get('selected_task_keys', [])
        if len(keys) != sample_count or len(set(keys)) != sample_count or any(k not in entries for k in keys):
            raise ValueError('Invalid sample selection')
        selected = {k: entries[k] for k in keys}
        if sorted(row['instance_id'] for row in selected.values()) != sorted(plan['task_ids']):
            raise ValueError('Selected identities differ from frozen launch')
        # Derived reporting view only; retain the original full manifest unchanged.
        save(run / 'postprocess/selected-manifest.json', selected)
        # Reporter binds the original full manifest hash to sweep metadata and
        # selects scheduled identities itself. Never pass the derived view.
    elif plan.get('excluded_task_keys'):
        entries = read(manifest)
        excluded = plan['excluded_task_keys']
        if len(set(excluded)) != len(excluded) or any(k not in entries for k in excluded):
            raise ValueError('Invalid full-profile exclusions')
        selected = [v['instance_id'] for k, v in entries.items() if k not in excluded]
        if not selected or sorted(selected) != sorted(plan['task_ids']):
            raise ValueError('Excluded identities differ from frozen launch')
    elif sorted(identities) != sorted(plan['task_ids']):
        raise ValueError('Expected exactly the frozen 51 unique task identities')
    sweep = Path(plan['environment']['A3_FLEET_EVIDENCE']) / plan['sweep']
    return launch, plan, manifest, sweep


def finish(run, *, report, observe_budget, aws=aws_describe,
           processes=controller_processes, clock=time.time, sleep=time.sleep, wait=False):
    """Dependency injection keeps tests entirely offline and without processes."""
    run = Path(run).resolve()
    output = run / 'postprocess'
    output.mkdir(exist_ok=True)
    launch, plan, manifest, sweep = inputs(run)
    # This is only a finite observation window. It never extends a host lifetime.
    end = launch['started_at'] + plan['max_minutes'] * 60 + 1800
    result = {'state': 'waiting_for_controller', 'run': str(run), 'sweep': plan['sweep'],
              'started_at': clock(), 'wait_deadline': end,
              'authority': 'read-only reporting/AWS verification and matching budget observation only'}
    while True:
        controller = processes(run, launch, plan)
        result['controller'] = controller
        save(output / 'status.json', result)
        if controller['state'] == 'exited':
            break
        if not wait or controller['state'] == 'unknown' or clock() >= end:
            result['state'] = 'controller_state_unknown' if controller['state'] == 'unknown' else 'controller_still_running'
            save(output / 'status.json', result)
            return result
        sleep(min(60, max(0, end - clock())))
    try:
        metadata = read(sweep / 'sweep.json')
        if metadata['sweep'] != plan['sweep'] or sorted(metadata['tasks']) != sorted(plan['task_ids']):
            raise ValueError('Collected sweep identity differs from this launch')
        arms = [arm for arm in metadata.get('arms', []) if arm.get('name') == 'teamharness']
        if len(arms) != 1 or arms[0].get('sha256') != plan['package_sha256']:
            raise ValueError('Collected sweep package differs from this launch')
        summary = report(sweep, manifest, arm='teamharness', output=output / 'report.json')
        expected = len(plan['task_ids'])
        if summary['expected'] != expected or sorted(row['instance_id'] for row in summary['tasks']) != sorted(plan['task_ids']):
            raise ValueError('Reporter did not account for the exact selected tasks')
        result['report_path'] = str(output / 'report.json')
        result['summary'] = {k: v for k, v in summary.items() if k != 'tasks'}
        result['results_complete'] = summary['collected'] == expected and summary['unknown'] == 0 and summary['evidence_errors'] == 0
    except Exception as error:
        result.update(results_complete=False, reporting_error=f'{type(error).__name__}: {error}')
        metadata = read(sweep / 'sweep.json') if (sweep / 'sweep.json').exists() else {}
        if metadata.get('sweep') != plan['sweep']:
            metadata = {}
    attempt = 0
    while True:
        attempt += 1
        cleanup = dict(cleanup_observation(metadata, plan['sweep'], aws), observed_at=clock())
        receipt = output / f'cleanup-{attempt:04d}.json'
        # Preserve earlier invocations rather than silently replace an observation.
        while receipt.exists():
            attempt += 1
            receipt = output / f'cleanup-{attempt:04d}.json'
        save(receipt, cleanup)
        result.update(cleanup_verified=cleanup['verified'], cleanup_receipt=str(receipt))
        if cleanup['verified'] or cleanup['errors'] or not wait or clock() >= end:
            break
        save(output / 'status.json', result)
        sleep(min(60, max(0, end - clock())))
    result['budget_closed'] = False
    if not plan.get('budget_limit_enabled', True):
        result.update(budget_closed=True, budget_limit_enabled=False)
    elif cleanup['verified']:
        try:
            ledger = Path(launch['budget_state'])
            commitments = read(ledger)['commitments']
            if len(commitments) != 1 or commitments[0]['id'] != launch['commitment_id'] or commitments[0]['run'] != str(run):
                raise ValueError('Budget ledger is not this launch’s single campaign leaf')
            item = observe_budget(ledger, launch['commitment_id'], now=clock(), cleaned=True)
            if item.get('id') != launch['commitment_id'] or item.get('run') != str(run) or item.get('cleanup_verified_at') is None:
                raise ValueError('Budget observation did not confirm the matching campaign cleanup')
            save(output / 'budget-observation.json', item)
            result.update(budget_closed=True, budget_receipt=str(output / 'budget-observation.json'))
        except Exception as error:
            result['budget_error'] = f'{type(error).__name__}: {error}'
    result.update(state='complete' if result.get('results_complete') and result['cleanup_verified'] and result['budget_closed'] else 'incomplete',
                  finished_at=clock())
    save(output / 'status.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--wait', action='store_true', help='Observe every 60 seconds until controller exit/cleanup, bounded by launch + host lifetime + 30 minutes')
    parser.add_argument('--reporter', type=Path, default=Path(__file__).with_name('report_fleet.py'))
    parser.add_argument('--budget-module', type=Path, default=Path(__file__).resolve().parents[2] / 'replica/budget.py')
    args = parser.parse_args()
    output = args.run.resolve() / 'postprocess'
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'finalize.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = finish(args.run, wait=args.wait,
                report=load_module(args.reporter, 'team_postprocess_reporter').report,
                observe_budget=load_module(args.budget_module, 'team_postprocess_budget').observe)
        except Exception as error:
            result = {'state': 'postprocess_error', 'error': f'{type(error).__name__}: {error}', 'cleanup_verified': False}
            save(output / 'status.json', result)
    print(json.dumps(result, indent=2))
    return 0 if result['state'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
