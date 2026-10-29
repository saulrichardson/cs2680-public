"""Freeze the maintained Python package for the existing two-host GPU replica.

Preparation is local only. The resulting tools/launch_iteration.py retains sole
ownership of provisioning, dispatch, collection and termination. The maintained
replica is the default; original course inputs remain explicit. No historical
solver or captured workload becomes part of this task-only experiment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import zipfile


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()
            and '__pycache__' not in p.parts and p.suffix != '.pyc'}


DEFAULTS = {'solve_seconds': 3600, 'select_tasks': 6, 'planning_workers': 3,
            'planning_turns': 15, 'model_slots': 3, 'engine_profile': 'stock-c5-headroom',
            'repair_guidance': 'default', 'workers': 1}


def settings(value, count):
    if not isinstance(value, dict) or set(value) - DEFAULTS.keys():
        raise ValueError('Unknown experiment settings: ' + str(value))
    result = DEFAULTS | value
    for name in ('solve_seconds', 'planning_workers', 'planning_turns', 'model_slots', 'workers'):
        if type(result[name]) is not int or result[name] < 1:
            raise ValueError(name + ' must be a positive integer')
    if result['select_tasks'] is not None and (type(result['select_tasks']) is not int or not 1 <= result['select_tasks'] <= count):
        raise ValueError('Require null (ordinary tasks) or 1 <= select_tasks <= cohort count')
    if result['engine_profile'] not in ('stock-c5', 'stock-c5-headroom'):
        raise ValueError('Unknown engine profile')
    if result['repair_guidance'] not in ('default', None):
        raise ValueError('repair_guidance must be default or null')
    return result


def prepare(args):
    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo))
    from lab.adapters.teamharness.build import build
    from lab.adapters.teamharness.course_submission import install_controller

    replica = args.replica.resolve()
    sys.path.insert(0, str(replica))
    from prepare_cohort import prepare as cohort
    from iteration import verify
    image_snapshot = None
    if args.image_snapshot:
        from image_snapshot import validate
        image_snapshot = validate(json.loads(args.image_snapshot.read_text()), replica / 'Dockerfile.gpu')

    spec = json.loads(args.spec.read_text()) if args.spec else {}
    if not isinstance(spec, dict) or set(spec) - {'name', 'count', 'seed', 'settings', 'variants', 'cpu_disk_gb'}:
        raise ValueError('Expected name, count, seed, cpu_disk_gb, settings and named variants')
    args.cpu_disk_gb = spec.get('cpu_disk_gb', getattr(args, 'cpu_disk_gb', None))
    if args.cpu_disk_gb is not None and (type(args.cpu_disk_gb) is not int or args.cpu_disk_gb < 200):
        raise ValueError('CPU storage must be an integer of at least 200 GiB')
    count, seed = spec.get('count', args.count), spec.get('seed', args.seed)
    if type(count) is not int or count < 1 or type(seed) is not int:
        raise ValueError('Cohort count must be positive and seed must be an integer')
    common = settings({'select_tasks': args.select, 'model_slots': args.model_slots,
                       'planning_workers': args.planning_workers,
                       'planning_turns': args.planning_turns,
                       'solve_seconds': args.solve_seconds,
                       'engine_profile': args.engine_profile, 'workers': args.workers} | spec.get('settings', {}), count)
    treatment = 'ordinary' if common['select_tasks'] is None else f'select{common["select_tasks"]}'
    variants = spec.get('variants', [{'name': f'python-random{count}-{treatment}'}])
    if not isinstance(variants, list) or not variants:
        raise ValueError('At least one named variant is required')
    names = set()
    cells = []
    for value in variants:
        if not isinstance(value, dict) or set(value) - {'name', 'settings'}:
            raise ValueError('A variant has a name and settings')
        name = value.get('name', '')
        if not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9-]{0,63}', name) or name in names:
            raise ValueError('Variant names must be unique lowercase names without path components')
        names.add(name)
        cells.append((name, settings(common | value.get('settings', {}), count)))

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    registry = (args.registry or output / 'registry').resolve()
    registry.mkdir(parents=True, exist_ok=True)
    selection = cohort(args.manifest.resolve(), output / 'cohort', count, seed, 1)
    source_revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    source_dirty = bool(subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True).strip())
    results = []
    shared_source = None
    shared_tools = None
    shared_course = None
    publications = {}
    for name, configuration in cells:
        cell = output / 'variants' / name if args.spec else output
        cell.mkdir(parents=True, exist_ok=True)
        receipt = build(cell / 'package', **configuration)
        # Compare captured runtime bytes, not just Git HEAD: the shared checkout
        # may change while a batch is being frozen. Only campaign settings and
        # the explicitly selected guide publication may differ between cells.
        source = {n: h for n, h in receipt['files'].items() if n.startswith('src/')
                  and n not in ('src/campaign.json', 'src/team-runtime.tar.gz')
                  and not n.startswith('src/teamharness/guides/portable-repair/')}
        if receipt['revision'] != source_revision or (shared_source is not None and source != shared_source):
            raise ValueError('Source changed during preparation; this incomplete batch must not be launched')
        shared_source = source
        publication = {n: h for n, h in receipt['files'].items() if n.startswith('engine/')
                       or n.startswith('src/teamharness/guides/portable-repair/')}
        identity = (configuration['engine_profile'], configuration['repair_guidance'])
        if identity in publications and publications[identity] != publication:
            raise ValueError('Engine/guidance changed during preparation; incomplete batch must not be launched')
        publications[identity] = publication
        result = freeze_cell(args, cell, name, configuration, receipt, output / 'cohort',
                             selection, registry, source_dirty, install_controller, verify, image_snapshot)
        captured = json.loads((Path(result['experiment']) / 'experiment.json').read_text())
        tooling = captured['tooling_files']
        if shared_tools is not None and tooling != shared_tools:
            raise ValueError('Replica tools changed during preparation; incomplete batch must not be launched')
        shared_tools = tooling
        course_inputs = {n: h for n, h in captured['files'].items() if n.startswith(
            ('course/dispatcher/', 'course/evaluation_scripts/', 'course/madsServe.py', 'cohort/'))}
        if shared_course is not None and shared_course != course_inputs:
            raise ValueError('Course/cohort changed during preparation; incomplete batch must not be launched')
        shared_course = course_inputs
        results.append(result)
    launcher = Path(results[0]['experiment']) / 'tools/launch_iteration.py'
    argv = ['python3', str(launcher)]
    for result in results:
        argv.extend(['--experiment', result['experiment']])
    batch = {'format': 1, 'name': spec.get('name', cells[0][0]), 'source_commit': source_revision,
             'registry': str(registry), 'cohort_sha256': selection['selected_manifest_sha256'],
             'variants': results, 'launch_argv': argv,
             'bundle_argv': argv + ['--bundle-only', str(output / 'validated-payload.tar.gz')],
             'resources_allocated': False}
    (output / 'batch.json').write_text(json.dumps(batch, indent=2) + '\n')
    (output / 'launch.sh').write_text('#!/usr/bin/env bash\nset -euo pipefail\nexec ' + shlex.join(argv) + ' "$@"\n')
    (output / 'launch.sh').chmod(0o755)
    return batch if args.spec else results[0] | {'batch': str(output / 'batch.json')}


def freeze_cell(args, output, name, configuration, receipt, cohort_path, selection,
                registry, source_dirty, install_controller, verify, image_snapshot):
    with zipfile.ZipFile(output / 'submission.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        for folder in ('src', 'engine'):
            for path in sorted((output / 'package' / folder).rglob('*')):
                if path.is_file():
                    archive.write(path, str(path.relative_to(output / 'package')))

    root = output / 'experiment'
    root.mkdir()
    shutil.copytree(cohort_path, root / 'cohort')
    course = root / 'course'
    course.mkdir()
    for folder in ('dispatcher', 'evaluation_scripts'):
        shutil.copytree(args.course / folder, course / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(args.course / 'madsServe.py', course / 'madsServe.py')
    for folder in ('src', 'engine'):
        shutil.copytree(output / 'package' / folder, course / folder)
    shutil.copy2(root / 'cohort/agent_task_input.json', course / 'evaluation_scripts/agent_task_input.json')
    final_submission = install_controller(course / 'evaluation_scripts/run_all.py')
    tools = root / 'tools'
    tools.mkdir()
    for path in args.replica.resolve().iterdir():
        if path.is_file() and not path.name.startswith('check_') and (path.suffix in ('.py', '.sh') or path.name in ('a4-serve', 'Dockerfile.gpu', 'transfer.json')):
            shutil.copy2(path, tools / path.name)
    shutil.copy2(Path(__file__).with_name('course_task.py'), tools / 'course_task.py')
    (root / 'workload').mkdir()  # Task-only run; no historical request corpus.
    count = selection['count']
    config = {
        'format': 1, 'name': name, 'experimental_settings': configuration,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'lab_root': str(registry), 'source_commit': receipt['revision'],
        'source_dirty': source_dirty,
        'build_receipt': receipt, 'resource_profile': 'gpu-cpu',
        'model': json.loads((course / 'engine/engine.json').read_text()),
        'submitted_flags': shlex.split((course / 'engine/flags.txt').read_text(), comments=True),
        'environment': selection['environment'] | {'A4_WORKERS': str(configuration['workers'])},
        'runner': ['python3', '../../tools/course_task.py', '--limit', str(count)],
        'stages': ['task'], 'task_count': count, 'cpu_type': args.cpu_type,
        'cpu_disk_gb': args.cpu_disk_gb,
        'graders': 3, 'cases': [], 'stop_batch_on_failure': True,
        'reuse_server': True, 'early_cpu_preparation': True,
        'image_snapshot': image_snapshot,
        'image_materialization': 'snapshot' if image_snapshot is not None else None,
        'server_reuse_tool_modes': {n: (tools / n).stat().st_mode & 0o777
                                   for n in ('a4-serve', 'start-gpu.sh', 'tcp_forward.py')},
        'input_roots': ['course', 'cohort', 'tools', 'workload'],
        'tooling_files': hashes(tools), 'files': hashes(root),
        'purpose': 'Seeded public-cohort rehearsal with one shared submission and original course grading.',
        'calibration': {'exact_course_runtime': False, 'known_differences': [
            'Reduced cohort and selection count reduce planning delay and inference contention.',
            'Private course driver, power and operating clocks remain uncalibrated.']},
        'submission_sha256': hashlib.sha256((output / 'submission.zip').read_bytes()).hexdigest(),
        'course_input_sha256': hashes(args.course),
        'final_submission': final_submission,
    }
    (root / 'experiment.json').write_text(json.dumps(config, indent=2) + '\n')
    verify(root)
    return {'name': name, 'experiment': str(root), 'package': str(output / 'package'),
            'settings': configuration, 'manifest_sha256': hashlib.sha256((root / 'experiment.json').read_bytes()).hexdigest(),
            'submission_sha256': config['submission_sha256'], 'cohort_sha256': selection['selected_manifest_sha256']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replica', type=Path, default=Path(__file__).resolve().parents[2] / 'replica',
                        help='Explicit reviewed alternative replica source; default is maintained lab/replica')
    parser.add_argument('--course', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--spec', type=Path, help='Named batch with common settings and per-variant overrides')
    parser.add_argument('--registry', type=Path, help='Shared local run registry; defaults to output/registry')
    parser.add_argument('--image-snapshot', type=Path, help='Exact retained image descriptor from the existing image_snapshot.py baker')
    parser.add_argument('--count', type=int, default=12)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--select', type=int, default=6)
    selection.add_argument('--no-allocation', dest='select', action='store_const', const=None,
                           help='Use each task owner directly, without cohort-wide plan ranking')
    parser.add_argument('--workers', type=int, default=1, help='Concurrent ordinary task owners')
    parser.add_argument('--seed', type=int, default=20261029)
    parser.add_argument('--model-slots', type=int, default=3)
    parser.add_argument('--planning-workers', type=int, default=3)
    parser.add_argument('--planning-turns', type=int, default=15)
    parser.add_argument('--solve-seconds', type=int, default=3600)
    parser.add_argument('--engine-profile', choices=['stock-c5', 'stock-c5-headroom'], default='stock-c5-headroom')
    parser.add_argument('--cpu-type', choices=['m7i.4xlarge', 'm7i.8xlarge', 'm7i.24xlarge'], default='m7i.8xlarge')
    parser.add_argument('--cpu-disk-gb', type=int, help='CPU root volume GiB, including retained cells and final evidence sealing')
    args = parser.parse_args()
    if args.select is not None and not 1 <= args.select <= args.count:
        parser.error('Require 1 <= select <= count')
    print(json.dumps(prepare(args), indent=2))
