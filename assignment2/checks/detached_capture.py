"""Isolated Linux container fixture ONLY: detached child holds capture pipe.

Run under Docker with --rm --network none; container teardown is the ownership
boundary even if fixture cleanup fails. Never scans/kills unrelated host PIDs.
"""
import json
import os
from pathlib import Path
import signal
import tempfile
import time
from mads.store import Store, atomic_json
from mads.runner import export
from mads.tools import Tools


def main():
    if not Path('/.dockerenv').exists():
        raise SystemExit('This fault fixture requires an isolated Docker container')
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory); tools=Tools(root,root/'run',Store(root/'run/journal'))
        command="python3 -c 'import os,time; p=os.fork(); (open(\"child.pid\",\"w\").write(str(os.getpid())),os.setsid(),time.sleep(60)) if p==0 else None'"
        child=None
        try:
            result=tools.bash(command,wait_seconds=.2)
            for _ in range(100):
                if (root/'child.pid').exists():
                    child=int((root/'child.pid').read_text()); break
                time.sleep(.02)
            assert child is not None
            closed=tools.jobs.close()
            assert closed[0]['process']['capture_status']=='incomplete', closed
            assert closed[0]['errors'], closed
            assert not tools.jobs.jobs['job-1']['reader'].is_alive()
            # Deliberately do not claim detached child was stopped by Jobs.close.
            os.kill(child,0)
            atomic_json(root/'run/result.json',dict(agent_outcome={'status':'completed'},
                harness_outcome={'status':'completed'},cleanup_outcome={'status':'failed','jobs':closed}))
            assert export(root,'HEAD',root/'run') == 1
            assert not (root/'run/patch.diff').exists()
            print(json.dumps({'verified':'detached holder remains outside group; incomplete capture blocks attestation', 'cleanup':closed}))
        finally:
            if child is not None:
                try: os.kill(child,signal.SIGKILL)
                except ProcessLookupError: pass


if __name__=='__main__': main()
