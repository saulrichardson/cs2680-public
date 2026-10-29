"""One fresh owner after a confirmed export and an in-place intake reset.

Candidate selection and outgoing archives remain in the agent container. The
recovery solver receives no prior patch, transcript, memory index or source path.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time

from .capture import capture
from . import workspace


def instructions():
    return Path(__file__).with_name('validation-prompt.txt').read_text()


def preserve(root, runtime, *, scratch_roots=None, bootstrap=None):
    """Capture supplied source before tools; local fixtures never sweep host temp."""
    root, runtime = Path(root).resolve(), Path(runtime).resolve()
    if scratch_roots is None:
        scratch_roots = ([Path('/tmp'), Path.home()]
                         if sys.platform == 'linux' and runtime == Path('/tmp/a3') else [])
    if bootstrap is None and sys.platform == 'linux' and runtime == Path('/tmp/a3'):
        bootstrap = Path('/tmp/a3-bootstrap')
    pin = runtime / 'capture-base.txt'
    intake = capture(root, pin.read_text().strip() if pin.exists() else None)
    result = workspace.preserve(root, runtime / 'intake-source',
        base=pin.read_text().strip() if pin.exists() else None,
        scratch_roots=scratch_roots, excluded=[runtime, *([bootstrap] if bootstrap else [])])
    result['candidate_sha256'] = intake['sha256']
    result['candidate_bytes'] = intake['bytes']
    result['bootstrap'] = str(Path(bootstrap).resolve()) if bootstrap else None
    return result


def prepare(root, runtime, boundary, *, preserved_source, recovery_brief=''):
    """Discard accessible first-owner material only after external export succeeds."""
    root, runtime = Path(root).resolve(), Path(runtime).resolve()
    if boundary.get('export_confirmed') is not True:
        raise ValueError('Recovery cannot reset source before outgoing evidence is exported')
    if boundary.get('source_base') != preserved_source['base']:
        raise ValueError('Recovery boundary and intake source base differ')
    if not isinstance(recovery_brief, str):
        raise ValueError('Recovery brief must be text')
    started = time.monotonic()
    ws = workspace.restore(root, preserved_source)
    clean = capture(root, ws.base)
    if (clean['sha256'] != preserved_source['candidate_sha256']
            or clean['bytes'] != preserved_source['candidate_bytes']):
        raise ValueError('Restored workdir differs from the supplied intake candidate')
    removed_scratch = workspace.clear_new_scratch(preserved_source)
    # The same Python process continues running deployed code. No earlier
    # request, response, patch, output, notes or prior-task memory survives here.
    for path in runtime.iterdir():
        if path.name in ('runtime', 'intake-source', 'deployed-source.txt', 'pid', 'stop-request'):
            continue
        if path.name in ('solver.stdout', 'solver.stderr', 'launcher.log') and path.is_file() and not path.is_symlink():
            # The launcher/worker still own descriptors for these inodes.
            # Truncate exported first-owner bytes without losing later output.
            path.write_bytes(b'')
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    # Remove prior-owner context from launch configuration. The active worker
    # already loaded it; retain execution settings only.
    if preserved_source.get('bootstrap'):
        bootstrap = Path(preserved_source['bootstrap'])
        config_path = bootstrap / 'launch.json'
        if config_path.is_file() and not config_path.is_symlink():
            config = json.loads(config_path.read_text())
            config['recovery'] = None
            config['memory'] = None
            config_path.write_text(json.dumps(config))
        for path in bootstrap.iterdir():
            if path.name in ('solver.py', 'launcher.py', 'stop.py', 'launch.json'):
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    receipt = {'owner': 'standard', 'phase': 'recovery', 'source_base': ws.base,
               'workspace': str(root), 'candidate_applied': False,
               'workspace_preparation_seconds': time.monotonic() - started,
               'intake_snapshot': {key: preserved_source[key] for key in
                                   ('files', 'file_bytes', 'archive_bytes', 'snapshot_seconds')},
               'recovery_brief': recovery_brief,
               'preparation': 'Original workdir and Git restored from pre-owner intake snapshot; outgoing evidence exported outside this sandbox.',
               'scratch_paths_removed': len(removed_scratch)}
    # Do not retain names of removed scratch files in the fresh owner's files.
    # They can themselves reveal the previous approach. The export is external.
    (runtime / 'recovery-preparation.json').write_text(json.dumps(receipt, indent=2))
    return ws, receipt


def observation(receipt):
    facts = {key: receipt[key] for key in ('source_base', 'workspace', 'candidate_applied', 'preparation')}
    return ('\n\n[Fresh recovery preparation — before your first action]\n'
            'Your repository starts at the original source and supplied environment. '
            'The previous attempt\'s patch and investigation are not available to this owner. '
            'The harness privately retains its selected candidate for finalization.\n'
            + json.dumps(facts, indent=2))


def brief_reference(receipt):
    """Original-source advice has a different lifetime from preparation facts."""
    brief = receipt.get('recovery_brief')
    if not brief:
        return ''
    return ('[Reviewer brief — historical, unverified reference]\n'
            f"Source basis: public task and original source {receipt['source_base']}.\n"
            'These leads are not additional requirements or current execution facts. '
            'Your later observations and revised understanding may contradict or supersede them.\n\n'
            + brief + '\n[End reviewer brief]')
