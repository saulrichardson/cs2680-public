"""Repair a saved Go candidate's cache, qualify it offline, then regrade its bytes.

Explicit operator action on an existing AWS host. Starts no model or fleet.
Original archives/results and image tags are never modified. Candidate and course
test inputs are bind-mounted into a temporary build; only dependency caches persist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def candidate_inputs(archive, instance, expected_sha, output):
    wanted = {'tasks.json', 'scaffold/model_patch_0.diff',
              'scaffold/pro_eval/0/replay/model.patch',
              'scaffold/pro_eval/0/grading-environment.json'}
    found = {}
    process = subprocess.Popen(['zstd', '-dc', str(archive)], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as stream:
            for member in stream:
                parts = PurePosixPath(member.name).parts
                if not parts or parts[0] != instance or '..' in parts or member.name.startswith('/'):
                    raise ValueError('Original archive member has an unexpected identity')
                name = '/'.join(parts[1:])
                if name in wanted:
                    if name in found or not member.isfile() or member.size > 20_000_000:
                        raise ValueError('Duplicate or invalid candidate evidence')
                    found[name] = stream.extractfile(member).read()
        if process.wait():
            raise ValueError('Original archive decompression failed')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
        process.wait()
    if found.keys() != wanted:
        raise ValueError('Original archive lacks exact submitted/graded candidate evidence')
    patch = found['scaffold/pro_eval/0/replay/model.patch']
    if (not patch or patch != found['scaffold/model_patch_0.diff']
            or hashlib.sha256(patch).hexdigest() != expected_sha):
        raise ValueError('Saved submitted and graded patch bytes do not match the requested SHA256')
    task = json.loads(found['tasks.json'])['0']
    grading = json.loads(found['scaffold/pro_eval/0/grading-environment.json'])
    if task['instance_id'] != instance or grading['image'] != task['sandbox_image']:
        raise ValueError('Original task and actual grading image differ')
    (output / 'candidate.patch').write_bytes(patch)
    return task, grading


def test_setup(script):
    """Use the course's own test reset/application prefix, without its verdict trap."""
    marker = '# Create temporary files for stdout and stderr'
    if script.count(marker) != 1 or script.count('trap cleanup_and_reward EXIT') != 1:
        raise ValueError('Unrecognized official test setup; review instead of guessing')
    prefix = script.split(marker)[0].replace('trap cleanup_and_reward EXIT', '')
    if prefix.count('cd /app 2>/dev/null') != 1:
        raise ValueError('Unrecognized course workspace selection')
    prefix = prefix.replace('cd /app 2>/dev/null', 'cd "${CANDIDATE_ROOT:-/app}" 2>/dev/null')
    return prefix + '\n'


PREPARE = r'''set -euo pipefail
export GOPROXY=https://proxy.golang.org,direct GOSUMDB=sum.golang.org GOTOOLCHAIN=local GOFLAGS=
export GOCACHE=/tmp/candidate-cache
before=$(git -C /app status --porcelain)
test "$(git -C /app rev-parse HEAD)" = "$(cat /ctx/base.txt)"
mkdir -p /tmp/candidate-prepare
cp -a /app /tmp/candidate-prepare/source
cd /tmp/candidate-prepare/source
git apply --check /ctx/candidate.patch
git apply /ctx/candidate.patch
go mod download all
go list -deps -test ./... >/tmp/candidate-prepare/candidate-dependencies
export CANDIDATE_ROOT="$PWD"
bash /ctx/test-setup.sh
go mod download all
go list -deps -test ./... >/tmp/candidate-prepare/grader-dependencies
test "$(git -C /app status --porcelain)" = "$before"
cd /
rm -rf /tmp/candidate-prepare /tmp/candidate-cache
'''

PREFLIGHT = r'''set -euo pipefail
cd /app
git apply --check /inputs/candidate.patch
git apply /inputs/candidate.patch
go list -deps -test ./... >/proof/candidate-dependencies.txt
go list -m all >/proof/candidate-modules.txt
bash /inputs/test-setup.sh
go list -deps -test ./... >/proof/grader-dependencies.txt
go list -m all >/proof/grader-modules.txt
# Compile every package and test binary; exec=true prevents test execution/init.
go test -p 2 -exec=true -run '^$' ./...
'''


