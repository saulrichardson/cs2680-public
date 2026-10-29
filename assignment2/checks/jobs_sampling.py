"""Coherent leader/descendant samples plus real gated child/capture checks (offline)."""
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mads.store import Store
from mads.tools import Tools


def main():
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        tools = Tools(root, root/'run', Store(root/'journal'))
        # Deterministically represent a shell that exits immediately AFTER
        # members() sampled it alive and enumerated no surviving processes.
        # This is a sampling test, not a mock provider or real-process result.
        class Transition:
            returncode = None
            pid = 123
            stdin = SimpleNamespace(closed=False)
            def poll(self):
                self.returncode = 0
                return 0
        job = dict(id='sample', proc=Transition(), capture_error=None, eof=False,
                   capture_done=threading.Event(), observed=0, path=root/'unused',
                   reason=None, lock=threading.RLock(), input_accepted=0,
                   input_written=0, input=bytearray(), input_error=None,
                   input_closed=False, input_eof_sent=False)
        with patch.object(tools.jobs, 'members', return_value=[]):
            state = tools.jobs.state(job)
        assert state['state'] == 'running' and state['shell_exit_code'] is None
        assert state['capture_status'] == 'in_progress'
        # Real children cannot finish before observation: release them through
        # separate files rather than depending on the scheduler beating sleep.
        try:
            for i in range(12):
                gate = root/f'gate-{i}'
                result = tools.bash(
                    f'(while [ ! -f {gate} ]; do sleep .01; done; printf CHILD) & exit 0', .02)
                jid = result['process']['job_id']
                tools.jobs.jobs[jid]['proc'].wait(timeout=5)
                result = tools.poll(jid, 0)
                assert result['process']['state'] == 'running'
                assert result['process']['shell_exit_code'] == 0
                assert result['process']['capture_status'] == 'in_progress'
                gate.touch()
                result = tools.poll(jid, 5)
                assert result['process']['state'] == 'exited'
                assert result['process']['capture_status'] == 'complete'
                assert Path(result['output']['path']).read_bytes() == b'CHILD'
        finally:
            cleanup = tools.jobs.close()
            assert not any(row['errors'] for row in cleanup)
    print('PASS coherent process sampling; 12 real gated child/capture/cleanup exercises')


if __name__ == '__main__':
    main()
