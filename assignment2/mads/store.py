"""Append-only observations and immutable, streamed artifacts."""
import base64
import copy
import hashlib
import io
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

CHUNK = 65536
RECORD_LINKS = ('context_id', 'manifest_record', 'request_record', 'http_request_record',
                'response_record', 'assistant_record', 'invocation_id', 'observation_record',
                'output_record', 'exchange_id', 'attempt_record')


def record_links(record):
    """Explicit causal references only; numbers inside captured content are not links."""
    mappings = (record, record.get('result', {}))
    return {value for fields in mappings if isinstance(fields, dict)
            for key, value in fields.items() if key in RECORD_LINKS and type(value) is int}


def dump(value):
    # JSON escapes preserve lone surrogate code units without invalid UTF-8.
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(
        'utf-8', 'backslashreplace').decode('utf-8')


def atomic_json(path, value):
    """Serialize before touching disk; readers see a complete old or new result."""
    data = (dump(value)+'\n').encode('utf-8')
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
            temporary = Path(out.name)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def now():
    return datetime.now(timezone.utc).isoformat()


class EvidenceError(RuntimeError):
    pass


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs = self.root / 'blobs'
        self.blobs.mkdir(exist_ok=True)
        self.path = self.root / 'events.jsonl'
        self.events = []
        self.lock = threading.RLock()
        self.append_failure = None
        if self.path.exists():
            self.events = self._read_events()

    def _read_events(self):
        data = self.path.read_bytes() if self.path.exists() else b''
        if data and not data.endswith(b'\n'):
            raise EvidenceError('Interrupted journal tail; further appends refused; original bytes preserved')
        try:
            events = [json.loads(line) for line in data.splitlines()]
            if any(row.get('id') != index for index, row in enumerate(events, 1)):
                raise ValueError('Nonsequential or duplicate journal IDs')
            return events
        except (ValueError, UnicodeError, AttributeError) as exc:
            raise EvidenceError(f'Invalid journal; further appends refused: {exc}') from exc

    def add(self, event, **fields):
        with self.lock:
            if self.append_failure:
                raise EvidenceError(self.append_failure)
            record = json.loads(dump(dict(id=len(self.events)+1, timestamp=now(), event=event, **fields)))
            before = list(self.events)
            try:
                with self.path.open('a', encoding='utf-8') as out:
                    out.write(dump(record)+'\n')
                    out.flush()
                    os.fsync(out.fileno())
                self.events.append(record)
                return record['id']
            except BaseException as exc:
                # A signal can arrive after write/flush/fsync but before memory commit.
                # Reconcile complete records; never append after an uncertain partial tail.
                try:
                    disk = self._read_events()
                    if disk not in (before, before+[record]):
                        raise EvidenceError('Journal changed unexpectedly during append')
                    if self.path.exists():
                        with self.path.open('rb') as source:
                            os.fsync(source.fileno())
                    self.events = disk
                except BaseException as recovery_error:
                    self.append_failure = f'Journal append uncertain; appends stopped: {recovery_error}'
                if isinstance(exc, OSError):
                    raise EvidenceError(f'Journal persistence failed: {exc}') from exc
                raise

    def record(self, record_id):
        if type(record_id) is not int or not 1 <= record_id <= len(self.events):
            raise ValueError(f'Unknown record {record_id}')
        return self.events[record_id-1]

    def ingest(self, source, length=None):
        """Stream one observed interval to content-addressed storage."""
        temporary = None
        try:
            digest, size = hashlib.sha256(), 0
            with tempfile.NamedTemporaryFile(dir=self.blobs, delete=False) as out:
                temporary = Path(out.name)
                while length is None or size < length:
                    data = source.read(CHUNK if length is None else min(CHUNK, length-size))
                    if not data:
                        break
                    out.write(data)
                    digest.update(data)
                    size += len(data)
                out.flush()
                os.fsync(out.fileno())
            key = digest.hexdigest()
            destination = self.blobs / key
            if destination.exists():
                self.verify(key)
            else:
                os.replace(temporary, destination)
            return {'artifact_id': key, 'path': str(destination), 'observed_bytes': size}
        except OSError as exc:
            raise EvidenceError(f'Artifact persistence failed: {exc}') from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def blob(self, data):
        return self.ingest(io.BytesIO(data.encode() if isinstance(data, str) else data))['artifact_id']

    def verify(self, key):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('Invalid artifact identity')
        path = self.blobs / key
        try:
            digest = hashlib.sha256()
            with path.open('rb') as source:
                while data := source.read(CHUNK):
                    digest.update(data)
            if digest.hexdigest() != key:
                raise EvidenceError(f'Artifact integrity failure: {path}')
        except OSError as exc:
            raise EvidenceError(f'Artifact unavailable: {path}: {exc}') from exc
        return path

    def artifact(self, key):
        path = self.verify(key)
        return {'artifact_id': key, 'path': str(path), 'observed_bytes': path.stat().st_size}

    def snapshot(self, path, upper=None):
        path = Path(path)
        with path.open('rb') as source:
            before = os.fstat(source.fileno())
            artifact = self.ingest(source, upper)
            after = os.fstat(source.fileno())
        try:
            current = path.stat()
            changed = (before.st_ino, before.st_size, before.st_mtime_ns) != (current.st_ino, current.st_size, current.st_mtime_ns)
        except OSError:
            changed = True
        artifact.update(source_path=str(path), observed_at=now(),
                        changed_during_capture=changed or before.st_mtime_ns != after.st_mtime_ns)
        return artifact

    def request(self, request, context_id, **metadata):
        return self.add('request', blob=self.blob(dump(request)), context_id=context_id, **metadata)

    def evidence(self, artifact, **metadata):
        return self.add('evidence', blob=artifact['artifact_id'], artifact=artifact, **metadata)

    def history(self, record_id, part='payload'):
        record = self.record(record_id)
        if part == 'payload' and 'blob' in record:
            # Preserve the observation's source coordinates and capture provenance.
            # Verified identity/size/path take precedence over saved metadata.
            artifact = {**record.get('artifact', {}), **self.artifact(record['blob'])}
            payload_kind = {'request':'compiled_api_request', 'http_request':'outgoing_http_body',
                            'response':'returned_http_body', 'assistant':'native_assistant_message',
                            'evidence':'captured_observation'}.get(record['event'], record['event'])
        else:
            artifact = self.artifact(self.blob(dump(record)))
            payload_kind = 'journal_event_record'
        return {**artifact, 'record_id': record_id, 'part': part, 'payload_kind': payload_kind,
                'provenance': {k: record[k] for k in ('id','event','timestamp','kind','request_record',
                    'http_request_record','context_id','manifest_record','response_record','assistant_record','invocation_id',
                    'call_id','tool_name','job_id','path','status','process','foreign_source','account_labels') if k in record}}

    def related(self, record_id):
        """One hop through recorded references, with direct immutable body paths."""
        linked = record_links(self.record(record_id))
        linked.update(record['id'] for record in self.events if record_id in record_links(record))
        return [dict(record_id=record['id'], event=record['event'],
                     **({'payload_path':str(self.blobs/record['blob'])} if 'blob' in record else {}),
                     history_read={'record_id':record['id']})
                for record in self.events if record['id'] in linked and record['id'] != record_id]

    def search(self, query, event=None, first=1, last=None, head=None, before=None, limit=20):
        head = len(self.events) if head is None else head
        if not 0 <= head <= len(self.events) or first < 1 or limit < 1:
            raise ValueError('Invalid journal head/range/limit')
        last = min(head, last if last is not None else head, before-1 if before else head)
        needle = query.encode('utf-8')
        matches = []
        next_before = None
        for record_id in range(last, first-1, -1):
            record = self.record(record_id)
            if event and record['event'] != event:
                continue
            artifact = self.history(record_id, 'payload')
            with Path(artifact['path']).open('rb') as source:
                tail, offset, found = b'', 0, None
                while True:
                    block = source.read(CHUNK)
                    window = tail + block
                    position = window.find(needle)
                    if position >= 0:
                        found = offset-len(tail)+position
                        break
                    if not block:
                        break
                    offset += len(block)
                    tail = window[-max(0, len(needle)-1):] if len(needle)>1 else b''
            if found is not None:
                matches.append({'record_id': record_id, 'event': record['event'],
                                'artifact': artifact, 'match_byte': found})
                if len(matches) == limit:
                    next_before = record_id if record_id > first else None
                    break
        return {'head': head, 'matches': matches,
                'continuation': {k:v for k,v in dict(query=query, event=event, first=first, last=last, head=head,
                                     before=next_before, limit=limit).items() if v is not None} if next_before else None}


