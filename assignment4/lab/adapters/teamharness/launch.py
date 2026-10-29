"""Freeze, review, then admit one CPU-only Teamharness HARD51 fleet controller.

Preparation has no AWS or budget effects. Launch consumes an already admitted
campaign leaf; the existing fleet controller exclusively collects and terminates.
A launch intent is retained even if dispatch fails: inspect it, never retry it.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import zipfile

HARD51_SHA256 = '6a5db9806ffd3cc5942f70b34bfcfd38c1011972cee0f20463369240642d79cb'
CONTROLLER_SHA256 = 'bad65bb9361eae9aaadbca39b54d204b139e798f2239919bfadbd4dd6fdbe64d'
HOST_RUNNER_SHA256 = 'c31759dcdd10b8fc12cdb8b4fcc5f93f2d0bbe7e2f9ecc64359185ae982e91cd'
SCOPE = ('Four m7i.24xlarge CPU hosts plus gp3 storage/IOPS/throughput and '
         'ordinary evidence transfer; OpenRouter API excluded; conservative bound, not invoice')
PREFLIGHT_SCOPE = ('One m7i.2xlarge CPU host plus 400 GiB gp3 storage/IOPS/throughput '
                   'and ordinary evidence transfer; OpenRouter API excluded; conservative bound, not invoice')
SAMPLE_SCOPE = ('One m7i.12xlarge CPU host plus 1000 GiB gp3 storage/IOPS/throughput '
                'and ordinary evidence transfer; OpenRouter API excluded; conservative bound, not invoice')


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_once(path: Path, value: dict) -> None:
    with path.open('x') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())


def inventory(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Source must contain regular files, not symlinks: {path}')
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            result[str(path.relative_to(root))] = {'sha256': digest(path), 'mode': path.stat().st_mode & 0o777}
    return result


def check_package(path: Path, solve_seconds: int = 3600) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names or len(names) != len(set(names)):
            raise ValueError('Package is empty or contains duplicate ZIP members')
        for entry in archive.infolist():
            name = PurePosixPath(entry.filename)
            if (not name.parts or name.parts[0] != 'src' or '..' in name.parts
                    or name.is_absolute() or '\\' in entry.filename
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError(f'Invalid package member: {entry.filename}')
        required = {'src/campaign.json', 'src/team-runtime.tar.gz', 'src/agentic_loop.py',
                    'src/teamharness/course_worker.py'}
        if not required.issubset(names):
            raise ValueError('Package must contain the course team runtime and deadline adapter')
        campaign = json.loads(archive.read('src/campaign.json'))
        if campaign.get('workflow') != 'main':
            raise ValueError('This launcher requires the main package; historical packages use their frozen launcher')
        if campaign.get('workers') != 1 or campaign.get('solve_seconds') != solve_seconds:
            raise ValueError(f'Expected one task per lane and a shared {solve_seconds}-second solving limit')


def check_envelope(minutes: int, amount: float, rate: float, profile: str = 'hard51') -> None:
    if profile not in ('hard51', 'preflight', 'sample6', 'sample5', 'sample3', 'sample2'):
        raise ValueError('Unknown launch profile')
    if minutes < (60 if profile == 'preflight' else 90):
        raise ValueError('Host lifetime must cover the explicit lane and validation limits')
    if not all(math.isfinite(x) and x > 0 for x in (amount, rate)):
        raise ValueError('Reservation and hourly ceiling must be positive finite numbers')
    if amount + 1e-9 < rate * minutes / 60 + 1:
        raise ValueError('Reservation must cover hourly ceiling × host minutes / 60 + $1')


def validate_preflight(source: Path, package: Path, tasks: dict) -> None:
    bundle = source / 'dev/teamharness-validation'
    manifest = json.loads((bundle / 'manifest.json').read_text())
    expected = {'profile': 'teamharness-preflight', 'task_keys': ['32'],
        'task_ids': [tasks['32']['instance_id']], 'solver_limit_seconds': 900,
        'outer_lane_limit_seconds': 1800, 'host_max_minutes': 60,
        'minimum_course_remaining_seconds': 2100, 'controller_sha256': CONTROLLER_SHA256,
        'hard51_sha256': HARD51_SHA256, 'original_runner_sha256': HOST_RUNNER_SHA256,
        'wrapper_sha256': digest(source / 'dev/fleet/host/runner.py'),
        'package_zip_sha256': digest(package),
        'deployed_route_sha256': digest(source / 'dev/course_origin.py')}
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError('Preflight validation manifest mismatch: ' + key)
    for name in ('repository', 'package'):
        hashes = {key: value['sha256'] for key, value in inventory(bundle / name).items()}
        if not hashes or hashes != manifest.get(name + '_inventory'):
            raise ValueError('Preflight frozen input inventory mismatch: ' + name)
    support = manifest.get('support_hashes', {})
    required = {'dev/fleet/fleet.py', 'dev/fleet/host/runner.py', 'dev/fleet/host/runner.original.py',
                'dev/teamharness-validation/run_suite.py', 'dev/teamharness-validation/src-validation.zip',
                'dev/course_origin.py', 'dev/openrouter_origin.py', 'dev/course-routes/openrouter-fleet.json'}
    if not required.issubset(support):
        raise ValueError('Preflight support inventory lacks required evidence')
    for relative, sha in support.items():
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or digest(source / path) != sha:
            raise ValueError('Preflight support hash mismatch: ' + relative)


def validate_environment_source(source: Path) -> None:
    # Do not launch new campaigns through the known reference-informed warmer.
    # Already running campaigns retain their frozen launcher and ownership.
    for script in (source / 'starter_code/evaluation_scripts').glob('prepare_images*.sh'):
        if 'gold_patch' in script.read_text():
            raise ValueError('Reference-dependent environment preparation is not allowed. '
                             'Freeze reference-free inputs with environments.py before launch.')
    config = source / 'dev/team-environments/config.json'
    if config.exists():
        value = json.loads(config.read_text())
        if (value.get('reference_solutions_used') is not False or
                value.get('dependency_mode') not in ('stock', 'base-dependencies')):
            raise ValueError('Environment qualification lacks a reference-free dependency policy')


def prepare(args) -> dict:
    run = args.run.resolve()
    source = args.fleet_source.resolve()
    package = args.package.resolve()
    validate_environment_source(source)
    profile = getattr(args, 'profile', 'hard51')
    preflight = profile == 'preflight'
    sample = profile in ('sample6', 'sample5', 'sample3', 'sample2')
    sample_count = {'sample2': 2, 'sample3': 3, 'sample6': 6}.get(profile, 5)
    no_budget_limit = getattr(args, 'no_budget_limit', False)
    if not no_budget_limit:
        check_envelope(args.max_minutes, args.reserve_usd, args.hourly_ceiling, profile)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,89}', args.sweep):
        raise ValueError('Sweep must be a short lowercase AWS-safe name')
    manifest = Path(args.manifest)
    if manifest.is_absolute() or '..' in manifest.parts or manifest.parts[0] != 'dev':
        raise ValueError('Manifest must be a relative path under frozen dev/')
    original_manifest = source / manifest
    if digest(original_manifest) != HARD51_SHA256:
        raise ValueError('Manifest differs from the established exact HARD51 manifest')
    tasks = json.loads(original_manifest.read_text())
    ids = [row['instance_id'] for row in tasks.values()]
    if len(tasks) != 51 or len(set(ids)) != 51:
        raise ValueError('Expected all 51 unique HARD51 task identities')
    keys = getattr(args, 'task_keys', None)
    excluded = getattr(args, 'exclude_task_keys', None) or []
    if excluded:
        if profile != 'hard51' or keys or len(set(excluded)) != len(excluded) or any(k not in tasks for k in excluded):
            raise ValueError('Exclusions require distinct HARD51 keys and the full profile')
        ids = [row['instance_id'] for k, row in tasks.items() if k not in set(excluded)]
        if not ids:
            raise ValueError('Exclusions leave no tasks')
    if sample:
        if not keys or len(keys) != sample_count or len(set(keys)) != sample_count or any(k not in tasks for k in keys):
            raise ValueError('Sample requires the configured number of distinct keys from the exact HARD51 manifest')
        if digest(source / 'dev/fleet/host/runner.py') != HOST_RUNNER_SHA256:
            raise ValueError('Sample requires the unchanged original host runner')
        ids = [tasks[k]['instance_id'] for k in keys]
    elif keys:
        raise ValueError('Task selection is only supported by sample profiles')
    if digest(source / 'dev/fleet/fleet.py') != CONTROLLER_SHA256:
        raise ValueError('Existing fleet controller changed; review instead of silently replacing ownership')
    solve_seconds = 900 if preflight else getattr(args, 'solve_seconds', 3600)
    if type(solve_seconds) is not int or solve_seconds < 1:
        raise ValueError('Positive task solving window required')
    check_package(package, solve_seconds)
    if preflight:
        # The single prerequisite attempt is explicitly shorter than HARD51.
        # It is not a benchmark replacement or a sampled score estimate.
        if args.max_minutes != 60:
            raise ValueError('Preflight profile has an explicit 60-minute host lifetime')
        if not (source / 'dev/teamharness-validation/manifest.json').is_file():
            raise ValueError('Preflight requires the frozen validation manifest')
        if not (source / 'dev/fleet/host/runner.original.py').is_file():
            raise ValueError('Preflight requires the preserved original host runner')
        if digest(source / 'dev/fleet/host/runner.original.py') != HOST_RUNNER_SHA256:
            raise ValueError('Preflight original host runner differs from the established controller')
        validate_preflight(source, package, tasks)
        ids = [tasks['32']['instance_id']]
    evidence_root = args.evidence_root.resolve()
    if (evidence_root / args.sweep).exists():
        raise ValueError('Sweep evidence already exists; never restart it')
    run.mkdir(parents=True, exist_ok=True)
    if any((run / name).exists() for name in ('launch-plan.json', 'launch.intent.json', 'launch.json', 'fleet-source', 'src.zip')):
        raise ValueError('Run has existing frozen inputs or ownership; choose a new run directory')
    frozen = run / 'fleet-source'
    frozen.mkdir()
    for name in ('dev', 'starter_code'):
        shutil.copytree(source / name, frozen / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(package, run / 'src.zip')
    shutil.copy2(Path(__file__).resolve(), run / 'launch-controller.py')
    env = {'A3_FLEET_EVIDENCE': str(evidence_root), 'A3_FLEET_SWE_V2': str(args.swe_v2.resolve())}
    command = [sys.executable, '-u', str(frozen / 'dev/fleet/fleet.py'), 'sweep',
               '--sweep', args.sweep, '--manifest', str(manifest), '--arm', 'teamharness=' + str(run / 'src.zip'),
               '--hosts', '1' if preflight or sample else '4', '--lanes', '1' if preflight else (str(sample_count) if sample else '13'),
               '--prep-jobs', '1' if preflight else (str(sample_count) if sample else '6'), '--grading-slots', '1' if preflight else ('3' if sample else '4'),
               '--tasks-per-lane', '1', '--run-limit', '1800' if preflight else '5400', '--max-minutes', str(args.max_minutes),
               '--instance-type', 'm7i.2xlarge' if preflight else ('m7i.12xlarge' if sample else 'm7i.24xlarge'), '--volume-gb', '400' if preflight else ('1000' if sample else '2000'),
               '--grading-environment', 'stock', '--slots', 'us-east-1:on-demand',
               '--route', 'dev/course-routes/openrouter-fleet.json']
    if preflight:
        command += ['--tasks', ids[0]]
    elif sample or excluded:
        command += ['--tasks', ','.join(ids)]
    caffeinate = shutil.which('caffeinate')
    if caffeinate:
        command = [caffeinate, '-i', *command]
    plan = {'schema': 1, 'profile': profile, 'prepared_at': time.time(), 'run': str(run), 'sweep': args.sweep,
            'package_sha256': digest(run / 'src.zip'), 'manifest_sha256': HARD51_SHA256,
            'controller_sha256': CONTROLLER_SHA256, 'task_ids': sorted(ids),
            'manifest': str(manifest), 'command': command, 'environment': env,
            'source_inventory': inventory(frozen), 'launcher_sha256': digest(run / 'launch-controller.py'),
            'max_minutes': args.max_minutes, 'budget_limit_enabled': not no_budget_limit, 'reserve_usd': None if no_budget_limit else args.reserve_usd,
            'hourly_ceiling': args.hourly_ceiling, 'scope': PREFLIGHT_SCOPE if preflight else (SAMPLE_SCOPE if sample else SCOPE),
            'selected_task_keys': keys if sample else None, 'excluded_task_keys': excluded,
            'solver_limit_seconds': solve_seconds, 'outer_lane_limit_seconds': 1800 if preflight else 5400,
            'note': 'Solver deadline must be enforced by the packaged adapter. Budget starts at reservation; controller starts after reservation; each host timer starts during cloud-init. The unchanged controller has no absolute elapsed cutoff.'}
    write_once(run / 'launch-plan.json', plan)
    return plan


def validate_plan(run: Path) -> dict:
    run = run.resolve()
    plan = json.loads((run / 'launch-plan.json').read_text())
    if plan['run'] != str(run):
        raise ValueError('Frozen run moved; do not retarget a prepared controller')
    if plan.get('budget_limit_enabled', True):
        check_envelope(plan['max_minutes'], plan['reserve_usd'], plan['hourly_ceiling'], plan.get('profile', 'hard51'))
    validate_environment_source(run / 'fleet-source')
    check_package(run / 'src.zip', plan['solver_limit_seconds'])
    if (digest(run / 'src.zip') != plan['package_sha256']
            or inventory(run / 'fleet-source') != plan['source_inventory']
            or digest(run / 'launch-controller.py') != plan['launcher_sha256']):
        raise ValueError('Frozen package, source or launcher bytes changed')
    if digest(run / 'fleet-source' / plan['manifest']) != HARD51_SHA256:
        raise ValueError('Frozen HARD51 identity changed')
    if plan.get('profile') == 'preflight':
        validate_preflight(run / 'fleet-source', run / 'src.zip',
                           json.loads((run / 'fleet-source' / plan['manifest']).read_text()))
    if (Path(plan['environment']['A3_FLEET_EVIDENCE']) / plan['sweep']).exists():
        raise ValueError('Sweep evidence already exists; inspect existing owner')
    return plan


def launch(args) -> dict:
    run = args.run.resolve()
    plan = validate_plan(run)
    for name in ('launch.intent.json', 'launch.json', 'launch.log'):
        if (run / name).exists():
            raise ValueError('Existing or uncertain controller ownership; never redispatch')
    for name in ('aws', 'ssh', 'rsync'):
        if not shutil.which(name):
            raise ValueError(f'Required controller executable is absent: {name}')
    key = Path.home() / '.config/cs2680/openrouter-api-key'
    if not key.is_file() or not key.stat().st_size:
        raise ValueError('Existing OpenRouter credential file is absent or empty')
    if not Path(plan['environment']['A3_FLEET_SWE_V2']).is_dir():
        raise ValueError('Course SWE-bench v2 task files are unavailable')
    enabled = plan.get('budget_limit_enabled', True)
    intent = {'run': str(run), 'command': plan['command'], 'attempted_at': time.time(),
              'budget_state': str(args.budget_state.resolve()) if enabled else None,
              'launch_plan_sha256': digest(run / 'launch-plan.json')}
    write_once(run / 'launch.intent.json', intent)
    reservation = None
    if enabled:
        spec = importlib.util.spec_from_file_location('teamharness_launch_budget', args.budget_module.resolve())
        budget = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(budget)
        reservation = budget.reserve(args.budget_state.resolve(), run, amount=plan['reserve_usd'],
                                     hourly_ceiling=plan['hourly_ceiling'], scope=plan['scope'])
        write_once(run / 'reservation.json', reservation)
    with (run / 'launch.log').open('xb') as log:
        child = subprocess.Popen(plan['command'], cwd=run / 'fleet-source', stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True,
                                 env={**os.environ, **plan['environment']})
    receipt = {**intent, 'pid': child.pid, 'started_at': time.time(),
               'commitment_id': reservation['id'] if reservation else None, 'package_sha256': plan['package_sha256'],
               'manifest_sha256': plan['manifest_sha256']}
    write_once(run / 'launch.json', receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare-only', action='store_true')
    mode.add_argument('--launch', action='store_true')
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--profile', choices=('hard51', 'preflight', 'sample6', 'sample5', 'sample3', 'sample2'), default='hard51')
    parser.add_argument('--task-keys', nargs='+')
    parser.add_argument('--exclude-task-keys', nargs='+')
    parser.add_argument('--fleet-source', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--manifest', default='dev/tasks/hard51.json')
    parser.add_argument('--sweep')
    parser.add_argument('--max-minutes', type=int, default=120)
    parser.add_argument('--reserve-usd', type=float, default=60)
    parser.add_argument('--no-budget-limit', action='store_true')
    parser.add_argument('--solve-seconds', type=int, default=3600)
    parser.add_argument('--hourly-ceiling', type=float, default=28)
    parser.add_argument('--evidence-root', type=Path, default=Path.home() / 'projects/cs2680-expressive-evidence/fleet')
    parser.add_argument('--swe-v2', type=Path, default=Path.home() / 'projects/cs2680-functional-harness/work/course-cache/SWE-bench_Pro-os/v2')
    parser.add_argument('--budget-state', type=Path)
    parser.add_argument('--budget-module', type=Path)
    args = parser.parse_args()
    required = ('fleet_source', 'package', 'sweep') if args.prepare_only else (() if args.no_budget_limit else ('budget_state', 'budget_module'))
    if any(getattr(args, name) is None for name in required):
        parser.error('Required for this mode: ' + ', '.join('--' + name.replace('_', '-') for name in required))
    result = prepare(args) if args.prepare_only else launch(args)
    print(json.dumps({k: result[k] for k in ('run', 'sweep', 'pid', 'package_sha256', 'manifest_sha256', 'reserve_usd', 'max_minutes') if k in result}, indent=2))


if __name__ == '__main__':
    main()
