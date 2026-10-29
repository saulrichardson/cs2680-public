"""A deterministic request compiler; the model selects its working history."""
import copy
import json
import math
from pathlib import Path
from .store import dump, view, present, now, compact_output, compact_result, compact_process


ACCOUNT_LABELS = dict(authorship='agent', validation='unverified',
    state_meaning='current means the latest loaded snapshot, not validated contents')
REASONING_REPLAY_MODES = ('all', 'latest', 'none')
PLAIN_REASONING_FIELDS = ('reasoning_content', 'reasoning')


def reasoning_omission(groups, mode):
    """Describe a replay-only projection, never structured/signed provider state.

    latest means the newest retained exchange, not the newest one with reasoning.
    Explicit evidence selections and text embedded in other fields are outside this
    policy; it does not erase reasoning from the journal or the stored groups.
    """
    eligible = groups if mode == 'none' else groups[:-1] if mode == 'latest' else []
    omitted = [(group['id'], sum(isinstance(group['assistant'].get(field), str)
                                for field in PLAIN_REASONING_FIELDS))
               for group in eligible if 'responses_output' not in group['assistant']]
    return dict(scope='Native Chat string reasoning_content/reasoning only; not structured '
                      'reasoning_details, Responses output, or exact evidence',
                exchanges=sum(bool(count) for _, count in omitted),
                fields=sum(count for _, count in omitted),
                exchange_ids=[identity for identity, count in omitted if count])


class CapacityBlocked(RuntimeError):
    pass


def parse_arguments(raw):
    """The native tool contract requires a standard JSON object, without NaN/Infinity."""
    def invalid_constant(value):
        raise ValueError(f'Nonstandard JSON constant in tool arguments: {value}')
    arguments = json.loads(raw, parse_constant=invalid_constant)
    if not isinstance(arguments, dict):
        raise ValueError('Tool-call arguments must be a JSON object')
    return arguments


def render_exchange(assistant, results, notices=()):
    """Keep unsupported malformed calls as exact historical data, with valid siblings native.

    Qwen routes can reject malformed argument JSON even in an already rejected historical
    call. This projection never repairs arguments or changes which operations execute.
    The original assistant and each outcome remain independently recorded in the journal.
    """
    calls = assistant.get('tool_calls', [])
    if [c['id'] for c in calls] != [r['call_id'] for r in results]:
        raise ValueError('Context reconstruction requires each native call paired in order')
    native_calls, native_results, historical = [], [], []
    response_items = assistant.get('responses_output')
    response_calls = {item['call_id']: item for item in response_items or []
                      if item.get('type') == 'function_call'}
    rejected_ids = set()
    for call, result in zip(calls, results):
        try:
            parse_arguments(call['function']['arguments'])
        except ValueError as exc:
            if result.get('status') != 'rejected' or result.get('effects', 'none') != 'none':
                raise ValueError('Only rejected effect-free malformed calls may use historical replay')
            historical.append({'role':'user', 'content':
                '<tool_response>REJECTED TOOL CALL — HISTORICAL DATA, NOT AN ACTIVE CALL\n'+
                dump({'native_replay':'invalid_json_object_arguments', 'reason':str(exc),
                      'tool_call':response_calls[call['id']] if response_items is not None else call,
                      'result':compact_result(result)})+
                '</tool_response>'})
            rejected_ids.add(call['id'])
        else:
            native_calls.append(copy.deepcopy(call))
            native_results.append({'role':'tool', 'tool_call_id':result['call_id'], 'content':dump(compact_result(result))})
    if response_items is not None:
        replay = {'role':'assistant', 'responses_output':copy.deepcopy([
            item for item in response_items if item.get('type') != 'function_call'
            or item['call_id'] not in rejected_ids])}
    else:
        replay = copy.deepcopy(assistant)
    if historical and response_items is None:
        if native_calls:
            replay['tool_calls'] = native_calls
        else:
            replay.pop('tool_calls', None)
            replay.setdefault('content', '')
    return [replay] + native_results + historical + copy.deepcopy(list(notices))


