"""Fresh contexts and explicit workspace ownership, with no hidden agent scheduler.

Each conversation has one executor lease. Agent slots are shared by processes
using this campaign directory. Nested delegation uses the same mechanism.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
import hashlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import traceback
import uuid
from typing import Callable, Any

from src import workspace as effects
from .prompts import system_prompt, freeze_guides, role_names
from .session import Session, CONTROL_TOOLS
from .tools import ToolRuntime, TOOLS


def snapshot(source: Path, target: Path) -> dict:
    source, target = source.resolve(), target.resolve()
    if target == source or source in target.parents:
        raise ValueError('Snapshot must be outside source workspace')
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        effects.git(source, 'rev-parse', 'HEAD')
    except RuntimeError:
        shutil.copytree(source, target, symlinks=True,
                        ignore=lambda _p, names: ['.git'] if '.git' in names else [])
        return {'workspace': str(target), 'base': None, 'source_candidate': None}
    # Preserve ancestry: final capture must resolve the frozen task revision.
    from .candidates import snapshot as freeze_source
    evidence = target.parent / 'snapshot-evidence'
    frozen = freeze_source(source, target, evidence)
    return {'workspace': str(target), 'base': frozen['revision'], 'source_candidate': frozen}


class AgentManager:
    def __init__(self, root: Path, provider_factory: Callable[[Path], Any], max_agents: int | None = None,
                 deadline: float | None = None, prompt_supplement: str | None = None, run_facts_provider=None):
        if max_agents is not None and (type(max_agents) is not int or max_agents < 1):
            raise ValueError('max_agents must be positive when explicitly configured')
        if deadline is not None:
            import math
            if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= 0:
                raise ValueError('deadline must be an actual positive finite Unix timestamp')
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'agents').mkdir(exist_ok=True)
        self.provider_factory = provider_factory
        self.threads: dict[str, threading.Thread] = {}
        self.workers: set[threading.Thread] = set()
        self.group_owners = {}
        self.thread_lock = threading.RLock()
        self.workflow = None
        self.run_facts_provider = run_facts_provider
        self.automatic_wakes_enabled = True
        with self._locked():
            self.guide_root = self.root / 'guides'
            self.guide_manifest = freeze_guides(self.guide_root)
            supplement_path = self.root / 'prompt-supplement.json'
            if prompt_supplement is not None and not isinstance(prompt_supplement, str):
                raise ValueError('Prompt supplement must be text')
            if supplement_path.exists():
                saved_supplement = json.loads(supplement_path.read_text())['text']
                if prompt_supplement is not None and prompt_supplement != saved_supplement:
                    raise ValueError('Frozen prompt supplement differs; cannot change on resume')
            else:
                saved_supplement = prompt_supplement or ''
                if (self.root / 'role-prompts.json').exists() and saved_supplement:
                    raise ValueError('Cannot add guidance to an existing frozen team')
                effects.dump(supplement_path, {'text': saved_supplement})
            supplement_suffix = ('\n\nAuthored task-specific guidance (not runtime evidence):\n' + saved_supplement) if saved_supplement else ''
            prompts_path = self.root / 'role-prompts.json'
            if not prompts_path.exists():
                effects.dump(prompts_path, {role: system_prompt(role, Path('/__TEAM_WORKSPACE__'), guide_root=self.guide_root) + supplement_suffix for role in role_names()})
            self.role_prompts = json.loads(prompts_path.read_text())
            config = self.root / 'manager.json'
            if config.exists():
                saved = json.loads(config.read_text())
                self.max_agents = saved['max_agents']
                self.deadline = saved.get('deadline')
                self.started_at = saved.get('started_at')
                if deadline is not None and deadline != self.deadline:
                    raise ValueError('Existing team deadline differs; it cannot be renewed on resume')
            else:
                self.max_agents = max_agents
                self.deadline = deadline
                self.started_at = time.time()
                effects.dump(config, {'max_agents': max_agents, 'deadline': deadline, 'version': 2, 'started_at': self.started_at})

    @contextmanager
    def _locked(self):
        with (self.root / 'manager.lock').open('a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def _path(self, agent_id: str) -> Path:
        if not isinstance(agent_id, str) or len(agent_id) != 32 or any(c not in '0123456789abcdef' for c in agent_id):
            raise ValueError('Expected recorded agent ID')
        p = self.root / 'agents' / agent_id
        if not (p / 'agent.json').exists():
            raise ValueError('Unknown agent')
        return p

    def descriptor(self, agent_id: str) -> dict:
        return json.loads((self._path(agent_id) / 'agent.json').read_text())

    def create(self, role: str, task: str, workspace: Path, private: bool = False,
               parent: str | None = None, identity: str | None = None,
               public_task: str | None = None, capture_base: str | None = None,
               working_directory: Path | None = None, inherit_context: bool = True,
               human_inputs: list[dict] | None = None, review_takeover: bool = False) -> str:
        agent_id = identity or uuid.uuid4().hex
        if len(agent_id) != 32 or any(c not in '0123456789abcdef' for c in agent_id):
            raise ValueError('Invalid agent identity')
        workspace = Path(workspace).resolve()
        if not workspace.is_dir() or not isinstance(task, str) or not task.strip():
            raise ValueError('Existing workspace and nonempty assignment required')
        if workspace == self.root or workspace in self.root.parents:
            raise ValueError('Evidence root must be outside the source workspace')
        inherited_context = None
        if human_inputs is not None:
            if parent is not None:
                raise ValueError('Explicit human inputs cannot be mixed with parent inheritance')
            inherited_context = {'human_inputs': human_inputs, 'working_brief': None}
        if parent:
            original = self.descriptor(parent)
            public_task = public_task if public_task is not None else original.get('public_task')
            inherited_context = self._session(parent).task_context()
            if not inherit_context:
                inherited_context = {**inherited_context, 'working_brief': None}
            if working_directory is None:
                working_directory = workspace / Path(original.get(
                    'working_directory', original['workspace'])).relative_to(original['workspace'])
        working_directory = (working_directory or workspace).resolve()
        relative_cwd = working_directory.relative_to(workspace)
        if not working_directory.is_dir():
            raise ValueError('Command working directory does not exist')
        with self._locked():
            p = self.root / 'agents' / agent_id
            if (p / 'agent.json').exists():
                d = json.loads((p / 'agent.json').read_text())
                if (d['role'], d['task'], d['parent'], d['starting_workspace'], d['workspace_mode']) != (role, task, parent, str(workspace), 'private' if private else 'shared'):
                    raise ValueError('Agent identity reused for a different assignment')
                if d.get('starting_working_directory', d['starting_workspace']) != str(working_directory):
                    raise ValueError('Agent identity reused with a different command directory')
                if ((public_task is not None and d.get('public_task') != public_task)
                        or (capture_base is not None and d.get('base') != capture_base)):
                    raise ValueError('Agent identity reused with a different public task or capture base')
                return agent_id
            p.mkdir(exist_ok=False)
            if private:
                spec = snapshot(workspace, p / 'workspace')
            else:
                try:
                    base = effects.git(workspace, 'rev-parse', 'HEAD').decode().strip()
                except RuntimeError:
                    base = None
                spec = {'workspace': str(workspace), 'base': base, 'source_candidate': None}
            if capture_base is not None:
                from .workspaces import bind_capture_base
                bind_capture_base(Path(spec['workspace']), workspace, capture_base)
                effects.git(Path(spec['workspace']), 'rev-parse', '--verify', capture_base + '^{commit}')
                spec['base'] = capture_base
            spec['working_directory'] = str(Path(spec['workspace']) / relative_cwd)
            metadata = {'agent_id': agent_id, 'parent': parent, 'archive_root': str(p),
                        'workspace_mode': 'private' if private else 'shared', 'max_agents': self.max_agents,
                        'task_deadline_unix': self.deadline}
            if role == 'main' and self.workflow is not None and self.workflow.report.get('completion_gate', {}).get('enabled'):
                metadata['completion_gate'] = {**self.workflow.report['completion_gate'],
                    'meaning': ('finish_role requests candidate verification. Each recorded failing completion grade transfers its exact candidate to a review-focused owner once; repeating that grade or other verification failures returns feedback to the current owner. Interactive failed grades leave you active.'
                                if self.workflow.report['completion_gate'].get('version') == 2 else
                                'finish_role requests candidate verification; a failed gate transfers the exact candidate to a fresh main conversation. Interactive failed grades alone do not trigger handoff.')}
            if review_takeover:
                metadata['review_takeover'] = True
            if role == 'main' and not review_takeover and self.workflow is not None and self.workflow.report.get('planning_boundary', {}).get('enabled'):
                metadata['planning_boundary'] = dict(self.workflow.report['planning_boundary'])
                # Historical takeovers retain ordinary planning, with no second
                # allocation ballot. Current review takeovers skip this block.
                if self.workflow.report.get('allocation'):
                    metadata['planning_boundary'].pop('pause_after_planning', None)
            system = self.role_prompts[role].replace('/__TEAM_WORKSPACE__', spec['workspace'])
            if metadata.get('planning_boundary', {}).get('enabled'):
                from .planning import PROMPT
                system += '\n\n' + PROMPT
                if metadata['planning_boundary'].get('pause_after_planning'):
                    from .planning import BATCH_PROMPT
                    system += '\n\n' + BATCH_PROMPT
            from .prompts import language_routing
            system += language_routing(Path(spec['working_directory']), self.guide_root, Path(spec['workspace']))
            from .presentation import human_inputs_text, session_facts
            system += '\n\nSession facts (runtime):\n' + session_facts(metadata)
            assignment = task
            if inherited_context is not None and public_task is not None:
                delivered_context = {k: v for k, v in inherited_context.items() if k != 'working_brief'}
                # The original public task is already literal immediately above.
                inherited_humans = list(delivered_context['human_inputs'])
                if inherited_humans and inherited_humans[0]['text'] == public_task:
                    inherited_humans.pop(0)
                delivered_context['human_inputs'] = inherited_humans
                assignment = ('ORIGINAL PUBLIC TASK (user input)\n' + public_task +
                    ('\n\nCURRENT TASK CONTEXT (literal human inputs and attributed working brief)\n' if parent else
                     '\n\nINHERITED HUMAN REQUIREMENTS (literal inputs and original references)\n') +
                    human_inputs_text(delivered_context) +
                    ('\n\nDELEGATED ASSIGNMENT (parent model interpretation)\n' if parent else
                     '\n\nNEW OWNER ASSIGNMENT (runtime handoff)\n') + task)
            session_task = ('WORKSPACE ASSIGNMENT (runtime fact)\n'
                            f"Execute commands and make candidate edits in {spec['workspace']}. "
                            f"Commands default to {spec['working_directory']}. Relative file-tool paths resolve in the repository workspace. "
                            'Absolute paths quoted in another role\'s handoff identify that role\'s evidence, '
                            'not your working directory. Translate repository-relative locations to your own copy. '
                            'The preserved baseline is evidence, not an editing or test-fixture workspace.\n\n'
                            + assignment)
            from .tools import EVALUATION_TOOL
            capabilities = TOOLS + CONTROL_TOOLS
            if role == 'main' and self.workflow is not None and self.workflow.grading_available:
                capabilities = capabilities + [EVALUATION_TOOL]
            descriptor = {**spec, **metadata, 'session_task': session_task, 'id': agent_id, 'role': role, 'task': task,
                          'system': system, 'tools': capabilities, 'root': str(p),
                          'guide_hashes': self.guide_manifest, 'created_at': time.time(), 'starting_workspace': str(workspace),
                          'starting_working_directory': str(working_directory)}
            if inherited_context is not None:
                descriptor['inherited_task_context'] = inherited_context
            if public_task is not None:
                descriptor['public_task'] = public_task
            effects.dump(p / 'agent.json', descriptor)
            effects.dump(p / 'runner.json', {'status': 'idle', 'id': agent_id})
        return agent_id

    def _session(self, agent_id: str, provider=None) -> Session:
        d = self.descriptor(agent_id)
        config = Session.recorded_config(Path(d['root']))
        if config is not None:
            return Session(root=Path(d['root']), provider=provider,
                           executor=ToolRuntime(self, agent_id, d), **config)
        return Session(root=Path(d['root']), role=d['role'], system=d['system'], task=d.get('session_task', d['task']),
                       provider=provider, tools=d['tools'], executor=ToolRuntime(self, agent_id, d),
                       workspace=Path(d['workspace']), public_task=d.get('public_task'),
                       inherited_inputs=d.get('inherited_task_context', {}).get('human_inputs'),
                       inherited_brief=d.get('inherited_task_context', {}).get('working_brief'),
                       metadata={k: d[k] for k in ('agent_id', 'parent', 'archive_root', 'workspace_mode', 'max_agents', 'task_deadline_unix', 'completion_gate', 'planning_boundary') if k in d})

    @staticmethod
    def _active(p: Path) -> bool:
        with (p / 'runner.lock').open('a') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False

    def start(self, agent_id: str) -> dict:
        with self._locked():
            return self._start_locked(agent_id)

    def _start_locked(self, agent_id: str) -> dict:
        """Start while holding the delivery lock; the executor lease is decisive."""
        p = self._path(agent_id)
        lease = (p / 'runner.lock').open('a')
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lease.close()
            return dict(self.inspect(agent_id), start='already_running')
        active = sum(self._active(other) for other in (self.root / 'agents').iterdir()
                     if other.is_dir() and other != p and (other / 'agent.json').exists())
        if self.max_agents is not None and active >= self.max_agents:
            lease.close()
            return dict(self.inspect(agent_id), start='capacity_unavailable', max_agents=self.max_agents)
        # Publish the initial checkpoint while controls share this lock.
        # The worker and an immediate follow-up must not race to create it.
        try:
            self._session(agent_id)
        except Exception:
            lease.close()
            raise
        effects.dump(p / 'runner.json', {'status': 'running', 'id': agent_id,
                                        'pid': os.getpid(), 'started_at': time.time()})
        thread = threading.Thread(target=self._run, args=(agent_id, lease), name='agent-' + agent_id[:8], daemon=True)
        with self.thread_lock:
            self.threads[agent_id] = thread
            self.workers.add(thread)
        thread.start()
        return dict(self.inspect(agent_id), start='started')

    def _provider(self, root: Path):
        from .provider import HTTPProvider
        config = Session.recorded_config(root)
        if (isinstance(self.provider_factory, type) and issubclass(self.provider_factory, HTTPProvider)
                and config and config.get('provider_configuration') is not None):
            return self.provider_factory(root, configuration=config['provider_configuration'])
        provider = self.provider_factory(root)
        if isinstance(provider, HTTPProvider) and config and config.get('provider_configuration') is not None:
            # A callable factory may still construct the frozen initial route.
            # The session's explicit configuration owns subsequent requests.
            provider.config = HTTPProvider.validate_configuration(config['provider_configuration'])
            provider.model = provider.config['model']
            provider.explicit_configuration = True
        return provider

    def _run(self, agent_id: str, lease) -> None:
        """Drain owned Python callbacks before publishing a final source patch.

        A model's finish_role return precedes this observation boundary. Initial
        tool receipts must be durable before capture; external shell lifetimes
        are not joined or stopped. An operator stop or the configured deadline
        can interrupt the wait, leaving the model return and unfinished effects
        explicit rather than publishing a completed final patch.
        """
        p = self._path(agent_id)
        provider = None
        terminal_return = None
        try:
            provider = self._provider(p)
            session = self._session(agent_id, provider)
            completion_boundary = False
            def pending_controls():
                with closing(sqlite3.connect(session.database)) as db:
                    state = json.loads(db.execute("SELECT body FROM objects WHERE kind='session_state' ORDER BY id DESC LIMIT 1").fetchone()[0])
                    controls = [json.loads(row[0]) for row in db.execute(
                        "SELECT body FROM objects WHERE kind='control' AND id>? ORDER BY id", (state['last_control'],))]
                    return [c for c in controls if not c.get('automatic')] if completion_boundary else controls
            def latest_run_control(controls):
                kinds = [control['type'] for control in controls if control['type'] in ('stop', 'message')]
                return kinds[-1] if kinds else None
            while True:
                result = session.run()
                completion_boundary = (result.get('status') in ('skipped', 'planning_timeout', 'planning_complete') or
                    (result.get('status') == 'completed' and self.workflow is not None
                    and self.workflow.report.get('completion_gate', {}).get('enabled')
                    and self.workflow.report.get('agents', {}).get('main') == agent_id))
                d = self.descriptor(agent_id)
                with self.thread_lock:
                    groups = self.group_owners.get(agent_id)
                interrupted, resume_controls = None, False
                while groups is not None and groups.callbacks_active():
                    if self.deadline is not None and time.time() >= self.deadline:
                        interrupted = 'task_deadline'
                        break
                    controls = pending_controls()
                    if result.get('status') == 'stopped' or latest_run_control(controls) == 'stop':
                        interrupted = 'operator_stop'
                        break
                    if controls:
                        # Let the existing session process explicit follow-ups
                        # and compaction in their original order.
                        resume_controls = True
                        break
                    time.sleep(.025)
                if resume_controls:
                    continue
                patch = None
                source_capture = {'state': 'not_applicable', 'reason': 'no_git_base'}
                if interrupted:
                    source_capture = {'state': 'interrupted', 'reason': interrupted,
                        'callbacks_active': groups.callbacks_active(), 'effects_terminated': False,
                        'message': 'Final source was not captured; outstanding callbacks may still complete.'}
                elif result.get('status') == 'planning_complete':
                    source_capture = {'state': 'not_applicable', 'reason': 'planning_pause',
                        'external_jobs_stopped': False, 'meaning': 'The investigation is waiting for allocation; no final candidate is captured.'}
                elif result.get('selected_checkpoint') is not None:
                    patch = ToolRuntime(self, agent_id, d).checkpoint_candidate(result['selected_checkpoint'])
                    source_capture = {'state': 'captured', 'sha256': patch['sha256'],
                        'checkpoint_id': result['selected_checkpoint'], 'live_source_compared': False,
                        'in_process_callbacks_drained': True, 'external_jobs_stopped': False,
                        'meaning': 'The model selected these earlier captured bytes; later working files remain unchanged.'}
                elif d['base']:
                    captures = p / 'captures'
                    captures.mkdir(exist_ok=True)
                    name = uuid.uuid4().hex
                    from .candidates import capture as capture_candidate
                    patch = capture_candidate(d['workspace'], captures / (name + '.patch'), d['base'])
                    check = captures / (name + '.check.patch')
                    confirm = capture_candidate(d['workspace'], check, d['base'])
                    if patch['sha256'] != confirm['sha256']:
                        raise RuntimeError('Source changed during final capture; candidate is uncertain')
                    source_capture = {'state': 'captured', 'sha256': patch['sha256'],
                        'in_process_callbacks_drained': True, 'external_jobs_stopped': False}
                # Complete under the same lock used to deliver follow-ups/start.
                # A message received during capture cannot be stranded behind a
                # disappearing executor, nor can a second worker replay the turn.
                with self._locked():
                    controls = pending_controls()
                    if controls and (not interrupted or (interrupted == 'operator_stop' and latest_run_control(controls) == 'message')):
                        continue
                    if interrupted:
                        result = {'status': 'stopped', 'outcome': None, 'session_return': result,
                            'report': 'Source capture interrupted by ' + interrupted +
                                      '; the original role return is preserved in session_return. Effects were not terminated.'}
                    status = result.get('status', 'error')
                    if status in ('complete', 'finished', 'completed'):
                        status = 'completed'
                    terminal_return = {**result, 'id': agent_id, 'status': status,
                                       'patch': patch, 'source_capture': source_capture, 'ended_at': time.time()}
                    if status == 'planning_complete':
                        effects.dump(p / 'planning-paused.json', {'meaning': 'Waiting for explicit controller admission; automatic observations cannot allocate compute.'})
                    elif completion_boundary:
                        effects.dump(p / 'completion-closed.json', {'meaning': 'Completion or planning ended this attempt; automatic observations cannot reopen this conversation.'})
                    fingerprint = hashlib.sha256(json.dumps(terminal_return, sort_keys=True).encode()).hexdigest()
                    self._archive_return_locked(agent_id, terminal_return, fingerprint)
                    effects.dump(p / 'runner.json', terminal_return)
                    lease.close()
                    break
        except Exception as e:
            with self._locked():
                terminal_return = {'id': agent_id, 'status': 'error', 'error': str(e),
                                   'error_type': type(e).__name__, 'ended_at': time.time(),
                                   'error_traceback': traceback.format_exc(),
                                   **{key: getattr(e, key) for key in ('sqlite_errorcode', 'sqlite_errorname', 'errno') if hasattr(e, key)}}
                fingerprint = hashlib.sha256(json.dumps(terminal_return, sort_keys=True).encode()).hexdigest()
                self._archive_return_locked(agent_id, terminal_return, fingerprint)
                effects.dump(p / 'runner.json', terminal_return)
                lease.close()
        finally:
            try:
                if provider is not None and hasattr(provider, 'close'):
                    provider.close()
            finally:
                lease.close()
                if self.workflow is not None:
                    try:
                        self.workflow.agent_returned(agent_id, terminal_return)
                    except Exception as error:
                        # Notification is a separate effect. Its failure must
                        # not erase a model return, error or captured patch.
                        effects.dump(p / 'notification-error.json', {
                            'agent_id': agent_id, 'error': str(error),
                            'error_type': type(error).__name__, 'at': time.time()})
                self.dispatch_wakeups()

    def inspect(self, agent_id: str) -> dict:
        p = self._path(agent_id)
        d = self.descriptor(agent_id)
        # Observe the lease before the receipt. If acquired, retain it through
        # the read so a replacement executor cannot publish a new running state.
        # If held, a stale running observation is safe to poll again. Reading
        # running first and testing the lease afterwards misclassified a worker
        # that published completion and released its lease in that gap.
        with (p / 'runner.lock').open('a') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                absent = True
            except BlockingIOError:
                absent = False
            state = json.loads((p / 'runner.json').read_text())
            if state['status'] == 'running' and absent:
                state = {**state, 'status': 'interrupted', 'message': 'Executor is absent; effects may have executed. Resume explicitly; no automatic replay.'}
        return {**state, 'agent_id': agent_id, 'workspace': d['workspace'], 'root': str(p),
                'role': d['role'], 'parent': d['parent'], 'workspace_mode': d['workspace_mode']}

    def wait(self, agent_id: str, timeout: float | None = None) -> dict:
        if timeout is not None and timeout < 0:
            raise ValueError('timeout must be nonnegative')
        start = time.monotonic()
        while True:
            state = self.inspect(agent_id)
            if state['status'] != 'running':
                return state
            if timeout is not None and time.monotonic() - start >= timeout:
                return state
            time.sleep(.025)

    def send(self, agent_id: str, message: str, identity: str | None = None, *,
             sender: str | None = None, relationship: str | None = None):
        with self._locked():
            session = self._session(agent_id)
            if sender is None:
                session.append_user(message, identity=identity)
            else:
                source = self.descriptor(sender)
                target = self.descriptor(agent_id)
                relationship = relationship or ('parent' if target.get('parent') == sender else
                    'child' if source.get('parent') == agent_id else 'peer')
                session.append_message(message, sender=sender, relationship=relationship, identity=identity)
        return {'agent_id': agent_id, 'message': 'Follow-up queued'}

    def observation(self, agent_id: str, value: dict, identity: str, *, resume: bool = False):
        with self._locked():
            return Session.append_control(self._path(agent_id), 'observation', identity=identity,
                options={'observation': value, 'resume': resume, 'automatic': False})

    def archive_return(self, agent_id: str, receipt: dict, fingerprint: str):
        """Retain an exact return before later continuation replaces runner.json."""
        with self._locked():
            return self._archive_return_locked(agent_id, receipt, fingerprint)

    def _archive_return_locked(self, agent_id, receipt, fingerprint):
        root = self._path(agent_id)
        alias = 'agent-return:' + fingerprint
        ref = Session.archive_record(root, 'agent_return', receipt, alias)
        return {'path': str(root / 'session.sqlite3'), 'ref': ref, 'fingerprint': fingerprint}

    def return_observation(self, agent_id: str, value: dict, identity: str):
        """Exactly-once evidence and automatic wake under the completion lock.

        The control journal is also the durable wake queue. A busy executor
        consumes it at its next boundary; an absent executor is started with the
        same identity and archive. No prior action is submitted again.
        """
        with self._locked():
            Session.append_control(self._path(agent_id), 'observation', identity=identity,
                options={'observation': value, 'resume': True, 'automatic': True})
            self._dispatch_wakeups_locked()

    def dispatch_wakeups(self):
        with self._locked():
            self._dispatch_wakeups_locked()

    def _dispatch_wakeups_locked(self):
        if not self.automatic_wakes_enabled:
            return
        for path in sorted((self.root / 'agents').glob('*/agent.json')):
            if (path.parent / 'completion-closed.json').exists():
                continue
            if ((path.parent / 'planning-paused.json').exists()
                    and not (path.parent / 'planning-admission.json').exists()):
                continue
            database = path.parent / 'session.sqlite3'
            if not database.exists():
                continue
            with closing(sqlite3.connect(database)) as db:
                row = db.execute("SELECT body FROM objects WHERE kind='session_state' ORDER BY id DESC LIMIT 1").fetchone()
                if row is None:
                    continue
                state = json.loads(row[0])
                controls = [json.loads(row[0]) for row in db.execute(
                    "SELECT body FROM objects WHERE kind='control' AND id>? ORDER BY id", (state['last_control'],))]
                if not any(c.get('automatic') and c.get('resume') for c in controls):
                    continue
                # Explicit stop is sticky across consumed and unconsumed
                # controls. Only a later human message/continuation releases it.
                explicit = [json.loads(row[0]) for row in db.execute(
                    "SELECT body FROM objects WHERE kind='control' ORDER BY id")]
                stopped = False
                for control in explicit:
                    if control['type'] == 'stop' and control.get('author', 'human') in ('human', 'runtime'):
                        stopped = True
                    elif control['type'] == 'message' or (control['type'] == 'observation'
                            and control.get('resume') and not control.get('automatic')):
                        stopped = False
                if stopped:
                    continue
            self._start_locked(path.parent.name)

    def stop(self, agent_id: str, identity: str | None = None, *, sender: str | None = None) -> dict:
        with self._locked():
            self._session(agent_id).request_stop(identity=identity, sender=sender)
        return {'agent_id': agent_id, 'stop_requested': True, 'jobs_terminated': False}

    def reconfigure(self, agent_id: str, *, reason: str, provider=None, **changes):
        """Explicit configuration transition for an inactive conversation.

        Runtime identity, workspace and prior effects remain fixed. An active
        executor owns request boundaries; operators must use its in-session
        transition or wait for that executor to return.
        """
        with self._locked():
            p = self._path(agent_id)
            with (p / 'runner.lock').open('a') as lease:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError('An active executor owns configuration boundaries') from None
                current_provider = self._provider(p)
                try:
                    return self._session(agent_id, current_provider).reconfigure(reason=reason, provider=provider, **changes)
                finally:
                    if hasattr(current_provider, 'close'):
                        current_provider.close()

    def compact(self, agent_id: str, max_request_bytes: int | None = None,
                retain_inputs: bool | None = None) -> dict:
        # Queuing is safe across processes; active executor performs it at a boundary.
        with self._locked():
            self._session(agent_id).request_compaction(max_request_bytes=max_request_bytes,
                                                       retain_inputs=retain_inputs)
        state = self.inspect(agent_id)
        if state['status'] == 'running':
            return {'agent_id': agent_id, 'compaction': 'queued'}
        # Starting explicitly continues this session; compaction executes before inference.
        return self.start(agent_id)

    def list_agents(self) -> dict:
        """Compact, observed roster. Report existence is separate from truth."""
        rows = []
        for path in sorted((self.root / 'agents').glob('*/agent.json')):
            current = self.inspect(path.parent.name)
            row = {k: current[k] for k in ('agent_id', 'role', 'parent', 'status', 'workspace', 'workspace_mode')}
            report = current.get('report')
            row['report_available'] = bool(report)
            if report:
                row['report_reference'] = {'path': str(path.parent / 'runner.json'), 'field': 'report'}
            row['patch'] = current.get('patch')
            checkpoint = path.parent / 'latest-checkpoint.json'
            row['checkpoint'] = json.loads(checkpoint.read_text()) if checkpoint.exists() else None
            rows.append(row)
        return {'agents': rows, 'observed_at': time.time(), 'deadline': self.deadline_status()}

    def wait_agents(self, agent_ids: list[str], mode: str = 'all', timeout: float | None = 30) -> dict:
        """Wait for terminal/attention states without cancelling any executor."""
        import math
        if not isinstance(agent_ids, list) or not agent_ids or len(set(agent_ids)) != len(agent_ids):
            raise ValueError('Provide a nonempty list of unique agent IDs')
        if mode not in ('any', 'all'):
            raise ValueError('Wait mode must be any or all')
        if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0):
            raise ValueError('timeout must be nonnegative and finite')
        for ident in agent_ids:
            self._path(ident)
        started = time.monotonic()
        while True:
            states = [self.inspect(ident) for ident in agent_ids]
            ready = [s['agent_id'] for s in states if s['status'] not in ('running', 'idle', 'ready')]
            satisfied = bool(ready) if mode == 'any' else len(ready) == len(agent_ids)
            elapsed = time.monotonic() - started
            if satisfied or (timeout is not None and elapsed >= timeout):
                return {'mode': mode, 'wait_satisfied': satisfied, 'timed_out': not satisfied,
                        'elapsed_seconds': elapsed, 'ready_agent_ids': ready,
                        'pending_agent_ids': [i for i in agent_ids if i not in ready],
                        'agents': [{k: s.get(k) for k in ('agent_id', 'role', 'status', 'workspace', 'patch')} for s in states],
                        'agents_cancelled': False, 'observed_at': time.time()}
            time.sleep(min(.025, max(0, timeout - elapsed)) if timeout is not None else .025)

    def deadline_status(self) -> dict:
        observed = time.time()
        return {'deadline_unix': self.deadline, 'observed_at': observed,
                'remaining_seconds': max(0, self.deadline - observed) if self.deadline is not None else None,
                'expired': observed >= self.deadline if self.deadline is not None else None,
                'authority': 'External task owner; shared by all roles and descendants. No deadline is inferred when absent.'}

    def checkpoint(self, agent_id: str, identity: str | None = None, label: str = '') -> dict:
        d = self.descriptor(agent_id)
        if not d.get('base'):
            return {'captured': False, 'error': 'A Git base is required for an exact source patch'}
        identity = identity or uuid.uuid4().hex
        if not identity or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Checkpoint identity must be hexadecimal')
        directory = Path(d['root']) / 'checkpoints'
        directory.mkdir(exist_ok=True)
        receipt = directory / (identity + '.json')
        if receipt.exists():
            return json.loads(receipt.read_text())
        from .candidates import capture
        first = capture(d['workspace'], directory / (identity + '.patch'), d['base'])
        second = capture(d['workspace'], directory / (identity + '.check.patch'), d['base'])
        result = {'checkpoint_id': identity, 'agent_id': agent_id, 'label': label,
                  'captured': first['sha256'] == second['sha256'], 'candidate': first,
                  'confirmation_sha256': second['sha256'], 'captured_at': time.time(),
                  'tests_passed': None, 'review_accepted': None,
                  'meaning': 'Source bytes matched across two capture reads; no test or review conclusion is inferred.'}
        effects.dump(receipt, result)
        if result['captured']:
            with self._locked():
                effects.dump(Path(d['root']) / 'latest-checkpoint.json', result)
        return result

    def task_status(self, agent_id: str | None = None) -> dict:
        from .session import context_status
        roster = self.list_agents()['agents']
        jobs, errors, candidates, contexts, groups = [], [], [], [], []
        for row in roster:
            d = self.descriptor(row['agent_id'])
            state = Path(d['root']) / 'workstate'
            workspace = {'root': d['workspace'], 'state': str(state), 'base': d.get('base'), 'capture_patch': bool(d.get('base'))}
            for path in sorted((state / 'jobs').glob('*/request.json')):
                try:
                    observed = effects.observe(workspace, path.parent.name, count=0)
                    if observed.get('state') != 'exited':
                        jobs.append({'agent_id': row['agent_id'], **{k: observed.get(k) for k in ('id', 'state', 'exit_code')}})
                except Exception as error:
                    errors.append({'agent_id': row['agent_id'], 'job_id': path.parent.name, 'error': str(error)})
            if row.get('checkpoint'):
                candidates.append({'kind': 'checkpoint', **row['checkpoint']})
            if row.get('patch'):
                runner = json.loads((Path(d['root']) / 'runner.json').read_text())
                candidates.append({'kind': 'role_capture', 'agent_id': row['agent_id'], 'candidate': row['patch'], 'captured_at': runner.get('ended_at')})
            group_root = Path(d['root']) / 'effects/parallel-groups'
            if group_root.exists():
                from .parallel import ParallelGroups
                for group in ParallelGroups(group_root, lambda: None).list():
                    if group['state'] in ('running', 'cancelling', 'unknown'):
                        groups.append({'agent_id': row['agent_id'], **group})
            if agent_id is None or agent_id == row['agent_id']:
                try:
                    contexts.append({'agent_id': row['agent_id'], **context_status(Path(d['root']))})
                except (OSError, ValueError, sqlite3.Error) as error:
                    contexts.append({'agent_id': row['agent_id'], 'observation_error': str(error)})
        workflow_path = self.root.parent / 'workflow.json'
        workflow = json.loads(workflow_path.read_text()) if workflow_path.exists() else {}
        events = workflow.get('stages', [])
        result = {'observed_at': time.time(), 'deadline': self.deadline_status(), 'agents': roster,
                'runtime': ToolRuntime(self, agent_id, self.descriptor(agent_id)).runtime_facts() if agent_id else None,
                'active_agent_ids': [r['agent_id'] for r in roster if r['status'] == 'running'],
                'running_or_uncertain_jobs': jobs, 'job_observation_errors': errors,
                'active_or_uncertain_parallel_groups': groups,
                'jobs_scope': 'Persisted job leader receipts; arbitrary detached shell descendants are not comprehensively inventoried.',
                'latest_candidate': max(candidates, key=lambda r: r.get('captured_at') or 0) if candidates else None,
                'workflow_candidate': workflow.get('candidate'),
                'workflow_state': workflow.get('state'), 'workflow_last_event': events[-1] if events else None,
                'contexts': contexts, 'meaning': 'Observed operational state, not a repair-quality or progress verdict.'}
        if self.workflow is not None:
            result['investigation'] = self.workflow.status()
        if agent_id is not None:
            d = self.descriptor(agent_id)
            result['current_workspace'] = {'agent_id': agent_id, 'root': d['workspace'],
                'mode': d['workspace_mode'], 'base': d.get('base'),
                'dependency_isolation': 'not_established',
                'meaning': 'A private source copy does not establish isolation of external caches, symlinks or services.'}
            result['own_latest_candidate'] = max(
                (c for c in candidates if c.get('agent_id') == agent_id),
                key=lambda c: c.get('captured_at') or 0, default=None)
            result['current_source_matches_candidate'] = None
        return result

    def parallel_groups(self, agent_id: str):
        from .parallel import ParallelGroups
        descriptor = self.descriptor(agent_id)
        with self.thread_lock:
            if agent_id not in self.group_owners:
                def executor():
                    runtime = ToolRuntime(self, agent_id, descriptor)
                    config = Session.recorded_config(Path(descriptor['root']))
                    if config is not None:
                        runtime.configure_tools(config['tools'])
                    return runtime
                self.group_owners[agent_id] = ParallelGroups(Path(descriptor['root']) / 'effects/parallel-groups',
                    executor)
            return self.group_owners[agent_id]

    def cancel_owned(self):
        """Explicit process cancellation stops inference, retaining jobs/effects.

        A provider call or effect already in flight may lack a final receipt
        when the process exits. Continuation observes that uncertainty instead
        of replaying it. The stop journal also suppresses later controllers'
        automatic wakeups for this main.
        """
        with self._locked():
            self.automatic_wakes_enabled = False
            with self.thread_lock:
                owned = set(self.threads)
            main = self.workflow.report.get('agents', {}).get('main') if self.workflow is not None and owned else None
            stopped = []
            for ident in owned | ({main} if main else set()):
                if ident == main or self.inspect(ident)['status'] == 'running':
                    self._session(ident).request_stop()
                    stopped.append(ident)
            return {'stop_requested': stopped, 'jobs_terminated': False,
                    'in_flight_effects': 'May have executed; inspect original receipts and environment before continuing.'}

    def shutdown(self, wait: bool = True):
        """Wait for owned callbacks, not shell lifetimes; respect a real deadline.

        Reaching that deadline records incomplete waiting without claiming that
        callbacks or jobs were cancelled. The course process owner enforces the
        actual process cutoff separately.
        """
        if wait:
            if not self.root.exists():
                return
            while True:
                if self.workers:
                    if self.workflow is not None:
                        self.workflow.dispatch_reviews()
                        self.workflow.reconcile_returns()
                    self.dispatch_wakeups()
                with self.thread_lock:
                    threads = list(self.workers)
                    groups = list(self.group_owners.values())
                live = [t for t in threads if t is not threading.current_thread() and t.is_alive()]
                callbacks = any(group.callbacks_active() for group in groups)
                if not live and not callbacks:
                    break
                if self.deadline is not None and time.time() >= self.deadline:
                    observation = {'wait_completed': False, 'reason': 'task_deadline',
                        'callbacks_active': callbacks, 'live_agent_threads': len(live),
                        'effects_terminated': False, 'observed_at': time.time()}
                    destination = self.root / 'shutdown-observations' / (uuid.uuid4().hex + '.json')
                    destination.parent.mkdir(exist_ok=True)
                    effects.dump(destination, observation)
                    return {**observation, 'receipt': str(destination)}
                for t in live:
                    t.join(.1)
                if callbacks and not live:
                    time.sleep(.025)
