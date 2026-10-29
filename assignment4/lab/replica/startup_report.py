"""Read-only fresh-host startup evidence; no provisioning, inference or cache operations."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile

from workload_replay import fixed_work_validity, response_validity, validate as validate_replay
from iteration import verify as verify_frozen


def sha(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(value):
    result = datetime.fromisoformat(value).timestamp() if isinstance(value, str) else value
    if type(result) not in (int, float) or not math.isfinite(result):
        raise ValueError('Missing or invalid timestamp')
    return result


class Archive:
    """Use verified regular members without extracting archive-controlled paths."""
    def __init__(self, run, role, events, retain=None):
        self.path = Path(run) / (role + '-evidence.tar.gz')
        receipts = [r for r in events if r.get('event') == 'evidence_collected' and r.get('role') == role]
        if len(receipts) != 1:
            raise ValueError('Need one stable collector receipt for ' + role)
        with self.path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != receipts[0]['sha256'] or self.path.stat().st_size != receipts[0]['bytes']:
            raise ValueError('Archive differs from collector receipt: ' + role)
        self.proof = {'path': str(self.path.resolve()), 'sha256': digest, 'bytes': self.path.stat().st_size}
        # A selected view needs one decompression pass, even when callers revisit
        # early members. Cache only requested bytes, never archive-controlled paths.
        self.archive = tarfile.open(self.path, 'r|*' if retain else 'r:*')
        self.cache = tempfile.TemporaryFile() if retain else None
        self.offsets = {}
        self.members = {}
        self.reads = {}
        try:
            for member in self.archive:
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or member.name in self.members:
                    raise ValueError('Unsafe or duplicate archive member')
                self.members[member.name] = member
                if retain and member.isfile() and retain(member.name):
                    offset = self.cache.tell()
                    with self.archive.extractfile(member) as stream:
                        shutil.copyfileobj(stream, self.cache, 1024 * 1024)
                    if self.cache.tell() - offset != member.size:
                        raise ValueError('Truncated retained archive member')
                    self.offsets[member.name] = (offset, member.size)
        except BaseException:
            self.close()
            raise

    def read(self, name):
        member = self.members.get(name)
        if member is None:
            return None
        if not member.isfile():
            raise ValueError('Evidence must be a regular archive member: ' + name)
        if self.cache is not None:
            if name not in self.offsets:
                raise ValueError('Member was not selected for this archive view: ' + name)
            offset, size = self.offsets[name]
            self.cache.seek(offset); data = self.cache.read(size)
        else:
            data = self.archive.extractfile(member).read()
        self.reads[name] = {'sha256': sha(data), 'bytes': len(data)}
        return data

    def json(self, name):
        data = self.read(name)
        return json.loads(data) if data is not None else None

    def lines(self, name):
        data = self.read(name)
        return [json.loads(line) for line in data.splitlines() if line.strip()] if data is not None else []

    def close(self):
        self.archive.close()
        if self.cache is not None: self.cache.close()


def canonical_request(request):
    # Only the owned cancellation identifier is non-semantic. Retain decoding,
    # tools, streaming and all user/model messages in the comparison identity.
    return sha(json.dumps({k: v for k, v in request.items() if k != 'rid'}, sort_keys=True).encode())


def usage_ok(usage):
    return isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                           for k in ('prompt_tokens', 'completion_tokens'))


def parse_sse(raw):
    message = {'role': 'assistant', 'content': '', 'reasoning_content': ''}
    tools = {}; usage = None; finish = None; done = False; models = set()
    for line in raw.splitlines():
        if not line.startswith(b'data:'):
            continue
        body = line[5:].strip()
        if done:
            raise ValueError('Data after SSE completion')
        if body == b'[DONE]':
            done = True; continue
        value = json.loads(body)
        if value.get('error') is not None:
            raise ValueError('Provider error in complete stream')
        if value.get('model'):
            models.add(value['model'])
        usage = value.get('usage') or usage
        for choice in value.get('choices', []):
            if choice.get('index', 0) != 0:
                raise ValueError('Multiple completion indices')
            delta = choice.get('delta') or {}
            for field in ('content', 'reasoning_content'):
                message[field] += delta.get(field) or ''
            for call in delta.get('tool_calls') or []:
                tool = tools.setdefault(call.get('index', 0), {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                if call.get('id'):
                    tool['id'] = call['id']
                for field in ('name', 'arguments'):
                    tool['function'][field] += (call.get('function') or {}).get(field) or ''
            if choice.get('finish_reason') is not None:
                if finish is not None:
                    raise ValueError('Multiple terminal finish frames')
                finish = choice['finish_reason']
    if tools:
        message['tool_calls'] = [tools[i] for i in sorted(tools)]
    if not done:
        raise ValueError('Missing SSE DONE')
    return message, finish, usage, models


def validate_response(receipt, request_raw, response_raw, kind, *, complete_length_policy='stop'):
    """Validate original bytes, not just a successful-looking receipt field."""
    if complete_length_policy not in ('stop', 'retain_and_continue'):
        raise ValueError('Unknown complete-length verification policy')
    request = json.loads(request_raw)
    if receipt.get('status') != 200 or receipt.get('error') or receipt.get('client_disconnected'):
        raise ValueError('Failed or disconnected HTTP exchange')
    expected_model = request.get('model')
    if not isinstance(expected_model, str) or not expected_model:
        raise ValueError('Request model identity missing')
    if kind == 'native':
        if receipt.get('state') != 'completed' or sha(request_raw) != receipt.get('native_request_sha256'):
            raise ValueError('Native request identity/state mismatch')
        response = json.loads(response_raw)
        if response.get('error') or response.get('model') != expected_model:
            raise ValueError('Native response error/model mismatch')
        choices = response.get('choices')
        if not isinstance(choices, list) or len(choices) != 1 or choices[0].get('index', 0) != 0:
            raise ValueError('Need one complete native choice')
        message = choices[0].get('message') or {}; finish = choices[0].get('finish_reason'); usage = response.get('usage')
        validity = response_validity(message, finish, request)
        mode = 'natural'
    else:
        if sha(json.dumps(request, sort_keys=True).encode()) != receipt.get('payload_sha256'):
            raise ValueError('Replay payload identity mismatch')
        if sha(response_raw) != receipt.get('response_sse_sha256'):
            raise ValueError('Replay SSE identity mismatch')
        message, finish, usage, models = parse_sse(response_raw)
        if models != {expected_model}:
            raise ValueError('Replay response model identity missing or different')
        mode = receipt.get('generation_mode', 'natural')
        if mode == 'fixed-generated-work':
            count = receipt.get('fixed_decode_tokens')
            if type(count) is not int or count < 1 or request.get('max_tokens') != count or request.get('min_tokens') != count or request.get('ignore_eos') is not True:
                raise ValueError('Fixed-work request policy mismatch')
            validity = fixed_work_validity(finish, usage, count)
        elif mode == 'natural':
            validity = response_validity(message, finish, request)
        else:
            raise ValueError('Unknown replay generation mode')
        if finish != receipt.get('finish_reason') or usage != receipt.get('usage'):
            raise ValueError('Stream disagrees with terminal receipt')
    retained_length = (kind == 'replay' and mode == 'natural' and finish == 'length'
        and complete_length_policy == 'retain_and_continue' and usage_ok(usage)
        and receipt.get('complete_length_policy') == complete_length_policy
        and receipt.get('continued_complete_length') is True
        and receipt.get('transport_complete') is True and receipt.get('stream_complete') is True
        and not receipt.get('stream_errors') and (receipt.get('response_validity') or {}).get('valid') is False)
    if not (validity['valid'] or retained_length) or not usage_ok(usage):
        raise ValueError('Incomplete/invalid response or missing measured usage')
    return {'request_sha256': sha(request_raw), 'semantic_request_sha256': canonical_request(request),
            'response_sha256': sha(response_raw), 'model': expected_model, 'finish_reason': finish,
            'usage': usage, 'generation_mode': mode, 'response_validation': validity,
            'transport_complete': True, 'retained_complete_length': retained_length,
            'natural_structural_validity': validity['valid'] if mode == 'natural' else None}


def first_workload_completion(cpu, job):
    prefix = 'jobs/' + job + '/'
    frozen = cpu.json(prefix + 'iteration/experiment.json') or {}
    sweep = (frozen.get('replay') or {}).get('workload_sweep') or {}
    length_policy = sweep.get('complete_length_policy', 'stop')
    if length_policy != 'stop':
        validate_replay(sweep, frozen.get('cases'))
    rows = []; excluded = {'local_preflight_receipts': 0, 'cancellation_probe_receipts': 0}
    probe_receipts = []
    for name in cpu.members:
        if not name.startswith(prefix) or not name.endswith('.receipt.json'):
            continue
        if '/native/runtime-preflight-' in name:
            excluded['local_preflight_receipts'] += 1; continue
        if '/native/serving-probe/' in name:
            excluded['cancellation_probe_receipts'] += 1
            probe = cpu.json(name)
            probe_receipts.append({'member': name, 'state': probe.get('state'),
                                   'elapsed_s': probe.get('elapsed_s'), 'status': probe.get('status'),
                                   'client_disconnected': probe.get('client_disconnected'),
                                   'interpretation': 'Intentional cancellation work retained; not a complete workload inference.'})
            continue
        if re.fullmatch(re.escape(prefix) + r'native/sweep/[^/]+/task-\d+/case-\d+/swe-agent/wire/\d+\.receipt\.json', name):
            kind = 'native'
        elif name.startswith(prefix + 'iteration-logs/replay/'):
            kind = 'replay'
        else:
            continue
        receipt = cpu.json(name)
        if receipt.get('finished_at_unix', receipt.get('finished_at')) is None:
            rows.append((math.inf, name, kind, receipt)); continue
        rows.append((timestamp(receipt.get('finished_at_unix', receipt.get('finished_at'))), name, kind, receipt))
    rejected = []
    for finished, name, kind, receipt in sorted(rows):
        if not math.isfinite(finished):
            rejected.append({'member': name, 'reason': 'No terminal timestamp'}); continue
        stem = name[:-len('.receipt.json')]
        try:
            request = cpu.read(stem + '.request.json')
            response = cpu.read(stem + ('.response.body' if kind == 'native' else '.response.sse'))
            if request is None or response is None:
                raise ValueError('Missing original request/response bytes')
            validated = validate_response(receipt, request, response, kind,
                                          complete_length_policy=length_policy if kind == 'replay' else 'stop')
            started = timestamp(receipt.get('started_at_unix', receipt.get('started_at')))
            if started > finished:
                raise ValueError('Response finished before request start')
            return {'first_validated_completion': {'member': name, 'kind': kind, 'started_at_unix': started,
                     'finished_at_unix': finished, 'http_elapsed_s': finished - started, **validated},
                    'earlier_unvalidated_terminal_receipts': rejected, 'excluded': excluded,
                    'intentional_probe_work': probe_receipts,
                    'eligible_receipt_count': len(rows)}
        except (ValueError, TypeError, KeyError) as error:
            rejected.append({'member': name, 'reason': str(error)})
    return {'first_validated_completion': None, 'earlier_unvalidated_terminal_receipts': rejected,
            'excluded': excluded, 'intentional_probe_work': probe_receipts, 'eligible_receipt_count': len(rows)}


def software_identity(runtime):
    if not runtime:
        return None
    result = {k: runtime.get(k) for k in ('python', 'torch', 'torch_cuda', 'device', 'packages', 'cuda_home')}
    result['compiler'] = runtime.get('compiler')
    gpu = runtime.get('gpu') or {}
    fields = [v.strip() for v in gpu.get('stdout', '').strip().split(',')]
    result['gpu_properties_except_unique_uuid'] = [fields[0], *fields[2:]] if gpu.get('exit_code') == 0 and len(fields) == 7 else None
    return result


SERVER_FIELDS = ('model_path', 'tokenizer_path', 'served_model_name', 'revision', 'dtype',
                 'quantization', 'kv_cache_dtype', 'context_length', 'mem_fraction_static',
                 'max_running_requests', 'max_total_tokens', 'max_total_num_tokens', 'max_req_input_len',
                 'max_prefill_tokens', 'chunked_prefill_size', 'prefill_decode_interval', 'page_size',
                 'schedule_policy', 'schedule_conservativeness', 'disable_radix_cache', 'enable_mixed_chunk',
                 'disable_overlap_schedule', 'enable_dynamic_chunking', 'random_seed', 'tp_size', 'dp_size',
                 'attention_backend', 'decode_attention_backend', 'prefill_attention_backend',
                 'moe_runner_backend', 'moe_a2a_backend', 'sampling_backend', 'disable_cuda_graph',
                 'cuda_graph_bs', 'cuda_graph_max_bs', 'enable_torch_compile', 'speculative_algorithm',
                 'tool_call_parser', 'reasoning_parser', 'version')


def server_identity(info):
    if not isinstance(info, dict):
        return None
    # Explicit allowlist excludes API keys, launch commands and unrelated paths.
    fields = lambda value: {k: value[k] for k in SERVER_FIELDS if k in value}
    states = info.get('internal_states')
    return {'top_level': fields(info),
            'internal_states': [fields(x) for x in states if isinstance(x, dict)] if isinstance(states, list) else None}


def identity_unknowns(job):
    result = []; runtime = job.get('runtime_software') or {}
    for field in ('python', 'torch', 'torch_cuda', 'device', 'cuda_home', 'gpu_properties_except_unique_uuid'):
        if not runtime.get(field):
            result.append('runtime.' + field)
    for package in ('sglang', 'flashinfer-python', 'sglang-kernel'):
        if not (runtime.get('packages') or {}).get(package):
            result.append('runtime.packages.' + package)
    compiler = runtime.get('compiler') or {}
    if compiler.get('exit_code') != 0 or not compiler.get('stdout'):
        result.append('runtime.compiler')
    server = job.get('observed_server_settings') or {}; top = server.get('top_level') or {}
    for field in ('model_path', 'served_model_name', 'context_length', 'mem_fraction_static',
                  'chunked_prefill_size', 'max_total_num_tokens', 'version'):
        if field not in top or top[field] is None:
            result.append('server.' + field)
    if not server.get('internal_states'):
        result.append('server.internal_states')
    else:
        for index, state in enumerate(server['internal_states']):
            for field in ('model_path', 'context_length', 'mem_fraction_static', 'chunked_prefill_size'):
                if field not in state or state[field] is None:
                    result.append(f'server.internal_states[{index}].' + field)
    return result


def analyze(run):
    run = Path(run).resolve(); local = {}
    def load(name):
        raw = (run / name).read_bytes(); local[name] = {'sha256': sha(raw), 'bytes': len(raw)}
        return [json.loads(x) for x in raw.splitlines() if x] if name.endswith('.jsonl') else json.loads(raw)
    events = load('controller-events.jsonl'); batch = load('batch.json'); resources = load('resources.json')
    allocation = timestamp(resources['created_at'])
    cpu = Archive(run, 'cpu', events); gpu = Archive(run, 'gpu', events)
    try:
        jobs = []
        for position, job in enumerate(batch['jobs']):
            root = Path(job['experiment']); manifest_raw = (root / 'experiment.json').read_bytes()
            if sha(manifest_raw) != job['manifest_sha256']:
                raise ValueError('Frozen manifest differs from launched identity')
            frozen = verify_frozen(root)
            invariant_names = [n for n in frozen['files'] if n.startswith(('course/', 'cohort/', 'workload/', 'native-comparison/'))]
            runtime = gpu.json('jobs/' + job['id'] + '/runtime.json')
            info_path = 'jobs/' + job['id'] + ('/native/server-info.json' if frozen['stages'] == ['native'] else '/iteration-logs/replay/server-info.json')
            server_info = cpu.json(info_path)
            log_raw = gpu.read('jobs/' + job['id'] + '/server.log')
            app_ready = None
            for line in (log_raw or b'').decode(errors='replace').splitlines():
                match = re.match(r'\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\].*Application startup complete', line)
                if match:
                    if app_ready is not None:
                        raise ValueError('Multiple server startups in one job log')
                    app_ready = timestamp(match[1].replace(' ', 'T') + '+00:00')
            completion = first_workload_completion(cpu, job['id'])
            first = completion['first_validated_completion']
            native = cpu.lines('jobs/' + job['id'] + '/native-events.jsonl')
            prep_start = next((timestamp(x['at']) for x in native if x.get('stage') == 'prepare-images' and x.get('state') == 'started'), None)
            prep_end = next((timestamp(x['at']) for x in native if x.get('stage') == 'waiting_for_model'), None)
            jobs.append({'job': job['id'], 'fresh_host_first_job': position == 0, 'manifest_sha256': sha(manifest_raw),
                         'model_requested': frozen['model'], 'submitted_flags': frozen['submitted_flags'],
                         'stages': frozen['stages'], 'matrix': frozen.get('matrix'), 'environment': frozen.get('environment'),
                         'invariant_files': {n: frozen['files'][n] for n in invariant_names},
                         'dockerfile_sha256': frozen['tooling_files'].get('Dockerfile.gpu'),
                         'tooling_files': frozen['tooling_files'], 'runtime_software': software_identity(runtime),
                         'observed_server_settings': server_identity(server_info),
                         'server_app_ready_at_unix_second_precision': app_ready, 'cpu_preparation_start_at_unix': prep_start,
                         'cpu_preparation_end_at_unix': prep_end,
                         'cpu_preparation_after_app_ready_s_approx': max(0, prep_end-app_ready) if prep_end is not None and app_ready is not None else None,
                         'allocation_to_app_ready_s_approx': app_ready-allocation if position == 0 and app_ready else None,
                         'allocation_to_first_validated_workload_completion_s': first['finished_at_unix']-allocation if position == 0 and first else None,
                         'workload_completion': completion})
        return {'run': str(run), 'generated_at': datetime.now(timezone.utc).isoformat(), 'allocation_at_unix': allocation,
                'instance_ids': {role: resources['resources'][role].get('id') for role in ('cpu', 'gpu')},
                'hosts': {role: {k: resources['resources'][role].get(k) for k in ('type', 'ami', 'disk_gb')}
                          for role in ('cpu', 'gpu')}, 'jobs': jobs,
                'gpu_bootstrap_stages': gpu.lines('evidence/gpu-bootstrap-stages.jsonl'),
                'evidence': {'local': local, 'archives': {'cpu': cpu.proof, 'gpu': gpu.proof},
                             'cpu_members_read': cpu.reads, 'gpu_members_read': gpu.reads},
                'limitations': ['First validated workload completion excludes local fixtures and deliberately cancelled lifecycle probes; it is not the first server warmup inference.',
                               'A diagnostic length finish is completed transport/fixed work, not a natural action or successful repair.',
                               'Missing or invalid earlier receipts remain explicit; no health-only or first-SSE milestone is substituted.',
                               'Runtime inventory covers reported packages/compiler/GPU properties, not a complete filesystem/software attestation. Requested checkpoint revision is not an independent weight-byte rehash.',
                               'UTC cross-host offsets were not independently calibrated; app-ready log precision is one second.',
                               'Later jobs reuse model/compiler/image storage and must not be called fresh-host starts.']}
    finally:
        cpu.close(); gpu.close()


def compare(control, candidate, allowed_tool_changes=(), allow_random_seed_difference=False):
    """Matching is necessary evidence, never a causal gain from one noisy pair."""
    left, right = control['jobs'][0], candidate['jobs'][0]
    mismatches = []; unknowns = []
    if control['run'] == candidate['run'] or any(control['instance_ids'].get(role) == candidate['instance_ids'].get(role) for role in ('cpu', 'gpu')):
        mismatches.append('same_run_or_reused_host')
    for label, job in [('control', left), ('candidate', right)]:
        unknowns.extend(label + '.' + name for name in identity_unknowns(job))
    for label, value in [('control', control), ('candidate', candidate)]:
        for role in ('cpu', 'gpu'):
            for field in ('type', 'ami', 'disk_gb'):
                if value.get('hosts', {}).get(role, {}).get(field) is None:
                    unknowns.append(label + '.' + role + '.' + field)
    seed_difference = None
    for field, a, b in [('hosts', control['hosts'], candidate['hosts']), *[(k, left.get(k), right.get(k)) for k in
                        ('model_requested', 'submitted_flags', 'stages', 'matrix', 'environment', 'invariant_files', 'dockerfile_sha256', 'runtime_software', 'observed_server_settings')]]:
        if field == 'observed_server_settings' and allow_random_seed_difference and a and b:
            def without_seed(value):
                return {name: ([{k: v for k, v in state.items() if k != 'random_seed'} for state in rows]
                               if isinstance(rows, list) else {k: v for k, v in rows.items() if k != 'random_seed'}
                               if isinstance(rows, dict) else rows) for name, rows in value.items()}
            pairs = [(a.get('top_level') or {}, b.get('top_level') or {}),
                     *zip(a.get('internal_states') or [], b.get('internal_states') or [])]
            known_seeds = all(type(x.get('random_seed')) is int and x['random_seed'] >= 0
                              and type(y.get('random_seed')) is int and y['random_seed'] >= 0
                              for x, y in pairs if 'random_seed' in x or 'random_seed' in y)
            if a != b and without_seed(a) == without_seed(b) and known_seeds:
                seed_difference = {'control': {'top_level': a['top_level'].get('random_seed'),
                                              'internal_states': [x.get('random_seed') for x in a.get('internal_states') or []]},
                                   'candidate': {'top_level': b['top_level'].get('random_seed'),
                                                'internal_states': [x.get('random_seed') for x in b.get('internal_states') or []]}}
                a, b = without_seed(a), without_seed(b)
        if a is None or b is None:
            unknowns.append(field)
        elif a != b:
            mismatches.append(field)
    a = left['workload_completion']['first_validated_completion']; b = right['workload_completion']['first_validated_completion']
    for key in ('semantic_request_sha256', 'generation_mode'):
        if a is None or b is None:
            unknowns.append('first_completion.' + key)
        elif a[key] != b[key]:
            mismatches.append('first_completion.' + key)
    changed = [n for n in sorted(set(left['tooling_files']) | set(right['tooling_files']))
               if left['tooling_files'].get(n) != right['tooling_files'].get(n)]
    unallowed = sorted(set(changed)-set(allowed_tool_changes))
    if unallowed:
        mismatches.append('unallowed_tool_changes')
    if left['workload_completion']['earlier_unvalidated_terminal_receipts'] or right['workload_completion']['earlier_unvalidated_terminal_receipts']:
        unknowns.append('earlier_completion_evidence')
    if a and b and (a['generation_mode'] != 'fixed-generated-work' or b['generation_mode'] != 'fixed-generated-work'):
        unknowns.append('generation_work_not_fixed_for_startup_contrast')
    matched = not mismatches and not unknowns
    x=left['allocation_to_first_validated_workload_completion_s']; y=right['allocation_to_first_validated_workload_completion_s']
    return {'matched_observed_fields': matched, 'mismatches': mismatches, 'unknowns': unknowns,
            'allowed_random_seed_difference': seed_difference,
            'changed_tools': changed, 'unallowed_changed_tools': unallowed,
            'control_elapsed_s': x, 'candidate_elapsed_s': y,
            'control_first_usage': a['usage'] if a else None, 'candidate_first_usage': b['usage'] if b else None,
            'paired_elapsed_difference_s': y-x if matched and x is not None and y is not None else None,
            'interpretation': 'Matching covers only reported identities and requires identical fixed-work requests for a startup contrast. An explicitly allowed random-seed difference is retained: equal emitted work does not ensure identical output, internal work or deterministic generation. This is not exact deployment attestation or a proven gain; replicate counterbalanced fresh-host runs and retain failures. Unmatched historical differences are not optimization effects.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, action='append', required=True)
    parser.add_argument('--allow-tool-change', action='append', default=[])
    parser.add_argument('--allow-random-seed-difference', action='store_true',
                        help='Predeclared startup timing contrast only; retain observed seed variation and require identical fixed-output requests.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reports = [analyze(path) for path in args.run]
    result = {'reports': reports, 'comparison': compare(*reports, args.allow_tool_change, args.allow_random_seed_difference) if len(reports) == 2 else None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps({'output': str(args.output.resolve()), 'runs': len(reports), 'comparison': result['comparison']}, indent=2))
