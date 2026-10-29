"""A stage owns its process group; cancellation must not leave native children alive."""
import os
import signal
import subprocess
import time
from pathlib import Path
import sys


def start(args, **kwargs):
    process=subprocess.Popen(args,start_new_session=True,**kwargs)
    process._a4_owned_group=process.pid
    return process


def stop(process):
    if getattr(process,'_a4_stop_requested',False):return
    if process.returncode is not None:return  # A reaped owner's numeric group ID is not signal authority.
    if getattr(process,'_a4_owned_group',None)!=process.pid:
        raise RuntimeError('Cannot signal an unowned process group')
    try:
        # WNOWAIT confirms this is still our unreaped child. Its PID cannot be
        # reused while we signal the group, even if the leader already exited.
        os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
        # Some kernels no longer expose getpgid for zombies. WNOWAIT still
        # retains the original child/PID; start_new_session established its group.
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass  # The retained child's group is already gone.
        process._a4_stop_requested=True
    except ChildProcessError as error:
        raise RuntimeError('Stage child identity unavailable; cancellation unconfirmed') from error


def wait(process):
    """Retain the leader identity until all owned live writers have stopped."""
    if getattr(process,'_a4_owned_group',None)!=process.pid:
        raise RuntimeError('Cannot await an unowned process group')
    # Do not use Popen.wait/poll until cleanup: either would reap the identity
    # needed to cancel descendants after an unexpected leader exit.
    os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOWAIT)
    live=group_running(process.pid)
    abandoned=live and not getattr(process,'_a4_stop_requested',False)
    if live:
        stop(process)
        while group_running(process.pid):time.sleep(.05)
    return process.wait(),abandoned


def stop_and_wait(process):
    """Do not seal evidence while an owned descendant is finishing cleanup."""
    if getattr(process,'_a4_owned_group',None)!=process.pid:
        raise RuntimeError('Cannot await an unowned process group')
    if process.returncode is None:
        stop(process);wait(process);return
    # Compatibility with callers which already reaped after a requested stop.
    # A vanished leader by itself never authorizes signaling a numeric group.
    while group_running(process.pid):
        if not getattr(process,'_a4_stop_requested',False):
            raise RuntimeError('Stage owner exited before descendant cancellation could be confirmed')
        time.sleep(.05)


def group_running(group):
    if sys.platform=='linux':
        # Orphaned zombies can persist under minimal PID1 implementations, but
        # cannot write evidence. Inspect only state/group, not process contents.
        for entry in Path('/proc').iterdir():
            if not entry.name.isdecimal():continue
            try:raw=(entry/'stat').read_text()
            except (FileNotFoundError,ProcessLookupError):continue
            fields=raw[raw.rfind(')')+2:].split()
            if int(fields[2])==group and fields[0] not in ('Z','X'):return True
        return False
    # macOS has waitid(WNOWAIT), but no /proc. killpg(0) counts the retained
    # zombie leader too, so use the same live-state distinction as Linux.
    listing=subprocess.check_output(['ps','-axo','pgid=,stat='],text=True)
    for line in listing.splitlines():
        pgid,state=line.split()
        if int(pgid)==group and state[0] not in ('Z','X'):return True
    return False
