"""Compact, literal activity previews over one recorded journal prefix.

This is an observation/navigation aid, not semantic summarization, delivery proof,
process monitoring, or continuation replay. Full decision inspection stays available.

Cursor usage: --after-record N returns turns with any new activity (assistant, invocation,
or tool_result) after journal record N. It is a prefix observation of what has been written
to the journal, NOT a claim about live process state or delivered content. Callers advance
the cursor by using the returned next_after_record value. Cursor values beyond the observed
journal head are rejected; equality is a valid poll with no new records. The cursor
covers the prefix, not just displayed turns: --last can omit older eligible turns.
Inspect omitted_turns before advancing if you need all activity. With no cursor,
the latest --last turns are returned as before.
"""
import json
from collections import Counter
from mads.responses import execution_view


def inspect_activity(journal, last=3, preview_chars=1200, after_record=None):
    if type(last) is not int or not 1 <= last <= 20:
        raise ValueError('last must be an integer in 1..20')
    if type(preview_chars) is not int or not 0 <= preview_chars <= 8000:
        raise ValueError('preview_chars must be an integer in 0..8000')
    if after_record is not None:
        if type(after_record) is not int or after_record < 0:
            raise ValueError('after_record must be a non-negative integer')

    def ref(event):
        if not event:
            return None
        result = {'record': event['id'], 'event': event['event']}
        if 'blob' in event:
            # Validate before advertising immutable bytes.
            journal.blob(event['blob'])
            result['payload'] = str(journal.journal / 'blobs' / event['blob'])
        else:
            result['journal_bytes'] = journal.record_ranges[event['id']]
        return result

    def preview(text, pointer=None):
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        return {'text': text[:preview_chars], 'chars': len(text),
                'omitted_chars': max(0, len(text) - preview_chars),
                **({'pointer': pointer} if pointer else {})}

    latest = {}
    assistants, invocations, outcomes = [], {}, {}
    for event in journal.events:
        latest[event['event']] = event
        if event['event'] == 'assistant':
            assistants.append(event)
        elif event['event'] == 'invocation':
            invocations.setdefault(event.get('assistant_record'), []).append(event)
        elif event['event'] == 'tool_result':
            outcomes.setdefault(event.get('invocation_id'), []).append(event)

    def outcome(event):
        result = event.get('result', {})
        row = {'source': ref(event), **{k: result[k] for k in
               ('status', 'effects', 'error', 'summary', 'context_change') if k in result}}
        if isinstance(result.get('process'), dict):
            row['recorded_process'] = {k: result['process'][k] for k in
                ('job_id', 'state', 'shell_exit_code', 'observed_at', 'capture_status')
                if k in result['process']}
        output = result.get('output')
        if isinstance(output, dict):
            # Read captured evidence only. Never dereference arbitrary recorded live paths.
            key = output.get('artifact_id')
            if not key and output.get('path'):
                from pathlib import Path
                key = Path(output['path']).name
            if key:
                try:
                    data = journal.blob(key)
                    text = data.decode('utf-8')
                    row['captured_output'] = {**preview(text),
                        'path': str(journal.journal / 'blobs' / key), 'bytes': len(data),
                        'scope': 'captured artifact preview; not necessarily the selection delivered to the model'}
                except UnicodeDecodeError:
                    row['captured_output'] = {'path': str(journal.journal / 'blobs' / key),
                                               'bytes': len(data), 'encoding': 'binary; no text preview'}
                except (ValueError, OSError) as exc:
                    row['captured_output'] = {'unavailable': str(exc)}
            elif 'text_parts' in output:
                row['recorded_output'] = preview(''.join(output['text_parts']))
        return row

    # Cursor filtering: find turns with activity after after_record.
    # A "turn with new activity" is one where the assistant record,
    # any invocation for that assistant, or any tool_result for those invocations
    # has an id > after_record.
    cursor_ids = set()
    if after_record is not None:
        # Validate cursor against observed head.
        head = journal.events[-1]['id'] if journal.events else 0
        if after_record > head:
            raise ValueError(
                f'after_record ({after_record}) is beyond the observed journal head ({head})')
        for event in journal.events:
            eid = event['id']
            if eid <= after_record:
                continue
            kind = event['event']
            if kind == 'assistant':
                cursor_ids.add(eid)
            elif kind == 'invocation':
                aid = event.get('assistant_record')
                if aid is not None:
                    cursor_ids.add(aid)
            elif kind == 'tool_result':
                iid = event.get('invocation_id')
                if iid is not None:
                    inv_event = journal.by_id.get(iid, {})
                    if inv_event.get('event') == 'invocation':
                        aid = inv_event.get('assistant_record')
                        if aid is not None:
                            cursor_ids.add(aid)

    # Filter before bounding and decoding expensive artifact previews.
    eligible = (assistants if after_record is None else
                [e for e in assistants if e['id'] in cursor_ids])
    turns = []
    for event in eligible[-last:]:
        native = journal.payload(event)
        # The native Responses reasoning item is opaque; execution_view does not decode it.
        message = execution_view(native) if event.get('protocol') == 'responses' else native
        row = {'source': ref(event), 'request_record': event.get('request_record'),
               'response_record': event.get('response_record'),
               'content': preview(message.get('content') or ''), 'calls': []}
        if isinstance(message.get('reasoning_content'), str):
            row['recorded_reasoning'] = preview(message['reasoning_content'])
        for invocation in invocations.get(event['id'], []):
            results = outcomes.get(invocation['id'], [])
            row['calls'].append({'source': ref(invocation),
                'name': invocation.get('tool_name'),
                'arguments': preview(invocation.get('raw_arguments', ''), '/raw_arguments'),
                'state': 'result_recorded' if results else 'no_result_in_observed_prefix',
                'outcomes': [outcome(result) for result in results]})
        # A live prefix can stop between the assistant and invocation records.
        row['native_call_count'] = len(message.get('tool_calls', []))
        if row['native_call_count'] != len(row['calls']):
            row['native_calls'] = [{'name': c.get('function', {}).get('name'),
                                   'arguments': preview(c.get('function', {}).get('arguments', ''))}
                                  for c in message.get('tool_calls', [])]
        turns.append(row)

    # Invocation count map over the entire observed prefix (independent of last/cursor).
    invocation_by_tool = Counter()
    for event in journal.events:
        if event['event'] == 'invocation':
            invocation_by_tool[event.get('tool_name', 'unknown')] += 1

    # next_after_record: the last complete record id of the observed journal prefix.
    next_after_record = journal.events[-1]['id'] if journal.events else 0

    return {'scope': 'literal recorded activity, not current liveness or proof of delivery/understanding',
        'journal': str(journal.journal / 'events.jsonl'),
        'journal_observed_sha256': journal.observed_sha256,
        'journal_head': journal.events[-1]['id'] if journal.events else None,
        'incomplete_tail_bytes': journal.incomplete_tail_bytes,
        'last_recorded_at': journal.events[-1].get('timestamp') if journal.events else None,
        'latest': {kind: ref(latest.get(kind)) for kind in
                   ('request', 'http_request', 'response', 'assistant', 'tool_result', 'run_end')},
        'turns': turns,
        'eligible_turns': len(eligible),
        'omitted_turns': max(0, len(eligible) - last),
        'next_after_record': next_after_record,
        'cursor_scope': 'Observed prefix head, not a pagination cursor. --last may omit older eligible turns; '
                        'repeat with the previous cursor and a larger last (max 20), or use decision inspection.',
        'invocations_by_tool': dict(invocation_by_tool),
        'invocations_by_tool_scope': 'entire observed journal prefix; counts journal invocation events, '
                                     'independent of last and after_record view',
        'navigation': 'Use inspect decision --record ID for causal detail, request --raw for exact delivery; blob paths contain full payloads. Non-blob refs name byte ranges in this observed journal prefix.'}
