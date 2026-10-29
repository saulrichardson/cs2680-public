"""Finite operator recovery relay using the original frozen OpenRouter route.

Backend credentials stay on the owned host. This does not start a model call,
change model settings, renew a task deadline, or control the fleet owner.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--deadline', type=float, required=True)
    a = p.parse_args()
    a.state.mkdir(parents=True, exist_ok=False)
    if time.time() >= a.deadline:
        raise ValueError('Original recovery deadline has expired')
    sys.path.insert(0, str(a.source / 'dev'))
    import course_origin
    route = a.source / 'dev/course-routes/openrouter-fleet.json'
    token = secrets.token_hex(32)
    server = course_origin.make_server(json.loads(route.read_text()), a.state / 'requests',
        backend_key=Path('/home/ubuntu/.config/cs2680/openrouter-api-key').read_text().strip(),
        client_key=token, address=('0.0.0.0', 0))
    network = json.loads(subprocess.check_output(['docker', 'network', 'inspect', 'bridge'], text=True))[0]
    gateways = [item['Gateway'] for item in network['IPAM']['Config'] if item.get('Gateway')]
    if len(gateways) != 1:
        raise ValueError('Recovery relay requires one observed Docker bridge gateway')
    endpoint = f'http://{gateways[0]}:{server.server_port}/v1'
    secret = a.state / 'client-auth.json'
    with open(secret, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as out:
        json.dump({'relay_token': token, 'base_url': endpoint}, out)
    (a.state / 'ready.json').write_text(json.dumps({'pid': os.getpid(),
        'base_url': endpoint,
        'route_sha256': hashlib.sha256(route.read_bytes()).hexdigest(),
        'model': 'standard', 'original_latest_deadline': a.deadline,
        'shutdown_at': a.deadline + 300, 'ready_at': time.time()}, indent=2))
    def stop():
        time.sleep(max(0, a.deadline + 300 - time.time()))
        server.shutdown()
    threading.Thread(target=stop, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
