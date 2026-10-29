"""Freeze an explicit AWS environment repair and check it before any model lane.

The original fleet controller/runner and original grading images stay intact.
The shared-assets preparation boundary checks every selected solver image first.
This is an opt-in experiment input, never a change to a running campaign.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def reference_free_prepare(text):
    """Retain course asset/image setup, removing the reference-informed warmer."""
    start = text.find('# --- 5. pre-warmed sandbox images')
    end = text.find('log "all images present"', start)
    if start < 0 or end < 0:
        raise ValueError('Unrecognized course preparation script; review its boundary')
    text = text[:start] + text[end:]
    old = 'need = ["tests/test.sh", "tests/config.json"] + (["solution/gold_patch.diff"] if t.get("sandbox_image") else [])'
    if text.count(old) != 1:
        raise ValueError('Unrecognized course verifier asset check')
    text = text.replace(old, 'need = ["tests/test.sh", "tests/config.json"]')
    text = text.replace("t.get('sandbox_image','')", "''")
    text = '#!/bin/bash\n# Reference-free course assets and pristine image preparation.\n' + text[text.index('set -euo pipefail'):]
    if 'gold_patch' in text:
        raise ValueError('Reference-patch dependency remains in preparation')
    return text


def freeze(source, package, keys, output, *, dependency_mode="stock", grading_dependencies="offline",
           solver_dependencies="offline"):
    if dependency_mode not in ("stock", "base-dependencies"):
        raise ValueError("Unknown dependency mode")
    if grading_dependencies not in ('offline', 'network'):
        raise ValueError('Unknown grading dependency access')
    if solver_dependencies not in ('offline', 'network'):
        raise ValueError('Unknown solver dependency access')
    if solver_dependencies == 'network' and grading_dependencies != 'network':
        raise ValueError('Solver network dependency access requires the same access in the grader')
    source, package, output = map(lambda p: Path(p).resolve(), (source, package, output))
    output.mkdir(parents=True, exist_ok=False)
    for name in ('dev', 'starter_code'):
        shutil.copytree(source / name, output / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    import importlib.util
    spec = importlib.util.spec_from_file_location('course_submission', Path(__file__).with_name('course_submission.py'))
    submission = importlib.util.module_from_spec(spec); spec.loader.exec_module(submission)
    submission_receipt = submission.install(output)
    bundle = output / 'dev/team-environments'
    bundle.mkdir()
    shutil.copy2(__file__, bundle / 'environments.py')
    shutil.copytree(package / 'src', bundle / 'src')
    if (package / 'fastjsonschema').is_dir():
        shutil.copytree(package / 'fastjsonschema', bundle / 'fastjsonschema')
    repository = Path(__file__).resolve().parents[3]
    shutil.copy2(repository / 'test/teamharness/test_live_workspaces.py', bundle / 'test_live_workspaces.py')
    manifest = json.loads((output / 'dev/tasks/hard51.json').read_text())
    selected = {k: manifest[k] for k in keys}
    if len(selected) != len(keys) or not selected:
        raise ValueError('Distinct selected tasks required')
    (bundle / 'selected.json').write_text(json.dumps(selected, indent=2))
    config = {'tasks': selected, 'dependency_mode': dependency_mode,
              'final_submission': submission_receipt,
              'grading_dependencies': grading_dependencies,
              'solver_dependencies': solver_dependencies,
              'reference_solutions_used': False,
              'submission_environment_parity': 'unverified: requires official deployment validation',
              'package_inventory': {str(p.relative_to(bundle)): sha(p)
                  for p in bundle.rglob('*') if p.is_file()},
              'grading': 'Solver and verifier both select sandbox_image in the provided runner. Stock binds both to docker_image; base-dependencies is a development intervention.'}
    (bundle / 'config.json').write_text(json.dumps(config, indent=2))
    prepare = output / 'starter_code/evaluation_scripts/prepare_images.sh'
    prepare.with_name('prepare_images.base.sh').write_text(reference_free_prepare(prepare.read_text()))
    prepare.write_text('''#!/bin/bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
if python3 -c 'import json,sys; sys.exit(0 if not json.load(open(sys.argv[1])) else 1)' "$1"; then
    bash "$here/prepare_images.base.sh" "$1"
    python3 "$root/dev/team-environments/environments.py" --host
else
    bash "$here/prepare_images.base.sh" "$@"
fi
''')
    evaluation = output / 'dev/run_evaluation.py'
    text = evaluation.read_text()
    needle = "    (scaffold / 'evaluation_scripts/agent_task_input.json').write_text(json.dumps(manifest, indent=2))"
    if text.count(needle) != 1:
        raise ValueError('Unrecognized course manifest staging boundary')
    replacement = """    # The provided runner uses sandbox_image for BOTH solver and verifier.
    environment_receipt = json.loads(Path('/data/environment-checks/RESULT.json').read_text())
    if not environment_receipt['passed']:
        raise RuntimeError('AWS task environments have not passed validation')
    for row in manifest.values():
        row['sandbox_image'] = environment_receipt['images'][row['instance_id']]['image_id']
