"""A continuing investigator with optional specialists and exact grading effects.

Independent attempts during investigation are model-selected. A configured
completion gate can transfer a failed candidate to a new owner conversation.
"""
from __future__ import annotations

from contextlib import contextmanager
import codecs
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from .candidates import Grader, capture, grade_candidate, snapshot, save, sha
from .presentation import facts
from src.workspace import git


def takeover_brief(source, candidate, completion, deadline, archive):
    """Describe an already verified materialization, without interpreting failures."""
    raw = Path(candidate['patch_path']).read_bytes()
    if sha(raw) != candidate['sha256'] or len(raw) != candidate['bytes']:
        raise ValueError('Takeover briefing candidate identity changed')
    stats = (subprocess.run(['git', 'apply', '--numstat', '-z', '--binary', '-'],
                           cwd=source, input=raw, check=True, capture_output=True).stdout if raw else b'')
    files = [entry.split(b'\t', 2)[2].decode('utf-8', errors='replace')
             for entry in stats.split(b'\0') if entry]
    # numstat reports a rename's destination. Include its removed old path too;
    # Git quotes control/non-ASCII bytes with C escapes in these header lines.
    for line in raw.splitlines():
        if line.startswith(b'rename from '):
            path = line[len(b'rename from '):]
            if path.startswith(b'"') and path.endswith(b'"'):
                path = codecs.escape_decode(path[1:-1])[0]
            files.append(path.decode('utf-8', errors='replace'))
    files = sorted(set(files))
    head = git(source, 'rev-parse', 'HEAD').decode().strip()
    checks = (completion or {}).get('checks', {})
    incomplete = []
    for name, check in checks.items():
        result = check.get('result') or {}
        execution = check.get('execution') or result.get('execution') or result
        if check.get('state') in ('unknown', 'uncertain', 'checking') or execution.get('state') in (
                'unknown', 'uncertain', 'running', 'checking', 'launch_error', 'deadline_exceeded'):
            incomplete.append(name)
    counts = checks.get('configured_grader', {}).get('result', {}).get('grade', {}).get('receipt', {})
    now = time.time()
    return {'observed_at': now, 'workspace': str(source),
        'inherited_repair': 'The inherited candidate is already applied in this workspace. It is not a patch waiting to be applied.',
        'candidate_sha256': candidate['sha256'], 'patch_bytes': len(raw), 'changed_files': files,
        'head': head, 'capture_base': candidate['base'],
        'head_relationship': ('HEAD identifies the preserved starting source. Restoring changed files from HEAD removes the inherited repair in those files.'
            if head == candidate['base'] else 'HEAD differs from the capture base. Inspect the recorded revisions before restoring files.'),
        'failed_completion_checks': [name for name, check in checks.items() if check.get('passed') is False],
        'incomplete_completion_checks': incomplete,
        'grade': ({'tests_failed': counts['tests_failed'], 'tests_total': counts['tests_total'],
                   'meaning': 'Counts for the inherited candidate; they do not identify the failing cases or cause.'}
                  if all(type(counts.get(k)) is int for k in ('tests_failed', 'tests_total')) else
                  {'available': False, 'meaning': 'No count-bearing configured-grader receipt in this completion.'}),
        'deadline': deadline, 'remaining_seconds_at_observation': max(0, deadline-now) if deadline is not None else None,
        'preserved_candidate_patch': candidate['patch_path'], 'complete_handoff_record': str(archive)}


