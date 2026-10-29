"""Small deployment, authenticated inference, and exact-grading course boundary."""
from __future__ import annotations

import base64
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import re
from pathlib import Path
import secrets
import shlex
import socket
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request

from .capture import PatchCaptureError, capture_failure, decode_snapshot, retain_json, restored_test_code_paths
from .models import InfrastructureError, AccountingError, ProviderCapacityRejected

REPOSITORY_NAME_SCRIPT = r"""
import json, re
from pathlib import Path
def first(path, pattern):
    try:
        match = re.search(pattern, Path(path).read_text(errors='replace'), re.M)
    except OSError:
        return None
    return match.group(1).strip() if match else None
def package():
    try:
        name = json.loads(Path('package.json').read_text()).get('name')
    except (OSError, ValueError, AttributeError):
        return None
    return name if isinstance(name, str) else None
for value in (first('go.mod', r'^module\s+(\S+)'), package(),
              first('pyproject.toml', r'^name\s*=\s*["\']([^"\']+)'),
              first('setup.cfg', r'^name\s*=\s*(\S+)'),
              first('setup.py', r'name\s*=\s*["\']([^"\']+)')):
    if value:
        print(value[:200])
        break
"""


class Remote:
    def __init__(self, task: dict):
        self.task = dict(task)
        self.url = task['sandbox_url'].rstrip('/')
        with urllib.request.urlopen(self.url + '/health', timeout=30) as response:
            self.python = json.load(response)['python']
        self.base = None
        self.initial_workdir = task['workdir']
        self.runtime = '/tmp/a3'

    def post(self, route: str, data: dict, timeout: int = 960):
        request = urllib.request.Request(self.url + route, json.dumps(data).encode(),
                                         {'Content-Type': 'application/json'})
        # Bootstrap effects are never retried after an uncertain transport outcome.
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                detail = error.read(8192).decode(errors='replace')
            raise InfrastructureError(f'Sandbox {route} HTTP {error.code}: {detail}') from error

    def execute(self, command: str, timeout: int = 120) -> str:
        result = self.post('/exec', {'command': command, 'cwd': self.task['workdir'], 'timeout_s': timeout})
        if result['exit_code'] or result['timed_out']:
            raise RuntimeError(f'Bootstrap failed: {result}')
        return result['output']

    def repository_name(self):
        """Project name from the checkout's own manifest, or None. Never raises.

        The course hands over no repository name; this only groups cross-task
        memory notes from the same project.
        """
        try:
            result = self.post('/exec', {'command': shlex.join([self.python, '-I', '-B', '-c', REPOSITORY_NAME_SCRIPT]),
                                         'cwd': self.task['workdir'], 'timeout_s': 30}, timeout=60)
            name = '' if result.get('exit_code') or result.get('timed_out') else (result.get('output') or '').strip()
        except Exception:
            return None
        return name.splitlines()[0][:200] if name else None

    def deploy(self):
        stream = io.BytesIO()
        source = Path(__file__).parent
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for path in sorted(source.rglob('*')):
                if (path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'):
                    archive.add(path, arcname='src/' + path.relative_to(source).as_posix())
        self.post('/deploy', {'archive': base64.b64encode(stream.getvalue()).decode()})

    def activate_recovery(self, path: str):
        if path != self.initial_workdir:
            raise ValueError('Recovery must restore the original task workdir')
        self.runtime = '/tmp/a3/recovery-owner'

    def capture(self) -> dict:
        command = [self.python, '-I', '-B', '/tmp/a3/runtime/src/capture.py',
                   '--capture-artifact', self.task['workdir'], self.runtime]
        if self.base:
            command.append(self.base)
        value = json.loads(self.execute(shlex.join(command)))
        if 'capture_error' in value:
            raise PatchCaptureError(value['capture_error'])
        size, digest = value['bytes'], value['sha256']
        if type(size) is not int or size < 0:
            raise ValueError('Invalid capture transfer size')
        data = bytearray()
        while len(data) < size:
            command = [self.python, '-I', '-B', '/tmp/a3/runtime/src/capture.py',
                       '--read-artifact', value['path'], str(len(data)), str(min(1 << 20, size - len(data)))]
            chunk = base64.b64decode(self.execute(shlex.join(command)).strip(), validate=True)
            if not chunk or len(data) + len(chunk) > size:
                raise ValueError('Capture transfer incomplete or larger than its manifest')
            data.extend(chunk)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Capture transfer differs from its manifest')
        value = json.loads(data)
        decode_snapshot(value)
        self.base = value['base']
        return value

    def retain_graded(self, snapshot: dict, candidate_id: int, attempt: int, evaluation: dict) -> dict:
        """Copy the captured snapshot and completed course receipt into owned evidence."""
        supplied = f'{self.runtime}/graded-patches/candidate-{candidate_id}-attempt-{attempt}.input.json'
        self.post('/write', {'path': supplied, 'content': json.dumps({
            'snapshot': snapshot, 'candidate_id': candidate_id, 'attempt': attempt,
            'evaluation': evaluation})}, timeout=15)
        command = [self.python, '-I', '-B', '/tmp/a3/runtime/src/capture.py',
                   '--retain-graded', self.runtime, supplied]
        return json.loads(self.execute(shlex.join(command), timeout=10))

    def archive(self, destination: Path):
        request = urllib.request.Request(self.url + '/archive', b'{}', {'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=300) as response, destination.open('wb') as output:
            import shutil
            shutil.copyfileobj(response, output)

    def preserve_ungraded(self, reason: str) -> dict:
        command = [self.python, '-I', '-B', '/tmp/a3/runtime/src/capture.py',
                   '--preserve-ungraded', self.task['workdir'], self.runtime, reason]
        result = self.post('/exec', {'command': shlex.join(command), 'cwd': self.task['workdir'],
                                     'timeout_s': 12}, timeout=15)
        if result['exit_code'] or result['timed_out']:
            raise RuntimeError('Bounded recovery helper did not return a completed receipt')
        return json.loads(result['output'])

    def stop(self) -> dict:
        command = [self.python, '-I', '-B', '/tmp/a3-bootstrap/stop.py']
        result = self.post('/exec', {'command': shlex.join(command), 'cwd': self.task['workdir'],
                                     'timeout_s': 23}, timeout=26)
        if result['exit_code'] or result['timed_out']:
            raise RuntimeError('Bounded solver shutdown did not return a completed receipt')
        return json.loads(result['output'])


class Evaluations:
    """One owner of the selected exact patch; no grading replay after uncertainty."""
    def __init__(self, dispatcher, task: int, remote, root: Path):
        self.dispatcher, self.task, self.remote, self.root = dispatcher, task, remote, root
        self.root.mkdir(parents=True, exist_ok=True)
        self.last_grade = None
        self.last_patch = b''
        self.last_hash = None
        # Selected final bytes and latest course evaluation are different facts.
        # A completed recovery grade takes selection ownership, even if partial.
        self.latest_grade = None
        self.latest_hash = None
        self.candidates = {}
        self.recovery_started = False
        self.note_phase = False
        self.recovery_candidates = set()
        self.recovery_artifacts = {}
        self.uncertain = False
        self.capture_failed = False
        self.finalization = None
        self.count = 0
        self.candidate_count = 0
        self.continuation_ready = False
        self.pre_evaluation_failed = False
        self.lock = threading.Lock()
        self.selection_lock = threading.Lock()
        self.closing = False

    def freeze(self):
        """Stop selection changes without waiting for remote grading or disk I/O."""
        with self.selection_lock:
            self.closing = True
            return {'patch_sha256': hashlib.sha256(self.last_patch).hexdigest(),
                    'grade': copy.deepcopy(self.last_grade),
                    'candidate_id': self.last_grade.get('candidate_id') if self.last_grade else None,
                    'course_current_patch_sha256': self.latest_hash,
                    'course_current_grade': copy.deepcopy(self.latest_grade)}

    def require_open(self):
        if self.closing:
            raise InfrastructureError('Task grading admission closed; no further evaluation started')

    def _report_retention_failure(self, result: dict, name: str, error: str):
        detail = f'{name}: {error[:1000]}'
        previous = result.get('retention_error', '')
        warning = previous or ('Auxiliary report persistence failed; grading state and selected patch bytes '
                               'remain known in memory. No evaluation was repeated. ')
        if detail not in warning:
            warning += ('; ' if previous else '') + detail
        result['retention_error'] = warning if len(warning) <= 3000 else warning[:2900] + ' [Additional persistence diagnostics omitted.]'
        with self.selection_lock:
            if not self.closing and self.last_grade is not None and result.get('patch_sha256') == self.last_hash:
                self.last_grade['retention_error'] = result['retention_error']

    def grade(self, flags: dict) -> dict:
        if not isinstance(flags, dict):
            raise ValueError('Expected one task lifecycle operation')
        action = flags.get('action')
        if action in ('evaluate_candidate', 'end_attempt', 'finish_task'):
            field = {'end_attempt': 'reason', 'finish_task': 'memory_note'}.get(action)
            expected = {'action', field} if field else {'action'}
            if set(flags) != expected or field and (not isinstance(flags[field], str) or not flags[field].strip()):
                raise ValueError(f'Invalid {action} operation; no action ran')
        elif set(flags) in ({'cap'}, {'begin_recovery'}, {'begin_note'}):
            if next(iter(flags.values())) is not True:
                raise ValueError('Internal lifecycle flags must be true')
        elif set(flags) == {'recovery_workspace'} and isinstance(flags['recovery_workspace'], str):
            pass
        else:
            raise ValueError('Unknown task lifecycle operation')
        with self.lock:
            self.require_open()
            started = time.monotonic()
            if flags.get('begin_note'):
                return self.begin_note()
            if action == 'finish_task':
                return self.finish_memory(flags['memory_note'])
            if self.note_phase and not flags.get('cap'):
                raise ValueError('This owner has ended; finish_task saves its memory note without another grade')
            if flags.get('begin_recovery'):
                return self.begin_recovery()
            if 'recovery_workspace' in flags:
                if not self.recovery_started:
                    raise ValueError('No recovery owner has been admitted')
                self.remote.activate_recovery(flags['recovery_workspace'])
                return {'recovery_workspace': flags['recovery_workspace']}
            if (action in ('evaluate_candidate', 'end_attempt') and self.last_grade
                    and self.last_grade['tests_total'] > 0 and self.last_grade['tests_failed'] == 0):
                raise ValueError('The passing candidate is secured; finish_task saves the note without another grade')
            if flags.get('cap'):
                result = self._settle_budget()
            else:
                result = self.end_attempt(flags['reason']) if action == 'end_attempt' else self._grade()
            result['elapsed_seconds'] = time.monotonic() - started
            if result.get('tests_failed') is not None and not result.get('unchanged'):
                result.setdefault('grading_seconds', result['elapsed_seconds'])
                record = self.candidates.get(result.get('candidate_id'))
                if record is not None:
                    record['grade']['grading_seconds'] = result['grading_seconds']
                    if record['receipts']:
                        record['receipts'][-1]['grading_seconds'] = result['grading_seconds']
                with self.selection_lock:
                    if not self.closing and result.get('patch_sha256') == self.last_hash:
                        self.last_grade['grading_seconds'] = result['grading_seconds']
                name = f'grade-{result["attempt"] + 1}.json'
                error = retain_json(self.root / name, result)
                if error:
                    self._report_retention_failure(result, name, error)
            if flags.get('cap') and not self.closing:
                receipt = {'result': copy.deepcopy(result),
                           'selected_patch_sha256': hashlib.sha256(self.last_patch).hexdigest()}
                with self.selection_lock:
                    if not self.closing:
                        self.finalization = receipt
                error = retain_json(self.root / 'finalization.json', receipt)
                if error:
                    self._report_retention_failure(result, 'finalization.json', error)
                    with self.selection_lock:
                        if not self.closing and self.finalization is receipt:
                            receipt['result'] = copy.deepcopy(result)
            return self._owner_result(result) if self.recovery_started and not flags.get('cap') else result

    def _owner_result(self, result):
        """Expose this owner's candidate facts, never the private fallback record.

        Full grading/selection records were retained above in the agent container.
        Projection happens before RPC serialization, so a recovery-side receipt or
        raw response cannot recover information omitted from the rendered message.
        """
        public = copy.deepcopy(result)
        public.pop('selected_candidate', None)
        private = (public.get('candidate_id') is not None
                   and public['candidate_id'] not in self.recovery_candidates)
        if private or public.get('current_candidate_empty'):
            for key in ('candidate_id', 'patch_sha256', 'patch_bytes', 'graded_patch',
                        'tests_failed', 'tests_total', 'attempt', 'grading_seconds',
                        'capture_stability', 'restored_test_code_files', 'evaluation'):
                public.pop(key, None)
            public['message'] = ('The attempt ended; the harness retained its selected candidate. '
                                 'No previous-owner source or grading details are supplied.')
        elif public.get('patch_sha256') == self.last_hash:
            public['selected_candidate'] = self.selected()
        return public

    def begin_note(self) -> dict:
        """Open memory finishing under the same task allowance as solving."""
        if self.uncertain:
            raise InfrastructureError('Grading outcome uncertain; no memory phase starts')
        if not self.note_phase:
            self.note_phase = True
        return {'note_phase': True}

    def finish_memory(self, note: str) -> dict:
        if not self.note_phase:
            raise ValueError('A memory note is written after this owner has ended')
        passed = bool(self.last_grade and self.last_grade['tests_failed'] == 0
                      and self.last_grade['tests_total'] > 0 and self.last_hash == self.latest_hash)
        from .memory import save as save_memory
        course_task = getattr(self.remote, 'task', None) or {}
        public = public_task(course_task) if all(course_task.get(key) is not None for key in
                                                 ('problem_statement', 'requirements', 'interface')) else ''
        # A later owner's note replaces an earlier one for the same task.
        destination = save_memory(self.root.parent.parent / 'memory', self.task, course_task.get('repo'), note,
                                  self.last_grade or {}, public_task=public, patch=self.last_patch, passed=passed)
        result = {'terminal': True, 'unchanged': True, 'evaluation_started': False, 'passed': passed,
                  'memory_note_path': str(destination),
                  'message': ('Memory note saved; finished with the selected fully passing candidate.' if passed else
                              'Memory note saved with this task\'s unsuccessful result.') + ' No new grading was started.'}
        if passed:
            result.update({**self.last_grade, 'reason': 'pass', 'patch_sha256': self.last_hash,
                           'selected_candidate': self.selected()})
        self.note_phase = False
        try:
            self.remote.post('/write', {'path': '/tmp/a3/memory-note.md', 'content': note}, timeout=15)
            result['runtime_memory_note_path'] = '/tmp/a3/memory-note.md'
        except Exception as error:
            result['memory_note_archive_error'] = f'{type(error).__name__}: {error}'
        error = retain_json(self.root / 'memory-finish.json', result)
        if error:
            self._report_retention_failure(result, 'memory-finish.json', error)
        return result

    def selected(self) -> dict | None:
        if self.last_grade is None:
            return None
        return {'candidate_id': self.last_grade['candidate_id'], 'patch_sha256': self.last_hash,
                'attempt': self.last_grade['attempt'], 'tests_failed': self.last_grade['tests_failed'],
                'tests_total': self.last_grade['tests_total']}

    def begin_recovery(self) -> dict:
        """Export first-owner evidence before permitting its sandbox reset."""
        if self.recovery_started:
            raise ValueError('This task already admitted its one recovery owner')
        if (self.uncertain or self.last_grade is None
                or self.last_grade['tests_total'] > 0 and self.last_grade['tests_failed'] == 0):
            raise ValueError('Recovery requires a confirmed unsuccessful actual grade')
        fallback_candidate_id = self.last_grade['candidate_id']
        record = self.candidates[fallback_candidate_id]
        outgoing = self.remote.capture()
        outgoing_patch = decode_snapshot(outgoing)
        (self.root / 'outgoing-ungraded.patch').write_bytes(outgoing_patch)
        (self.root / 'outgoing-snapshot.json').write_text(json.dumps(outgoing))
        archive_path = self.root.parent / 'initial-owner.tar.gz'
        self.remote.archive(archive_path)
        # Read the complete archive before acknowledging export. A truncated
        # transport or corrupt tar leaves the original sandbox untouched.
        with tarfile.open(archive_path, 'r:gz') as archive:
            for member in archive:
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        while stream.read(1 << 20):
                            pass
        digest = hashlib.sha256()
        with archive_path.open('rb') as stream:
            for block in iter(lambda: stream.read(1 << 20), b''):
                digest.update(block)
        result = {'recovery_started': True, 'selected_candidate': self.selected(),
                  'fallback_grade': copy.deepcopy(self.last_grade),
                  'fallback_snapshot': copy.deepcopy(record['snapshot']),
                  'candidates': [self._candidate_receipt(item) for item in self.candidates.values()],
                  'outgoing_archive': {'path': str(archive_path), 'sha256': digest.hexdigest(),
                                       'bytes': archive_path.stat().st_size}}
        error = retain_json(self.root / 'recovery-start.json', result)
        if error:
            raise InfrastructureError('Recovery export receipt could not be retained: ' + error)
        self.recovery_started = True
        # The worker needs permission to reset and the base identity, not the
        # outgoing candidate, old receipts or an index into the private archive.
        return {'recovery_started': True, 'export_confirmed': True,
                'source_base': record['snapshot']['base']}

    @staticmethod
    def _candidate_receipt(record):
        snapshot = record['snapshot']
        return {'candidate_id': record['grade']['candidate_id'], 'patch_sha256': snapshot['sha256'],
                'base': snapshot['base'], 'patch_bytes': snapshot['bytes'],
                'grade': copy.deepcopy(record['grade']),
                'receipts': copy.deepcopy(record['receipts'])}

    def end_attempt(self, reason: str) -> dict:
        """Settle the owner's current candidate before routing its termination."""
        if self.uncertain:
            raise InfrastructureError('Grading outcome uncertain; ending cannot replay an unknown effect')
        result = self._grade()
        if result.get('reason') == 'infrastructure_error':
            return result
        if result.get('reason') == 'no_patch':
            # An unchanged source base is not a new repair. Keep any already
            # captured fallback rather than grading empty bytes to enable recovery.
            result = {**(self.last_grade or {}), 'unchanged': True,
                      'evaluation_started': False, 'current_candidate_empty': True}
        elif type(result.get('tests_failed')) is not int or result.get('preparation_error'):
            # Capture/preparation did not establish a candidate outcome. Let the
            # owner repair that concrete failure; never manufacture a give_up.
            return {**result, 'attempt_end_reason': reason}
        with self.selection_lock:
            self.require_open()
            passed = bool(self.last_grade and self.last_grade['tests_total'] > 0 and self.last_grade['tests_failed'] == 0)
            return {**result, 'terminal': True, 'reason': 'pass' if passed else 'give_up',
                    'attempt_end_reason': reason, 'selected_candidate': self.selected(),
                    'message': ('The captured candidate passed; finish with the memory note.' if passed else
                                'Attempt ended after candidate settlement. The selected captured candidate is retained.' if self.last_grade else
                                'Attempt ended at the unchanged source base; no candidate was graded and no recovery is eligible.')}

    def verify_cap(self, outcome: dict) -> dict:
        """Verify the solver's completed finalization without observing new bytes."""
        receipt = self.finalization
        if self.uncertain or receipt is None:
            raise InfrastructureError('Budget finalization receipt missing or uncertain; no capture or grading repeated')
        if (hashlib.sha256(self.last_patch).hexdigest() != receipt['selected_patch_sha256']
                or self.last_grade is not None and self.last_hash != receipt['selected_patch_sha256']
                or receipt['result'].get('reason') != outcome.get('reason')):
            raise InfrastructureError('Budget finalization receipt does not match selected bytes or termination reason')
        grade = outcome.get('grade')
        if self.last_grade is not None:
            if (not isinstance(grade, dict) or grade.get('patch_sha256') != self.last_hash
                    or any(grade.get(key) != self.last_grade.get(key) for key in ('tests_failed', 'tests_total', 'attempt'))):
                raise InfrastructureError('Runtime grade does not match the selected final patch')
        elif grade is not None:
            raise InfrastructureError('Runtime claims a grade absent from the finalization receipt')
        return copy.deepcopy(receipt)

    def _settle_budget(self) -> dict:
        """Settle current source once, without reopening an ended or passing owner."""
        if self.uncertain:
            raise RuntimeError('Previous grading outcome uncertain; automatic replay refused')
        passed = self.last_grade and self.last_grade['tests_total'] > 0 and self.last_grade['tests_failed'] == 0
        result = {} if self.note_phase or passed else self._grade()
        # Empty source, capture/preparation failure, or a historical receipt must
        # not replace the latest actual selection in the terminal outcome.
        selected = self.last_grade or {}
        return {**result, **selected, 'terminal': True,
                'reason': 'pass' if selected.get('tests_total', 0) > 0 and selected.get('tests_failed') == 0 else 'cap',
                'unchanged': not result.get('evaluation_started', False),
                'evaluation_started': result.get('evaluation_started', False),
                'selected_candidate': self.selected()}

    def _grade(self) -> dict:
        if self.uncertain:
            raise RuntimeError('Previous grading outcome uncertain; automatic replay refused')
        previous, hashes = None, []
        for _ in range(4):
            try:
                snapshot = self.remote.capture()
                decode_snapshot(snapshot)
            except Exception as error:
                self.capture_failed = True
                details = capture_failure(error)
                result = {'terminal': False,
                          'reason': 'capture_failed',
                          'evaluation_started': False, 'unchanged': True, 'capture_error': details,
                          'message': 'Evaluation not started. Patch capture failed. No new grade produced; previous grade unchanged.'}
                path = self.root / f'capture-failure-{time.time_ns()}.json'
                result['host_receipt_path'] = str(path)
                retention_error = retain_json(path, {**result, 'capture_error': capture_failure(error, limit=None)})
                if retention_error:
                    result['receipt_error'] = retention_error
                return result
            self.require_open()
            hashes.append(snapshot['sha256'])
            if previous and (snapshot['base'], snapshot['sha256']) == (previous['base'], previous['sha256']):
                break
            previous = snapshot
        else:
            self.capture_failed = True
            return {'tests_failed': None, 'tests_total': None, 'attempt': None,
                    'terminal': False, 'reason': 'unstable_patch',
                    'evaluation_started': False,
                    'capture_stability': {'consecutive_equal': False, 'atomic': False, 'observed_hashes': hashes},
                    'message': 'Repository patch changed between captures; no grading started. Inspect concurrent writers and evaluate_candidate again when the intended files are stable.'}
        self.capture_failed = False
        stability = {'consecutive_equal': True, 'atomic': False, 'observed_hashes': hashes,
                     'note': 'Two consecutive equal captures provide bounded evidence of stability, not an atomic filesystem snapshot.'}
        patch = decode_snapshot(snapshot)
        digest = snapshot['sha256']
        prior = next((record for record in self.candidates.values() if record['snapshot']['sha256'] == digest), None)
        if prior is not None:
            result_grade = copy.deepcopy(prior['grade'])
            if self.recovery_started:
                candidate_id = prior['grade']['candidate_id']
                self.recovery_candidates.add(candidate_id)
                # Recovery independently produced these exact bytes. Its own
                # artifact is readable even when the reusable grade came from
                # the initial owner; the old path was deliberately removed.
                if candidate_id not in self.recovery_artifacts:
                    try:
                        artifact = self.remote.retain_graded(
                            snapshot, candidate_id, prior['grade']['attempt'], prior['grade']['evaluation'])
                        if (not isinstance(artifact, dict) or artifact.get('sha256') != digest
                                or artifact.get('bytes') != snapshot['bytes']
                                or artifact.get('candidate_id') != candidate_id
                                or artifact.get('attempt') != prior['grade']['attempt']
                                or artifact.get('evaluation') != prior['grade']['evaluation']):
                            raise ValueError('Reused candidate artifact returned a different identity')
                        self.recovery_artifacts[candidate_id] = artifact
                    except Exception as error:
                        self.recovery_artifacts[candidate_id] = {
                            'status': 'error', 'candidate_id': candidate_id, 'sha256': digest,
                            'bytes': snapshot['bytes'], 'retention_error': str(error)}
                result_grade['graded_patch'] = self.recovery_artifacts[candidate_id]
            return {**result_grade, 'terminal': False,
                    'reason': 'unchanged',
                    'unchanged': True, 'evaluation_started': False, 'patch_sha256': digest,
                    'selected_candidate': self.selected()}
        if not patch:
            return {'tests_failed': None, 'tests_total': None, 'attempt': None,
                    'terminal': False,
                    'reason': 'no_patch',
                    'unchanged': False, 'patch_sha256': digest,
                    'message': 'No repository patch to grade; make a change before evaluate_candidate.'}
        result = self._evaluate_snapshot(snapshot, stability)
        if self.recovery_started and result.get('candidate_id') in self.candidates:
            self.recovery_candidates.add(result['candidate_id'])
            self.recovery_artifacts[result['candidate_id']] = result['graded_patch']
        return result

    def _evaluate_snapshot(self, snapshot: dict, stability: dict, *, candidate_id: int | None = None,
                           purpose: str = 'candidate_evaluation') -> dict:
        started = time.monotonic()
        patch, digest = decode_snapshot(snapshot), snapshot['sha256']
        existing = self.candidates.get(candidate_id)
        if candidate_id is None:
            self.candidate_count += 1
            candidate_id = self.candidate_count
        def preparation_failed(error, stage):
            self.pre_evaluation_failed = True
            details = capture_failure(error)
            return {'terminal': False, 'reason': 'submission_failed',
                    'evaluation_started': False, 'unchanged': True, 'candidate_id': candidate_id,
                    'candidate_sha256': digest, 'patch_bytes': len(patch), 'preparation_stage': stage,
                    'continuation_ready': self.continuation_ready, 'expected_course_attempt': self.count,
                    'preparation_error': details,
                    'message': f'Evaluation not started. Submission preparation failed at {stage}: '
                               f'{details["type"]}: {details["message"]}. Previous grade and selected patch unchanged. '
                               'No course evaluation attempt was consumed. '
                               + ('Course continuation already completed; it will not be repeated. ' if self.continuation_ready else
                                  'No course continuation completed for this candidate. ')
                               + f'The next evaluate_candidate reuses expected course attempt {self.count}; '
                                 'it will not skip an attempt because local candidate retention or submission writing failed.'}
        try:
            if existing is None:
                (self.root / f'candidate-{candidate_id}.patch').write_bytes(patch)
                (self.root / f'candidate-{candidate_id}.json').write_text(json.dumps({k:v for k,v in snapshot.items() if k != 'patch_base64'}, indent=2))
        except Exception as error:
            return preparation_failed(error, 'candidate_retention')
        self.require_open()
        if self.count and not self.continuation_ready:
            try:
                self.dispatcher.continue_task(self.task)
            except BaseException:
                self.uncertain = True
                raise
            self.continuation_ready = True
        self.require_open()
        try:
            path = Path(self.dispatcher.patch_path(self.task))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(patch)
        except Exception as error:
            return preparation_failed(error, 'submission_write')
        self.require_open()
        self.pre_evaluation_failed = False
        try:
            grade = self.dispatcher.evaluate(self.task)
            failed, total, attempt = (grade.get(k) for k in ('tests_failed', 'tests_total', 'attempt'))
            if (type(failed) is not int or type(total) is not int or type(attempt) is not int
                    or failed < 0 or total < 0 or failed > total or attempt != self.count):
                raise ValueError('Invalid course grading receipt')
        except BaseException:
            self.uncertain = True
            raise
        self.count += 1
        self.continuation_ready = False
        evaluation = {'status': 'completed', 'course_result': copy.deepcopy(grade),
                      'purpose': purpose, 'elapsed_seconds': time.monotonic() - started}
        artifact = {'status': 'unavailable', 'sha256': digest, 'bytes': len(patch),
                    'candidate_id': candidate_id, 'attempt': attempt}
        grade = {**grade, 'capture_stability': stability, 'candidate_id': candidate_id,
                 'patch_bytes': len(patch), 'graded_patch': artifact, 'evaluation_purpose': purpose,
                 'evaluation': evaluation}
        grade['patch_sha256'] = digest
        record = existing or {'snapshot': copy.deepcopy(snapshot), 'grade': grade, 'receipts': []}
        record['receipts'].append(copy.deepcopy(grade))
        self.candidates[candidate_id] = record
        self.latest_grade, self.latest_hash = grade, digest
        passed = total > 0 and failed == 0
        with self.selection_lock:
            if self.closing:
                return {**grade, 'unchanged': False, 'terminal': True, 'reason': 'infrastructure_error',
                        'patch_sha256': digest, 'selection_committed': False,
                        'message': 'Evaluation completed after boundary shutdown; its result is retained but the frozen selected submission was not changed.'}
            self.last_grade, self.last_patch, self.last_hash = grade, patch, digest
        expose_candidate = purpose == 'candidate_evaluation'
        if not expose_candidate:
            artifact = {**artifact, 'status': 'private',
                        'scope': 'Selected outgoing candidate retained by the course boundary; unavailable to this owner.'}
        if expose_candidate and hasattr(self.remote, 'retain_graded'):
            errors = []
            for transfer_attempt in range(2):
                try:
                    copied = self.remote.retain_graded(snapshot, candidate_id, attempt, evaluation)
                    if (not isinstance(copied, dict) or copied.get('sha256') != digest or copied.get('bytes') != len(patch)
                            or copied.get('candidate_id') != candidate_id or copied.get('attempt') != attempt
                            or copied.get('evaluation') != evaluation):
                        raise ValueError('Task-runtime graded patch copy returned a different identity')
                    artifact = {**copied, 'transfer_attempts': transfer_attempt + 1}
                    if errors:
                        artifact['transfer_errors'] = errors
                    break
                except Exception as error:
                    errors.append(f'{type(error).__name__}: {error}')
            else:
                artifact = {**artifact, 'status': 'error', 'retention_error': '; '.join(errors),
                            'transfer_attempts': len(errors)}
                artifact.pop('path', None)
        grade = {**grade, 'graded_patch': artifact,
                 'restored_test_code_files': restored_test_code_paths(snapshot.get('files', []))}
        grade['grading_seconds'] = time.monotonic() - started
        record['grade'] = grade
        record['receipts'][-1] = copy.deepcopy(grade)
        self.latest_grade = grade
        with self.selection_lock:
            if not self.closing and self.last_hash == digest:
                self.last_grade = grade
        reason = 'pass' if passed else 'continue'
        result = {**grade, 'unchanged': False, 'evaluation_started': True, 'terminal': passed,
                  'reason': reason, 'patch_sha256': digest, 'selected_candidate': self.selected()}
        error = retain_json(self.root / f'grade-{attempt + 1}.json', result)
        if error:
            self._report_retention_failure(result, f'grade-{attempt + 1}.json', error)
        return result

    def finish(self, reason: str, iterations: int) -> dict | None:
        if self.uncertain:
            # The course's done() can start grading an unevaluated patch. Let
            # its shutdown resolution own an uncertain attempt; never replay it.
            raise InfrastructureError('Previous grading outcome uncertain; dispatcher.done was not called')
        # A grade already admitted before shutdown may complete during draining.
        # It is the course's latest evaluated artifact, even though no new solver
        # action may be admitted. Reconcile that completed result, never regrade it.
        if self.latest_grade is not None:
            candidate = self.candidates[self.latest_grade['candidate_id']]
            self.last_patch = decode_snapshot(candidate['snapshot'])
            self.last_grade = copy.deepcopy(self.latest_grade)
            self.last_hash = self.latest_hash
        path = Path(self.dispatcher.patch_path(self.task))
        path.parent.mkdir(parents=True, exist_ok=True)
        # continue_task removes the course's evaluated flag. If the next capture
        # or submission failed, leave the patch empty so done cannot implicitly
        # evaluate abandoned bytes. The course retains its previous actual grade.
        path.write_bytes(b'' if self.continuation_ready else self.last_patch)
        return self.dispatcher.done(self.task, reason, iterations)


class OperationReceipts:
    """Retain results for two effectful RPCs; an unknown operation is never rerun."""
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock, self.receipts, self.events = threading.Lock(), {}, {}
        self.revoked = False
        for path in root.glob('*.json'):
            try:
                receipt = json.loads(path.read_text())
            except (OSError, ValueError):
                receipt = {'operation_id': path.stem, 'state': 'unknown'}
            if receipt.get('state') == 'pending':
                receipt['state'] = 'unknown'
                receipt['effect_started'] = None
                receipt['error'] = {'type': 'InfrastructureError', 'message': 'Process ended before a completed receipt was retained; automatic replay refused'}
            self.receipts[path.stem] = receipt

    @staticmethod
    def validate(identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier):
            raise ValueError('A stable operation_id of 1–128 letters, digits, underscores or hyphens is required')

    def save(self, receipt):
        path = self.root / (receipt['operation_id'] + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(receipt))
        temporary.replace(path)

    def lookup(self, identifier, wait_seconds=0):
        self.validate(identifier)
        if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float)) or not 0 <= wait_seconds <= 30:
            raise ValueError('wait_seconds must be between 0 and 30')
        with self.lock:
            event = self.events.get(identifier)
        if event:
            event.wait(wait_seconds)
        with self.lock:
            return copy.deepcopy(self.receipts.get(identifier, {'operation_id': identifier, 'state': 'missing'}))

    def perform(self, identifier, route, payload, action):
        self.validate(identifier)
        fingerprint = hashlib.sha256(json.dumps([route, payload], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self.lock:
            previous = self.receipts.get(identifier)
            if previous is not None:
                if previous.get('fingerprint') != fingerprint:
                    raise ValueError('operation_id already identifies another payload, or its prior identity is unknown; no action ran')
                return copy.deepcopy(previous)
            if self.revoked:
                raise InfrastructureError('Task effects revoked during boundary shutdown; no new inference or grading started')
            receipt = {'operation_id': identifier, 'state': 'pending', 'route': route,
                       'fingerprint': fingerprint, 'started': time.time(), 'effect_started': False}
            self.receipts[identifier] = receipt
            self.events[identifier] = threading.Event()
        try:
            self.save(dict(receipt))  # Must succeed before any effect starts; never hold the state lock during I/O.
        except Exception as error:
            with self.lock:
                receipt.update(state='failed', effect_started=False,
                    error={'type': 'InfrastructureError', 'message': 'Initial operation receipt could not be retained; no inference or grading started.'},
                    retention_error=type(error).__name__ + ': ' + str(error))
                self.events[identifier].set()
                return copy.deepcopy(receipt)
        with self.lock:
            if self.revoked:
                receipt.update(state='failed', effect_started=False,
                    error={'type': 'InfrastructureError', 'message': 'Task effects revoked before execution; no inference or grading started.'})
                self.events[identifier].set()
                result = copy.deepcopy(receipt)
            else:
                receipt['effect_started'] = True
                result = None
        if result is not None:
            self.retain_completion(identifier, result)
            return self.lookup(identifier)
        started = time.monotonic()
        try:
            result = action()
            update = {'state': 'completed', 'result': result}
        except Exception as error:
            update = {'state': 'failed', 'error': {'type': type(error).__name__, 'message': str(error)}}
            if isinstance(error, ProviderCapacityRejected):
                update['error']['details'] = {'kind': error.kind, 'response_path': error.response_path,
                    'supported_output_maximum': error.supported_output_maximum, 'detail': error.detail}
        with self.lock:
            receipt.update(update, elapsed_seconds=time.monotonic() - started,
                           completed_after_shutdown=self.revoked)
            self.events[identifier].set()
            completed = copy.deepcopy(receipt)
        self.retain_completion(identifier, completed)
        return self.lookup(identifier)

    def retain_completion(self, identifier, completed):
        try:
            self.save(completed)
        except Exception as error:
            # Completed results remain available in memory even if persistence
            # fails or stalls. A persisted pending receipt is unknown on restart.
            with self.lock:
                self.receipts[identifier]['retention_error'] = type(error).__name__ + ': ' + str(error)

    def revoke(self):
        with self.lock:
            self.revoked = True
            return [copy.deepcopy(receipt) for receipt in self.receipts.values()
                    if receipt.get('state') in ('pending', 'unknown')]

    def drain(self, deadline):
        while True:
            with self.lock:
                pending = [copy.deepcopy(receipt) for receipt in self.receipts.values()
                           if receipt.get('state') in ('pending', 'unknown')]
                events = [self.events[receipt['operation_id']] for receipt in pending
                          if receipt.get('state') == 'pending' and receipt['operation_id'] in self.events]
            remaining = deadline - time.monotonic()
            if not pending or not events or remaining <= 0:
                return pending
            events[0].wait(remaining)


class TaskServer:
    def __init__(self, gateway, evaluations: Evaluations, logger):
        self.gateway, self.evaluations, self.logger = gateway, evaluations, logger
        self.operations = OperationReceipts(evaluations.root.parent / 'operations')
        # A definite rate-limit wait remains owned by this task. Revoking its
        # effects prevents another physical attempt without stopping siblings.
        self.gateway.effects_revoked = lambda: self.operations.revoked
        self.token = secrets.token_urlsafe(32)
        self.auxiliary_lock, self.auxiliary_handlers = threading.Lock(), {}
        self.auxiliary_sequence = 0
        self.auxiliary_revoked = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, value):
                raw = json.dumps(value).encode()
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Completed operation receipt already exists for lookup.

            def do_POST(self):
                if self.headers.get('Authorization') != 'Bearer ' + owner.token:
                    self.reply(401, {'error': 'Unauthorized task gateway'})
                    return
                try:
                    value = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                    if self.path in ('/complete', '/grade'):
                        identifier = value.pop('operation_id', None)
                        def action():
                            if self.path == '/complete':
                                return owner.gateway.complete(value['request'])
                            control = getattr(owner.gateway, 'run_control', None)
                            task_id = getattr(owner.gateway, 'task_id', None)
                            if control is not None and task_id is not None:
                                if value.get('begin_recovery'):
                                    control.require_task(task_id)
                            return owner.evaluations.grade(value)
                        result = owner.operations.perform(identifier, self.path, value, action)
                    elif self.path == '/state':
                        result = owner.gateway.state()
                    elif self.path == '/operation':
                        result = owner.operations.lookup(value.get('operation_id'), value.get('wait_seconds', 0))
                    elif self.path == '/event':
                        method = value['method']
                        if method not in ('tool_call', 'tool_result'):
                            raise ValueError('Only tool call/result events are accepted from the solver')
                        with owner.auxiliary_lock:
                            if owner.auxiliary_revoked:
                                raise InfrastructureError('Task trace admission closed; no new logging delivery started')
                            owner.auxiliary_sequence += 1
                            identifier = f'event-{owner.auxiliary_sequence}'
                            owner.auxiliary_handlers[identifier] = {'handler_id': identifier,
                                'route': '/event', 'method': method, 'started': time.time()}
                        try:
                            getattr(owner.logger, method)(**value['fields'])
                        finally:
                            with owner.auxiliary_lock:
                                owner.auxiliary_handlers.pop(identifier)
                        result = {'logged': True}
                    else:
                        self.reply(404, {'error': 'Unknown task gateway route'})
                        return
                    self.reply(200, result)
                except Exception as error:
                    # Error bodies never include headers, credentials, or raw request objects.
                    name = type(error).__name__
                    self.reply(429 if name == 'BudgetExceeded' else 400 if name == 'CapacityError' else 500,
                               {'error': str(error), 'type': name})

        self.server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.close_receipt = None
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.05), daemon=True)
        self.thread.start()

    def close(self, drain_seconds=2.0):
        if isinstance(drain_seconds, bool) or not isinstance(drain_seconds, (int, float)) or not 0 <= drain_seconds <= 30:
            raise ValueError('drain_seconds must be between 0 and 30')
        if self.close_receipt is not None:
            return copy.deepcopy(self.close_receipt)
        started = time.monotonic()
        admitted = self.operations.revoke()
        with self.auxiliary_lock:
            self.auxiliary_revoked = True
        selected = self.evaluations.freeze()
        stopper = threading.Thread(target=self.server.shutdown, daemon=True)
        stopper.start()
        pending = self.operations.drain(started + drain_seconds)
        stopper.join(timeout=.1)
        self.server.server_close()
        self.thread.join(timeout=.1)
        with self.auxiliary_lock:
            auxiliary = list(self.auxiliary_handlers.values())
        self.close_receipt = {'admission_revoked': True, 'drain_seconds': drain_seconds,
            'drained': not pending, 'pending_operations_at_close': admitted, 'pending_operations': pending,
            'drain_scope': 'inference_and_grading', 'unresolved_auxiliary_handlers': auxiliary,
            'server_thread_stopped': not self.thread.is_alive(), 'elapsed_seconds': time.monotonic() - started,
            'frozen_selection': selected,
            'note': 'Pending effects are not canceled or replayed. Late completions may update their operation receipts, never the frozen selected submission.'}
        return copy.deepcopy(self.close_receipt)


