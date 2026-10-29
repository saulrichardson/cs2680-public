"""Shared admission for the course's nonstream Chat Completions route.

FIFO within each task, round-robin across tasks. Helpers and compactions use
their owner's task relay, so they share that owner's queue and the global cap.
The caller's lifetime is independent of the upstream worker. A logical identity
is scoped to a task and immutable request bytes; retries attach to that operation
or retrieve its result, never dispatch again. Only known completed service or a
known rejection releases capacity. Uncertain sent work remains occupied until
the enclosing server lifecycle ends; this module cannot prove remote cancellation.
When all slots are terminally unknown, waiting/new calls receive an explicit
unavailable result instead of waiting for a local worker that no longer exists.

This is deliberately not a resumable dispatcher. A nonempty archive refuses
construction, because replaying its unresolved intents would duplicate work.
No model parameters, request bodies, generation limits or prompts are changed.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import http.client
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _error(reason):
    message = ('All model slots belong to earlier requests with unknown outcomes. '
               'This request was not dispatched; earlier work was not cancelled or retried.'
               if reason == 'upstream_capacity_unknown' else
               'Model request did not establish a safe new response.')
    return json.dumps({'error': {'type': 'request_scheduler', 'code': reason, 'message': message}}).encode()


def _headers(state, content_type='application/json'):
    headers = {'Content-Type': content_type, 'X-A4-Relay-State': state}
    # A lost local reply to completed work may retrieve the same result using
    # its stable identity. Errors (including HTTP 200 envelopes) are terminal.
    if state != 'completed':
        headers['X-A4-No-Retry'] = '1'
    return headers


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: dict
    receipt: dict


class RequestRejected(Exception):
    """Local refusal, suitable for returning directly from ModelRelay."""
    def __init__(self, reason, receipt=None):
        super().__init__(reason)
        self.status, self.body = 409, _error(reason)
        self.headers, self.receipt = _headers('rejected'), receipt or {'reason': reason}


class _Handle:
    def __init__(self, scheduler, record, directory):
        self.scheduler, self.record, self.directory = scheduler, record, directory
        self.done = threading.Event()
        self.wire_status = None
        self.wire_headers = None
        self.local_body = None

    def result(self, timeout=None):
        """Wait only; timing out or abandoning this wait never cancels upstream."""
        if not self.done.wait(timeout):
            raise TimeoutError('Waiting caller stopped; model request remains owned by scheduler')
        with self.scheduler._condition:
            receipt = dict(self.record)
            status, headers, local = self.wire_status, dict(self.wire_headers), self.local_body
        body = local if local is not None else (self.directory / 'response.body').read_bytes()
        return Response(status, body, headers, receipt)


class RequestScheduler:
    def __init__(self, upstream, key, max_inflight, archive, *, connect_timeout=30):
        if type(max_inflight) is not int or max_inflight < 1:
            raise ValueError('max_inflight must be a positive integer')
        self._url = urlsplit(upstream.rstrip('/') + '/chat/completions')
        if self._url.scheme not in ('http', 'https') or not self._url.hostname or self._url.username or self._url.fragment:
            raise ValueError('Expected an HTTP(S) upstream base URL without credentials or fragment')
        self._key, self.max_inflight = key, max_inflight
        # Only connection establishment is timed. No new generation/read timer.
        self._connect_timeout = connect_timeout
        self.archive = Path(archive)
        self.archive.mkdir(parents=True, exist_ok=True)
        if any(self.archive.iterdir()):
            raise ValueError('Scheduler archive must be empty; prior dispatches are not replayable')
        (self.archive / 'requests').mkdir()
        (self.archive / 'refusals').mkdir()
        self._condition = threading.Condition()
        self._tasks, self._calls, self._by_sequence = {}, {}, {}
        self._rotation, self._held, self._active = deque(), set(), set()
        self._closed, self._archive_error = False, False
        self._refusals, self._arrivals = 0, 0
        self._dispatcher = threading.Thread(target=self._dispatch, name='model-admission', daemon=True)
        self._dispatcher.start()

    def _write(self, handle):
        path = handle.directory / 'receipt.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(handle.record, sort_keys=True, indent=2) + '\n')
        temporary.replace(path)

    def _event(self, event, **facts):
        with (self.archive / 'events.jsonl').open('a') as out:
            out.write(json.dumps({'event': event, 'time': time.time(), **facts}, sort_keys=True) + '\n')

    def register_task(self, task_id):
        if not isinstance(task_id, str) or not task_id:
            raise ValueError('task_id must be a nonempty string')
        with self._condition:
            if self._closed:
                raise RequestRejected('scheduler_closed')
            if task_id in self._tasks:
                if self._tasks[task_id]['finished']:
                    raise RequestRejected('task_already_finished')
                return
            self._event('task_registered', task_id=task_id)
            self._tasks[task_id] = {'finished': False, 'queue': deque()}

    def _reject(self, task_id, request_id, body, reason):
        self._refusals += 1
        directory = self.archive / 'refusals' / f'{self._refusals:06d}'
        directory.mkdir()
        if isinstance(body, bytes):
            (directory / 'request.body').write_bytes(body)
        receipt = {'state': 'rejected', 'reason': reason, 'received_at': time.time(),
                   'task_id': task_id, 'request_id_sha256': _sha(str(request_id).encode()),
                   'request_sha256': _sha(body) if isinstance(body, bytes) else None,
                   'upstream_dispatched': False}
        (directory / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
        self._event('rejected', **receipt)
        raise RequestRejected(reason, receipt)

    def _unavailable(self):
        """No local worker remains that could establish reusable capacity."""
        if self._archive_error:
            return 'scheduler_archive_failed'
        if len(self._held) >= self.max_inflight and not self._active:
            return 'upstream_capacity_unknown'
        return None

    def submit(self, task_id, request_id, body):
        with self._condition:
            self._arrivals += 1
            if not isinstance(task_id, str) or not isinstance(request_id, str) or not request_id or not isinstance(body, bytes):
                self._reject(task_id, request_id, body, 'missing_or_invalid_identity')
            key, digest = (task_id, request_id), _sha(body)
            existing = self._calls.get(key)
            if existing is not None:
                if digest != existing.record['request_sha256'] or body != (existing.directory / 'request.json').read_bytes():
                    self._reject(task_id, request_id, body, 'identity_body_conflict')
                self._event('attached', sequence=existing.record['sequence'], state=existing.record['state'])
                return existing
            if self._closed or self._archive_error:
                self._reject(task_id, request_id, body, 'scheduler_closed_or_archive_failed')
            unavailable = self._unavailable()
            if unavailable:
                self._reject(task_id, request_id, body, unavailable)
            task = self._tasks.get(task_id)
            if task is None or task['finished']:
                self._reject(task_id, request_id, body, 'task_not_open')
            try:
                parsed = json.loads(body)
                if not isinstance(parsed, dict) or parsed.get('stream', False) is not False:
                    raise ValueError('Nonstream object required')
            except (ValueError, UnicodeError):
                self._reject(task_id, request_id, body, 'nonstream_json_required')
            # A new UUID cannot turn a previously uncertain identical operation
            # into a safe retry. Known completed identical calls remain legal.
            if any(h.record['task_id'] == task_id and h.record['state'] == 'uncertain'
                   and h.record['request_sha256'] == digest for h in self._calls.values()):
                self._reject(task_id, request_id, body, 'same_body_has_uncertain_dispatch')
            sequence = len(self._calls) + 1
            directory = self.archive / 'requests' / f'{sequence:06d}'
            directory.mkdir()
            (directory / 'request.json').write_bytes(body)
            now = time.time()
            record = {'sequence': sequence, 'task_id': task_id, 'request_id_sha256': _sha(request_id.encode()),
                      'request_sha256': digest, 'request_bytes': len(body), 'state': 'queued',
                      'received_at': now, 'queued_at': now, 'started_at': None, 'sent_at': None,
                      'first_body_at': None, 'finished_at': None, 'upstream_status': None,
                      'response_complete': False, 'response_bytes': 0, 'response_sha256': None,
                      'prompt_tokens': None, 'completion_tokens': None, 'usage_known': False,
                      'reason': None, 'capacity_held': False}
            handle = _Handle(self, record, directory)
            self._write(handle)
            self._event('queued', sequence=sequence, task_id=task_id)
            self._calls[key] = handle
            self._by_sequence[sequence] = handle
            if not task['queue']:
                self._rotation.append(task_id)
            task['queue'].append(sequence)
            self._condition.notify_all()
            return handle

    def _dispatch(self):
        with self._condition:
            while True:
                while not self._closed and (self._archive_error or not self._rotation or len(self._held) >= self.max_inflight):
                    unavailable = self._unavailable()
                    if unavailable:
                        for task_id in list(self._rotation):
                            self._cancel_queued(task_id, unavailable)
                    self._condition.wait()
                if self._closed:
                    return
                task_id = self._rotation.popleft()
                queue = self._tasks[task_id]['queue']
                sequence = queue.popleft()
                if queue:
                    self._rotation.append(task_id)
                handle = self._by_sequence[sequence]
                self._held.add(sequence)
                self._active.add(sequence)
                handle.record.update(state='started', started_at=time.time(), capacity_held=True)
                try:
                    self._write(handle)
                    self._event('started', sequence=sequence, task_id=task_id)
                    threading.Thread(target=self._execute, args=(handle,),
                                     name=f'model-upstream-{sequence}', daemon=True).start()
                except Exception:
                    self._archive_error = True
                    self._settle(handle, 'failed', 'local_start_failed', 502, _error('local_start_failed'))

    @staticmethod
    def _classify(status, raw):
        """Service completion is distinct from the provider's action validation."""
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeError):
            parsed = None
        known_rejections = {400, 401, 403, 404, 405, 409, 413, 415, 422, 429}
        if status in known_rejections:
            return 'failed', 'upstream_rejected', parsed
        if not 200 <= status < 300 or not isinstance(parsed, dict):
            return 'uncertain', 'upstream_completion_unknown', parsed
        if parsed.get('error') is not None:
            error = parsed['error']
            code = error.get('code') if isinstance(error, dict) else None
            if str(code) in {str(value) for value in known_rejections}:
                return 'failed', 'upstream_rejected_envelope', parsed
            return 'uncertain', 'upstream_error_envelope', parsed
        return 'completed', 'complete_http_json', parsed

    def _execute(self, handle):
        connection, sent, raw_status, full = None, False, None, False
        raw_headers, count, digest = {}, 0, hashlib.sha256()
        state, reason, local = 'failed', 'connection_failed_before_send', None
        parsed = None
        try:
            cls = http.client.HTTPSConnection if self._url.scheme == 'https' else http.client.HTTPConnection
            connection = cls(self._url.hostname, self._url.port, timeout=self._connect_timeout)
            connection.connect()
            connection.sock.settimeout(None)
            path = self._url.path + ('?' + self._url.query if self._url.query else '')
            body = (handle.directory / 'request.json').read_bytes()
            with self._condition:
                # Persist the uncertain-send boundary BEFORE attempting a write.
                handle.record['sent_at'] = time.time()
                self._write(handle)
            sent = True
            connection.request('POST', path, body=body,
                               headers={'Content-Type': 'application/json', 'Accept-Encoding': 'identity',
                                        'Authorization': 'Bearer ' + self._key})
            response = connection.getresponse()
            raw_status = response.status
            for name in ('Content-Type', 'Content-Encoding'):
                if response.getheader(name) is not None:
                    raw_headers[name] = response.getheader(name)
            expected = response.length
            with (handle.directory / 'response.body').open('wb') as out:
                while True:
                    try:
                        chunk = response.read1(65536)
                    except http.client.IncompleteRead as error:
                        chunk = error.partial
                        if chunk:
                            out.write(chunk); digest.update(chunk); count += len(chunk)
                        raise
                    if not chunk:
                        break
                    if count == 0:
                        with self._condition:
                            handle.record['first_body_at'] = time.time()
                    out.write(chunk); digest.update(chunk); count += len(chunk)
                if expected is not None and count != expected:
                    raise http.client.IncompleteRead(b'', expected - count)
            full = True
            state, reason, parsed = self._classify(raw_status, (handle.directory / 'response.body').read_bytes())
        except Exception as error:
            state = 'uncertain' if sent else 'failed'
            reason = 'sent_transport_unknown' if sent else 'connection_failed_before_send'
            local = _error(reason)
            # Exception type is useful; its text may contain URLs or credentials.
            with self._condition:
                handle.record['exception_type'] = type(error).__name__
        finally:
            if connection is not None:
                connection.close()
            with self._condition:
                handle.record.update(upstream_status=raw_status, response_complete=full,
                                     response_bytes=count, response_sha256=digest.hexdigest())
                usage = parsed.get('usage') if isinstance(parsed, dict) else None
                for name in ('prompt_tokens', 'completion_tokens'):
                    value = usage.get(name) if isinstance(usage, dict) else None
                    handle.record[name] = value if type(value) is int and value >= 0 else None
                handle.record['usage_known'] = all(handle.record[n] is not None for n in ('prompt_tokens', 'completion_tokens'))
                if isinstance(parsed, dict) and isinstance(parsed.get('choices'), list):
                    handle.record['finish_reasons'] = [c.get('finish_reason') if isinstance(c, dict) else None
                                                       for c in parsed['choices']]
                headers = _headers(state, raw_headers.get('Content-Type', 'application/json'))
                if full and 'Content-Encoding' in raw_headers:
                    headers['Content-Encoding'] = raw_headers['Content-Encoding']
                # For a full response preserve the original status and bytes,
                # including 5xx/errors. A partial response is evidence, not JSON.
                status = raw_status if full else 409 if sent else 502
                self._settle(handle, state, reason, status, local, headers)

    def _settle(self, handle, state, reason, status, local_body=None, headers=None):
        sequence = handle.record['sequence']
        handle.record.update(state=state, reason=reason, finished_at=time.time(), capacity_held=state == 'uncertain')
        try:
            self._write(handle)
            self._event(state, sequence=sequence, task_id=handle.record['task_id'], reason=reason)
        except Exception:
            # A response without a durable terminal receipt cannot free a sent
            # operation. Preserve in-memory accounting and stop new dispatches.
            self._archive_error = True
            state = 'uncertain' if handle.record['sent_at'] is not None else 'failed'
            handle.record.update(state=state, reason='terminal_archive_failed', capacity_held=state == 'uncertain')
            local_body, status, headers = _error('terminal_archive_failed'), 409, _headers(state)
        self._active.discard(sequence)
        if state != 'uncertain':
            self._held.discard(sequence)
        handle.wire_status, handle.wire_headers, handle.local_body = status, headers or _headers(state), local_body
        handle.done.set()
        self._condition.notify_all()

    def _cancel_queued(self, task_id, reason='task_closed_before_dispatch'):
        task = self._tasks[task_id]
        self._rotation = deque(t for t in self._rotation if t != task_id)
        while task['queue']:
            handle = self._by_sequence[task['queue'].popleft()]
            self._settle(handle, 'failed', reason, 409, _error(reason))

    def _drain(self, task_id, wait, timeout):
        deadline = None if timeout is None else time.monotonic() + timeout
        def owned(sequences):
            return sorted(s for s in sequences if task_id is None or self._by_sequence[s].record['task_id'] == task_id)
        while wait and owned(self._active):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                break
            self._condition.wait(remaining)
        active, uncertain = owned(self._active), owned(self._held - self._active)
        return {'safe_to_refill': not active and not uncertain and not self._archive_error,
                'active': active, 'uncertain': uncertain, 'inflight': len(active) + len(uncertain),
                'archive_error': self._archive_error}

    def finish_task(self, task_id, wait=True, timeout=None):
        """Cancel queued work and drain actual workers; unknowns return unsafe.

        An optional timeout limits only the caller's wait, never model execution.
        Already uncertain terminal workers do not cause an unbounded wait.
        """
        with self._condition:
            if task_id not in self._tasks:
                raise ValueError('Task was not registered')
            self._tasks[task_id]['finished'] = True
            self._cancel_queued(task_id)
            result = self._drain(task_id, wait, timeout)
            self._event('task_finished', task_id=task_id, **result)
            return result

    def close(self, wait=True, timeout=None):
        with self._condition:
            self._closed = True
            for task_id, task in self._tasks.items():
                task['finished'] = True
                self._cancel_queued(task_id)
            self._condition.notify_all()
            result = self._drain(None, wait, timeout)
            self._event('closed', **result)
            return result

    def snapshot(self):
        with self._condition:
            return {'max_inflight': self.max_inflight, 'closed': self._closed,
                    'archive_error': self._archive_error, 'request_count': len(self._calls),
                    'arrival_count': self._arrivals, 'refusal_count': self._refusals,
                    'queued': sum(len(t['queue']) for t in self._tasks.values()),
                    'inflight': len(self._held), 'active_upstreams': len(self._active),
                    'unknown_count': len(self._held - self._active),
                    'task_count': len(self._tasks),
                    'open_tasks': sum(not t['finished'] for t in self._tasks.values()),
                    'requests': [dict(h.record) for h in self._by_sequence.values()]}
