"""Opt-in task-only server handoff. No workload call or mutation is retried.

The original job owns the service process, runtime and logs for its entire life.
An uncertain owner/completion/cleanup withholds successor admission. A known owned
generation may be retired once and replaced before any successor executor starts.
"""
import argparse
import base64
from datetime import datetime
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path('/opt/a4-replica')
MODEL = {'format': 1, 'engine': 'sglang',
         'model': 'nvidia/Qwen3.6-35B-A3B-NVFP4@1355db6a052410cfd62085d94b58866fd0f2c3c5'}
TOOLS = ('a4-serve', 'start-gpu.sh', 'tcp_forward.py')
PARALLEL_FLAGS = {'--dp-size', '--dp', '--data-parallel-size', '--tp-size', '--tp',
                  '--tensor-parallel-size', '--pp-size', '--pp', '--ep-size', '--ep', '--nnodes'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def policy(config):
    value = config.get('reuse_server', False)
    if type(value) is not bool:
        raise ValueError('reuse_server must be boolean')
    if value:
        supported(config)
    return value


def supported(config):
    if config.get('stages') != ['task'] or config.get('resource_profile', 'gpu-cpu') != 'gpu-cpu':
        raise ValueError('reuse_server supports only task-only self-hosted jobs')
    model = config.get('model', {})
    if any(model.get(k) != v for k, v in MODEL.items() if k != 'format'):
        raise ValueError('reuse_server requires the fixed course SGLang/checkpoint')
    modes = config.get('server_reuse_tool_modes')
    if (not isinstance(modes, dict) or set(modes) != set(TOOLS)
            or any(type(mode) is not int or not 0 <= mode <= 0o777 for mode in modes.values())
            or not modes['a4-serve'] & 0o111):
        raise ValueError('reuse_server requires complete frozen serving-helper modes')
    flags = config.get('submitted_flags')
    if not isinstance(flags, list) or any(not isinstance(x, str) for x in flags):
        raise ValueError('reuse_server requires explicit frozen serving flags')
    for i, flag in enumerate(flags):
        name, sep, value = flag.partition('=')
        if name in PARALLEL_FLAGS:
            if not sep:
                value = flags[i + 1] if i + 1 < len(flags) else None
            if value != '1':
                raise ValueError('reuse_server requires single-GPU tp1/dp1/pp1/ep1/nnodes1')
        if name in ('--enable-dp-attention', '--enable-dp-lm-head', '--disaggregation-mode',
                    '--dist-init-addr', '--node-rank', '--base-gpu-id', '--gpu-id-step'):
            raise ValueError('reuse_server does not support distributed serving flags')


def job_id(value):
    if not re.fullmatch(r'[0-9]{3}', value):
        raise ValueError('Expected a three-digit job ID')
    return value


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected regular owned file: ' + str(path))
    return path


def write_once(path, value):
    # Never overwrite an earlier generation/transition observation.
    pending = path.with_name(path.name + '.pending')
    with pending.open('x') as output:
        json.dump(value, output, indent=2); output.write('\n')
        output.flush(); os.fsync(output.fileno())
    try:
        os.link(pending, path)
    finally:
        pending.unlink()


def frozen_identity(root, ident):
    job = root / 'jobs' / job_id(ident)
    snapshot = job / 'iteration'
    manifest = regular(snapshot / 'experiment.json')
    config = json.loads(manifest.read_text())
    supported(config)
    engine = snapshot / 'course/engine'
    if engine.is_symlink() or not engine.is_dir():
        raise ValueError('Missing regular engine directory')
    files = {}
    for path in sorted(engine.rglob('*')):
        if path.is_symlink():
            raise ValueError('Engine symlinks are unsupported for reuse')
        if path.is_file():
            name = str(path.relative_to(snapshot))
            digest = sha(path)
            if digest != config['files'].get(name):
                raise ValueError('Frozen engine file changed: ' + name)
            files[name] = {'sha256': digest, 'mode': path.stat().st_mode & 0o777}
    expected = {n for n in config['files'] if n.startswith('course/engine/')}
    if set(files) != expected or not {'course/engine/engine.json', 'course/engine/flags.txt',
                                     'course/engine/launch.sh'} <= expected:
        raise ValueError('Frozen engine tree inventory changed or incomplete')
    if json.loads((engine / 'engine.json').read_text()) != config['model']:
        raise ValueError('Frozen model identity differs from engine')
    if shlex.split((engine / 'flags.txt').read_text(), comments=True) != config['submitted_flags']:
        raise ValueError('Frozen flags differ from engine')
    tools = {}
    for name in TOOLS:
        # The GPU payload deliberately omits each job's tools directory. Its
        # shared installed tools are bound by this frozen manifest, including modes.
        path = regular(root / name)
        if sha(path) != config['tooling_files'].get(name):
            raise ValueError('Installed serving helper differs from frozen tool: ' + name)
        if path.stat().st_mode & 0o777 != config.get('server_reuse_tool_modes', {}).get(name):
            raise ValueError('Installed serving helper mode differs from frozen tool: ' + name)
        if name == 'a4-serve' and not path.stat().st_mode & 0o111:
            raise ValueError('Serving helper lost executable mode')
        tools[name] = {'sha256': sha(path), 'mode': path.stat().st_mode & 0o777}
    return {'job': ident, 'manifest_sha256': sha(manifest), 'engine_files': files,
            'model': config['model'], 'flags': config['submitted_flags'], 'tools': tools}


def completion(root, ident):
    """CPU-side evidence from the original executor; no process or task action."""
    job = root / 'jobs' / job_id(ident)
    if not regular(job / 'experiment.complete') or (job / 'experiment.failed').exists():
        raise ValueError('Prior executor did not complete normally')
    manifest = regular(job / 'iteration/experiment.json')
    config = json.loads(manifest.read_text()); supported(config)
    receipt = json.loads(regular(job / 'experiment.json').read_text())
    stages = receipt.get('stages')
    if (receipt.get('frozen_experiment') != sha(manifest) or not isinstance(stages, list)
            or len(stages) != 1 or stages[0].get('stage') != 'task'
            or stages[0].get('exit_code') != 0
            or stages[0].get('owner_exited_with_live_descendants') is not False):
        raise ValueError('Prior executor completion/group closure is not established')
    # The stock cleanup ignores docker-rm errors. Normal host exit alone does
    # not establish that detached preflight/task agent and proxy producers ended.
    course = job / 'iteration/execution/course'
    source = regular(course / 'evaluation_scripts/run_all.py')
    if sha(source) != config['files'].get('course/evaluation_scripts/run_all.py'):
        raise ValueError('Executed course runner does not match frozen source')
    names = []; logs = {}
    pattern = re.compile(r'^== agent a3agent_([0-9]+_[0-9]+) \([^\n]+\); networks a3sbx_\1, a3egress_\1; API http://a3proxy_\1:3128/v1 -> ', re.M)
    for name in ('preflight.log', 'task.log'):
        path = regular(job / 'iteration' / name)
        runs = pattern.findall(path.read_text())
        if len(runs) != 1:
            raise ValueError('Exact prior course producer names unavailable: ' + name)
        names.extend(['a3agent_' + runs[0], 'a3proxy_' + runs[0]])
        logs[name] = {'sha256': sha(path), 'run': runs[0]}
    if len(set(names)) != 4:
        raise ValueError('Preflight and task producer identities are not distinct')
    ids = docker('ps', '-a', '--no-trunc', '--format', '{{.ID}}').split()
    if len(ids) != len(set(ids)) or any(not re.fullmatch('[0-9a-f]{64}', x) for x in ids):
        raise ValueError('Unknown CPU producer inventory')
    inspected = json.loads(docker('inspect', *ids)) if ids else []
    if len(inspected) != len(ids) or {x.get('Id') for x in inspected} != set(ids):
        raise ValueError('Incomplete CPU producer inspection')
    retained = []
    for item in inspected:
        name = item.get('Name'); mounts = item.get('Mounts')
        if not isinstance(name, str) or not isinstance(mounts, list):
            raise ValueError('Unknown CPU producer name/mount')
        related = []
        for bind in mounts:
            if bind.get('Type') == 'bind':
                source_path = Path(bind.get('Source', ''))
                if not source_path.is_absolute(): raise ValueError('Unknown CPU bind path')
                if source_path == course or source_path.is_relative_to(course):
                    related.append({'source':str(source_path),'destination':bind.get('Destination')})
        if name.lstrip('/') in names or related:
            retained.append({'id':item['Id'],'name':name,'related_mounts':related})
    closure = {'expected_names':names,'logs':logs,'runner_sha256':sha(source),
               'inventory_ids':ids,'retained_producers':retained,'no_job_producers_observed':not retained}
    # A fresh write-once CPU receipt retains refused cleanup, too.
    write_once(job / 'server-reuse-producer-closure.json', closure)
    if retained:
        raise ValueError('Prior detached course inference producer cleanup is unconfirmed')
    return {'job': ident, 'manifest_sha256': sha(manifest), 'normal_completion': True,
            'executor_receipt_sha256': sha(job / 'experiment.json'),
            'complete_marker_sha256': sha(job / 'experiment.complete'), 'stages': stages,
            'producer_closure':closure}


def docker(*args):
    result = subprocess.run(['sudo', 'docker', *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Docker observation/action failed: ' + ' '.join(args[:2]) +
                           ' status=' + str(result.returncode) + ' ' + result.stderr[-1000:])
    return result.stdout


def project(item):
    ident = item.get('Id')
    if not isinstance(ident, str) or not re.fullmatch('[0-9a-f]{64}', ident):
        raise ValueError('Missing original full Docker ID')
    state = item.get('State', {})
    if not state.get('StartedAt') or state.get('Status') not in ('running', 'exited', 'dead'):
        raise ValueError('Unknown Docker lifecycle identity')
    if any(type(state.get(k)) is not bool for k in ('Running', 'Paused', 'Restarting')):
        raise ValueError('Incomplete Docker running-state observation')
    if type(item.get('RestartCount')) is not int or item['RestartCount'] < 0:
        raise ValueError('Missing Docker restart identity')
    return {'id': ident, 'name': item.get('Name'), 'image': item.get('Image'),
            'started_at': state['StartedAt'], 'restart_count': item.get('RestartCount'),
            'state': {k: state[k] for k in ('Status', 'Running', 'Paused', 'Restarting')},
            'mounts': item.get('Mounts'), 'networks': item.get('NetworkSettings', {}).get('Networks')}


def inspect(ids):
    values = json.loads(docker('inspect', *ids))
    if not isinstance(values, list) or len(values) != len(ids):
        raise ValueError('Incomplete Docker inspection')
    rows = [project(x) for x in values]
    if {x['id'] for x in rows} != set(ids):
        raise ValueError('Docker returned unexpected full IDs')
    return {x['id']: x for x in rows}


def mount(row, destination, source, readonly=True):
    matches = [m for m in row['mounts'] or [] if m.get('Destination') == destination]
    if len(matches) != 1 or matches[0].get('Type') != 'bind' or matches[0].get('Source') != str(source) or matches[0].get('RW') is not (not readonly):
        raise ValueError('Original read-only serving mount differs: ' + destination)


def generation(root, producer):
    """Anchor exact original Docker IDs before deciding whether they may be reused."""
    job = root / 'jobs' / producer; target = job / 'server-generation.json'
    if target.exists():
        value = json.loads(regular(target).read_text())
        if value.get('producer_job') != producer or value.get('frozen') != frozen_identity(root, producer):
            raise ValueError('Generation producer mismatch')
        return value
    frozen = frozen_identity(root, producer)
    rows = [project(x) for x in json.loads(regular(root / 'evidence/server-containers.json').read_text())]
    if len(rows) != 2 or len({x['id'] for x in rows}) != 2 or {x['name'] for x in rows} != {'/a4-stock-server', '/a4-server-transport'}:
        raise ValueError('Missing original model/relay inventory')
    image = regular(root / 'evidence/gpu-image.id').read_text().strip()
    if not re.fullmatch('sha256:[0-9a-f]{64}', image) or any(x['image'] != image for x in rows):
        raise ValueError('Original serving image differs from bootstrap image')
    model = next(x for x in rows if x['name'] == '/a4-stock-server')
    relay = next(x for x in rows if x['name'] == '/a4-server-transport')
    mount(model, '/engine', job / 'iteration/course/engine')
    mount(model, '/model', root / 'weights/model')
    mount(model, '/usr/local/bin/a4-serve', root / 'a4-serve')
    mount(model, '/run/serve.key', root / 'serve.key')
    mount(relay, '/relay.py', root / 'tcp_forward.py')
    if set(model['networks'] or {}) != {'a4-server-private'} or set(relay['networks'] or {}) != {'a4-server-private', 'bridge'}:
        raise ValueError('Original serving network differs')
    source = root / 'weights/model'
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Model mount identity unavailable')
    stat = source.stat()
    value = {'producer_job': producer, 'recorded_at_unix': time.time(), 'frozen': frozen,
             'containers': rows, 'image': image, 'model_mount_identity': {'path': str(source), 'device': stat.st_dev, 'inode': stat.st_ino},
             'runtime_reference': str(job / 'runtime.json'), 'runtime_sha256': sha(regular(job / 'runtime.json')),
             'server_log_reference': str(job / 'server.log'),
             'limitation': 'Pinned checkpoint plus original mount identity, not an independent hash of all weight bytes.'}
    write_once(target, value)
    return value


def owned_current(gen):
    """No name-based kill/reuse: old IDs, image, start time and mounts must agree."""
    old = {x['id']: x for x in gen['containers']}; observed = inspect(list(old))
    for ident, row in observed.items():
        for key in ('id', 'name', 'image', 'started_at', 'restart_count', 'mounts'):
            if row[key] != old[ident][key]:
                raise ValueError('Original server identity changed: ' + key)
        # Docker can clear endpoint/IP fields after exit. Immutable full ID,
        # StartedAt, image, restart count and mounts still bind safe retirement.
        if row['state']['Running'] and row['networks'] != old[ident]['networks']:
            raise ValueError('Original running server network changed')
    return observed


def http(endpoint, key, path, method='GET'):
    request = urllib.request.Request(endpoint.rstrip('/') + path,
                                    headers={'Authorization': 'Bearer ' + key}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        return error.code, error.read(2 * 1024 * 1024 + 1)


def http_observation(endpoint, key, path, method='GET', retain_body=True):
    started = time.time()
    error = None
    try:
        status, raw = http(endpoint, key, path, method)
    except (OSError, HTTPException) as failure:
        status = None; raw = getattr(failure, 'partial', b'')
        if not isinstance(raw, bytes): raw = b''
        error = type(failure).__name__ + ': ' + str(failure)
    value = {'path': path, 'method': method, 'started_at_unix': started,
             'finished_at_unix': time.time(), 'status': status,
             'response_sha256': hashlib.sha256(raw).hexdigest(), 'observed_bytes': len(raw),
             'observer_limit_exceeded': len(raw) > 2 * 1024 * 1024,
             'read_completed': error is None, 'error': error}
    if retain_body:
        safe = raw.replace(key.encode(), b'[REDACTED]') if key else raw
        value.update(response_bytes_base64=base64.b64encode(safe).decode(),
                     response_text=safe.decode(errors='replace'), credential_redacted=safe != raw)
    return value, raw


def retire(root, gen, receipt):
    """One exact-ID stop, then prove absence and original wrapper exit before restart."""
    owned_current(gen)  # A new unknown owner cannot be swept up in fallback.
    ids = [x['id'] for x in gen['containers']]
    receipt['retirement'] = {'ids': ids, 'requested_at_unix': time.time()}
    try:
        docker('rm', '-f', *ids)
        receipt['retirement']['remove_returned_success'] = True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        receipt['retirement']['remove_error'] = type(error).__name__ + ': ' + str(error)
        # No mutation retry. Read-only confirmation may establish its effect.
    deadline = time.monotonic() + 30
    exit_path = root / 'jobs' / gen['producer_job'] / 'server-exit.json'
    while True:
        inventory = docker('ps', '-a', '--no-trunc', '--format', '{{.ID}}').split()
        if len(set(inventory)) != len(inventory) or any(not re.fullmatch('[0-9a-f]{64}', x) for x in inventory):
            raise ValueError('Unknown container inventory during retirement')
        if not set(ids) & set(inventory) and exit_path.exists():
            exited = json.loads(regular(exit_path).read_text())
            start = max(datetime.fromisoformat(x['started_at'].replace('Z', '+00:00')).timestamp() for x in gen['containers'])
            if (exited.get('boundary') != 'start-gpu.sh returned' or type(exited.get('exit_code')) is not int
                    or not isinstance(exited.get('recorded_at_unix'), (int, float)) or exited['recorded_at_unix'] < start):
                raise ValueError('Original producer termination receipt is invalid')
            receipt['retirement'].update(confirmed_at_unix=time.time(), producer_exit=exited,
                                         producer_exit_sha256=sha(exit_path))
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Original server retirement/producer exit remains unconfirmed')
        time.sleep(.1)


def transition(root, producer, previous, successor, prior_completion, endpoint):
    """Called once before a successor executor exists; response is never workload."""
    for ident in (producer, previous, successor): job_id(ident)
    receipt = {'successor_job': successor, 'previous_job': previous, 'producer_job': producer,
               'started_at_unix': time.time(), 'state': 'blocked', 'previous_completion': prior_completion,
               'no_workload_dispatched': True, 'rng_reset': False,
               'limitation': 'Positive dp1 flush is an idle-at-operation observation, not ingress exclusion or full process/RNG reset.'}
    try:
        current = frozen_identity(root, previous); desired = frozen_identity(root, successor)
        config = json.loads((root / 'jobs' / successor / 'iteration/experiment.json').read_text())
        if not policy(config): raise ValueError('Successor did not explicitly opt into reuse')
        if (prior_completion.get('job') != previous or prior_completion.get('manifest_sha256') != current['manifest_sha256']
                or prior_completion.get('normal_completion') is not True
                or prior_completion.get('producer_closure', {}).get('no_job_producers_observed') is not True):
            raise ValueError('Prior CPU executor completion is unknown or mismatched')
        gen = generation(root, producer); observed = owned_current(gen)
        receipt['generation'] = gen
        receipt['producer_log_offset_before'] = regular(root / 'jobs' / producer / 'server.log').stat().st_size
        compatible = all(gen['frozen'][k] == desired[k] for k in ('engine_files', 'model', 'flags', 'tools'))
        model = root / 'weights/model';stat = model.stat()
        compatible = compatible and gen['model_mount_identity'] == {'path': str(model), 'device': stat.st_dev, 'inode': stat.st_ino}
        compatible = compatible and regular(root / 'evidence/gpu-image.id').read_text().strip() == gen['image']
        healthy = all(x['state'] == {'Status': 'running', 'Running': True, 'Paused': False, 'Restarting': False} for x in observed.values())
        failure = None
        try:
            if not compatible: raise ValueError('Frozen engine/helper/image/model identity differs')
            if not healthy or (root / 'jobs' / producer / 'server.failed').exists():
                raise ValueError('Original server is not running normally')
            key = regular(root / 'serve.key').read_text().strip()
            receipt['health'], raw = http_observation(endpoint, key, '/health')
            if receipt['health']['status'] != 200 or receipt['health']['observer_limit_exceeded']:
                raise ValueError('Live-server health refused')
            # Raw server_info can contain the API key. Retain its identity/hash
            # and selected settings rather than copying credentials into receipts.
            receipt['server_info'], raw = http_observation(endpoint, key, '/server_info', retain_body=False)
            if receipt['server_info']['status'] != 200 or receipt['server_info']['observer_limit_exceeded']:
                raise ValueError('Server identity read failed')
            info = json.loads(raw)
            if (not isinstance(info, dict) or not isinstance(info.get('internal_states'), list)
                    or any(not isinstance(x, dict) for x in info['internal_states'])):
                raise ValueError('Incomplete actual serving identity')
            settings = [{k: state.get(k) for k in ('dp_size', 'tp_size', 'pp_size', 'ep_size', 'nnodes', 'model_path', 'served_model_name', 'random_seed')}
                        for state in [info, *info.get('internal_states', [])]]
            receipt['server_settings'] = settings
            if len(settings) != 2 or any(any(x[k] != 1 for k in ('dp_size', 'tp_size', 'pp_size', 'ep_size', 'nnodes')) or x['model_path'] != '/model'
                                        or x['served_model_name'] != 'qwen3.6-35b-a3b-fp4' for x in settings):
                raise ValueError('Actual serving identity is not supported single-GPU/dp1')
            receipt['flush'], raw = http_observation(endpoint, key, '/flush_cache', method='POST')
            if receipt['flush']['status'] != 200 or receipt['flush']['observer_limit_exceeded']:
                raise ValueError('Live-server cache flush refused')
            # Detect exit/replacement across the observation/flush boundary.
            after = owned_current(gen)
            if any(x['state'] != {'Status': 'running', 'Running': True, 'Paused': False, 'Restarting': False} for x in after.values()) or (root / 'jobs' / producer / 'server.failed').exists():
                raise ValueError('Original server ended during handoff')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            failure = type(error).__name__ + ': ' + str(error)
        if failure:
            receipt['fallback_reason'] = failure
            retire(root, gen, receipt)
            receipt['state'] = 'restart_ready'
        else:
            receipt.update(state='reused', runtime_reference=gen['runtime_reference'], server_log_reference=gen['server_log_reference'],
                           producer_failure_marker=str(root / 'jobs' / producer / 'server.failed'),
                           metrics_epoch_boundary_unix=receipt['flush']['finished_at_unix'],
                           producer_log_offset_after=regular(root / 'jobs' / producer / 'server.log').stat().st_size)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        receipt['error'] = type(error).__name__ + ': ' + str(error)
    receipt['finished_at_unix'] = time.time()
    write_once(root / 'jobs' / successor / 'server-reuse.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['completion', 'transition']); parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--job'); parser.add_argument('--producer'); parser.add_argument('--previous'); parser.add_argument('--successor')
    parser.add_argument('--completion'); parser.add_argument('--endpoint', default='http://127.0.0.1:30000')
    args = parser.parse_args()
    if args.action == 'completion':
        result = completion(args.root, args.job)
    else:
        result = transition(args.root, args.producer, args.previous, args.successor, json.loads(args.completion), args.endpoint)
    print(json.dumps(result))
    if result.get('state') == 'blocked': raise SystemExit(2)