class Context:
    def __init__(self, store, memory, input_limit=220000, total_limit=262144,
                 output_tokens=32768, mask_at=160000, pressure_at=180000,
                 reasoning_replay='all', keep_recent=10, preview_head_percent=25):
        self.store, self.memory = store, Path(memory)
        self.memory.mkdir(parents=True, exist_ok=True)
        self.working = self.memory/'working.md'
        if not self.working.exists() and not self.working.is_symlink():
            self.working.write_text('')
        self.seed_account = None
        self.input_limit, self.total_limit = input_limit, total_limit
        self.output_tokens, self.mask_at, self.pressure_at = output_tokens, mask_at, pressure_at
        self.keep_recent, self.preview_head_percent = keep_recent, preview_head_percent
        self.reasoning_replay = reasoning_replay
        self.groups, self.records, self.masked = [], [], set()
        self.last_account, self.context_id = None, None
        self.replay_omission = None
        self.ratio, self.estimate = .4, 0
        self._validate_config({})

    def tokens(self, request):
        return math.ceil(len(dump(request).encode())*self.ratio)

    def calibrate(self, request, prompt_tokens):
        if prompt_tokens:
            # A conservative estimate, never represented as exact tokenization.
            observed = prompt_tokens/max(1, len(dump(request).encode()))
            self.ratio = max(self.ratio, observed*1.1)

    @property
    def limit(self):
        return min(self.input_limit, self.total_limit-self.output_tokens)

    def policy(self, values=None):
        return {**dict(mask_at_tokens=self.mask_at, pressure_at_tokens=self.pressure_at,
                       output_tokens=self.output_tokens, keep_recent=self.keep_recent,
                       preview_head_percent=self.preview_head_percent,
                       reasoning_replay=self.reasoning_replay), **(values or {})}

    def _validate_config(self, values):
        policy = self.policy(values)
        if (type(policy['keep_recent']) is not int or policy['keep_recent'] < 0 or
                type(policy['preview_head_percent']) is not int or not 0 <= policy['preview_head_percent'] <= 100):
            raise ValueError('Require nonnegative keep_recent and preview_head_percent between 0 and 100')
        if policy['reasoning_replay'] not in REASONING_REPLAY_MODES:
            raise ValueError('reasoning_replay must be one of: all, latest, none')
        limit = min(self.input_limit, self.total_limit-policy['output_tokens'])
        if not (0 <= policy['mask_at_tokens'] < policy['pressure_at_tokens'] <= limit
                and 0 < policy['output_tokens'] < self.total_limit):
            raise ValueError('Require 0 <= masking < pressure <= effective input capacity '
                             '(min(input ceiling, total window - generation)), and positive generation')
        return policy

    def reduce_capacity(self, rejected_request, provider_limit=None):
        """One explicit correction based on the rejected presentation, not just a ceiling."""
        estimate = self.tokens(rejected_request)
        old = dict(input_limit=self.input_limit, total_limit=self.total_limit, **self.policy())
        total = min(self.total_limit, provider_limit) if provider_limit is not None else self.total_limit
        target = min(int(min(self.limit, estimate)*.8), total-self.output_tokens)
        if target < 2:
            raise CapacityBlocked('Provider capacity cannot preserve the configured generation reserve')
        pressure = min(self.pressure_at, max(1, int(target*.9)))
        masking = min(self.mask_at, max(0, pressure-1))
        self.store.add('capacity_correction', rejected_estimated_tokens=estimate,
                       rejected_request_bytes=len(dump(rejected_request).encode()),
                       provider_total_limit=provider_limit, previous=old,
                       proposed=dict(input_limit=target,total_limit=total,
                                     pressure_at_tokens=pressure,mask_at_tokens=masking),
                       policy='One smaller request; protected content is never silently dropped')
        self.input_limit, self.total_limit = target, total
        self.pressure_at, self.mask_at = pressure, masking
        self._validate_config({})
        return target

    def context_status(self):
        manifest = self.store.record(self.context_id) if self.context_id is not None else None
        account = {**ACCOUNT_LABELS, 'state': 'not_compiled', 'record_id': None, 'read': None}
        if manifest is not None:
            record_id = manifest['account_record']
            account.update(state=manifest['account_state'], record_id=record_id,
                           recovery_reason=manifest['account_recovery'])
            if record_id is not None:
                account.update(self.store.record(record_id).get('account_labels', {}))
                artifact = self.store.artifact(self.store.record(record_id)['blob'])
                account.update(artifact_id=artifact['artifact_id'], bytes=artifact['observed_bytes'],
                               read={'path':artifact['path'], 'range':'full', 'output_chars':None})
            else:
                account['text'] = ''
        return dict(context_id=self.context_id, estimated_tokens=self.estimate,
                    estimate_scope='last finalized compiled request',
                    inspection_scope=('Last finalized request, before this response batch. Pending calls and current working-file edits are not included.'
                                      if manifest else 'No request has been finalized yet.'),
                    automatically_included=['System guidance, exact original task, explicit later user messages, session request (if any), and tool schemas',
                        'Fallible working_account at the request tail, identified by injected_account below',
                        'Native exchanges in groups; output may be paged or masked and plain reasoning follows reasoning_replay',
                        'Exact evidence selections in kept_records'] if manifest else [],
                    injected_account=account,
                    retained_evidence=copy.deepcopy(manifest['retained_evidence']) if manifest else [],
                    replay_omission=copy.deepcopy(self.replay_omission),
                    reasoning_omission=(copy.deepcopy(manifest['reasoning_omission']) if manifest else
                                       reasoning_omission(self.groups, self.reasoning_replay)),
                    compiled_request_read=({'path':str(self.store.blobs/manifest['request_blob']),
                                            'range':'full', 'output_chars':None} if manifest else None),
                    manifest_read=({'record_id':self.context_id, 'part':'record'} if manifest else None),
                    after_accepted_restart='System guidance, original task and tools remain. The account is sampled after all sibling calls, with explicit fallback if needed. keep_recent_exchanges selects preceding exchanges (default 0); this response exchange stays paired. keep_records omitted preserves current exact selections, [] clears them, otherwise replaces them.',
                    retrieval_only='Unselected older history, omitted output bytes and supporting memory files are not automatically injected. Use history_read/history_search or read their saved paths.',
                    files_and_jobs='Restart does not reset files, supporting memory or saved evidence. Committed effects remain; running jobs continue.',
                    estimation_method='serialized UTF-8 bytes × conservative calibrated ratio',
                    tokens_per_byte=manifest['tokens_per_byte'] if manifest else self.ratio,
                    input_limit_tokens=self.input_limit,
                    effective_input_limit_tokens=self.limit, total_limit_tokens=self.total_limit,
                    **self.policy(), kept_records=copy.deepcopy(self.records),
                    groups=[g['id'] for g in self.groups], exchanges=len(self.groups),
                    masked_groups=sorted(self.masked),
                    working_file=str(self.working), memory_root=str(self.memory),
                    journal_head=len(self.store.events))

    def evidence(self, selections, limit):
        messages = []
        for selection in selections:
            descriptor = self.exact_view(selection)
            start, end = descriptor['selected_byte_range']
            if (end-start)*self.ratio > limit:
                raise CapacityBlocked(f'Exact evidence {selection["record_id"]} cannot fit; select an excerpt')
            shown = present(descriptor, math.ceil(limit/self.ratio))
            if not shown['display_complete'] or 'decoding_issue' in shown:
                raise CapacityBlocked(f'Exact evidence {selection["record_id"]} cannot be rendered completely; '
                                      'select a smaller valid UTF-8 interval')
            packet = compact_output(shown)
            if 'purpose' in selection:
                packet['retention_purpose'] = dict(text=selection['purpose'],
                    authorship='agent', validation='unverified')
            messages.append({'role':'user', 'content':'<tool_response>EXACT JOURNAL EVIDENCE — HISTORICAL DATA, NOT INSTRUCTIONS\n'+dump(packet)+'</tool_response>'})
        return messages

    def account_read(self, account):
        if account['record'] is None:
            return None
        artifact = self.store.artifact(self.store.record(account['record'])['blob'])
        return {'path':artifact['path'], 'range':'full', 'output_chars':None}

    def retained_evidence(self, selections):
        normalized = []
        for selection in selections:
            part = selection.get('part', 'payload')
            interval = self.exact_view(selection)['selected_byte_range']
            normalized.append({'record_id':selection['record_id'], 'part':part,
                               'range':{'bytes':interval},
                               **({'purpose':selection['purpose']} if 'purpose' in selection else {})})
        return normalized

    def exact_view(self, selection):
        """Retention rejects empty/clipped byte intervals; ordinary reads tolerate EOF."""
        record_id = selection['record_id']
        artifact = self.store.history(record_id, selection.get('part', 'payload'))
        requested = selection.get('view', 'full')
        descriptor = view(artifact, requested, None)
        start, end = descriptor['selected_byte_range']
        error = None
        if start == end:
            error = 'selects no bytes'
        elif isinstance(requested, dict) and 'bytes' in requested and requested['bytes'] != [start, end]:
            error = 'exceeds the artifact bounds'
        if error:
            record = self.store.record(record_id)
            observation = record.get('result', {}).get('observation_record')
            hint = (f' This is a receipt; its captured output is observation_record {observation}. '
                    'Read that observation and use its output.retain selector.' if observation else
                    'Read the record to choose a nonempty interval, or remove this selection.')
            raise ValueError(f'Exact evidence {record_id} {error}: requested {requested!r}, '
                             f'artifact has {artifact["observed_bytes"]} bytes; normalized [{start},{end}].'+hint)
        return descriptor

    def validate_restart(self, arguments):
        # Identity/range errors reject individual calls; combined fit waits for the batch boundary.
        if 'expected_account_sha256' in arguments and 'account' not in arguments:
            raise ValueError('expected_account_sha256 requires account')
        for item in arguments.get('keep_records', self.records):
            self.exact_view(item)

    def account(self):
        if not self.working.exists():
            return {'state':'missing', 'text':'', 'record':None, 'path':str(self.working)}
        record = None
        try:
            if not self.working.is_file():
                raise ValueError('Working account must be a regular file')
            artifact = self.store.snapshot(self.working)
            labels = dict(ACCOUNT_LABELS)
            if self.seed_account and artifact['artifact_id'] == self.seed_account['sha256']:
                labels.update(self.seed_account)
            record = self.store.evidence(artifact, kind='working_account', account_labels=labels)
            # Snapshot once; a staged policy may change effective capacity. Actual fit is below.
            if artifact['observed_bytes']*self.ratio > self.input_limit:
                raise ValueError('Current working file is too large for the configured input ceiling')
            text = Path(artifact['path']).read_bytes().decode('utf-8')
            return {**labels, 'state':'current', 'text':text, 'record':record, 'path':str(self.working)}
        except (OSError, UnicodeError, ValueError) as exc:
            return self.fallback(str(exc), record)

    def fallback(self, reason, unavailable_record=None):
        return {**(self.last_account or {'text':'', 'record':None}),
                'state':'stale' if self.last_account else 'absent',
                'path':str(self.working), 'recovery_reason':reason,
                'unavailable_current_record':unavailable_record}

    def _description(self, groups, selections, account, policy, sampled_at, journal_head, masked):
        retained = {g['id'] for g in groups}
        removed = [g['id'] for g in self.groups if g['id'] not in retained]
        omission = copy.deepcopy(self.replay_omission)
        if removed:
            omission = dict(exchanges=len(removed), exchange_id_bounds=[removed[0], removed[-1]],
                history_search={'query':'', 'event':'exchange', 'first':removed[0], 'last':journal_head},
                meaning='Latest replacement: historical exchanges omitted from replay, without a judgment of relevance or understanding. Earlier omitted history remains searchable; this search span may include retained exchanges.')
        return dict(previous_context_id=self.context_id,
                    groups=[g['id'] for g in groups], exchanges=len(groups),
                    kept_records=copy.deepcopy(selections), masked_groups=sorted(masked),
                    account_record=account['record'], account_state=account['state'],
                    account_recovery=account.get('recovery_reason'),
                    account_read=self.account_read(account),
                    retained_evidence=self.retained_evidence(selections), replay_omission=omission,
                    reasoning_omission=reasoning_omission(groups, policy['reasoning_replay']),
                    input_limit_tokens=self.input_limit, total_limit_tokens=self.total_limit,
                    effective_input_limit_tokens=min(self.input_limit, self.total_limit-policy['output_tokens']),
                    **policy, tokens_per_byte=self.ratio,
                    estimation_method='serialized UTF-8 bytes × conservative calibrated ratio',
                    working_file=str(self.working), memory_root=str(self.memory),
                    runtime_sampled_at=sampled_at, journal_head_at_sampling=journal_head)

    def _render(self, groups, allowances, masked, candidate):
        rendered = []
        omitted_reasoning = set(candidate['reasoning_omission']['exchange_ids'])
        for group in groups:
            results = []
            for i, result in enumerate(group['results']):
                shown = copy.deepcopy(result)
                if group.get('fresh') and shown.get('context_change') in ('applied', 'unchanged'):
                    shown['applied_context'] = {key:candidate[key] for key in
                        ('groups', 'kept_records', 'account_record', 'account_state',
                         'account_read', 'retained_evidence', 'replay_omission',
                         'reasoning_replay', 'reasoning_omission',
                         'effective_input_limit_tokens', 'output_tokens', 'mask_at_tokens',
                         'pressure_at_tokens', 'keep_recent', 'preview_head_percent')}
                descriptor = shown.get('output')
                key = (group['id'], i)
                if descriptor and (group.get('fresh') or group['id'] in masked
                                   or group['id'] in self.masked or key in allowances):
                    descriptor['preview_head_percent'] = candidate['preview_head_percent']
                    allowance = allowances.get(key, math.ceil(candidate['effective_input_limit_tokens']/self.ratio))
                    if group['id'] in masked:
                        allowance = 0
                    shown['output'] = present(descriptor, allowance,
                        'historical_output_masked' if group['id'] in masked else
                        'request_capacity' if key in allowances else None)
                results.append(shown)
            messages = render_exchange(group['assistant'], results, group.get('notices', []))
            if group['id'] in omitted_reasoning:
                # render_exchange detached this copy. Keep the raw group reusable
                # when all is restored; never edit provider-native structured blocks.
                for field in PLAIN_REASONING_FIELDS:
                    if isinstance(messages[0].get(field), str):
                        del messages[0][field]
            rendered.append({'id':group['id'], 'messages':messages, 'results':results})
        return rendered

    def _assemble(self, system, task, schemas, model, account, evidence, rendered, runtime,
                  candidate, request_options, request_transform=None, user_messages=()):
        messages = [{'role':'system', 'content':system}, {'role':'user', 'content':task}]
        messages += [{'role':'user', 'content':text} for text in user_messages]
        messages += evidence
        for group in rendered:
            messages += copy.deepcopy(group['messages'])
        # Keep the complete candidate in the journal and control acknowledgment.
        # Reuse the existing tail carrier after native pairs: this global state
        # is supplied by the harness, not produced by the tool carrying it.
        context = {key:copy.deepcopy(candidate[key]) for key in
                   ('exchanges', 'effective_input_limit_tokens', 'output_tokens', 'replay_omission',
                    'reasoning_replay', 'reasoning_omission')}
        context.update(masked_exchanges=len(candidate['masked_groups']),
                       exact_evidence_selections=len(candidate['kept_records']))
        facts = {**runtime, 'kind':'fresh_global_state', 'sampled_at':candidate['runtime_sampled_at'],
                 'context':context, 'working_account':{**ACCOUNT_LABELS, **account}}
        for key in ('running_jobs', 'capture_failures'):
            if key in facts:
                facts[key] = [compact_process(job) for job in facts[key]]
        tool_tail = messages[-1]['role'] == 'tool'
        if not tool_tail:
            messages.append({'role':'user', 'content':''})
        tail = json.loads(messages[-1]['content']) if tool_tail else None
        def update_facts():
            if tool_tail:
                tail['harness_runtime'] = facts
                messages[-1]['content'] = dump(tail)
            else:
                messages[-1]['content'] = '<tool_response>CURRENT HARNESS FACTS\n'+dump(facts)+'</tool_response>'
        request = {**request_options, 'model':model, 'messages':messages, 'tools':schemas,
                   'tool_choice':'auto', 'max_tokens':candidate['output_tokens']}
        def project():
            return request_transform(request) if request_transform else request
        update_facts()
        estimate = self.tokens(project())
        remaining = candidate['effective_input_limit_tokens']-estimate
        facts['context'].update(estimated_tokens_before_telemetry=estimate,
            estimate_scope='compiled request before estimate, continuation-headroom fields, and pressure notice',
            continuation_headroom_estimate_tokens=remaining)
        if estimate >= candidate['pressure_at_tokens'] or remaining < candidate['output_tokens']:
            facts['notice'] = ('Context pressure: extract needed findings, correct working.md, retain essential exact evidence, '
                               'then use context_restart to replace completed exploration. Body masking alone retains old interpretations. '
                               'A long next response can consume remaining continuation headroom.')
        update_facts()
        # Keep the finalized canonical tail for exchange logging before protocol projection.
        if tool_tail:
            rendered[-1]['messages'][-1] = copy.deepcopy(messages[-1])
        return project()

    def _fit(self, system, task, schemas, model, account, selections, groups, runtime,
             policy, sampled_at, journal_head, request_options, request_transform=None, user_messages=()):
        allowances = {}
        ids = {g['id'] for g in groups}
        # Policy masks are reversible for surviving exchanges. Restore protected
        # recent views or disable masks without resurrecting replaced history.
        # The newest exchange stays protected even when keep_recent is zero.
        recent = max(1, policy['keep_recent'])
        protected = {g['id'] for g in groups[-recent:]}
        masked = (self.masked & ids) - protected if policy['mask_at_tokens'] else set()
        candidate = self._description(groups, selections, account, policy, sampled_at, journal_head, masked)
        evidence = self.evidence(selections, candidate['effective_input_limit_tokens'])
        def assemble():
            candidate['masked_groups'] = sorted(masked)
            rendered = self._render(groups, allowances, masked, candidate)
            request = self._assemble(system, task, schemas, model, account, evidence, rendered,
                                     runtime, candidate, request_options, request_transform, user_messages)
            return request, rendered
        request, rendered = assemble()
        if policy['mask_at_tokens'] and self.tokens(request) > policy['mask_at_tokens']:
            # Hard-cap paging below can still bound even a fresh observation.
            eligible = groups[:-recent]
            # Exact selections are independently included above; duplicate bodies remain eligible.
            masked.update(g['id'] for g in eligible)
            request, rendered = assemble()
        while self.tokens(request) > candidate['effective_input_limit_tokens']:
            candidates = []
            for position, group in enumerate(groups):
                for i, result in enumerate(group['results']):
                    if 'output' in result:
                        content = rendered[position]['results'][i]['output']
                        size = sum(len(s) for s in content['text_parts'])
                        if size:
                            candidates.append((size, (group['id'], i)))
            if not candidates:
                raise CapacityBlocked(f'Irreducible finalized input estimate {self.tokens(request)} exceeds '
                    f'effective capacity {candidate["effective_input_limit_tokens"]}; '
                    'original requirements, tool schemas, native history, or exact evidence cannot fit')
            size, key = max(candidates, key=lambda item:(item[0], -item[1][0], -item[1][1]))
            allowances[key] = size//2
            request, rendered = assemble()
        return request, rendered, candidate

    def compile(self, system, task, schemas, model, runtime, current=None, restart=None,
                account_snapshot=None, request_options=None, request_transform=None, user_messages=()):
        if not isinstance(user_messages, (list, tuple)) or any(not isinstance(text, str) for text in user_messages):
            raise ValueError("user_messages must contain explicit user-message text")
        account = self.account() if account_snapshot is None else account_snapshot
        sampled_at, journal_head = now(), len(self.store.events)
        previous = list(self.groups)
        configurations = current.get('configurations', []) if current else []
        controls = configurations + ([restart] if restart else [])
        proposed = {}
        for configuration in configurations:
            proposed.update(configuration['arguments'])
        def select(adopt):
            groups = previous + ([current] if current else [])
            selections = copy.deepcopy(self.records)
            if adopt and restart:
                n = restart['arguments'].get('keep_recent_exchanges', 0)
                groups = (previous[-n:] if n else []) + ([current] if current else [])
                selections = copy.deepcopy(restart['arguments'].get('keep_records', self.records))
            return groups, selections
        def fit_with_recovery(groups, selections, policy):
            nonlocal account
            options = [account]
            if account['state'] == 'current' and account['text'] and self.last_account:
                options.append(self.fallback('Current account does not fit the candidate context', account['record']))
            if any(a['text'] for a in options):
                options.append({'state':'absent', 'text':'', 'record':None, 'path':str(self.working),
                    'recovery_reason':'Neither the current account nor the previous snapshot fits this context. '
                                      'Neither account was injected; repair the working file through ordinary tools.',
                    'omitted_account_records':list(dict.fromkeys(a['record'] for a in options if a['record'] is not None))})
            for option in options:
                try:
                    fit = self._fit(system, task, schemas, model, option, selections, groups, runtime,
                                    policy, sampled_at, journal_head, request_options or {}, request_transform, user_messages)
                    account = option
                    return fit
                except CapacityBlocked as exc:
                    failure = exc
            raise failure
        rejected = None
        groups, selections = select(True)
        for control in controls:
            current['results'][control['index']].update(status='completed', context_change='applied')
        try:
            policy = self._validate_config(proposed) if controls else self.policy()
            request, rendered, candidate = fit_with_recovery(groups, selections, policy)
        except (CapacityBlocked, ValueError) as exc:
            if not controls:
                raise
            rejected = str(exc)
            for control in controls:
                current['results'][control['index']].update(status='rejected', context_change='unchanged',
                    error=rejected, ordinary_effects='Sibling ordinary effects remain as recorded; no actions were replayed or rolled back')
            groups, selections = select(False)
            policy = self.policy()
            request, rendered, candidate = fit_with_recovery(groups, selections, policy)
        # Persist exactly the finalized body before committing active context or allowing another call.
        serialized = dump(request)
        measured = self.tokens(request)
        if measured > candidate['effective_input_limit_tokens']:
            raise CapacityBlocked('Finalized request exceeds effective capacity')
        request_blob = self.store.blob(serialized)
        for control in controls:
            event = 'context_restart' if control is restart else 'context_configuration'
            self.store.add(event, applied=rejected is None, error=rejected,
                           arguments=control['arguments'], applied_context=candidate)
        committed = []
        previous_omitted = (set(self.store.record(self.context_id)['reasoning_omission']['exchange_ids'])
                            if self.context_id is not None else set())
        omitted = set(candidate['reasoning_omission']['exchange_ids'])
        for delivered, representation in zip(groups, rendered):
            results = representation['results']
            reasoning_changed = (delivered['id'] in previous_omitted) != (delivered['id'] in omitted)
            if results != delivered['results'] or delivered.get('fresh') or reasoning_changed:
                messages = representation['messages']
                self.store.add('exchange' if current and representation['id']==current['id'] else
                               'exchange_representation', exchange_id=representation['id'], messages=messages,
                               reasoning_replay=policy['reasoning_replay'],
                               omitted_reasoning_fields=([field for field in PLAIN_REASONING_FIELDS
                                   if isinstance(delivered['assistant'].get(field), str)]
                                   if delivered['id'] in omitted else []),
                               **({'protocol':'responses'} if 'responses_output' in delivered['assistant'] else {}))
            updated = {**delivered, 'results':results, 'fresh':False}
            for result in results:
                result.pop('harness_runtime', None)
                if 'output' in result:
                    output = result['output']
                    # Policy masking must retain the pre-mask display budget for
                    # immutable restoration. Capacity paging remains a bound.
                    if delivered['id'] not in candidate['masked_groups']:
                        output['output_chars'] = sum(len(s) for s in output['text_parts'])
            committed.append(updated)
        identity = self.store.add('context_manifest', **candidate, estimated_tokens=measured,
            estimate_scope='finalized compiled request', bytes=len(serialized.encode()), request_blob=request_blob)
        for old, updated in zip(groups, committed):
            old.update(updated)
        self.groups, self.records, self.masked = groups, selections, set(candidate['masked_groups'])
        self.replay_omission = copy.deepcopy(candidate['replay_omission'])
        for key, attribute in (('mask_at_tokens','mask_at'), ('pressure_at_tokens','pressure_at'),
                               ('output_tokens','output_tokens'), ('keep_recent','keep_recent'),
                               ('preview_head_percent','preview_head_percent'),
                               ('reasoning_replay','reasoning_replay')):
            setattr(self, attribute, policy[key])
        if account['state'] == 'current':
            self.last_account = account
        self.context_id, self.estimate = identity, measured
        return request
