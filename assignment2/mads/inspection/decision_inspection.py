"""Bounded, read-only projections of Journal evidence; never a wire-body builder."""
import json
from pathlib import Path

from mads.store import dump


ANCHORS = ('request', 'http_request', 'response', 'assistant')
SELECTABLE = ANCHORS + ('invocation', 'tool_result')
LINK_FIELDS = ('request_record', 'http_request_record', 'response_record', 'assistant_record',
               'context_id', 'attempt_record', 'invocation_id', 'account_record')
NOTICE_MARKER = 'STUDY PARTICIPANT MESSAGE'


class DecisionInspector:
    def __init__(self, journal, preview_chars, offset, limit):
        self.journal = journal
        self.preview_chars, self.offset, self.limit = preview_chars, offset, limit
        self.issues = []
        self.edges = []
        self.cache = {}

    def preview(self, value):
        text = value if isinstance(value, str) else dump(value)
        shown = text[:self.preview_chars]
        return {'text': shown, 'chars': len(text), 'shown_chars': len(shown),
                'omitted_chars': len(text) - len(shown),
                'representation': 'text' if isinstance(value, str) else 'JSON rendering, not raw bytes'}

    def page(self, values, render=lambda value: value, offset=None):
        offset = self.offset if offset is None else offset
        selected = values[offset:offset + self.limit]
        end = min(offset + len(selected), len(values))
        return {'total': len(values), 'offset': offset, 'limit': self.limit,
                'omitted_before': min(offset, len(values)),
                'omitted_after': len(values) - end,
                'next_offset': end if end < len(values) else None,
                'items': [render(value) for value in selected]}

    def issue(self, code, **fields):
        value = {'code': code, **fields}
        if value not in self.issues:
            self.issues.append(value)

    def blob(self, key):
        if key not in self.cache:
            try:
                self.cache[key] = self.journal.blob(key)
            except (OSError, ValueError) as exc:
                self.cache[key] = None
                self.issue('blob_unavailable', sha256=key, error=self.preview(str(exc)))
        return self.cache[key]

    def artifact(self, key):
        if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            self.issue('invalid_blob_identity')
            return {'state': 'unavailable'}
        raw = self.blob(key)
        return {'state': 'verified' if raw is not None else 'unavailable', 'sha256': key,
                'path': str(self.journal.journal / 'blobs' / key),
                'bytes': len(raw) if raw is not None else None}

    def ref(self, event):
        if event is None:
            return None
        result = {'record_id': event['id'], 'event': event['event'],
                  'recorded_links': {k: event[k] if type(event[k]) is int or event[k] is None
                                     else self.preview(event[k]) for k in LINK_FIELDS if k in event},
                  'record_source': {'path': str(self.journal.journal / 'events.jsonl'),
                                    'byte_range': self.journal.record_ranges[event['id']]},
                  'retrieve_argv': ['python', '-m', 'mads', 'logs', str(self.journal.run),
                                    '--event', str(event['id']), '--raw']}
        if 'blob' in event:
            result['payload'] = self.artifact(event['blob'])
        artifact = event.get('artifact')
        if isinstance(artifact, dict):
            result['recorded_provenance'] = self.preview({k: artifact[k] for k in
                ('source_path', 'observed_at', 'changed_during_capture', 'job_id', 'job_byte_range')
                if k in artifact})
        return result

    def payload(self, event):
        if not event or 'blob' not in event:
            if event:
                self.issue('payload_not_captured', record_id=event['id'])
            return None
        artifact = self.artifact(event['blob'])
        if artifact['state'] != 'verified':
            return None
        try:
            value = json.loads(self.blob(event['blob']))
            if not isinstance(value, dict):
                self.issue('payload_not_object', record_id=event['id'])
            return value
        except (ValueError, UnicodeError):
            self.issue('payload_not_json', record_id=event['id'])
            return None

    def parent(self, event, field, kinds):
        if event is None:
            return None
        identifier = event.get(field)
        if identifier is None:
            self.issue('link_not_captured', record_id=event['id'], field=field)
            return None
        target = self.journal.by_id.get(identifier) if type(identifier) is int else None
        valid = target and target['event'] in kinds and target['id'] < event['id']
        self.edges.append({'from_record': event['id'], 'field': field,
                           'to_record': identifier if type(identifier) is int else None, 'valid': bool(valid)})
        if not valid:
            self.issue('invalid_or_unavailable_link', record_id=event['id'], field=field,
                       target=self.preview(identifier))
            return None
        return target

    def children(self, event, kind, field):
        if not event:
            return []
        return [row for row in self.journal.events if row['event'] == kind
                and type(row.get(field)) is int and row[field] == event['id'] and row['id'] > event['id']]

    def unique_child(self, event, kind, field):
        candidates = self.children(event, kind, field)
        if len(candidates) == 1:
            self.parent(candidates[0], field, (event['event'],))
            return candidates[0]
        if candidates:
            self.issue('ambiguous_children', record_id=event['id'], event=kind,
                       candidate_count=len(candidates))
        return None

    def inspect(self, record, item, view):
        if record == 'latest':
            selected = next((e for e in reversed(self.journal.events) if e['event'] in ANCHORS), None)
        else:
            try:
                if type(record) is not int and not (isinstance(record, str) and record.isdecimal()):
                    raise ValueError('Record must be an integer ID or latest')
                selected = self.journal.by_id[int(record)]
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError(f'Unknown decision record: {record}') from exc
            if selected['event'] not in SELECTABLE:
                raise ValueError('Select a request, http_request, response, assistant, invocation or tool_result record')
        response = assistant = wire = compiled = manifest = None
        focus_invocation = None
        anchor = selected
        if anchor and anchor['event'] == 'tool_result':
            anchor = self.parent(anchor, 'invocation_id', ('invocation',))
        if anchor and anchor['event'] == 'invocation':
            focus_invocation = anchor
            anchor = self.parent(anchor, 'assistant_record', ('assistant',))
        if anchor and anchor['event'] == 'assistant':
            assistant = anchor
            response = self.parent(assistant, 'response_record', ('response',))
        elif anchor and anchor['event'] == 'response':
            response = anchor
        elif anchor and anchor['event'] == 'http_request':
            wire = anchor
            response = self.unique_child(wire, 'response', 'http_request_record')
        elif anchor and anchor['event'] == 'request':
            compiled = anchor
            # A logical request can have multiple attempts, including a newer unfinished one.
            # Do not choose a response or substitute compiled bytes for uncaptured wire bytes.
            self.issue('response_not_selected', record_id=anchor['id'])
        if response:
            linked_wire = self.parent(response, 'http_request_record', ('http_request',))
            if wire is None:
                wire = linked_wire
            if assistant is None:
                assistant = self.unique_child(response, 'assistant', 'response_record')
        compiled_links = [self.parent(e, 'request_record', ('request',))
                          for e in (wire, response, assistant) if e]
        compiled_links = [e for e in compiled_links if e]
        if len({e['id'] for e in compiled_links}) > 1:
            self.issue('conflicting_request_links')
            # Preserve the exact response->HTTP link but do not attach an ambiguous context.
            compiled = None
        elif compiled_links:
            compiled = compiled_links[0]
        if compiled:
            manifest = self.parent(compiled, 'context_id', ('context_manifest',))
        chain = {name: self.ref(e) for name, e in (
            ('compiled_request', compiled), ('wire_request', wire), ('response', response),
            ('assistant', assistant), ('context_manifest', manifest))}
        request_event = wire if view == 'wire' else compiled
        request = self.request_view(request_event, compiled, item, view)
        related = [e for e in self.journal.events if compiled and type(e.get('request_record')) is int
                   and e['request_record'] == compiled['id'] and e['id'] > compiled['id']
                   and e['event'] in ('api_attempt', 'http_request', 'response', 'api_error', 'api_retry')]
        invocations = self.children(assistant, 'invocation', 'assistant_record')
        for invocation in invocations:
            if not self.children(invocation, 'tool_result', 'invocation_id'):
                self.issue('invocation_outcome_not_recorded', record_id=invocation['id'])
        decision = self.response_view(response, assistant, invocations)
        context = self.context_view(manifest)
        calls = self.page(invocations, self.invocation_view)
        rejected = self.page(self.children(assistant, 'response_rejected', 'response_record'),
                             lambda e: {'source': self.ref(e), 'detail': self.preview(e)})
        if not wire:
            self.issue('no_captured_causal_http_request')
        if not response:
            self.issue('response_unavailable_or_not_selected')
        if response and not assistant:
            self.issue('assistant_not_recorded_or_ambiguous', record_id=response['id'])
        if self.journal.incomplete_tail_bytes:
            self.issue('incomplete_journal_tail', bytes=self.journal.incomplete_tail_bytes)
        # Materialize projections before state/issues: verifying even a sibling retry's
        # blob may reveal unavailable evidence. Nested pages start at zero; focus lets
        # --offset navigate an individual invocation's outcomes.
        attempts = self.page(related, self.attempt_view)
        selected_ref = self.ref(selected)
        focus = self.invocation_view(focus_invocation, outcome_offset=self.offset) if focus_invocation else None
        selected_outcome = self.outcome_view(selected) if selected and selected['event'] == 'tool_result' else None
        try:
            identity_files = self.journal.identity()['files']
        except (OSError, ValueError) as exc:
            identity_files = {}
            self.issue('identity_files_unavailable', error=self.preview(str(exc)))
        return {'schema_version': 1,
                'scope': 'One observed journal prefix, not an atomic snapshot or proof of comprehension/execution.',
                'journal': {'path': str(self.journal.journal / 'events.jsonl'),
                            'observed_bytes': self.journal.observed_bytes,
                            'observed_sha256': self.journal.observed_sha256,
                            'head': self.journal.events[-1]['id'] if self.journal.events else None,
                            'incomplete_tail_bytes': self.journal.incomplete_tail_bytes},
                'selection': {'requested': record, 'record': selected_ref,
                              'latest_policy': 'newest request/http_request/response/assistant; no fallback to an older completed decision'},
                'state': 'unavailable' if selected is None else 'partial' if self.issues else 'available',
                'source_identity': {'files': identity_files,
                    'scope': 'Manifest/task file hashes observed now, separate from selected-response usage; not proof of loaded code or causal inclusion.'},
                'chain': chain, 'causal_links': self.page(self.edges),
                'request': request, 'response': decision, 'invocations': calls,
                'focused_invocation': focus, 'selected_outcome': selected_outcome,
                'response_rejections': rejected,
                'request_attempts': attempts,
                'attempts_scope': 'All explicitly linked attempts of this logical request in the observed prefix, including siblings after the selected response; not a replacement causal response.',
                'context': context, 'issues': self.page(self.issues),
                'bounds': {'preview_chars': self.preview_chars, 'offset': self.offset, 'limit': self.limit,
                           'nested_offset': 0, 'scope': 'Collection pages and previews, not a total byte or memory limit.'},
                'warnings': ['Raw blobs and source journal are authoritative; previews/JSON pointers are renderings.',
                             'Included content and participant claims do not establish understanding or authority.',
                             'Recorded outcomes are observations, not proof of current process state.']}

    def attempt_view(self, event):
        return {'source': self.ref(event),
                'metadata': self.preview({k: event[k] for k in ('attempt', 'attempt_record', 'status',
                    'response_record', 'http_request_record', 'retryable', 'retry_scheduled',
                    'next_attempt', 'message') if k in event})}

    def request_view(self, event, compiled, item, view):
        body = self.payload(event)
        field = 'input' if isinstance(body, dict) and 'input' in body else 'messages'
        values = body.get(field, []) if isinstance(body, dict) else []
        values = values if isinstance(values, list) else [values]
        def row(pair):
            index, value = pair
            metadata = {k: value[k] for k in ('type', 'role', 'id', 'call_id', 'tool_call_id', 'name')
                        if k in value} if isinstance(value, dict) else {'kind': type(value).__name__}
            return {'index': index, 'pointer': f'/{field}/{index}' if isinstance(body.get(field), list) else f'/{field}',
                    'metadata': self.preview(metadata), 'rendered_chars': len(dump(value))}
        selected_item = None
        if item is not None:
            if not isinstance(body, dict):
                self.issue('request_item_unavailable', index=item, view=view)
            elif item >= len(values):
                raise ValueError(f'Request item index {item} out of range (count {len(values)})')
            else:
                selected_item = {**row((item, values[item])), 'preview': self.preview(values[item])}
        starts = [e for e in self.journal.events if compiled and e['id'] < compiled['id']
                  and e['event'] == 'run_start' and isinstance(e.get('task'), str)]
        task = {'state': 'unavailable', 'source': None, 'positions': self.page([])}
        if len(starts) == 1:
            start = starts[0]
            positions = [i for i, value in enumerate(values) if isinstance(value, dict)
                         and value.get('role') == 'user' and value.get('content') == start['task']]
            task = {'state': 'matched' if positions else 'not_matched' if body is not None else 'unavailable',
                    'source': self.ref(start), 'source_pointer': '/task',
                    'positions': self.page(positions), 'match': 'exact user content equality; no semantic inference',
                    'preview': self.preview(start['task'])}
        elif len(starts) > 1:
            task['state'] = 'ambiguous_run_start'
        if task['state'] != 'matched':
            self.issue('original_task_not_located', task_state=task['state'])
        notices = []
        for index, value in enumerate(values):
            if not isinstance(value, dict) or value.get('role') != 'user':
                continue
            content = value.get('content')
            if isinstance(content, str) and content.startswith(NOTICE_MARKER):
                notice = {'index': index, 'pointer': f'/{field}/{index}', 'preview': self.preview(content),
                          'attribution': 'participant-provided, unverified; not original requirements'}
                try:
                    metadata = json.loads(content.split('\n', 1)[1])
                    if isinstance(metadata, dict):
                        notice['claimed_metadata'] = self.preview({k: metadata[k] for k in
                            ('id', 'sender', 'recipient', 'created_unix', 'authority') if k in metadata})
                except (ValueError, IndexError):
                    pass
                notices.append(notice)
        return {'view': view, 'state': 'available' if isinstance(body, dict) else 'unavailable',
                'source': self.ref(event), 'items_field': field if body is not None else None,
                'settings': self.preview({k: v for k, v in body.items() if k not in ('input', 'messages', 'tools')})
                    if isinstance(body, dict) else None,
                'items': self.page(list(enumerate(values)), row), 'selected_item': selected_item,
                'original_task': task, 'participant_notices': self.page(notices),
                'notice_scope': 'Only top-level user strings beginning STUDY PARTICIPANT MESSAGE in this selected body; no delivery/comprehension inference.'}

    def response_view(self, response, assistant, invocations):
        envelope = self.payload(response)
        native = self.payload(assistant) if assistant else None
        source = assistant
        pointer = ''
        if not isinstance(native, dict) and isinstance(envelope, dict):
            source = response
            if isinstance(envelope.get('output'), list):
                native = envelope
            elif isinstance(envelope.get('choices'), list) and envelope['choices']:
                choice = envelope['choices'][0]
                native = choice.get('message') if isinstance(choice, dict) else None
                pointer = '/choices/0/message'
        if not isinstance(native, dict):
            self.issue('native_decision_unavailable')
        items = []
        if isinstance(native, dict):
            if isinstance(native.get('output'), list):
                items = [(f'/output/{i}', value) for i, value in enumerate(native['output'])]
            else:
                items = [(pointer + '/' + k.replace('~', '~0').replace('/', '~1'), v)
                         for k, v in native.items() if k != 'tool_calls']
                if isinstance(native.get('tool_calls'), list):
                    items += [(f'{pointer}/tool_calls/{i}', value) for i, value in enumerate(native['tool_calls'])]
        def matches_for(value):
            if not isinstance(value, dict):
                return None
            if value.get('type') == 'function_call':
                return [e['id'] for e in invocations if e.get('call_id') == value.get('call_id')
                        and e.get('native_item_id') == value.get('id')]
            if value.get('type') == 'function' and 'function' in value:
                return [e['id'] for e in invocations if e.get('call_id') == value.get('id')]
            return None
        for item_pointer, value in items:
            if assistant and matches_for(value) == []:
                self.issue('native_call_without_recorded_invocation', record_id=assistant['id'], pointer=item_pointer)
        def item_view(pair):
            pointer, value = pair
            row = {'pointer': pointer, 'preview': self.preview(value)}
            if isinstance(value, dict):
                row['metadata'] = self.preview({k: value[k] for k in
                    ('type', 'id', 'call_id', 'role', 'status', 'name') if k in value})
                function = value.get('function') if isinstance(value.get('function'), dict) else value
                if 'arguments' in function:
                    row['arguments'] = self.preview(function['arguments'])
                    row['arguments_pointer'] = pointer + ('/function' if function is not value else '') + '/arguments'
                matches = matches_for(value)
                if matches is not None:
                    row['invocation_records'] = self.page(matches, offset=0)
            return row
        return {'state': 'available' if isinstance(native, dict) else 'unavailable',
                'native_source': self.ref(source),
                'native_items': self.page(items, item_view),
                'http_status': response.get('status') if response and type(response.get('status')) is int else None,
                'assistant_metadata': self.preview({k: assistant[k] for k in
                    ('protocol', 'finish_reason', 'native_finish_reason') if k in assistant}) if assistant else None,
                'envelope_metadata': self.preview({k: envelope[k] for k in ('id', 'model', 'status', 'error',
                    'incomplete_details') if k in envelope}) if isinstance(envelope, dict) else None,
                'usage': self.preview(envelope['usage']) if isinstance(envelope, dict) and 'usage' in envelope else None,
                'usage_scope': 'Only the selected returned response, not aggregate usage across retries.',
                'execution_scope': 'Native items are decisions, not proof of tool execution. Consult linked invocations/results; encrypted reasoning stays opaque.'}

    def invocation_view(self, event, outcome_offset=0):
        outcomes = self.children(event, 'tool_result', 'invocation_id')
        if not outcomes:
            self.issue('invocation_outcome_not_recorded', record_id=event['id'])
        return {'source': self.ref(event),
                'identity': self.preview({k: event[k] for k in ('assistant_record', 'call_id', 'native_item_id', 'tool_name') if k in event}),
                'arguments': self.preview(event.get('raw_arguments', '')),
                'arguments_pointer': '/raw_arguments',
                'outcome_state': 'recorded' if outcomes else 'unavailable',
                'outcomes': self.page(outcomes, self.outcome_view, offset=outcome_offset)}

    def outcome_view(self, event):
        result = event.get('result', {})
        if not isinstance(result, dict):
            result = {}
        evidence = []
        mappings = [('', result)]
        if isinstance(result.get('account_update'), dict):
            mappings.append(('/account_update', result['account_update']))
        for prefix, mapping in mappings:
            for field in ('observation_record', 'output_record'):
                if type(mapping.get(field)) is int:
                    target = self.journal.by_id.get(mapping[field])
                    if target is not None and target['id'] >= event['id']:
                        target = None
                    if target is None:
                        self.issue('outcome_evidence_unavailable', record_id=event['id'],
                                   field=prefix + '/' + field, target=mapping[field])
                    evidence.append({'pointer': '/result' + prefix + '/' + field,
                                     'record_id': mapping[field], 'source': self.ref(target)})
        output = result.get('output')
        artifact = None
        if isinstance(output, dict) and isinstance(output.get('artifact_id'), str):
            artifact = self.artifact(output['artifact_id'])
        return {'source': self.ref(event), 'summary': self.preview({k: result[k] for k in
                    ('status', 'effects', 'error', 'reason', 'context_change', 'account_update') if k in result}),
                'detail': self.preview(result), 'detail_pointer': '/result',
                'evidence': self.page(evidence, offset=0), 'output_artifact': artifact}

    def context_view(self, manifest):
        if not manifest:
            return {'state': 'unavailable'}
        account = None
        account_record = manifest.get('account_record')
        account_source = self.journal.by_id.get(account_record) if type(account_record) is int else None
        if account_source and account_source['id'] >= manifest['id']:
            account_source = None
        if account_record is not None and account_source is None:
            self.issue('account_record_unavailable', record_id=manifest['id'], target=self.preview(account_record))
        # Legacy accounts were agent-authored. New snapshots carry explicit seed
        # attribution; read the selected snapshot, never the latest working file.
        labels = account_source.get('account_labels', {}) if account_source else {}
        if not isinstance(labels, dict):
            labels = {}
        read = manifest.get('account_read')
        if isinstance(read, dict) and isinstance(read.get('path'), str):
            account = self.artifact(Path(read['path']).name)
            if account['state'] == 'verified':
                raw = self.blob(account['sha256'])
                try:
                    account['preview'] = self.preview(raw.decode('utf-8'))
                except UnicodeError:
                    account['preview'] = None
        policies = {key: manifest[key] for key in ('input_limit_tokens', 'total_limit_tokens',
            'effective_input_limit_tokens', 'output_tokens', 'mask_at_tokens', 'pressure_at_tokens',
            'keep_recent', 'preview_head_percent', 'estimated_tokens', 'estimate_scope', 'tokens_per_byte') if key in manifest}
        selections = manifest.get('retained_evidence', [])
        def evidence(selection):
            identifier = selection.get('record_id') if isinstance(selection, dict) else None
            target = self.journal.by_id.get(identifier) if type(identifier) is int else None
            if target and target['id'] >= manifest['id']:
                target = None
            if target is None:
                self.issue('retained_evidence_unavailable', record_id=manifest['id'], target=self.preview(identifier))
            return {'selection': self.preview(selection), 'source': self.ref(target)}
        return {'state': 'available', 'source': self.ref(manifest),
                'account': {'record_id': manifest.get('account_record'), 'state': manifest.get('account_state'),
                            'recovery': self.preview(manifest.get('account_recovery')),
                            'authorship': labels.get('authorship', 'agent'),
                            'validation': labels.get('validation', 'unverified'),
                            'source': self.ref(account_source), 'snapshot': account},
                'controls': self.preview(policies),
                'groups': self.page(manifest.get('groups', [])),
                'masked_groups': self.page(manifest.get('masked_groups', [])),
                'retained_evidence': self.page(selections, evidence),
                'replay_omission': self.preview(manifest.get('replay_omission')),
                'scope': 'Controls/account sampled for the causal request, not later sibling changes. Retention/presence is not comprehension.'}


def inspect_decision(journal, record='latest', item=None, view='wire', preview_chars=240, offset=0, limit=20):
    if type(preview_chars) is not int or not 0 <= preview_chars <= 4000:
        raise ValueError('preview_chars must be 0..4000')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('offset must be nonnegative and limit must be 1..100')
    if item is not None and (type(item) is not int or item < 0):
        raise ValueError('item must be a nonnegative index')
    if view not in ('wire', 'compiled'):
        raise ValueError('view must be wire or compiled')
    return DecisionInspector(journal, preview_chars, offset, limit).inspect(record, item, view)
