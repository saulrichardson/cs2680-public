"""Opt-in AWS dependency access for task sandboxes, retaining private tool routing."""
from pathlib import Path


DEPENDENCY_ENV = {'GOPROXY': 'https://proxy.golang.org,direct',
                  'GOSUMDB': 'sum.golang.org', 'GOTOOLCHAIN': 'auto'}


OFFLINE_PREFLIGHT = '''            if "REACHABLE" in out:
                log(f"preflight: sandbox isolation FAILED: {out.strip()}"); bad = 1
            else:
                log("preflight: sandbox sees neither the proxy nor the internet (good)")'''
NETWORK_PREFLIGHT = '''            if "PROXY_REACHABLE" in out or "INTERNET_REACHABLE" not in out:
                log(f"preflight: dependency-network policy FAILED: {out.strip()}"); bad = 1
            else:
                log("preflight: solver dependency internet available; agent proxy remains unreachable (declared AWS network profile)")'''


def instrument(text):
    old = '       "-e", "GOPROXY=off", "-e", "GOSUMDB=off", "-e", "GOTOOLCHAIN=local",'
    boundary = '       "--entrypoint", "bash", t.sandbox_image, "-c", inner)'
    if text.count(old) != 1 or text.count(boundary) != 1 or text.count(OFFLINE_PREFLIGHT) != 1:
        raise ValueError('Unrecognized original task-sandbox dependency boundary')
    replacement = '       ' + ', '.join('"-e", "' + key + '=' + value + '"' for key, value in DEPENDENCY_ENV.items()) + ','
    text = text.replace(old, replacement).replace(OFFLINE_PREFLIGHT, NETWORK_PREFLIGHT)
    # Retain NET: agent -> sandbox tools still use the original private route.
    # This additional gateway is for package managers inside the solver only.
    return text.replace(boundary, boundary + '\n    sh("docker", "network", "connect", "bridge", sbx(t.k))')


def install(source):
    path = Path(source) / 'starter_code/evaluation_scripts/run_all.py'
    text = instrument(path.read_text())
    path.write_text(text)