def public_task(task: dict) -> str:
    return '\n\n'.join('## ' + title + '\n' + task[key] for title, key in (
        ('Problem statement', 'problem_statement'), ('Requirements', 'requirements'), ('Interface', 'interface')))


def bootstrap_sources(python: str, runtime='/tmp/a3', bootstrap='/tmp/a3-bootstrap') -> dict:
    """Private isolated imports; original Python environment survives for tools."""
    runtime, bootstrap = Path(runtime), Path(bootstrap)
    child = ("import runpy,signal,sys\n"
             f"sys.path.insert(0, {str(runtime / 'runtime')!r})\n"
             "from src.models import InfrastructureError\n"
             "def cancel(signum,frame):\n"
             " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
             " raise InfrastructureError('Course boundary canceled the investigation; new effects are revoked')\n"
             "signal.signal(signal.SIGTERM,cancel)\n"
             f"sys.argv=['src.agent', {str(bootstrap / 'launch.json')!r}]\n"
             "runpy.run_module('src.agent', run_name='__main__')\n")
    launcher = f'''import json,os,pathlib,signal,subprocess,time
p=pathlib.Path({str(runtime)!r})
env={{k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('API_KEY','TOKEN','SECRET'))}}
with (p/'solver.stdout').open('wb') as out,(p/'solver.stderr').open('wb') as err:
 child=subprocess.Popen([{python!r},'-I','-B',{str(bootstrap / 'solver.py')!r}],stdout=out,stderr=err,env=env,start_new_session=True)
 (p/'pid').write_text(str(child.pid))
 deadline=None
 forced=False
 while True:
  try:
   code=child.wait(timeout=0.1)
   break
  except subprocess.TimeoutExpired:
   if deadline is None and (p/'stop-request').exists():
    child.send_signal(signal.SIGTERM)
    deadline=time.monotonic()+15
   elif deadline is not None and time.monotonic()>deadline:
    child.kill()
    forced=True
(p/'process-status.json').write_text(json.dumps({{'exit_code':code,'forced':forced,'cancellation_requested':deadline is not None}}))
'''
    stop = f'''import json,pathlib,time
p=pathlib.Path({str(runtime)!r})
(p/'stop-request').write_text('Boundary shutdown requested; do not start new effects.')
deadline=time.monotonic()+20
receipt={{'cleanup_confirmed':False,'note':'Solver or owned-job cleanup remains unknown; no atomic snapshot is claimed.'}}
while time.monotonic()<deadline:
 try:
  status=json.loads((p/'process-status.json').read_text())
  receipt.update(status)
  outcome=json.loads((p/'outcome.json').read_text())
  clean=status['exit_code']==0 and not status.get('forced') and outcome.get('owned_work_cleanup',{{}}).get('confirmed') is True
  receipt.update(cleanup_confirmed=clean,outcome=outcome)
  if clean:
   receipt['note']='Solver exited after registered-job cleanup; unrelated processes and concurrent filesystem changes are not covered.'
  break
 except (OSError,ValueError,KeyError):
  if 'exit_code' in receipt:
   break
  time.sleep(0.1)
print(json.dumps(receipt))
'''
    return {'solver.py': child, 'launcher.py': launcher, 'stop.py': stop}


