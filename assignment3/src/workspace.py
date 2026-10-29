"""Restore the task's original directory after exporting an unsuccessful owner.

The course supplies one task sandbox. Recovery therefore uses a sequential reset,
not a second directory that still points at the first owner's files. Capture the
complete supplied workdir before tools run, including private Git metadata and
installed/ignored dependencies. Restore it at the same absolute path so editable
imports, virtual environments and workspace links keep their original meaning.
This is lifecycle isolation of owned evidence, not confinement of a hostile shell.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import time


class WorkspaceError(RuntimeError):
    pass


@dataclass
class Workspace:
    path: Path
    base: str
    env: dict[str, str]


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _git(root, *args):
    env = {key: value for key, value in os.environ.items() if key not in {
        'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
        'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'}}
    result = subprocess.run(['git', '-c', 'safe.directory=*', *args], cwd=root,
                            env=env, capture_output=True)
    if result.returncode:
        raise WorkspaceError(result.stderr.decode(errors='replace'))
    return result.stdout.decode().strip()


def _inside(path, parents):
    return any(path == parent or parent in path.parents for parent in parents)


def _inventory(root, excluded):
    """Enumerate without following links; existing scratch is never deleted."""
    entries = set()
    if not root.is_dir() or root.is_symlink():
        return entries
    for folder, directories, names in os.walk(root, followlinks=False):
        directory = Path(folder)
        directories[:] = [name for name in directories if not _inside(directory / name, excluded)]
        for name in directories + names:
            path = directory / name
            if not _inside(path, excluded):
                entries.add(path.relative_to(root).as_posix())
    return entries


def preserve(root, directory, *, base=None, scratch_roots=(), excluded=()):
    """Pin the entire supplied checkout, not an after-the-fact clean Git clone."""
    started = time.monotonic()
    root, directory = Path(root).resolve(), Path(directory).resolve()
    if directory == root or root in directory.parents:
        raise WorkspaceError('The intake snapshot must be outside the task workdir')
    if not (root / '.git').is_dir() or (root / '.git').is_symlink():
        raise WorkspaceError('Blind recovery requires task-local Git metadata at intake')
    base = base or _git(root, 'rev-parse', 'HEAD')
    if _git(root, 'rev-parse', 'HEAD') != base:
        raise WorkspaceError('Intake checkout differs from the acquired source base')
    # An alternate object store could later reveal outgoing commits or disappear.
    # Course task images normally have a self-contained .git; fail explicitly if not.
    alternates = root / '.git/objects/info/alternates'
    if alternates.exists() and alternates.read_text().strip():
        raise WorkspaceError('Blind recovery intake cannot depend on an external Git object store')
    directory.mkdir(parents=True, exist_ok=False)
    archive_path = directory / 'source.tar'
    count, total = 0, 0
    with tarfile.open(archive_path, 'w') as archive:
        for folder, directories, names in os.walk(root, followlinks=False):
            directory_path = Path(folder)
            directories.sort()
            for name in sorted(directories + names):
                path = directory_path / name
                info = archive.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                if path.is_symlink() or path.is_dir():
                    archive.addfile(info)
                elif stat.S_ISREG(path.lstat().st_mode):
                    # Never share mutable inodes with either owner, including
                    # Git objects that arrived hard-linked in the task image.
                    info.type, info.linkname, info.size = tarfile.REGTYPE, '', path.stat().st_size
                    with path.open('rb') as source:
                        archive.addfile(info, source)
                    count += 1
                    total += info.size
                else:
                    raise WorkspaceError(f'Cannot preserve non-file task input: {path}')
    exclusions = [root, directory, *(Path(path).resolve() for path in excluded)]
    scratch = []
    for path in dict.fromkeys(str(Path(path).resolve()) for path in scratch_roots):
        parent = Path(path)
        if parent.is_dir() and not _inside(parent, exclusions):
            scratch.append({'root': path, 'entries': sorted(_inventory(parent, exclusions))})
    receipt = {'base': base, 'root': str(root), 'archive': str(archive_path),
               'sha256': _digest(archive_path), 'files': count, 'file_bytes': total,
               'archive_bytes': archive_path.stat().st_size,
               'snapshot_seconds': time.monotonic() - started,
               'scratch': scratch, 'excluded': [str(path) for path in exclusions],
               'provenance': 'Complete supplied task workdir captured before any solving-owner action.'}
    (directory / 'receipt.json').write_text(json.dumps(receipt))
    return receipt


def _remove(path):
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path)


def restore(root, preserved):
    """Verify the snapshot before removing anything, then restore at the same path."""
    root = Path(root).resolve()
    archive_path = Path(preserved['archive'])
    if str(root) != preserved['root'] or archive_path.is_symlink() or _digest(archive_path) != preserved['sha256']:
        raise WorkspaceError('Intake source snapshot identity mismatch; outgoing source was not removed')
    # Validate every member before changing the source. Only the snapshot we
    # created is accepted; do not extract externally supplied campaign archives.
    with tarfile.open(archive_path, 'r:') as archive:
        members = archive.getmembers()
        for member in members:
            relative = Path(member.name)
            if (relative.is_absolute() or '..' in relative.parts
                    or not (member.isfile() or member.isdir() or member.issym())):
                raise WorkspaceError('Invalid intake snapshot member; source was not removed')
        for path in root.iterdir():
            _remove(path)
        directories = []
        for member in members:
            target = root / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
                directories.append((target, member))
            elif member.issym():
                target.symlink_to(member.linkname)
            else:
                with archive.extractfile(member) as incoming, target.open('xb') as output:
                    shutil.copyfileobj(incoming, output, 1 << 20)
                target.chmod(member.mode)
                os.utime(target, (member.mtime, member.mtime))
        for target, member in reversed(directories):
            target.chmod(member.mode)
            os.utime(target, (member.mtime, member.mtime))
    if _git(root, 'rev-parse', 'HEAD') != preserved['base']:
        raise WorkspaceError('Restored Git identity differs from the acquired base')
    return Workspace(root, preserved['base'], {})


def clear_new_scratch(preserved):
    """Remove newly created paths only, within the explicit intake inventory."""
    removed = []
    exclusions = [Path(path) for path in preserved['excluded']]
    for scope in preserved['scratch']:
        root = Path(scope['root'])
        existing = set(scope['entries'])
        current = _inventory(root, exclusions)
        # Top-most new directories remove their descendants once. Never follow
        # a new symlink, even if it points to a pre-existing dependency or home.
        deleted = []
        for name in sorted(current - existing, key=lambda item: (len(Path(item).parts), item)):
            relative = Path(name)
            if any(parent in relative.parents for parent in deleted):
                continue
            target = root / relative
            _remove(target)
            deleted.append(relative)
            removed.append(str(target))
    return removed
