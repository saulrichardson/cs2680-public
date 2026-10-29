"""Serving readiness belongs to setup, never to an individual solver's investigation."""
import time
import urllib.request
import hashlib
import json
import math
import os

STARTUP_ALLOWANCE_S=45*60


def startup_deadline():
    return time.time()+STARTUP_ALLOWANCE_S


def record_launch(root):
    """CPU-clock receipt after accepted GPU launch dispatch; publish exactly once."""
    launched=time.time()
    value={'job_id':root.name,'frozen_manifest_sha256':hashlib.sha256((root/'iteration/experiment.json').read_bytes()).hexdigest(),
           'launched_at_unix':launched,'deadline_unix':launched+STARTUP_ALLOWANCE_S,
           'boundary':'GPU server dispatch accepted; timestamp from CPU host'}
    path=root/'model-launch.json';pending=root/'model-launch.pending'
    with pending.open('x') as stream:
        json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
    try:os.link(pending,path)  # An existing launch receipt cannot be reset.
    finally:pending.unlink()
    return value


def wait_for_launch(root,cancelled):
    """Preparation can finish before GPU bootstrap; that is not model startup time."""
    expected=hashlib.sha256((root/'iteration/experiment.json').read_bytes()).hexdigest()
    while True:
        if cancelled():raise RuntimeError('Model launch wait cancelled or infrastructure failed')
        path=root/'model-launch.json'
        if path.exists():
            receipt=json.loads(path.read_text())
            launched,deadline=receipt.get('launched_at_unix'),receipt.get('deadline_unix')
            if (receipt.get('job_id')!=root.name or receipt.get('frozen_manifest_sha256')!=expected
                    or type(launched) not in (int,float) or type(deadline) not in (int,float)
                    or not math.isfinite(launched) or not math.isfinite(deadline)
                    or deadline!=launched+STARTUP_ALLOWANCE_S):
                raise ValueError('Invalid or mismatched model launch receipt')
            return deadline
        time.sleep(1)

def wait(endpoint,cancelled,deadline):
    started=time.time()
    while True:
        if cancelled():raise RuntimeError('Serving readiness cancelled or infrastructure failed')
        try:
            with urllib.request.urlopen(endpoint.rstrip('/')+'/health',timeout=5) as response:
                if response.status==200:return {'ready_at_unix':time.time(),'elapsed_s':time.time()-started}
        except Exception:pass
        if time.time()>=deadline:raise RuntimeError('Engine exceeded course 45-minute startup allowance')
        time.sleep(1)