def regrade(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    context = output / 'inputs'
    context.mkdir()
    task, grading = candidate_inputs(args.archive, args.instance, args.sha256, context)
    image = grading['image']
    image_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
    # Dockerfile FROM needs a named local image; a config ID is interpreted as
    # a registry repository. Bind a new unique name, then verify its content ID.
    base_tag = 'cs2680-candidate-input:' + image_id.removeprefix('sha256:')
    existing = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', base_tag],
                              capture_output=True, text=True)
    if existing.returncode == 0 and existing.stdout.strip() != image_id:
        raise ValueError('Existing candidate build tag points at different bytes')
    if existing.returncode:
        subprocess.run(['docker', 'tag', image_id, base_tag], check=True)
    if subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', base_tag], text=True).strip() != image_id:
        raise ValueError('Candidate build base identity changed')
    tests = args.tests.resolve()
    shutil.copytree(tests, context / 'tests')
    (context / 'base.txt').write_text(task['base_commit'] + '\n')
    (context / 'test-setup.sh').write_text(test_setup((tests / 'test.sh').read_text()))
    (context / 'prepare.sh').write_text(PREPARE)
    tag = 'cs2680-candidate-regrade:' + args.sha256[:24]
    (context / 'Dockerfile').write_text(f'FROM {base_tag}\n'
        'RUN --mount=type=bind,source=.,target=/ctx,readonly '
        '--mount=type=bind,source=tests,target=/tests,readonly bash /ctx/prepare.sh\n')
    identity = {'instance_id': args.instance, 'base_commit': task['base_commit'],
        'candidate_sha256': args.sha256, 'candidate_bytes': (context / 'candidate.patch').stat().st_size,
        'original_archive': str(args.archive), 'original_archive_sha256': digest(args.archive),
        'previous_grading_image': image_id, 'original_grading_record': grading,
        'verifier_inventory': {p.name: digest(p) for p in tests.iterdir() if p.is_file()},
        'evaluation_script_sha256': digest(args.evaluation_script),
        'models_started': 0, 'started_at': time.time()}
    save(output / 'identity.json', identity)
    phases = []

    def run(name, argv, env=None):
        receipt = {'name': name, 'argv': argv, 'started_at': time.time()}
        save(output / (name + '.intent.json'), receipt)
        with (output / (name + '.log')).open('xb') as log:
            code = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, env=env).returncode
        receipt.update(exit_code=code, finished_at=time.time())
        phases.append(receipt)
        save(output / 'phases.json', phases)
        if code:
            raise RuntimeError(name + ' failed; no subsequent phase was admitted')

    try:
        run('candidate-cache-build', ['docker', 'build', '--progress=plain', '-t', tag, str(context)])
        derived = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()
        proof = output / 'offline-proof'
        proof.mkdir()
        run('candidate-offline-preflight', ['docker', 'run', '--rm', '--network', 'none',
            '--cpus', '1', '--memory', '4g', '-e', 'GOPROXY=off', '-e', 'GOSUMDB=off',
            '-e', 'GOTOOLCHAIN=local', '-e', 'GOFLAGS=',
            '-v', str(context) + ':/inputs:ro', '-v', str(tests) + ':/tests:ro',
            '-v', str(proof) + ':/proof', '--entrypoint', 'bash', derived, '-c', PREFLIGHT])
        if digest(context / 'candidate.patch') != args.sha256:
            raise ValueError('Candidate input changed during qualification')
        save(output / 'qualification.json', {**identity, 'passed': True,
            'qualified_image': derived, 'network': 'none', 'cpus': 1, 'memory': '4g',
            'test_execution': False, 'full_candidate_and_verifier_dependency_graphs': True,
            'proof_files': {p.name: digest(p) for p in proof.iterdir() if p.is_file()},
            'finished_at': time.time()})
        scripts = output / 'grading/evaluation_scripts'
        scripts.mkdir(parents=True)
        shutil.copy2(args.evaluation_script, scripts / 'evaluate_one.sh')
        staged = dict(task, sandbox_image=derived)
        (scripts / 'agent_task_input.json').write_text(json.dumps({'0': staged}, indent=2))
        grade = output / 'grading/result'
        env = dict(os.environ, SWEBENCH_PRO_OS=str(tests.parents[3]), A3_EVAL_TIMEOUT='2100', A3_DOCKER='docker')
        run('exact-candidate-regrade', ['bash', str(scripts / 'evaluate_one.sh'), '0',
            str(context / 'candidate.patch'), str(grade)], env)
        if digest(grade / 'replay/model.patch') != args.sha256:
            raise ValueError('Grader did not replay the exact saved candidate')
        result = {**identity, 'state': 'complete', 'qualified_image': derived,
            'qualification_passed': True, 'regraded_candidate_sha256': digest(grade / 'replay/model.patch'),
            'reward': (grade / 'reward.txt').read_text().strip(),
            'verifier_stdout': (grade / 'verifier_stdout.txt').read_text(errors='replace'),
            'grader_stderr': (grade / 'run-script-stderr.txt').read_text(errors='replace'),
            'run_script_stdout': (grade / 'run-script-stdout.txt').read_text(errors='replace'),
            'tests': json.loads((grade / 'output.json').read_text()), 'finished_at': time.time()}
        result['raw_go_test_observations'] = [
            {'name': name, 'status': status, 'authority': 'raw Go output; original parser/verdict retained separately'}
            for status, name in re.findall(r'^--- (PASS|FAIL): (Test\S+)', result['run_script_stdout'], re.M)]
        save(output / 'RESULT.json', result)
        return result
    except BaseException as error:
        save(output / 'RESULT.json', {**identity, 'state': 'failed_before_verified_result',
            'error': str(error), 'finished_at': time.time()})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--instance', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--tests', type=Path, required=True)
    parser.add_argument('--evaluation-script', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(regrade(parser.parse_args()), indent=2))