def finish_task(remote, server, evaluations, gateway, root, reason, iterations, outcome, *, interrupted=None, worker_started=True, dispatcher=None, task=None):
    """Finalize one boundary task; recover evidence without replaying effects."""
    if interrupted is not None:
        outcome['interruption'] = type(interrupted).__name__
    pending = server.operations.revoke() if server is not None else []
    if evaluations is not None:
        outcome['selected_submission_at_shutdown'] = evaluations.freeze()
        selected = outcome['selected_submission_at_shutdown']
        outcome['selected_candidate'] = ({'candidate_id': selected['candidate_id'],
            'patch_sha256': selected['patch_sha256'],
            **{key: selected['grade'][key] for key in ('attempt', 'tests_failed', 'tests_total')}}
            if selected.get('grade') else None)
    outcome['pending_operations_at_shutdown'] = pending
    if pending:
        reason = 'infrastructure_error'
    if reason not in ('infrastructure_error', 'shared_stop') and not (isinstance(outcome.get('owned_work_cleanup'), dict)
            and outcome['owned_work_cleanup'].get('confirmed') is True):
        outcome['boundary_error'] = 'Worker outcome lacks a confirmed owned-job cleanup receipt; bounded shutdown required.'
        reason = 'infrastructure_error'
    outcome.update(reason=reason, iterations=iterations, official=None,
                   budget={'unavailable': True}, finalization_stage='shutdown_started')
    failure = retain_json(root / 'official-outcome.json', outcome)
    if failure:
        outcome['outcome_retention_error'] = failure
    if reason in ('infrastructure_error', 'shared_stop'):
        try:
            cleanup = remote.stop() if worker_started else {'cleanup_confirmed': True, 'note': 'No solver launch was attempted.'}
            worker_outcome = cleanup.pop('outcome', {})
            boundary_outcome = dict(outcome)
            outcome.update(worker_outcome)
            outcome.update(boundary_outcome)
            if reason == 'shared_stop' and type(worker_outcome.get('iterations')) is int:
                outcome['iterations'] = worker_outcome['iterations']
            if cleanup.get('cleanup_confirmed') is True and isinstance(worker_outcome.get('owned_work_cleanup'), dict):
                outcome['owned_work_cleanup'] = worker_outcome['owned_work_cleanup']
            outcome['boundary_cleanup'] = cleanup
            iterations = outcome.get('iterations', iterations)
        except Exception as error:
            outcome['boundary_cleanup'] = {'cleanup_confirmed': False,
                'error': (type(error).__name__ + ': ' + str(error))[:2300]}
        if 'ungraded_final_candidate' not in outcome:
            try:
                if not outcome['boundary_cleanup']['cleanup_confirmed']:
                    raise InfrastructureError('Owned writers could not be confirmed stopped; recovery capture was not attempted')
                outcome['ungraded_final_candidate'] = remote.preserve_ungraded(reason)
            except Exception as error:
                outcome['ungraded_final_candidate'] = {
                    'status': 'unknown', 'ungraded': True, 'reason': reason,
                    'error': (type(error).__name__ + ': ' + str(error))[:2300],
                    'note': 'Bounded recovery delivery unavailable; any completed child artifact may remain in the runtime archive. No capture or grading replay attempted.'}
        failure = retain_json(root / 'ungraded-final-capture.json', outcome['ungraded_final_candidate'])
        if failure:
            outcome['ungraded_final_candidate']['boundary_receipt_error'] = failure
    finalization_error = None
    try:
        if server is not None:
            outcome['boundary_shutdown'] = server.close()
    except Exception as error:
        finalization_error = error
        outcome['boundary_shutdown_error'] = f'{type(error).__name__}: {error}'
    if hasattr(gateway, 'close_reporting'):
        try:
            outcome['gateway_trace_delivery'] = gateway.close_reporting(wait_seconds=.2)
        except Exception as error:
            outcome['gateway_trace_delivery'] = {'complete': False, 'error': f'{type(error).__name__}: {error}'}
    worker_trace = outcome.get('trace_delivery')
    gateway_trace = outcome.get('gateway_trace_delivery')
    auxiliary = outcome.get('boundary_shutdown', {}).get('unresolved_auxiliary_handlers')
    outcome['course_trace_complete'] = (
        isinstance(worker_trace, dict) and worker_trace.get('complete') is True
        and isinstance(gateway_trace, dict) and gateway_trace.get('complete') is True
        and isinstance(auxiliary, list) and not auxiliary)
    outcome['course_trace_scope'] = 'api_request, tool_call, tool_result; excludes scaffold-owned run_end/close'
    unfinished = server.operations.revoke() if server is not None else []
    outcome['pending_operations_at_finalization'] = unfinished
    # Pending inference cannot change the captured candidate. Pending grading
    # must settle before course done(), which rejects a still-running evaluation.
    # Preserve pending/unknown operations in evidence; they are never replayed.
    outcome.update(reason=reason, finalization_stage='effects_closed')
    failure = retain_json(root / 'official-outcome.json', outcome)
    if failure:
        outcome['outcome_retention_error'] = failure
    try:
        if reason in ('infrastructure_error', 'shared_stop') and not outcome.get('boundary_cleanup', {}).get('cleanup_confirmed'):
            outcome['archive_note'] = 'Best-effort archive while registered-job cleanup remained unknown; concurrent writes may make this archive incomplete or internally inconsistent.'
        remote.archive(root / 'task-artifacts.tar.gz')
    except Exception as error:
        outcome['archive_error'] = f'{type(error).__name__}: {error}'
    official, budget = None, {'unavailable': True}
    try:
        if gateway is not None:
            pending_before_budget = server.operations.revoke() if server is not None else []
            budget = gateway.state()
            pending_after_budget = server.operations.revoke() if server is not None else []
            if pending_before_budget or pending_after_budget:
                budget = {**budget, 'snapshot_while_operations_pending': True}
    except Exception as error:
        budget['error'] = f'{type(error).__name__}: {error}'
        finalization_error = finalization_error or error
    try:
        if server is not None:
            for receipt in server.operations.revoke():
                if receipt.get('route') != '/grade':
                    continue
                while receipt.get('state') == 'pending':
                    # The course owns its deadline and already-admitted grading.
                    # Stop waiting if it has taken over shutdown, not on an
                    # invented local grading timeout.
                    try:
                        from dispatcher import SEQ_DIR
                    except ImportError:
                        SEQ_DIR = None
                    if SEQ_DIR and (Path(SEQ_DIR) / 'shutdown').exists():
                        raise InfrastructureError('Course shutdown while admitted grading was pending')
                    receipt = server.operations.lookup(receipt['operation_id'], wait_seconds=1)
                if receipt.get('state') == 'unknown':
                    raise InfrastructureError('Grading delivery remains unknown; no operation was replayed')
            outcome['pending_operations_at_finalization'] = server.operations.revoke()
        if evaluations is None:
            if dispatcher is None or task is None:
                raise InfrastructureError('Acquired task identity unavailable for finalization')
            path = Path(dispatcher.patch_path(task))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'')
            official = dispatcher.done(task, reason, iterations)
        else:
            if reason == 'cap':
                outcome['cap_finalization'] = evaluations.verify_cap(outcome)
            official = evaluations.finish(reason, iterations)
        outcome['official_status'] = 'released'
        outcome['selected_candidate'] = evaluations.selected() if evaluations is not None else None
        if evaluations is None or evaluations.last_grade is None:
            outcome['official_note'] = ('No agent-evaluated candidate; finalized with an empty patch. '
                'The course may perform its empty-patch evaluation while releasing the task.')
    except Exception as error:
        finalization_error = error
        outcome['finalization_error'] = f'{type(error).__name__}: {error}'
        reason = 'infrastructure_error'
    if finalization_error is not None:
        reason = 'infrastructure_error'
    outcome.update(reason=reason, iterations=iterations, official=official, budget=budget,
                   finalization_stage='failed' if finalization_error is not None else 'complete')
    failure = retain_json(root / 'official-outcome.json', outcome)
    if failure:
        outcome['outcome_retention_error'] = failure
    if finalization_error is not None and interrupted is None:
        raise finalization_error  # Original scaffold owns unresolved shutdown.
    return official


