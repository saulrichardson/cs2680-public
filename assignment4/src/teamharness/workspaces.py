"""Bind an assigned live directory to a separate frozen starting-source identity.

Lifecycle integration: after candidates.snapshot(), call
prepare_live_workspace(assigned, frozen, evidence). Pass the returned workspace and
base to manager.create(..., private=False, capture_base=...). The frozen workspace
remains the replay baseline. For a private child with an inherited capture base,
call bind_capture_base(child_workspace, parent_workspace, base) after copying.
Neither operation checks out, resets, stages, or commits the assigned source.
"""
import json
from pathlib import Path
import subprocess
import tempfile

from src.workspace import git
from .candidates import (capture, save, submodule_state, _git_directory,
                          _check_dependency_state)


CAPTURE_BASES = 'teamharness-capture-bases'


def capture_base_manifest(workspace, revision):
    # Resolve to an object ID before using it as a filename.
    identity = git(workspace, 'rev-parse', '--verify', revision + '^{commit}').decode().strip()
    return _git_directory(Path(workspace)) / CAPTURE_BASES / (identity + '.json')


def bind_capture_base(workspace, source, revision, *, dependencies=None):
    """Copy a capture commit's objects and dependency receipt, without Git refs.

    Local object import adds immutable Git objects, never hardlinks source files.
    This also handles synthetic starting commits omitted by ordinary local clones.
    A dependency receipt describes this materialization, not external cache isolation.
    """
    workspace, source = Path(workspace).resolve(), Path(source).resolve()
    identity = git(source, 'rev-parse', '--verify', revision + '^{commit}').decode().strip()
    # Plumbing works with the older Git shipped in course images and imports
    # only objects: fetch's newer flags are not portable, while an ordinary
    # fetch would overwrite the assigned repository's FETCH_HEAD.
    exists = subprocess.run(['git', '-C', str(workspace), 'cat-file', '-e',
                             identity + '^{commit}'], capture_output=True)
    if exists.returncode:
        revisions = identity + '\n'
        head = git(workspace, 'rev-parse', 'HEAD').decode().strip()
        shared = subprocess.run(['git', '-C', str(source), 'cat-file', '-e',
                                 head + '^{commit}'], capture_output=True)
        if shared.returncode == 0:
            revisions += '^' + head + '\n'
        with tempfile.TemporaryFile() as pack:
            exported = subprocess.run(['git', '-C', str(source), 'pack-objects',
                '--stdout', '--revs'], input=revisions.encode(), stdout=pack, stderr=subprocess.PIPE)
            if exported.returncode:
                raise RuntimeError(exported.stderr.decode(errors='replace'))
            pack.seek(0)
            imported = subprocess.run(['git', '-C', str(workspace), 'index-pack', '--stdin'],
                                      stdin=pack, capture_output=True)
            if imported.returncode:
                raise RuntimeError(imported.stderr.decode(errors='replace'))
        git(workspace, 'cat-file', '-e', identity + '^{commit}')
    if dependencies is None:
        original = capture_base_manifest(source, identity)
        if original.exists():
            expected = json.loads(original.read_text())['submodules']
            observed = submodule_state(workspace)
            # A frozen copy omits an already absent gitlink from its new index.
            # That deletion does not invent dependency bytes in either copy.
            def present(rows):
                return [{**row, **({'children': present(row['children'])} if 'children' in row else {})}
                        for row in rows if row['state'] != 'absent']
            if present(expected) != present(observed):
                raise ValueError('Copied workspace dependency state differs from capture-base source')
            dependencies = observed
    if dependencies is not None:
        destination = capture_base_manifest(workspace, identity)
        destination.parent.mkdir(parents=True, exist_ok=True)
        record = {'revision': identity, 'submodules': dependencies}
        if destination.exists() and json.loads(destination.read_text()) != record:
            raise ValueError('Capture base already has a different dependency receipt')
        save(destination, record)
    return identity


def prepare_live_workspace(assigned, frozen, evidence):
    """Return a shared manager spec for assigned source, checking starting bytes.

    The original staged/unstaged split stays in the user's index; capture uses its
    own temporary index. Dirty starting bytes already belong to the synthetic
    base, so an unchanged live tree produces an empty candidate patch. An observed
    concurrent source change is reported with files preserved, never reset.
    """
    assigned, evidence = Path(assigned).resolve(), Path(evidence)
    if assigned != Path(frozen.get('working_directory', frozen['source'])).resolve():
        raise ValueError('Assigned directory differs from the frozen source identity')
    working_directory = assigned
    assigned = Path(frozen['source']).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    dependencies = submodule_state(assigned)
    _check_dependency_state(assigned, dependencies)
    if dependencies != frozen['source_submodules']:
        raise RuntimeError('Assigned dependency state changed since baseline capture; source is preserved')
    base = bind_capture_base(assigned, frozen['workspace'], frozen['revision'],
                             dependencies=dependencies)
    observed = capture(assigned, evidence / 'live-starting.patch', base)
    if observed['bytes']:
        raise RuntimeError('Assigned source changed since baseline capture; source and evidence are preserved')
    result = {'workspace': str(assigned), 'working_directory': str(working_directory), 'base': base, 'source_candidate': frozen,
              'workspace_mode': 'shared', 'starting_candidate': observed,
              'meaning': 'Assigned live source; frozen baseline is separate evidence. '
                         'External symlinks, ignored caches and services may be shared.'}
    save(evidence / 'live-workspace.json', result)
    return result
