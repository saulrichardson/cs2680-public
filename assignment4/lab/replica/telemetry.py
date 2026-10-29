"""Low-rate host/server telemetry; separate from solver observations and decisions."""
import argparse
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request


def nonnegative(value):
    number=int(value)
    if number<0:raise ValueError('Negative kernel counter/gauge')
    return number


def cpu_stat(raw):
    lines={parts[0]:parts[1:] for line in raw.splitlines() if (parts:=line.split())}
    ticks=lines['cpu']
    if len(ticks)<4:raise ValueError('Incomplete aggregate CPU counters')
    names=('user','nice','system','idle','iowait','irq','softirq','steal','guest','guest_nice')
    # Guest time is included in user/nice, and iowait may decrease. Preserve
    # raw cumulative ticks; downstream deltas must handle boot/reset/overlap.
    return {'cpu_ticks_cumulative':{name:nonnegative(ticks[i]) if i<len(ticks) else None for i,name in enumerate(names)},
            'clock_ticks_per_second':os.sysconf('SC_CLK_TCK'),
            'context_switches_cumulative':nonnegative(lines['ctxt'][0]),
            'processes_created_cumulative':nonnegative(lines['processes'][0]),
            'processes_running_gauge':nonnegative(lines['procs_running'][0]),
            'processes_blocked_gauge':nonnegative(lines['procs_blocked'][0]),
            'boot_time_unix':nonnegative(lines['btime'][0])}


def memory_info(raw):
    names=('MemTotal','MemAvailable','MemFree','Buffers','Cached','SwapTotal','SwapFree','Dirty','Writeback','Shmem','Slab','SReclaimable','SUnreclaim')
    values={name:None for name in names}
    for line in raw.splitlines():
        name,separator,rest=line.partition(':')
        if name not in values:continue
        parts=rest.split()
        if not separator or len(parts)!=2 or parts[1]!='kB':raise ValueError('Malformed memory field: '+name)
        values[name]=nonnegative(parts[0])
    if values['MemTotal'] is None:raise ValueError('Missing MemTotal')
    # Linux meminfo labels KiB values as kB. Missing optional fields stay null.
    return {'gauges_kib':values}


def pressure_info(raw):
    values={'some':None,'full':None}
    for line in raw.splitlines():
        parts=line.split()
        if not parts:continue
        if parts[0] not in values:raise ValueError('Unknown pressure category')
        fields=dict(part.split('=',1) for part in parts[1:])
        averages={name:float(fields[name]) for name in ('avg10','avg60','avg300')}
        if any(not math.isfinite(v) or not 0<=v<=100 for v in averages.values()):raise ValueError('Invalid pressure average')
        values[parts[0]]={'averages_pct':averages,'total_stall_us_cumulative':nonnegative(fields['total'])}
    if values['some'] is None:raise ValueError('Missing pressure some category')
    return values


def observation(source,read):
    try:return {'source':str(source),'values':read(),'error':None}
    except (OSError,ValueError,KeyError,IndexError) as error:
        return {'source':str(source),'values':None,'error':type(error).__name__+': '+str(error)}


def filesystem_space(path):
    stat=os.statvfs(path)
    return {'device_id':os.stat(path).st_dev,'total_bytes':stat.f_blocks*stat.f_frsize,
            'free_bytes':stat.f_bfree*stat.f_frsize,'available_bytes':stat.f_bavail*stat.f_frsize,
            'total_inodes':stat.f_files if stat.f_files>=0 else None,
            'free_inodes':stat.f_ffree if stat.f_ffree>=0 else None,
            'available_inodes':stat.f_favail if stat.f_favail>=0 else None}


def host_sample(root,proc=Path('/proc')):
    """Five small proc reads and statvfs/stat; no processes or directory walks.

    PSI averages and memory/filesystem/process counts are gauges. CPU/PSI totals
    are cumulative and not a per-sample utilization or an additive wall time.
    The filesystem is the campaign root's mount, not total workspace sizes.
    """
    started=time.perf_counter()
    row={'started_at_unix':time.time()}
    row['cpu']=observation(proc/'stat',lambda:cpu_stat((proc/'stat').read_text()))
    row['memory']=observation(proc/'meminfo',lambda:memory_info((proc/'meminfo').read_text()))
    row['pressure']={kind:observation(proc/'pressure'/kind,lambda kind=kind:pressure_info((proc/'pressure'/kind).read_text()))
                     for kind in ('cpu','memory','io')}
    row['filesystem']=observation(root,lambda:filesystem_space(root))
    row.update(finished_at_unix=time.time(),elapsed_s=time.perf_counter()-started)
    return row


def sample(root,role,key):
    row={'at':datetime.now(timezone.utc).isoformat(),'at_unix':time.time(),'role':role,'load_average':os.getloadavg()}
    if role=='cpu':row['host']=host_sample(root)
    command=['nvidia-smi','--query-gpu=utilization.gpu,memory.used,power.draw,clocks.sm,clocks.mem','--format=csv,noheader,nounits'] if role=='gpu' else ['sudo','docker','stats','--no-stream','--format','{{json .}}']
    try:
        hardware=subprocess.run(command,capture_output=True,text=True,timeout=10)
        row['hardware']={'status':hardware.returncode,'output':hardware.stdout,'error':hardware.stderr}
    except subprocess.TimeoutExpired:row['hardware']={'error':'sampling timed out'}
    except OSError as error:row['hardware']={'error':type(error).__name__+': '+str(error)}
    if role=='cpu':
        try:
            from task_resource_events import observe
            row['task_resources']=observe(root,row['hardware'])
        except Exception as error:
            # Resource attribution is optional evidence, never a reason to lose
            # the ordinary host/hardware sample or stop this existing collector.
            row['task_resources']={'status':'unknown','error':type(error).__name__+': '+str(error)}
    if role=='gpu':
        try:
            req=urllib.request.Request('http://127.0.0.1:30000/metrics',headers={'Authorization':'Bearer '+key})
            with urllib.request.urlopen(req,timeout=5) as response:row['server_metrics']=response.read().decode().replace(key,'[REDACTED]')
        except Exception as error:row['server_metrics_error']=type(error).__name__
    row['finished_at_unix']=time.time()
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path('/opt/a4-replica'));p.add_argument('--role',choices=['gpu','cpu'],required=True);p.add_argument('--interval',type=float,default=5);a=p.parse_args()
    if not math.isfinite(a.interval) or a.interval<1:p.error('Sampling interval must be at least one finite second')
    root=a.root;directory=root/'evidence';directory.mkdir(exist_ok=True)
    key=(root/'serve.key').read_text().strip()
    while not any((root/name).exists() for name in ['experiment.complete','experiment.failed']):
        row=sample(root,a.role,key)
        with (directory/(a.role+'-telemetry.jsonl')).open('a') as out:out.write(json.dumps(row)+'\n')
        time.sleep(a.interval)


if __name__=='__main__':main()
