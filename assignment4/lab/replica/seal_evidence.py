"""Create one immutable, checksummed archive on an owned host after execution ends."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ITEMS=['jobs','iteration','iteration-logs','evidence','bootstrap.log','prepare.log','experiment.json','experiment.failed','experiment.log','investigator.log','server.log','controlled-shutdown.json','gpu-smoke.json','cache/hostlogs','kernel-cache/profiles']
EXCLUDES=['*/home/.cache/*','*/native/comparison/environments/swe-agent/*','*/native/comparison/upstream/*','*/native/comparison/environments/jcode/*','*/native/original-source*/*','*/sweep/*/task-*/case-0/*/app/*','jobs/*/iteration/native-comparison/upstream/*','jobs/*/iteration/native-comparison/environments/jcode/*']
def seal(root,role):
    root=Path(root);receipt=root/'evidence-receipt.json';lock=root/'evidence-seal.lock'
    if receipt.exists():return
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        pid=int(lock.read_text())
        try:os.kill(pid,0)
        except ProcessLookupError:lock.rename(lock.with_name('evidence-seal.stale-'+str(time.time_ns())));return seal(root,role)
        return
    with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
    try:
        preservation=None
        if role=='cpu':
            from interrupted_capture import verify_before_seal
            preservation=verify_before_seal(root)
        if role=='gpu':
            # Solving has ended. Preserve the deliberate stop as an execution
            # fact so the launch wrapper cannot call this a serving failure.
            (root/'controlled-shutdown.json').write_text(json.dumps({'at_unix':time.time(),'reason':'execution ended; sealing GPU evidence before termination'}))
            subprocess.run(['sudo','docker','stop','a4-stock-server','a4-server-transport'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        items=[name for name in ITEMS if (root/name).exists()]
        partial=root/'evidence.tar.partial';final=root/'evidence.tar.gz'
        with (root/'evidence-tar.log').open('wb') as errors:
            completed=subprocess.run(['sudo','tar',*['--exclude='+pattern for pattern in EXCLUDES],'-czf',str(partial),*items],cwd=root,stderr=errors)
        # Tar's status 1 can report telemetry changing during capture. Completed
        # task traces are immutable; preserve that warning rather than hiding it.
        if completed.returncode not in (0,1):raise RuntimeError('Tar failed: '+str(completed.returncode))
        subprocess.run(['tar','-tzf',str(partial)],check=True,stdout=subprocess.DEVNULL)
        partial.replace(final)
        with final.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        data={'sha256':digest,'bytes':final.stat().st_size,'role':role,'tar_exit_code':completed.returncode,'sealed_at_unix':time.time(),'interrupted_preservation':preservation}
        temporary=receipt.with_suffix('.tmp');temporary.write_text(json.dumps(data));temporary.replace(receipt)
    except BaseException as error:
        (root/'evidence-seal.failed.json').write_text(json.dumps({'error':type(error).__name__+': '+str(error)}));raise
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/opt/a4-replica'));p.add_argument('--role',choices=['gpu','cpu'],required=True);a=p.parse_args();seal(a.root,a.role)
