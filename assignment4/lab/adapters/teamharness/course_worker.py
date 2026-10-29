"""Runs the frozen team inside the task environment; exports exact source evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import tarfile
import threading
import time
import traceback
import uuid

from src import workspace as effects
from .agents import AgentManager
from .baseline import digest as file_digest
from .candidates import capture, submodule_state
from .main import Investigation
from .provider import HTTPProvider


def save(path, value):
    effects.dump(Path(path), value)


PUBLIC_SECTIONS = (('problem_statement', 'PROBLEM STATEMENT'), ('requirements', 'REQUIREMENTS'), ('interface', 'INTERFACE'))


def public_task_text(public, deadline, grading):
    """Literal public fields under labels; course runtime facts stay separate."""
    from .presentation import clock
    parts = [f'{label}\n{public[key]}' for key, label in PUBLIC_SECTIONS if public.get(key)]
    parts.append('RUNTIME (course adapter facts, not task requirements)\n'
                 f'Task deadline: {clock(deadline)}, shared by every role and descendant.\n{grading}')
    return '\n\n'.join(parts)


def resume_contract(config):
    """A restored workflow keeps its recorded deadline and existing role state."""
    state = Path(config['state'])
    run_root = state / 'run'
    if not (run_root / 'workflow.json').is_file():
        raise ValueError('No persisted workflow to resume; no new task or snapshot is created')
    manager = read(run_root / 'agents/manager.json')
    if manager is None or manager.get('deadline') != config['deadline']:
        raise ValueError('Resume must retain the original recorded shared solver deadline')
    if time.time() >= config['deadline']:
        raise TimeoutError('Original shared solver deadline expired; task cannot be renewed')
    if manager.get('max_agents') != config['max_agents']:
        raise ValueError('Resume cannot change the recorded manager capacity')
    return run_root


def feedback_client(config):
    """Replay immutable selected bytes against original course base, then grade once."""
    import subprocess
    import urllib.request
    def grade(candidate):
        state = Path(config['state'])
        raw = Path(candidate['patch_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != candidate['sha256']:
            raise ValueError('Selected patch changed before grading')
        folder = state / 'interactive-candidates' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        replay = folder / 'replay'
        subprocess.run(['git', 'clone', '--quiet', '--shared', '--no-checkout',
                        config['task']['workdir'], str(replay)], check=True, capture_output=True)
        try:
            effects.git(replay, 'reset', '--hard', config['course_base'])
            initial = (state / 'run/source/source-before.patch').read_bytes()
            frozen = read(state / 'run/source/snapshot.json')
            if hashlib.sha256(initial).hexdigest() != frozen['source_patch_sha256']:
                raise ValueError('Original starting patch identity changed')
            for patch in (initial, raw):
                if patch:
                    subprocess.run(['git', '-C', str(replay), 'apply', '--binary', '-'],
                                   input=patch, check=True, capture_output=True)
            captured = effects.capture(replay, folder / 'course.patch', config['course_base'])
            course_patch = Path(captured['patch_path']).read_bytes()
            request = {'candidate_sha256': candidate['sha256'],
                       'course_sha256': captured['sha256'], 'patch': course_patch.decode('utf-8')}
            save(folder / 'identity.json', {k: v for k, v in request.items() if k != 'patch'})
            remaining = config['deadline'] - time.time()
            if remaining <= 0:
                raise TimeoutError('Shared solving deadline expired before course evaluation')
            req = urllib.request.Request(config['base_url'].rstrip('/') + '/grade',
                data=json.dumps(request).encode(), headers={'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + config['relay_token']})
            # Never retry an uncertain evaluation. The adapter retains late receipts.
            with urllib.request.urlopen(req, timeout=remaining) as response:
                result = json.loads(response.read())
            save(folder / 'receipt.json', result)
            return result
        finally:
            import shutil
            shutil.rmtree(replay)
    return grade


def run(config, *, resume=False, admission=False):
    if config.get('workflow') != 'main':
        raise ValueError('This runtime only continues main investigations; use the historical package for other workflows')
    state, task = Path(config['state']), config['task']
    run_root = resume_contract(config) if resume else state / 'run'
    output = state
    if resume:
        output = state / 'resume-attempts' / uuid.uuid4().hex
        output.mkdir(parents=True, exist_ok=False)
        save(output / 'intent.json', {'operation': 'resume', 'started_at': time.time(),
            'original_deadline': config['deadline'], 'workflow': str(run_root / 'workflow.json')})
    else:
        run_root.mkdir(exist_ok=False)
    os.environ.update(A4_BASE_URL=config['base_url'], A4_MODEL=config['model'],
                      A4_API_KEY=config['relay_token'], A4_API_PROTOCOL='chat_completions')
    # The only solver deadline is the externally configured shared task window.
    def deadline():
        time.sleep(max(0, config['deadline'] - time.time()))
        save(state / 'deadline.json', {'deadline': config['deadline'], 'at': time.time()})
        os._exit(124)
    threading.Thread(target=deadline, daemon=True).start()
    public = {k: task[k] for k in ('problem_statement', 'requirements', 'interface') if k in task}
    grading = ('Course counts-only grading and post-grade repair share this deadline; final capture/grading may finish afterward.'
               if config.get('grade_feedback') else 'The external evaluator grades the captured candidate after all solver roles stop.')
    # The model reads labelled literal fields; observers fingerprint this record.
    if not (run_root / 'public-task.json').exists():
        save(run_root / 'public-task.json', {**public, 'runtime': {'shared_solver_deadline_unix': config['deadline'], 'grading': grading}})
    from .task_guidance import select_guidance
    supplement = select_guidance(Path(__file__).with_name('task-guidance.json'), task)
    def run_facts():
        import urllib.request
        request = urllib.request.Request(config['base_url'].rstrip('/') + '/runtime-facts',
            data=b'{}', headers={'Authorization': 'Bearer ' + config['relay_token'], 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    manager = AgentManager(run_root / 'agents', HTTPProvider, max_agents=config['max_agents'],
        deadline=config['deadline'], prompt_supplement=supplement, run_facts_provider=run_facts)
    try:
        feedback = feedback_client(config) if config.get('grade_feedback') else None
        investigation = Investigation(manager, run_root, feedback_grader=feedback,
                                     completion_gate=config.get('completion_gate', False),
                                     planning_boundary=config.get('planning_boundary', False))
        result = (investigation.admit('Selected by the deterministic course task allocation policy.') if admission else
                  investigation.resume() if resume else investigation.run(
            public_task_text(public, config['deadline'], grading), Path(task['workdir'])))
        if result['state'] == 'planning_complete':
            returned = result['main_return']
            save(state / 'planning-result.json', {'state': result['state'],
                'agent_id': result['agents']['main'], 'session_id': returned['session_id'],
                'planning': returned['planning'], 'deadline': config['deadline'],
                'meaning': 'Planning evidence and model estimates only; no candidate is accepted.'})
        save(output / 'worker-result.json', {'state': result['state'], 'finished_at': time.time(),
            'operation': 'resume' if resume else 'run', 'original_deadline': config['deadline']})
    except BaseException:
        (output / 'error.txt').write_text(traceback.format_exc())
        raise
    finally:
        manager.shutdown(wait=True)


def group_live(group):
    """Linux receipt check ignores dead, not-yet-reaped child zombies."""
    for stat in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = stat.read_text().split(') ', 1)[1].split()
            if int(fields[2]) == group and fields[0] != 'Z':
                return True
        except (OSError, ValueError, IndexError):
            continue
    return False


def stop_jobs(state):
    results = []
    for descriptor in sorted((state / 'run/agents/agents').glob('*/agent.json')):
        d = json.loads(descriptor.read_text())
        ws = {'root': d['workspace'], 'state': str(descriptor.parent / 'workstate'),
              'base': d.get('base'), 'capture_patch': bool(d.get('base'))}
        for job in sorted((descriptor.parent / 'workstate/jobs').glob('*/request.json')):
            ident = job.parent.name
            try:
                observed = effects.observe(ws, ident, count=0)
                if observed.get('state') == 'running':
                    observed = effects.dispatch('job', {'workspace': ws, 'id': ident,
                        'action': 'kill', 'yield_time_ms': 1000})
                # A completed shell can leave redirected background children.
                # Its exit receipt is retained; inspect the recorded process group
                # separately before claiming source is stable for final capture.
                process = read(job.parent / 'process.json', {})
                group = process.get('process_group')
                live = bool(group and group_live(group))
                if live:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                until = time.monotonic() + 10
                while group and group_live(group) and time.monotonic() < until:
                    time.sleep(.05)
                results.append({'agent_id': d['id'], 'job_id': ident,
                                'state': observed.get('state'), 'exit_code': observed.get('exit_code'),
                                'background_group_killed': live, 'process_group': group,
                                'group_stopped': not group or not group_live(group)})
            except Exception as error:
                results.append({'agent_id': d['id'], 'job_id': ident, 'state': 'unknown', 'error': str(error)})
    return results


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def choose_candidate(config, *, allow_live=True):
    state = Path(config['state'])
    run_root = state / 'run'
    workflow = read(run_root / 'workflow.json', {})
    if workflow and workflow.get('workflow') != 'main':
        raise ValueError('Historical candidate selection requires its frozen runtime')
    accepted = workflow.get('candidate') if workflow.get('state') == 'completed' else None
    gated = workflow.get('completion_gate', {}).get('enabled')
    gate = workflow.get('completion_attempts', {}).get(workflow.get('latest_completion'), {})
    verified_completion = (gate.get('state') == 'passed'
        and gate.get('agent_id') == workflow.get('agents', {}).get('main'))
    if gated and (not verified_completion or
                  (accepted and gate.get('candidate', {}).get('sha256') != accepted.get('sha256'))):
        accepted = None
    main_return_fallback = False
    if accepted is None and workflow.get('workflow') == 'main':
        ident = workflow.get('agents', {}).get('main')
        returned = read(run_root / 'agents/agents' / str(ident) / 'runner.json', {})
        if returned.get('status') == 'completed' and (not gated or (verified_completion and
                gate.get('candidate', {}).get('sha256') == (returned.get('patch') or {}).get('sha256'))):
            accepted = returned.get('patch')
            main_return_fallback = accepted is not None
    if accepted is not None:
        raw = Path(accepted['patch_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != accepted['sha256']:
            raise RuntimeError('Accepted candidate identity changed')
        initial = (run_root / 'source/source-before.patch').read_bytes()
        role = 'main'
        kind = 'main_returned_candidate' if main_return_fallback else 'main_candidate'
        return {'kind': kind, 'source_agent_id': workflow['agents'][role],
                'source_role': role, 'source_status': 'completed', 'reviewed': False,
                'workflow_state': workflow['state'], 'selected_sha256': accepted['sha256'],
                'selected_base': accepted['base']}, [initial, raw]
    if not allow_live:
        return None
    # Interrupted main source is evidence, not a correctness verdict.
    ident = workflow.get('agents', {}).get('main')
    if ident:
        agent_root = run_root / 'agents/agents' / ident
        descriptor = read(agent_root / 'agent.json')
        runner = read(agent_root / 'runner.json', {})
        if descriptor is not None:
            captured = capture(descriptor['workspace'], state / 'main-fallback.patch', config['course_base'])
            confirm = capture(descriptor['workspace'], state / 'main-fallback-check.patch', config['course_base'])
            if captured['sha256'] != confirm['sha256']:
                raise RuntimeError('Candidate still changing after solver stop')
            if captured['bytes']:
                return {'kind': 'unreviewed_fallback', 'source_agent_id': ident, 'source_role': 'main',
                        'source_status': runner.get('status', 'unknown'), 'reviewed': False,
                        'workflow_state': workflow.get('state', 'unavailable'),
                        'selected_sha256': captured['sha256'], 'selected_base': config['course_base']}, [Path(captured['patch_path']).read_bytes()]
    return {'kind': 'empty', 'source_agent_id': None, 'source_role': None, 'source_status': 'unavailable',
            'reviewed': False, 'workflow_state': workflow.get('state', 'unavailable'),
            'selected_sha256': hashlib.sha256(b'').hexdigest(), 'selected_base': config['course_base']}, []


def finalize(config):
    state = Path(config['state'])
    # Selection of already captured bytes is independent of live jobs/source.
    selected = choose_candidate(config, allow_live=False)
    try:
        jobs = stop_jobs(state)
    except Exception as error:
        jobs = [{'state': 'unknown', 'error': str(error)}]
    save(state / 'owned-jobs-stop.json', jobs)
    selection, patches = selected if selected is not None else choose_candidate(config)
    save(state / 'selection.json', selection)
    frozen = read(state / 'run/source/snapshot.json', {})
    if frozen.get('original_base') != config['course_base']:
        raise RuntimeError('Starting source does not identify the original course base')
    # Materialize preserved bytes, preferring their verified archive. Drift in a
    # mutable baseline does not invalidate an intact archive or selected patch.
    import shutil
    import subprocess
    replay = state / 'final-replay'
    if frozen.get('archive'):
        from .baseline import restore
        replay = restore(frozen['archive'], replay)
    else:
        shutil.copytree(Path(frozen['workspace']), replay, symlinks=True)
    checked = capture(replay, state / 'baseline-check.patch', frozen['revision'])
    if checked['bytes'] or effects.git(replay, 'rev-parse', 'HEAD').decode().strip() != frozen['revision']:
        raise RuntimeError('Preserved starting source changed; no exact replay available')
    if submodule_state(replay) != frozen['submodules']:
        raise RuntimeError('Preserved starting dependencies changed; no exact replay available')
    immutable = selection['kind'] in ('main_candidate', 'main_returned_candidate')
    if immutable:
        initial = (state / 'run/source/source-before.patch').read_bytes()
        if (hashlib.sha256(initial).hexdigest() != frozen['source_patch_sha256'] or
                selection['selected_base'] != frozen['revision']):
            raise RuntimeError('Selected candidate does not match the preserved starting source')
        # The preserved baseline already contains the initial dirty source.
        apply = patches[1:]
    else:
        effects.git(replay, 'reset', '--hard', config['course_base'])
        effects.git(replay, 'clean', '-fd')
        apply = patches
    for patch in apply:
        if patch:
            subprocess.run(['git', '-C', str(replay), 'apply', '--binary', '-'], input=patch,
                           check=True, capture_output=True)
    if immutable and capture(replay, state / 'selected-check.patch', frozen['revision'])['sha256'] != selection['selected_sha256']:
        raise RuntimeError('Replay differs from the selected immutable candidate')
    receipt = capture(replay, state / 'candidate.patch', config['course_base'])
    confirm = capture(replay, state / 'candidate-check.patch', config['course_base'])
    if receipt['sha256'] != confirm['sha256']:
        raise RuntimeError('Replay changed during final capture')
    save(state / 'candidate.json', receipt)
    save(state / 'finalization.json', {'course_base': config['course_base'],
        'candidate_sha256': receipt['sha256'], 'replayed_sha256': confirm['sha256'],
        'selected_sha256': selection['selected_sha256'], 'selection_kind': selection['kind'],
        'reviewed': selection['reviewed'], 'exact_course_capture': True,
        'baseline_dependencies_unchanged': True, 'original_checkout_modified': False,
        'initial_patch_sha256': hashlib.sha256(patches[0]).hexdigest() if immutable else None,
        'replay_components': [hashlib.sha256(p).hexdigest() for p in patches]})


def export(state):
    archive = state / 'evidence.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for directory, dirs, files in os.walk(state, topdown=True):
            dirs[:] = sorted(d for d in dirs if d not in ('runtime', 'workspace', 'baseline', 'replay', 'final-replay', '.git', '__pycache__'))
            for name in sorted(files):
                if name in ('config.json', 'runtime.b64', 'evidence.tar.gz', 'transfer.json'):
                    continue
                path = Path(directory) / name
                if path.is_file() and not path.is_symlink():
                    tar.add(path, arcname=str(path.relative_to(state)), recursive=False)
    save(state / 'transfer.json', {'path': str(archive), 'bytes': archive.stat().st_size,
         'sha256': file_digest(archive)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('run', 'resume', 'admit', 'capture', 'export', 'finalize', 'collect', 'discard'))
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.operation in ('run', 'resume', 'admit'):
        run(config, resume=args.operation != 'run', admission=args.operation == 'admit')
    else:
        capture_ready = False
        try:
            if args.operation == 'discard':
                save(Path(config['state']) / 'owned-jobs-stop.json', stop_jobs(Path(config['state'])))
                state = Path(config['state'])
                workflow = read(state / 'run/workflow.json', {})
                ident = workflow.get('agents', {}).get('main')
                descriptor = read(state / 'run/agents/agents' / str(ident) / 'agent.json')
                if descriptor:
                    first = capture(descriptor['workspace'], state / 'discarded.patch', config['course_base'])
                    second = capture(descriptor['workspace'], state / 'discarded-check.patch', config['course_base'])
                    save(state / 'discarded-source.json', {'capture': first,
                        'stable': first['sha256'] == second['sha256'], 'submitted': False})
                save(Path(config['state']) / 'discarded.json', {
                    'author': 'course allocation controller', 'at': time.time(),
                    'meaning': 'Task not allocated implementation. Original planning work is archived; no candidate is submitted.'})
            elif args.operation in ('capture', 'finalize'):
                finalize(config)
                if args.operation == 'capture':
                    state = Path(config['state'])
                    save(state / 'candidate-transfer.json', {'files': {
                        name: {'path': str(state / name), 'bytes': (state / name).stat().st_size,
                               'sha256': file_digest(state / name)}
                        for name in ('candidate.patch', 'candidate.json', 'selection.json', 'finalization.json')}})
                    capture_ready = True
            elif args.operation == 'export':
                pass  # Existing exporter below; no capture, solver or grade replay.
            else:
                save(Path(config['state']) / 'owned-jobs-stop.json', stop_jobs(Path(config['state'])))
                raise RuntimeError('Solver worker termination unconfirmed: evidence only, no candidate capture')
        except BaseException:
            (Path(config['state']) / 'finalization-error.txt').write_text(traceback.format_exc())
            raise
        finally:
            # Preserve all original request/response/trace evidence even when
            # replay or capture fails. Config contains a scoped relay secret.
            if not capture_ready:
                Path(args.config).unlink(missing_ok=True)
                export(Path(config['state']))


if __name__ == '__main__':
    main()
