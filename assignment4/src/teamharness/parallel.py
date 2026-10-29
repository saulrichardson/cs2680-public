"""Persisted independent tools shared by explicit groups and native batches.

The executor factory supplies a fresh ToolRuntime for each callback. No session
or SQLite connection is shared by group threads. Original tool returns, process
observations and cancellation requests remain distinct. Ordinary tool ordering
is outside this module and unchanged.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
from typing import Callable
import uuid

from src import workspace as effects


FORBIDDEN = {'repair_plan', 'parallel', 'inspect_parallel', 'cancel_parallel', 'compact', 'finish_role', 'configure_session'}


def save(path: Path, value) -> None:
    """Distinct temporary paths also protect concurrent observation writers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    with temporary.open('x') as out:
        json.dump(value, out, ensure_ascii=False, allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


def read(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


@contextmanager
def locked(path: Path):
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


class ParallelGroups:
    def __init__(self, root: Path, executor_factory: Callable, max_workers: int | None = None):
        if max_workers is not None and (type(max_workers) is not int or max_workers < 1):
            raise ValueError('max_workers must be a positive explicit concurrency limit')
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.executor_factory = executor_factory
        self.max_workers = max_workers
        self.threads: dict[str, threading.Thread] = {}
        self._callback_lock = threading.Lock()
        self._callbacks: dict[tuple, str] = {}

    def callbacks_active(self) -> bool:
        """Whether this owner still owes an in-process callback or its receipt.

        Queued members count before their callbacks begin. Once a callback has
        returned and its original receipt is durable, following an external
        shell job does not keep the owning Python process alive. Other owners'
        persisted groups are observations, never callbacks to resume here.
        """
        with self._callback_lock:
            # A second manager may cancel a queued member while this owner's
            # worker is observing a long-lived shell. Its durable skip receipt
            # removes the callback obligation without waiting for that job.
            for key, state in list(self._callbacks.items()):
                if state == 'pending' and (Path(key[0]) / 'members' / f'{key[1]:04d}' / 'skipped.json').is_file():
                    self._callbacks.pop(key)
            return bool(self._callbacks)

    def _path(self, group_id: str, existing=True) -> Path:
        if not isinstance(group_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', group_id):
            raise ValueError('Expected a safe explicit parallel group ID')
        path = self.root / group_id
        if existing and not (path / 'group.json').exists():
            raise ValueError('Unknown parallel group')
        return path

    @staticmethod
    def _active(path: Path) -> bool:
        with (path / 'runner.lock').open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False

    def start(self, calls: list[dict], group_id: str, yield_ms: int = 0,
              effect_ids: list[str] | None = None) -> dict:
        if not isinstance(calls, list) or not calls:
            raise ValueError('A parallel group requires at least one explicit call')
        if type(yield_ms) is not int or yield_ms < 0:
            raise ValueError('yield_ms must be nonnegative')
        calls = json.loads(json.dumps(calls, allow_nan=False))
        if effect_ids is not None and (not isinstance(effect_ids, list) or len(effect_ids) != len(calls)
                or any(not isinstance(value, str) or not value for value in effect_ids)
                or len(set(effect_ids)) != len(calls)):
            raise ValueError('Every native call requires a distinct effect identity')
        for call in calls:
            if (not isinstance(call, dict) or set(call) != {'name', 'arguments'}
                    or not isinstance(call['name'], str) or not call['name']
                    or not isinstance(call['arguments'], dict)):
                raise ValueError('Each call requires a tool name and argument object')
            if call['name'] in FORBIDDEN:
                raise ValueError('Parallel groups cannot nest groups or perform session control: ' + call['name'])
        path = self._path(group_id, existing=False)
        with locked(self.root / 'groups.lock'):
            if path.exists():
                descriptor = read(path / 'group.json')
                if descriptor is None:
                    raise RuntimeError('Prior group creation is incomplete; inspect evidence rather than replay')
                if descriptor['calls'] != calls:
                    raise ValueError('Parallel group identity reused with different calls')
                if effect_ids is not None and [m['effect_id'] for m in descriptor['members']] != effect_ids:
                    raise ValueError('Parallel group identity reused with different effects')
                # A durable group is never redispatched, even when its owner died.
                return self.inspect(group_id)
            path.mkdir()
            lease = (path / 'runner.lock').open('a')
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            namespace = hashlib.sha256(str(self.root).encode()).hexdigest()
            members = []
            for index, call in enumerate(calls):
                member = path / 'members' / f'{index:04d}'
                member.mkdir(parents=True)
                effect_id = effect_ids[index] if effect_ids is not None else f'parallel:{namespace}:{group_id}:{index}'
                members.append({'index': index, 'effect_id': effect_id,
                    'job_id': hashlib.sha256(effect_id.encode()).hexdigest()[:32] if call['name'] == 'exec_command' else None})
            save(path / 'group.json', {'group_id': group_id, 'calls': calls, 'members': members,
                 'created_at': time.time(), 'creator_pid': os.getpid(), 'max_workers': self.max_workers or len(calls)})
            thread = threading.Thread(target=self._run, args=(path, lease), daemon=True,
                                      name='parallel-' + group_id)
            self.threads[group_id] = thread
            with self._callback_lock:
                self._callbacks.update({(str(path), index): 'pending' for index in range(len(calls))})
            thread.start()
        return self.wait(group_id, timeout=yield_ms / 1000)

    def _run(self, path: Path, lease) -> None:
        try:
            descriptor = read(path / 'group.json')
            pending = queue.Queue()
            for index in range(len(descriptor['calls'])):
                pending.put(index)
            def work():
                while True:
                    try:
                        index = pending.get_nowait()
                    except queue.Empty:
                        return
                    try:
                        self._execute(path, descriptor, index)
                    finally:
                        pending.task_done()
            workers = [threading.Thread(target=work, daemon=True) for _ in range(min(descriptor['max_workers'], len(descriptor['calls'])))]
            for thread in workers:
                thread.start()
            while any(thread.is_alive() for thread in workers):
                if (path / 'cancel-request.json').exists():
                    self._drive_cancel(path)
                for thread in workers:
                    thread.join(.025)
        except BaseException as error:
            save(path / 'runner-error.json', {'error': str(error), 'error_type': type(error).__name__,
                                             'at': time.time(), 'replay_safe': False})
        finally:
            # An owner that failed before dispatch cannot perform its queued
            # callbacks. Preserve their persisted not-started/unknown state;
            # active workers clear themselves only after saving their return.
            with self._callback_lock:
                for key, state in list(self._callbacks.items()):
                    if key[0] == str(path) and state == 'pending':
                        self._callbacks.pop(key)
            lease.close()

    def _execute(self, path: Path, descriptor: dict, index: int) -> None:
        key = (str(path), index)
        with self._callback_lock:
            self._callbacks[key] = 'executing'
        try:
            result = self._invoke(path, descriptor, index)
        finally:
            with self._callback_lock:
                self._callbacks.pop(key, None)
        # A returned background-job receipt is retained, while this group follows
        # the launched command to an observed exit. No command is run again.
        member = path / 'members' / f'{index:04d}'
        call, metadata = descriptor['calls'][index], descriptor['members'][index]
        if call['name'] == 'exec_command' and result and result.get('state') == 'running':
            while True:
                observed = self._job_observation(member, metadata['job_id'], call)
                if not observed or observed.get('state') != 'running':
                    break
                if (path / 'cancel-request.json').exists():
                    self._cancel_member(path, descriptor, index)
                time.sleep(.025)

    def _invoke(self, path: Path, descriptor: dict, index: int) -> dict | None:
        member = path / 'members' / f'{index:04d}'
        call, metadata = descriptor['calls'][index], descriptor['members'][index]
        try:
            executor = self.executor_factory()
        except Exception as error:
            save(member / 'result.json', {'executed': False, 'error': str(error), 'stage': 'executor_construction'})
            return
        with locked(path / 'group.lock'):
            if ((member / 'skipped.json').exists() or (path / 'cancel-request.json').exists()
                    or (path / 'dispatch-stop.json').exists()):
                if not (member / 'skipped.json').exists():
                    save(member / 'skipped.json', {'executed': False, 'reason': 'cancelled_before_start', 'at': time.time()})
                return
            save(member / 'intent.json', {**metadata, 'call': call, 'pid': os.getpid(),
                 'started_at': time.time(), 'workspace': getattr(executor, 'workspace', None)})
        try:
            result = executor(call['name'], call['arguments'], metadata['effect_id'])
            if not isinstance(result, dict):
                raise TypeError('Tool executor did not return an object')
        except BaseException as error:
            result = {'execution_state': 'unknown', 'replay_safe': False,
                      'error': str(error), 'error_type': type(error).__name__}
        save(member / 'result.json', result)
        return result

    def skip_unstarted(self, group_id: str) -> None:
        """Stop dispatch without signalling already launched callbacks or jobs."""
        path = self._path(group_id)
        with locked(path / 'group.lock'):
            save(path / 'dispatch-stop.json', {'at': time.time(), 'reason': 'session_interrupted'})
            descriptor = read(path / 'group.json')
            for index in range(len(descriptor['calls'])):
                member = path / 'members' / f'{index:04d}'
                if not any((member / name).exists() for name in ('intent.json', 'result.json', 'skipped.json')):
                    save(member / 'skipped.json', {'executed': False, 'state': 'not_executed',
                         'reason': 'session_interrupted_before_start', 'at': time.time()})

    def _job_observation(self, member: Path, job_id: str, call: dict) -> dict | None:
        intent = read(member / 'intent.json', {})
        workspace = intent.get('workspace')
        if not workspace or not (Path(workspace['state']) / 'jobs' / job_id / 'request.json').exists():
            return None
        try:
            return effects.observe(workspace, job_id, count=call['arguments'].get('max_output_bytes', 16000))
        except Exception as error:
            return {'id': job_id, 'state': 'unknown', 'error': str(error), 'replay_safe': False}

    def _member(self, path: Path, descriptor: dict, index: int, active: bool) -> dict:
        folder = path / 'members' / f'{index:04d}'
        call, metadata = descriptor['calls'][index], descriptor['members'][index]
        result, intent = read(folder / 'result.json'), read(folder / 'intent.json')
        skipped = read(folder / 'skipped.json')
        value = {'index': index, 'name': call['name'], **metadata,
                 'intent_path': str(folder / 'intent.json') if intent else None,
                 'result_path': str(folder / 'result.json') if result is not None else None}
        if skipped:
            value.update(state='cancelled_before_start', result=skipped)
        elif result is not None:
            value['result'] = result
            if result.get('execution_state') == 'unknown' or result.get('state') == 'unknown':
                value['state'] = 'unknown'
            elif result.get('executed') is False or result.get('applied') is False:
                value['state'] = 'failed'
            else:
                value['state'] = 'completed'
        elif intent:
            value.update(state='executing' if active else 'unknown', replay_safe=False,
                         message='Original effect receipt is unavailable; never replay this member' if not active else 'Effect entered; awaiting original receipt')
        else:
            value.update(state='pending' if active else 'not_started', executed=False)
        if call['name'] == 'exec_command':
            observed = self._job_observation(folder, metadata['job_id'], call)
            if observed:
                value['job_observation'] = observed
                if result is not None and result.get('state') == 'running':
                    value['state'] = 'completed' if observed['state'] == 'exited' else observed['state']
        cancellation = read(folder / 'cancel-result.json')
        if cancellation:
            value['cancellation'] = cancellation
        elif (folder / 'cancel-intent.json').exists():
            value['cancellation'] = {'state': 'unknown', 'replay_safe': False,
                                     'message': 'Cancellation request has no receipt; inspect the actual job'}
        elif (path / 'cancel-request.json').exists() and value['state'] in ('executing', 'running', 'unknown'):
            value['cancellation'] = {'requested': True, 'state': 'awaiting_job_launch' if metadata['job_id'] else 'not_cancelable',
                                     'message': 'A request does not establish that the effect stopped; await its receipt or inspect source'}
        return value

    def inspect(self, group_id: str) -> dict:
        path = self._path(group_id)
        descriptor, active = read(path / 'group.json'), self._active(path)
        members = [self._member(path, descriptor, i, active) for i in range(len(descriptor['calls']))]
        states = {m['state'] for m in members}
        cancel = read(path / 'cancel-request.json')
        if states & {'pending', 'executing', 'running'}:
            state = 'cancelling' if cancel else 'running'
        elif 'unknown' in states:
            state = 'unknown'
        elif 'cancelled_before_start' in states:
            state = 'cancelled'
        elif states & {'failed', 'not_started'}:
            state = 'completed_with_failures' if 'failed' in states else 'not_started'
        else:
            state = 'completed'
        return {'group_id': group_id, 'state': state, 'owner_active': active,
                'cancel_requested': cancel is not None, 'members': members,
                'archive': str(path), 'replay_safe': False,
                'runner_error': read(path / 'runner-error.json')}

    def list(self) -> list[dict]:
        summaries = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir() or not (path / 'group.json').exists():
                continue
            result = self.inspect(path.name)
            summaries.append({key: result[key] for key in ('group_id', 'state', 'owner_active', 'cancel_requested', 'archive')} |
                {'members': [{key: member.get(key) for key in ('index', 'name', 'state', 'job_id', 'result_path')}
                             for member in result['members']]})
        return summaries

    def wait(self, group_id: str, timeout: float | None = None) -> dict:
        if timeout is not None and timeout < 0:
            raise ValueError('timeout must be nonnegative')
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            result = self.inspect(group_id)
            if result['state'] not in ('running', 'cancelling') or (end is not None and time.monotonic() >= end):
                return result
            time.sleep(.025 if end is None else min(.025, max(0, end - time.monotonic())))

    def cancel(self, group_id: str, yield_ms: int = 1000) -> dict:
        if type(yield_ms) is not int or yield_ms < 0:
            raise ValueError('yield_ms must be nonnegative')
        path = self._path(group_id)
        with locked(path / 'group.lock'):
            if not (path / 'cancel-request.json').exists():
                save(path / 'cancel-request.json', {'id': uuid.uuid4().hex, 'requested_at': time.time(), 'signal': 'SIGTERM'})
            descriptor = read(path / 'group.json')
            for index in range(len(descriptor['calls'])):
                member = path / 'members' / f'{index:04d}'
                if not any((member / name).exists() for name in ('intent.json', 'result.json', 'skipped.json')):
                    save(member / 'skipped.json', {'executed': False, 'reason': 'cancelled_before_start', 'at': time.time()})
        self._drive_cancel(path)
        return self.wait(group_id, timeout=yield_ms / 1000)

    def _drive_cancel(self, path: Path) -> None:
        descriptor = read(path / 'group.json')
        for index in range(len(descriptor['calls'])):
            self._cancel_member(path, descriptor, index)

    def _cancel_member(self, path: Path, descriptor: dict, index: int) -> None:
        metadata, call = descriptor['members'][index], descriptor['calls'][index]
        if call['name'] != 'exec_command':
            return
        member = path / 'members' / f'{index:04d}'
        with locked(member / 'cancel.lock'):
            if (member / 'cancel-intent.json').exists():
                return
            intent = read(member / 'intent.json', {})
            workspace = intent.get('workspace')
            if not workspace:
                return
            job = Path(workspace['state']) / 'jobs' / metadata['job_id']
            if not (job / 'process.json').exists():
                return  # The scheduler revisits after the real job exists.
            observed = self._job_observation(member, metadata['job_id'], call)
            if not observed or observed.get('state') != 'running':
                return
            request = read(path / 'cancel-request.json')
            effect_id = metadata['effect_id'] + ':cancel:' + request['id']
            save(member / 'cancel-intent.json', {'effect_id': effect_id, 'requested_at': time.time(), 'job_id': metadata['job_id']})
            key = (str(path), index, 'cancel')
            with self._callback_lock:
                self._callbacks[key] = 'executing'
            try:
                try:
                    # Fresh runtime, deterministic control identity, real SIGTERM.
                    result = self.executor_factory()('job', {'id': metadata['job_id'], 'action': 'terminate',
                        'yield_time_ms': 0, 'max_output_bytes': 1}, effect_id)
                except BaseException as error:
                    result = {'execution_state': 'unknown', 'replay_safe': False, 'error': str(error)}
                save(member / 'cancel-result.json', {'requested': True, 'signal': 'SIGTERM', 'receipt': result})
            finally:
                with self._callback_lock:
                    self._callbacks.pop(key, None)

    def shutdown(self, wait: bool = True) -> list[dict]:
        """Joining is explicit; shutdown never cancels effects or kills jobs."""
        if wait:
            for thread in tuple(self.threads.values()):
                thread.join()
        return self.list()
