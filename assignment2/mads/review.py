"""An optional, non-judging view of the task beside actual repository changes."""
import hashlib
import os
from pathlib import Path
import subprocess

from .store import view


class GitReviewError(ValueError):
    """Git could not provide a comparison; distinct from lost journal evidence."""


def capture(tools):
    """Best-effort automatic review; callers can still do their primary work."""
    try:
        return tools.review(output_chars=0)
    except GitReviewError as exc:
        notice = 'Optional Git review unavailable; no change comparison captured.'
        record = tools.store.add('change_review_unavailable', error=str(exc), notice=notice)
        return {'observation_record': record, 'notice': notice,
                'scope': {'tracked_diff': False, 'git_unavailable': str(exc)}}


def changes(tools, paths=None, base='HEAD', output_chars=18000):
    """Capture a review, not a completion gate or an atomic repository snapshot."""
    repo = tools.repo
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_PAGER='cat')

    def git(*args):
        try:
            result = subprocess.run(['git', '-C', str(repo), *args], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise GitReviewError('git review failed: '+str(exc)) from exc
        if result.returncode:
            raise GitReviewError('git review failed: '+result.stderr.decode('utf-8', 'replace'))
        return result.stdout

    pathspecs, selected_names = [], []
    for name in paths or []:
        # Lexical normalization permits a changed symlink itself to be reviewed.
        path = Path(os.path.abspath(repo / Path(name).expanduser()))
        try:
            relative = path.relative_to(repo)
            if path != repo:
                path.parent.resolve().relative_to(repo.resolve())
        except ValueError:
            raise ValueError('review paths must be inside the repository') from None
        selected_names.append(str(relative))
        pathspecs.append(':(literal)'+str(relative))

    # Source copies are useful workspaces too. Do not hide the supplied task just
    # because Git metadata is absent or unavailable; never invent a baseline.
    unavailable = None
    try:
        if git('rev-parse', '--is-inside-work-tree').strip() != b'true':
            unavailable = 'Not a Git working tree'
    except GitReviewError as exc:
        unavailable = str(exc)
    commit, diff, status = None, b'', b''
    if unavailable is None:
        # Resolve first so later commands cannot interpret base as an option.
        try:
            commit = git('rev-parse', '--verify', '--end-of-options', base+'^{commit}').decode().strip()
        except GitReviewError as exc:
            if base != 'HEAD':
                raise
            # An initialized repository need not have a first commit. Report
            # the missing baseline without inventing an empty/clean diff.
            unavailable = 'Default HEAD comparison unavailable: '+str(exc)
    if unavailable is None:
        diff = git('diff', '--no-ext-diff', '--no-textconv', '--no-color',
                   '--binary', commit, '--', *pathspecs)
        status = git('status', '--short', '--untracked-files=all', '--', *pathspecs)
        untracked = git('ls-files', '--others', '--exclude-standard', '-z', '--', *pathspecs)
        names = [os.fsdecode(name) for name in untracked.split(b'\0') if name]
    else:
        # No scan or implicit ignored-file inclusion without Git. Explicit paths
        # are current snapshots only; directories/missing files report unavailable.
        names = list(dict.fromkeys(selected_names))
    task = tools.original_task
    task_only = unavailable is not None and not names
    coverage_notice = ('TASK ONLY: no implementation files or diff were captured. '
                       'Repeat review with explicit file paths to inspect current source.'
                       if task_only else None)
    sections = [
        '# Change review — observations, not a correctness verdict\n',
        (coverage_notice+'\n' if coverage_notice else ''),
        'Original task below is the exact initial supplied task, not an agent summary. '
        'Later user corrections remain in the conversation; this view does not replace them. '
        'No tests are run. Files and Git state are sampled sequentially, not atomically.\n',
        ('The diff includes staged and unstaged tracked changes against the resolved commit. '
         'Untracked files are separate. Ignored files are excluded.\n' if unavailable is None else
         'Git comparison unavailable: '+unavailable+'\n'
         'No baseline or change status is inferred. Only explicitly selected current files '
         'are sampled; directory traversal and ignored-file classification are unavailable. '
         'Use paths to request file snapshots.\n'),
        f'Base commit: {commit}\nRequested base: {base}\n'
        f'Paths: {paths or ("whole repository" if unavailable is None else "none selected")}\n'
        f'Task SHA256: {hashlib.sha256(task.encode()).hexdigest()}\n',
        '## Original task\n'+(task if task else '[No original task supplied to these Tools]')+'\n',
        '## Working-tree status\n'+(status.decode('utf-8', 'backslashreplace') if unavailable is None else '[Unavailable]'),
        '## Tracked changes\n'+((diff.decode('utf-8', 'backslashreplace') or '[No tracked changes]') if unavailable is None else '[Unavailable; not an empty diff]')+'\n',
        '## Untracked files\n' if unavailable is None else '## Explicitly selected current files (not changes)\n'
    ]
    # Limit review expansion, not capture/retrieval of a selected file. Scope is explicit.
    snapshots = []
    for name in names[:20]:
        path = repo / name
        sections.append(f'### {name!r}\n')
        if path.is_symlink():
            sections.append('Symlink target: '+repr(os.readlink(path))+'\n')
            continue
        try:
            artifact = tools.store.snapshot(path)
        except OSError as exc:
            sections.append('Unavailable at sample: '+str(exc)+'\n')
            continue
        artifact['observation_kind'] = 'new_file_snapshot'
        record = tools.store.evidence(artifact, kind='file_read',
                                      selected_byte_range=[0, artifact['observed_bytes']])
        snapshots.append(record)
        sections.append(f'Exact snapshot: {artifact["path"]}; record {record}; '
                        f'{artifact["observed_bytes"]} bytes\n')
        data = Path(artifact['path']).read_bytes()
        try:
            text = data.decode('utf-8') if b'\0' not in data else None
        except UnicodeError:
            text = None
        if text is None:
            sections.append('[Binary/non-UTF-8; read snapshot with encoding=base64]\n')
        else:
            sections.append(text[:16000]+'\n')
            if len(text) > 16000:
                sections.append('[File excerpt only: first 16000 characters. Read exact snapshot for remainder.]\n')
    if len(names) > 20:
        sections.append('Not expanded (narrow paths or read individually): '+repr(names[20:])+'\n')
    if not names:
        sections.append('[None]\n')
    sections.append('\n## Review questions (not findings)\n'
                    'Compare the externally visible behavior in the task with the diff, including '
                    'callers and public entry points. Derive a concrete check from the task rather '
                    'than merely confirming the implementation. Which requirement or uncertainty '
                    'does each executed test actually discriminate? A passing self-authored test '
                    'can encode the same mistaken interpretation as the code.\n')
    artifact = tools.store.artifact(tools.store.blob('\n'.join(sections)))
    artifact['observation_kind'] = 'change_review'
    record = tools.store.evidence(artifact, kind='change_review', base_commit=commit,
                                  paths=paths or [], file_records=snapshots,
                                  git_unavailable=unavailable)
    return {'output': {**view(artifact, output_chars=output_chars), 'action': 'review'},
            'observation_record': record, 'base_commit': commit,
            **({'notice': coverage_notice, 'suggested_next': 'review(paths=[explicit source files])'}
               if coverage_notice else {}),
            'scope': {'paths': paths or [], 'tracked_diff': unavailable is None,
                      'current_file_snapshots': len(snapshots),
                      'task_only': task_only,
                      'git_unavailable': unavailable,
                      'untracked_total': len(names) if unavailable is None else None,
                      'untracked_expanded': min(20, len(names)) if unavailable is None else None,
                      'selected_current_files': len(names) if unavailable is not None else None,
                      'ignored_files': 'excluded' if unavailable is None else 'not inferred; explicit paths only',
                      'tests': 'not run'},
            'meaning': 'Exact initial task with the stated evidence coverage; no semantic validation or completion gate'}
