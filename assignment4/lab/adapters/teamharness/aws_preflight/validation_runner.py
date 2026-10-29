"""Validation-only fleet entry point; the original controller remains the owner.

Install as dev/fleet/host/runner.py in a NEW source freeze. The byte-identical
original runner is runner.original.py beside it. This file does not provision,
reserve money, terminate instances, or independently collect evidence.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.pending')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def inventory(root):
    root = Path(root)
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def verify_inventory(root, expected):
    actual = inventory(root)
    if actual != expected:
        changed = sorted(k for k in actual.keys() | expected.keys() if actual.get(k) != expected.get(k))
        raise RuntimeError('Frozen input inventory changed: ' + repr(changed))



def packaged_check_spec(package):
    """Only the maintained main package is executable on this branch."""
    campaign = json.loads((Path(package) / 'src/campaign.json').read_text())
    if campaign.get('workflow') != 'main':
        raise ValueError('Only main packages are supported; historical packages require their frozen runtime')
    return 'main', 'lab/adapters/teamharness/check_main.py'


def verify_packaged_check(check, workflow, *, require_linux=False):
    """Check actual main fixture evidence without inferring model quality."""
    if workflow != 'main':
        raise ValueError('Only main packaged checks are supported')
    if require_linux and check.get('platform') != 'linux':
        raise RuntimeError('Packaged preflight check did not run on Linux')
    flags = ('passed', 'original_base_replay_verified', 'final_grade_reused',
             'selection_verified', 'returned_checkpoint_fallback_verified',
             'owned_jobs_stopped_at_course_finalization')
    digest = check.get('candidate_sha256', '')
    if (check.get('workflow') != 'main' or not all(check.get(key) is True for key in flags)
            or type(check.get('interactive_grades')) is not int or check['interactive_grades'] != 2
            or type(check.get('automatic_reviews')) is not int or check['automatic_reviews'] != 0
            or check.get('failed_counts') != [3, 0]
            or any(type(count) is not int for count in check['failed_counts'])
            or check.get('model_quality_measured') is not False
            or not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)):
        raise RuntimeError('Packaged main boundary fixture lacks complete passing coverage')
    return {'workflow': 'main', 'scripted_mechanics_verified': True,
            'platform': check.get('platform'), 'model_quality_measured': False}


def run_packaged_mechanics(repository, package, output):
    """Local existing packaged fixture only: no fleet, admission, live model or handoff."""
    repository, package, output = [Path(p).resolve() for p in (repository, package, output)]
    if any(output == root or root in output.parents for root in (repository, package)):
        raise ValueError('Mechanics evidence must be outside repository and package inputs')
    workflow, script = packaged_check_spec(package)
    before_repo, before_package = inventory(repository), inventory(package)
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, script, '--package', str(package), '--repo', str(repository),
               '--output', str(output / 'packaged')]
    result = {'scope': 'packaged_scripted_mechanics', 'workflow': workflow,
              'mechanics_passed': False, 'full_preflight_passed': False,
              'ready_for_original_runner': False, 'live_model_validation': 'not_run',
              'model_quality_measured': False, 'original_course_task_started': False, 'command': command}
    try:
        with (output / 'packaged-check.log').open('xb') as log:
            child = subprocess.run(command, cwd=repository,
                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, stdout=log, stderr=subprocess.STDOUT)
        result['exit_code'] = child.returncode
        if child.returncode:
            raise RuntimeError('Packaged scripted mechanics did not pass')
        check = json.loads((output / 'packaged/CHECK.json').read_text())
        result['verification'] = verify_packaged_check(check, workflow)
        verify_inventory(repository, before_repo)
        verify_inventory(package, before_package)
        result['mechanics_passed'] = True
    except Exception as error:
        result.update(error=str(error), error_type=type(error).__name__)
        raise
    finally:
        save(output / 'RESULT.json', result)
    return result


def verify_transport(agent, requests):
    """Bind original HTTP bytes to each preceding session call, retaining retries."""
    path = Path(agent) / 'context.sqlite3'
    if not path.is_file():
        raise RuntimeError('Live role lacks original transport database')
    observed, covered, source_index = [], set(), 0
    if any(b['created'] < a['created'] for a, b in zip(requests, requests[1:])):
        raise RuntimeError('Session request clock order is ambiguous')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        attempts = db.execute("SELECT id,body,sha256,created FROM objects WHERE kind='transport_request' ORDER BY id").fetchall()
        raws = {row[0]: row for row in db.execute("SELECT id,body,sha256 FROM objects WHERE kind='raw_request'")}
        used_raws = set()
        previous_at = float('-inf')
        for ident, body, digest, at in attempts:
            if hashlib.sha256(body.encode()).hexdigest() != digest:
                raise RuntimeError('Transport request object integrity check failed')
            if at < previous_at or at < requests[0]['created']:
                raise RuntimeError('Transport request has no unambiguous preceding session request')
            previous_at = at
            # Session._call records before entering its synchronous provider.
            # Retries occur before the next model_request in this same session.
            while source_index + 1 < len(requests) and requests[source_index + 1]['created'] <= at:
                source_index += 1
            original = requests[source_index]
            transport = json.loads(body)
            if transport.get('request') != original['request']:
                raise RuntimeError('Original transport request differs from its session request')
            links = db.execute("SELECT source FROM links JOIN objects ON objects.id=links.source "
                "WHERE relation='transport_request' AND target=? AND kind='raw_request'", (ident,)).fetchall()
            if len(links) != 1:
                raise RuntimeError('Transport request lacks exactly one original raw request')
            raw_id = links[0][0]
            _, metadata, metadata_digest = raws[raw_id]
            if raw_id in used_raws or hashlib.sha256(metadata.encode()).hexdigest() != metadata_digest:
                raise RuntimeError('Raw request ownership or metadata integrity check failed')
            owners = db.execute("SELECT target FROM links WHERE source=? AND relation='transport_request'", (raw_id,)).fetchall()
            if owners != [(ident,)]:
                raise RuntimeError('Raw request has ambiguous transport ownership')
            blob = db.execute('SELECT bytes FROM blobs WHERE object_id=?', (raw_id,)).fetchone()
            if blob is None:
                raise RuntimeError('Original raw request bytes are missing')
            raw, metadata = blob[0], json.loads(metadata)
            raw_digest = hashlib.sha256(raw).hexdigest()
            if metadata.get('sha256') != raw_digest or metadata.get('bytes') != len(raw):
                raise RuntimeError('Original raw request byte integrity check failed')
            if json.loads(raw) != transport['request']:
                raise RuntimeError('Original raw request differs from its transport request')
            used_raws.add(raw_id)
            covered.add(original['id'])
            observed.append({'session_request_ref': 'obj:' + str(original['id']),
                'transport_request_ref': 'obj:' + str(ident), 'raw_request_ref': 'obj:' + str(raw_id),
                'raw_request_sha256': raw_digest, 'raw_request_bytes': len(raw), 'purpose': original['purpose']})
        if used_raws != raws.keys():
            raise RuntimeError('Unaccounted original raw request evidence')
    if covered != {request['id'] for request in requests}:
        raise RuntimeError('Session request lacks original transport evidence')
    return {'verified': True, 'database': str(path), 'session_requests': len(requests),
            'transport_attempts': len(observed), 'attempts': observed}


def verify_wire(root, repository, *, original_guide_root=None):
    """Check saved requests; relocated archives retain their original prompt paths.

    original_guide_root affects rendering only. Guide bytes and transport records
    are always read from the supplied evidence root, never rewritten or replaced.
    """
    sys.path.insert(0, str(repository))
    from src.teamharness.presentation import session_facts
    from src.teamharness.prompts import system_prompt, guidance_manifest
    from src.teamharness.session import CONTROL_TOOLS
    from src.teamharness.tools import TOOLS
    manager = Path(root) / 'workflow/run/agents'
    rendered_guides = Path(original_guide_root) if original_guide_root is not None else manager / 'guides'
    expected_guides = guidance_manifest(repository / 'src/teamharness/guides')
    if guidance_manifest(manager / 'guides') != expected_guides:
        raise RuntimeError('Live probe guide snapshot differs from package source')
    rows = []
    for path in sorted((manager / 'agents').glob('*/agent.json')):
        descriptor = json.loads(path.read_text())
        # Session facts render from the descriptor's own recorded runtime fields.
        facts = {key: descriptor[key] for key in ('agent_id', 'parent', 'archive_root', 'workspace_mode', 'max_agents',
                 'task_deadline_unix', 'completion_gate', 'review_takeover', 'planning_boundary') if key in descriptor}
        expected = system_prompt(descriptor['role'], Path(descriptor['workspace']), guide_root=rendered_guides)
        expected += '\n\nSession facts (runtime):\n' + session_facts(facts)
        if descriptor['system'] != expected or descriptor['tools'] != TOOLS + CONTROL_TOOLS:
            raise RuntimeError('Live descriptor prompt/tool contract differs from frozen source')
        if descriptor['guide_hashes'] != expected_guides:
            raise RuntimeError('Live descriptor guide hashes differ from source')
        counts = {'agent': 0, 'compaction': 0}
        requests = []
        with closing(sqlite3.connect((path.parent / 'session.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            for ident, body, digest, at in db.execute("SELECT id,body,sha256,created FROM objects WHERE kind='model_request' ORDER BY id"):
                if hashlib.sha256(body.encode()).hexdigest() != digest:
                    raise RuntimeError('Original session request integrity check failed')
                event = json.loads(body)
                request = event['request']
                if request['messages'][0] != {'role': 'system', 'content': expected}:
                    raise RuntimeError('Actual request changed its frozen system prompt')
                if event['purpose'] == 'agent':
                    counts['agent'] += 1
                    if request.get('tools') != TOOLS + CONTROL_TOOLS:
                        raise RuntimeError('Actual agent request tool schemas differ from frozen package')
                elif event['purpose'].startswith('compaction'):
                    counts['compaction'] += 1
                    if 'tools' in request or 'tool_choice' in request or not request.get('response_format'):
                        raise RuntimeError('Compaction wire contract differs from no-tools structured request')
                else:
                    raise RuntimeError('Unknown live inference request purpose')
                requests.append({'id': ident, 'created': at, 'request': request, 'purpose': event['purpose']})
        if not counts['agent']:
            raise RuntimeError('Live role has no actual inference request')
        rows.append({'agent_id': descriptor['id'], 'role': descriptor['role'], 'requests': counts,
                     'transport': verify_transport(path.parent, requests),
                     'prompt_sha256': hashlib.sha256(expected.encode()).hexdigest(),
                     'tools_sha256': hashlib.sha256(json.dumps(TOOLS + CONTROL_TOOLS, sort_keys=True).encode()).hexdigest(),
                     'guide_hashes': expected_guides})
    if not rows:
        raise RuntimeError('No live role evidence found')
    return {'verified': True, 'rendered_guide_root': str(rendered_guides), 'sessions': rows}


class Validation:
    def __init__(self, plan, config, source):
        self.plan, self.config, self.source = plan, config, source
        self.data = Path('/data')
        self.output = self.data / 'teamharness-validation'
        self.output.mkdir(parents=True, exist_ok=False)
        self.started = time.time()
        self.results = []
        self.phase = 'initializing validation'
        self.host_deadline = float(Path('/etc/a3fleet-deadline').read_text())

    def status(self):
        save(self.data / 'status.json', {'sweep': self.plan['sweep'], 'host': self.plan['host'],
            'phase': self.phase, 'updated': time.time(), 'started': self.started,
            'items': {'validation': 1}, 'items_total': 1, 'running': [], 'running_model_lanes': 0,
            'validation': {'phases_finished': len(self.results), 'evidence': str(self.output)},
            'host_deadline': self.host_deadline})

    def run_phase(self, name, command, seconds, cwd, environment=None):
        self.phase = name
        started = time.time()
        deadline = min(started + seconds, self.host_deadline - 300)
        if deadline <= started:
            raise RuntimeError('Insufficient host lifetime for validation and collection')
        self.status()
        with (self.data / 'runner.log').open('a') as progress:
            progress.write(json.dumps({'at': started, 'phase': name, 'event': 'validation_phase_started'}) + '\n')
        with (self.output / (name + '.log')).open('xb') as log:
            child = subprocess.Popen(command, cwd=cwd, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                **(environment or {})}, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            timed_out = False
            try:
                while child.poll() is None:
                    if time.time() >= deadline:
                        timed_out = True
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                        break
                    self.status()
                    time.sleep(2)
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        receipt = {'phase': name, 'command': command, 'started_at': started,
                   'finished_at': time.time(), 'exit_code': child.returncode, 'timed_out': timed_out,
                   'deadline_scope': 'Preflight phase only; not the HARD51 solving policy'}
        self.results.append(receipt)
        save(self.output / 'phases.json', self.results)
        with (self.data / 'runner.log').open('a') as progress:
            progress.write(json.dumps({'at': time.time(), 'phase': name, 'event': 'validation_phase_finished',
                                      'exit_code': child.returncode, 'timed_out': timed_out}) + '\n')
        self.archive()  # Original collector can retain completed phases immediately.
        if timed_out or child.returncode:
            raise RuntimeError(name + ' did not pass; original course task has not started')

    def archive(self):
        destination = self.data / 'results/validation/validation.tar.zst'
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.pending')
        subprocess.run(['tar', '--zstd', '-cf', str(temporary), '-C', str(self.data), self.output.name], check=True)
        temporary.replace(destination)

    def fail(self, error):
        save(self.output / 'RESULT.json', {'passed': False, 'error': str(error), 'error_type': type(error).__name__,
             'phase': self.phase, 'original_course_task_started': False, 'at': time.time()})
        self.archive()
        manifest = json.loads((self.source / self.plan['manifest']).read_text())
        tasks = {row['instance_id']: row for row in manifest.values()}
        for arm in self.plan['arms']:
            for iid in self.plan['tasks']:
                save(self.data / 'results' / arm['name'] / (iid + '.json'), {
                    'instance_id': iid, 'repo': tasks[iid]['repo'], 'arm': arm['name'], 'kind': 'model',
                    'host': self.plan['host'], 'lane_status': 'validation_failed', 'solved': None,
                    'model_task_started': False, 'reason': 'infrastructure_error', 'error': str(error),
                    'validation_archive': '/data/results/validation/validation.tar.zst'})
        self.phase = 'validation_failed'
        self.status()
        save(self.data / 'DONE.json', {'finished': time.time(), 'items': len(self.plan['tasks']),
             'rows': len(self.plan['tasks']) * len(self.plan['arms']), 'validation_passed': False})

    def execute(self):
        config = self.config
        bundle = self.source / 'dev/teamharness-validation'
        repo, package = bundle / 'repository', bundle / 'package'
        self.phase = 'frozen input verification'
        verify_inventory(repo, config['repository_inventory'])
        verify_inventory(package, config['package_inventory'])
        for relative, expected in config['support_hashes'].items():
            if sha(self.source / relative) != expected:
                raise RuntimeError('Frozen support file changed: ' + relative)
        if self.plan['tasks'] != config['task_ids'] or len(self.plan['arms']) != 1:
            raise RuntimeError('Validation plan does not select the one authorized original task')
        if sha(self.plan['arms'][0]['zip']) != config['package_zip_sha256']:
            raise RuntimeError('Original course arm ZIP differs from validated package')
        # Portable fixture sandbox uses /work, outside all source captures.
        subprocess.run(['sudo', 'mkdir', '-p', '/work'], check=True)
        subprocess.run(['sudo', 'chown', f'{os.getuid()}:{os.getgid()}', '/work'], check=True)
        self.run_phase('native-suite', [sys.executable, str(bundle / 'run_suite.py'),
            '--output', str(self.output / 'suite.json')], 360, repo,
            {'TEAMHARNESS_COURSE_SOURCE': str(bundle / 'pristine-route-source/dev')})
        suite = json.loads((self.output / 'suite.json').read_text())
        if not suite['passed'] or suite['skipped'] or not suite['tests_run']:
            raise RuntimeError('Native suite failed, skipped required coverage or executed no tests')
        workflow, check_script = packaged_check_spec(package)
        self.run_phase('packaged-check', [sys.executable, check_script,
            '--package', str(package), '--repo', str(repo), '--output', str(self.output / 'packaged')], 360, repo)
        check = json.loads((self.output / 'packaged/CHECK.json').read_text())
        save(self.output / 'packaged-proof.json', verify_packaged_check(check, workflow, require_linux=True))
        raise RuntimeError('Main packaged mechanics passed; live main-workflow validation is not implemented. '
                           'No live Qwen probe or original course handoff was started.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', type=Path)
    mode.add_argument('--mechanics-only', action='store_true', help='Run only the existing scripted packaged fixture locally')
    parser.add_argument('--repository', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.mechanics_only:
        if not all((args.repository, args.package, args.output)):
            parser.error('--mechanics-only requires --repository, --package and --output')
        result = run_packaged_mechanics(args.repository, args.package, args.output)
        print(json.dumps(result))
        return 0
    source = Path(__file__).resolve().parents[3]
    config = json.loads((source / 'dev/teamharness-validation/manifest.json').read_text())
    plan = json.loads(args.plan.read_text())
    validation = Validation(plan, config, source)
    try:
        validation.execute()
    except BaseException as error:
        validation.fail(error)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