""" + needle
    evaluation.write_text(text.replace(needle, replacement))
    if grading_dependencies == 'network':
        import importlib.util
        spec = importlib.util.spec_from_file_location('grading_dependencies', Path(__file__).with_name('grading_dependencies.py'))
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        helper.install(output)
    if solver_dependencies == 'network':
        import importlib.util
        spec = importlib.util.spec_from_file_location('solver_dependencies', Path(__file__).with_name('solver_dependencies.py'))
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        helper.install(output)
    return config


GO_PREPARE = r'''set -eu
export GOPROXY=https://proxy.golang.org,direct GOSUMDB=sum.golang.org GOTOOLCHAIN=auto GOFLAGS=
ROOT=/app; [ ! -d /testbed/.git ] || ROOT=/testbed
[ -f "$ROOT/go.mod" ] || exit 0
before=$(git -C "$ROOT" status --porcelain)
mkdir -p /tmp/team-env
cp -a "$ROOT" /tmp/team-env/base
cd /tmp/team-env/base
go mod download all
go list -deps -test ./... >/tmp/team-env/base-deps
test "$(git -C "$ROOT" status --porcelain)" = "$before"
cd /
rm -rf /tmp/team-env
'''

SOURCE_CHECK = '''from pathlib import Path
import tempfile
from src.teamharness.candidates import snapshot, capture
from src.teamharness.workspaces import prepare_live_workspace
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    repo = next(p for p in (Path('/testbed'), Path('/app')) if (p/'.git').exists())
    frozen = snapshot(repo, root/'baseline', root/'evidence')
    spec = prepare_live_workspace(repo, frozen, root/'live')
    assert capture(repo, root/'candidate.patch', spec['base'])['bytes'] == 0
print('Task source snapshot, object import and exact empty capture passed')
'''


def host(bundle):
    config = json.loads((bundle / 'config.json').read_text())
    for relative, expected in config['package_inventory'].items():
        if sha(bundle / relative) != expected:
            raise ValueError('Frozen environment input changed: ' + relative)
    output = Path('/data/environment-checks')
    output.mkdir(parents=True, exist_ok=True)
    attempt = output / str(time.time_ns())
    attempt.mkdir()
    results = {}
    mode = config['dependency_mode']
    selected = config['tasks']
    plan = Path('/opt/a3fleet/plan.json')
    if plan.exists():
        assigned = set(json.loads(plan.read_text())['tasks'])
        selected = {k: t for k, t in selected.items() if t['instance_id'] in assigned}
        if {t['instance_id'] for t in selected.values()} != assigned:
            raise ValueError('Host assignment differs from frozen environment inputs')

    def check(entry):
        key, task = entry
        iid = task['instance_id']
        directory = attempt / key
        directory.mkdir(exist_ok=True)
        phases = []

        def run(name, argv):
            started = time.time()
            with (directory / (name + '.log')).open('w') as log:
                code = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT).returncode
            phases.append({'name': name, 'argv': argv, 'exit_code': code,
                           'started_at': started, 'finished_at': time.time()})
            save(directory / 'phases.json', phases)
            if code:
                raise RuntimeError(f'{iid}: {name} failed; see its retained log')

        image = task['docker_image']
        try:
            # Shared portable Python, wheels and agent image are already ready.
            # Pull/warm independent task images concurrently, without admitting
            # any solver until every task's subsequent qualification succeeds.
            task_input = directory / 'task.json'
            task_input.write_text(json.dumps({key: {k: v for k, v in task.items() if k != 'sandbox_image'}}))
            run('course-prepare', ['bash', str(bundle.parents[1] /
                'starter_code/evaluation_scripts/prepare_images.base.sh'), str(task_input)])
            if mode == 'base-dependencies':
                context = directory / 'build'
                context.mkdir(exist_ok=True)
                script = GO_PREPARE
                (context / 'prepare.sh').write_text(script)
                (context / 'Dockerfile').write_text(f'FROM {image}\nRUN --mount=type=bind,source=.,target=/ctx,readonly bash /ctx/prepare.sh\n')
                image = 'cs2680-team-environment:' + hashlib.sha256((iid + script).encode()).hexdigest()[:24]
                run('build', ['docker', 'build', '--progress=plain', '-t', image, str(context)])
            image_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
            common = ['docker', 'run', '--rm', '--network', 'none', '--cpus', '1', '--memory', '4g',
                      '-v', str(bundle) + ':/check:ro',
                      '-v', '/data/a3cache/assets/portable_python:/opt/agentpy:ro',
                      '-v', '/data/a3cache/assets/portable_python_musl:/opt/agentpy_musl:ro',
                      '-e', 'PYTHONPATH=/check', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                      '--entrypoint', 'sh', image_id, '-c']
            python = 'if [ -f /lib/ld-musl-x86_64.so.1 ]; then py=/opt/agentpy_musl/bin/python3; else py=/opt/agentpy/bin/python3; fi; '
            import shlex
            run('git-and-source', common + [python + 'git --version; "$py" -m unittest discover -s /check -p test_live_workspaces.py && "$py" -c ' + shlex.quote(SOURCE_CHECK)])
            if config.get('grading_dependencies') == 'network':
                network = list(common)
                network[network.index('--network') + 1] = 'bridge'
                probe = ('import urllib.request,hashlib; '
                    'urls=["https://proxy.golang.org/github.com/mpvl/unique/@v/v0.0.0-20150818121801-cbe035fff7de.mod",'
                    '"https://proxy.golang.org/github.com/cockroachdb/apd/v2/@v/v2.0.2.mod"]; '
                    '[(print(u,hashlib.sha256(urllib.request.urlopen(u,timeout=30).read()).hexdigest())) for u in urls]')
                run('grader-dependency-network', network + [python + '"$py" -c ' + shlex.quote(probe)])
            if config.get('solver_dependencies') == 'network':
                # Exercise the same dual-network topology as start_sandbox,
                # not only a standalone Docker bridge. No project build gate.
                private = 'team-dependencies-' + str(time.time_ns())
                container = private + '-probe'
                run('solver-private-network', ['docker', 'network', 'create', '--internal', private])
                try:
                    start = ['docker', 'run', '-d', '--name', container, '--network', private,
                        '--cpus', '1', '--memory', '4g', '-v', '/data/a3cache/assets/portable_python:/opt/agentpy:ro',
                        '-v', '/data/a3cache/assets/portable_python_musl:/opt/agentpy_musl:ro',
                        '-e', 'GOPROXY=https://proxy.golang.org,direct', '-e', 'GOSUMDB=sum.golang.org',
                        '-e', 'GOTOOLCHAIN=auto', '--entrypoint', 'sh', image_id, '-c', 'sleep 120']
                    run('solver-start', start)
                    run('solver-gateway', ['docker', 'network', 'connect', 'bridge', container])
                    run('solver-dependency-network', ['docker', 'exec', container, 'sh', '-c',
                        python + '"$py" -c ' + shlex.quote(probe)])
                finally:
                    subprocess.run(['docker', 'rm', '-f', container], check=False, capture_output=True)
                    subprocess.run(['docker', 'network', 'rm', private], check=False, capture_output=True)
            return iid, {'passed': True, 'image': image, 'image_id': image_id,
                         'original_grading_image': task['docker_image'], 'dependency_mode': mode,
                         'solver_dependencies': config.get('solver_dependencies', 'offline'),
                         'reference_solutions_used': False, 'phases': phases}
        except Exception as error:
            return iid, {'passed': False, 'error': str(error), 'phases': phases}

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for iid, result in pool.map(check, selected.items()):
            results[iid] = result
            save(output / 'progress.json', results)
    report = {'dependency_mode': mode, 'reference_solutions_used': False,
              'grading_dependencies': config.get('grading_dependencies', 'offline'),
              'solver_dependencies': config.get('solver_dependencies', 'offline'),
              'submission_environment_parity': config['submission_environment_parity'],
              'passed': bool(results) and all(r['passed'] for r in results.values()),
              'images': results, 'finished_at': time.time(),
              'scope': 'Harness/source checks offline at 1 CPU / 4 GiB; explicit dependency probes check grader bridge and solver private-plus-bridge access. Only this host assignment is prepared.'}
    save(attempt / 'RESULT.json', report)
    save(output / 'RESULT.json', report)
    archive = Path('/data/results/validation/environment-checks.tar.zst')
    archive.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['tar', '--zstd', '-cf', str(archive), '-C', '/data', output.name], check=True)
    print(json.dumps(report), flush=True)
    if not report['passed']:
        raise RuntimeError('Environment qualification failed; no model tasks may start')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', action='store_true')
    parser.add_argument('--fleet-source', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--task-keys', nargs='+')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--dependency-mode', choices=['stock', 'base-dependencies'], default='stock',
                        help='Stock task images by default; base-only cache warming is a development intervention')
    parser.add_argument('--grading-dependencies', choices=['offline', 'network'], default='network')
    parser.add_argument('--solver-dependencies', choices=['offline', 'network'], default='network',
                        help='Task sandboxes can resolve real dependencies; requires --grading-dependencies network. Use offline for a control.')
    args = parser.parse_args()
    if args.host:
        host(Path(__file__).resolve().parent)
    else:
        if not all((args.fleet_source, args.package, args.task_keys, args.output)):
            parser.error('Freeze requires --fleet-source, --package, --task-keys and --output')
        freeze(args.fleet_source, args.package, args.task_keys, args.output, dependency_mode=args.dependency_mode,
               grading_dependencies=args.grading_dependencies, solver_dependencies=args.solver_dependencies)
