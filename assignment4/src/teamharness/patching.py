"""Exact, evidenced text patches; no claim of a multi-file transaction.

Only this dedicated patch capability is workspace-relative. The shell and file
reader retain their own capabilities. All files and hunks are checked before any
source write. A workspace-derived advisory lock serializes calls to this module;
it cannot exclude an unrestricted shell or another editor. Files are rechecked
immediately before each mutation and observed afterward. Earlier successful
writes are retained if a later write fails, with explicit per-file receipts.

Syntax is ``*** Begin Patch`` followed by Add/Update/Delete File sections and
``*** End Patch``. Updates contain ``@@`` hunks with exact space/minus/plus
lines. ``@@ exact line`` is an exact section anchor, not a fuzzy search hint.
``*** End of File`` restricts a hunk to the file's end. Pure insertions need that
marker unless the file is empty. A ``\\ No newline at end of file`` marker is
supported; otherwise updates retain the file's final-newline convention and
existing line endings. New files use LF. Moves and binary patches are unsupported.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any


PATCH_FORMAT = """Pass one patch string with real newline characters. Start with the
exact line *** Begin Patch and finish with *** End Patch. Do not add trailing
*** to either delimiter or as file-header decoration: path text is literal.
Paths are relative to the assigned
workspace: use src/main.py, not workspace/src/main.py unless that subdirectory
actually exists. Do not use absolute paths or a/ and b/ Git-diff prefixes.
Add: *** Add File: path, then prefix EVERY content line with + (a blank line is +).
Update: *** Update File: path, then @@ on its own line; prefix unchanged context
with one space, removed lines with -, and added lines with +. Context must match
current file contents exactly. Do not include diff --git, ---/+++ file headers,
or numbered @@ -1,3 +1,4 @@ Git hunks.
Delete: *** Delete File: path with no following content or hunks.
Example assumes example.txt contains 'context' then 'old value', obsolete.txt
exists, and notes.txt does not. Adapt paths and old context after reading files;
the example is instructional and is never applied automatically."""

PATCH_EXAMPLE = """*** Begin Patch
*** Add File: notes.txt
+new note
+
*** Update File: example.txt
@@
 context
