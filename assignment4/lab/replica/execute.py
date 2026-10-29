"""Execute a frozen iteration on the CPU host; stock runner owns all task effects."""
import argparse
from datetime import datetime,timezone
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import processes


def check_job_writers(root):
    """Failure-only guard: Docker daemon writers can outlive their host clients."""
    root=root.resolve();receipt={'job_root':str(root),'at_unix':time.time(),'state':'unknown','containers':[]}
    try:
        ids=subprocess.check_output(['docker','ps','--all','--quiet','--no-trunc'],text=True).split()
        receipt['inventory_ids']=ids
        if any(len(value)!=64 or any(c not in '0123456789abcdef' for c in value) for value in ids) or len(ids)!=len(set(ids)):
            raise RuntimeError('Invalid Docker container inventory')
        inspected=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True)) if ids else []
        if not isinstance(inspected,list) or len(inspected)!=len(ids) or {item.get('Id') for item in inspected}!=set(ids):
            raise RuntimeError('Docker inspection did not cover the inventory')
        receipt['inspection']=[{'id':item['Id'],'state':item.get('State'),'mounts':item.get('Mounts'),'image':item.get('Image')} for item in inspected]
        for item in inspected:
            state=item.get('State');mounts=item.get('Mounts')
            if not isinstance(state,dict) or state.get('Status') not in ('created','running','paused','restarting','removing','exited','dead'):
                raise RuntimeError('Unknown Docker container state')
            if any(type(state.get(key)) is not bool for key in ('Running','Paused','Restarting')) or not isinstance(mounts,list):
                raise RuntimeError('Incomplete Docker state/mount inspection')
            overlap=[]
            for mount in mounts:
                if not isinstance(mount,dict) or mount.get('Type') not in ('bind','volume','tmpfs') or type(mount.get('RW')) is not bool:
                    raise RuntimeError('Unknown Docker mount metadata')
                if mount['Type']=='bind':
                    source=mount.get('Source')
                    if not isinstance(source,str) or not Path(source).is_absolute():raise RuntimeError('Invalid Docker bind source')
                    path=Path(source).resolve()
                    if mount['RW'] and (path.is_relative_to(root) or root.is_relative_to(path)):
                        overlap.append({'source':source,'resolved_source':str(path),'destination':mount.get('Destination'),'rw':True})
            pending=state['Status'] not in ('exited','dead') or any(state[key] for key in ('Running','Paused','Restarting'))
            receipt['containers'].append({'id':item['Id'],'state':state['Status'],'running':state['Running'],
                'paused':state['Paused'],'restarting':state['Restarting'],'overlapping_writable_binds':overlap,
                'writer_or_pending':bool(pending and overlap)})
        receipt['state']='writers_present' if any(item['writer_or_pending'] for item in receipt['containers']) else 'no_job_writers_observed'
        if receipt['state']=='writers_present':raise RuntimeError('Docker containers retain writable job mounts; cleanup requires reconciliation')
    except BaseException as error:
        receipt['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        (root/'container-writer-check.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def execute(root,endpoint,wait_for_model_launch=False):
    from iteration import verify,write,file_digest
    root=root.resolve();experiment=root/'iteration';config=verify(experiment)
    from course_api import validate
    external=validate(config)
    if external and endpoint!=config['course_api']['origin']:raise ValueError('Course API endpoint differs from frozen route')
    if wait_for_model_launch and (external or config['stages'] not in (['native'],['task'])):
        raise ValueError('Model-launch waiting requires one self-hosted native or task stage')
    with (root/'executor.claim').open('x') as claim:claim.write(str(os.getpid()))
    logs=root/'iteration-logs';logs.mkdir(exist_ok=True)
    events=logs/'events.jsonl'
    def event(kind,**fields):
        value={'at':datetime.now(timezone.utc).isoformat(),'event':kind,**fields}
        with events.open('a') as out:out.write(json.dumps(value)+'\n')
        return value
    stages=[];active=None;cancelled=False
    (root/'executor.pid').write_text(str(os.getpid()))
    def interrupt(signum,frame):
        nonlocal cancelled
        cancelled=True
        if active is not None:processes.stop(active)
    signal.signal(signal.SIGTERM,interrupt)
    try:
        event('waiting_for_host')
        while not (root/'cpu-bootstrap.complete').exists():
            if cancelled or (root/'bootstrap.failed').exists() or (root/'server.failed').exists():raise RuntimeError('CPU preparation cancelled or infrastructure failed')
            time.sleep(5)
        if cancelled or (root/'server.failed').exists():raise RuntimeError('CPU preparation cancelled or infrastructure failed')
        probe=subprocess.run(['docker','info'],capture_output=True,text=True)
        (logs/'docker-info.json').write_text(json.dumps({'exit_code':probe.returncode,'stdout':probe.stdout,'stderr':probe.stderr},indent=2))
        if probe.returncode:raise RuntimeError('CPU host Docker is not usable in this execution session; no model calls admitted')
        from readiness import startup_deadline
        model_deadline=None if wait_for_model_launch or external else startup_deadline()
        if external:
            event('external_course_route',route=config['course_api'],self_hosted_readiness=False)
        elif config['stages'] not in (['task'], ['native']):
            event('waiting_for_model')
            from readiness import wait
            observed=wait(endpoint,lambda:cancelled or (root/'server.failed').exists(),model_deadline)
            event('model_ready',**observed)
        else:
            event('overlapping_task_preparation_and_serving_startup',stage=config['stages'][0])
        key=root/'serve.key';common=['--experiment',str(experiment),'--base-url',endpoint,'--key-file',str(key)]
        commands={
            'course_api':[sys.executable,str(Path(__file__).resolve().parent/'course_api.py'),'run','--root',str(root),'--key-file',str(key)],
            'native':[sys.executable,str(Path(__file__).resolve().parent/'native_job.py'),'--root',str(root),'--endpoint',endpoint],
            'replay':([sys.executable,str(Path(__file__).resolve().parent/'iteration.py'),'replay',*common,'--output',str(logs/'replay'),'--exclusive-endpoint','--concurrency',str(config['replay']['concurrency']),'--decode-tokens',str(config['replay']['decode_tokens'])] if 'replay' in config['stages'] else []),
            'task':[sys.executable,str(Path(__file__).resolve().parent/'iteration.py'),'task',*common,'--cache',str(root/'cache'),'--first-name',os.environ.get('A4_STUDENT_FIRST_NAME','Development'),'--last-name',os.environ.get('A4_STUDENT_LAST_NAME','Run')],
        }
        for name in config['stages']:
            if cancelled or (root/'server.failed').exists():raise RuntimeError('CPU preparation cancelled or infrastructure failed')
            event('stage_started',stage=name);started=time.monotonic()
            stage_env=dict(os.environ)
            stage_env.pop('A4_ENGINE_STARTUP_DEADLINE',None);stage_env.pop('A4_WAIT_FOR_MODEL_LAUNCH',None)
            if wait_for_model_launch:stage_env['A4_WAIT_FOR_MODEL_LAUNCH']='1'
            elif not external:stage_env['A4_ENGINE_STARTUP_DEADLINE']=str(model_deadline)
            with (logs/(name+'.log')).open('wb') as log:
                active=processes.start(commands[name],cwd=root,stdout=log,stderr=subprocess.STDOUT,
                    env=stage_env)
                if cancelled:processes.stop(active)
                returncode,abandoned=processes.wait(active)
            receipt=event('stage_finished',stage=name,exit_code=returncode,elapsed_s=time.monotonic()-started,
                          owner_exited_with_live_descendants=abandoned)
            stages.append(receipt);write(root/'experiment.json',{'stages':stages,'frozen_experiment':file_digest(experiment/'experiment.json')})
            if returncode or cancelled or abandoned:raise RuntimeError(name+' failed; inspect retained output')
            if name=='course_api':check_job_writers(root)
            if name=='native':
                # The sweep records per-attempt failures even when its stage exits
                # zero. Preserve missing candidates before declaring stage closure.
                from interrupted_capture import preserve
                preserve(root,reason='native_stage_ended_without_complete_candidate')
        (root/'experiment.complete').write_text('complete\n');event('complete')
    except BaseException as error:
        if active is not None:
            try:
                processes.stop_and_wait(active)
                if config['stages']==['native']:
                    from interrupted_capture import preserve
                    preserve(root,reason='executor_interruption' if cancelled else 'native_stage_failure')
                check_job_writers(root)
            except BaseException as cleanup_error:
                receipt=event('cleanup_unconfirmed',original_error=type(error).__name__+': '+str(error),
                              error=type(cleanup_error).__name__+': '+str(cleanup_error))
                write(root/'cleanup-unconfirmed.json',receipt)
                raise
        event('failed',error=type(error).__name__+': '+str(error))
        (root/'experiment.failed').write_text(type(error).__name__+': '+str(error))
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path('/opt/a4-replica'));p.add_argument('--endpoint',required=True);p.add_argument('--wait-for-model-launch',action='store_true');a=p.parse_args();execute(a.root,a.endpoint.rstrip('/'),a.wait_for_model_launch)