def byte_range(path, selection, size):
    if selection == 'full':
        return 0, size
    if not isinstance(selection, dict) or len(selection) != 1 or not set(selection) <= {'lines', 'bytes'}:
        raise ValueError('range must be full, {lines:[first,last]}, or {bytes:[start,end]}')
    kind, pair = next(iter(selection.items()))
    if not isinstance(pair, list) or len(pair) != 2 or any(type(n) is not int for n in pair):
        raise ValueError('range requires two integer positions')
    a, b = pair
    if a < (1 if kind == 'lines' else 0) or b < a:
        raise ValueError('Invalid ordered range')
    if kind == 'bytes':
        return min(a, size), min(b, size)
    line, position, start = 1, 0, 0 if a == 1 else None
    with Path(path).open('rb') as source:
        while data := source.read(CHUNK):
            for i, value in enumerate(data):
                if value == 10:
                    line += 1
                    if line == a:
                        start = position+i+1
                    if line == b+1:
                        return size if start is None else start, position+i+1
            position += len(data)
    return size if start is None else start, size


def view(artifact, selection='full', output_chars=12000, encoding='utf8', tail=False):
    if output_chars is not None and (type(output_chars) is not int or output_chars < 0):
        raise ValueError('output_chars must be null or a nonnegative integer')
    if encoding not in ('utf8', 'base64'):
        raise ValueError('encoding must be utf8 or base64')
    if encoding == 'base64' and isinstance(selection, dict) and 'lines' in selection:
        raise ValueError('Binary retrieval uses byte ranges')
    start, end = byte_range(artifact['path'], selection, artifact['observed_bytes'])
    return {**artifact, 'selected_byte_range': [start, end], 'output_chars': output_chars,
            'encoding': encoding, 'preview_tail': tail}