-old value
+new value
*** Delete File: obsolete.txt
*** End Patch
"""


def patch_format_help() -> dict:
    return {'patch_format': PATCH_FORMAT, 'example': {'patch': PATCH_EXAMPLE}}


class PatchError(ValueError):
    """An explicit syntax, capability or current-source conflict."""

    def __init__(self, message, *, code='invalid_patch', path=None):
        super().__init__(message)
        self.code, self.path = code, path


def patch_recovery(error):
    """A correction for a known validation failure, never an automatic edit."""
    code = getattr(error, 'code', 'patch_validation_failed')
    if code == 'patch_context_mismatch':
        return {'code': code, 'message': 'No source writes started. Read the current file and construct exact matching context.',
                'example_call': {'name': 'read_file', 'arguments': {'path': error.path, 'start_line': 1}}}
    message = ('No source writes started. Wrap the patch in the exact Begin Patch and End Patch lines.'
               if code == 'invalid_patch_delimiters' else
               'No source writes started. Correct the reported file or hunk after reading its current contents.')
    return {'code': code, 'message': message,
            'example_call': {'name': 'apply_patch', 'arguments': {'patch':
                '*** Begin Patch\n*** Update File: example.txt\n@@\n-old value\n+new value\n*** End Patch\n'}},
            'example_scope': 'Syntax example only: substitute a real relative path and exact old context. Submit explicitly.'}


@dataclass(frozen=True)
class PatchLine:
    kind: str
    content: str
    no_newline: bool = False


@dataclass(frozen=True)
class Hunk:
    anchor: str | None
    lines: tuple[PatchLine, ...]
    eof: bool = False


@dataclass(frozen=True)
class Change:
    kind: str
    path: str
    hunks: tuple[Hunk, ...] = ()
    additions: tuple[PatchLine, ...] = ()


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _save(path: Path, value: Any) -> None:
    raw = (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()
    temporary = path.with_name(path.name + '.pending')
    with temporary.open('wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _path(workspace: Path, name: str) -> Path:
    if (not name or '\x00' in name or '\\' in name or name.startswith('/')
            or any(part in ('', '.', '..') for part in name.split('/'))
            or (len(name) > 1 and name[1] == ':')):
        raise PatchError(f'Patch paths must be unambiguous workspace-relative paths: {name!r}')
    path = workspace
    for part in name.split('/'):
        path = path / part
        if path.is_symlink():
            raise PatchError(f'Symlink patch paths are unsupported; use the shell explicitly: {name!r}')
    return path


def _parse(patch: str) -> tuple[Change, ...]:
    if not isinstance(patch, str) or '\x00' in patch:
        raise PatchError('Patch must be UTF-8 text without NUL bytes')
    # A patch may be delivered with LF or CRLF protocol line separators. Source
    # line contents (including other whitespace) are never stripped or guessed.
    lines = patch.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    lines = [line[:-1] if line.endswith('\r') else line for line in lines]
    if not lines or lines[0] != '*** Begin Patch' or lines[-1] != '*** End Patch':
        raise PatchError('Expected exact Begin Patch and End Patch delimiters', code='invalid_patch_delimiters')
    changes: list[Change] = []
    seen: set[str] = set()
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        prefixes = {'*** Add File: ': 'add', '*** Update File: ': 'update', '*** Delete File: ': 'delete'}
        selected = next(((prefix, kind) for prefix, kind in prefixes.items() if header.startswith(prefix)), None)
        if selected is None:
            raise PatchError(f'Expected Add/Update/Delete File at patch line {index + 1}')
        prefix, kind = selected
        name = header[len(prefix):]
        if name in seen:
            raise PatchError(f'Duplicate file section: {name!r}')
        seen.add(name)
        index += 1
        body: list[str] = []
        while index < len(lines) - 1 and not lines[index].startswith(('*** Add File: ', '*** Update File: ', '*** Delete File: ')):
            body.append(lines[index])
            index += 1
        if kind == 'delete':
            if body:
                raise PatchError(f'Delete File section must have no hunks: {name!r}')
            changes.append(Change(kind, name))
            continue
        if kind == 'add':
            additions: list[PatchLine] = []
            for line in body:
                if line == '\\ No newline at end of file':
                    if not additions or additions[-1].no_newline:
                        raise PatchError(f'Misplaced no-newline marker in {name!r}')
                    previous = additions[-1]
                    additions[-1] = PatchLine('+', previous.content, True)
                elif line.startswith('+'):
                    if additions and additions[-1].no_newline:
                        raise PatchError(f'No-newline marker must describe the last line in {name!r}')
                    additions.append(PatchLine('+', line[1:]))
                else:
                    raise PatchError(f'Add File lines must start with + in {name!r}')
            changes.append(Change(kind, name, additions=tuple(additions)))
            continue
        hunks: list[Hunk] = []
        position = 0
        while position < len(body):
            header = body[position]
            if header != '@@' and not header.startswith('@@ '):
                raise PatchError(f'Update File needs an @@ hunk in {name!r}')
            anchor = header[3:] if header.startswith('@@ ') else None
            if anchor == '':
                raise PatchError(f'Empty section anchor in {name!r}; use @@')
            position += 1
            parts: list[PatchLine] = []
            eof = False
            while position < len(body) and body[position] != '@@' and not body[position].startswith('@@ '):
                line = body[position]
                position += 1
                if line == '*** End of File':
                    eof = True
                    if position != len(body):
                        raise PatchError(f'End of File must finish the file section in {name!r}')
                    break
                if line == '\\ No newline at end of file':
                    if not parts or parts[-1].no_newline:
                        raise PatchError(f'Misplaced no-newline marker in {name!r}')
                    previous = parts[-1]
                    parts[-1] = PatchLine(previous.kind, previous.content, True)
                elif line[:1] in (' ', '+', '-'):
                    parts.append(PatchLine(line[0], line[1:]))
                else:
                    raise PatchError(f'Invalid hunk line in {name!r}: expected space, + or - prefix')
            if not parts or not any(part.kind in ('+', '-') for part in parts):
                raise PatchError(f'Empty or context-only hunk in {name!r}')
            hunk = Hunk(anchor, tuple(parts), eof)
            if hunk in hunks:
                raise PatchError(f'Duplicate hunk in {name!r}')
            hunks.append(hunk)
        if not hunks:
            raise PatchError(f'Update File needs a nonempty hunk in {name!r}')
        changes.append(Change(kind, name, tuple(hunks)))
    if not changes:
        raise PatchError('Patch contains no file operations')
    return tuple(changes)


def _source_lines(raw: bytes) -> list[tuple[str, str]]:
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise PatchError('Binary/non-UTF-8 files are unsupported by the text patch tool') from error
    if '\x00' in text:
        raise PatchError('Binary/NUL-containing files are unsupported by the text patch tool')
    result = []
    segments = text.split('\n')
    for index, segment in enumerate(segments):
        if index < len(segments) - 1:
            result.append((segment[:-1], '\r\n') if segment.endswith('\r') else (segment, '\n'))
        elif segment:
            result.append((segment, ''))
    return result


def _updated(raw: bytes, change: Change) -> bytes:
    original = _source_lines(raw)
    default_ending = next((ending for _, ending in original if ending), '\n')
    output: list[tuple[str, str, bool]] = []
    cursor = 0
    explicit_after_no_newline = False
    explicit_before_no_newline = False
    for number, hunk in enumerate(change.hunks, 1):
        old = [line for line in hunk.lines if line.kind != '+']
        start = cursor
        if hunk.anchor is not None:
            anchors = [i for i in range(cursor, len(original)) if original[i][0] == hunk.anchor]
            if len(anchors) != 1:
                raise PatchError(f'{change.path}: hunk {number} section anchor matched {len(anchors)} lines, expected exactly one')
            start = anchors[0]
        if not old:
            if original and not hunk.eof:
                raise PatchError(f'{change.path}: insertion without old context requires End of File')
            candidates = [len(original)]
        else:
            candidates = [i for i in range(start, len(original) - len(old) + 1)
                if all(original[i+j][0] == line.content and (not line.no_newline or original[i+j][1] == '')
                       for j, line in enumerate(old))
                and (not hunk.eof or i + len(old) == len(original))]
        if len(candidates) != 1:
            raise PatchError(f'{change.path}: hunk {number} exact old context matched {len(candidates)} locations, expected one; no fuzzy edit performed',
                             code='patch_context_mismatch', path=change.path)
        at = candidates[0]
        output.extend((content, ending, False) for content, ending in original[cursor:at])
        old_position = at
        ending = next((end for _, end in original[at:at+len(old)] if end), default_ending)
        for line in hunk.lines:
            if line.kind == '+':
                output.append((line.content, '' if line.no_newline else ending, line.no_newline))
                explicit_after_no_newline |= line.no_newline
            else:
                if line.kind == ' ':
                    content, old_ending = original[old_position]
                    output.append((content, old_ending, line.no_newline))
                    explicit_after_no_newline |= line.no_newline
                explicit_before_no_newline |= line.no_newline
                old_position += 1
        cursor = at + len(old)
    output.extend((content, ending, False) for content, ending in original[cursor:])
    # Existing unterminated final lines become ordinary lines if text is appended.
    # An explicit no-newline marker anywhere except the result's end is invalid.
    for index, (content, ending, explicit) in enumerate(output[:-1]):
        if explicit:
            raise PatchError(f'{change.path}: explicit no-newline marker is not at the resulting file end')
        if not ending:
            output[index] = (content, default_ending, False)
    if output:
        preserve_missing = bool(original and not original[-1][1]) and not explicit_before_no_newline
        if explicit_after_no_newline or preserve_missing:
            output[-1] = (output[-1][0], '', output[-1][2])
        elif not output[-1][1]:
            output[-1] = (output[-1][0], default_ending, output[-1][2])
    return ''.join(content + ending for content, ending, _ in output).encode('utf-8')


def _observe(path: Path) -> tuple[dict, bytes | None]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'exists': False}, None
    if not stat.S_ISREG(info.st_mode):
        raise PatchError(f'Expected a regular file, not a symlink/directory/special file: {path}')
    # Read bytes and mode from one opened regular file, not two path versions.
    # NONBLOCK prevents a concurrent replacement by a FIFO from blocking us.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise PatchError(f'Source became a non-regular file while observing: {path}')
        raw = source.read()
        after = os.fstat(source.fileno())
    identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if identity(before) != identity(after):
        raise PatchError(f'Source changed while reading its snapshot: {path}')
    return {'exists': True, 'sha256': _hash(raw), 'bytes': len(raw), 'mode': stat.S_IMODE(after.st_mode)}, raw


def _identity(observation: dict) -> dict:
    return {key: observation[key] for key in ('exists', 'sha256', 'bytes', 'mode') if key in observation}


def _commit_file(path: Path, raw: bytes | None, mode: int, *, create: bool = False) -> None:
    """One file replacement; callers must not infer multi-file atomicity."""
    if raw is None:
        path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.team-patch-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(raw)
            os.fchmod(output.fileno(), mode)
            output.flush()
            os.fsync(output.fileno())
        if create:
            # Create must not overwrite a file that appeared after preflight.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def _workspace_lock(workspace: Path):
    directory = Path(tempfile.gettempdir()) / f'teamharness-patch-locks-{os.getuid()}'
    directory.mkdir(mode=0o700, exist_ok=True)
    with (directory / (_hash(os.fsencode(workspace)) + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def apply_patch(workspace: Path, evidence_root: Path, patch: str, operation_id: str) -> dict:
    """Apply one exact patch and return a durable receipt or explicit uncertainty.

    Reusing an ID with identical inputs returns its saved receipt. An interrupted
    operation with no receipt is never replayed. ``evidence_root`` must be outside
    the workspace. Successful writes are never silently rolled back.
    """
    workspace, evidence_root = Path(workspace).resolve(), Path(evidence_root).resolve()
    if not workspace.is_dir():
        raise ValueError('Patch workspace must be an existing directory')
    if evidence_root.is_relative_to(workspace):
        raise ValueError('Patch evidence must be outside the candidate workspace')
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError('Patch operation_id must be a nonempty string')
    if not isinstance(patch, str):
        raise ValueError('Patch must be a string')
    request = {'operation_id': operation_id, 'workspace': str(workspace), 'patch_sha256': _hash(patch.encode())}
    archive = evidence_root / _hash(operation_id.encode())
    with _workspace_lock(workspace):
        evidence_root.mkdir(parents=True, exist_ok=True)
        if archive.exists():
            intent = archive / 'intent.json'
            try:
                prior = json.loads(intent.read_text())
            except (OSError, ValueError):
                prior = None
            if prior != request:
                return {**request, 'applied': False, 'state': 'identity_conflict', 'archive': str(archive),
                        'error': 'Operation identity already exists with different or unreadable inputs; not replayed'}
            receipt = archive / 'receipt.json'
            if receipt.is_file():
                return json.loads(receipt.read_text())
            return {**request, 'applied': False, 'state': 'unknown', 'replay_safe': False, 'archive': str(archive),
                    'error': 'Prior operation has no final receipt; inspect its per-file receipts and current source, do not replay'}
        archive.mkdir()
        _save(archive / 'intent.json', request)
        (archive / 'patch.txt').write_bytes(patch.encode())
        result = {**request, 'applied': False, 'state': 'conflict', 'files': [], 'archive': str(archive),
                  'patch_path': str(archive / 'patch.txt'), 'receipt_path': str(archive / 'receipt.json'),
                  'atomicity': 'Per-file replacement only; dedicated patch calls share a lock, unrestricted shell/editor writes can race'}
        planned = []
        try:
            changes = _parse(patch)
            names = [change.path for change in changes]
            if any(left != right and right.startswith(left + '/') for left in names for right in names):
                raise PatchError('A patch cannot name both a file and a child beneath that file')
            for index, change in enumerate(changes):
                target = _path(workspace, change.path)
                before, raw = _observe(target)
                if change.kind == 'add':
                    if before['exists']:
                        raise PatchError(f'Add File already exists: {change.path}')
                    after = ''.join(line.content + ('' if line.no_newline else '\n') for line in change.additions).encode()
                elif not before['exists']:
                    raise PatchError(f'{change.kind.title()} File does not exist: {change.path}')
                elif change.kind == 'delete':
                    after = None
                else:
                    after = _updated(raw, change)
                mode = before.get('mode', 0o644)
                expected = {'exists': False} if after is None else {'exists': True, 'sha256': _hash(after), 'bytes': len(after), 'mode': mode}
                row = {'path': change.path, 'operation': change.kind, 'before': before, 'planned_after': expected,
                       'status': 'not_attempted', 'changed': _identity(before) != expected}
                if raw is not None:
                    snapshot = archive / f'{index:04d}.before'
                    snapshot.write_bytes(raw)
                    row['before']['snapshot'] = str(snapshot)
                if after is not None:
                    snapshot = archive / f'{index:04d}.planned'
                    snapshot.write_bytes(after)
                    row['planned_after']['snapshot'] = str(snapshot)
                result['files'].append(row)
                planned.append((target, after, mode))
            _save(archive / 'validated.json', result)
        except (PatchError, OSError) as error:
            result['error'] = str(error)
            result['error_type'] = type(error).__name__
            result.update(source_writes_started=False,
                          recovery=patch_recovery(error),
                          message='Patch validation failed before any source writes. Read the error and current files, then explicitly submit a corrected patch; the rejected input was not repaired.',
                          **patch_format_help())
            _save(archive / 'receipt.json', result)
            return result

        result['state'] = 'applying'
        for index, (target, after, mode) in enumerate(planned):
            row = result['files'][index]
            mutation_entered = False
            try:
                _path(workspace, row['path'])  # Recheck symlinks before mutation.
                current, _ = _observe(target)
                if _identity(current) != _identity(row['before']):
                    raise PatchError(f'Source changed after validation: {row["path"]}; earlier writes, if any, are retained')
                row['status'] = 'attempting'
                _save(archive / f'{index:04d}.receipt.json', row)
                mutation_entered = True
                if row['changed']:
                    if row['operation'] == 'add':
                        _commit_file(target, after, mode, create=True)
                    else:
                        _commit_file(target, after, mode)
                actual, actual_bytes = _observe(target)
                if actual_bytes is not None:
                    snapshot = archive / f'{index:04d}.after'
                    snapshot.write_bytes(actual_bytes)
                    actual['snapshot'] = str(snapshot)
                row['after'] = actual
                if _identity(actual) != _identity(row['planned_after']):
                    raise PatchError(f'Post-write source differs from planned bytes/mode: {row["path"]}')
                row['status'] = 'applied' if row['changed'] else 'unchanged'
                _save(archive / f'{index:04d}.receipt.json', row)
            except (OSError, PatchError) as error:
                attempted = mutation_entered
                row.update(status='unknown' if attempted else 'conflict', error=str(error), error_type=type(error).__name__)
                try:
                    actual, actual_bytes = _observe(target)
                    if actual_bytes is not None:
                        snapshot = archive / f'{index:04d}.after'
                        snapshot.write_bytes(actual_bytes)
                        actual['snapshot'] = str(snapshot)
                    row['after'] = actual
                    if attempted and _identity(actual) == _identity(row['planned_after']):
                        row['status'] = 'applied_observed_after_error'
                    elif attempted and _identity(actual) == _identity(row['before']):
                        row['status'] = 'failed_unchanged'
                except (OSError, PatchError) as observation_error:
                    row['observation_error'] = str(observation_error)
                result['state'] = 'partial' if any(r['status'] in ('applied', 'applied_observed_after_error') for r in result['files']) else ('unknown' if row['status'] == 'unknown' else 'conflict')
                result.update(error=str(error), error_type=type(error).__name__, replay_safe=False)
                result['recovery'] = {'code': 'patch_write_incomplete',
                    'message': 'Source may have changed. Inspect the per-file outcomes and current files before constructing another edit; do not replay the whole patch blindly.',
                    'archive': str(archive)}
                _save(archive / f'{index:04d}.receipt.json', row)
                break
        else:
            result.update(applied=True, state='applied', changed=any(row['changed'] for row in result['files']))
        _save(archive / 'receipt.json', result)
        return result


def display_diff(result: dict, limit: int = 12000):
    """Unified diff of applied files from this receipt's archived bytes.

    Only rows observed as applied are shown; the receipt remains authoritative
    for planned, partial and uncertain outcomes. Returns (text, complete) or None.
    """
    import difflib
    parts = []
    for row in result.get('files', []):
        if row.get('status') not in ('applied', 'applied_observed_after_error') or not row.get('changed'):
            continue
        before = (row.get('before') or {}).get('snapshot')
        after = (row.get('after') or {}).get('snapshot')
        old = Path(before).read_bytes().decode('utf-8', errors='replace') if before else ''
        new = Path(after).read_bytes().decode('utf-8', errors='replace') if after else ''
        parts.append(''.join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
            'a/' + row['path'] if before else '/dev/null', 'b/' + row['path'] if after else '/dev/null')))
    text = ''.join(part if part.endswith('\n') else part + '\n' for part in parts if part)
    return (text[:limit], len(text) <= limit) if text else None
