"""Read-only, streaming analysis of frozen repair traces and guidance delivery.

Input URLs are private short-lived capabilities and never appear in output.
No archived code, solver action or grader is executed. Only native SQLite copies,
selected small artifacts and derived transcripts are materialized temporarily.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request

try:
    from .report_fleet import result_reference
except ImportError:
    from report_fleet import result_reference


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    pending.replace(path)


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from dictionaries(item)
    elif isinstance(value, list):
        for item in value:
            yield from dictionaries(item)


def bounded(value, limit=16000):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f'\n[derived excerpt: {len(value)} original characters]'
    if isinstance(value, dict):
        return {k: bounded(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [bounded(v, limit) for v in value]
    return value


def collect(item, root):
    """Check the entire immutable compressed payload while selecting members."""
    process = subprocess.Popen(['zstd', '-dc'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    transfer = {'bytes': 0, 'error': None}

    def feed():
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(item['url'], timeout=120) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    transfer['bytes'] += len(chunk)
                    process.stdin.write(chunk)
            transfer['sha256'] = digest.hexdigest()
        except Exception as error:
            # Presigned URLs must not be included in failure reports.
            transfer['error'] = type(error).__name__
        finally:
            process.stdin.close()

    thread = threading.Thread(target=feed, daemon=True)
    thread.start()
    kept, inventory = {}, {}
    prefix = 'scaffold/.tasks/trace/madsServe_logs/0/teamharness/'
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            for member in archive:
                parts = PurePosixPath(member.name).parts
                if (not parts or parts[0] != item['instance_id'] or '..' in parts
                        or member.name.startswith('/')):
                    raise ValueError('Unsafe or incorrect archive root')
                if not member.isfile():
                    continue
                name = '/'.join(parts[1:])
                native = name[len(prefix):] if name.startswith(prefix) else None
                database = bool(native and re.fullmatch(
                    r'run/agents/agents/[0-9a-f]{32}/session\.sqlite3(?:-journal|-wal|-shm)?', native))
                wanted = name in {'tasks.json', 'scaffold/model_patch_0.diff',
                    'scaffold/pro_eval/eval_results.json', 'scaffold/pro_eval/0/replay/model.patch'}
                wanted |= name.startswith('scaffold/pro_eval/0/') and '/replay/' not in name
                wanted |= bool(native and (native in {'candidate.json', 'candidate.patch', 'grade.json',
                    'selection.json', 'finalization.json', 'launch.json', 'stop.json', 'error.txt',
                    'finalization-error.txt', 'run/workflow.json', 'run/source/source-before.patch'}
                    or native.endswith(('/agent.json', '/runner.json')) or native.endswith('.patch')
                    or native.startswith('run/agents/guides/portable-repair/')))
                if not database and (not wanted or member.size > 2_000_000):
                    continue
                key = native if native is not None else name
                if key in inventory:
                    raise ValueError('Duplicate selected member: ' + key)
                source = archive.extractfile(member)
                if database:
                    destination = root / key
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open('xb') as output:
                        shutil.copyfileobj(source, output)
                    with destination.open('rb') as evidence:
                        inventory[key] = {'bytes': member.size, 'sha256': hashlib.file_digest(evidence, 'sha256').hexdigest()}
                else:
                    raw = source.read()
                    kept[key] = raw
                    inventory[key] = {'bytes': len(raw), 'sha256': sha(raw)}
        thread.join()
        if process.wait() != 0 or transfer['error']:
            raise ValueError('Archive stream failed: ' + str(transfer['error']))
        if transfer['bytes'] != item['bytes'] or transfer.get('sha256') != item['sha256']:
            raise ValueError('Immutable archive length/SHA256 mismatch')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
        process.wait()
        thread.join(timeout=5)
    return kept, inventory


WRITE = re.compile(r'apply_patch|\bsed\s+-i\b|\bperl\s+-[^\s]*i|\.write_(?:text|bytes)\s*\(|'
                   r'open\([^\n]{0,200},\s*[\"\x27][wax]|\b(?:cat|tee)\b[^\n]{0,180}\s>\s*[^&]|'
                   r'\b(?:cp|mv|rm|git\s+(?:apply|reset|checkout))\s')
TEST = re.compile(r'\b(?:pytest|go test|npm test|yarn test|jest|vitest|cargo test|unittest|ctest)\b')


def inspect_session(path, descriptor, documents):
    # All files here are disposable derived copies. Opening their journals for
    # recovery cannot change the original archive or the S3 object version.
    with sqlite3.connect(path) as db:
        db.execute('SELECT name FROM sqlite_master LIMIT 1').fetchall()
        db.execute('PRAGMA query_only=ON')
        events, tool_views, reads, grades = [], {}, [], []
        requests = responses = 0
        tools = Counter()
        first_request = first_edit = first_test = None
        last_state = None
        for ident, kind, raw, digest, created in db.execute(
                'SELECT id,kind,body,sha256,created FROM objects ORDER BY id'):
            if kind not in {'model_request', 'model_response', 'tool_result', 'session_state',
                            'compaction_result', 'provider_observation'}:
                continue
            if sha(raw.encode()) != digest:
                raise ValueError('Session object hash mismatch: ' + str(ident))
            body = json.loads(raw)
            if kind == 'model_request':
                if body.get('purpose') != 'agent':
                    continue
                request = body['request']
                requests += 1
                if first_request is None:
                    system = request['messages'][0]['content']
                    first_request = {'id': ident, 'at': created, 'model': request['model'],
                        'system_sha256': sha(system.encode()), 'system_characters': len(system),
                        'portable_instruction': 'Host instruction for this documentation treatment' in system,
                        'foundation_present': documents['SYSTEM_PROMPT.md'].strip() in system}
                for message in request['messages']:
                    text = message.get('content')
                    if message.get('role') == 'tool' and isinstance(text, str):
                        match = result_reference(text)
                        if match:
                            tool_views.setdefault(int(match[1]), {'request_id': ident, 'at': created, 'text': text})
            elif kind == 'model_response':
                if body.get('purpose') not in (None, 'agent'):
                    continue
                responses += 1
                response = body.get('response') or {}
                choices = response.get('choices') or []
                message = choices[0].get('message', {}) if choices else {}
                calls = []
                for call in message.get('tool_calls') or []:
                    function = call.get('function', {})
                    try:
                        arguments = json.loads(function.get('arguments', '{}'))
                    except (ValueError, TypeError):
                        arguments = function.get('arguments')
                    calls.append({'name': function.get('name'), 'arguments': bounded(arguments)})
                events.append({'id': ident, 'at': created, 'kind': 'model',
                    'text': bounded(message.get('content'), 20000),
                    'reasoning': bounded(message.get('reasoning_content') or message.get('reasoning'), 20000),
                    'calls': calls, 'finish_reason': choices[0].get('finish_reason') if choices else None})
            elif kind == 'tool_result':
                name, receipt = body['name'], body['receipt']
                tools[name] += 1
                nodes = list(dictionaries(receipt))
                contents = [node['content'] for node in nodes if isinstance(node.get('content'), str)]
                commands = list(dict.fromkeys(node['command'] for node in nodes if isinstance(node.get('command'), str)))
                paths = list(dict.fromkeys(node['path'] for node in nodes if isinstance(node.get('path'), str)))
                edit = name in ('apply_patch', 'edit_file') or any(WRITE.search(command) for command in commands)
                test = any(TEST.search(command) for command in commands)
                if edit and first_edit is None:
                    first_edit = {'id': ident, 'at': created, 'tool': name, 'commands': bounded(commands, 1000),
                                  'classification': 'observed edit tool or shell-write heuristic; inspect trace for scope'}
                if test and first_test is None:
                    first_test = {'id': ident, 'at': created, 'tool': name, 'commands': bounded(commands, 1000)}
                observed = {}
                for node in nodes:
                    candidate = node.get('path', '')
                    doc = candidate.split('/portable-repair/', 1)[-1] if isinstance(candidate, str) else ''
                    if doc in documents and isinstance(node.get('content'), str) and node['content']:
                        text = node['content']
                        observed[doc] = {'method': 'file_receipt', 'offset': node.get('offset'),
                            'characters': len(text), 'complete_receipt': node.get('complete'),
                            'file_sha256_matches': node.get('sha256') == sha(documents[doc].encode())}
                for doc, text in documents.items():
                    if any(text.strip() in content for content in contents):
                        observed.setdefault(doc, {'method': 'literal_full_document_in_output',
                                                   'characters': len(text), 'complete_receipt': True})
                    elif any(text[:256] in content for content in contents):
                        observed.setdefault(doc, {'method': 'literal_document_prefix_in_output',
                                                   'complete_receipt': False})
                for doc, evidence in observed.items():
                    reads.append({'id': ident, 'at': created, 'tool': name, 'document': doc, **evidence})
                if name == 'evaluate_candidate' and isinstance(receipt, dict):
                    facts = receipt.get('grade_facts') or {}
                    counts = next((node for node in nodes if type(node.get('tests_failed')) is int
                                   and type(node.get('tests_total')) is int), {})
                    grades.append({'id': ident, 'at': created, 'failed': counts.get('tests_failed'),
                        'total': counts.get('tests_total'), 'candidate_sha256': facts.get('candidate_sha256'),
                        'reused': receipt.get('reused'), 'trend': facts.get('trend')})
                event = {'id': ident, 'at': created, 'kind': 'tool', 'tool': name,
                    'commands': bounded(commands), 'paths': paths, 'guidance': list(observed),
                    'exit_codes': [node['exit_code'] for node in nodes if 'exit_code' in node],
                    'output_excerpts': [bounded(content, 4500) for content in contents
                                        if not any(text.strip() in content for text in documents.values())][:8]}
                if name in ('evaluate_candidate', 'finish_role', 'spawn_agent', 'compact'):
                    event['receipt'] = bounded(receipt, 12000)
                events.append(event)
            elif kind == 'session_state':
                last_state = body
            elif kind in ('compaction_result', 'provider_observation'):
                events.append({'id': ident, 'at': created, 'kind': kind, 'body': bounded(body, 10000)})
        for read in reads:
            view = tool_views.get(read['id'])
            text = documents[read['document']]
            read['following_request_id'] = view['request_id'] if view else None
            read['delivered_at'] = view['at'] if view else None
            read['full_body_delivered'] = bool(view and text.strip() in view['text'])
            read['prefix_delivered'] = bool(view and text[:256] in view['text'])
        failed_grade = next((grade for grade in grades if type(grade['failed']) is int and grade['failed'] > 0), None)
        skill_reads = [read for read in reads if read['document'].endswith('/SKILL.md')]
        delivered_skills = [read for read in skill_reads if read['full_body_delivered']]
        first_skill_delivery = min((read['delivered_at'] for read in delivered_skills), default=None)
        after_failure = [read for read in reads if failed_grade and read['at'] > failed_grade['at']]
        return {'agent_id': descriptor.get('id', path.parent.name), 'role': descriptor.get('role'),
            'parent': descriptor.get('parent'), 'requests': requests, 'responses': responses,
            'first_request': first_request, 'status': last_state.get('status') if last_state else None,
            'report': bounded(last_state.get('report'), 20000) if last_state else None,
            'tools': dict(tools), 'reads': reads, 'grades': grades, 'first_edit': first_edit,
            'first_test': first_test, 'skills_fully_delivered': sorted({read['document'] for read in delivered_skills}),
            'skill_before_first_edit': (first_skill_delivery < first_edit['at'])
                if first_skill_delivery is not None and first_edit else None,
            'document_accesses_after_first_failed_grade': after_failure, 'events': events}


def analyze(item, output, documents):
    with tempfile.TemporaryDirectory(prefix='trace-audit-', dir=output) as directory:
        root = Path(directory)
        files, inventory = collect(item, root)
        sessions = []
        for path in sorted(root.glob('run/agents/agents/*/session.sqlite3')):
            key = str(path.parent.relative_to(root)) + '/agent.json'
            descriptor = json.loads(files[key]) if key in files else {}
            sessions.append(inspect_session(path, descriptor, documents))
        def read(name, default=None):
            return json.loads(files[name]) if name in files else default
        candidate = read('candidate.json', {})
        patch_hashes = {name: sha(files[name]) for name in ('candidate.patch', 'scaffold/model_patch_0.diff',
                        'scaffold/pro_eval/0/replay/model.patch') if name in files}
        native_docs = {name.split('run/agents/guides/portable-repair/', 1)[1]: sha(raw)
                       for name, raw in files.items() if name.startswith('run/agents/guides/portable-repair/')}
        expected_docs = {name: sha(text.encode()) for name, text in documents.items()}
        record = {'arm': item['arm'], 'source_key': item['source_key'], 'instance_id': item['instance_id'],
            'archive_sha256': item['sha256'], 'archive_bytes': item['bytes'], 'archive_verified': True,
            'outcome': read('scaffold/pro_eval/eval_results.json'), 'grade': read('grade.json'),
            'candidate': candidate, 'selection': read('selection.json'), 'workflow': read('run/workflow.json'),
            'stop': read('stop.json'), 'finalization': read('finalization.json'),
            'runtime_errors': {name: files[name].decode(errors='replace')
                               for name in ('error.txt', 'finalization-error.txt') if name in files},
            'patch_hashes': patch_hashes, 'same_captured_submitted_graded_bytes':
                len(patch_hashes) == 3 and set(patch_hashes.values()) == {candidate.get('sha256')},
            'library_matches_publication': native_docs == expected_docs if item['arm'] == 'documentation' else not native_docs,
            'library_files': len(native_docs), 'sessions': sessions,
            'verifier': {name: raw.decode(errors='replace') for name, raw in files.items()
                         if name.startswith('scaffold/pro_eval/0/') and '/replay/' not in name and len(raw) < 150000},
            'candidate_patch': files.get('candidate.patch', b'').decode(errors='replace'),
            'artifact_hashes': {name: value for name, value in inventory.items() if 'session.sqlite3' not in name}}
        save(output / (item['arm'] + '-' + item['source_key'] + '.json'), record)
        return {key: record[key] for key in ('arm', 'source_key', 'instance_id', 'archive_verified',
                    'same_captured_submitted_graded_bytes', 'library_matches_publication', 'outcome', 'grade')} | {
            'sessions': len(sessions), 'requests': sum(session['requests'] for session in sessions)}


def summarize_usage(output):
    """Derive exposure counts from completed records, without assuming compliance."""
    groups = {}
    for path in output.glob('*.json'):
        record = json.loads(path.read_text())
        if not isinstance(record, dict) or not {'arm', 'source_key', 'sessions'} <= record.keys():
            continue
        groups.setdefault(record['arm'], []).append(record)
    result = {}
    for arm, records in groups.items():
        chapters = Counter()
        counts = Counter()
        for record in records:
            sessions = record['sessions']
            reads = [read for session in sessions for read in session['reads']]
            chapter_names = {read['document'] for read in reads
                             if read['document'].endswith('/SKILL.md') and read['full_body_delivered']}
            chapters.update(chapter_names)
            counts['tasks'] += 1
            counts['sessions'] += len(sessions)
            counts['passed'] += record['outcome'].get('0') is True
            counts['agent_requests'] += sum(session['requests'] for session in sessions)
            counts['catalog_delivered'] += any(read['document'] == 'SKILLS.md' and read['full_body_delivered'] for read in reads)
            counts['chapter_delivered'] += bool(chapter_names)
            counts['chapter_before_first_detected_edit'] += any(session['skill_before_first_edit'] is True for session in sessions)
            counts['reference_access'] += any('/references/' in read['document'] for read in reads)
            counts['failed_interactive_grade'] += any(type(grade['failed']) is int and grade['failed'] > 0
                                                     for session in sessions for grade in session['grades'])
            counts['post_failure_library_access'] += any(session['document_accesses_after_first_failed_grade'] for session in sessions)
        result[arm] = {**dict(counts), 'chapter_tasks': dict(chapters)}
    return {'arms': result, 'scope': 'Observed exposure; no inference of comprehension, compliance beyond detected edits, or causal benefit.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--documents', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    documents = {str(path.relative_to(args.documents)): path.read_text()
                 for path in args.documents.rglob('*.md')}
    items = json.loads(args.input.read_text())
    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(analyze, item, args.output, documents): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                row = future.result()
                rows.append(row)
                print(json.dumps(row), flush=True)
            except Exception as error:
                # Exception messages may contain source URLs. Keep type only.
                errors.append({'arm': item['arm'], 'source_key': item['source_key'], 'error_type': type(error).__name__})
                print(json.dumps(errors[-1]), flush=True)
            save(args.output / 'progress.json', {'completed': rows, 'errors': errors, 'expected': len(items)})
    save(args.output / 'summary.json', {'completed': rows, 'errors': errors, 'expected': len(items),
                                      'finished_at': time.time()})
    save(args.output / 'usage.json', summarize_usage(args.output))
    raise SystemExit(bool(errors))


if __name__ == '__main__':
    main()