def solve(dispatcher, task: dict, profiles: dict, *, run_control=None):
    from .models import Gateway
    root = Path('madsOpt_logs/expressive') / str(task['task'])
    remote = gateway = evaluations = server = None
    worker_started = False
    reason, iterations, outcome = 'infrastructure_error', 0, {}
    try:
        root.mkdir(parents=True, exist_ok=False)
        evaluations = Evaluations(dispatcher, task['task'], None, root / 'grades')
        remote = Remote(task)
        evaluations.remote = remote
        remote.deploy()
        initial = remote.capture()
        remote.post('/write', {'path': '/tmp/a3/capture-base.txt', 'content': initial['base']})
        logger = dispatcher.logger(task['task'])
        gateway = Gateway(os.environ['CS2680_BASE_URL'], os.environ['CS2680_API_KEY'], profiles,
                          root / 'model', lambda method, fields: getattr(logger, method)(**fields),
                          task_id=task["task"] if run_control is not None else None,
                          **({'run_control': run_control} if run_control is not None else {}))
        if not task.get('repo'):
            # The course hands over no repository name; memory grouping reads it from the checkout.
            task['repo'] = remote.repository_name()
            if isinstance(getattr(remote, 'task', None), dict):
                remote.task['repo'] = task['repo']
        memory_root = root.parent / 'memory'
        from .memory import prepare as prepare_memory
        memory = prepare_memory(remote, memory_root, task.get('repo'))
        server = TaskServer(gateway, evaluations, logger)
        # Find the source address actually used toward this sandbox's private network.
        from urllib.parse import urlsplit
        host = socket.gethostbyname(urlsplit(task['sandbox_url']).hostname)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((host, 8000))
            address = probe.getsockname()[0]
        config = {'task': public_task(task), 'root': task['workdir'], 'runtime': '/tmp/a3',
                  'gateway_url': f'http://{address}:{server.server.server_port}', 'token': server.token,
                  'profiles': profiles, 'memory': memory}
        remote.post('/write', {'path': '/tmp/a3-bootstrap/launch.json', 'content': json.dumps(config)})
        # The launcher owns process status; task-local agent owns coding tools/jobs.
        for name, source in bootstrap_sources(remote.python).items():
            remote.post('/write', {'path': '/tmp/a3-bootstrap/' + name, 'content': source})
        worker_started = True  # A lost launch response may still have started the child.
        remote.execute('nohup ' + shlex.quote(remote.python) + ' -I -B /tmp/a3-bootstrap/launcher.py > /tmp/a3/launcher.log 2>&1 < /dev/null &')
        while True:
            from dispatcher import SEQ_DIR, DispatcherShutdown
            if run_control is not None and run_control.stop_event.is_set():
                # This is an intentional submission-wide stop, not a model
                # give_up or an independent infrastructure failure in this task.
                # finish_task still cancels and reaps this worker before export.
                reason = 'shared_stop'
                outcome['shared_stop'] = {'reason': run_control.stop_reason or 'Submission stopping',
                                          'observed_at': time.time()}
                break
            # Monetary admission belongs to the worker's next inference. The
            # last admitted response still owns its actions and final settlement.
            if (Path(SEQ_DIR) / 'shutdown').exists():
                raise DispatcherShutdown('Course shutdown while solver was active')
            status = json.loads(remote.execute('if test -f /tmp/a3/process-status.json; then cat /tmp/a3/process-status.json; else echo null; fi'))
            if status is not None:
                break
            time.sleep(1)
        if reason != 'shared_stop':
            text = remote.execute('if test -f /tmp/a3/outcome.json; then cat /tmp/a3/outcome.json; else echo null; fi')
            outcome = json.loads(text) or {}
            reason = outcome.get('reason', reason)
            iterations = outcome.get('iterations', gateway.state().get('calls', 0))
            if status['exit_code']:
                reason = 'infrastructure_error'
    except Exception as error:
        if isinstance(error, AccountingError) and run_control is not None:
            run_control.stop(f'Shared accounting failure: {error}')
        outcome['boundary_error'] = f'{type(error).__name__}: {error}'
        reason = 'infrastructure_error'
    finally:
        official = finish_task(remote, server, evaluations, gateway, root,
                               reason, iterations, outcome, interrupted=sys.exc_info()[1], worker_started=worker_started,
                               dispatcher=dispatcher, task=task['task'])
    return official
