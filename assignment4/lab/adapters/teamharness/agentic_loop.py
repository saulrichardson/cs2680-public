"""Course lifecycle around the frozen Python team, with one shared task deadline."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import tarfile
import tempfile
import time
import traceback
import threading
import sys

from .backend import ModelRelay, Remote
from .bridge import Workspace
from .course_feedback import CourseGrades


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def usage(archive, logger, model):
    count = 0
    accounting = []
    for path in sorted((archive / 'run/agents/agents').glob('*/context.sqlite3')):
        # A cutoff can leave a hot journal. Recover only a disposable copy;
        # collected database and journal bytes remain original evidence.
        with tempfile.TemporaryDirectory(prefix='team-usage-') as temp:
            copied = Path(temp) / path.name
            companions = [p for p in (path, *(Path(str(path) + suffix) for suffix in ('-journal', '-wal', '-shm'))) if p.exists()]
            for source in companions:
                shutil.copy2(source, Path(temp) / source.name)
            try:
                with sqlite3.connect(copied) as db:
                    for row in db.execute("SELECT id FROM objects WHERE kind='transport_request' ORDER BY id"):
                        response = db.execute("SELECT o.body FROM objects o JOIN links l ON l.source=o.id WHERE o.kind='transport_response' AND l.relation='transport_request' AND l.target=? ORDER BY o.id DESC LIMIT 1", (row[0],)).fetchone()
                        body = json.loads(response[0]) if response else {}
                        tokens = (body.get('response') or {}).get('usage') or {}
                        count += 1
                        logger.api_request(count, tokens.get('prompt_tokens'), tokens.get('completion_tokens'),
                                           tokens.get('total_tokens'), model)
                accounting.append({'agent': path.parent.name, 'state': 'read_from_disposable_copy',
                                   'sidecars': [p.name for p in companions[1:]]})
            except sqlite3.DatabaseError as error:
                accounting.append({'agent': path.parent.name, 'state': 'accounting_unavailable', 'error': str(error)})
    save(archive / 'usage-accounting.json', {'recorded_requests': count, 'agents': accounting})
    return count


class Agent:
    def __init__(self, dispatcher):
        self.dispatcher = dispatcher
        self.run_started_at = time.time()
        self.active_tasks = set()
        self.facts_lock = threading.Lock()
        self.model_scheduler = None

    def runtime_facts(self):
        with self.facts_lock:
            now = time.time()
            elapsed = max(0, now - self.run_started_at)
            facts = {'available': True, 'supplier': 'course agent process',
                    'observed_at': now, 'run_started_at': self.run_started_at,
                    'elapsed_seconds': elapsed, 'active_tasks': len(self.active_tasks),
                    'scope': 'Tasks and elapsed time owned by this course agent process; platform owns run enforcement.'}
            return facts

    def run(self):
        config = json.loads((Path(__file__).parent / 'campaign.json').read_text())
        if config.get('model_slots') is not None:
            from .request_scheduler import RequestScheduler
            self.model_scheduler = RequestScheduler(os.environ['CS2680_BASE_URL'], os.environ['CS2680_API_KEY'],
                config['model_slots'], Path('madsServe_logs/model-scheduler').resolve())
        try:
            if 'allocation' in config:
                return self.allocate(config)
            with ThreadPoolExecutor(max_workers=config['workers']) as pool:
                for future in [pool.submit(self.worker, config) for _ in range(config['workers'])]:
                    future.result()
        finally:
            if self.model_scheduler is not None:
                # Retire queued work without delaying course collection. Sent
                # requests remain held and observable; only the enclosing server
                # lifecycle can resolve cancellation after this agent exits.
                retirement = self.model_scheduler.close(wait=False)
                save(Path('madsServe_logs/model-scheduler/retirement.json'), retirement)
                save(Path('madsServe_logs/model-scheduler/final.json'), self.model_scheduler.snapshot())

    def worker(self, config):
        from dispatcher import DispatcherShutdown
        try:
            while True:
                task = self.dispatcher.next_task()
                if task is None:
                    return
                self.solve(task, config)
        except DispatcherShutdown:
            return

    def solve(self, task, config):
        if config.get('workflow') != 'main':
            raise ValueError('This package runs only the main workflow; historical campaigns require their frozen runtime')
        attempt = TaskAttempt(self, task, config)
        try:
            if attempt.prepare():
                attempt.execute('run')
            attempt.finish()
        except BaseException:
            (attempt.archive / 'adapter-error.txt').write_text(traceback.format_exc())
            raise
        finally:
            attempt.close()

    def allocate(self, config):
        """Acquire once, plan with a bounded pool, partition once, then resume owners.

        The directory is created before requesting tasks. Re-entry refuses rather
        than replaying an uncertain dispatcher/worker mutation. Per-task receipts
        remain available for operator-led recovery and original course shutdown.
        """
        from dispatcher import DispatcherShutdown
        from src.teamharness.allocation import fingerprint, select
        options = config['allocation']
        target_fields = [key for key in ('count', 'skip_count') if key in options]
        if (config.get('workflow') != 'main'
                or not config.get('planning_boundary', {}).get('pause_after_planning')
                or type(options.get('planning_workers')) is not int or options['planning_workers'] < 1
                or len(target_fields) != 1
                or type(options.get(target_fields[0])) is not int or options[target_fields[0]] < 0):
            raise ValueError('Allocation requires paused planning, a nonnegative target and positive worker counts')
        root = Path('madsServe_logs/allocation').resolve()
        root.mkdir(parents=True, exist_ok=False)
        save(root / 'policy.json', options)
        attempts, records, failures = {}, [], []
        try:
            with (root / 'opened.jsonl').open('x') as journal:
                while True:
                    task = self.dispatcher.next_task()
                    if task is None:
                        break
                    ident = task['task']
                    if ident in attempts:
                        raise ValueError('Dispatcher returned a duplicate task identity')
                    journal.write(json.dumps(task) + '\n'); journal.flush()
                    attempts[ident] = TaskAttempt(self, task, config)
            def plan(attempt):
                attempt.planning_phase = 'preparation'
                if not attempt.prepare():
                    raise RuntimeError('Task sandbox unavailable; investigation did not start')
                attempt.planning_phase = 'planning'
                result = attempt.execute('run')
                if result.get('state') != 'exited' or result.get('exit_code') != 0:
                    raise RuntimeError(f"Planning worker ended with state={result.get('state')}, "
                                       f"exit_code={result.get('exit_code')}; see run-result.json and commands/")
                saved = attempt.remote.read_json(attempt.state + '/planning-result.json')
                if saved['state'] != 'planning_complete' or saved['deadline'] != attempt.deadline:
                    raise ValueError('Worker did not return a paused result with the original deadline')
                return saved
            with ThreadPoolExecutor(max_workers=options['planning_workers']) as pool:
                futures = {pool.submit(plan, attempt): attempt for attempt in attempts.values()}
                for future in as_completed(futures):
                    attempt = futures[future]
                    try:
                        saved = future.result()
                        facts = saved['planning'] if saved else {}
                        decision = facts.get('decision', {})
                        record = {'task_id': attempt.ident, 'fingerprint': fingerprint(attempt.task),
                            'eligible': bool(saved),
                            'assessment': decision.get('assessment'), 'recommendation': decision.get('decision'),
                            'planning': saved, 'archive': str(attempt.archive)}
                    except DispatcherShutdown:
                        raise
                    except Exception as error:
                        phase = attempt.planning_phase
                        attempt.planning_failure = phase + '_failed'
                        (attempt.archive / 'planning-error.txt').write_text(traceback.format_exc())
                        record = {'task_id': attempt.ident, 'fingerprint': fingerprint(attempt.task),
                            'eligible': False, 'assessment': None, 'recommendation': None,
                            'error': str(error), 'error_type': type(error).__name__, 'phase': phase,
                            'archive': str(attempt.archive)}
                        failures.append({key: record[key] for key in ('task_id', 'phase', 'error', 'error_type', 'archive')})
                        print(f"Task {attempt.ident}: {phase} failed ({type(error).__name__}); "
                              f"see {attempt.archive}/planning-error.txt", file=sys.stderr, flush=True)
                    save(attempt.archive / 'planning.json', record)
                    records.append(record)
            # Waiting consumes each original task deadline. Recheck before selection.
            for record in records:
                attempt = attempts[record['task_id']]
                if record['eligible'] and time.time() >= attempt.deadline:
                    record['eligible'] = False
                    record['ineligible_reason'] = 'campaign_cutoff'
                    attempt.planning_failure = 'campaign_cutoff'
            target = (max(0, len(attempts) - options['skip_count']) if 'skip_count' in options else options['count'])
            selection = select(records, target)
            selection['policy'] = {**options, 'opened_tasks': len(attempts), 'implementation_target': target,
                'selected_tasks': len(selection['selected']), 'unselected_tasks': len(selection['reserve']),
                'meaning': 'Existing readiness/effort ranking is unchanged. Failed or expired tasks cannot be admitted; fewer eligible tasks can leave the target unfilled.'}
            save(root / 'selection.json', selection)  # Durable before any admission.
            def solve_selected(row):
                attempt = attempts[row['task_id']]
                if time.time() < attempt.deadline:
                    attempt.execute('admit')
                else:
                    attempt.planning_failure = 'campaign_cutoff'
                    return attempt.finish(discard=True)
                return attempt.finish()
            # Every selected owner resumes now; the shared model scheduler owns
            # inference capacity. A second task queue would spend their deadlines
            # waiting for an unrelated investigation to finish.
            # Retire unselected sandboxes while selected owners work. Evidence
            # collection must not wait behind another task's solver deadline.
            with ThreadPoolExecutor(max_workers=max(1, len(attempts))) as pool:
                futures = {pool.submit(solve_selected, row): row['task_id'] for row in selection['selected']}
                futures.update({pool.submit(attempts[row['task_id']].finish, discard=True): row['task_id']
                                for row in selection['reserve']})
                for future in as_completed(futures):
                    try:
                        future.result()
                    except DispatcherShutdown:
                        raise
                    except Exception as error:
                        failures.append({'task_id': futures[future], 'error': str(error), 'error_type': type(error).__name__})
            no_usable_plans = target > 0 and not selection['selected'] and bool(failures)
            save(root / 'result.json', {'selected': [row['task_id'] for row in selection['selected']],
                'failures': failures, 'finished_at': time.time(),
                'state': 'planning_failed' if no_usable_plans else 'finished_with_failures' if failures else 'finished',
                'meaning': 'Allocation/execution receipts only; original task grades determine success.'})
            if no_usable_plans:
                raise RuntimeError('No usable planning results; see task closure outcomes in '
                                   'madsServe_logs/allocation/result.json and original per-task planning-error.txt')
        except DispatcherShutdown:
            save(root / 'shutdown.json', {'at': time.time(), 'meaning': 'Course ended the run; unfinished tasks and uncertain effects are retained.'})
        except BaseException:
            save(root / 'controller-error.json', {'at': time.time(), 'traceback': traceback.format_exc(),
                'meaning': 'No automatic restart or replay. Inspect task and command receipts.'})
            raise
        finally:
            for attempt in attempts.values():
                attempt.close()


class TaskAttempt:
    """One course task's existing transport, deadline and finalization across phases."""
    def __init__(self, owner, task, config):
        self.owner, self.dispatcher = owner, owner.dispatcher
        self.task, self.config, self.ident = task, config, task['task']
        self.archive = (Path('madsServe_logs') / str(self.ident) / 'teamharness').resolve()
        self.archive.mkdir(parents=True, exist_ok=False)
        save(self.archive / 'task.json', task)
        self.relay = self.grades = self.remote = None
        self.deadline = 0
        self.result = {}
        self.installed = False
        self.model_drain = None
        self.planning_failure = None
        with owner.facts_lock:
            owner.active_tasks.add(self.ident)

    def prepare(self):
        task, config, archive = self.task, self.config, self.archive
        if not task.get('sandbox_url'):
            (archive / 'error.txt').write_text('Task sandbox unavailable')
            return False
        state_root = Path(config.get('state_root', '/work'))
        if not state_root.is_absolute():
            raise ValueError('Task state_root must be an absolute sandbox path')
        self.state = str(state_root / f'teamharness-{self.ident}')
        workspace = Workspace(task['workdir'], self.state, task['sandbox_url'])
        self.remote = Remote(workspace, archive / 'commands')
        self.relay = ModelRelay(task['sandbox_url'], os.environ['CS2680_BASE_URL'], os.environ['CS2680_API_KEY'],
            grade_callback=(lambda request: self.grades.evaluate(request)) if config.get('grade_feedback') else None,
            facts_callback=self.owner.runtime_facts, audit_root=archive / 'course-request-adaptations',
            scheduler=self.owner.model_scheduler, task_id=str(self.ident))
        self.model = os.environ['CS2680_MODEL_STANDARD']
        python = config.get('python')
        if python is None:
            detected = self.remote.checked('if [ -f /lib/ld-musl-x86_64.so.1 ]; then printf /opt/agentpy_musl/bin/python3; else printf /opt/agentpy/bin/python3; fi')
            python = detected['output']['content'].strip()
            if python not in ('/opt/agentpy/bin/python3', '/opt/agentpy_musl/bin/python3'):
                raise RuntimeError('Portable Python detection did not return a recognized interpreter')
        save(archive / 'runtime-python.json', {'executable': python})
        worker = {'state': self.state, 'task': task, 'course_base': workspace.base, 'workflow': config['workflow'],
            'base_url': self.relay.url, 'relay_token': self.relay.token, 'model': self.model,
            'max_agents': config['max_agents'], 'deadline': 0,
            'grade_feedback': config.get('grade_feedback', True),
            'completion_gate': config.get('completion_gate', False),
            'planning_boundary': config.get('planning_boundary', False)}
        self.call = ('cd ' + shlex.quote(self.state + '/runtime') + ' && ' + shlex.quote(python) +
            ' -m src.teamharness.course_worker {operation} --config ' + shlex.quote(self.state + '/config.json'))
        self.remote.install(Path(__file__).parent / 'team-runtime.tar.gz', self.state, worker, python)
        self.deadline = time.time() + config['solve_seconds']
        worker['deadline'] = self.deadline
        if config.get('grade_feedback'):
            self.grades = CourseGrades(self.dispatcher, self.ident, archive / 'interactive-grades', self.deadline)
        self.remote.write(self.state + '/config.json', json.dumps(worker))
        self.installed = True
        save(archive / 'launch.json', {'started_at': time.time(), 'deadline': self.deadline,
            'workflow': worker['workflow'], 'solve_seconds': config['solve_seconds'],
            'max_agents': config['max_agents'], 'grade_feedback': config.get('grade_feedback', True),
            'model': self.model, 'course_base': workspace.base})
        return True

    def execute(self, operation):
        # Unique Remote.command journals an intent before dispatch and never retries.
        self.result = self.remote.command(self.call.format(operation=operation), deadline=self.deadline)
        save(self.archive / (operation + '-result.json'), self.result)
        return self.result

    def finish(self, *, discard=False):
        intent = self.archive / 'finish-intent.json'
        with intent.open('x') as stream:
            json.dump({'discard': discard, 'at': time.time()}, stream)
        self.drain_model()
        calls, patch, workflow, cutoff = 0, b'', {}, False
        submitted = False
        finalization_error = None
        critical = {}
        if self.installed:
            cutoff = bool(self.result.get('cutoff') or self.result.get('exit_code') == 124)
            save(self.archive / 'stop.json', {'deadline': self.deadline, 'cutoff': cutoff,
                'exit_code': self.result.get('exit_code'), 'state': self.result.get('state')})
            operation = ('discard' if discard else 'capture') if self.result.get('state') == 'exited' else 'collect'
            final = self.remote.command(self.call.format(operation=operation))
            save(self.archive / 'finalizer-process.json', {'operation': operation,
                'exit_code': final.get('exit_code'), 'state': final.get('state')})
            if operation == 'capture' and final.get('state') == 'exited' and final.get('exit_code') == 0:
                phase = 'candidate-transfer'
                try:
                    manifest = self.remote.read_json(self.state + '/candidate-transfer.json')
                    critical = manifest['files']
                    if set(critical) != {'candidate.patch', 'candidate.json', 'selection.json', 'finalization.json'}:
                        raise RuntimeError('Candidate transfer manifest has unexpected members')
                    copied = {}
                    for name, item in critical.items():
                        if item['path'] != self.state + '/' + name:
                            raise RuntimeError('Candidate transfer path differs from the task state')
                        copied[name] = self.remote.download(item['path'], self.archive / name, item['bytes'], item['sha256'])
                    candidate = json.loads((self.archive / 'candidate.json').read_text())
                    selection = json.loads((self.archive / 'selection.json').read_text())
                    finalized = json.loads((self.archive / 'finalization.json').read_text())
                    patch = (self.archive / 'candidate.patch').read_bytes()
                    digest = hashlib.sha256(patch).hexdigest()
                    if (candidate['sha256'] != digest or candidate['bytes'] != len(patch)
                            or candidate['base'] != self.remote.workspace.base
                            or finalized.get('course_base') != candidate['base']
                            or finalized.get('candidate_sha256') != digest
                            or finalized.get('replayed_sha256') != digest
                            or finalized.get('selected_sha256') != selection['selected_sha256']
                            or finalized.get('selection_kind') != selection['kind']
                            or finalized.get('exact_course_capture') is not True):
                        raise RuntimeError('Candidate capture and transfer identities differ')
                    save(self.archive / 'candidate-transfer-receipt.json', copied)
                    phase = 'final-submission'
                    submitted = True
                    self.submit_final(patch)
                except Exception as error:
                    # Failed capture proof or uncertain submission still allows
                    # evidence collection, but never a second submission attempt.
                    finalization_error = error
                    if phase == 'candidate-transfer':
                        critical = {}
                    save(self.archive / (phase + '-error.json'), {'error': str(error),
                        'error_type': type(error).__name__, 'replay_safe': False})
                # done() destroys the sandbox. Preserve bulk evidence first,
                # after attempting to place the exact patch in the course folder.
                exported = self.remote.command(self.call.format(operation='export'))
                save(self.archive / 'export-process.json', exported)
                if exported.get('state') != 'exited' or exported.get('exit_code') != 0:
                    raise RuntimeError('Evidence export incomplete; task not closed; see final submission receipts')
            transfer = self.remote.read_json(self.state + '/transfer.json')
            copied = self.remote.download(transfer['path'], self.archive / 'native-evidence.tar.gz', transfer['bytes'], transfer['sha256'])
            save(self.archive / 'transfer-receipt.json', copied)
            save(self.archive / 'transfer.json', transfer)
            with tarfile.open(self.archive / 'native-evidence.tar.gz') as tar:
                critical_seen = set()
                for member in tar.getmembers():
                    p = Path(member.name)
                    if p.is_absolute() or '..' in p.parts or member.issym() or member.islnk():
                        raise RuntimeError('Invalid evidence archive member')
                    name = p.as_posix()
                    if name in critical:
                        if name in critical_seen or not member.isfile():
                            raise RuntimeError('Bulk evidence has an ambiguous candidate receipt')
                        critical_seen.add(name)
                        expected = critical[name]
                        with tar.extractfile(member) as source:
                            observed = hashlib.file_digest(source, 'sha256').hexdigest()
                        if member.size != expected['bytes'] or observed != expected['sha256']:
                            raise RuntimeError('Bulk evidence differs from the submitted candidate receipts')
                if critical_seen != set(critical):
                    raise RuntimeError('Bulk evidence is missing candidate receipts')
                tar.extractall(self.archive)
            calls = usage(self.archive, self.dispatcher.logger(self.ident), self.model)
            workflow_path = self.archive / 'run/workflow.json'
            workflow = json.loads(workflow_path.read_text()) if workflow_path.exists() else {}
            if final.get('exit_code') != 0 and not discard:
                raise RuntimeError('Final candidate unavailable; original traces were collected')
            if not discard:
                candidate = json.loads((self.archive / 'candidate.json').read_text())
                patch = (self.archive / 'candidate.patch').read_bytes()
                if hashlib.sha256(patch).hexdigest() != candidate['sha256'] or len(patch) != candidate['bytes']:
                    raise RuntimeError('Candidate transport identity mismatch')
        if finalization_error is not None:
            raise finalization_error
        if not submitted:
            self.submit_final(patch)
        if discard:
            reason = self.planning_failure or 'allocation_discarded'
        elif not self.installed:
            reason = 'sandbox_unavailable'
        elif cutoff:
            reason = 'campaign_cutoff'
        elif self.result.get('exit_code') != 0:
            reason = 'agent_error'
        else:
            reason = 'completed' if workflow.get('state') == 'completed' else 'incomplete_workflow'
        save(self.archive / 'grade.json', self.dispatcher.done(self.ident, reason, calls))
        self.close()

    def submit_final(self, patch):
        identity = {'sha256': hashlib.sha256(patch).hexdigest(), 'bytes': len(patch), 'at': time.time()}
        with (self.archive / 'final-submission-intent.json').open('x') as out:
            json.dump(identity, out)
        if self.grades is not None:
            self.grades.final_submit(patch)
        else:
            self.dispatcher.submit_patch(self.ident, patch.decode('utf-8'))
        save(self.archive / 'final-submission.json', {**identity, 'finished_at': time.time(),
            'state': 'submitted_or_original_grade_retained', 'task_closed': False})

    def close(self):
        self.drain_model()
        if self.relay is not None:
            self.relay.close()
            self.relay = None
        with self.owner.facts_lock:
            self.owner.active_tasks.discard(self.ident)

    def drain_model(self):
        if self.owner.model_scheduler is None or self.relay is None or self.model_drain is not None:
            return
        # The sandbox worker/finalizer owns source and process retirement. An
        # upstream request can outlive its caller; keep its shared slot occupied
        # without blocking capture, final grading or the original task deadline.
        self.model_drain = self.owner.model_scheduler.finish_task(str(self.ident), wait=False)
        save(self.archive / 'model-drain.json', self.model_drain)
