"""Workspace effects. Runs inside the task sandbox as well as on a local machine.

The model can execute any shell command. These checks protect harness operation
identity and private job state; they are not an OS security sandbox.
"""
import errno
import difflib
import fnmatch
import stat
import tempfile
import fcntl
import pty
import select
import signal
import socket
import termios
import base64
import bisect
import itertools
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid


def dump(path, value):
    path = Path(path)
    tmp = path.with_suffix('.pending')
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


def clean_env():
    return {k: v for k, v in os.environ.items()
            if k not in {'A4_API_KEY', 'CS2680_API_KEY', 'OPENROUTER_API_KEY'}}


def git(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace'))
    return p.stdout


def capture(root, destination, base=None):
    """Use a private index, so untracked files enter the diff without staging edits."""
    root, destination = Path(root), Path(destination)
    base = base or git(root, 'rev-parse', 'HEAD').decode().strip()
    index = destination.with_suffix('.index')
    env = dict(clean_env(), GIT_INDEX_FILE=str(index))
    def run(*args):
        p = subprocess.run(['git', '-C', str(root), *args], env=env, capture_output=True)
        if p.returncode:
            raise RuntimeError(p.stderr.decode(errors='replace'))
        return p.stdout
    try:
        run('read-tree', base)
        run('add', '-A', '--', '.')
        patch = run('diff', '--cached', '--binary', '--full-index', '--no-ext-diff', base, '--', '.')
        destination.write_bytes(patch)
        return {'base': base, 'sha256': hashlib.sha256(patch).hexdigest(),
                'bytes': len(patch), 'patch_path': str(destination)}
    finally:
        index.unlink(missing_ok=True)


def job_path(workspace, ident):
    if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
        raise ValueError('Invalid job ID')
    path = Path(workspace['state']) / 'jobs' / ident
    if not path.is_dir():
        raise ValueError('Unknown job in this workspace')
    return path


def utf8_prefix(raw, count):
    """Include the end of a codepoint, so a small page still makes progress."""
    end = min(len(raw), count)
    while end < len(raw) and raw[end] & 0xC0 == 0x80:
        end += 1
    return raw[:end]


def page(path, offset=0, count=16000, raw=False):
    with Path(path).open('rb') as f:
        total = os.fstat(f.fileno()).st_size
        f.seek(offset)
        data = f.read(count if raw or count == 0 else count + 3)
        if not raw and count:
            data = utf8_prefix(data, count)
    return {('base64' if raw else 'content'): base64.b64encode(data).decode() if raw else data.decode(errors='replace'), 'offset': offset,
            'next_offset': offset + len(data), 'total_bytes': total,
            'complete': offset + len(data) >= total}


def command_preview(path, max_bytes=12000):
    """Literal beginning/end ranges from one observed output file; no log interpretation."""
    with Path(path).open('rb') as stream:
        total = os.fstat(stream.fileno()).st_size
        if total <= max_bytes:
            return None  # The ordinary complete page already conveys this output.
        head_size = max_bytes // 2
        head = utf8_prefix(stream.read(head_size + 3), head_size)
        # Avoid cutting a UTF-8 codepoint at either range boundary, and prefer
        # whole lines when a newline is near the cut. Offsets stay exact.
        cut = head.rfind(b'\n')
        if cut >= head_size // 2:
            head = head[:cut + 1]
        start = total - (max_bytes - head_size)
        stream.seek(start)
        tail = stream.read(total - start)
        if len(tail) != total - start:
            raise OSError('Output changed length during preview; use the original page or observe again')
        while tail and tail[0] & 0xC0 == 0x80:
            tail = tail[1:]
            start += 1
        cut = tail.find(b'\n')
        if 0 <= cut < len(tail) // 2:
            tail = tail[cut + 1:]
            start += cut + 1
        end = len(head)
    return {'total_bytes': total, 'omitted_range': [end, start],
            'ranges': [{'offset': 0, 'next_offset': end, 'content': head.decode(errors='replace')},
                       {'offset': start, 'next_offset': total, 'content': tail.decode(errors='replace')}],
            'meaning': 'Observed beginning and end, not a contiguous page or a change since the previous poll.'}


def observe(workspace, ident, offset=0, count=16000):
    path = job_path(workspace, ident)
    info = json.loads((path / 'request.json').read_text())
    result = json.loads((path / 'result.json').read_text()) if (path / 'result.json').exists() else {'state': 'running'}
    # Distinguish a missing launcher from a legitimate running process.
    if result['state'] == 'running' and ((path / 'started.json').exists() or (path / 'launcher.json').exists()):
        receipt = path / ('started.json' if (path / 'started.json').exists() else 'launcher.json')
        pid = json.loads(receipt.read_text())['pid']
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            result = {'state': 'unknown', 'error': 'Job process disappeared without a completion receipt; do not replay automatically'}
    observed = time.time()
    return {**info, **result, 'elapsed_seconds': max(0, result.get('finished_at', observed) - info['started_at']) if info.get('started_at') is not None else None,
            'cancellation_remaining_seconds': max(0, info['cancel_at'] - observed) if info.get('cancel_at') is not None and result['state'] == 'running' else None,
            'id': ident, 'output_ref': 'job:' + ident,
            'output_path': str(path / 'output'), 'output': page(path / 'output', offset, count)}


def socket_path(ident):
    # Unix sockets have a short path limit, independent of evidence directory length.
    root = Path('/tmp') / ('investigator-jobs-' + str(os.getuid()))
    root.mkdir(mode=0o700, exist_ok=True)
    if root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise RuntimeError('Job control directory must be private to this user')
    return root / (ident + '.sock')


def worker(path):
    path = Path(path)
    request = json.loads((path / 'request.json').read_text())
    dump(path / 'started.json', {'pid': os.getpid()})
    control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    address = socket_path(path.name)
    proc = None
    master = slave = None
    try:
        control.bind(str(address))
        control.listen(4)
        argv = [request['shell'], '-c', request['command']]
        if request['tty']:
            master, slave = pty.openpty()
            def terminal_session():
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            proc = subprocess.Popen(argv, cwd=request['cwd'], stdin=slave, stdout=slave,
                                    stderr=slave, env=clean_env(), preexec_fn=terminal_session)
            os.close(slave)
            slave = None
            output_fd = input_fd = master
        else:
            proc = subprocess.Popen(argv, cwd=request['cwd'], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env=clean_env(), start_new_session=True)
            output_fd, input_fd = proc.stdout.fileno(), proc.stdin.fileno()
        os.set_blocking(output_fd, False)
        os.set_blocking(input_fd, False)
        input_open, output_open = True, True
        dump(path / 'process.json', {'pid': proc.pid, 'process_group': proc.pid})
        cancellation = None
        cancel_after = request.get('kill_after_ms')
        cancel_deadline = time.monotonic() + max(0, request['cancel_at'] - time.time()) if cancel_after is not None else None
        with (path / 'output').open('ab', buffering=0) as out:
            while output_open or proc.poll() is None:
                if cancel_deadline is not None and cancellation is None and time.monotonic() >= cancel_deadline:
                    cancellation = {'requested_by': 'model', 'kill_after_ms': cancel_after,
                                    'signal': 'SIGKILL', 'attempted_at': time.time()}
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                        cancellation['signal_sent'] = True
                    except ProcessLookupError:
                        cancellation['signal_sent'] = False
                    dump(path / 'cancellation.json', cancellation)
                ready, _, _ = select.select([control] + ([output_fd] if output_open else []), [], [], .05)
                if output_open and output_fd in ready:
                    try:
                        chunk = os.read(output_fd, 65536)
                    except OSError as e:
                        if e.errno == errno.EIO and request['tty']:
                            chunk = b''
                        elif e.errno == errno.EAGAIN:
                            continue
                        else:
                            raise
                    if chunk:
                        out.write(chunk)
                    else:
                        output_open = False
                if control in ready:
                    conn, _ = control.accept()
                    try:
                        with conn:
                            conn.settimeout(2)
                            raw = bytearray()
                            while not raw.endswith(b'\n') and len(raw) <= 1000000:
                                part = conn.recv(65536)
                                if not part:
                                    break
                                raw.extend(part)
                            action = json.loads(raw)
                            receipt = {'request_id': action['request_id'], 'action': action['action'], 'executed': False}
                            try:
                                if action['action'] == 'input':
                                    if not input_open:
                                        raise ValueError('stdin is closed')
                                    original = action['chars'].encode('utf-8')
                                    start = action.get('input_offset', 0)
                                    data = original[start:]
                                    receipt.update(bytes_written=0, requested_bytes=len(data),
                                        input_offset=start, next_input_offset=start, input_complete=False)
                                    count = os.write(input_fd, data) if data else 0
                                    receipt.update(executed=True, bytes_written=count,
                                        next_input_offset=start+count, input_complete=start+count==len(original))
                                elif action['action'] == 'close_stdin':
                                    if request['tty']:
                                        raise ValueError('A terminal has no stdin half-close; send its EOF character with input or signal the job')
                                    if input_open:
                                        proc.stdin.close()
                                        input_open = False
                                    receipt.update(executed=True, stdin_closed=True)
                                else:
                                    sig = {'interrupt': signal.SIGINT, 'terminate': signal.SIGTERM, 'kill': signal.SIGKILL}[action['action']]
                                    os.killpg(proc.pid, sig)
                                    receipt.update(executed=True, signal=sig.name)
                            except (OSError, ValueError) as e:
                                receipt['error'] = str(e)
                            dump(path / ('control-' + action['request_id'] + '.json'), {'request': action, 'receipt': receipt})
                            conn.sendall(json.dumps(receipt).encode() + b'\n')
                    except (OSError, ValueError, KeyError):
                        # A disconnected control client must not stop output capture
                        # or the child process. Executed controls already have receipts.
                        continue
        code = proc.wait()
        dump(path / 'result.json', {'state': 'exited', 'exit_code': code, 'finished_at': time.time(), 'cancellation': cancellation})
    except Exception as e:
        # A failed supervisor is not evidence that a started command exited.
        dump(path / 'result.json', {'state': 'unknown' if proc else 'exited', 'exit_code': None,
                                  'error': str(e), 'finished_at': time.time()})
    finally:
        control.close()
        address.unlink(missing_ok=True)
        for fd in (master, slave):
            if fd is not None:
                os.close(fd)
        if proc:
            for stream in (proc.stdin, proc.stdout):
                if stream:
                    stream.close()


def wait_job(ws, ident, yield_time_ms):
    end = time.monotonic() + yield_time_ms / 1000
    while True:
        result = observe(ws, ident, count=0)
        if result['state'] != 'running' or time.monotonic() >= end:
            return
        time.sleep(min(.025, max(0, end - time.monotonic())))


def present_job(ws, ident, offset=None, count=16000):
    """A default observation advances the cursor past exactly the bytes it shows.

    Long output first observed from byte zero shows its literal beginning and
    end; the omitted middle stays retrievable by explicit offset. Explicit
    offsets select one contiguous page and leave the cursor unchanged.
    """
    path = job_path(ws, ident)
    with (path / 'cursor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cursor = path / 'cursor.json'
        start = offset if offset is not None else (json.loads(cursor.read_text()) if cursor.exists() else 0)
        result = observe(ws, ident, start, count)
        preview_bytes = min(count, 12000)
        if offset is None and start == 0 and result['output']['total_bytes'] > preview_bytes:
            try:
                preview = command_preview(path / 'output', preview_bytes)
            except OSError as error:
                result['output_preview_error'] = str(error)
            else:
                if preview:
                    result['output_preview'] = preview
        if offset is None:
            preview = result.get('output_preview')
            dump(cursor, preview['total_bytes'] if preview else result['output']['next_offset'])
        return result


def control_job(ws, ident, action, chars, input_offset=0):
    path = job_path(ws, ident)
    request = {'request_id': uuid.uuid4().hex, 'action': action, 'chars': chars, 'input_offset':input_offset}
    # Connection errors before send are definite non-execution. After send, an
    # absent acknowledgement is uncertain and is never automatically replayed.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(3)
        try:
            conn.connect(str(socket_path(ident)))
        except OSError as e:
            return {'action': action, 'executed': False, 'error': str(e)}
        dump(path / ('control-intent-' + request['request_id'] + '.json'), request)
        try:
            conn.sendall(json.dumps(request).encode() + b'\n')
            raw = bytearray()
            while not raw.endswith(b'\n'):
                part = conn.recv(65536)
                if not part:
                    raise RuntimeError('Control connection closed without acknowledgement')
                raw.extend(part)
            return json.loads(raw)
        except (OSError, RuntimeError, ValueError) as e:
            return {'action': action, 'execution_state': 'unknown', 'error': str(e),
                    'request_id': request['request_id'], 'replay_safe': False}


def bounded(data, key, default, minimum, maximum):
    value = data.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{key} must be {minimum}..{maximum}')
    return value


def snapshot_bytes(ws, raw, **metadata):
    """Retain exact bytes outside mutable source before returning a presentation page."""
    directory = Path(ws['state']) / 'artifacts'
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    path = directory / digest
    if not path.exists():
        temporary = directory / (uuid.uuid4().hex + '.pending')
        try:
            temporary.write_bytes(raw)
            try:
                os.link(temporary, path)  # Publish complete bytes exclusively.
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    if path.stat().st_size != len(raw) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Existing immutable file snapshot does not match its content identity')
    return dict(path=str(path), bytes=len(raw), sha256=digest, **metadata)


def source_path(ws, value):
    # Resolve symlinks intentionally: edits preserve the link and change its target.
    return (Path(ws['root']) / value).resolve()


def read_file(ws, data):
    path = source_path(ws, data['path'])
    if not path.exists():
        detail = ''
        if '#' in str(path) and Path(str(path).split('#', 1)[0]).is_file():
            detail = ' This looks like a Markdown section link; read the file path before # and search for the heading.'
        raise FileNotFoundError('No file exists at ' + str(path) + '.' + detail)
    if path.is_dir():
        raise IsADirectoryError(str(path) + ' is a directory; use search_files or the shell to list its files.')
    if not path.is_file():
        raise ValueError('read_file requires a regular file; use shell for devices or streams')
    raw = path.read_bytes()
    saved = snapshot_bytes(ws, raw, source_path=str(path), purpose='file_snapshot')
    lines = raw.splitlines(keepends=True)
    ends = list(itertools.accumulate(map(len, lines)))
    if data.get('offset') is not None:
        start, end = data['offset'], len(raw)
    else:
        first, count = data.get('start_line', 1), data.get('line_count', 200)
        start = sum(map(len, lines[:first-1]))
        end = start + sum(map(len, lines[first-1:first-1+count]))
    part = raw[start:min(end, start+data.get('max_output_bytes', 16000))]
    return dict(path=str(path), sha256=saved['sha256'], total_bytes=len(raw), total_lines=len(lines),
        range_kind='bytes' if data.get('offset') is not None else 'lines', offset_unit='bytes', line_number_origin=1,
        offset=start, next_offset=start+len(part), range_end_offset=end,
        first_displayed_line=bisect.bisect_right(ends,start)+1 if part else None,
        last_displayed_line=bisect.bisect_right(ends,start+len(part)-1)+1 if part else None,
        complete=start+len(part)>=len(raw), range_complete=start+len(part)>=end,
        content=part.decode('utf-8', errors='replace'),
        display_encoding='UTF-8 with replacement; snapshot preserves original bytes',
        _artifacts={'snapshot_ref':saved})


def edit_file(ws, data):
    path = source_path(ws, data['path'])
    operation = data['operation']
    if operation not in {'create','write','replace','delete'}:
        return dict(applied=False, error='Unknown edit operation', path=str(path))
    exists = path.exists()
    if exists and not path.is_file():
        return dict(applied=False, error='Target is not a regular file', path=str(path))
    before = path.read_bytes() if exists else None
    current_hash = hashlib.sha256(before).hexdigest() if exists else None
    expected = data.get('expected_sha256')
    if expected is not None and expected != current_hash:
        return dict(applied=False, error='Source hash differs; inspect current source or supply the intended hash',
                    path=str(path), current_sha256=current_hash)
    if operation == 'create' and exists:
        return dict(applied=False, error='File already exists; use write to replace intentionally', path=str(path))
    if operation in {'replace','delete'} and not exists:
        return dict(applied=False, error='File does not exist', path=str(path))
    counts = []
    if operation == 'replace':
        try:
            text = before.decode('utf-8')
        except UnicodeDecodeError:
            return dict(applied=False, error='Exact replacement requires UTF-8 text; use shell for binary edits', path=str(path))
        for i, replacement in enumerate(data['replacements']):
            old, new = replacement['old'], replacement['new']
            count = text.count(old)
            counts.append(count)
            if count != replacement.get('count', 1):
                return dict(applied=False, path=str(path), failed_replacement=i,
                    match_count=count, expected_count=replacement.get('count',1),
                    error='Exact match count differs; no replacements applied. Include distinguishing context or specify the intended count.')
            text = text.replace(old, new)
        after = text.encode('utf-8')
    elif operation in {'create','write'}:
        after = data['content'].encode('utf-8')
    else:
        after = None
    artifacts = {}
    if before is not None:
        artifacts['before_ref'] = snapshot_bytes(ws, before, source_path=str(path), purpose='before_edit')
    if after is not None:
        artifacts['after_ref'] = snapshot_bytes(ws, after, source_path=str(path), purpose='intended_after_edit')
    diff = ''.join(difflib.unified_diff(
        (before or b'').decode('utf-8', errors='replace').splitlines(keepends=True),
        (after or b'').decode('utf-8', errors='replace').splitlines(keepends=True),
        fromfile=str(path) if before is not None else '/dev/null',
        tofile=str(path) if after is not None else '/dev/null')).encode('utf-8')
    artifacts['diff_ref'] = snapshot_bytes(ws, diff, purpose='display_diff')
    result = dict(path=str(path), operation=operation, before_sha256=current_hash,
        after_sha256=hashlib.sha256(after).hexdigest() if after is not None else None,
        match_counts=counts, changed=False,
        diff=diff[:16000].decode(errors='replace'), diff_complete=len(diff)<=16000,
        _artifacts=artifacts)
    temporary = None
    committed = False
    try:
        if after is not None:
            if data.get('create_parents', False):
                path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'.', dir=path.parent)
            with os.fdopen(fd, 'wb') as f:
                f.write(after)
                f.flush()
                os.fsync(f.fileno())
            if exists:
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            else:
                # mkstemp uses 0600; new source files use normal creation permissions.
                mask = os.umask(0)
                os.umask(mask)
                os.chmod(temporary, 0o666 & ~mask)
        now = path.read_bytes() if path.exists() else None
        if now != before:
            return dict(result, applied=False, error='Source changed during preparation; file not edited')
        if after is None:
            path.unlink()
        elif operation == 'create':
            # Exclusive link avoids clobbering a file created after the existence check.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        committed = True
        return dict(result, applied=True, changed=before!=after)
    except OSError as e:
        return dict(result, applied=committed, error=str(e))
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def search_files(ws, data):
    root = source_path(ws, data.get('path', '.'))
    pattern = data.get('glob', '**')
    excludes = data.get('exclude', ['.git/**'])
    query = data.get('query')
    insensitive = data.get('ignore_case', False)
    if data.get('regex', False):
        raise ValueError('search_files matches literal text. Use exec_command with grep or another search program for regular expressions; its process remains observable and controllable through job.')
    def matches(path, pat):
        return fnmatch.fnmatchcase(path, pat) or ('/' not in pat and fnmatch.fnmatchcase(Path(path).name, pat))
    errors, skipped_binary, records = [], 0, []
    def walk_error(e):
        errors.append(dict(path=e.filename, error=str(e)))
    if not root.exists():
        raise FileNotFoundError(str(root))
    if not (root.is_file() or root.is_dir()):
        raise ValueError('search_files requires a regular file or directory')
    def files_under_root():
        for directory, dirs, files in os.walk(root, onerror=walk_error, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not any(
                matches((Path(directory)/d).relative_to(root).as_posix()+'/', x) for x in excludes))
            for name in sorted(files):
                yield Path(directory)/name
    paths = [root] if root.is_file() else files_under_root()
    for path in paths:
        relative = path.name if root.is_file() else path.relative_to(root).as_posix()
        if not matches(relative,pattern) or any(matches(relative,x) for x in excludes):
            continue
        if query is None:
            records.append(dict(path=str(path), relative_path=relative))
            continue
        try:
            if not path.is_file():
                raise OSError('Not a regular readable file')
            raw = path.read_bytes()
            if b'\0' in raw:
                skipped_binary += 1
                continue
            text = raw.decode('utf-8')
            digest = hashlib.sha256(raw).hexdigest()
            offset = 0
            for line_no, line in enumerate(text.splitlines(keepends=True),1):
                found = query.casefold() in line.casefold() if insensitive else query in line
                if found:
                    records.append(dict(path=str(path), line=line_no, byte_offset=offset,
                                        text=line, sha256=digest))
                offset += len(line.encode('utf-8'))
        except UnicodeDecodeError:
            skipped_binary += 1
        except OSError as e:
            errors.append(dict(path=str(path), error=str(e)))
    raw = ''.join(json.dumps(record,ensure_ascii=False)+'\n' for record in records).encode('utf-8')
    saved = snapshot_bytes(ws, raw, root=str(root), purpose='search_results')
    part = raw[:data.get('max_output_bytes',16000)]
    display_entries = []
    display_bytes = 0
    for row in raw.splitlines(keepends=True):
        if display_bytes + len(row) > len(part):
            break
        display_entries.append(json.loads(row))
        display_bytes += len(row)

    return dict(search_page=dict(version=1, entries=display_entries, next_offset=display_bytes, complete=display_bytes==len(raw)), root=str(root), glob=pattern, exclude=excludes, query=query,
        regex=data.get('regex',False), ignore_case=insensitive,
        follows_directory_symlinks=False, includes_hidden_files=True,
        matches=len(records), skipped_binary_files=skipped_binary, errors=errors,
        scan_complete=not errors, total_bytes=len(raw), next_offset=len(part),
        complete=len(part)==len(raw), content=part.decode(errors='replace'),
        format='JSON lines; a byte page may end within a record',
        _artifacts={'results_ref':saved})


def dispatch(op, data):
    ws = data['workspace']
    if op in {'read_file', 'edit_file', 'search_files'}:
        return globals()[op](ws, data)
    if op in {'shell', 'job'}:
        wait_ms = bounded(data, 'yield_time_ms', 10000, 0, 2**63-1)
        count = bounded(data, 'max_output_bytes', 16000, 1, 65536)
        if op == 'job':
            ident = data['id']
            job_path(ws, ident)
            action = data.get('action', 'poll')
            if action not in {'poll', 'input', 'close_stdin', 'interrupt', 'terminate', 'kill'}:
                raise ValueError('Unknown job action')
            if action == 'input' and (not isinstance(data.get('chars'), str) or not 0 < len(data['chars'].encode()) <= 65536):
                raise ValueError('input requires chars containing 1..65536 UTF-8 bytes')
            if action != 'input' and data.get('chars') is not None:
                raise ValueError('chars is valid only for input')
            offset = data.get('offset')
            if offset is not None and (type(offset) is not int or offset < 0):
                raise ValueError('offset must be nonnegative')
            input_offset = data.get('input_offset',0)
            if action == 'input':
                if type(input_offset) is not int or not 0 <= input_offset <= len(data['chars'].encode('utf-8')):
                    raise ValueError('input_offset must be within the UTF-8 bytes of chars')
            elif 'input_offset' in data:
                raise ValueError('input_offset is valid only for input')
            receipt = control_job(ws, ident, action, data.get('chars'), input_offset) if action != 'poll' else None
            if data.get('_defer_output'):
                result = observe(ws, ident, count=0)
            else:
                wait_job(ws, ident, wait_ms)
                result = present_job(ws, ident, offset, count)
            return dict(result, control=receipt) if receipt else result
        command = data['command']
        if not isinstance(command, str) or not command.strip():
            raise ValueError('command must be nonempty')
        shell = data.get('shell', '/bin/sh')
        if not isinstance(shell, str) or not Path(shell).is_absolute() or not os.access(shell, os.X_OK):
            raise ValueError('shell must name an executable by absolute path')
        tty = data.get('tty', False)
        if type(tty) is not bool:
            raise ValueError('tty must be boolean')
        cwd = str((Path(ws['root']) / (data.get('cwd') or ws['root'])).resolve())
        if not Path(cwd).is_dir():
            raise ValueError('cwd must be an existing directory')
        kill_after = data.get('kill_after_ms')
        if kill_after is not None and (type(kill_after) is not int or kill_after < 0):
            raise ValueError('kill_after_ms must be a nonnegative integer or omitted')
        ident = data.get('_job_id') or uuid.uuid4().hex
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('Invalid launch job ID')
        path = Path(ws['state']) / 'jobs' / ident
        path.mkdir(parents=True)
        (path / 'output').touch()
        started = time.time()
        dump(path / 'request.json', {'command': command, 'cwd': cwd, 'shell': shell,
                                    'tty': tty, 'started_at': started, 'kill_after_ms': kill_after,
                                    'cancel_at': started + kill_after / 1000 if kill_after is not None else None})
        with (path / 'launcher.log').open('wb') as log:
            launcher = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--job', str(path)],
                             stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True,
                             env=clean_env(), close_fds=True)
        launcher.stdin.close()
        dump(path / 'launcher.json', {'pid': launcher.pid})
        if data.get('_defer_output'):
            return observe(ws, ident, count=0)
        wait_job(ws, ident, wait_ms)
        return present_job(ws, ident, count=count)
    if op == 'wait':
        wait_job(ws, data['id'], bounded(data, 'yield_time_ms', 10000, 0, 2**63-1))
        return observe(ws, data['id'], count=0)
    if op == 'observe':
        return observe(ws, data['id'], data.get('offset', 0))
    if op == 'read_output':
        ref = data['ref']
        if not ref.startswith('job:'):
            raise ValueError('Expected job:ID output reference')
        path = job_path(ws, ref[4:])
        return {'ref': ref, **page(path / 'output', data.get('offset', 0), data.get('bytes', 16000), data.get('raw',False))}
    if op == 'capture':
        dest = Path(ws['state']) / ('candidate-' + uuid.uuid4().hex + '.patch')
        first = capture(ws['root'], dest, ws.get('base'))
        check = dest.with_suffix('.check.patch')
        second = capture(ws['root'], check, ws.get('base'))
        check.unlink(missing_ok=True)
        if first['sha256'] != second['sha256']:
            return {'error': 'Source changed during capture; no candidate graded', 'executed': False}
        return first
    if op == 'snapshot':
        parent = Path(ws['state']) / ('review-' + uuid.uuid4().hex)
        parent.mkdir()
        before = capture(ws['root'], parent / 'source.patch', ws.get('base')) if ws.get('capture_patch', True) else None
        target = parent / 'repository'
        # Copy file bytes, including ignored environment files. Dereference links
        # rather than leaving writable links back to the owner's workspace.
        shutil.copytree(ws['root'], target, symlinks=False,
                        ignore=lambda p, names: ['.git'] if '.git' in names else [])
        if before is None:
            return {'root': str(target), 'state': str(parent / 'state'),
                    'role': 'reviewer', 'base': None, 'capture_patch': False}
        after = capture(ws['root'], parent / 'after.patch', ws.get('base'))
        if (before['base'], before['sha256']) != (after['base'], after['sha256']):
            raise RuntimeError('Source changed during snapshot; no review started')
        git(target, 'init', '-q')
        # New repository metadata, index and stash. No shared worktree administration.
        git(target, 'add', '-A')
        git(target, '-c', 'user.name=Harness', '-c', 'user.email=harness@local',
            'commit', '-qm', 'Review snapshot', '--allow-empty')
        return {'root': str(target), 'state': str(parent / 'state'), 'role': 'reviewer',
                'source_candidate': before, 'base': git(target, 'rev-parse', 'HEAD').decode().strip()}
    if op == 'patch':
        return page(data['path'], data.get('offset',0), data.get('bytes',65536), raw=True)
    raise ValueError('Unknown workspace operation ' + op)


if __name__ == '__main__':
    if sys.argv[1] == '--job':
        worker(sys.argv[2])
    else:
        if sys.argv[1] == '--stdin':
            request = json.load(sys.stdin)
        elif sys.argv[1] == '--request-file':
            request_path = Path(sys.argv[2])
            request = json.loads(request_path.read_text())
            request_path.unlink()
        else:
            request = json.loads(base64.b64decode(sys.argv[1]))
        try:
            result = dispatch(request['op'], request['payload'])
        except Exception as e:
            result = {'error': str(e), 'operation': request['op'], 'execution_state': 'not_confirmed'}
        print(json.dumps(result))