class Investigation:
    def __init__(self, manager, root: Path, feedback_grader=None, completion_gate=None, evidence_environment=None,
                 planning_boundary=None):
        self._report_lock = threading.RLock()
        self._grade_lock = threading.RLock()
        self._dispatch_lock = threading.RLock()
        self.local_grader = None
        self.manager = manager
        self.feedback_grader = feedback_grader
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.report = {'version': 3, 'stages': [], 'agents': {}, 'state': 'created'}
        path = self.root / 'workflow.json'
        if path.exists():
            self.report = json.loads(path.read_text())
            if evidence_environment is not None and evidence_environment != self.report.get('evidence_environment'):
                raise ValueError('Resume requires the recorded evidence environment; old grades cannot acquire a new scope')
            if self.report.get('workflow') != 'main':
                raise ValueError('Recorded workflow is not a main investigation; use its frozen runtime')
            saved = self.report.get('grader')
            if saved:
                self.local_grader = Grader(tuple(saved['argv']), saved['name'], saved['official'])
            if bool(feedback_grader) != self.report['feedback_enabled']:
                raise ValueError('Resume requires the original course grading capability')
            from .completion import policy
            saved_policy = self.report.get('completion_gate', policy(False))
            if completion_gate is not None and policy(completion_gate) != saved_policy:
                raise ValueError('Resume requires the recorded completion gate')
            from .planning import policy as planning_policy
            if planning_boundary is not None and planning_policy(planning_boundary) != self.report.get('planning_boundary', planning_policy(False)):
                raise ValueError('Resume requires the recorded planning boundary')
        else:
            from .completion import policy
            self.report.update(version=3, workflow='main', evaluations={}, reviews={},
                               interactive_grades={}, automatic_review={'enabled': False},
                               completion_gate=policy(completion_gate), completion_attempts={})
            from .planning import policy as planning_policy
            self.report['planning_boundary'] = planning_policy(planning_boundary)
            self.report['evidence_environment'] = json.loads(json.dumps(evidence_environment)) if evidence_environment is not None else {
                'available': False, 'meaning': 'No complete environment identity supplied. External dependencies, caches and services are not established by the candidate hash.'}
        manager.workflow = self

    @contextmanager
    def owner(self):
        with (self.root / 'workflow.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('An existing controller owns this investigation') from None
            yield

    def persist(self) -> None:
        """Keep exact prior projections; original session evidence is never rewritten."""
        path = self.root / 'workflow.json'
        if path.exists():
            raw = path.read_bytes()
            revisions = self.root / 'workflow-revisions'
            revisions.mkdir(exist_ok=True)
            old = revisions / (sha(raw) + '.json')
            if not old.exists():
                with old.open('xb') as stream:
                    stream.write(raw)
        save(path, self.report)

    def check_deadline(self) -> None:
        deadline = self.report.get('task_deadline_unix')
        if deadline is not None and time.time() >= deadline:
            raise RuntimeError('Original shared task deadline has expired; continuation cannot renew it')

    def start(self, ident: str, label: str) -> None:
        self.check_deadline()
        started = self.manager.start(ident)
        if isinstance(started, dict) and started.get('start') == 'capacity_unavailable':
            raise RuntimeError(f'No available agent slot for {label}; agent {ident} remains recorded')

    def starting_workspace(self) -> Path:
        """Observe drift and restore verified starting bytes without erasing it."""
        frozen = self.report['frozen']
        workspace = Path(self.report.get('baseline_materialization', frozen['workspace']))
        checks = self.root / 'baseline-checks'
        checks.mkdir(exist_ok=True)
        observed = None
        try:
            observed = capture(workspace, checks / (uuid.uuid4().hex + '.patch'), frozen['revision'])
            clean = (not observed['bytes'] and
                     git(workspace, 'rev-parse', 'HEAD').decode().strip() == frozen['revision'])
        except (OSError, RuntimeError, ValueError) as error:
            clean = False
            observed = {'error_type': type(error).__name__, 'error': str(error)}
        if clean:
            return workspace
        self.record('baseline_integrity_failure', workspace=str(workspace), observed=observed)
        archive = frozen.get('archive')
        if archive is None:
            raise RuntimeError('Frozen baseline was modified; no verified archive exists for restoration')
        from .baseline import restore
        recovered = restore(archive, self.root / 'baseline-materializations' / uuid.uuid4().hex)
        checked = capture(recovered, checks / (uuid.uuid4().hex + '.patch'), frozen['revision'])
        if checked['bytes'] or git(recovered, 'rev-parse', 'HEAD').decode().strip() != frozen['revision']:
            raise RuntimeError('Restored baseline does not match the recorded starting revision')
        self.report['baseline_materialization'] = str(recovered)
        self.record('baseline_restored', contaminated_workspace=str(workspace),
                    workspace=str(recovered), archive=archive, verified_candidate=checked,
                    meaning='Starting bytes restored into a new directory; altered source retained, not adopted or erased.')
        return recovered

    @property
    def grading_available(self):
        return self.local_grader is not None or self.feedback_grader is not None

    def evidence_scope(self):
        definition = self.report.get('grader')
        return {'grader_definition_sha256': sha(json.dumps(definition, sort_keys=True).encode()) if definition else None,
                'grader_definition_scope': 'Recorded argv/name/authority; executable and external service bytes are not identified here.' if definition else 'External grader implementation identity unavailable.',
                'environment': json.loads(json.dumps(self.report.get('evidence_environment', {'available': False}))),
                'meaning': 'A reused receipt is the earlier observation under this recorded scope, not a new execution or proof about unrecorded environment changes.'}

    def record(self, stage, **facts):
        with self._report_lock:
            self.report['stages'].append({'stage': stage, 'at': time.time(), **facts})
            self.persist()

    def run(self, task: str, workspace: Path, grader: Grader | None = None):
        with self.owner():
            if (self.root / 'workflow.json').exists():
                raise ValueError('Existing investigation is continued explicitly; never start a replacement main')
            self.local_grader = grader
            frozen = snapshot(Path(workspace), self.root / 'baseline', self.root / 'source')
            from .baseline import freeze
            frozen['archive'] = freeze(Path(frozen['workspace']), self.root / 'source/baseline.tar')
            self.report.update(state='running', task=task, frozen=frozen,
                feedback_enabled=self.feedback_grader is not None, task_deadline_unix=self.manager.deadline,
                grader={'argv': list(grader.argv), 'name': grader.name, 'official': grader.official} if grader else None)
            self.record('investigation_created')
            from .workspaces import prepare_live_workspace
            live = prepare_live_workspace(workspace, frozen, self.root / 'source/live')
            ident = self.manager.create('main', task, Path(live['workspace']), private=False,
                                        public_task=task, capture_base=live['base'],
                                        working_directory=Path(live['working_directory']))
            self.report['agents']['main'] = ident
            self.record('main_created', agent_id=ident)
            return self.advance()

    def resume(self, grader: Grader | None = None, message: str | None = None):
        with self.owner():
            if not self.report.get('agents', {}).get('main'):
                raise ValueError('No recorded main investigation exists')
            if grader is not None and grader != self.local_grader:
                raise ValueError('Resume cannot change the original grader')
            if self.report['task_deadline_unix'] != self.manager.deadline:
                raise ValueError('Resume cannot renew the original task deadline')
            self.check_deadline()
            if self.report.get('handoff', {}).get('state') == 'pending':
                if self.report['handoff'].get('kind') == 'completion_gate':
                    self._complete_handoff()
                    return self.advance()
                previous = self.report['handoff']['previous_main']
                self.manager.stop(previous, identity='handoff-recovery-' + self.report['handoff']['candidate']['sha256'], sender='runtime')
                observed = self.manager.inspect(previous)
                if observed['status'] not in ('running', 'stopped'):
                    self.manager.start(previous)
                self.manager.wait(previous)
                self._complete_handoff()
                return self.advance()
            ident = self.report['agents']['main']
            if (message is None and self.report.get('completion_gate', {}).get('enabled')
                    and self.report.get('state') != 'completed'
                    and self.manager.inspect(ident)['status'] == 'completed'):
                return self.advance(returned=self.manager.inspect(ident))
            if not Path(self.manager.descriptor(ident)['workspace']).is_dir():
                raise ValueError('Main workspace is missing; exact restoration is required before continuation')
            if message is not None:
                self.manager.send(ident, message)
            else:
                self.manager.observation(ident, {'event': 'operator_continuation',
                    'meaning': 'Explicit continuation of the same investigation; existing effects are not replayed.'},
                    uuid.uuid4().hex, resume=True)
            self.record('main_continuation_requested', agent_id=ident)
            self.dispatch_reviews()
            self.reconcile_returns()
            return self.advance()

    def admit(self, reason: str):
        """Allocate further work to the same paused owner, without renewing limits."""
        from . import planning
        with self.owner():
            if not self.report.get('planning_boundary', {}).get('pause_after_planning'):
                raise ValueError('This investigation has no external allocation boundary')
            previous = self.report.get('allocation')
            if previous and self.report.get('state') != 'planning_complete':
                if previous['reason'] != reason:
                    raise ValueError('Admission identity reused with a different reason')
                return dict(self.report)
            self.check_deadline()
            ident = self.report['agents']['main']
            root = Path(self.manager.descriptor(ident)['root'])
            returned = self.manager.inspect(ident)
            if returned.get('status') != 'planning_complete':
                # Re-observe a completed admission rather than repeating its work.
                if (root / 'planning-admission.json').exists():
                    previous = json.loads((root / 'planning-admission.json').read_text())
                    if previous['reason'] != reason:
                        raise ValueError('Admission identity reused with a different reason')
                    return dict(self.report)
                raise ValueError('Only a settled planning result can be admitted')
            admission = planning.admit(root, returned['planning'], reason)
            self.report['allocation'] = {'agent_id': ident, **admission}
            self.record('planning_admitted', agent_id=ident, admission=admission)
            self.manager.observation(ident, {'event': 'planning_admitted', 'admission': admission,
                'meaning': 'Continue this existing investigation. The allocation does not override the model assessment or establish a feasible repair.'},
                'planning-admission', resume=True)
            return self.advance()

    def advance(self, returned=None):
        if (self.report.get('handoff', {}).get('state') == 'pending'
                and self.report['handoff'].get('kind') == 'completion_gate'):
            self._complete_handoff()
            returned = None
        ident = self.report['agents']['main']
        try:
            with self._report_lock:
                self.report.update(state='running')
            if returned is None:
                self.record('main_started', agent_id=ident)
                self.start(ident, 'main')
                returned = self.manager.wait(ident)
            with self._report_lock:
                self.report = json.loads((self.root / 'workflow.json').read_text())
            if self.report.get('handoff', {}).get('state') == 'pending':
                if returned.get('status') not in ('stopped', 'completed'):
                    raise RuntimeError('Previous investigator did not reach a stopped boundary; handoff withheld')
                self._complete_handoff()
                return self.advance()
            if returned.get('status') in ('skipped', 'planning_timeout', 'planning_complete'):
                self.report.update(state=returned['status'], main_return=returned,
                                   outcome=returned.get('outcome'), conclusion=returned.get('report'))
                self.report.pop('candidate', None)
                self.record('planning_ended_without_implementation', agent_id=ident,
                            planning=returned.get('planning'), meaning='No completion acceptance or automatic takeover.')
                return dict(self.report)
            self.dispatch_reviews()
            if returned.get('status') == 'completed' and self.report.get('completion_gate', {}).get('enabled'):
                gate = self.verify_completion(ident, returned)
                if gate['state'] != 'passed':
                    if not gate['checks']['candidate_identity']['passed']:
                        self.report.update(state='completion_blocked', main_return=returned)
                        self.record('completion_cannot_handoff', completion=gate)
                        return dict(self.report)
                    from .completion import confirmed_grade_failure
                    previous_handoff = self._grade_handoff(gate)
                    if self.report['completion_gate'].get('version') == 2 and (previous_handoff or not confirmed_grade_failure(gate)):
                        self.report.pop('candidate', None)
                        self.report.update(state='completion_recovery', main_return=returned)
                        # Freeze notification contents before delivery. Resume can observe
                        # the same completion again without changing its control identity.
                        notification = gate.setdefault('recovery_observation', {'event': 'completion_needs_work',
                            'completion_id': gate['id'], 'checks': json.loads(json.dumps(gate['checks'])),
                            'receipt_path': str(Path(gate['evidence_root']) / 'receipt.json'),
                            'previous_grade_handoff': previous_handoff,
                            'meaning': ('This recorded failing grade already transferred ownership. ' if previous_handoff else '') +
                                       'Completion was not accepted. You remain owner. Inspect the failed or incomplete checks, '
                                       'correct execution setup or repair the candidate, and request completion again. '
                                       'Uncertain effects may have executed: observe their files/processes/receipts before a new explicit action. '
                                       'Do not replay a lost effect merely because its result is missing.'})
                        self.record('completion_returned_to_owner', agent_id=ident, completion_id=gate['id'])
                        self.manager.observation(ident, notification, 'completion-recovery-' + gate['id'], resume=True)
                        with self.manager._locked():
                            (Path(self.manager.descriptor(ident)['root']) / 'completion-closed.json').unlink(missing_ok=True)
                        return self.advance()
                    self.report['handoff'] = {'kind': 'completion_gate', 'state': 'pending',
                        'previous_main': ident, 'candidate': gate['candidate'],
                        'completion_id': gate['id'], 'completion': gate}
                    self.report.pop('candidate', None)
                    self.record('completion_handoff_requested', agent_id=ident, completion_id=gate['id'])
                    self._complete_handoff()
                    return self.advance()
            with self._report_lock:
                self.report['main_return'] = returned
                if returned.get('status') == 'completed':
                    self.report.update(state='completed', candidate=returned.get('patch'),
                                       conclusion=returned.get('report'), outcome=returned.get('outcome'))
                    self.record('investigation_returned', agent_id=ident, candidate=returned.get('patch'),
                                meaning='The model ended its investigation; this is not a test, review or grading verdict.')
                else:
                    self.report['state'] = returned.get('status', 'interrupted')
                    self.record('main_needs_attention', receipt=returned)
            return dict(self.report)
        except Exception as error:
            with self._report_lock:
                self.report.update(state='interrupted', error=str(error), error_type=type(error).__name__)
                self.record('investigation_interrupted')
            raise

    def status(self):
        with self._report_lock:
            return {'main_agent_id': self.report.get('agents', {}).get('main'),
                    'latest_evaluation': self.report.get('latest_evaluation'),
                    'evaluations': [{k: v.get(k) for k in ('state', 'candidate', 'result', 'review_agent_id')}
                                    for v in self.report['evaluations'].values()],
                    'automatic_review': self.report['automatic_review'],
                    'completion_gate': self.report.get('completion_gate', {'enabled': False}),
                    'latest_completion': self.report.get('completion_attempts', {}).get(self.report.get('latest_completion')),
                    'meaning': ('Each recorded failing completion grade transfers exact source to the review-focused owner once; repeating that grade or other verification failures returns feedback to the current owner. Interactive grades leave ownership unchanged.'
                        if self.report.get('completion_gate', {}).get('version') == 2 and self.report['completion_gate']['enabled'] else
                        'A failed completion check transfers the exact candidate to a new main conversation; interactive grading alone does not transfer ownership.'
                        if self.report.get('completion_gate', {}).get('enabled') else
                        'Independent attempts are model-selected; failed grades do not transfer ownership.')}

    def evaluate(self, agent_id: str, effect_id: str, checkpoint_id: str | None = None):
        if agent_id != self.report['agents']['main'] or not self.grading_available:
            return {'executed': False, 'error': 'No grading capability is attached to this main investigator'}
        from .tools import ToolRuntime
        descriptor = self.manager.descriptor(agent_id)
        if checkpoint_id is None:
            checkpoint = self.manager.checkpoint(agent_id, effect_id, 'Candidate selected for evaluation')
            if not checkpoint.get('captured'):
                return {'evaluation_started': False, 'checkpoint': checkpoint}
            checkpoint_id = checkpoint['checkpoint_id']
        candidate = ToolRuntime(self.manager, agent_id, descriptor).checkpoint_candidate(checkpoint_id)
        return self._evaluate_captured(candidate, effect_id, checkpoint_id)

    def _evaluate_captured(self, candidate, effect_id, checkpoint_id=None):
        key = candidate['sha256']
        with self._grade_lock:
            with self._report_lock:
                self.report = json.loads((self.root / 'workflow.json').read_text())
            item = self.report['evaluations'].get(key)
            if item is None:
                self.check_deadline()
                item = {'state': 'pending', 'candidate': candidate, 'checkpoint_id': checkpoint_id,
                        'effect_id': effect_id, 'started_at': time.time(), 'evidence_scope': self.evidence_scope()}
                with self._report_lock:
                    self.report['evaluations'][key] = item
                    self.record('evaluation_intent', candidate=candidate, checkpoint_id=checkpoint_id)
                try:
                    result = self._evaluate(candidate, key)
                    with self._report_lock:
                        item.update(state='received', result=result)
                        self.report['interactive_grades'][key] = {'state': 'received', 'candidate': candidate, 'result': result}
                        self.report['latest_evaluation'] = {'candidate_sha256': key, 'result': result}
                        self.record('evaluation_received', candidate=candidate, result=result)
                except Exception as error:
                    with self._report_lock:
                        item.update(state='unknown', error_type=type(error).__name__, error=str(error))
                        self.record('evaluation_uncertain', candidate=candidate, error=str(error))
            if item['state'] != 'received':
                return {'evaluation_started': False, 'grading_state': item['state'],
                        'candidate': candidate, 'checkpoint_id': checkpoint_id,
                        'error': item.get('error'), 'replay_safe': False,
                        'meaning': 'A prior grading effect has no valid completed receipt. Continue investigating; do not replay it.'}
            receipt = item['result'].get('receipt', {})
            failed, total = receipt.get('tests_failed'), receipt.get('tests_total')
            previous = None
            for other_key, other in self.report['evaluations'].items():
                if other_key == key:
                    break
                counts = other.get('result', {}).get('receipt', {})
                if other.get('state') == 'received' and type(counts.get('tests_failed')) is int and type(counts.get('tests_total')) is int:
                    previous = {'candidate_sha256': other_key, **counts}
            supplier = self.local_grader.name if self.local_grader else 'course grader'
            grade_facts = {'supplier': supplier, 'candidate_sha256': key,
                'tests_failed': failed, 'tests_total': total, 'previous_candidate': previous,
                'trend': (f"{previous['tests_failed']}/{previous['tests_total']} failing → {failed}/{total} failing"
                          if previous and type(failed) is int and type(total) is int else 'No comparable previous counts')}
            return {'grading_state': 'received', 'candidate': candidate, 'checkpoint_id': checkpoint_id,
                    'evidence_scope': item.get('evidence_scope', {'environment': {'available': False},
                        'meaning': 'Historical receipt has no recorded environment identity.'}),
                    'grade': item['result'], 'grade_facts': grade_facts, 'reused': item['effect_id'] != effect_id,
                    'review_agent_id': None, 'review_error': None,
                    'handoff_requested': self.report.get('handoff', {}).get('state') == 'pending',
                    'meaning': 'Counts describe these captured bytes. The investigator chooses whether to continue, start an independent attempt or finish.'}

    def verify_completion(self, ident, returned):
        from .completion import candidate_identity, replay_check, command_passed
        state = self.manager._session(ident)._load()
        key = hashlib.sha256(f"{ident}:{state['epoch']}:{state['assistant_counter']}".encode()).hexdigest()
        previous = self.report.get('completion_attempts', {}).get(key)
        if previous is not None:
            identity = candidate_identity(previous.get('candidate'), self.report['frozen']['revision'])
            previous.setdefault('checks', {})['candidate_identity'] = identity
            if not identity['passed']:
                previous['state'] = 'failed'
            if previous['state'] == 'checking':
                previous.update(state='failed', uncertainty='Prior completion checks were interrupted; effects are not replayed.')
                self.record('completion_check_uncertain', completion_id=key)
            self.persist()
            return previous
        candidate = returned.get('patch')
        item = {'id': key, 'agent_id': ident, 'state': 'checking', 'candidate': candidate,
                'started_at': time.time(), 'checks': {}, 'evidence_root': str(self.root / 'completion' / key)}
        self.report.setdefault('completion_attempts', {})[key] = item
        self.report.update(state='checking_completion', latest_completion=key)
        self.record('completion_check_started', completion_id=key, candidate=candidate)
        root = self.root / 'completion' / key
        root.mkdir(parents=True, exist_ok=True)
        def persist():
            self.report['completion_attempts'][key] = item
            save(root / 'receipt.json', item)
            self.persist()
        def check(name, passed, **facts):
            item['checks'][name] = {'passed': passed, **facts}
            persist()
        identity = candidate_identity(candidate, self.report['frozen']['revision'])
        item['checks']['candidate_identity'] = identity
        persist()
        if identity['passed']:
            raw = Path(candidate['patch_path']).read_bytes()
            saved = root / 'candidate.patch'
            with saved.open('xb') as stream:
                stream.write(raw)
            item['candidate'] = candidate = {**candidate, 'patch_path': str(saved)}
            check('nonempty_solver_diff', bool(raw) or not self.report['completion_gate']['require_diff'],
                  bytes=len(raw), compared_to='preserved task starting source, including initial dirty files')
            if item['checks']['nonempty_solver_diff']['passed']:
                try:
                    replay = replay_check(self.starting_workspace(), candidate['base'], candidate, root / 'replay-check')
                    check('original_source_replay', command_passed(replay), execution=replay)
                    if command_passed(replay):
                        for index, command in enumerate(self.report['completion_gate']['checks']):
                            self.check_deadline()
                            result = grade_candidate(self.starting_workspace(), candidate['base'], candidate,
                                Grader(tuple(command['argv']), command['name']), root / f'check-{index}', deadline=self.manager.deadline)
                            check('command:' + command['name'], command_passed(result), execution=result)
                        from . import repair
                        agent_root = Path(self.manager.descriptor(ident)['root'])
                        plan = repair.current(agent_root)
                        if plan is not None and (plan['checks'] or self.report['completion_gate'].get('version') != 2):
                            # Reuse only a finished receipt for this plan and exact patch.
                            prior = []
                            uncertain = []
                            paths = list((agent_root / 'test-plan-runs').glob('*/receipt.json'))
                            # Older gates wrote outside the agent's plan-run journal.
                            # Discover their receipts without migrating or replaying effects.
                            for completion in self.report.get('completion_attempts', {}).values():
                                if completion['agent_id'] == ident:
                                    legacy = self.root / 'completion' / completion['id'] / 'test-plan' / 'receipt.json'
                                    if legacy.exists():
                                        paths.append(legacy)
                            for path in sorted(paths):
                                observed = json.loads(path.read_text())
                                if (observed.get('target') == 'candidate'
                                        and observed.get('plan_id') == plan['id']
                                        and observed.get('candidate', {}).get('sha256') == candidate['sha256']
                                        and observed.get('candidate', {}).get('base') == candidate['base']):
                                    (prior if observed.get('state') == 'finished' else uncertain).append(observed)
                            tests = (max(prior, key=lambda r: r['finished_at']) if prior else
                                     {'passed': False, 'state': 'uncertain', 'previous_runs': uncertain, 'replay_safe': False}
                                     if uncertain else repair.run(self, ident, plan, candidate,
                                         agent_root / 'test-plan-runs' / ('completion-' + key)))
                            check('executable_test_plan', tests.get('passed') is True, result=tests)
                        if plan is not None and not plan['checks'] and self.report['completion_gate'].get('version') == 2:
                            item['verification_gap'] = 'No model-defined executable checks; the prose plan is not test evidence.'
                        if self.grading_available:
                            graded = self._evaluate_captured(candidate, 'completion:' + key)
                            result = graded.get('grade', {})
                            execution = result.get('execution')
                            check('configured_grader', result.get('passed') is True and
                                  (execution is None or command_passed(execution)), result=graded)
                        else:
                            item['grading'] = {'available': False, 'meaning': 'No grader attached; no test-success claim is made.'}
                except Exception as error:
                    check('verification_effect', False, state='uncertain', error=str(error),
                          error_type=type(error).__name__, replay_safe=False)
            # Verification commands and other trusted processes may touch files;
            # acceptance still refers to the exact saved and returned artifacts.
            final_identity = candidate_identity(candidate, self.report['frozen']['revision'])
            returned_identity = candidate_identity(returned.get('patch'), self.report['frozen']['revision'])
            item['checks']['candidate_identity'] = {**final_identity,
                'passed': final_identity['passed'] and returned_identity['passed'],
                'returned_artifact': returned_identity}
        item.update(state='passed' if all(c['passed'] for c in item['checks'].values()) else 'failed',
                    finished_at=time.time())
        persist()
        self.record('completion_check_finished', completion_id=key, result=item)
        return item

    def _grade_handoff(self, gate):
        """Observe whether this exact failing grade already transferred ownership."""
        from .completion import confirmed_grade_failure
        if not confirmed_grade_failure(gate):
            return None
        grade = gate['checks']['configured_grader']['result']['grade']
        for handoff in self.report.get('handoff_history', []):
            previous = handoff.get('completion', {})
            if (handoff.get('kind') == 'completion_gate' and handoff.get('state') == 'completed'
                    and all(handoff.get('candidate', {}).get(k) == gate['candidate'][k] for k in ('base', 'sha256'))
                    and previous.get('checks', {}).get('configured_grader', {}).get('result', {}).get('grade') == grade):
                return {k: handoff[k] for k in ('completion_id', 'previous_main', 'new_main')}
        return None

    def _complete_handoff(self):
        handoff = self.report['handoff']
        old = handoff['previous_main']
        if self.manager.inspect(old)['status'] not in ('completed', 'stopped'):
            raise RuntimeError('Previous investigator has not returned; handoff withheld')
        candidate = handoff['candidate']
        token = handoff.get('completion_id', candidate['sha256'])
        folder = self.root / 'takeover' / token
        source = folder / 'workspace'
        ident = hashlib.sha256(('completion-takeover:' + old + ':' + token).encode()).hexdigest()[:32]
        raw = Path(candidate['patch_path']).read_bytes()
        if sha(raw) != candidate['sha256'] or len(raw) != candidate['bytes']:
            raise ValueError('Failed candidate bytes changed before handoff')
        folder.mkdir(parents=True, exist_ok=True)
        observed = None
        if source.exists() or source.is_symlink():
            try:
                observed = capture(source, folder / ('observed-' + uuid.uuid4().hex + '.patch'), candidate['base'])
            except (OSError, RuntimeError, ValueError) as error:
                observed = {'error_type': type(error).__name__, 'error': str(error)}
            if observed.get('sha256') != candidate['sha256']:
                # Older preparation wrote directly to workspace. Preserve an
                # incomplete copy, but never replace an already assigned source.
                if (self.manager.root / 'agents' / ident / 'agent.json').exists():
                    raise ValueError('Takeover source differs from the exact failed candidate')
                preserved = folder / ('incomplete-' + uuid.uuid4().hex)
                source.rename(preserved)
                self.record('handoff_preparation_incomplete', preserved_workspace=str(preserved), observed=observed)
                observed = None
        if observed is None:
            staging = folder / ('preparing-' + uuid.uuid4().hex)
            shutil.copytree(self.starting_workspace(), staging, symlinks=True)
            if raw:
                subprocess.run(['git', '-C', str(staging), 'apply', '--binary', '-'],
                               input=raw, check=True, capture_output=True)
            observed = capture(staging, folder / 'starting.patch', candidate['base'])
            if observed['sha256'] != candidate['sha256']:
                raise ValueError('Takeover source differs from the exact failed candidate')
            staging.rename(source)
        humans = self.manager._session(old).task_context()['human_inputs']
        handoff_record = {'version': 2, 'kind': 'completion_gate_handoff',
            'candidate': candidate, 'completion': handoff.get('completion'),
            'previous_investigator': {'agent_id': old,
                'archive': self.manager.descriptor(old)['root'],
                'meaning': 'Prior model interpretations remain available by reference, not inherited as conversation history.'}}
        from .repair_prompts import FAILED_COMPLETION_TAKEOVER
        handoff_path = folder / 'handoff.json'
        if handoff_path.exists():
            saved = json.loads(handoff_path.read_text())
            if saved.get('candidate') != candidate:
                raise ValueError('Persisted handoff identifies a different candidate')
            # A partially created owner resumes its original assignment, including
            # its dated brief. Never regenerate its clock or replace old prompts.
            descriptor = self.manager.root / 'agents' / ident / 'agent.json'
            assignment = saved.get('assignment') or (json.loads(descriptor.read_text())['task'] if descriptor.exists() else
                'ORIGINAL PUBLIC TASK (human requirement)\n' + self.report['task'] +
                '\n\nCOMPLETION HANDOFF (runtime observations)\n' + facts(saved) +
                '\n\nNEW OWNER ASSIGNMENT (authored diagnosis-and-repair guidance)\n' + FAILED_COMPLETION_TAKEOVER)
        else:
            from .presentation import takeover_observation
            from .repair import current
            brief = takeover_brief(source, candidate, handoff.get('completion'), self.manager.deadline, handoff_path)
            prior = current(Path(self.manager.descriptor(old)['root']))
            if prior:
                brief['previous_check_definitions'] = {'author': 'previous investigator model',
                    'checks': prior['checks'], 'plan_path': str(Path(self.manager.descriptor(old)['root']) / 'repair-plans' / (prior['id'] + '.json')),
                    'meaning': 'Available definitions, not a diagnosis or proof that these checks are sufficient. Choose or revise checks independently.'}
            handoff_record['brief'] = brief
            assignment = ('TAKEOVER BRIEF (dated runtime observations)\n' + takeover_observation(brief) +
                '\n\nORIGINAL PUBLIC TASK (human requirement)\n' + self.report['task'] +
                '\n\nCOMPLETION HANDOFF (runtime observations)\nFull original receipt: ' + str(handoff_path) +
                '\nPrevious investigator archive: ' + handoff_record['previous_investigator']['archive'] +
                '\n\nNEW OWNER ASSIGNMENT (authored diagnosis-and-repair guidance)\n' + FAILED_COMPLETION_TAKEOVER)
            handoff_record['assignment'] = assignment
            save(handoff_path, handoff_record)
        self.manager.create('main', assignment, source, private=False,
                            identity=ident, public_task=self.report['task'], capture_base=candidate['base'],
                            human_inputs=humans,
                            review_takeover=self.report['completion_gate'].get('version') == 2,
                            working_directory=source / Path(self.report['frozen'].get(
                                'working_directory', self.report['frozen']['source'])).relative_to(
                                    self.report['frozen']['source']))
        self.report['agents'].setdefault('original_main', old)
        self.report['agents']['main'] = ident
        handoff.update(state='completed', new_main=ident, starting_candidate=observed,
                       handoff_path=str(folder / 'handoff.json'))
        self.report.setdefault('handoff_history', []).append(dict(handoff))
        self.record('handoff_completed', previous_main=old, agent_id=ident, candidate=observed)

    def _evaluate(self, candidate, key):
        if self.feedback_grader is not None:
            result = self.feedback_grader(candidate)
            receipt = result.get('receipt', {})
            failed, total = receipt.get('tests_failed'), receipt.get('tests_total')
            if (result.get('candidate_sha256') != candidate['sha256']
                    or type(failed) is not int or type(total) is not int or not 0 <= failed <= total
                    or result.get('passed') is not (total > 0 and failed == 0)):
                raise ValueError('Course receipt is not valid for the selected candidate')
            return result
        result = grade_candidate(self.starting_workspace(), self.report['frozen']['revision'], candidate,
                                 self.local_grader, self.root / 'evaluations' / key)
        answer = {'candidate_sha256': candidate['sha256'], 'execution': result,
                  'official': self.local_grader.official, 'passed': None,
                  'counts_status': 'unavailable', 'receipt': {}}
        if result.get('state') == 'exited' and result.get('source_unchanged'):
            try:
                counts = json.loads(Path(result['stdout_path']).read_text())
                failed, total = counts.get('tests_failed'), counts.get('tests_total')
                if type(failed) is int and type(total) is int and 0 <= failed <= total:
                    answer.update(receipt={'tests_failed': failed, 'tests_total': total},
                                  counts_status='reported', passed=total > 0 and failed == 0)
            except (OSError, ValueError, AttributeError):
                pass
        return answer

    def review_checkpoint(self, agent_id, candidate):
        if agent_id != self.report['agents']['main']:
            raise ValueError('Only the main requests candidate review')
        key = candidate['sha256']
        with self._grade_lock:
            reviews = self.report.setdefault('checkpoint_reviews', {})
            item = reviews.setdefault(key, {'candidate': candidate, 'result': {
                'meaning': 'Adversarial checkpoint review requested by main; no grade implied.'}})
            self.schedule_review(key, item)
            ident = item['review_agent_id']
            return {'agent_id': ident, 'candidate_sha256': key,
                    'state': self.manager.inspect(ident)['status'],
                    'meaning': 'Independent interpretation; inspect/wait for evidence, then resolve findings. No approval is implied.'}

    def schedule_review(self, key, item):
        if 'review_agent_id' not in item:
            candidate = item['candidate']
            root = self.root / 'failure-reviews' / key
            root.mkdir(parents=True, exist_ok=True)
            source = root / 'source'
            if not source.exists():
                raw = Path(candidate['patch_path']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != key:
                    raise ValueError('Graded patch bytes changed before review materialization')
                shutil.copytree(self.starting_workspace(), source, symlinks=True)
                if raw:
                    subprocess.run(['git', '-C', str(source), 'apply', '--binary', '-'],
                                   input=raw, check=True, capture_output=True)
            observed = capture(source, root / 'starting.patch', candidate['base'])
            if observed['sha256'] != key:
                raise ValueError('Review starting bytes differ from the exact graded candidate')
            ident = hashlib.sha256(('failure-review:' + key).encode()).hexdigest()[:32]
            from .repair import current
            main_root = Path(self.manager.descriptor(self.report['agents']['main'])['root'])
            plan = current(main_root)
            verification = {'checks': plan['checks'] if plan else [],
                            'authored_guidance': plan['guidance'] if plan else [],
                            'execution_receipts': []}
            if plan:
                baseline = main_root / 'repair-plans' / (plan['id'] + '.baseline.json')
                if baseline.exists():
                    verification['baseline_receipt'] = str(baseline)
                for path in sorted((main_root / 'test-plan-runs').glob('*/receipt.json')):
                    receipt = json.loads(path.read_text())
                    if receipt.get('candidate', {}).get('sha256') == key:
                        verification['execution_receipts'].append(str(path))
            task = ('CANDIDATE EVIDENCE (runtime receipt; grader counts only when present)\n' +
                    facts({'candidate': candidate, 'grade': item['result'],
                           'verification': verification}) +
                    '\n\nTry to find the strongest consequential counterexample to this exact repair. '
                    'Start from the task and candidate, independently of the main agent\'s narrative. '
                    'Counts do not reveal hidden cases. Use real callers, controlled probes and original outputs. '
                    'You may inspect team archives to resolve a concrete question. Return supported findings '
                    'and uncertainties; your report does not approve or block the main investigation. '
                    'Experimental edits remain in your private copy unless the main deliberately integrates them.')
            self.manager.create('reviewer', task, source, private=True,
                                parent=self.report['agents']['main'], identity=ident, public_task=self.report['task'], inherit_context=False)
            copied = capture(Path(self.manager.descriptor(ident)['workspace']), root / 'reviewer-starting.patch', candidate['base'])
            if copied['sha256'] != key:
                raise ValueError('Private reviewer did not inherit the graded bytes')
            with self._report_lock:
                item['review_agent_id'] = ident
                self.report['reviews'][ident] = {'candidate_sha256': key, 'state': 'queued',
                                                'starting_candidate': copied}
                self.report['agents']['failure-review-' + key] = ident
                self.record('failure_review_created', agent_id=ident, candidate=candidate)
        self.dispatch_reviews()

    def dispatch_reviews(self):
        # Starting and notifying are idempotent effects governed by manager
        # leases/controls. This callback can outlive the workflow owner; never
        # publish its old in-memory report over a newer continuation.
        with self._dispatch_lock:
            path = self.root / 'workflow.json'
            report = json.loads(path.read_text()) if path.exists() else self.report
            for ident, review in report['reviews'].items():
                state = self.manager.inspect(ident)
                if state['status'] == 'idle':
                    self.manager.start(ident)
                elif state['status'] != 'running':
                    self.agent_returned(ident, state)

    def agent_returned(self, ident, returned=None):
        # The callback appends immutable evidence only. A controller that
        # outlives its workflow lease cannot overwrite a newer projection.
        path = self.root / 'workflow.json'
        report = json.loads(path.read_text()) if path.exists() else self.report
        main = report.get('agents', {}).get('main')
        if ident == main:
            return None
        descriptor = self.manager.descriptor(ident)
        parent = descriptor.get('parent')
        if not parent:
            return None
        returned = returned or self.manager.inspect(ident)
        if returned['status'] in ('idle', 'running'):
            return None
        review = report.get('reviews', {}).get(ident)
        notification = {'event': 'failure_review_returned' if review else 'agent_returned',
            'agent_id': ident, 'parent_agent_id': parent, 'status': returned['status'],
            'assignment': {'author': parent, 'text': descriptor['task']},
            'source': {'workspace': descriptor['workspace'], 'base': descriptor.get('base')},
            'model_report': {'author': ident, 'text': returned.get('report'), 'outcome': returned.get('outcome')},
            'artifacts': {'root': descriptor['root'], 'patch': returned.get('patch')},
            'meaning': 'Attributed model interpretation of this assignment and captured source; not a runtime correctness verdict or approval of later edits.'}
        if review:
            notification['candidate_sha256'] = review['candidate_sha256']
        receipt = {k: v for k, v in returned.items() if k not in
                   ('agent_id', 'workspace', 'root', 'role', 'parent', 'workspace_mode')}
        fingerprint = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
        notification['return_receipt'] = receipt
        archived_return = self.manager.archive_return(ident, receipt, fingerprint)
        notification['return_reference'] = archived_return
        identity = 'agent-return-' + ident + '-' + fingerprint
        if parent == main:
            self.manager.return_observation(main, notification, identity)
        else:
            self.manager.observation(parent, notification, identity)
            # Root awareness does not change who delegated this assignment.
            # Keep the routed event small; originals stay in the child's archive.
            if self._return_belongs_to(ident, report):
                routed = {k: notification[k] for k in
                          ('event', 'agent_id', 'parent_agent_id', 'status', 'source', 'artifacts', 'meaning')}
                routed.update(assignment={'author': parent, 'reference': {'path': str(Path(descriptor['root']) / 'agent.json'), 'field': 'task'}},
                    model_report={'author': ident, 'reference': {**archived_return, 'field': 'report'}},
                    return_reference=archived_return)
                self.manager.return_observation(main, routed, identity)
        return fingerprint, notification

    def _descends_from(self, ident, ancestor):
        seen = set()
        while ident and ident not in seen:
            seen.add(ident)
            ident = self.manager.descriptor(ident).get('parent')
            if ident == ancestor:
                return True
        return False

    def _return_belongs_to(self, ident, report):
        """A takeover changes the root owner, not the helper's actual parent."""
        owners = {report.get('agents', {}).get('main')}
        for handoff in reversed(report.get('handoff_history', [])):
            if handoff.get('state') == 'completed' and handoff.get('new_main') in owners:
                owners.add(handoff['previous_main'])
        return any(owner and self._descends_from(ident, owner) for owner in owners)

    def reconcile_returns(self):
        """Recover missed completion notifications from durable terminal receipts."""
        path = self.root / 'workflow.json'
        report = json.loads(path.read_text()) if path.exists() else self.report
        for path in sorted((self.manager.root / 'agents').glob('*/agent.json')):
            descriptor = json.loads(path.read_text())
            if descriptor.get('parent') and self._return_belongs_to(path.parent.name, report):
                from contextlib import closing
                import sqlite3
                database = path.parent / 'session.sqlite3'
                receipts = []
                if database.exists():
                    with closing(sqlite3.connect(database)) as db:
                        receipts = [json.loads(row[0]) for row in db.execute(
                            "SELECT body FROM objects WHERE kind='agent_return' ORDER BY id")]
                for receipt in receipts:
                    self.agent_returned(path.parent.name, receipt)
                # Includes pre-upgrade receipts and observed absent executors.
                # A durable return can precede runner.json publication if its
                # process dies in that gap; do not invent a second uncertain
                # return for the same already recorded execution.
                observed = self.manager.inspect(path.parent.name)
                published = any(r.get('ended_at', 0) >= observed.get('started_at', float('inf')) for r in receipts)
                if observed['status'] != 'interrupted' or not published:
                    self.agent_returned(path.parent.name, observed)

    def settled_report(self):
        """Project current receipts after caller-owned callback draining.

        Joining helpers belongs to command lifetime, not a model action gate.
        Reload before writing so an older callback controller never publishes
        its stale workflow copy over a newer explicit continuation.
        """
        with self.owner():
            with self._report_lock:
                self.report = json.loads((self.root / 'workflow.json').read_text())
            ident = self.report['agents']['main']
            returned = self.manager.inspect(ident)
            # Notification identities are the delivery ledger. Derive review
            # status without treating reports as correctness verdicts.
            from contextlib import closing
            import sqlite3
            with closing(sqlite3.connect(Path(self.manager.descriptor(ident)['root']) / 'session.sqlite3')) as db:
                notifications = [json.loads(row[0]).get('observation', {}) for row in db.execute(
                    "SELECT body FROM objects WHERE kind='control' ORDER BY id")]
            for review_id, review in self.report['reviews'].items():
                notices = [n for n in notifications if n.get('agent_id') == review_id and n.get('event') == 'failure_review_returned']
                review['delivered_returns'] = [hashlib.sha256(json.dumps(n['return_receipt'], sort_keys=True).encode()).hexdigest() for n in notices]
                observed = self.manager.inspect(review_id)
                review['state'] = 'returned' if observed['status'] not in ('idle', 'running') else 'queued' if observed['status'] == 'idle' else 'started'
                if notices:
                    review['returned'] = observed
            with self._report_lock:
                self.report['main_return'] = returned
                accepted = self.report.get('completion_attempts', {}).get(self.report.get('latest_completion'), {})
                gated = self.report.get('completion_gate', {}).get('enabled')
                verified = (accepted.get('state') == 'passed' and accepted.get('agent_id') == ident
                    and accepted.get('candidate', {}).get('sha256') == (returned.get('patch') or {}).get('sha256'))
                self.report['state'] = ('completion_pending' if gated and returned['status'] == 'completed' and not verified
                                        else returned['status'])
                if returned['status'] == 'completed':
                    if not gated or verified:
                        self.report.update(candidate=returned.get('patch'), conclusion=returned.get('report'),
                                           outcome=returned.get('outcome'))
                    else:
                        self.report.pop('candidate', None)
                self.record('main_state_observed', agent_id=ident, receipt=returned)
                return dict(self.report)
