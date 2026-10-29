"""Exact Git patch capture, adapted from our prior candidate.py at 620edbb.

Source: assignment3/dev/jcode_adapter/vendor/src/meta_harness/candidate.py,
course-components revision 3aca9de. No separate source license was supplied.
The copied-index timestamp preservation is a deliberate correctness invariant.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def capture(root: str | Path, base: str | None = None) -> dict:
    def git(*args: str, env=None) -> bytes:
        return subprocess.check_output(['git', *args], cwd=root, env=env, stderr=subprocess.PIPE)
    base = base or git('rev-parse', 'HEAD').decode().strip()
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', base):
        raise ValueError('Expected full acquired commit identity')
    with tempfile.TemporaryDirectory(prefix='a3-index-') as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / 'index'))
        index = Path(os.fsdecode(git('rev-parse', '--git-path', 'index')).strip())
        if not index.is_absolute():
            index = Path(root) / index
        if index.exists():
            shutil.copy2(index, env['GIT_INDEX_FILE'])
        else:
            git('read-tree', base, env=env)
        git('add', '-A', '--', '.', env=env)
        data = git('diff', '--cached', '--binary', '--full-index', '--no-ext-diff',
                   '--no-textconv', '--no-renames', base, '--', '.', env=env)
        names = git('diff', '--cached', '--no-renames', '--name-only', '-z', base, '--', '.', env=env)
    return {'base': base, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
            'files': [os.fsdecode(n) for n in names.split(b'\0') if n],
            'patch_base64': base64.b64encode(data).decode()}


def restored_test_code_paths(paths: list[str]) -> list[str]:
    """Changed paths that look like test code by common naming conventions.

    A presentation heuristic for the public fact that the verifier restores
    original test code and applies its own tests. It does not describe hidden
    test paths or contents, and it is not a statement of the verifier's rules.
    """
    code = {'tests', 'test', '__tests__', 'spec', 'specs'}
    data = {'testdata', 'test_data', 'fixtures', 'fixture', 'mocks', 'mock', '__snapshots__'}
    restored = []
    for path in paths:
        *parents, name = path.split('/')
        if any(parent in data for parent in parents):
            continue
        if (any(parent in code for parent in parents) or name == 'conftest.py'
                or re.search(r'_test\.(go|py|js|jsx|ts|tsx)$', name)
                or re.search(r'^test_.*\.py$', name)
                or re.search(r'\.(test|spec)\.(js|jsx|ts|tsx)$', name)):
            restored.append(path)
    return restored


def decode_snapshot(value: dict) -> bytes:
    data = base64.b64decode(value['patch_base64'], validate=True)
    if hashlib.sha256(data).hexdigest() != value['sha256'] or len(data) != value['bytes']:
        raise ValueError('Exact patch identity or byte count mismatch')
    return data


def retain_json(path: str | Path, value: dict) -> str | None:
    """Retain a diagnostic receipt without suppressing the outcome in memory."""
    try:
        Path(path).write_text(json.dumps(value, indent=2))
    except (OSError, ValueError, TypeError) as error:
        return (type(error).__name__ + ': ' + str(error))[:2300]
    return None


def retain_graded_snapshot(runtime: str | Path, snapshot: dict, candidate_id: int,
                           attempt: int, evaluation: dict) -> dict:
    """Retain captured bytes and their completed evaluation, never current source."""
    if type(candidate_id) is not int or candidate_id < 1 or type(attempt) is not int or attempt < 0:
        raise ValueError('Graded patch identity requires a positive candidate ID and nonnegative course attempt')
    result = evaluation.get('course_result') if isinstance(evaluation, dict) else None
    if (not isinstance(result, dict) or evaluation.get('status') != 'completed'
            or type(result.get('attempt')) is not int or result['attempt'] != attempt
            or type(result.get('tests_failed')) is not int or type(result.get('tests_total')) is not int
            or not 0 <= result['tests_failed'] <= result['tests_total']):
        raise ValueError('Graded patch retention requires its matching completed course result')
    data = decode_snapshot(snapshot)
    folder = Path(runtime).resolve() / 'graded-patches'
    folder.mkdir(parents=True, exist_ok=True)
    stem = f'candidate-{candidate_id}-attempt-{attempt}-{snapshot["sha256"]}'
    destination = folder / (stem + '.patch')
    try:
        with destination.open('xb') as output:
            output.write(data)
    except FileExistsError:
        if destination.is_symlink() or not destination.is_file():
            raise ValueError('Existing graded patch path is not an owned regular file')
        if destination.read_bytes() != data:
            raise ValueError('Existing graded patch differs from its immutable captured identity')
    receipt = {'status': 'retained', 'path': str(destination), 'sha256': snapshot['sha256'], 'bytes': len(data),
               'base': snapshot['base'], 'candidate_id': candidate_id, 'attempt': attempt,
               'index_path': str(folder / 'index.jsonl'), 'receipt_path': str(folder / (stem + '.json')),
               'evaluation': evaluation}
    try:
        with Path(receipt['index_path']).open('ab') as output:
            output.write(json.dumps({key: receipt[key] for key in ('candidate_id', 'attempt', 'path', 'sha256', 'bytes')},
                                   separators=(',', ':')).encode() + b'\n')
        with Path(receipt['receipt_path']).open('x') as output:
            json.dump({key: value for key, value in receipt.items() if key != 'status'} | {
                'artifact_retention': 'retained', 'current_workspace_identity': 'not_checked',
            }, output, indent=2)
    except (OSError, ValueError) as error:
        receipt['status'] = 'partial'
        receipt.pop('receipt_path', None)
        receipt['retention_error'] = f'Exact graded patch retained; auxiliary index/receipt failed: {type(error).__name__}: {error}'
    return receipt


class PatchCaptureError(RuntimeError):
    """A capture command failed before any course evaluation was requested."""
    def __init__(self, details: dict):
        self.details = details
        super().__init__(details['message'])


def capture_failure(error: Exception, limit: int | None = 8192) -> dict:
    """Bounded diagnostics only; never serialize process environment or input."""
    if isinstance(error, PatchCaptureError):
        return dict(error.details)

    def diagnostic(value):
        text = value.decode(errors='replace') if isinstance(value, bytes) else str(value)
        for key, secret in os.environ.items():
            if any(marker in key.upper() for marker in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD')) and len(secret) >= 8:
                text = text.replace(secret, '[redacted]')
        return text

    message = diagnostic(error)
    details = {'type': type(error).__name__, 'message': message[:limit] if limit is not None else message,
               'message_characters': len(message), 'message_truncated': limit is not None and len(message) > limit}
    if isinstance(error, subprocess.CalledProcessError):
        stderr = diagnostic(error.stderr or b'')
        details.update(returncode=error.returncode, git_stderr=stderr[:limit] if limit is not None else stderr,
                       git_stderr_characters=len(stderr), git_stderr_bytes=len(stderr.encode()),
                       git_stderr_truncated=limit is not None and len(stderr) > limit)
    return details


def capture_for_boundary(root: str, runtime: str, base: str | None = None) -> dict:
    """Return a known failure with a task-readable receipt, never a fake patch."""
    try:
        return capture(root, base)
    except Exception as error:
        details = capture_failure(error)
        destination = Path(runtime).resolve()
        repository = Path(root).resolve()
        if destination == repository or repository in destination.parents:
            details['receipt_error'] = 'Capture diagnostics must remain outside the repository'
        else:
            path = destination / 'out' / f'capture-failure-{time.time_ns()}.json'
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                full = capture_failure(error, limit=None)
                if 'git_stderr' in full:
                    stderr_path = path.with_suffix('.stderr.txt')
                    try:
                        stderr_path.write_text(full['git_stderr'])
                        details['git_stderr_path'] = str(stderr_path)
                    except OSError as retention_error:
                        details['git_stderr_retention_error'] = capture_failure(retention_error)['message']
                retained = retain_json(path, details)
                if retained:
                    details['receipt_error'] = retained
                else:
                    details['receipt_path'] = str(path)
            except OSError as retention_error:
                details['receipt_error'] = capture_failure(retention_error)['message']
        return {'capture_error': details}


def preserve_ungraded(root: str | Path, runtime: str | Path, reason: str,
                      timeout: float = 10, *, known_grade: dict | None = None) -> dict:
    """Best-effort recovery artifact only; never selects or grades these bytes."""
    started = time.monotonic()
    runtime, root = Path(runtime).resolve(), Path(root).resolve()
    stem = runtime / f'ungraded-final-{time.time_ns()}'
    receipt = {'status': 'pending', 'ungraded': True, 'reason': reason,
               'patch_path': str(stem.with_suffix('.patch')),
               'receipt_path': str(stem.with_suffix('.json')),
               'capture_note': 'Single Git capture; concurrent filesystem changes are not an atomic snapshot.'}
    try:
        if runtime == root or root in runtime.parents:
            raise ValueError('Recovery artifacts must remain outside the repository')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 10:
            raise ValueError('Recovery capture timeout must be greater than zero and at most 10 seconds')
        runtime.mkdir(parents=True, exist_ok=True)
        pin = runtime / 'capture-base.txt'
        base = pin.read_text().strip() if pin.exists() else None
        if base is not None and not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', base):
            raise ValueError('Recovery base pin must be a full commit identity')
        receipt['base_source'] = 'pinned_initial_task_base' if base else 'current_HEAD_fallback'
        if not base:
            receipt['basis_note'] = 'No initial task base was supplied; this is a diff from current HEAD, not a claim of the complete original-task patch.'
        error = retain_json(receipt['receipt_path'], receipt)
        if error:
            raise OSError(error)
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            # The child owns capture, decoding, hashing and artifact writes. Only
            # its bounded receipt crosses back into this process.
            command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                       '--write-ungraded', str(root), receipt['receipt_path']]
            process = subprocess.Popen(command,
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                       start_new_session=True)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                receipt['status'] = 'timeout'
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)
                raise TimeoutError(f'Recovery capture exceeded {timeout:g} seconds; no patch selected or graded')
            if process.returncode:
                errors.seek(0)
                raise RuntimeError(f'Recovery capture exited {process.returncode}: '
                                   + errors.read(2048).decode(errors='replace'))
            output.seek(0)
            raw = output.read(65537)
            if len(raw) > 65536:
                raise ValueError('Recovery child receipt exceeds 64 KiB')
            receipt.update(json.loads(raw))
    except Exception as error:
        if receipt['status'] != 'timeout':
            receipt['status'] = 'error'
        receipt['error'] = (type(error).__name__ + ': ' + str(error))[:2300]
    receipt['elapsed_seconds'] = time.monotonic() - started
    if (receipt['status'] == 'captured' and known_grade
            and known_grade.get('patch_sha256') == receipt['sha256']
            and type(known_grade.get('tests_failed')) is int
            and type(known_grade.get('tests_total')) is int):
        receipt['ungraded'] = False
        receipt['grading_receipt'] = {key: known_grade[key] for key in
            ('candidate_id', 'patch_sha256', 'attempt', 'tests_failed', 'tests_total')
            if key in known_grade}
        receipt['reason'] = 'Final working bytes match this actual grading receipt; capture did not start a new evaluation.'
    try:
        # A forbidden in-repository destination must not receive even a receipt.
        if runtime == root or root in runtime.parents:
            raise ValueError('Recovery receipt destination is inside the repository')
        error = retain_json(receipt['receipt_path'], receipt)
        if error:
            receipt['receipt_error'] = error
    except Exception as error:
        receipt['receipt_error'] = (type(error).__name__ + ': ' + str(error))[:2300]
    return receipt


def _write_ungraded(root: str, receipt_path: str) -> dict:
    receipt = json.loads(Path(receipt_path).read_bytes())
    pin = Path(receipt_path).parent / 'capture-base.txt'
    snapshot = capture(root, pin.read_text().strip() if pin.exists() else None)
    data = decode_snapshot(snapshot)
    destination = Path(receipt['patch_path'])
    temporary = destination.with_suffix('.patch.part')
    temporary.write_bytes(data)
    temporary.replace(destination)
    receipt.update(status='captured', **{key: snapshot[key] for key in ('base', 'sha256', 'bytes')},
                   files=[name[:512] for name in snapshot['files'][:50]], file_count=len(snapshot['files']),
                   files_truncated=len(snapshot['files']) > 50 or any(len(name) > 512 for name in snapshot['files'][:50]))
    Path(receipt_path).write_text(json.dumps(receipt, indent=2))
    return receipt


def capture_artifact(root, runtime, base=None):
    """Keep the potentially large snapshot out of bootstrap command output."""
    value = capture_for_boundary(root, runtime, base)
    if 'capture_error' in value:
        return value
    folder = Path(runtime) / 'capture-transfers'
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, suffix='.json', delete=False) as target:
        raw = json.dumps(value).encode()
        target.write(raw)
        path = target.name
    return {'path': path, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


if __name__ == '__main__':
    if sys.argv[1] == '--capture-artifact':
        print(json.dumps(capture_artifact(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)))
    elif sys.argv[1] == '--read-artifact':
        with Path(sys.argv[2]).open('rb') as incoming:
            incoming.seek(int(sys.argv[3]))
            print(base64.b64encode(incoming.read(min(int(sys.argv[4]), 1 << 20))).decode())
    elif sys.argv[1] == '--capture':
        print(json.dumps(capture_for_boundary(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)))
    elif sys.argv[1] == '--preserve-ungraded':
        print(json.dumps(preserve_ungraded(sys.argv[2], sys.argv[3], sys.argv[4])))
    elif sys.argv[1] == '--write-ungraded':
        print(json.dumps(_write_ungraded(sys.argv[2], sys.argv[3])))
    elif sys.argv[1] == '--retain-graded':
        supplied = json.loads(Path(sys.argv[3]).read_bytes())
        print(json.dumps(retain_graded_snapshot(sys.argv[2], supplied['snapshot'], supplied['candidate_id'],
                                               supplied['attempt'], supplied['evaluation'])))
    else:
        print(json.dumps(capture(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)))