def present(descriptor, allowance, reason=None):
    """Render bounded text, preserving exact byte coordinates and continuation."""
    result = dict(descriptor)
    # Committed views can be rendered again after masking or budget changes.
    # These fields describe this display, not the underlying observation.
    for field in ('observation_scope', 'decoding_issue', 'byte_retrieval', 'omission_reason', 'continuation'):
        result.pop(field, None)
    start, end = descriptor['selected_byte_range']
    requested = descriptor['output_chars']
    cap = max(0, allowance if requested is None else min(allowance, requested))
    encoding = descriptor['encoding']
    parts, ranges = [], []
    with Path(descriptor['path']).open('rb') as source:
        if encoding == 'base64':
            source.seek(start)
            raw = source.read(min(end-start, cap//4*3))
            if raw:
                parts, ranges = [base64.b64encode(raw).decode()], [[start, start+len(raw)]]
        elif cap:
            def piece(a, b, chars, backwards=False):
                if not chars:
                    return '', [a,a]
                source.seek(max(a, b-4*chars-4) if backwards else a)
                offset = source.tell()
                raw = source.read(min(b-offset, 4*chars+4))
                if backwards:
                    while raw and raw[0] & 0xc0 == 0x80:
                        raw, offset = raw[1:], offset+1
                issue = None
                while True:
                    try:
                        text = raw.decode('utf-8')
                        break
                    except UnicodeDecodeError as exc:
                        text = raw[:exc.start].decode('utf-8')
                        # Invalid read-ahead beyond the visible prefix is irrelevant
                        # to this exact display. Never blank an already valid prefix.
                        if not backwards and len(text) >= chars:
                            break
                        issue = f'{exc.reason} at artifact bytes [{offset+exc.start},{offset+exc.end}); unshown bytes remain retrievable exactly'
                        if backwards and exc.end < len(raw):
                            offset += exc.end
                            raw = raw[exc.end:]
                            continue
                        break
                if backwards:
                    prefix = text[:-chars]
                    offset += len(prefix.encode('utf-8'))
                    text = text[-chars:]
                else:
                    text = text[:chars]
                if issue:
                    result['decoding_issue'] = issue
                    result['byte_retrieval'] = {'path':descriptor['path'],
                        'range':{'bytes':[start,end]},'encoding':'base64'}
                return text, [offset,offset+len(text.encode('utf-8'))]
            text, interval = piece(start, end, cap)
            if descriptor.get('preview_tail') and interval[1] < end and cap:
                head = cap*descriptor.get('preview_head_percent',25)//100
                text, interval = piece(start, end, head)
                tail_text, tail_range = piece(interval[1], end, cap-head, True)
                if text:
                    parts.append(text); ranges.append(interval)
                if tail_text:
                    parts.append(tail_text); ranges.append(tail_range)
            elif text:
                parts, ranges = [text], [interval]
    omitted, cursor = [], start
    for a, b in ranges:
        if a > cursor:
            omitted.append([cursor, a])
        cursor = b
    if cursor < end:
        omitted.append([cursor, end])
    result.update(text_parts=parts, shown_byte_ranges=ranges, omitted_byte_ranges=omitted,
                  display_complete=not omitted, eof=end == descriptor['observed_bytes'] and not omitted,
                  artifact_display_complete=start == 0 and end == descriptor['observed_bytes'] and not omitted,
                  display_status=('empty_artifact' if not descriptor['observed_bytes'] else
                                  'empty_selection' if start == end else
                                  'decoding_failed' if 'decoding_issue' in result and not parts else
                                  'metadata_only' if not cap else 'partial' if omitted else 'complete'),
                  byte_coordinates='zero-based, half-open; selected/shown/omitted ranges index the immutable artifact')
    if 'job_byte_range' in descriptor:
        origin = descriptor['job_byte_range'][0]
        result['selected_job_byte_range'] = [origin+start, origin+end]
        result['shown_job_byte_ranges'] = [[origin+a, origin+b] for a,b in ranges]
        result['omitted_job_byte_ranges'] = [[origin+a, origin+b] for a,b in omitted]
    if omitted:
        result['omission_reason'] = reason or ('decoding_issue' if 'decoding_issue' in result else 'presentation_allowance')
        result['continuation'] = {'path': descriptor['path'], 'range': {'bytes': omitted[0]},
                                  'encoding': 'base64' if 'decoding_issue' in result else encoding}
    scope = observation_scope(result)
    # Scope and useful content precede the detailed machine-readable coordinates.
    return {'observation_scope': scope, 'text_parts': result.pop('text_parts'), **result}


def observation_scope(result):
    """Describe only captured provenance and exact coverage of this rendered view."""
    size = result['observed_bytes']
    start, end = result['selected_byte_range']
    shown = sum(b-a for a,b in result['shown_byte_ranges'])
    unshown, cursor = [], 0
    for a,b in result['shown_byte_ranges']:
        if cursor < a:
            unshown.append([cursor,a])
        cursor = b
    if cursor < size:
        unshown.append([cursor,size])
    kind = result.get('observation_kind')
    detail = (f"file-change diff for {result['target_path']}" if kind == 'file_change_diff' else
              f"one page of literal historical journal search results at head {result['search_head']}" if kind == 'journal_search_page' else
              'immutable saved artifact; this read did not take a new source snapshot' if kind == 'immutable_artifact' else '')
    if 'record_id' in result:
        source = (f"Historical {result['payload_kind'].replace('_',' ')} from journal record {result['record_id']}"
                  f" (recorded {result['provenance']['timestamp']})")
        if detail:
            source += '; '+detail
    else:
        source = detail[0].upper()+detail[1:] if detail else 'Captured artifact'
    if 'source_path' in result:
        source += f"; file snapshot of {result['source_path']} captured at {result['observed_at']}"
        if result.get('changed_during_capture'):
            source += '; source changed during capture'
    if 'job_byte_range' in result:
        a,b = result['job_byte_range']
        source += f"; process-output interval for job {result['job_id']}, log bytes [{a},{b})"
    if 'process_state' in result:
        source += f"; process {result['process_state']}, capture {result['capture_status']} at {result.get('process_observed_at',result['observed_at'])}"
    elif 'capture_status' in result:
        source += f"; capture {result['capture_status']} at {result['observed_at']}"
    if start == end:
        selection = f"Selected empty interval [{start},{end}) of {size} captured bytes"
    else:
        portion = ('the full artifact' if start == 0 and end == size else
                   'only prefix' if start == 0 else 'only suffix' if end == size else 'only an interior interval')
        selection = f"Selected {portion} [{start},{end}) of {size} captured bytes"
    action = result.get('action')
    statement = f"{action + ': ' if action else ''}{source}. {selection}; displayed {shown} of {end-start} selected bytes."
    statement += f" {size-shown} captured bytes are not displayed in this observation."
    if 'omission_reason' in result:
        statement += f" Selected bytes omitted: {result['omission_reason'].replace('_',' ')}."
    next_read = result.get('continuation')
    # Coverage describes this view, not session-wide reading. Recover selected
    # omissions first; a completed suffix must not redirect to an earlier prefix.
    if next_read is None and end < size:
        next_read = {'path':result['path'], 'range':{'bytes':[end,size]}, 'encoding':result['encoding']}
    if next_read:
        statement += ' next_read retrieves omitted selected bytes, then the following suffix; coverage is per observation.'
    if result['display_status'] == 'metadata_only':
        statement = (f"{action + ': ' if action else ''}No content displayed "
                     f"({result['omission_reason'].replace('_',' ')}). {selection}.")
        if next_read:
            statement += ' next_read retrieves saved bytes.'
    if result.get('stdin_error'):
        statement += (f" Recorded stdin error: {result['stdin_error']}; "
                      f"{result['stdin_pending_bytes']} input bytes pending at this observation.")
    return {'statement':statement, 'unshown_byte_ranges':unshown, 'next_read':next_read}


def _compact_artifact(artifact):
    """Remove only the hash duplicated by a content-addressed path."""
    result = copy.deepcopy(artifact)
    if result.get('path') and result.get('artifact_id') == Path(result['path']).name:
        result.pop('artifact_id')
    return result


def compact_process(state):
    """Project sampled mechanical state without inferring delivery or consumption."""
    result = copy.deepcopy(state)
    # The original leader PID is diagnostic detail; current live membership and
    # shell exit remain distinct. New/unknown fields are retained by default.
    result.pop('initial_pid', None)
    for key in ('capture_error', 'stdin_error', 'stop_error', 'termination_reason'):
        if result.get(key) is None:
            result.pop(key, None)
    if result.get('group_identity_reused') is False:
        result.pop('group_identity_reused')
    if isinstance(result.get('undelivered_input'), dict):
        result['undelivered_input'] = _compact_artifact(result['undelivered_input'])
    return result


def compact_output(descriptor):
    """A lossless-content wire projection of an already presented descriptor.

    The full descriptor stays in the journal/compiler. Coordinates here remain
    zero-based half-open artifact bytes; the job interval supplies its origin.
    Unknown fields and explicit errors survive rather than being allowlisted away.
    """
    full = copy.deepcopy(descriptor)
    start, end = full['selected_byte_range']
    size = full['observed_bytes']
    selection = ('empty selection' if start == end else 'full artifact' if start == 0 and end == size
                 else 'only a prefix' if start == 0 else 'only a suffix' if end == size
                 else 'only an interior interval')
    status = full['display_status']
    display = ('UTF-8 decoding failed' if status == 'decoding_failed' else
               'no content displayed' if status in ('metadata_only', 'empty_artifact', 'empty_selection') else
               'some selected bytes omitted' if not full['display_complete'] else 'all selected bytes displayed')
    action = full.get('action')
    result = {'scope': f"{action + ': ' if action else ''}{selection}; {display}.",
              'text_parts': full['text_parts']}
    next_read = full['observation_scope']['next_read']
    # One immutable path and one forward action, rather than continuation,
    # byte_retrieval, scope.next_read and the same path repeated separately.
    if not next_read or next_read.get('path') != full['path']:
        result['path'] = full['path']
    for key in ('observed_bytes', 'selected_byte_range', 'shown_byte_ranges', 'encoding', 'display_status'):
        result[key] = full[key]
    source = {}
    for old, new in (('observation_kind', 'kind'), ('source_path', 'path'),
                     ('observed_at', 'observed_at'), ('target_path', 'target_path'),
                     ('record_id', 'record_id'), ('part', 'part'), ('payload_kind', 'payload_kind'),
                     ('job_id', 'job_id'), ('job_byte_range', 'job_byte_range'), ('search_head', 'search_head')):
        if old in full:
            source[new] = full[old]
    provenance = full.get('provenance', {})
    for key in ('timestamp', 'event', 'kind', 'job_id', 'path', 'status'):
        if key in provenance:
            source.setdefault('recorded_at' if key == 'timestamp' else key, provenance[key])
    if isinstance(provenance.get('process'), dict):
        source['process'] = compact_process(provenance['process'])
    extra_provenance = {k:v for k,v in provenance.items() if k not in
        {'id', 'timestamp', 'event', 'kind', 'job_id', 'path', 'status', 'process',
         'request_record', 'http_request_record', 'context_id', 'manifest_record',
         'response_record', 'assistant_record', 'invocation_id', 'call_id', 'tool_name'}}
    if extra_provenance:
        source['provenance'] = extra_provenance
    if source:
        result['source'] = source
    if next_read:
        result['next_read'] = next_read
    consumed = {'action', 'artifact_id', 'path', 'observed_bytes', 'selected_byte_range',
        'shown_byte_ranges', 'encoding', 'display_status', 'text_parts', 'observation_scope',
        'output_chars', 'preview_tail', 'preview_head_percent', 'display_complete',
        'artifact_display_complete', 'eof', 'byte_coordinates', 'omitted_byte_ranges',
        'selected_job_byte_range', 'shown_job_byte_ranges', 'omitted_job_byte_ranges',
        'continuation', 'byte_retrieval', 'observation_kind', 'source_path', 'observed_at',
        'target_path', 'record_id', 'part', 'payload_kind', 'provenance', 'job_id',
        'job_byte_range', 'search_head'}
    for key, value in full.items():
        if key not in consumed and not (key == 'changed_during_capture' and value is False):
            result[key] = value
    # Do not conceal an anomalous identity merely because normal artifacts use
    # their hash as the basename. This is a projection, not validation/repair.
    if 'artifact_id' in full and full['artifact_id'] != Path(full['path']).name:
        result['artifact_id'] = full['artifact_id']
    return result


def compact_result(result):
    """Keep outcomes/actionable facts; full receipts remain directly retrievable."""
    full = copy.deepcopy(result)
    projected = {}
    for key in ('tool_name', 'status', 'effects'):
        if key in full:
            projected[key] = full.pop(key)
    output = full.pop('output', None)
    is_view = isinstance(output, dict) and {'path', 'observed_bytes', 'selected_byte_range',
                                           'output_chars', 'encoding'} <= output.keys()
    if is_view:
        # Finishing a batch can render its receipt before the next compilation.
        # Do not expand an unbounded requested view merely to record that finish.
        if 'display_status' not in output:
            output = present(output, 16000)
        projected['output'] = compact_output(output)
        # Select the captured interval, not the receipt at details.record_id.
        # Undisplayed bytes are not decoded here; retention still validates them.
        record_id = output.get('record_id', result.get('observation_record'))
        start, end = output['selected_byte_range']
        if record_id is not None and start < end and output['encoding'] == 'utf8' and 'decoding_issue' not in output:
            projected['output']['retain'] = {'record_id':record_id,
                'part':output.get('part', 'payload'), 'view':{'bytes':[start, end]}}
    elif output is not None:
        projected['output'] = output
    process = full.pop('process', None)
    if isinstance(process, dict):
        projected['process'] = compact_process(process)
        if is_view:
            # Only remove facts exactly duplicated by this sampled state. A
            # capture snapshot can differ from the process sample; preserve it.
            for key, state_key in (('process_state', 'state'), ('capture_status', 'capture_status'),
                                   ('stdin_error', 'stdin_error'), ('stdin_pending_bytes', 'stdin_pending_bytes'),
                                   ('live_path', 'live_path'), ('process_observed_at', 'observed_at')):
                if key in projected['output'] and projected['output'][key] == process.get(state_key):
                    projected['output'].pop(key)
    elif process is not None:
        projected['process'] = process
    if 'result_record' in full:
        projected['details'] = {'record_id':full.pop('result_record'), 'part':'record'}
        for key in ('call_id', 'native_item_id', 'invocation_id'):
            full.pop(key, None)
    if is_view:
        for key, output_key in (('path', 'source_path'), ('read_kind', 'observation_kind'),
                                ('source_record', 'record_id'), ('search_head', 'search_head')):
            if key in full and full[key] == output.get(output_key):
                full.pop(key)
        if full.get('path') == output.get('target_path'):
            full.pop('path', None)
        if full.get('historical_data') is True and ('record_id' in output or output.get('observation_kind') == 'journal_search_page'):
            full.pop('historical_data')
    if full.get('diff_lossy_before') is False:
        full.pop('diff_lossy_before')
    if full.get('search_continuation') is None:
        full.pop('search_continuation', None)
    if result.get('tool_name') == 'send' and all(key in full for key in
            ('job_id', 'accepted_bytes', 'pending_bytes', 'eof_requested',
             'stdin_pending_bytes', 'stdin_eof_requested')):
        same_pending = full['pending_bytes'] == full['stdin_pending_bytes']
        same_eof = full['eof_requested'] == full['stdin_eof_requested']
        known_receipt = (f"send: accepted {full['accepted_bytes']} UTF-8 bytes into job {full['job_id']}'s input queue; "
            f"{full['pending_bytes']} bytes were pending at this observation. "
            f"EOF {'requested after queued input drains' if full['eof_requested'] else 'not requested'}. "
            'Acceptance does not establish delivery or consumption. Do not resend accepted bytes; poll for process and input state.')
        if full.get('stdin_error'):
            known_receipt += ' Input error: '+full['stdin_error']
        if same_pending and same_eof and full.get('receipt') == known_receipt:
            full.pop('receipt')
        if same_pending:
            full.pop('pending_bytes')
        if same_eof:
            full.pop('eof_requested')
    for key in ('error', 'capture_error', 'stdin_error', 'stop_error'):
        if full.get(key) is None:
            full.pop(key, None)
    if isinstance(full.get('related_records'), list):
        links = []
        for link in full['related_records']:
            link = copy.deepcopy(link)
            if 'record_id' in link:
                link.pop('payload_path', None)
                if link.get('history_read') == {'record_id':link['record_id']}:
                    link.pop('history_read')
            links.append(link)
        full['related_records'] = links
    projected.update(full)
    return projected
