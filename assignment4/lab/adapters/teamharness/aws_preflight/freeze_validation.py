"""Prepare-only: stage exact validation inputs in a new fleet-source directory.

No provisioning, Docker, model requests, budget calls, or subprocess controller.
The maintained teamharness launch.py --profile preflight owns later admission.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tarfile
import zipfile

CONTROLLER_SHA256 = 'bad65bb9361eae9aaadbca39b54d204b139e798f2239919bfadbd4dd6fdbe64d'
HARD51_SHA256 = '6a5db9806ffd3cc5942f70b34bfcfd38c1011972cee0f20463369240642d79cb'
CORE = ('api.py', 'bridge.py', 'workspace.py', 'store.py', 'transport.py', 'sandbox_server.py', '__init__.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inventory(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def tree(source, destination):
    if any(p.is_symlink() for p in source.rglob('*')):
        raise ValueError('Validation freeze expects regular files: ' + str(source))
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def prepare(repository, fleet_source, pristine_route, package, output):
    repository, fleet_source, pristine_route, package, output = [Path(p).resolve() for p in
        (repository, fleet_source, pristine_route, package, output)]
    if output.exists():
        raise ValueError('Output exists; never modify a prepared or active source')
    if sha(fleet_source / 'dev/fleet/fleet.py') != CONTROLLER_SHA256:
        raise ValueError('Fleet controller differs from reviewed original')
    if sha(fleet_source / 'dev/tasks/hard51.json') != HARD51_SHA256:
        raise ValueError('Original HARD51 manifest differs from pin')
    campaign = json.loads((package / 'src/campaign.json').read_text())
    if campaign.get('workflow') != 'main':
        raise ValueError('Validation requires the main package; historical workflows use their frozen implementation')
    if campaign.get('workers') != 1 or campaign.get('solve_seconds') != 900:
        raise ValueError('Preflight package must explicitly use one worker and 900 seconds')
    package_inventory = inventory(package / 'src')
    with tarfile.open(package / 'src/team-runtime.tar.gz', 'r:gz') as archive:
        inner = {}
        for member in archive.getmembers():
            if not member.isfile() or not member.name.startswith('src/') or '..' in Path(member.name).parts:
                raise ValueError('Unexpected packaged runtime member')
            name = member.name[4:]
            if name in inner:
                raise ValueError('Duplicate packaged runtime member')
            inner[name] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    if inner != {name: value for name, value in package_inventory.items() if name != 'team-runtime.tar.gz'}:
        raise ValueError('Embedded runtime differs from outer package files')
    for name in CORE:
        if sha(repository / 'src' / name) != sha(package / 'src' / name):
            raise ValueError('Package core differs from maintained freeze: ' + name)
    for relative, expected in inventory(repository / 'src/teamharness').items():
        if sha(package / 'src/teamharness' / relative) != expected:
            raise ValueError('Package team runtime differs from maintained freeze: ' + relative)
    for name in ('agentic_loop.py', 'backend.py'):
        if sha(repository / 'lab/adapters/teamharness' / name) != sha(package / 'src' / name):
            raise ValueError('Course adapter differs from maintained freeze')
    if sha(repository / 'lab/adapters/teamharness/course_worker.py') != sha(package / 'src/teamharness/course_worker.py'):
        raise ValueError('Course worker differs from maintained freeze')
    output.mkdir(parents=True)
    tree(fleet_source / 'dev', output / 'dev')
    tree(fleet_source / 'starter_code', output / 'starter_code')
    bundle = output / 'dev/teamharness-validation'
    bundle.mkdir()
    staged = bundle / 'repository'
    (staged / 'src').mkdir(parents=True)
    for name in CORE:
        shutil.copy2(repository / 'src' / name, staged / 'src' / name)
    tree(repository / 'src/teamharness', staged / 'src/teamharness')
    tree(repository / 'test/teamharness', staged / 'test/teamharness')
    # Some Python distributions install a standard-library `test` package.
    # Explicit local packages keep the main fixture import unambiguous.
    for marker in (staged / 'test/__init__.py', staged / 'test/teamharness/__init__.py'):
        if not marker.exists():
            marker.write_text('"""Frozen validation fixtures; not solver input."""\n')
    tree(repository / 'lab/adapters/teamharness', staged / 'lab/adapters/teamharness')
    (bundle / 'package').mkdir()
    tree(package / 'src', bundle / 'package/src')
    original = output / 'dev/fleet/host/runner.original.py'
    shutil.copy2(output / 'dev/fleet/host/runner.py', original)
    preparation = Path(__file__).resolve().parent
    shutil.copy2(preparation / 'validation_runner.py', output / 'dev/fleet/host/runner.py')
    shutil.copy2(preparation / 'run_suite.py', bundle / 'run_suite.py')
    route_names = ('course_origin.py', 'openrouter_origin.py', 'course-routes/openrouter-fleet.json')
    for name in route_names:
        target = bundle / 'pristine-route-source/dev' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pristine_route / 'dev' / name, target)
    # Validate exact relation without modifying the staged pristine route.
    spec = importlib.util.spec_from_file_location('preflight_route_patch', repository / 'lab/adapters/teamharness/route.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    test_copy = bundle / 'route-check.py'
    shutil.copy2(pristine_route / 'dev/course_origin.py', test_copy)
    patch = module.patch_origin(test_copy)
    if sha(test_copy) != sha(output / 'dev/course_origin.py'):
        raise ValueError('Pristine route plus current extension differs from deployed course route')
    test_copy.unlink()
    for name in route_names[1:]:
        if sha(pristine_route / 'dev' / name) != sha(output / 'dev' / name):
            raise ValueError('Pristine and deployed model/provider configuration differs')
    archive_path = bundle / 'src-validation.zip'
    with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted((bundle / 'package/src').rglob('*')):
            if file.is_file():
                archive.write(file, str(file.relative_to(bundle / 'package')))
    tasks = json.loads((output / 'dev/tasks/hard51.json').read_text())
    support = ['dev/fleet/fleet.py', 'dev/fleet/host/runner.py', 'dev/fleet/host/runner.original.py',
               'dev/teamharness-validation/run_suite.py', 'dev/course_origin.py', 'dev/openrouter_origin.py',
               'dev/course-routes/openrouter-fleet.json', 'dev/teamharness-validation/src-validation.zip']
    support += ['dev/teamharness-validation/pristine-route-source/dev/' + name for name in route_names]
    manifest = {'schema': 1, 'profile': 'teamharness-preflight', 'prepared_only': True,
        'controller_sha256': CONTROLLER_SHA256, 'hard51_sha256': HARD51_SHA256,
        'original_runner_sha256': sha(original), 'wrapper_sha256': sha(output / 'dev/fleet/host/runner.py'),
        'task_keys': ['32'], 'task_ids': [tasks['32']['instance_id']],
        'solver_limit_seconds': 900, 'outer_lane_limit_seconds': 1800, 'host_max_minutes': 60,
        'minimum_course_remaining_seconds': 2100, 'campaign': campaign,
        'repository_inventory': inventory(staged), 'package_inventory': inventory(bundle / 'package'),
        'package_zip_sha256': sha(archive_path), 'deployed_route_sha256': sha(output / 'dev/course_origin.py'),
        'route_patch': patch, 'support_hashes': {name: sha(output / name) for name in support},
        'ownership': 'Original fleet controller collects and terminates; wrapper only gates original host runner.'}
    (bundle / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--fleet-source', type=Path, required=True)
    parser.add_argument('--pristine-route-source', type=Path, required=True)
    parser.add_argument('--package', type=Path, required=True, help='Built directory containing src/campaign.json with 900 seconds')
    parser.add_argument('--output', type=Path, required=True, help='New future fleet-source directory')
    args = parser.parse_args()
    result = prepare(args.repository, args.fleet_source, args.pristine_route_source, args.package, args.output)
    print(json.dumps({k: result[k] for k in ('profile', 'task_keys', 'package_zip_sha256', 'wrapper_sha256', 'original_runner_sha256')}, indent=2))
