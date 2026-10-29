"""Matched serving sweep: natural completion or explicitly fixed-work diagnostic; no tools execute."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
from pathlib import Path
import statistics
from threading import Event, Lock
import time
import urllib.parse
import uuid

from iteration import write, digest


def validate(config, cases=None):
    if set(config) - {'concurrencies', 'repetitions', 'fixed_decode_tokens', 'conversation_schedule', 'cell_schedule', 'complete_length_policy'}:
        raise ValueError('Unknown workload_sweep setting')
    length_policy = config.get('complete_length_policy', 'stop')
    if length_policy not in ('stop', 'retain_and_continue'):
        raise ValueError('Unknown complete_length_policy')
    if length_policy == 'retain_and_continue' and ('conversation_schedule' not in config or 'fixed_decode_tokens' in config):
        raise ValueError('Length continuation requires captured conversations without fixed decoding')
    cells = config.get('cell_schedule')
    if 'cell_schedule' in config:
        if 'concurrencies' in config or 'repetitions' in config:
            raise ValueError('Explicit cells cannot also specify automatic concurrency/repetition sweep')
        if not isinstance(cells, list) or not cells:
            raise ValueError('Nonempty explicit cell_schedule required')
        for cell in cells:
            if not isinstance(cell, dict) or set(cell) != {'concurrency', 'reset_cache'}:
                raise ValueError('Cell requires concurrency and reset_cache')
            if type(cell['concurrency']) is not int or cell['concurrency'] < 1 or type(cell['reset_cache']) is not bool:
                raise ValueError('Cell concurrency must be positive and reset_cache must be boolean')
        levels = list(dict.fromkeys(cell['concurrency'] for cell in cells))
    else:
        levels = config.get('concurrencies')
        if not isinstance(levels, list) or not levels or len(set(levels)) != len(levels) or any(type(n) is not int or n < 1 for n in levels):
            raise ValueError('Distinct positive concurrency levels required')
        if type(config.get('repetitions', 2)) is not int or config.get('repetitions', 2) < 1:
            raise ValueError('Positive workload_sweep repetitions required')
    if 'fixed_decode_tokens' in config and (type(config['fixed_decode_tokens']) is not int or config['fixed_decode_tokens'] < 1):
        raise ValueError('Positive integer fixed_decode_tokens required for the explicit diagnostic')
    if 'conversation_schedule' in config:
        schedule = config['conversation_schedule']
        if not isinstance(schedule, list) or not schedule:
            raise ValueError('Nonempty conversation_schedule required')
        names, covered = [], []
        def delay(value):
            return type(value) in (int, float) and math.isfinite(value) and value >= 0
        for conversation in schedule:
            if not isinstance(conversation, dict) or set(conversation) != {'id', 'initial_delay_s', 'terminal_delay_s', 'steps'}:
                raise ValueError('Conversation requires id, initial_delay_s, terminal_delay_s and steps')
            if not isinstance(conversation['id'], str) or not conversation['id'].strip():
                raise ValueError('Nonempty conversation id required')
            names.append(conversation['id'])
            if not all(delay(conversation[field]) for field in ('initial_delay_s', 'terminal_delay_s')):
                raise ValueError('Conversation delays must be finite nonnegative numbers')
            steps = conversation['steps']
            if not isinstance(steps, list) or not steps:
                raise ValueError('Nonempty ordered conversation steps required')
            for index, step in enumerate(steps):
                if not isinstance(step, dict) or set(step) != {'case', 'delay_after_previous_s'}:
                    raise ValueError('Step requires case and delay_after_previous_s')
                if not isinstance(step['case'], str) or not step['case'] or not delay(step['delay_after_previous_s']):
                    raise ValueError('Step requires a case id and finite nonnegative delay')
                if index == 0 and step['delay_after_previous_s'] != 0:
                    raise ValueError('First step delay must be zero; use initial_delay_s')
                covered.append(step['case'])
        if len(set(names)) != len(names) or len(set(covered)) != len(covered):
            raise ValueError('Conversation ids and scheduled cases must be unique')
        if cases is not None and set(covered) != {case['name'] for case in cases}:
            raise ValueError('Conversation schedule must cover every frozen case exactly once')
    return levels, config.get('repetitions', 2)


def validate_workload(root, frozen, model):
    """Read-only preflight, also called before the replay entrypoint contacts its endpoint."""
    cases = frozen['cases']
    if not cases or len({c['name'] for c in cases}) != len(cases):
        raise ValueError('Distinct nonempty workload cases required')
    result = validate(frozen['replay']['workload_sweep'], cases)
    for case in cases:
        source = json.loads((root / 'workload' / case['file']).read_text())
        if source.get('model') != model or source.get('n', 1) != 1:
            raise ValueError('Every source request must target the served model with one completion')
    return result


def response_validity(message, finish, request):
    """Structural validity is observable; it is not semantic correctness or a repair grade."""
    if finish not in ('stop', 'tool_calls', 'function_call'):
        return {'valid': False, 'reason': 'Non-natural or missing finish reason: ' + str(finish)}
    declared = {t.get('function', {}).get('name') for t in request.get('tools', [])}
    calls = message.get('tool_calls', [])
    if finish == 'tool_calls' and not calls:
        return {'valid': False, 'reason': 'Tool-call finish without tools'}
    for call in calls:
        function = call.get('function') or {}
        if function.get('name') not in declared:
            return {'valid': False, 'reason': 'Undeclared tool name'}
        try:
            if not isinstance(json.loads(function.get('arguments', '')), dict):
                raise ValueError('Arguments must be an object')
        except (ValueError, TypeError):
            return {'valid': False, 'reason': 'Incomplete or non-object tool arguments'}
    return {'valid': True, 'claim': 'Natural finish and declared tools with complete JSON object arguments; semantic correctness unmeasured'}



def fixed_work_validity(finish, usage, count):
    # Check exact measured work and retain the reported finish without inferring its cause.
    exact = type((usage or {}).get('completion_tokens')) is int and usage['completion_tokens'] == count
    valid = exact and finish in ('length', 'stop', 'tool_calls', 'function_call')
    return {'valid': valid, 'kind': 'fixed-generated-work-diagnostic', 'expected_completion_tokens': count,
            'observed_completion_tokens': (usage or {}).get('completion_tokens'),
            'finish_classification': 'length' if finish == 'length' else 'reported non-length finish with exact measured token count' if valid else 'invalid or mismatched fixed work',
            'claim': 'Only exact generated-token work and completed transport are checked. Partial text/tools are not actions or a natural-completion/semantic-quality result.'}


def completion_metrics(rows):
    """Keep finished service work separate from natural structural validity."""
    result = {'workload_completed_requests': sum(not r.get('error') for r in rows),
              'transport_completed_requests': sum(r.get('transport_complete', False) for r in rows),
              'structurally_valid_responses': sum(r.get('generation_mode') == 'natural' and
                  (r.get('response_validity') or {}).get('valid', False) for r in rows),
              'structural_validity_unassessed_responses': sum(r.get('generation_mode') != 'natural' for r in rows),
              'complete_length_responses': sum(r.get('transport_complete', False) and r.get('finish_reason') == 'length' for r in rows),
              'continued_complete_length_responses': sum(r.get('continued_complete_length', False) for r in rows)}
    for field in ('prompt_tokens', 'completion_tokens'):
        values = [r['usage'].get(field) if isinstance(r.get('usage'), dict) else None for r in rows]
        known = [v for v in values if type(v) is int and v >= 0]
        result['known_' + field] = sum(known)
        result['unknown_' + field + '_requests'] = len(values)-len(known)
        result[field] = sum(known) if len(known) == len(values) else None
    return result


def run(args, root, frozen, out, key, client):
    levels, repetitions = validate_workload(root, frozen, args.model)
    fixed_tokens = frozen['replay']['workload_sweep'].get('fixed_decode_tokens')
    generation_mode = 'fixed-generated-work' if fixed_tokens is not None else 'natural'
    cases = frozen['cases']
    conversations = frozen['replay']['workload_sweep'].get('conversation_schedule')
    length_policy = frozen['replay']['workload_sweep'].get('complete_length_policy', 'stop')
    rows, batches, resets = [], [], []
    endpoint = urllib.parse.urlsplit(client.base)
    def request(case, phase, repetition, concurrency, position, enqueued, dispatch=None, failure=None, dependency=None):
        original = json.loads((root / 'workload' / case['file']).read_text())
        if original.get('model') != args.model:
            raise ValueError('Source request model differs from served model')
        payload = dict(original)
        # Natural mode changes only transport; the diagnostic records its explicit decode overrides.
        overrides = {'set': {}, 'removed': {}}
        if fixed_tokens is not None:
            overrides['set'] = {'max_tokens': fixed_tokens, 'min_tokens': fixed_tokens, 'ignore_eos': True}
            for field in ('stop', 'max_completion_tokens', 'max_output_tokens', 'stop_token_ids'):
                if field in payload: overrides['removed'][field] = payload.pop(field)
            payload.update(overrides['set'])
        payload.update(stream=True, stream_options=dict(original.get('stream_options') or {}, include_usage=True), rid=uuid.uuid4().hex)
        name = f'{case["name"]}-{phase}-r{repetition}-c{concurrency}'
        if dispatch is None:
            write(out / (name + '.request.json'), payload)
        start = time.monotonic()
        row = {'case': case['name'], 'phase': phase, 'repetition': repetition, 'concurrency': concurrency,
               'generation_mode': generation_mode, 'fixed_decode_tokens': fixed_tokens, 'decoding_overrides': overrides,
               'position': position, 'source_request_sha256': case['request_sha256'],
               'queued_at_monotonic': enqueued, 'started_at': datetime.now(timezone.utc).isoformat(),
               'queue_s': start - enqueued, 'source_decoding_options': {k: v for k, v in original.items() if k not in ('messages', 'tools')},
               'payload_sha256': digest(json.dumps(payload, sort_keys=True).encode()), 'transport_changes': ['stream=true', 'stream_options.include_usage=true', 'rid']}
        row['complete_length_policy'] = length_policy
        if dependency is not None: row.update(dependency)
        conn = (http.client.HTTPSConnection if endpoint.scheme == 'https' else http.client.HTTPConnection)(endpoint.hostname, endpoint.port, timeout=30)
        message = {'role': 'assistant', 'content': '', 'reasoning_content': ''}; tools = {}; usage = None; finish = None; done = False; stream_errors = []
        first = last = None; max_gap = 0.; stream_hash = hashlib.sha256(); suppressed = False; body_complete = False
        try:
            conn.connect()
            # No inference-duration cap. A separately bounded campaign owns resource shutdown.
            conn.sock.settimeout(None)
            def send(admitted_at=None):
                if admitted_at is not None: row['http_admitted_at_monotonic'] = admitted_at
                if dispatch is not None: write(out / (name + '.request.json'), payload)
                row['http_dispatched_at_monotonic'] = time.monotonic()
                conn.request('POST', '/v1/chat/completions', json.dumps(payload).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
            if dispatch is None:
                send()
            elif not dispatch(send):
                suppressed = True
                return None  # Another conversation failed before this request was sent.
            response = conn.getresponse(); row['status'] = response.status
            with (out / (name + '.response.sse')).open('wb') as archive:
                for line in response:
                    archive.write(line); stream_hash.update(line)
                    if not line.startswith(b'data:'):
                        continue
                    body = line[5:].strip()
                    if body == b'[DONE]':
                        done = True; continue
                    value = json.loads(body)
                    if value.get('error') is not None:
                        stream_errors.append(value)
                        continue  # Retain the complete stream through DONE before rejecting it.
                    usage = value.get('usage') or usage
                    for choice in value.get('choices', []):
                        if choice.get('index', 0) != 0:
                            raise ValueError('Workload expects one completion per request')
                        delta = choice.get('delta') or {}
                        if any(delta.get(k) for k in ('content', 'reasoning_content', 'tool_calls')):
                            now = time.monotonic()
                            if first is None: first = now - start
                            if last is not None: max_gap = max(max_gap, now - last)
                            last = now
                        for field in ('content', 'reasoning_content'):
                            message[field] += delta.get(field) or ''
                        for call in delta.get('tool_calls') or []:
                            tool = tools.setdefault(call.get('index', 0), {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                            if call.get('id'): tool['id'] = call['id']
                            for field in ('name', 'arguments'):
                                tool['function'][field] += (call.get('function') or {}).get(field) or ''
                        finish = choice.get('finish_reason') or finish
            body_complete = True
            if tools: message['tool_calls'] = [tools[i] for i in sorted(tools)]
            validity = fixed_work_validity(finish, usage, fixed_tokens) if fixed_tokens is not None else response_validity(message, finish, original)
            if stream_errors:
                validity = dict(validity, valid=False, reason='Server reported an SSE error', generation_check_valid_before_stream_error=validity['valid'])
            row['response_validity'] = validity
            valid_usage = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('prompt_tokens', 'completion_tokens'))
            continued_length = (length_policy == 'retain_and_continue' and finish == 'length'
                                and response.status == 200 and done and not stream_errors and valid_usage)
            row['continued_complete_length'] = continued_length
            if response.status != 200 or not done or not (validity['valid'] or continued_length):
                raise ValueError('Failed/incomplete/invalid response: ' + json.dumps(validity))
            if not valid_usage:
                raise ValueError('Missing measured usage')
        except Exception as error:
            row['error'] = type(error).__name__ + ': ' + str(error)
            if failure is not None: failure(case['name'])
            if body_complete and row.get('status') == 200 and done and not stream_errors and finish == 'length':
                row['cancellation'] = {'requested': False, 'reason': 'Complete length response already ended'}
            else:
                try:
                    status, body = client.call('/abort_request', {'rid': payload['rid']}, method='POST')
                    row['cancellation'] = {'status': status, 'body': body.decode(errors='replace'), 'settlement_verified': False}
                except Exception as failure:
                    row['cancellation'] = {'error': type(failure).__name__, 'settlement_verified': False}
        finally:
            conn.close()
            completed_at = time.monotonic()
            if not suppressed:
                # A connect failure still retains the exact intended request.
                if dispatch is not None and not (out / (name + '.request.json')).exists():
                    write(out / (name + '.request.json'), payload)
                row.update(elapsed_s=completed_at - start, finished_at_monotonic=completed_at, first_generated_chunk_s=first,
                       max_generated_chunk_gap_s=max_gap, usage=usage, finish_reason=finish, stream_complete=done, stream_errors=stream_errors,
                       transport_complete=bool(body_complete and row.get('status') == 200 and done and not stream_errors),
                       response_sse_sha256=stream_hash.hexdigest(), finished_at=datetime.now(timezone.utc).isoformat())
                write(out / (name + '.response.json'), {'message': message, 'finish_reason': finish, 'usage': usage, 'generation_mode': generation_mode, 'fixed_decode_tokens': fixed_tokens, 'stream_errors': stream_errors})
                # Ignore transport IDs when comparing output identities across concurrency settings.
                normalized = json.loads(json.dumps(message))
                for call in normalized.get('tool_calls', []): call.pop('id', None)
                row['response_content_sha256'] = digest(json.dumps(normalized, sort_keys=True).encode())
                write(out / (name + '.receipt.json'), row)
        return row

    def batch(concurrency, repetition, phase, order):
        started = time.monotonic()
        started_at_unix = time.time()
        # All jobs have the same ready time. This measures saturation, not tool-wait simulation.
        current = []; remaining = iter(enumerate(order)); stopped = False
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            pending = set()
            def admit():
                try: pos, i = next(remaining)
                except StopIteration: return False
                pending.add(pool.submit(request, cases[i], phase, repetition, concurrency, pos, started))
                return True
            for _ in range(min(concurrency, len(order))): admit()
            while pending:
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                completed = [future.result() for future in finished]
                current.extend(completed)
                stopped = stopped or any(row.get('error') for row in completed)
                if not stopped:
                    for _ in completed: admit()
        current.sort(key=lambda row: row['position'])
        rows.extend(current)
        elapsed = time.monotonic() - started
        values = [r['elapsed_s'] for r in current]
        sample = {'concurrency': concurrency, 'repetition': repetition, 'phase': phase,
                  'generation_mode': generation_mode, 'fixed_decode_tokens': fixed_tokens,
                  'started_at_unix': started_at_unix, 'finished_at_unix': time.time(),
                  'started_at': datetime.fromtimestamp(started_at_unix, timezone.utc).isoformat(),
                  'finished_at': datetime.now(timezone.utc).isoformat(), 'case_order': [cases[i]['name'] for i in order],
                  'elapsed_s': elapsed, 'requests': len(current), 'unsent_cases': [cases[i]['name'] for _, i in remaining],
                  'passed': len(current) == len(order) and all(not r.get('error') for r in current),
                  'request_latency_median_s': statistics.median(values), 'request_latency_max_s': max(values),
                  **completion_metrics(current),
                  'queue_seconds_sum': sum(r['queue_s'] for r in current)}
        batches.append(sample); write(out / 'batch-samples.json', batches); write(out / 'receipts.json', rows)
        if not sample['passed']:
            raise ValueError('Workload has failed or uncertain responses; refuse further cache reset/inference')

    def conversation_batch(concurrency, repetition, phase, order):
        started = time.monotonic(); started_unix = time.time()
        stopped = Event(); dispatch_lock = Lock(); progress_lock = Lock()
        current, failure_evidence = [], {}
        by_name = {case['name']: case for case in cases}
        case_order = [step['case'] for i in order for step in conversations[i]['steps']]
        positions = {name: i for i, name in enumerate(case_order)}
        records = [{'conversation_id': conversations[i]['id'], 'state': 'not_admitted',
                    'cases': [step['case'] for step in conversations[i]['steps']],
                    'completed_cases': [], 'observed_cases': []} for i in order]

        def fail(case):
            # Serialize failure with admission, not socket writes. Already admitted
            # calls are in flight; blocking uploads must not hold the shared lock.
            with dispatch_lock:
                if not stopped.is_set():
                    failure_evidence.update(case=case, detected_at_monotonic=time.monotonic())
                    stopped.set()

        def dispatch(send):
            with dispatch_lock:
                if stopped.is_set(): return False
                admitted_at = time.monotonic()
            send(admitted_at)
            return True

        def record(index, **fields):
            with progress_lock:
                records[index].update(fields)
                write(out / f'conversations-{phase}-r{repetition}-c{concurrency}.json', records)

        def wait_until(ready):
            return not stopped.wait(max(0., ready - time.monotonic()))

        def run_conversation(index, specification):
            with dispatch_lock:
                if stopped.is_set(): return
                admitted = time.monotonic(); admitted_unix = time.time()
            record(index, state='active', admitted_at_monotonic=admitted,
                   admitted_at_unix=admitted_unix, initial_delay_s=specification['initial_delay_s'],
                   terminal_delay_s=specification['terminal_delay_s'])
            ready = admitted + specification['initial_delay_s']
            observed, completed = [], []
            previous = None
            for step_index, step in enumerate(specification['steps']):
                if previous is not None:
                    ready = previous['finished_at_monotonic'] + step['delay_after_previous_s']
                if not wait_until(ready): break
                row = request(by_name[step['case']], phase, repetition, concurrency,
                              positions[step['case']], ready, dispatch, fail,
                              {'conversation_id': specification['id'], 'conversation_step': step_index,
                               'dependency_ready_at_monotonic': ready,
                               'recorded_delay_s': specification['initial_delay_s'] if step_index == 0 else step['delay_after_previous_s'],
                               'predecessor_case': None if previous is None else previous['case']})
                if row is None: break
                with progress_lock: current.append(row)
                observed.append(row['case'])
                if not row.get('error'): completed.append(row['case'])
                record(index, observed_cases=list(observed), completed_cases=list(completed))
                if row.get('error'): break
                previous = row
            complete = len(completed) == len(specification['steps'])
            if complete:
                terminal_ready = previous['finished_at_monotonic'] + specification['terminal_delay_s']
                record(index, state='terminal_delay', last_response_finished_at_monotonic=previous['finished_at_monotonic'],
                       terminal_ready_at_monotonic=terminal_ready)
                complete = wait_until(terminal_ready)
            finished = time.monotonic()
            record(index, state='completed' if complete else 'failed' if any(r.get('error') for r in current if r['conversation_id'] == specification['id']) else 'interrupted',
                   finished_at_monotonic=finished, finished_at_unix=time.time(),
                   slot_elapsed_s=finished-admitted,
                   unstarted_cases=[step['case'] for step in specification['steps'] if step['case'] not in observed])

        def conversation(index, specification):
            try:
                run_conversation(index, specification)
            except BaseException:
                fail('conversation:' + specification['id'])
                raise

        try:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                pending = [pool.submit(conversation, index, conversations[i]) for index, i in enumerate(order)]
                for future in pending: future.result()
        finally:
            current.sort(key=lambda row: row['position']); rows.extend(current)
            values = [r['elapsed_s'] for r in current]; observed = {r['case'] for r in current}
            for record_row in records:
                record_row['unstarted_cases'] = [name for name in record_row['cases'] if name not in record_row['observed_cases']]
                if record_row['state'] in ('active', 'terminal_delay'):
                    record_row['state'] = 'completion_unconfirmed'
            sample = {'concurrency': concurrency, 'repetition': repetition, 'phase': phase,
                      'generation_mode': generation_mode, 'fixed_decode_tokens': fixed_tokens,
                      'scheduling_mode': 'fixed-history-arrival-simulation',
                      'started_at_unix': started_unix, 'finished_at_unix': time.time(),
                      'started_at': datetime.fromtimestamp(started_unix, timezone.utc).isoformat(),
                      'finished_at': datetime.now(timezone.utc).isoformat(), 'case_order': case_order,
                      'conversation_order': [conversations[i]['id'] for i in order], 'conversations': records,
                      'failure': failure_evidence or None, 'elapsed_s': time.monotonic()-started,
                      'requests': len(current), 'unsent_cases': [name for name in case_order if name not in observed],
                      'passed': len(current) == len(case_order) and all(r['state'] == 'completed' for r in records) and not stopped.is_set(),
                      'request_latency_median_s': statistics.median(values) if values else None,
                      'request_latency_max_s': max(values) if values else None,
                      **completion_metrics(current),
                      'queue_seconds_sum': sum(r['queue_s'] for r in current)}
            batches.append(sample)
            write(out / f'conversations-{phase}-r{repetition}-c{concurrency}.json', records)
            write(out / 'batch-samples.json', batches); write(out / 'receipts.json', rows)
        if not sample['passed']:
            raise ValueError('Workload has failed or uncertain responses; refuse further cache reset/inference')

    explicit_cells = frozen['replay']['workload_sweep'].get('cell_schedule')
    cell_progress = []
    if explicit_cells is not None:
        # Publish every cell before any endpoint mutation. A later failure keeps
        # all not-yet-entered work visible without resetting or retrying it.
        cell_progress = [dict(cell_index=i, **cell, state='unstarted',
                              planned_cases=[case['name'] for case in cases])
                         for i, cell in enumerate(explicit_cells)]
        write(out / 'cell-progress.json', cell_progress)
    try:
        if explicit_cells is not None:
            count = len(conversations) if conversations is not None else len(cases)
            order = list(range(count))
            for index, cell in enumerate(explicit_cells):
                current_cell = cell_progress[index]
                current_cell['state'] = 'running'
                write(out / 'cell-progress.json', cell_progress)
                try:
                    if cell['reset_cache']:
                        status, body = client.call('/flush_cache', method='POST')
                        resets.append({'cell_index': index, 'concurrency': cell['concurrency'],
                                       'status': status, 'body': body.decode(errors='replace')})
                        write(out / 'cache-resets.json', resets)
                        if status != 200:
                            raise ValueError('Cache reset failed; refusing declared cell')
                    run_batch = conversation_batch if conversations is not None else batch
                    # Cell index replaces automatic repetition only as a unique
                    # receipt identity; no implicit warm phase or order reversal.
                    run_batch(cell['concurrency'], index, 'cell', order)
                    current_cell['state'] = 'completed'
                except BaseException as error:
                    current_cell.update(state='failed', error=type(error).__name__ + ': ' + str(error))
                    raise
                finally:
                    write(out / 'cell-progress.json', cell_progress)
        else:
            for repetition in range(repetitions):
                # Counterbalance operating point order; workload order is matched within a repetition.
                operating_points = levels if repetition % 2 == 0 else list(reversed(levels))
                count = len(conversations) if conversations is not None else len(cases)
                # A dependency-mode reversal changes task order, never the steps inside a task.
                order = list(range(count)) if repetition % 2 == 0 else list(reversed(range(count)))
                for concurrency in operating_points:
                    status, body = client.call('/flush_cache', method='POST')
                    resets.append({'repetition': repetition, 'concurrency': concurrency, 'status': status, 'body': body.decode(errors='replace')}); write(out / 'cache-resets.json', resets)
                    if status != 200: raise ValueError('Cache reset failed; refusing cold label')
                    run_batch = conversation_batch if conversations is not None else batch
                    run_batch(concurrency, repetition, 'cold', order)
                    run_batch(concurrency, repetition, 'warm', order)
        report = {'completed_at': datetime.now(timezone.utc).isoformat(), 'passed': True, 'requests': rows, 'batch_samples': batches,
                  'completion': completion_metrics(rows),
                  'configuration': {'kind': 'fixed-history-arrival-simulation' if conversations is not None else 'workload-saturation-sweep', 'generation_mode': generation_mode, 'fixed_decode_tokens': fixed_tokens, 'concurrencies': levels, 'repetitions': None if explicit_cells is not None else repetitions, 'cell_schedule': explicit_cells,
                                    'complete_length_policy': length_policy,
                                    'source_decoding_preserved': fixed_tokens is None, 'cases': [c['name'] for c in cases]},
                  'experiment_sha256': digest((root / 'experiment.json').read_bytes()),
                  'claims': ('Fixed captured histories and completion-relative observed delays; task slots remain occupied through simulated initial, inter-request and terminal work. New responses do not change successor histories and no tools/graders execute. This is not live work or a CPU-contention simulation; generated-output/cache differences remain measured confounders.' if conversations is not None else 'Matched fixed request count at every concurrency. Saturated serving replay only; no generated actions execute and no repair quality is inferred.'),
                  'cache': ('Explicit cells use exactly their declared reset_cache boundary and preserve source task/case order; no implicit phase. Process/compiler state is retained. A flush clears prefix cache, not all runtime state.' if explicit_cells is not None else 'Cold begins with successful whole-cache reset; requests may share prefixes within each batch. Warm repeats the same whole batch without reset. Warm does not mean all prefixes remain resident.'),
                  'correctness': ('Fixed-generated-work diagnostic with explicit decode overrides. Success verifies exact measured tokens and complete transport, not a natural completion, valid action or semantic correctness. No generated action executes.' if fixed_tokens is not None else 'Captured-history completion permits complete length responses under the explicit policy; those remain invalid natural completions. Transport/workload completion and structural validity are reported separately. No generated action executes or changes successors.' if length_policy == 'retain_and_continue' else 'Responses retain original SSE and reconstructed complete messages. Structural finish/tool-argument validation is not semantic correctness; compare output identity and graded repairs separately.')}
        write(out / 'summary.json', report)
        return report
    finally:
        write(out / 'receipts.json', rows)
        write(out / 'batch-samples.json', batches)
