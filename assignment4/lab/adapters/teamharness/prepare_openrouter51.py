"""Freeze current committed Python code for the existing HARD51 fleet launcher.

Local preparation only. The existing launcher owns paid dispatch and the frozen
fleet controller owns collection and termination. Other chats' worktrees and
already frozen campaigns are never changed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def install_tool_choice_bridge(source):
    path = source / 'dev/course_origin.py'
    before = path.read_bytes()
    text = before.decode()
    boundary = "    if spec.get('provider'):\n"
    if text.count(boundary) != 1:
        raise ValueError('Unrecognized course route translation boundary')
    replacement = '''    # OpenRouter's FP8 routes support a named function but reject required.
    # With one advertised tool these represent the same required function;
    # the runtime separately enforces its one-call planning contract.
    if (spec['upstream'] == 'openrouter' and translated.get('tool_choice') == 'required'
            and len(translated.get('tools', [])) == 1):
        translated['tool_choice'] = {'type': 'function', 'function': {
            'name': translated['tools'][0]['function']['name']}}
''' + boundary
    text = text.replace(boundary, replacement)
    compile(text, str(path), 'exec')
    path.write_text(text)
    return {'before_sha256': hashlib.sha256(before).hexdigest(),
            'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'change': 'OpenRouter only: single required function uses its explicit name; other tool choices unchanged'}


def prepare(args):
    repo = Path(__file__).resolve().parents[3]
    gitroot = Path(subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--show-toplevel'], text=True).strip())
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    snapshot = root / 'source-snapshot'
    snapshot.mkdir()
    with subprocess.Popen(['git', '-C', str(gitroot), 'archive', revision, 'assignment4'], stdout=subprocess.PIPE) as process:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            archive.extractall(snapshot, filter='data')
        if process.wait():
            raise RuntimeError('Committed source freeze failed')
    frozen = snapshot / 'assignment4'
    subprocess.run(['git', 'init', '-q', str(frozen)], check=True)
    common = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--path-format=absolute', '--git-common-dir'], text=True).strip()
    (frozen / '.git/objects/info/alternates').write_text(str(Path(common) / 'objects') + '\n')
    (frozen / '.git/HEAD').write_text(revision + '\n')
    sys.path.insert(0, str(frozen))
    receipt = module('hard51_build', frozen / 'lab/adapters/teamharness/build.py').build(
        root / 'package', solve_seconds=3600)
    assert 'allocation' not in receipt['campaign'] and receipt['campaign']['workers'] == 1
    with zipfile.ZipFile(root / 'package.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((root / 'package/src').rglob('*')):
            if path.is_file():
                archive.write(path, str(path.relative_to(root / 'package')))
    source = args.fleet_source.resolve()
    tasks = json.loads((source / 'dev/tasks/hard51.json').read_text())
    environment = module('hard51_environment', frozen / 'lab/adapters/teamharness/environments.py').freeze(
        source, root / 'package', list(tasks), root / 'environment-source',
        dependency_mode='stock', solver_dependencies='network', grading_dependencies='network')
    staged = root / 'environment-source'
    structured_route = module('hard51_route', frozen / 'lab/adapters/teamharness/route.py').patch_origin(
        staged / 'dev/course_origin.py')
    route = install_tool_choice_bridge(staged)
    bundle = staged / 'dev/team-environments'
    # Check each actual task source, rather than repeat generic fixture discovery
    # in all 51 images. The composed packaged flow runs once on each new host.
    path = bundle / 'environments.py'
    text = path.read_text()
    discovery = '"$py" -m unittest discover -s /check -p test_live_workspaces.py && '
    if text.count(discovery) != 1:
        raise ValueError('Unrecognized per-image source qualification boundary')
    path.write_text(text.replace(discovery, ''))
    tests = bundle / 'test/teamharness'
    tests.mkdir(parents=True)
    for name in ('test_main_flow.py', 'receipt_fixture.py'):
        shutil.copy2(frozen / 'test/teamharness' / name, tests / name)
    adapters = bundle / 'lab/adapters/teamharness'
    adapters.mkdir(parents=True)
    for name in ('check_main.py', 'report_fleet.py'):
        shutil.copy2(frozen / 'lab/adapters/teamharness' / name, adapters / name)
    (bundle / 'qualify_main.py').write_text('''import os, shutil, subprocess, sys, tempfile
from pathlib import Path
root = Path(__file__).resolve().parent
os.chdir(root)
os.environ['PYTHONPATH'] = os.pathsep.join([str(root/'test/teamharness'), str(root)])
work = Path(tempfile.mkdtemp(prefix='packaged-main-', dir='/data/environment-checks'))
shutil.copytree(root/'src', work/'package/src')
if (root/'fastjsonschema').is_dir():
    shutil.copytree(root/'fastjsonschema', work/'package/fastjsonschema')
subprocess.run([sys.executable, str(root/'lab/adapters/teamharness/check_main.py'),
    '--package', str(work/'package'), '--repo', str(root),
    '--output', str(work/'evidence'), '--completion-handoff'], check=True)
''')
    path = staged / 'starter_code/evaluation_scripts/prepare_images.sh'
    text = path.read_text()
    boundary = '    python3 "$root/dev/team-environments/environments.py" --host'
    if text.count(boundary) != 1:
        raise ValueError('Unrecognized host qualification boundary')
    path.write_text(text.replace(boundary, boundary + '\n    python3 "$root/dev/team-environments/qualify_main.py" > /data/environment-checks/packaged-main.log 2>&1\n    tar --zstd -cf /data/results/validation/environment-checks.tar.zst -C /data environment-checks'))
    environment['qualification'] = 'Actual source capture per selected image; one packaged completion/review handoff on each host; no regression discovery.'
    environment['openrouter_tool_choice_bridge'] = route
    environment['openrouter_structured_output_bridge'] = structured_route
    environment['package_inventory'] = {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(bundle.rglob('*')) if p.is_file() and p != bundle / 'config.json'}
    (bundle / 'config.json').write_text(json.dumps(environment, indent=2) + '\n')
    result = {'revision': revision, 'task_count': len(tasks), 'campaign': receipt['campaign'],
              'package_sha256': hashlib.sha256((root / 'package.zip').read_bytes()).hexdigest(),
              'tool_choice_bridge': route, 'environment_source': str(staged)}
    (root / 'preparation.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fleet-source', type=Path, required=True)
    print(json.dumps(prepare(parser.parse_args()), indent=2))
