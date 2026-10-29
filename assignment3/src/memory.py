"""Cross-task memory: earlier tasks' public text, notes and selected diffs, with a retrieval index."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import os
import uuid

# Notes are written and read by one submission process. Only notes carrying
# this identifier are presented, so files left by any other run are ignored.
RUN_ID = uuid.uuid4().hex


def _atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.memory-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def changed_files(patch):
    """Paths a unified diff touches, in order, from its own headers."""
    files = []
    for line in patch.splitlines():
        if line.startswith('diff --git a/'):
            path = line[len('diff --git a/'):].split(' b/', 1)[0]
            if path not in files:
                files.append(path)
    return files


def save(directory, task, repo, note, grade, *, public_task='', patch=b'', passed=True):
    """The note is interpretation; the task text and selected diff are the actual inputs and bytes."""
    path = Path(directory) / f'task-{task}.md'
    diff = patch.decode('utf-8', errors='replace') if isinstance(patch, bytes) else patch
    provenance = {'run_id': RUN_ID, 'task': task, 'repo': repo,
                  'note_sha256': hashlib.sha256(note.encode()).hexdigest(),
                  'task_sha256': hashlib.sha256(public_task.encode()).hexdigest(),
                  'diff_sha256': hashlib.sha256(diff.encode()).hexdigest(),
                  'files': changed_files(diff), 'passed': passed,
                  'candidate_id': grade.get('candidate_id'), 'patch_sha256': grade.get('patch_sha256'),
                  'attempt': grade.get('attempt'), 'tests_failed': grade.get('tests_failed'),
                  'tests_total': grade.get('tests_total')}
    _atomic(path.with_suffix('.task.txt'), public_task)
    _atomic(path.with_suffix('.patch'), diff)
    _atomic(path, note)
    _atomic(path.with_suffix('.json'), json.dumps(provenance, indent=2))
    return path


def _title(public_task):
    for line in public_task.splitlines():
        line = line.strip().lstrip('#').strip()
        if line and line.lower() != 'problem statement':
            return line[:160]
    return ''


def prepare(remote, directory, repo, *, runtime="/tmp/a3"):
    """Copy complete notes into owned sandbox evidence; present only an index.

    Files are versioned by content, so later tasks' writes cannot change this
    task's retained evidence. Repository matching is runtime metadata, not a
    semantic relevance judgment or endorsement of any observation.
    """
    entries = []
    for path in sorted(Path(directory).glob('task-*.md')):
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                note = stream.read()
            # Read exactly as written (newline=''); universal newlines would turn
            # CRLF into LF and fail the integrity check for that record.
            with path.with_suffix('.task.txt').open(encoding='utf-8', newline='') as stream:
                public_task = stream.read()
            with path.with_suffix('.patch').open(encoding='utf-8', newline='') as stream:
                diff = stream.read()
            provenance = json.loads(path.with_suffix('.json').read_text())
        except (OSError, ValueError):
            continue
        digest = hashlib.sha256(note.encode()).hexdigest()
        if (not isinstance(provenance, dict) or provenance.get('run_id') != RUN_ID
                or provenance.get('note_sha256') != digest
                or provenance.get('task_sha256') != hashlib.sha256(public_task.encode()).hexdigest()
                or provenance.get('diff_sha256') != hashlib.sha256(diff.encode()).hexdigest()):
            continue  # Not authored and recorded by this run, or changed since.
        target = str(Path(runtime) / 'prior-memory' / f'{path.stem}-{digest}.md')
        passed = provenance.get('passed', True)
        outcome = ('It passed grading.' if passed else
                   f"It did NOT pass: {provenance.get('tests_failed')} of {provenance.get('tests_total')} "
                   'required grading entries were failing, so its diff and diagnosis may be wrong.')
        contents = ('Cross-task memory: one task finished earlier in this run. ' + outcome +
                    ' The public task and diff are exact; the note is the solving model\'s interpretation, '
                    'not a requirement of your task.\n'
                    'Runtime provenance: ' + json.dumps(provenance) +
                    '\n\n# Public task of that earlier task\n' + public_task +
                    '\n\n# Model-authored note\n' + note +
                    '\n\n# Exact ' + ('passing' if passed else 'unsuccessful (did not pass)') +
                    ' diff\n```diff\n' + diff + '\n```\n')
        remote.post('/write', {'path': target, 'content': contents})
        entries.append({'task': provenance.get('task'),
                        'repo': provenance.get('repo'),
                        'same_repository': bool(repo and provenance.get('repo') == repo),
                        'title': _title(public_task), 'passed': passed,
                        'tests_failed': provenance.get('tests_failed'), 'tests_total': provenance.get('tests_total'),
                        'files': provenance.get('files', []),
                        'path': target, 'note_sha256': digest,
                        'candidate_id': provenance.get('candidate_id'),
                        'patch_sha256': provenance.get('patch_sha256')})
    if not entries:
        return ''
    entries.sort(key=lambda item: not item['same_repository'])
    index = str(Path(runtime) / 'prior-memory/index.json')
    remote.post('/write', {'path': index, 'content': json.dumps(entries, indent=2)})
    return ('Cross-task memory holds tasks finished earlier in this run, passed or not. Each record contains that task\'s public text, '
            'the solving model\'s note and the exact selected diff; each entry below lists whether it passed and the files its diff changed. '
            'A failed task\'s repository knowledge (how to run checks, where behavior lives) can still help; its diff and diagnosis did not pass. '
            'An earlier task neither establishes requirements nor identifies hidden tests for this one. '
            'Same-repository entries appear first; read a record with read_file when its files or subject overlap yours, '
            'or search the copied records with exec_command.\n'
            f'Memory index: {index}\n' + '\n'.join(json.dumps(item) for item in entries))


def evidence_paths(runtime):
    """Only this task's declared, owned note copies are maintenance evidence."""
    directory = Path(runtime).resolve() / 'prior-memory'
    index = directory / 'index.json'
    if not index.is_file():
        return []
    entries = json.loads(index.read_text())
    paths = [str(index)]
    for entry in entries:
        path = Path(entry['path']).resolve()
        if path.parent != directory or not path.is_file():
            raise ValueError('Memory index refers outside its owned evidence directory')
        paths.append(str(path))
    return paths
