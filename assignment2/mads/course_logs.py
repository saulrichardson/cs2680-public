"""Assignment JSONL projection. Native journals remain the authoritative evidence.

No receipt is re-rendered as a tool result: only strings in recorded HTTP bodies
are projected. Repeated/changed presentations are separate events, not executions.
See docs/course-compatibility.md for the intentionally separate provenance schema.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from .store import EvidenceError, atomic_json, dump

HISTORICAL = '<tool_response>REJECTED TOOL CALL — HISTORICAL DATA, NOT AN ACTIVE CALL\n'


def _json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _blob(journal, row):
    sha = row['blob']
    data = (journal/'blobs'/sha).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise EvidenceError('Corrupt course-log source blob: '+sha)
    return data


def _journals(run):
    """Follow explicit Provider.fork links, not arbitrary journals in repo copies."""
    found = {}
    pending = [run/'journal']
    while pending:
        path = pending.pop().resolve()
        if path in found:
            continue
        if not path.is_relative_to(run):
            raise EvidenceError('Child journal outside the exported run: '+str(path))
        raw = (path/'events.jsonl').read_bytes()
        if raw and not raw.endswith(b'\n'):
            raise EvidenceError('Interrupted native journal; preserve and repair before projection')
        rows = [json.loads(line) for line in raw.splitlines()]
        if any(r['id'] != i for i, r in enumerate(rows, 1)):
            raise EvidenceError('Nonsequential source journal: '+str(path))
        found[path] = rows
        for row in rows:
            if row['event'] == 'provider_fork':
                pending.append(path/row['child_journal'])
    return found


def _presentations(body):
    key = 'messages' if 'messages' in body else 'input'
    for i, item in enumerate(body.get(key, [])):
        if not isinstance(item, dict):
            continue
        pointer = f'/{key}/{i}'
        if item.get('role') == 'tool':
            yield item['content'], pointer+'/content', item.get('tool_call_id'), False
        elif item.get('type') == 'function_call_output':
            yield item['output'], pointer+'/output', item.get('call_id'), False
        elif item.get('role') == 'user' and isinstance(item.get('content'), str) and item['content'].startswith(HISTORICAL):
            # The entire notice (not a made-up native tool message) was sent.
            yield item['content'], pointer+'/content', None, True


def project(run):
    """Return exact-field course rows plus an independently named manifest.

    Global iterations count logical requests across parent and child journals.
    Transport retries share an iteration; each attempted call gets its own usage
    row. Missing usage stays null, never an invented zero. Native response usage
    (including provider-specific detail) and HTTP status remain in the manifest.
    """
    run = Path(run).resolve()
    journals = _journals(run)
    root = run/'journal'
    timeline = sorted(((p, r) for p, rows in journals.items() for r in rows),
                      key=lambda pair: (pair[1]['timestamp'], str(pair[0]), pair[1]['id']))
    records = {(p, r['id']): r for p, r in timeline}
    iterations = {(p, r['id']): i for i, (p, r) in enumerate(
        ((p, r) for p, r in timeline if r['event'] == 'request'), 1)}
    output, links, presented = [], [], set()
    calls = {(p, r['id']): r for p, r in timeline if r['event'] == 'invocation'}
    results = {(p, r['invocation_id']): r for p, r in timeline if r['event'] == 'tool_result'}
    responses = {(p, r['attempt_record']): r for p, r in timeline
                 if r['event'] == 'response' and 'attempt_record' in r}
    errors = {(p, r['attempt_record']): r for p, r in timeline
              if r['event'] == 'api_error' and 'attempt_record' in r}

    def ref(p, r):
        return {'journal':str(p.relative_to(run)/'events.jsonl'), 'record_id':r['id']}

    def emit(p, r, event, fields, **provenance):
        output.append(dict(timestamp=r['timestamp'], event=event, **fields))
        links.append(dict(line=len(output), source=ref(p, r), **provenance))

    def origin(p, invocation):
        assistant = records[p, invocation['assistant_record']]
        return iterations[p, assistant['request_record']]

    for p, row in timeline:
        event = row['event']
        if event == 'run_start' and p == root:
            emit(p, row, event, dict(model_id=row['route']['model'], workdir=row['workdir']))
        elif event == 'api_attempt':
            response = responses.get((p, row['id']))
            body = _json(_blob(p, response)) if response else None
            usage = body.get('usage') if isinstance(body, dict) else None
            usage = usage if isinstance(usage, dict) else {}
            is_responses = 'input_tokens' in usage
            emit(p, row, 'api_request', dict(iteration=iterations[p, row['request_record']],
                 prompt_tokens=usage.get('input_tokens' if is_responses else 'prompt_tokens'),
                 completion_tokens=usage.get('output_tokens' if is_responses else 'completion_tokens'),
                 total_tokens=usage.get('total_tokens')),
                 response=ref(p, response) if response else None, usage=usage,
                 status=response.get('status') if response else None,
                 usage_known=all(k in usage for k in (
                     ('input_tokens','output_tokens','total_tokens') if is_responses else
                     ('prompt_tokens','completion_tokens','total_tokens'))),
                 timing='attempt timestamp; usage joined from actual response, not estimated')
        elif event == 'api_retry':
            error = errors.get((p, row['attempt_record']))
            emit(p, row, event, dict(iteration=iterations[p, row['request_record']],
                 error=error['message'] if error else None, backoff_s=row['delay_seconds']))
        elif event == 'invocation':
            arguments = _json(row['raw_arguments'])
            emit(p, row, 'tool_call', dict(iteration=origin(p, row), tool_name=row['tool_name'],
                 arguments=arguments if arguments is not None else row['raw_arguments']),
                 raw_arguments=row['raw_arguments'], call_id=row['call_id'])
        elif event == 'http_request':
            body = json.loads(_blob(p, row))
            for content, pointer, call_id, historical in _presentations(body):
                value = _json(content[len(HISTORICAL):-len('</tool_response>')]) if historical else _json(content)
                receipt = value.get('result') if historical and isinstance(value, dict) else value
                # Compact results link their native receipt even when bodies are masked.
                details = receipt.get('details', {}) if isinstance(receipt, dict) else {}
                native = records.get((p, details.get('record_id')))
                invocation_id = native.get('invocation_id') if native and native['event'] == 'tool_result' else None
                if invocation_id is None and isinstance(receipt, dict):
                    invocation_id = receipt.get('invocation_id')
                if historical:
                    call = value.get('tool_call', {}) if isinstance(value, dict) else {}
                    call_id = call.get('call_id', call.get('id'))
                invocation = calls.get((p, invocation_id))
                if invocation is None:
                    # Unknown associations must fail visibly, not guess a result.
                    raise EvidenceError(f'No native invocation for {p}:{row["id"]}{pointer} ({call_id})')
                if invocation['id'] >= row['id'] or invocation['call_id'] != call_id:
                    raise EvidenceError('Tool presentation has an inconsistent native reference')
                native = results.get((p, invocation['id']))
                status = native['result'].get('status') if native else None
                emit(p, row, 'tool_result', dict(iteration=origin(p, invocation),
                     tool_name=invocation['tool_name'], result=content,
                     is_error=status in ('failed', 'rejected')),
                     pointer=pointer, invocation=ref(p, invocation),
                     receipt=ref(p, native) if native else None,
                     presentation='historical_user_notice' if historical else 'native_tool',
                     request_iteration=iterations[p, row['request_record']],
                     body_sha256=row['blob'],
                     delivery='recorded HTTP dispatch; server consumption is not attested')
                presented.add((p, invocation['id']))
        elif event == 'run_end' and p == root:
            state = row['harness_outcome']['status']
            reason = ('done' if state == 'completed' else 'self-imposed limit' if
                      state == 'operator_turn_limit' else 'error')
            emit(p, row, event, dict(reason=reason, num_iterations=len(iterations)))
    manifest = dict(format='mads-course-projection-v1',
        semantics='Exact-field lossy index, NOT a replacement for native evidence. Results are each actual wire presentation; repetitions are not tool executions. Unsent terminal receipts stay native-only. Iterations globally identify logical calls including children; retries share one iteration. Null usage is unavailable, never zero. Responses input/output usage maps to prompt/completion.',
        journals=[dict(path=str(p.relative_to(run)/'events.jsonl'),
                       sha256=hashlib.sha256((p/'events.jsonl').read_bytes()).hexdigest(),
                       records=len(rows)) for p, rows in journals.items()],
        lines=links,
        original_native_root=next((r.get('run_root') for r in journals[root] if r['event']=='run_start'), None),
        unpresented_invocations=[ref(p, r) for (p, i), r in calls.items() if (p, i) not in presented],
        complete=any(r['event']=='run_end' for r in journals[root]))
    return output, manifest


def export_course_log(run, destination):
    run, destination = Path(run).resolve(), Path(destination).resolve()
    rows, manifest = project(run)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = ''.join(dump(row)+'\n' for row in rows).encode('utf-8')
    temporary = destination.with_suffix('.jsonl.tmp')
    temporary.write_bytes(data)
    os.replace(temporary, destination)
    manifest.update(native_root=os.path.relpath(run, destination.parent),
                    course_file=destination.name, course_sha256=hashlib.sha256(data).hexdigest())
    atomic_json(destination.with_suffix('.manifest.json'), manifest)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    export_course_log(args.run, args.destination)


if __name__ == '__main__':
    main()
