"""Observation-only task OOM evidence for the existing CPU collector/supervisor.

An oom_kill event identifies container resource loss, never a shell exit or a
failed repair. No process is signalled and no task operation is retried.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from iteration import write

INSPECT_FORMAT='{"Id":{{json .Id}},"Image":{{json .Image}},"State":{{json .State}},"Mounts":{{json .Mounts}}}'


def digest(data):return hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=256)
def _json(path, inode, size, modified):
    raw=Path(path).read_bytes()
    return json.loads(raw),digest(raw)


def read_json(path):
    stat=path.stat()
    return _json(str(path),stat.st_ino,stat.st_size,stat.st_mtime_ns)


def bindings(root):
    values={};errors=[]
    for path in sorted(root.glob('jobs/*/native/sweep/*/task-*/case-0/*/run.json')):
        try:
            output=path.parent;app=output/'app';job=output.parents[5]
            if app.resolve()!=app or not app.is_dir():raise ValueError('Indirect or missing app')
            run,run_sha=read_json(path);plan,plan_sha=read_json(output.parents[1]/'plan.json')
            if len(plan['cases'])!=1 or output.name not in plan['arms']:raise ValueError('Ambiguous native plan')
            case=plan['cases'][0];task_path=output.parents[1]/case['task']
            if not task_path.resolve().is_relative_to(output.parents[1]):raise ValueError('Task escapes native plan')
            frozen,_=read_json(job/'iteration/experiment.json')
            public_path=job/'iteration/course/evaluation_scripts/agent_task_input.json'
            public,public_sha=read_json(public_path)
            if frozen['files'].get('course/evaluation_scripts/agent_task_input.json')!=public_sha:raise ValueError('Frozen task manifest mismatch')
            task,task_sha=read_json(task_path);entry=public[str(case['course_task_id'])]
            expected={k:entry[k] for k in ('problem_statement','requirements','interface') if k in entry}
            if task!=expected or task_sha!=case['task_sha256'] or case['base']!=entry['base_commit'] or case['id']!=entry['instance_id']:
                raise ValueError('Frozen public task/native plan mismatch')
            for key,value in [('source_base',case['base']),('image_id',case['image_id']),('task_sha256',task_sha),('arm',output.name)]:
                if run.get(key)!=value:raise ValueError('Run identity mismatch: '+key)
            if not re.fullmatch('sha256:[0-9a-f]{64}',case['image_id']):raise ValueError('Mutable image identity')
            values[str(app)]={'job':job.name,'attempt':str(output.relative_to(root)),'app':str(app),'arm':output.name,
                'image_id':case['image_id'],'instance_id':case['id'],'course_task_id':str(case['course_task_id']),
                'source_base':case['base'],'task_sha256':task_sha,'plan_sha256':plan_sha,'run_sha256':run_sha}
        except (OSError,ValueError,KeyError,IndexError,TypeError,AttributeError) as error:
            errors.append({'run':str(path.relative_to(root)),'error':type(error).__name__+': '+str(error)})
    return values,errors


def identity_start(proc,pid):
    raw=(proc/str(pid)/'stat').read_text();fields=raw[raw.rfind(')')+2:].split()
    return int(fields[19])  # Linux stat field 22: starttime, in clock ticks.


def container_sample(item,bound,proc,cgroups):
    cid=item['Id'];state=item['State'];pid=state['Pid'];started=time.time()
    if not re.fullmatch('[0-9a-f]{64}',cid) or state.get('Running') is not True or type(pid) is not int or pid<=0 or not state.get('StartedAt'):
        raise ValueError('Container is not a resolved running process')
    start=identity_start(proc,pid)
    membership=(proc/str(pid)/'cgroup').read_text()
    paths=[line[3:] for line in membership.splitlines() if line.startswith('0::')]
    if len(paths)!=1:raise ValueError('No unique cgroup v2 membership')
    relative=Path(paths[0].lstrip('/'))
    if '..' in relative.parts or not any(part in (cid,'docker-'+cid+'.scope') for part in relative.parts):
        raise ValueError('Container/cgroup identity not resolved')
    group=cgroups/relative;inode=group.stat().st_ino
    fields=dict(line.split() for line in (group/'memory.events.local').read_text().splitlines())
    kills=int(fields['oom_kill'])
    if kills<0:raise ValueError('Negative OOM counter')
    current=int((group/'memory.current').read_text());limit=(group/'memory.max').read_text().strip()
    if identity_start(proc,pid)!=start or group.stat().st_ino!=inode:raise ValueError('Process/cgroup changed during sample')
    return {'binding':bound,'container_id':cid,'container_started_at':state.get('StartedAt'),'container_pid':pid,
        'process_start_ticks':start,'boot_id':(proc/'sys/kernel/random/boot_id').read_text().strip(),
        'cgroup':str(group),'cgroup_inode':inode,'memory_events_local':{k:int(v) for k,v in fields.items()},
        'memory_current_bytes':current,'memory_max':limit,'started_at_unix':started,'finished_at_unix':time.time(),
        'container_running_observed':True,'command_completion':'not_established_by_resource_event'}


def transition(sample,previous):
    identity={k:sample[k] for k in ('container_id','container_started_at','boot_id','cgroup_inode')}
    count=sample['memory_events_local']['oom_kill'];before=previous.get(sample['container_id'])
    comparable=before is not None and before['identity']==identity and before['count']<=count
    event=None
    if count and (not comparable or count>before['count']):
        key=dict(identity,oom_kill_count=count)
        event=dict(sample,event_id=digest(json.dumps(key,sort_keys=True).encode()),
            kind='oom_kill_counter_increased' if comparable else 'existing_oom_kills_first_observed',
            prior_count=before['count'] if comparable else None,new_kills=count-before['count'] if comparable else None,
            prior_observed_at_unix=before['observed_at_unix'] if comparable else None,
            statement='The owned task container reports OOM-killed processes; command exit, victim ancestry and repair outcome are unknown.')
    next_value={'identity':identity,'count':count,'observed_at_unix':sample['finished_at_unix']}
    return event,next_value


def observe(root,hardware,proc=Path('/proc'),cgroups=Path('/sys/fs/cgroup'),inspect=None):
    """One bounded inspection plus small proc/cgroup reads per live task.

    Reuses Docker IDs already returned by the existing stats observation. No
    process scan, task command, new background process, or cancellation policy.
    """
    root=Path(root).resolve();started=time.perf_counter();known,errors=bindings(root)
    row={'started_at_unix':time.time(),'errors':errors,'samples':[],'events':[]}
    if not known:row.update(status='unknown' if errors else 'no_native_attempts',elapsed_s=time.perf_counter()-started);return row
    try:
        if hardware.get('status')!=0:raise ValueError('Docker stats inventory unavailable')
        ids=[]
        for line in hardware['output'].splitlines():
            value=json.loads(line);ident=value.get('Container') or value.get('ID')
            if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{12,64}',ident):raise ValueError('Unresolved Docker stats ID')
            ids.append(ident)
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate Docker stats ID')
        if inspect is None:
            result=subprocess.run(['sudo','docker','inspect','--format',INSPECT_FORMAT,*ids],capture_output=True,text=True,timeout=5) if ids else None
            if result is not None and result.returncode:raise RuntimeError('Docker inspection failed: '+result.stderr)
            items=[json.loads(line) for line in result.stdout.splitlines()] if result is not None else []
        else:items=inspect(ids)
        if not isinstance(items,list) or len(items)!=len(ids) or any(sum(item.get('Id','').startswith(ident) for item in items)!=1 for ident in ids):
            raise ValueError('Docker inspection did not cover the observed inventory')
        directory=root/'evidence';directory.mkdir(exist_ok=True)
        state_path=directory/'task-resource-state.json'
        state=json.loads(state_path.read_text()) if state_path.exists() else {'previous':{},'latest_events':{}}
        for item in items:
            try:
                matches=[known[mount['Source']] for mount in item.get('Mounts',[]) if mount.get('Type')=='bind' and mount.get('RW') is True
                         and mount.get('Destination')=='/app' and mount.get('Source') in known]
                if not matches:continue
                if len(matches)!=1 or item.get('Image')!=matches[0]['image_id']:raise ValueError('Task/container image binding mismatch')
                sample=container_sample(item,matches[0],proc,cgroups);row['samples'].append(sample)
                event,next_value=transition(sample,state['previous']);state['previous'][sample['container_id']]=next_value
                if event:
                    row['events'].append(event);state['latest_events'][sample['container_id']]=event
                    with (directory/'task-resource-events.jsonl').open('a') as output:output.write(json.dumps(event)+'\n')
            except (OSError,ValueError,KeyError,IndexError,TypeError) as error:
                row['errors'].append({'container_id':item.get('Id'),'error':type(error).__name__+': '+str(error)})
        write(state_path,state)
        write(directory/'task-resource-events.json',{'events':list(state['latest_events'].values()),
              'scope':'Latest observed OOM count per exact task container; full event history retained separately. No command failure or stopping decision.'})
        row['status']='partial' if row['errors'] and row['samples'] else 'unknown' if row['errors'] else 'observed'
    except (OSError,ValueError,KeyError,IndexError,TypeError,AttributeError,RuntimeError,subprocess.TimeoutExpired) as error:
        row['errors'].append({'error':type(error).__name__+': '+str(error)});row['status']='unknown'
    row.update(finished_at_unix=time.time(),elapsed_s=time.perf_counter()-started)
    return row


def surface(run,job,payload):
    """Existing supervisor reporting only: no phase/outcome/marker changes."""
    run=Path(run);value=json.loads(payload);seen_path=run/'task-resource-events-seen.json'
    seen=set(json.loads(seen_path.read_text())) if seen_path.exists() else set();new=[]
    for event in value.get('events',[]):
        if event['binding']['job']!=job['id'] or event['event_id'] in seen:continue
        seen.add(event['event_id']);new.append(event)
        with (run/'task-resource-events.jsonl').open('a') as output:output.write(json.dumps(event)+'\n')
    if new:
        write(seen_path,sorted(seen))
        write(run/'task-resource-attention.json',{'job':job['id'],'observed_at_unix':time.time(),'events':new,
            'assessment_needed':True,'execution_changed':False,'scope':'Resource failure evidence; existing execution remains under its original owner. Command status is not inferred.'})
    return new
