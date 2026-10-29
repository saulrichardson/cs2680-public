"""Independently audit saved client evidence. Hosted model input is not observable here."""
import argparse
import base64
import copy
import hashlib
import io
import json
import math
from collections import Counter
from pathlib import Path


def encoded(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8','backslashreplace')


def native_replay(message):
    fields = ('role','content','reasoning','reasoning_content','reasoning_details')
    result = {key:message[key] for key in fields if message.get(key) is not None}
    if 'tool_calls' in message:
        result['tool_calls'] = [{key:call[key] for key in ('id','type','function')}
                                for call in message['tool_calls']]
    return result


def argument_object(raw):
    def reject_constant(value):
        raise ValueError(value)
    try:
        return isinstance(json.loads(raw,parse_constant=reject_constant),dict)
    except (TypeError,ValueError):
        return False


def malformed_projection(message):
    result = native_replay(message)
    calls, removed = [],False
    for call in result.get('tool_calls',[]):
        if argument_object(call['function']['arguments']):
            calls.append(call)
        else:
            removed = True
    if removed:
        if calls:
            result['tool_calls'] = calls
        else:
            result.pop('tool_calls',None)
            result.setdefault('content','')
    return result if removed else None


def wrapped(content, label):
    prefix = '<tool_response>'+label+'\n'
    if isinstance(content,str) and content.startswith(prefix) and content.endswith('</tool_response>'):
        return json.loads(content[len(prefix):-len('</tool_response>')])
    return None


def rejected_response(content):
    prefix = '<tool_response>NATIVE RESPONSE REJECTED — HISTORICAL DATA, NOT ACTIVE ITEMS\n'
    suffix = '\nNo calls executed. Send a complete valid response.</tool_response>'
    if isinstance(content,str) and content.startswith(prefix) and content.endswith(suffix):
        return json.loads(content[len(prefix):-len(suffix)])
    return None


def audit(journal, strict=False, review=None):
    journal = Path(journal).resolve()
    events = [json.loads(line) for line in (journal/'events.jsonl').read_text().splitlines()]
    by_id = {record['id']:record for record in events}
    counts, errors, limitations = Counter(), [], set()
    verified_blobs, checked_views = set(), set()
    review_parts, response_items, response_envelopes = [], [], []

    def check(condition, where, message):
        if not condition:
            errors.append({'where':where,'error':message})

    def blob(key):
        data = (journal/'blobs'/key).read_bytes()
        if key not in verified_blobs:
            check(hashlib.sha256(data).hexdigest() == key,key,'Immutable artifact hash mismatch')
            verified_blobs.add(key)
        return data

    def payload(record):
        return json.loads(blob(record['blob']))

    def output_path(output):
        return output.get('path') or output.get('next_read',{}).get('path')

    def artifact_identity(output):
        return output.get('artifact_id') or Path(output_path(output) or '').name

    def artifact_bytes(output):
        if output.get('artifact_id'):
            return blob(output['artifact_id'])
        path = Path(output_path(output))
        # Compact views name the immutable artifact once, through its path.
        if path.parent.name == 'blobs' and len(path.name) == 64 and all(
                char in '0123456789abcdef' for char in path.name):
            return blob(path.name)
        # Saved container paths refer to this run, never to the auditor's live /run.
        for prefix in ('/output/run/','/run/'):
            if str(path).startswith(prefix):
                path = journal.parent/str(path)[len(prefix):]
                break
        return path.read_bytes()

    def audit_view(output, where):
        identity = hashlib.sha256(encoded(output)).digest()
        if identity in checked_views:
            return
        checked_views.add(identity)
        raw = artifact_bytes(output)
        start,end = output['selected_byte_range']
        ranges, parts = output['shown_byte_ranges'],output['text_parts']
        check(output['encoding'] in ('utf8','base64'),where,'Unknown displayed-byte encoding')
        check(0 <= start <= end <= len(raw),where,'Selected range outside artifact')
        check(output['observed_bytes'] == len(raw),where,'Observed extent differs from artifact length')
        check(len(parts) == len(ranges),where,'Text parts and shown intervals are not paired')
        omitted, cursor = [], start
        for part,(a,b) in zip(parts,ranges):
            check(cursor <= a <= b <= end,where,'Shown intervals overlap or exceed selection')
            if a > cursor:
                omitted.append([cursor,a])
            actual = base64.b64decode(part,validate=True) if output['encoding'] == 'base64' else part.encode()
            check(actual == raw[a:b],where,f'Displayed bytes differ from immutable interval [{a},{b})')
            cursor = b
        if cursor < end:
            omitted.append([cursor,end])
        compact = 'scope' in output
        if compact:
            status = output['display_status']
            valid_statuses = ({'empty_artifact'} if not raw else {'empty_selection'} if start == end else
                ({'partial'} if ranges else {'decoding_failed'}) if output.get('decoding_issue') else
                {'metadata_only','partial'} if omitted and not ranges else {'partial'} if omitted else {'complete'})
            check(status in valid_statuses,where,'Compact display status disagrees with delivered intervals')
            selection = ('empty selection' if start == end else 'full artifact' if start == 0 and end == len(raw)
                         else 'only a prefix' if start == 0 else 'only a suffix' if end == len(raw)
                         else 'only an interior interval')
            display = ('UTF-8 decoding failed' if status == 'decoding_failed' else
                       'no content displayed' if status in ('metadata_only','empty_artifact','empty_selection') else
                       'some selected bytes omitted' if omitted else 'all selected bytes displayed')
            expected_scope = f'{selection}; {display}.'
            check(isinstance(output['scope'],str) and (output['scope'] == expected_scope or
                  output['scope'].endswith(': '+expected_scope)),where,'Compact scope misstates selection or display coverage')
        if 'omitted_byte_ranges' in output or not compact:
            check(output['omitted_byte_ranges'] == omitted,where,'Omitted intervals are not the exact complement')
        if 'display_complete' in output or not compact:
            check(output['display_complete'] == (not omitted),where,'Display completeness disagrees with omissions')
        if 'artifact_display_complete' in output:
            check(output['artifact_display_complete'] == (start == 0 and end == len(raw) and not omitted),
                  where,'Artifact display completeness disagrees with selection')
        if compact:
            # Recover holes in this selection first, then move forward. A completed
            # suffix must not send retrieval back to an already-consumed prefix.
            next_range = omitted[0] if omitted else [end,len(raw)] if end < len(raw) else None
            continuation = output.get('next_read')
            if next_range is not None:
                check(isinstance(continuation,dict),where,'Compact view omits its next unshown interval retrieval')
                if isinstance(continuation,dict):
                    check(continuation.get('range') == {'bytes':next_range},where,
                          'Compact next_read does not recover selected omissions or advance forward')
                    check(continuation.get('path') == output_path(output),where,
                          'Compact next_read changes immutable artifact path')
                    check(continuation.get('encoding') == ('base64' if output.get('decoding_issue') else output['encoding']),
                          where,'Compact next_read encoding differs from usable artifact retrieval')
            else:
                check(continuation is None,where,'Complete compact artifact has a spurious next_read')
        elif omitted:
            continuation = output.get('continuation',{})
            check(continuation.get('range') == {'bytes':omitted[0]},where,'Continuation does not begin at first omitted interval')
            check(continuation.get('path') == output['path'],where,'Continuation changes immutable artifact path')
            if output.get('decoding_issue'):
                if continuation.get('encoding') != 'base64':
                    if strict:
                        check(False,where,'Decoding failure has an unusable UTF-8 continuation')
                    else:
                        limitations.add('Baseline decoding failure continuation may repeat UTF-8; separate byte_retrieval remains available')
        process_range = output.get('job_byte_range',output.get('source',{}).get('job_byte_range'))
        if process_range is not None:
            origin,stop = process_range
            check(stop-origin == len(raw),where,'Process interval extent differs from immutable bytes')
            for local,job in (('selected_byte_range','selected_job_byte_range'),
                              ('shown_byte_ranges','shown_job_byte_ranges'),('omitted_byte_ranges','omitted_job_byte_ranges')):
                if job in output:
                    expected = [origin+n for n in output[local]] if local == 'selected_byte_range' else [
                        [origin+a,origin+b] for a,b in output[local]]
                    check(output[job] == expected,where,'Local and process coordinates disagree')
        counts['unique_delivered_views'] += 1

    def expected_process(state):
        expected = {key:value for key,value in state.items() if key != 'initial_pid' and not (
            key in ('capture_error','stdin_error','stop_error','termination_reason') and value is None)
            and not (key == 'group_identity_reused' and value is False)}
        artifact = expected.get('undelivered_input')
        if isinstance(artifact,dict) and artifact.get('artifact_id') == Path(artifact.get('path','')).name:
            expected['undelivered_input'] = {key:value for key,value in artifact.items() if key != 'artifact_id'}
        return expected

    def expected_source(descriptor):
        source = {new:descriptor[old] for old,new in (
            ('observation_kind','kind'),('source_path','path'),('observed_at','observed_at'),
            ('target_path','target_path'),('record_id','record_id'),('part','part'),
            ('payload_kind','payload_kind'),('job_id','job_id'),('job_byte_range','job_byte_range'),
            ('search_head','search_head')) if old in descriptor}
        provenance = descriptor.get('provenance',{})
        for key in ('timestamp','event','kind','job_id','path','status'):
            if key in provenance:
                source.setdefault('recorded_at' if key == 'timestamp' else key,provenance[key])
        if isinstance(provenance.get('process'),dict):
            source['process'] = expected_process(provenance['process'])
        extra = {key:value for key,value in provenance.items() if key not in {
            'id','timestamp','event','kind','job_id','path','status','process','request_record',
            'http_request_record','context_id','manifest_record','response_record','assistant_record',
            'invocation_id','call_id','tool_name'}}
        if extra:
            source['provenance'] = extra
        return source

    def result_source(result, call_id, where, before=None):
        """Resolve a compact receipt independently of its omitted transport aliases."""
        compact = 'details' in result
        if compact:
            details = result['details']
            check(isinstance(details,dict) and set(details) == {'record_id','part'} and
                  details.get('part') == 'record' and type(details.get('record_id')) is int,
                  where,'Compact details is not an exact authoritative-result lookup')
            record = by_id.get(details.get('record_id'),{}) if isinstance(details,dict) else {}
            check(record.get('event') == 'tool_result',where,'Compact details does not resolve to an authoritative tool_result')
            check(before is None or record.get('id',before) < before,where,'Compact details refers to a future result')
            original = record.get('result')
        else:
            check(result.get('call_id') == call_id,where,'Native output envelope and result call IDs differ')
            original = by_id.get(result.get('result_record'),{}).get('result')
        if original is None:
            check(not strict or not assistants,where,'Tool output lacks an authoritative result lookup')
            return None
        check(original.get('call_id') == call_id,where,'Native output envelope and authoritative result call IDs differ')
        for key in ('effects','tool_name','error','reason','status'):
            if key in ('status','error','reason') and original.get('context_change') == 'pending':
                continue  # Context controls acquire their final status during batch compilation.
            if key in original or key in result:
                check(result.get(key) == original.get(key),where,f'Replayed outcome changed {key}')
        if not compact:
            for key in ('invocation_id','native_item_id'):
                check(result.get(key) == original.get(key),where,f'Replayed outcome changed {key}')
        if original.get('output'):
            shown = result.get('output',{})
            identity = artifact_identity(shown)
            check(identity == original['output']['artifact_id'],where,'Replayed output changed immutable identity')
            descriptor = original['output']
            check(shown.get('selected_byte_range') == descriptor['selected_byte_range'],where,
                  'Replayed output changed selected observation range')
            if compact:
                check(shown.get('source',{}) == expected_source(descriptor),where,'Compact output changed source provenance')
                if 'retain' in shown:
                    expected = {'record_id':descriptor.get('record_id',original.get('observation_record')),
                                'part':descriptor.get('part','payload'),
                                'view':{'bytes':descriptor['selected_byte_range']}}
                    check(shown['retain'] == expected,where,'Retain selector changed captured evidence identity or interval')
                    start,end = descriptor['selected_byte_range']
                    check(expected['record_id'] in by_id and start < end and shown['encoding'] == 'utf8'
                          and 'decoding_issue' not in shown,where,'Retain selector is not usable nonempty UTF-8 evidence')
                action = descriptor.get('action')
                if action:
                    check(shown.get('scope','').startswith(action+': '),where,'Compact scope changed the observation action')
                for key in ('changed_during_capture','stdin_error','stdin_pending_bytes','capture_error',
                            'capture_status','process_state','process_observed_at','live_path'):
                    if key not in descriptor or key == 'changed_during_capture' and descriptor[key] is False:
                        continue
                    process_key = {'process_state':'state','process_observed_at':'observed_at'}.get(key,key)
                    expected = descriptor[key]
                    actual = shown.get(key,result.get('process',{}).get(process_key))
                    check(actual == expected,where,f'Compact output lost or changed {key}')
        if compact and isinstance(original.get('process'),dict):
            check(result.get('process') == expected_process(original['process']),where,
                  'Compact process differs from authoritative sampled state')
        if compact:
            # Only these documented aliases/representations may disappear. Any
            # other outcome field, including future diagnostics, stays exact.
            aliases = {'output','process','call_id','native_item_id','invocation_id','result_record',
                       'harness_runtime','applied_context','related_records'}
            if original.get('context_change') == 'pending':
                aliases.update(('context_change','status','error','reason','ordinary_effects'))
            descriptor = original.get('output') or {}
            for key,target in (('path','source_path'),('read_kind','observation_kind'),
                               ('source_record','record_id'),('search_head','search_head')):
                if key in original and original[key] == descriptor.get(target):
                    aliases.add(key)
            if original.get('path') == descriptor.get('target_path'):
                aliases.add('path')
            if original.get('historical_data') is True and ('record_id' in descriptor or
                    descriptor.get('observation_kind') == 'journal_search_page'):
                aliases.add('historical_data')
            if original.get('diff_lossy_before') is False:
                aliases.add('diff_lossy_before')
            for key in ('search_continuation','error','capture_error','stdin_error','stop_error'):
                if original.get(key) is None:
                    aliases.add(key)
            if original.get('tool_name') == 'send' and all(key in original for key in
                    ('job_id','accepted_bytes','pending_bytes','eof_requested',
                     'stdin_pending_bytes','stdin_eof_requested')):
                same_pending = original['pending_bytes'] == original['stdin_pending_bytes']
                same_eof = original['eof_requested'] == original['stdin_eof_requested']
                if same_pending:
                    aliases.add('pending_bytes')
                if same_eof:
                    aliases.add('eof_requested')
                receipt = (f"send: accepted {original['accepted_bytes']} UTF-8 bytes into job {original['job_id']}'s input queue; "
                    f"{original['pending_bytes']} bytes were pending at this observation. "
                    f"EOF {'requested after queued input drains' if original['eof_requested'] else 'not requested'}. "
                    'Acceptance does not establish delivery or consumption. Do not resend accepted bytes; poll for process and input state.')
                if original.get('stdin_error'):
                    receipt += ' Input error: '+original['stdin_error']
                if same_pending and same_eof and original.get('receipt') == receipt:
                    aliases.add('receipt')
            for key,value in original.items():
                if key not in aliases:
                    check(key in result and result[key] == value,where,f'Compact outcome lost or changed {key}')
        counts['authoritative_result_lookups'] += 1
        return original

    def history_bytes(record_id, part):
        source = by_id[record_id]
        return blob(source['blob']) if part == 'payload' and 'blob' in source else encoded(source)

    def selected_interval(raw, selection):
        if selection in ('full',None):
            return [0,len(raw)]
        if 'bytes' in selection:
            return [min(n,len(raw)) for n in selection['bytes']]
        lines = io.BytesIO(raw).readlines()
        first,last = selection['lines']
        return [len(b''.join(lines[:first-1])),len(b''.join(lines[:last]))]

    def inspect_views(value, where):
        if isinstance(value,dict):
            if {'selected_byte_range','shown_byte_ranges','text_parts'} <= value.keys():
                audit_view(value,where)
            else:
                for child in value.values():
                    inspect_views(child,where)
        elif isinstance(value,list):
            for child in value:
                inspect_views(child,where)

    starts = [record for record in events if record['event'] == 'run_start']
    task = starts[0].get('task') if starts else None
    if task is None:
        limitations.add('No run_start task is present; original-task preservation is not covered')
    assistants = []
    for record in events:
        if 'blob' in record:
            blob(record['blob'])
        if record['event'] == 'assistant':
            native = payload(record)
            assistants.append((record['id'],native))
            source = by_id.get(record.get('response_record'))
            if source and source.get('blob'):
                response = payload(source)
                if record.get('protocol') == 'responses':
                    expected = {'protocol':'responses', **{key:response.get(key) for key in
                                ('id','status','output','incomplete_details','error')}}
                    check(native == expected,record['id'],'Saved Responses turn differs from returned native envelope')
                    counts['raw_response_output_matches'] += 1
                    response_envelopes.append({'response_record':source['id'],
                        'assistant_record':record['id'], **{key:response.get(key) for key in
                        ('id','model','status','incomplete_details','error','usage','reasoning')}})
                else:
                    check(response['choices'][0]['message'] == native,record['id'],'Saved assistant differs from returned response message')
                counts['raw_response_assistant_matches'] += 1
            else:
                limitations.add('Some assistant records have no linked raw response for comparison')
            for field in ('reasoning','reasoning_content','reasoning_details'):
                if native.get(field) is not None:
                    counts['returned_'+field] += 1
            if native.get('protocol') == 'responses':
                limitations.add('Responses encrypted content is opaque; only exact saved/replayed bytes and item metadata are inspected')
                output = native.get('output')
                for index,item in enumerate(output if isinstance(output,list) else []):
                    identity = {'assistant_record':record['id'],'index':index,
                                'item_sha256':hashlib.sha256(encoded(item)).hexdigest()}
                    if not isinstance(item,dict):
                        response_items.append(identity)
                        counts['returned_malformed_responses_items'] += 1
                        continue
                    identity.update({key:item[key] for key in ('type','id','call_id','phase','status') if key in item})
                    if isinstance(item.get('encrypted_content'),str):
                        encrypted = item['encrypted_content'].encode()
                        identity.update(encrypted_content_bytes=len(encrypted),
                                        encrypted_content_sha256=hashlib.sha256(encrypted).hexdigest())
                        counts['returned_encrypted_content_bytes'] += len(encrypted)
                    response_items.append(identity)
                    counts['returned_responses_items'] += 1
            if review is not None:
                review_parts.append((record['id'],f'### Assistant record {record["id"]}\n\n```json\n'+json.dumps(native,ensure_ascii=False,indent=2)+'\n```\n'))

    assistant_by_id = dict(assistants)
    plain_fields = ('reasoning_content', 'reasoning')
    reasoning_scope = ('Native Chat string reasoning_content/reasoning only; not structured '
                       'reasoning_details, Responses output, or exact evidence')
    fixture_limitation = ('Literal compiler fixtures without assistant source records '
                          'cannot establish native replay fidelity')

    def selected_sources(group_ids, where, before):
        """Resolve identity, not whichever earlier message happens to match."""
        selected = []
        for group_id in group_ids:
            group = by_id.get(group_id, {})
            assistant_id = group.get('assistant_record', group_id)
            original = assistant_by_id.get(assistant_id)
            if (before is not None and (group_id >= before or assistant_id >= before)):
                original = None
            if original is None:
                limitations.add(fixture_limitation)
                if assistants:
                    check(False, where, 'Selected exchange has no earlier native assistant source')
            selected.append((group_id, assistant_id, original))
        return selected

    def chat_projection(original, assistant_id, before):
        rejected = any(r['event'] == 'response_rejected'
                       and r.get('response_record') == assistant_id
                       and (before is None or r['id'] < before) for r in events)
        if rejected:
            # Invalid envelopes execute no calls. Only these typed safe fields may
            # survive; matching content alone cannot attest a rejected envelope.
            fields = original if isinstance(original, dict) else {}
            safe = {'role': 'assistant', 'content': fields.get('content')
                    if isinstance(fields.get('content'), str) else ''}
            for field in ('reasoning', 'reasoning_content', 'reasoning_details'):
                value = fields.get(field)
                if (field != 'reasoning_details' and isinstance(value, str) or
                        field == 'reasoning_details' and isinstance(value, list)
                        and all(isinstance(item, dict) for item in value)):
                    safe[field] = value
            return safe, 'rejected_envelope_safe_replays'
        malformed = malformed_projection(original)
        if malformed is not None:
            return malformed, 'explicit_malformed_call_projections'
        return native_replay(original), 'native_replay_matches'

    def omitted_fields(source, omit, before):
        _, assistant_id, original = source
        if not omit or original is None or original.get('protocol') == 'responses':
            return []
        replay, _ = chat_projection(original, assistant_id, before)
        return [field for field in plain_fields if isinstance(replay.get(field), str)]

    def reasoning_selection(manifest, selected, where, before):
        declared = 'reasoning_replay' in manifest or 'reasoning_omission' in manifest
        mode = manifest.get('reasoning_replay', 'all')
        check(mode in ('all', 'latest', 'none'), where, 'Unknown reasoning replay policy')
        eligible = {source[0] for source in (selected if mode == 'none' else
                    selected[:-1] if mode == 'latest' else [])}
        omissions = [omitted_fields(source, source[0] in eligible, before) for source in selected]
        if declared:
            check('reasoning_replay' in manifest and 'reasoning_omission' in manifest, where,
                  'Reasoning replay policy and omission metadata must be declared together')
            marker = manifest.get('reasoning_omission')
            valid = (isinstance(marker, dict) and set(marker) ==
                     {'scope', 'fields', 'exchanges', 'exchange_ids'} and
                     marker.get('scope') == reasoning_scope and
                     type(marker.get('fields')) is int and type(marker.get('exchanges')) is int and
                     isinstance(marker.get('exchange_ids'), list))
            check(valid, where, 'Malformed reasoning omission metadata or scope')
            if all(source[2] is not None for source in selected) or mode == 'all':
                expected = dict(scope=reasoning_scope,
                    fields=sum(len(fields) for fields in omissions),
                    exchanges=sum(bool(fields) for fields in omissions),
                    exchange_ids=[source[0] for source, fields in zip(selected, omissions) if fields])
                check(marker == expected, where,
                      'Reasoning omission metadata differs from selected native sources and policy')
                counts['reasoning_omission_source_checks'] += 1
            elif valid:
                # Literal compiler fixtures have no raw native source. Check their
                # shape/eligibility, but do not pretend to attest absent fields.
                ids = marker['exchange_ids']
                check(all(type(identity) is int and identity in eligible for identity in ids)
                      and ids == [source[0] for source in selected if source[0] in ids]
                      and len(ids) == len(set(ids)) == marker['exchanges']
                      and len(ids) <= marker['fields'] <= 2 * len(ids), where,
                      'Reasoning omission metadata violates policy eligibility or counts')
        return mode, omissions

    def audit_chat_message(message, source, omitted, where, before):
        _, assistant_id, original = source
        if original is None:
            return
        check(original.get('protocol') != 'responses', where,
              'Chat selection refers to a different protocol')
        expected, counter = chat_projection(original, assistant_id, before)
        expected = copy.deepcopy(expected)
        for field in omitted:
            expected.pop(field, None)
        check(message == expected, where,
              'Chat replay differs from selected native source or declared reasoning policy')
        if message == expected:
            counts[counter] += 1
            counts['selected_chat_source_checks'] += 1
            for field in ('reasoning', 'reasoning_content', 'reasoning_details'):
                if message.get(field) is not None:
                    counts['replayed_' + field] += 1

    def audit_representation(record):
        """A representation is bound to its source and the following compiler commit."""
        where = record['id']
        selected = selected_sources([record['exchange_id']], where, where)
        declared = 'reasoning_replay' in record or 'omitted_reasoning_fields' in record
        mode = record.get('reasoning_replay', 'all')
        check(mode in ('all', 'latest', 'none'), where, 'Unknown representation reasoning policy')
        omit = mode == 'none'
        if declared:
            check('reasoning_replay' in record and 'omitted_reasoning_fields' in record, where,
                  'Representation reasoning policy and omitted fields must be declared together')
            following = next((e for e in events if e['id'] > where
                              and e['event'] == 'context_manifest'), None)
            check(following is not None and record['exchange_id'] in following.get('groups', []),
                  where, 'Reasoning representation has no following selected compiler manifest')
            if following is not None:
                check(mode == following.get('reasoning_replay', 'all'), where,
                      'Representation reasoning policy differs from compiler manifest')
                omit = (mode == 'none' or mode == 'latest' and
                        record['exchange_id'] in following.get('groups', [])[:-1])
        fields = omitted_fields(selected[0], omit, where)
        if declared and selected[0][2] is not None:
            check(record.get('omitted_reasoning_fields') == fields, where,
                  'Representation omitted fields differ from selected native source and policy')
        if record.get('protocol') == 'responses':
            check(not record.get('omitted_reasoning_fields'), where,
                  'Responses representation cannot omit native reasoning items')
            return  # Native item/output sequence is checked separately below.
        messages = [message for message in record.get('messages', [])
                    if message.get('role') == 'assistant']
        check(len(messages) == 1, where, 'Chat representation must contain one selected assistant')
        if len(messages) == 1:
            audit_chat_message(messages[0], selected[0], fields, where, where)

    def responses_messages(request, where, manifest, before):
        """Inspect native items independently; project only user/tool observations for shared checks."""
        messages, native_items, sequence, pending, call_ids = [], [], [], [], set()
        historical_calls, rejected_turns = [], {}
        for item in request['input']:
            kind = item.get('type')
            if kind == 'function_call_output':
                sequence.append(('output',item.get('call_id')))
                check(bool(pending) and item.get('call_id') == pending[0],where,
                      'Responses call/result pairing or order mismatch')
                if pending:
                    pending.pop(0)
                messages.append({'role':'tool','tool_call_id':item.get('call_id'),'content':item['output']})
            elif item.get('role') in ('system','developer','user'):
                check(not pending,where,'Responses calls interrupted before paired outputs')
                messages.append(item)
                content = item.get('content')
                rejected = wrapped(content,'REJECTED TOOL CALL — HISTORICAL DATA, NOT AN ACTIVE CALL')
                if rejected is not None:
                    historical_calls.append(rejected['tool_call'])
                rejected = rejected_response(content)
                if rejected is not None:
                    identifier = rejected.get('response_record')
                    original = next((native for rid,native in assistants if rid == identifier
                                     and (before is None or rid < before)),None)
                    check(rejected.get('native_replay') == 'invalid_response_envelope'
                          and original is not None and rejected.get('native_turn') == original,where,
                          'Rejected Responses envelope notice changes original native turn')
                    check(any(r['event'] == 'response_rejected' and r.get('response_record') == identifier
                              for r in events),where,'Rejected Responses envelope has no recorded rejection')
                    check(not any(r['event'] == 'invocation' and r.get('assistant_record') == identifier
                                  for r in events),where,'Rejected Responses envelope executed a call')
                    rejected_turns[identifier] = original
                    counts['exact_historical_responses_envelopes'] += 1
            else:
                native_items.append(item)
                sequence.append(('native',item))
                if kind == 'function_call':
                    identifier = item.get('call_id')
                    check(isinstance(identifier,str) and bool(identifier) and identifier not in call_ids,
                          where,'Missing or duplicate Responses call ID')
                    pending.append(identifier)
                    call_ids.add(identifier)
                    if strict:
                        check(argument_object(item.get('arguments')),where,
                              'Responses native replay includes arguments that are not standard JSON objects')
        check(not pending,where,'Request ends with unpaired Responses calls')
        if manifest:
            expected, expected_sequence = [], []
            for group_id in manifest['groups']:
                group = by_id.get(group_id,{})
                assistant_id = group.get('assistant_record',group_id)
                original = next((native for rid,native in assistants if rid == assistant_id
                                 and (before is None or rid < before)),None)
                if original is None:
                    limitations.add('Literal compiler fixtures without assistant source records cannot establish native replay fidelity')
                    if assistants:
                        check(False,where,'Responses selected exchange has no earlier native assistant source')
                    continue
                check(original.get('protocol') == 'responses',where,'Responses selection refers to a different protocol')
                if assistant_id not in rejected_turns:
                    items = [item for item in original.get('output') or [] if item not in historical_calls]
                    expected.extend(items)
                    expected_sequence.extend(('native',item) for item in items)
                    expected_sequence.extend(('output',item.get('call_id')) for item in items
                                             if item.get('type') == 'function_call')
            check(native_items == expected,where,'Responses replay changes selected native items or their order')
            check(sequence == expected_sequence,where,'Responses replay changes native item/output exchange order')
            counts['responses_native_sequence_checks'] += 1
        else:
            limitations.add('Responses request without a manifest cannot establish complete selected native-item replay')
        counts['responses_replayed_items'] += len(native_items)
        return messages

    def audit_request(request, where, manifest=None, before=None):
        responses = 'input' in request
        messages = responses_messages(request,where,manifest,before) if responses else request.get('messages',[])
        source_selection, omissions, assistant_index = [], [], 0
        if manifest is not None:
            source_selection = selected_sources(manifest.get('groups', []), where, before)
            _, omissions = reasoning_selection(manifest, source_selection, where, before)
            if not responses:
                check(sum(message.get('role') == 'assistant' for message in messages) == len(source_selection),
                      where, 'Chat assistant count differs from selected exchanges')
        else:
            limitations.add('Request without selected manifest cannot establish complete native-source replay')
        pending = []
        facts, account = None,None
        fact_positions, head_accounts = [], []
        exact_views = []
        for position,message in enumerate(messages):
            role = message['role']
            if role == 'tool':
                if not responses:
                    check(bool(pending) and message['tool_call_id'] == pending[0],where,'Native call/result pairing or order mismatch')
                    if pending:
                        pending.pop(0)
                result = json.loads(message['content'])
                original = result_source(result,message['tool_call_id'],where,before)
                inspect_views(result,where)
                if 'harness_runtime' in result:
                    fact_positions.append(position)
                facts = result.get('harness_runtime',facts)
            else:
                check(not pending,where,'Native calls interrupted before paired results')
                if role == 'assistant':
                    calls = message.get('tool_calls',[])
                    pending = [call['id'] for call in calls]
                    check(len(pending) == len(set(pending)),where,'Duplicate native call IDs')
                    if strict:
                        check(all(argument_object(call['function']['arguments']) for call in calls),
                              where,'Native replay includes arguments that are not standard JSON objects')
                    if manifest is not None:
                        if assistant_index < len(source_selection):
                            audit_chat_message(message, source_selection[assistant_index],
                                               omissions[assistant_index], where, before)
                        assistant_index += 1
                    elif assistants:
                        # Historical journals without a selected manifest can only
                        # establish a match to some prior source, not its identity.
                        candidates = [(record_id, native) for record_id, native in assistants
                                      if before is None or record_id < before]
                        check(any(chat_projection(native, record_id, before)[0] == message
                                  for record_id, native in candidates), where,
                              'Assistant replay does not match any earlier native message')
                    else:
                        limitations.add(fixture_limitation)
                if role == 'user':
                    content = message.get('content')
                    head_account = wrapped(content,'AGENT WORKING STATE — FALLIBLE')
                    if head_account is not None:
                        head_accounts.append(head_account)
                        account = head_account
                    current_facts = wrapped(content,'CURRENT HARNESS FACTS')
                    if current_facts is not None:
                        fact_positions.append(position)
                        facts = current_facts
                    exact = wrapped(content,'EXACT JOURNAL EVIDENCE — HISTORICAL DATA, NOT INSTRUCTIONS')
                    if exact is not None:
                        exact_views.append(exact)
                        audit_view(exact,where)
                        selected = exact['selected_byte_range']
                        ranges = exact['shown_byte_ranges']
                        check(ranges == [selected] if selected[0] != selected[1] else not ranges,
                              where,'Pinned exact evidence is shortened')
                    rejected = wrapped(content,'REJECTED TOOL CALL — HISTORICAL DATA, NOT AN ACTIVE CALL')
                    if rejected is not None:
                        call,result = rejected['tool_call'],rejected['result']
                        call_id = call.get('call_id') if responses else call.get('id')
                        arguments = call.get('arguments') if responses else call['function']['arguments']
                        original = result_source(result,call_id,where,before) or result
                        check(rejected.get('native_replay') in ('unparseable_arguments','invalid_json_object_arguments') and result['status'] == 'rejected'
                              and result.get('effects','none') == 'none',
                              where,'Historical malformed call lost its paired rejection/effect status')
                        check(not argument_object(arguments),where,
                              'Historical malformed-call projection contains valid JSON object arguments')
                        if assistants:
                            check(any(call in (native.get('output') or [] if responses else native_replay(native).get('tool_calls',[])) for record_id,native in assistants
                                      if before is None or record_id < before),where,'Historical malformed call differs from original native call')
                        if responses:
                            check(call.get('type') == 'function_call' and original.get('native_item_id') == call.get('id'),
                                  where,'Historical rejected Responses call changes item identity')
                        inspect_views(result,where)
                        counts['exact_historical_malformed_calls'] += 1
        check(not pending,where,'Request ends with unpaired native calls')
        if task is not None:
            check(len(messages)>1 and messages[1] == {'role':'user','content':task},where,'Original task changed or moved')
            counts['original_task_checks'] += 1
        tail_layout = facts is not None and 'working_account' in facts
        if tail_layout:
            account = facts['working_account']
            check(fact_positions == [len(messages)-1],where,'Fresh facts and working account must appear once at the request tail')
            check(not head_accounts,where,'Duplicate head account in tail layout')
        if account and account['state'] in ('current','stale'):
            source = by_id.get(account.get('record'),{})
            check(bool(source.get('blob')),where,'Current/stale account has no saved snapshot')
            if source.get('blob'):
                check(account['text'].encode() == blob(source['blob']),where,'Account text differs from its named snapshot')
                counts['account_snapshot_matches'] += 1
        if manifest and manifest.get('request_blob'):
            selections = manifest.get('kept_records',[])
            check(len(exact_views) == len(selections),where,'Exact evidence count differs from retained selections')
            for shown,selection in zip(exact_views,selections):
                record_id,part = selection['record_id'],selection.get('part','payload')
                raw = history_bytes(record_id,part)
                check(artifact_identity(shown) == hashlib.sha256(raw).hexdigest(),where,
                      'Exact evidence differs from selected historical record bytes')
                check(shown['selected_byte_range'] == selected_interval(raw,selection.get('view','full')),where,
                      'Exact evidence range differs from retained selection')
                if 'scope' in shown:
                    source = shown.get('source',{})
                    check(source.get('record_id') == record_id and source.get('part') == part,where,
                          'Compact exact evidence changes historical source identity')
                purpose = selection.get('purpose')
                expected_purpose = None
                if 'purpose' in selection:
                    check(isinstance(purpose,str) and bool(purpose),where,
                          'Retained selection purpose must be a nonempty string')
                    expected_purpose = dict(text=purpose,authorship='agent',validation='unverified')
                check(shown.get('retention_purpose') == expected_purpose,where,
                      'Exact evidence retention purpose differs from selection or fallible attribution')
                counts['exact_selection_matches'] += 1
            check(encoded(request) == blob(manifest['request_blob']),where,'Request differs from finalized compiler blob')
            check(manifest['bytes'] == len(encoded(request)),where,'Manifest serialized length is stale')
            estimate = math.ceil(len(encoded(request))*manifest['tokens_per_byte'])
            check(manifest['estimated_tokens'] == estimate,where,'Manifest finalized estimate differs from serialization')
            limit = min(manifest['input_limit_tokens'],manifest['total_limit_tokens']-manifest['output_tokens'])
            check(manifest['effective_input_limit_tokens'] == limit and estimate <= limit,where,'Final request exceeds or misstates effective capacity')
            check(request['max_output_tokens' if responses else 'max_tokens'] == manifest['output_tokens'],where,'Generation allowance differs from manifest')
            check(facts is not None and account is not None,where,'Compiled request lacks account/runtime observations')
            if facts:
                if tail_layout:
                    projected = {key:manifest[key] for key in
                        ('exchanges','effective_input_limit_tokens','output_tokens','replay_omission')}
                    projected.update({key: manifest[key] for key in
                                      ('reasoning_replay', 'reasoning_omission') if key in manifest})
                    projected.update(masked_exchanges=len(manifest['masked_groups']),
                                     exact_evidence_selections=len(manifest['kept_records']))
                    telemetry = {'estimated_tokens_before_telemetry','estimate_scope',
                                 'continuation_headroom_estimate_tokens'}
                    check(set(facts['context']) == set(projected) | telemetry,where,'Unexpected compact context fields')
                    for key,value in projected.items():
                        check(facts['context'].get(key) == value,where,f'Compact context differs from manifest: {key}')
                    check(facts['sampled_at'] == manifest['runtime_sampled_at'],where,'Runtime sample time differs from manifest')
                    check(account['record'] == manifest['account_record'] and account['state'] == manifest['account_state'],where,
                          'Tail account identity differs from manifest')
                    # Independently reconstruct the counted body before telemetry;
                    # the whole account is present already, in the same carrier.
                    before_telemetry = copy.deepcopy(request)
                    tail = before_telemetry['input' if responses else 'messages'][-1]
                    tool_tail = tail.get('role') == 'tool' or tail.get('type') == 'function_call_output'
                    if tool_tail:
                        field = 'output' if responses else 'content'
                        result = json.loads(tail[field])
                        baseline_facts = result['harness_runtime']
                    else:
                        baseline_facts = wrapped(tail['content'],'CURRENT HARNESS FACTS')
                    for key in telemetry:
                        baseline_facts['context'].pop(key,None)
                    baseline_facts.pop('notice',None)
                    if tool_tail:
                        tail[field] = encoded(result).decode()
                    else:
                        tail['content'] = '<tool_response>CURRENT HARNESS FACTS\n'+encoded(baseline_facts).decode()+'</tool_response>'
                    estimated_before = math.ceil(len(encoded(before_telemetry))*manifest['tokens_per_byte'])
                    check(facts['context']['estimated_tokens_before_telemetry'] == estimated_before,where,
                          'Compact pre-telemetry estimate omits or changes serialized input')
                    check(facts['context']['estimate_scope'] == 'compiled request before estimate, continuation-headroom fields, and pressure notice',
                          where,'Compact estimate scope differs from its counted input')
                    check(facts['context']['continuation_headroom_estimate_tokens'] == limit-estimated_before,where,
                          'Compact continuation headroom differs from capacity and estimate')
                else:
                    # Saved historical journals retain their original full facts/head-account contract.
                    excluded = {'id','event','timestamp','bytes','request_blob','estimated_tokens','estimate_scope'}
                    for key,value in manifest.items():
                        if key not in excluded:
                            check(facts['context'].get(key) == value,where,f'Displayed candidate metadata differs from manifest: {key}')
                    check(facts['account_record'] == manifest['account_record'] and facts['account_state'] == manifest['account_state'],where,'Runtime account identity differs from manifest')
                for message in messages:
                    if message['role'] == 'tool':
                        result = json.loads(message['content'])
                        source_id = result.get('details',{}).get('record_id',result.get('result_record'))
                        original = by_id.get(source_id,{}).get('result',result)
                        # Prior acknowledgements describe their original adoption, not the current candidate.
                        if result.get('applied_context') and original.get('invocation_id',0) > (manifest.get('previous_context_id') or 0):
                            for key,value in result['applied_context'].items():
                                check(manifest.get(key) == value,where,f'Current control acknowledgement differs from manifest: {key}')
            if 'account_read' in manifest:
                record_id = manifest['account_record']
                read = manifest['account_read']
                if record_id is None:
                    check(read is None,where,'Absent account has an invented snapshot retrieval')
                else:
                    check(isinstance(read,dict) and set(read) == {'path','range','output_chars'} and
                          read['range'] == 'full' and read['output_chars'] is None and
                          Path(read['path']).name == by_id[record_id]['blob'],where,
                          'Account retrieval does not identify the injected immutable snapshot')
                normalized = [{'record_id':selection['record_id'],'part':selection.get('part','payload'),
                               'range':{'bytes':selected_interval(history_bytes(selection['record_id'],selection.get('part','payload')),
                                                                 selection.get('view','full'))},
                               **({'purpose':selection['purpose']} if 'purpose' in selection else {})}
                              for selection in selections]
                check(manifest.get('retained_evidence') == normalized,where,
                      'Normalized retained-evidence retrieval differs from exact selections')
                check(facts and facts.get('kind') == 'fresh_global_state',where,'Current facts lack fresh-global-state attribution')
                if account:
                    seed_authorship = 'operator-supplied; fallible, not original requirements'
                    check(account.get('authorship') in ('agent', seed_authorship) and
                          account.get('validation') == 'unverified' and
                          account.get('state_meaning') == 'current means the latest loaded snapshot, not validated contents',where,
                          'Working account lacks fallible authorship/validation attribution')
                    if account.get('authorship') == seed_authorship:
                        # A label alone is not provenance. Independently bind the delivered
                        # account to local import records and the exact foreign manifest bytes.
                        imported = by_id.get(account.get('handoff_import_record'), {})
                        seeded = by_id.get(account.get('handoff_account_record'), {})
                        origin = by_id.get(imported.get('manifest_record'), {})
                        linked = (imported.get('event') == 'handoff_import' and
                                  seeded.get('event') == 'handoff_account' and
                                  origin.get('event') == 'handoff_manifest' and
                                  seeded.get('manifest_record') == origin.get('id') and
                                  any(m.get('kind') == 'account' and m.get('record_id') == seeded.get('id')
                                      for m in imported.get('mapping', [])))
                        check(linked, where, 'Seed account lacks local handoff provenance links')
                        if linked:
                            foreign = payload(origin)
                            selection = foreign.get('account', {})
                            raw = blob(seeded['blob'])
                            digest = hashlib.sha256(raw).hexdigest()
                            check(foreign.get('account_authorship') == seed_authorship and
                                  selection.get('sha256') == digest and selection.get('bytes') == len(raw) and
                                  account.get('sha256') == digest and account.get('text', '').encode() == raw and
                                  seeded.get('foreign_source', {}).get('manifest_sha256') == origin['blob'] and
                                  seeded.get('foreign_source', {}).get('selection') == selection,
                                  where, 'Seed account differs from its attributed handoff bytes')
                            counts['seed_account_provenance_checks'] += 1
            if 'replay_omission' in manifest:
                previous = by_id.get(manifest.get('previous_context_id'),{})
                removed = [group for group in previous.get('groups',[]) if group not in manifest['groups']]
                marker = manifest['replay_omission']
                if removed:
                    expected = {'exchanges':len(removed),'exchange_id_bounds':[removed[0],removed[-1]],
                        'history_search':{'query':'','event':'exchange','first':removed[0],
                                          'last':manifest['journal_head_at_sampling']}}
                    check(isinstance(marker,dict) and {key:value for key,value in marker.items() if key != 'meaning'} == expected,
                          where,'Replay omission marker differs from removed exchange history')
                    check(isinstance(marker,dict) and isinstance(marker.get('meaning'),str) and bool(marker['meaning']),
                          where,'Replay omission marker lacks its scope explanation')
                else:
                    check(marker == previous.get('replay_omission'),where,'Replay omission marker changed without omitted exchanges')
            counts['finalized_manifest_checks'] += 1

    check([record['id'] for record in events] == list(range(1,len(events)+1)),'journal','Record IDs are not consecutive')
    for record in events:
        kind,identifier = record['event'],record['id']
        counts[kind+'_records'] += 1
        try:
            if kind == 'context_manifest':
                if record.get('request_blob'):
                    audit_request(json.loads(blob(record['request_blob'])),f'manifest {identifier}',record,identifier)
                else:
                    limitations.add('Baseline manifests do not retain finalized compiler request blobs; final-fit/candidate agreement is not covered')
                    if strict:
                        check(False,identifier,'Strict audit requires a finalized compiler request blob')
            elif kind == 'request':
                request = payload(record)
                manifest = by_id.get(record.get('context_id'))
                audit_request(request,f'request {identifier}',manifest,identifier)
            elif kind == 'http_request':
                logical = by_id.get(record.get('request_record'))
                check(logical is not None,identifier,'HTTP body lacks logical request link')
                if logical is not None:
                    request = dict(payload(logical))
                    request.update(request.pop('extra_body',{}))
                    check(payload(record) == request,identifier,'Actual HTTP body differs from logical request after extra_body expansion')
                    counts['logical_http_matches'] += 1
            elif kind == 'invocation' and 'native_item_id' in record:
                original = next((native for rid,native in assistants if rid == record.get('assistant_record')), {})
                check(original.get('status') == 'completed',identifier,'Incomplete Responses envelope executed a call')
                calls = [item for item in original.get('output',[]) if item.get('type') == 'function_call'
                         and item.get('id') == record['native_item_id']]
                check(len(calls) == 1,identifier,'Invocation native item ID does not identify one returned function call')
                if len(calls) == 1:
                    call = calls[0]
                    check((record.get('call_id'),record.get('tool_name'),record.get('raw_arguments')) ==
                          (call.get('call_id'),call.get('name'),call.get('arguments')),identifier,
                          'Responses invocation differs from native call ID/name/arguments')
                counts['responses_invocation_checks'] += 1
            elif kind in ('exchange','exchange_representation'):
                if 'exchange_id' in record:
                    audit_representation(record)
                if record.get('protocol') == 'responses':
                    sequence = []
                    for message in record.get('messages',[]):
                        if 'responses_output' in message:
                            sequence.extend(message['responses_output'])
                        elif message['role'] == 'tool':
                            sequence.append({'type':'function_call_output','call_id':message['tool_call_id'],
                                             'output':message['content']})
                        else:
                            sequence.append(message)
                    responses_messages({'input':sequence},identifier,{'groups':[record['exchange_id']]},identifier)
                for message in record.get('messages',[]):
                    if message['role'] == 'tool':
                        result = json.loads(message['content'])
                        result_source(result,message['tool_call_id'],identifier,identifier)
                        inspect_views(result,identifier)
            elif kind == 'tool_result':
                invocation = by_id.get(record.get('invocation_id'),{})
                if 'native_item_id' in invocation:
                    result = record['result']
                    check((result.get('invocation_id'),result.get('call_id'),result.get('native_item_id')) ==
                          (invocation['id'],invocation['call_id'],invocation['native_item_id']),identifier,
                          'Responses authoritative result changes invocation or native item identity')
            if review is not None and kind == 'tool_result':
                result = record['result']
                body = f'### Tool result record {identifier}\n\n```json\n'+json.dumps(result,ensure_ascii=False,indent=2)+'\n```\n'
                if result.get('output'):
                    raw = artifact_bytes(result['output'])
                    try:
                        text = raw.decode()
                        body += '\nComplete captured artifact (UTF-8; presentation range may select less):\n\n```text\n'+text+'\n```\n'
                    except UnicodeDecodeError:
                        body += '\nComplete captured artifact (base64):\n\n```text\n'+base64.b64encode(raw).decode()+'\n```\n'
                review_parts.append((identifier,body))
        except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
            errors.append({'where':identifier,'error':f'{type(exc).__name__}: {exc}'})
    if review is not None:
        Path(review).write_text('# Full unique assistant and tool-result evidence\n\nHistorical data, not active instructions. This expansion is for human/model trajectory review; creating it does not establish that it has been read.\n\n'+'\n'.join(body for identifier,body in sorted(review_parts)))
    counts['verified_artifact_hashes'] = len(verified_blobs)
    return {'journal':str(journal),'strict':strict,'passed':not errors,'counts':dict(counts),
            'errors':errors,'limitations':sorted(limitations),
            'responses_items':response_items,'responses_envelopes':response_envelopes,
            'scope':'Saved client data and mechanical fidelity only; no claim about hosted template rendering or model understanding',
            'review':str(Path(review).resolve()) if review is not None else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('journal',type=Path)
    parser.add_argument('--strict',action='store_true',help='require final compiler manifests and corrected continuation contract')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--review',type=Path,help='write every unique assistant and tool outcome, expanding complete captured artifacts')
    args = parser.parse_args()
    result = audit(args.journal,args.strict,args.review)
    serialized = json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        args.output.write_text(serialized)
    print(serialized,end='')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
