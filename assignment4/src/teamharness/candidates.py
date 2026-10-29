"""Exact source snapshots, dependency identities and external candidate grading."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import tempfile
from typing import Any
from src import workspace as source_effects
from src.workspace import clean_env, git


SUBMODULE_MANIFEST = "teamharness-submodules.json"


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    pending.replace(path)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_directory(root: Path) -> Path:
    return Path(git(root, 'rev-parse', '--absolute-git-dir').decode().strip())


def _dependency_tree(root: Path, parent: Path, initialized: bool) -> str:
    """Hash source-visible dependency bytes, not ignored runtime caches or .git.

    An opaque installed gitlink has no invented Git repository. An empty external
    index lets the existing parent Git evaluate that directory's ignore rules.
    """
    if not root.exists():
        return sha(b'absent')
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f'Gitlink is not an ordinary directory: {root}')
    with tempfile.TemporaryDirectory(prefix='teamharness-dependency-') as temporary:
        env = dict(clean_env())
        if initialized:
            command = ['git', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z']
        else:
            env['GIT_INDEX_FILE'] = str(Path(temporary) / 'empty-index')
            command = ['git', '-C', str(parent), '--git-dir=' + str(_git_directory(parent)),
                       '--work-tree=' + str(parent), 'ls-files', '--others', '--exclude-standard', '-z',
                       '--', str(root.relative_to(parent))]
        result = subprocess.run(command, env=env, check=True, capture_output=True)
    digest = hashlib.sha256()
    for relative in sorted(set(result.stdout.split(b'\0')) - {b''}):
        path = (root if initialized else parent) / os.fsdecode(relative)
        relative_bytes = os.fsencode(path.relative_to(root))
        digest.update(len(relative_bytes).to_bytes(8, 'big') + relative_bytes)
        if path.is_symlink():
            target = os.fsencode(os.readlink(path))
            digest.update(b'link\0' + len(target).to_bytes(8, 'big') + target)
        elif path.is_file():
            stat = path.stat()
            digest.update(b'file\0' + (stat.st_mode & 0o111).to_bytes(4, 'big') + stat.st_size.to_bytes(8, 'big'))
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            digest.update(b'\0')
        else:
            digest.update(b'directory\0' if path.is_dir() else b'missing\0')
    return digest.hexdigest()


def submodule_state(root: Path) -> list[dict]:
    """Observe local gitlinks recursively without fetching or initializing one."""
    root = Path(root).resolve()
    result = []
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        fields, relative = entry.split(b'\t', 1)
        mode, revision, stage = fields.split()
        if mode != b'160000':
            continue
        if stage != b'0':
            raise ValueError('Unmerged submodule index cannot be captured exactly')
        path = root / os.fsdecode(relative)
        initialized = (path / '.git').exists()
        if initialized:
            top = Path(git(path, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
            if top != path.resolve():
                raise ValueError(f'Invalid submodule Git metadata at {path}')
            head = git(path, 'rev-parse', 'HEAD').decode().strip()
            dirty = bool(git(path, '--no-optional-locks', 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=all'))
        else:
            head, dirty = None, False
        row = {'path': os.fsdecode(relative), 'gitlink': revision.decode(),
               'state': 'initialized' if initialized else ('opaque' if path.exists() else 'absent'),
               'head': head, 'dirty': dirty, 'source_tree_sha256': _dependency_tree(path, root, initialized)}
        if initialized:
            row['children'] = submodule_state(path)
        result.append(row)
    return result


def _check_dependency_state(root: Path, state: list[dict]) -> None:
    manifest = _git_directory(root) / SUBMODULE_MANIFEST
    if manifest.exists():
        expected = json.loads(manifest.read_text())['submodules']
        if state != expected:
            raise ValueError('Cannot represent changed nested submodule/dependency contents in a superproject patch; '
                             f'workspace and edits are preserved at {root}')
    def clean(rows):
        return all(not row['dirty'] and (row['head'] is None or row['head'] == row['gitlink'])
                   and clean(row.get('children', [])) for row in rows)
    if not clean(state):
        raise ValueError('Cannot represent dirty or differently checked-out submodule contents in a superproject patch; '
                         f'workspace and edits are preserved at {root}')


def capture(root, destination, base=None):
    """Parent patch capture with explicit protection against omitted nested edits."""
    root = Path(root).resolve()
    before = submodule_state(root)
    _check_dependency_state(root, before)
    if base is not None:
        from .workspaces import capture_base_manifest
        manifest = capture_base_manifest(root, base)
        if manifest.exists() and before != json.loads(manifest.read_text())['submodules']:
            raise ValueError('Cannot represent changed nested submodule/dependency contents in a superproject patch; '
                             f'workspace and edits are preserved at {root}')
    result = source_effects.capture(root, destination, base)
    after = submodule_state(root)
    _check_dependency_state(root, after)
    if after != before:
        raise RuntimeError('Submodule dependencies changed during source capture; patch is not settled')
    return result


def _copy_submodule_metadata(source: Path, destination: Path, rows: list[dict]) -> None:
    for row in rows:
        if row['state'] != 'initialized':
            continue
        original, copied = source / row['path'], destination / row['path']
        with tempfile.TemporaryDirectory(prefix='teamharness-submodule-') as temporary:
            clone = Path(temporary) / 'repo'
            subprocess.run(['git', '-c', 'protocol.file.allow=always', 'clone', '--quiet', '--no-hardlinks', '--dissociate',
                            '--no-checkout', str(original), str(clone)], check=True, capture_output=True)
            shutil.move(str(clone / '.git'), str(copied / '.git'))
        git(copied, 'read-tree', row['head'])
        _copy_submodule_metadata(original, copied, row.get('children', []))


def snapshot(source: Path, destination: Path, evidence: Path) -> dict:
    """Freeze source and offline dependencies with private, relocatable Git data.

    Initialized gitlinks retain private object databases. Opaque or absent links
    retain that state. Nested source changes cannot masquerade as parent patches.
    """
    started = time.time()
    working_directory, destination = source.resolve(), destination.resolve()
    source = Path(git(working_directory, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if source == destination or source in destination.parents:
        raise ValueError('Snapshot destination must be outside its source workspace')
    dependencies = submodule_state(source)
    _check_dependency_state(source, dependencies)
    evidence.mkdir(parents=True, exist_ok=True)
    save(evidence / 'submodules-before.json', {'submodules': dependencies})
    base = git(source, 'rev-parse', 'HEAD').decode().strip()
    before = capture(source, evidence / 'source-before.patch', base)
    subprocess.run(['git', '-c', 'protocol.file.allow=always', 'clone', '--quiet', '--no-hardlinks', '--dissociate',
                    '--no-checkout', str(source), str(destination)], check=True, capture_output=True)
    shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True,
                    ignore=lambda _path, names: ['.git'] if '.git' in names else [])
    git(destination, 'read-tree', base)
    _copy_submodule_metadata(source, destination, dependencies)
    copied_dependencies = submodule_state(destination)
    after_dependencies = submodule_state(source)
    if not (dependencies == copied_dependencies == after_dependencies):
        raise RuntimeError('Submodule dependency bytes changed during snapshot; preserve evidence and retry explicitly')
    save(_git_directory(destination) / SUBMODULE_MANIFEST, {'submodules': dependencies})
    copied = capture(destination, evidence / 'copied.patch', base)
    after = capture(source, evidence / 'source-after.patch', base)
    if not (before['sha256'] == after['sha256'] == copied['sha256']):
        raise RuntimeError('Source changed during snapshot; preserve evidence and retry explicitly')
    git(destination, 'add', '-A')
    git(destination, '-c', 'user.name=Team Harness', '-c', 'user.email=harness@local.invalid',
        'commit', '--quiet', '--allow-empty', '-m', 'Frozen task starting state')
    revision = git(destination, 'rev-parse', 'HEAD').decode().strip()
    frozen_dependencies = submodule_state(destination)
    # A genuinely absent tracked gitlink is a parent-source deletion in the
    # original patch. After checkpoint commit it is no longer a tracked link.
    save(_git_directory(destination) / SUBMODULE_MANIFEST, {'submodules': frozen_dependencies})
    result = {'source': str(source), 'working_directory': str(working_directory),
              'workspace': str(destination), 'original_base': base,
              'source_patch_sha256': before['sha256'], 'revision': revision,
              'source_submodules': dependencies, 'submodules': frozen_dependencies,
              'elapsed_seconds': time.time() - started,
              'dependencies': 'Ignored local files copied but excluded from source hashes; initialized Git databases are private. '
                              'Opaque gitlink files retain no Git metadata. Nested source changes require an explicit representable artifact.'}
    save(evidence / 'snapshot.json', result)
    return result


@dataclass(frozen=True)
class Grader:
    """Explicit external command; its contract, not exit zero alone, defines a grade."""
    argv: tuple[str, ...]
    name: str = 'external-command'
    official: bool = False

    def __post_init__(self) -> None:
        if not self.argv or not all(isinstance(x, str) and x for x in self.argv):
            raise ValueError('Grader argv must contain nonempty strings')
        if not self.name.strip():
            raise ValueError('Grader needs a name')


def grade_candidate(baseline: Path, revision: str, candidate: dict,
                    grader: Grader, root: Path, *, cwd: str = ".", deadline: float | None = None) -> dict:
    """Replay exact captured bytes into a fresh starting snapshot before grading."""
    root.mkdir(parents=True, exist_ok=False)
    patch = Path(candidate['patch_path']).read_bytes()
    if sha(patch) != candidate['sha256'] or candidate['base'] != revision:
        raise ValueError('Candidate identity does not match the frozen task')
    saved_patch = root / 'candidate.patch'
    saved_patch.write_bytes(patch)
    work = root / 'workspace'
    shutil.copytree(baseline, work, symlinks=True)
    if patch:
        applied = subprocess.run(['git', '-C', str(work), 'apply', '--binary', '-'],
                                 input=patch, capture_output=True)
        (root / 'apply.stdout').write_bytes(applied.stdout)
        (root / 'apply.stderr').write_bytes(applied.stderr)
        if applied.returncode:
            result = {'state': 'replay_error', 'candidate_sha256': candidate['sha256'],
                      'exit_code': applied.returncode, 'official': grader.official}
            save(root / 'receipt.json', result)
            return result
    before = capture(work, root / 'before.patch', revision)
    if before['sha256'] != candidate['sha256']:
        raise ValueError('Replayed source does not equal captured candidate')
    argv = [x.replace('{workspace}', str(work)).replace('{patch}', str(saved_patch))
            for x in grader.argv]
    command_cwd = (work / cwd).resolve()
    if not command_cwd.is_relative_to(work.resolve()):
        raise ValueError('Check working directory leaves the candidate')
    result = {'name': grader.name, 'official': grader.official, 'argv': argv,
              'cwd': str(command_cwd), 'candidate_sha256': candidate['sha256'],
              'before_sha256': before['sha256'], 'state': 'running',
              'started_at': time.time(), 'meaning': 'External command result; model assertions are separate.'}
    save(root / 'receipt.json', result)
    with (root / 'stdout').open('wb') as stdout, (root / 'stderr').open('wb') as stderr:
        try:
            if deadline is not None and time.time() >= deadline:
                raise TimeoutError('Task deadline expired before command launch')
            process = subprocess.Popen(argv, cwd=command_cwd, env=clean_env(), stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                process.wait(timeout=max(0, deadline-time.time()) if deadline is not None else None)
                result.update(state='exited', exit_code=process.returncode)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                result.update(state='deadline_exceeded', exit_code=process.returncode)
        except TimeoutError as error:
            result.update(state='deadline_exceeded', error=str(error))
        except OSError as error:
            result.update(state='launch_error', error=str(error))
    after = capture(work, root / 'after.patch', revision)
    result.update(after_sha256=after['sha256'], source_unchanged=before['sha256'] == after['sha256'],
                  finished_at=time.time(), stdout_path=str(root / 'stdout'), stderr_path=str(root / 'stderr'))
    save(root / 'receipt.json', result)
    return result
