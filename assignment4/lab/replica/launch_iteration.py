"""Run a bounded batch of frozen experiments on one owned, warm two-host environment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import signal
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from course_api import PROFILE,DEFAULT_PROFILE,roles,validate,verify_course,protected_key

HERE=Path(__file__).resolve().parent


def early_preparation(config):
    """Native's historical default remains; task overlap requires a frozen opt-in."""
    enabled=config.get('early_cpu_preparation',config.get('stages')==['native'])
    if type(enabled) is not bool:raise ValueError('early_cpu_preparation must be boolean')
    if enabled and (config.get('stages') not in (['native'],['task'])
                    or config.get('resource_profile',DEFAULT_PROFILE)!=DEFAULT_PROFILE):
        raise ValueError('Early CPU preparation requires one self-hosted native or task stage')
    return enabled


def aws(*args):
    raw=subprocess.check_output(['aws',*args,'--region','us-east-1','--output','json'])
    return json.loads(raw) if raw.strip() else {}


def resource_workers(config):
    """Outer task slots consume host resources independently of solver workers."""
    workers = int(config['environment']['A4_WORKERS'])
    admission = config.get('ahe_admission')
    if admission is not None:
        lanes = admission.get('concurrency')
        if type(lanes) is not int or lanes not in (1, 2, 5, 10):
            raise ValueError('Unsupported AHE resource concurrency')
        runner = config.get('runner', [])
        if runner[:3] != ['python3', 'evaluation_scripts/run_ahe_lanes.py', '--concurrency'] or len(runner) < 4 or runner[3] != str(lanes):
            raise ValueError('AHE runner and resource concurrency differ')
        workers *= lanes
    return workers


def allocation_shape(jobs):
    """Keep attempts for evidence/accounting; unique frozen tasks size image storage."""
    tasks = set()
    for job in jobs:
        manifest = Path(job['experiment']) / 'course/evaluation_scripts/agent_task_input.json'
        entries = json.loads(manifest.read_text())
        if len(entries) != job['task_count']:
            raise ValueError('Plan task count differs from frozen manifest')
        tasks.update(json.dumps(task, sort_keys=True, separators=(',', ':')) for task in entries.values())
    if len({job.get('cpu_type') for job in jobs}) != 1:
        raise ValueError('One common CPU type required per allocation')
    disks = {job.get('cpu_disk_gb') for job in jobs}
    if len(disks) != 1 or any(d is not None and (type(d) is not int or d < 200) for d in disks):
        raise ValueError('One common CPU storage allocation of at least 200 GiB required')
    return {'workers': max(job.get('resource_workers', job['workers']) for job in jobs),
            'task_count': sum(job['task_count'] for job in jobs), 'distinct_task_count': len(tasks)}


def plan(experiments):
    import iteration
    import server_reuse
    import image_snapshot
    iteration.require_serving_executable(HERE/'a4-serve')
    jobs=[];tooling=None
    for index,path in enumerate(experiments):
        frozen=iteration.verify(path.resolve())
        if frozen.get('stages') != ['task']:
            raise ValueError('The maintained Python replica accepts task experiments; use the preserved project for native/replay campaigns')
        external=validate(frozen)
        if frozen.get('resource_profile',DEFAULT_PROFILE) not in (DEFAULT_PROFILE,PROFILE):raise ValueError('Builder profile is not an experiment')
        image_artifact=image_snapshot.policy(frozen,HERE/'Dockerfile.gpu')
        reuse=server_reuse.policy(frozen)
        early=early_preparation(frozen)
        if external:verify_course(path.resolve(),frozen)
        stop_on_failure=frozen.get('stop_batch_on_failure',False)
        if type(stop_on_failure) is not bool:raise ValueError('stop_batch_on_failure must be boolean')
        if jobs and stop_on_failure!=jobs[0]['stop_batch_on_failure']:raise ValueError('Batch experiments require the same stop_batch_on_failure policy')
        if not (path/'tools').is_dir():raise ValueError('Prepare a new snapshot with frozen tooling')
        if 'native' in frozen['stages']:
            native=path/'native-comparison'
            pins=json.loads((native/'baselines.json').read_text())
            # Refuse paid allocation until actual native tools have run inside
            # every selected task image with the exact current adapter/assets.
            from preflight_proof import verify
            manifest=path/'course/evaluation_scripts/agent_task_input.json'
            verify(path/'preflight',manifest,native)
            for arm in ('swe-agent','jcode'):
                upstream=native/'upstream'/arm
                head=subprocess.check_output(['git','-C',str(upstream),'rev-parse','HEAD'],text=True).strip()
                dirty=subprocess.check_output(['git','-C',str(upstream),'status','--porcelain'],text=True).strip()
                if head!=pins[arm]['commit'] or dirty:
                    raise ValueError('Native snapshot lost its pinned clean Git state before allocation: '+arm+' '+dirty)
        for name,expected in frozen['tooling_files'].items():
            if iteration.file_digest(HERE/name)!=expected:raise ValueError('Use this experiment\'s frozen tools or prepare a new snapshot: '+name)
        if tooling is not None and tooling!=frozen['tooling_files']:raise ValueError('Batch measurements require the same tooling version')
        tooling=frozen['tooling_files']
        jobs.append({'id':f'{index:03d}','experiment':str(path.resolve()),'manifest_sha256':iteration.file_digest(path/'experiment.json'),
                     'resource_profile':frozen.get('resource_profile',DEFAULT_PROFILE),'course_api':frozen.get('course_api'),
                     'workers':int(frozen['environment']['A4_WORKERS']),'resource_workers':resource_workers(frozen),'task_count':frozen['task_count'],'lab_root':frozen['lab_root'],'cpu_type':frozen.get('cpu_type'),'cpu_disk_gb':frozen.get('cpu_disk_gb'),'stop_batch_on_failure':stop_on_failure,'reuse_server':reuse,'early_cpu_preparation':early,'image_snapshot':image_artifact,'image_materialization':frozen.get('image_materialization')})
    if not jobs:raise ValueError('At least one experiment required')
    if any((j['image_snapshot'],j['image_materialization'])!=(jobs[0]['image_snapshot'],jobs[0]['image_materialization']) for j in jobs):raise ValueError('One exact image artifact and materialization per fresh-host batch required')
    if len({j['resource_profile'] for j in jobs})!=1:raise ValueError('Do not mix resource profiles in one batch')
    if jobs[0]['resource_profile']==PROFILE and any((j['course_api'],j['cpu_type'])!=(jobs[0]['course_api'],jobs[0]['cpu_type']) for j in jobs):
        raise ValueError('Course API batch requires one route and host')
    if len({j['lab_root'] for j in jobs})!=1:raise ValueError('Use one local registry per batch')
    if any(j['reuse_server'] for j in jobs):
        for job in jobs:server_reuse.supported(iteration.verify(Path(job['experiment'])))
    return jobs


def bundle(jobs, output, gpu_only=False):
    import iteration
    iteration.require_serving_executable(HERE/'a4-serve')
    with tarfile.open(fileobj=output,mode='w:gz') as archive:
        if jobs[0].get('image_snapshot') is not None:
            import io
            raw=(json.dumps(jobs[0]['image_snapshot'],indent=2)+'\n').encode()
            member=tarfile.TarInfo('image-snapshot.json');member.size=len(raw);member.mode=0o644
            archive.addfile(member,io.BytesIO(raw))
            raw=(jobs[0]['image_materialization']+'\n').encode();member=tarfile.TarInfo('image-materialization');member.size=len(raw);member.mode=0o644;archive.addfile(member,io.BytesIO(raw))
        for job in jobs:
            snapshot=Path(job['experiment']);frozen=iteration.verify(snapshot)
            for name in [*frozen['files'],'experiment.json']:
                if gpu_only and name!='experiment.json' and not name.startswith('course/engine/'):continue
                member=Path(name)
                if member.is_absolute() or '..' in member.parts:raise ValueError('Invalid frozen member path')
                archive.add(snapshot/member,arcname='jobs/'+job['id']+'/iteration/'+name)
            for link in snapshot.rglob('*'):
                if link.is_symlink() and str(link.relative_to(snapshot)) not in frozen['files'] and (not gpu_only or str(link.relative_to(snapshot)).startswith('course/engine/')):
                    archive.add(link,arcname='jobs/'+job['id']+'/iteration/'+str(link.relative_to(snapshot)))
        for tool in HERE.iterdir():
            if tool.is_file() and (tool.suffix in ('.py','.sh') or tool.name in ['a4-serve','Dockerfile.gpu']):archive.add(tool,arcname=tool.name)


def claim_supervisor(run):
    descriptor=os.open(run/'supervisor.lock',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:os.write(descriptor,str(os.getpid()).encode())
    finally:os.close(descriptor)


def server_start_command(path):
    """A required service ending is observable even when its launcher exits zero."""
    root='/opt/a4-replica'
    publish="""import json, os, sys, time
from pathlib import Path
job, root, status = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
controlled = (root/'controlled-shutdown.json').is_file()
receipt = {'exit_code': status, 'recorded_at_unix': time.time(),
           'boundary': 'start-gpu.sh returned', 'controlled_shutdown': controlled}
pending = job/'server-exit.pending'
with pending.open('x') as output:
    json.dump(receipt, output); output.flush(); os.fsync(output.fileno())
pending.replace(job/'server-exit.json')
if not controlled: (job/'server.failed').touch()
"""
    script=('cd '+shlex.quote(root)+' || exit; status=0; A4_RUNTIME='+shlex.quote(path+'/runtime.json')+
            ' A4_EXPERIMENT='+shlex.quote(path+'/iteration')+' bash start-gpu.sh || status=$?; '+
            'printf "Serving launcher returned status %s\\n" "$status"; '+
            'python3 -c '+shlex.quote(publish)+' '+shlex.quote(path)+' '+shlex.quote(root)+' "$status"')
    return 'nohup bash -c '+shlex.quote(script)+' > '+shlex.quote(path+'/server.log')+' 2>&1 < /dev/null &'



def observe_task_resources(run,job,transport):
    """Bounded read-only observation; never refresh access or alter job phase."""
    from iteration import write
    observed={'job':job['id'],'at':time.time(),'state':'unknown'}
    try:
        command='if test -f /opt/a4-replica/evidence/task-resource-events.json; then cat /opt/a4-replica/evidence/task-resource-events.json; fi'
        # Do not use Transport.ssh: its access-refresh side effect is appropriate
        # for controller commands, but not for this optional observation.
        result=subprocess.run(transport.args('cpu',command),capture_output=True,timeout=3)
        if result.returncode:raise RuntimeError('Resource observation read exited '+str(result.returncode))
        if result.stdout.strip():
            from task_resource_events import surface
            events=surface(run,job,result.stdout)
            observed.update(state='observed',new_event_ids=[event['event_id'] for event in events])
        else:observed['state']='no_resource_receipt_observed'
    except (OSError,ValueError,KeyError,TypeError,AttributeError,RuntimeError,subprocess.TimeoutExpired) as error:
        observed['error']=type(error).__name__+': '+str(error)
    try:
        path=Path(run)/'task-resource-observation.json'
        previous=json.loads(path.read_text()) if path.exists() else {}
        if observed.get('error') and (observed['job'],observed['error'])!=(previous.get('job'),previous.get('error')):
            with (Path(run)/'task-resource-observation-errors.jsonl').open('a') as output:output.write(json.dumps(observed)+'\n')
        write(path,observed)
    except (OSError,ValueError,TypeError):pass  # Reporting failure cannot stop execution/collection.
    return observed


def reuse_transition(run, transport, producer, previous, successor):
    """One bounded handoff before next executor dispatch; uncertainty is never retried."""
    from iteration import write
    directory=run/'server-transitions';directory.mkdir(exist_ok=True)
    receipt={'producer_job':producer['id'],'previous_job':previous['id'],'successor_job':successor['id'],
             'started_at_unix':time.time(),'state':'blocked'}
    try:
        def invoke(role,argv,timeout):
            result=transport.ssh(role,'python3 /opt/a4-replica/server_reuse.py '+shlex.join(argv),timeout=timeout)
            if result.returncode:
                # Preserve any complete helper receipt, including its blocked state.
                if result.stdout.strip():
                    try:receipt['remote_receipt']=json.loads(result.stdout)
                    except ValueError:pass
                raise RuntimeError(role+' reuse boundary failed: '+result.stderr.decode(errors='replace')[-1000:])
            return json.loads(result.stdout)
        completed=invoke('cpu',['completion','--job',previous['id']],75)
        value=invoke('gpu',['transition','--producer',producer['id'],'--previous',previous['id'],
                            '--successor',successor['id'],'--completion',json.dumps(completed)],180)
        if (value.get('successor_job')!=successor['id'] or value.get('producer_job')!=producer['id']
                or value.get('previous_job')!=previous['id'] or value.get('state') not in ('reused','restart_ready')):
            raise ValueError('Missing or mismatched server handoff receipt')
        receipt.update(value)
        # CPU archive references the actual producer; no fake launch/runtime copy.
        code='from pathlib import Path; from server_reuse import write_once; import json; write_once(Path('+repr('/opt/a4-replica/jobs/'+successor['id']+'/server-reuse.json')+'),json.loads('+repr(json.dumps(value))+'))'
        saved=transport.ssh('cpu','cd /opt/a4-replica && python3 -c '+shlex.quote(code),timeout=35)
        if saved.returncode:raise RuntimeError('CPU handoff receipt publication unconfirmed')
        return value
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as error:
        receipt.update(state='blocked',error=type(error).__name__+': '+str(error))
        raise
    finally:
        receipt['observed_at_unix']=time.time();write(directory/(successor['id']+'.json'),receipt)


def supervise(run):
    claim_supervisor(run)
    state=json.loads((run/'resources.json').read_text());res=state['resources'];jobs=json.loads((run/'batch.json').read_text())['jobs']
    host_roles=roles(state);external=host_roles==('cpu',)
    if set(r for r in ('gpu','cpu') if r in res)!=set(host_roles):raise ValueError('Resource inventory differs from declared profile')
    if any(j.get('resource_profile',DEFAULT_PROFILE)!=state.get('resource_profile',DEFAULT_PROFILE) for j in jobs):raise ValueError('Plan/resource profile mismatch')
    configs={}
    for job in jobs:
        if not job.get('experiment'):continue
        manifest=Path(job['experiment'])/'experiment.json'
        config=json.loads(manifest.read_text());configs[job['id']]=config
        if job.get('manifest_sha256') and hashlib.sha256(manifest.read_bytes()).hexdigest()!=job['manifest_sha256']:
            raise ValueError('Frozen experiment differs from launch plan')
        if job.get('early_cpu_preparation',early_preparation(config))!=early_preparation(config):
            raise ValueError('Frozen early CPU preparation policy differs from launch plan')
    if any(job.get('reuse_server',False) for job in jobs):
        from server_reuse import policy,supported
        from iteration import verify,file_digest
        for job in jobs:
            snapshot=Path(job['experiment']);config=verify(snapshot);supported(config)
            if policy(config)!=job.get('reuse_server',False) or file_digest(snapshot/'experiment.json')!=job['manifest_sha256']:
                raise ValueError('Frozen server-reuse policy/manifest differs from plan')
    def record(phase,**kw):
        from iteration import write
        write(run/'progress.json',{'phase':phase,'at':time.time(),**kw})
    from owned_transport import Transport
    transport=None
    def command(role,cmd,check=True):
        r=transport.ssh(role,cmd,timeout=None)
        r.stdout=r.stdout.decode(errors='replace');r.stderr=r.stderr.decode(errors='replace')
        if check and r.returncode:raise RuntimeError(role+': '+r.stderr[-1000:])
        return r
    def copy(role,path,destination,observation=None):
        # Replacing the same immutable upload is safe; this does not retry a job.
        attempts=[]
        while True:
            started=time.time()
            result=subprocess.run(['scp','-q','-i',str(run/'ssh-key'),'-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3','-o','UserKnownHostsFile='+str(run/'known_hosts'),str(path),'ubuntu@'+res[role]['public_ip']+':'+destination])
            if observation is not None:
                from iteration import write
                attempts.append({'started_at_unix':started,'finished_at_unix':time.time(),'exit_code':result.returncode})
                write(observation,{'source_bytes':path.stat().st_size,'destination':destination,'attempts':attempts,'state':'uploaded' if result.returncode==0 else 'upload_failed_or_partial'})
            if result.returncode==0:return
            transport.refresh_access()
            if transport.instance(role)['State']['Name'] in ('terminated','shutting-down'):raise RuntimeError('Owned host ended during upload')
            time.sleep(5)
    def present(role,path):
        # Only a completed test distinguishes absent from present. Transport loss
        # says neither; it must not enter the failure handler and cancel a job.
        while True:
            try:
                result=transport.ssh(role,'test -f '+shlex.quote(path),timeout=30)
                if result.returncode in (0,1):return result.returncode==0
                detail={'returncode':result.returncode,'stderr':result.stderr.decode(errors='replace')[-1000:]}
            except (OSError,subprocess.SubprocessError) as error:
                detail={'error':type(error).__name__+': '+str(error)}
            with (run/'marker-observation-errors.jsonl').open('a') as output:
                output.write(json.dumps({'at':time.time(),'role':role,'path':path,**detail})+'\n')
            # No mutation is retried here. Signals and the independent
            # collector retain the existing ownership and stop paths.
            time.sleep(5)
    controller=None;active_job=None;dispatched=set();outcomes=[];server_producer=None
    def finish_batch():
        marker='complete' if all(item['execution_completed'] for item in outcomes) else 'failed'
        command('cpu',"printf '%s' "+"'"+json.dumps({'jobs':outcomes})+"' > /opt/a4-replica/experiment.json; touch /opt/a4-replica/experiment."+marker)
        record('batch finished; collecting and shutting down',outcomes=outcomes)
        controller.wait()
        if controller.returncode:raise RuntimeError('Collection/cleanup failed; inspect controller log')
        record('collected and cleaned up',outcomes=outcomes)
    def stop_failed_batch(job):
        """A confirmed terminal failure closes later admission only when frozen explicitly."""
        nonlocal active_job
        if not job.get('stop_batch_on_failure',False):return False
        outcomes.append({'job':job['id'],'execution_completed':False})
        outcomes.extend({'job':remaining['id'],'execution_completed':False,'not_started':True,
                         'reason':'earlier_job_failed','failed_job':job['id']} for remaining in jobs[jobs.index(job)+1:])
        active_job=None
        record('batch stopped after confirmed job failure',job=job['id'],outcomes=outcomes)
        finish_batch()
        return True
    def native_job(job):
        return configs.get(job['id'],{}).get('stages')==['native']
    def early_job(job):
        return not external and early_preparation(configs.get(job['id'],{}))
    def launch_gated_job(job):
        return not external and configs.get(job['id'],{}).get('stages') in (['native'],['task'])
    def start_cpu(job,launch_wait=True):
        nonlocal active_job
        if job['id'] in dispatched:raise RuntimeError('Refusing duplicate CPU executor dispatch')
        path='/opt/a4-replica/jobs/'+job['id']
        command('cpu','set -e; ln -s /opt/a4-replica/cache '+path+'/cache; ln -s /opt/a4-replica/serve.key '+path+'/serve.key; ln -s /opt/a4-replica/cpu-bootstrap.complete '+path+'/cpu-bootstrap.complete')
        dispatched.add(job['id']);active_job=job
        command('cpu','nohup python3 -u /opt/a4-replica/execute.py --root '+path+' --endpoint '+endpoint+
                (' --wait-for-model-launch' if launch_wait and launch_gated_job(job) else '')+' > '+path+'/supervisor.log 2>&1 < /dev/null & executor_pid=$!; printf "%s" "$executor_pid" > '+path+'/executor-dispatch.pid')
    def stop_cpu(job):
        # Wait for the executor's group cleanup before the collector seals CPU evidence.
        path='/opt/a4-replica/jobs/'+job['id']
        command('cpu','touch '+path+'/server.failed; if test ! -f '+path+'/experiment.complete && test ! -f '+path+'/experiment.failed; then '+
            'executor_pid=""; if test -f '+path+'/executor-dispatch.pid; then executor_pid=$(cat '+path+'/executor-dispatch.pid); '+
            'elif test -f '+path+'/executor.pid; then executor_pid=$(cat '+path+'/executor.pid); fi; '+
            'if test -n "$executor_pid"; then kill -TERM "$executor_pid" 2>/dev/null || true; '+
            'while kill -0 "$executor_pid" 2>/dev/null && test ! -f '+path+'/experiment.failed && test ! -f '+path+'/experiment.complete; do sleep 1; done; '+
            'else echo "Executor dispatch is uncertain; owner PID unavailable" >&2; exit 3; fi; '+
            'if test ! -f '+path+'/experiment.failed && test ! -f '+path+'/experiment.complete; then echo "Executor ended without confirmed group cleanup" >&2; exit 3; fi; fi')
    previous_term=signal.getsignal(signal.SIGTERM)
    def interrupted(signum,frame):raise RuntimeError('Supervisor interrupted by signal '+str(signum))
    signal.signal(signal.SIGTERM,interrupted)
    try:
        record('waiting for instance network')
        for role in host_roles:
            while True:
                instance=aws('ec2','describe-instances','--instance-ids',res[role]['id'])['Reservations'][0]['Instances'][0]
                if instance['State']['Name'] in ['terminated','shutting-down']:raise RuntimeError('Instance ended before becoming reachable')
                if instance.get('PublicIpAddress'):
                    res[role].update(public_ip=instance['PublicIpAddress'],private_ip=instance['PrivateIpAddress']);break
                time.sleep(5)
        (run/'resources.json').write_text(json.dumps(state,indent=2)+'\n')
        transport=Transport(run)
        controller=subprocess.Popen([sys.executable,'-u',str(HERE/'collect_and_cleanup.py'),str(run)],stdout=(run/'controller.log').open('ab'),stderr=subprocess.STDOUT,start_new_session=True)
        state['controller_pid']=controller.pid;(run/'resources.json').write_text(json.dumps(state,indent=2)+'\n')
        for role in host_roles:
            while command(role,'true',check=False).returncode:time.sleep(5)
            command(role,'sudo mkdir -p /opt/a4-replica; sudo chown ubuntu:ubuntu /opt/a4-replica')
            copy(role,run/('gpu-payload.tar.gz' if role=='gpu' else 'payload.tar.gz'),'/opt/a4-replica/payload.tar.gz')
            command(role,'cd /opt/a4-replica && tar -xzf payload.tar.gz')
            if role=='gpu' and jobs[0].get('image_materialization')=='archive':
                archive=jobs[0]['image_snapshot']['archive']
                if 'object' in archive:
                    from image_object import issue_capability,CAPABILITY_NAME
                    from iteration import write
                    capability=run/CAPABILITY_NAME
                    try:
                        receipt=issue_capability(jobs[0]['image_snapshot'],capability)
                        copy(role,capability,'/opt/a4-replica/'+CAPABILITY_NAME)
                        command(role,'chmod 600 /opt/a4-replica/'+CAPABILITY_NAME)
                        write(run/'image-object-capability-issued.json',receipt)
                    finally:capability.unlink(missing_ok=True)
                else:
                    from image_snapshot import file_hash
                    started=time.time()
                    if file_hash(archive['path'])!=archive['sha256']:raise ValueError('Image archive changed after admission')
                    verified=time.time()
                    observation=run/'image-archive-transfer.json'
                    copy(role,Path(archive['path']),'/opt/a4-replica/image.tar',observation)
                    from iteration import write
                    receipt=json.loads(observation.read_text());receipt.update(source_sha256=archive['sha256'],source_validation_started_at_unix=started,source_validation_finished_at_unix=verified)
                    write(observation,receipt)
                    command(role,'mkdir -p /opt/a4-replica/evidence && printf %s '+shlex.quote(json.dumps(receipt))+' > /opt/a4-replica/evidence/image-archive-transfer.json')
            copy(role,run/'serve.key','/opt/a4-replica/serve.key')
            command(role,'chmod 600 /opt/a4-replica/serve.key')
            command(role,'nohup python3 /opt/a4-replica/telemetry.py --role '+role+' > /opt/a4-replica/telemetry.log 2>&1 < /dev/null &')
        record('installing CPU host dependencies' if external else 'installing host dependencies and pinned model')
        if not external:command('gpu',"nohup bash -c '(if ! command -v docker >/dev/null; then sudo apt-get update -qq && sudo apt-get install -y docker.io; fi) && sudo systemctl enable --now docker && bash /opt/a4-replica/bootstrap-gpu.sh || touch /opt/a4-replica/bootstrap.failed' > /opt/a4-replica/bootstrap.log 2>&1 < /dev/null &")
        command('cpu',"nohup bash -c 'bash /opt/a4-replica/bootstrap-cpu.sh || touch /opt/a4-replica/bootstrap.failed' > /opt/a4-replica/bootstrap.log 2>&1 < /dev/null &")
        endpoint=jobs[0]['course_api']['origin'] if external else 'http://'+res['gpu']['private_ip']+':30000';ready=set()
        while len(ready)<len(host_roles):
            for role in reversed(host_roles):
                if present(role,'/opt/a4-replica/bootstrap.failed'):raise RuntimeError(role+' bootstrap failed')
                if present(role,'/opt/a4-replica/'+role+'-bootstrap.complete'):ready.add(role)
            if active_job and present('cpu','/opt/a4-replica/jobs/'+active_job['id']+'/experiment.failed'):
                if stop_failed_batch(active_job):return
                raise RuntimeError('Early CPU preparation failed; GPU server not dispatched')
            if 'cpu' in ready and early_job(jobs[0]) and jobs[0]['id'] not in dispatched:
                record('preparing CPU job during GPU bootstrap',job=jobs[0]['id']);start_cpu(jobs[0])
            if len(ready)<len(host_roles):time.sleep(10)
        for index,job in enumerate(jobs):
            path='/opt/a4-replica/jobs/'+job['id'];record('starting frozen experiment',job=job['id'])
            if job['id'] in dispatched and present('cpu',path+'/experiment.failed'):
                if stop_failed_batch(job):return
                raise RuntimeError('Early CPU preparation failed; GPU server not dispatched')
            handoff=None
            if not external and job.get('reuse_server',False) and server_producer is not None:
                handoff=reuse_transition(run,transport,server_producer,jobs[index-1],job)
            if not external and (handoff is None or handoff['state']=='restart_ready'):
                # Default restart is unchanged. Opt-in fallback already retired
                # exact original IDs and confirmed producer closure; never name-kill again.
                if handoff is None:command('gpu','sudo docker rm -f a4-stock-server a4-server-transport >/dev/null 2>&1 || true')
                command('gpu',server_start_command(path));server_producer=job
            # Reuse retains the original producer and its existing handoff proof;
            # only a newly accepted server dispatch gets a launch receipt.
            launch_wait=handoff is None or handoff['state']=='restart_ready'
            if launch_wait and launch_gated_job(job):
                code='from pathlib import Path; from readiness import record_launch; record_launch(Path('+repr(path)+'))'
                command('cpu','cd /opt/a4-replica && python3 -c '+shlex.quote(code))
            if job['id'] not in dispatched:start_cpu(job,launch_wait=launch_wait)
            while not any(present('cpu',path+'/'+n) for n in ['experiment.complete','experiment.failed']):
                if native_job(job):observe_task_resources(run,job,transport)
                if not external and present('gpu','/opt/a4-replica/jobs/'+server_producer['id']+'/server.failed'):
                    stop_cpu(job)
                time.sleep(10)
            failed=present('cpu',path+'/experiment.failed')
            if failed and stop_failed_batch(job):return
            outcomes.append({'job':job['id'],'execution_completed':not failed})
            active_job=None
            record('experiment ended',job=job['id'],execution_completed=not failed)
        finish_batch()
    except BaseException as error:
        record('supervisor failure',error=type(error).__name__+': '+str(error))
        if active_job:
            try:stop_cpu(active_job)
            except Exception as stop_error:
                record('executor cancellation unconfirmed; collection not admitted',error=type(stop_error).__name__+': '+str(stop_error))
                raise
        try:command('cpu',"echo 'Infrastructure supervisor failed' > /opt/a4-replica/experiment.failed",check=False)
        except Exception:pass
        raise
    finally:signal.signal(signal.SIGTERM,previous_term)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--course-api-key-file',type=Path);p.add_argument('--experiment',type=Path,action='append');p.add_argument('--supervise',type=Path);p.add_argument('--bundle-only',type=Path);a=p.parse_args()
    if a.supervise:supervise(a.supervise.resolve());raise SystemExit(0)
    if not a.experiment:p.error('--experiment required; repeat it for a warm batch')
    import iteration
    jobs=plan(a.experiment)
    if a.bundle_only:
        with a.bundle_only.open('xb') as output:bundle(jobs,output)
        print(json.dumps({'bundle':str(a.bundle_only.resolve()),'jobs':jobs,'resources_allocated':False}));raise SystemExit(0)
    external=jobs[0]['resource_profile']==PROFILE
    if external:
        if not a.course_api_key_file:p.error('--course-api-key-file required for external course route')
        credential=protected_key(a.course_api_key_file).read_text()
    elif a.course_api_key_file:p.error('External credential is not valid for a self-hosted batch')
    lab=Path(jobs[0]['lab_root']);(lab/'runs').mkdir(exist_ok=True)
    # Build and verify all local transport material before allocating paid resources.
    with tempfile.NamedTemporaryFile(dir=lab/'runs',suffix='.tar.gz') as temporary, tempfile.NamedTemporaryFile(dir=lab/'runs',suffix='.tar.gz') as gpu_payload:
        bundle(jobs,temporary)
        temporary.flush()
        if not external:bundle(jobs,gpu_payload,gpu_only=True);gpu_payload.flush()
        shape=allocation_shape(jobs)
        result=subprocess.check_output([sys.executable,str(HERE/'provision.py'),'--run-root',str(lab/'runs'),'--experiments',str(len(jobs)),'--workers',str(shape['workers']),'--task-count',str(shape['task_count']),'--distinct-task-count',str(shape['distinct_task_count']),*(['--cpu-type',jobs[0]['cpu_type']] if jobs[0].get('cpu_type') else []),*(['--cpu-disk-gb',str(jobs[0]['cpu_disk_gb'])] if jobs[0].get('cpu_disk_gb') is not None else []),*(['--resource-profile',PROFILE] if external else []),*(['--image-snapshot-json',json.dumps(jobs[0]['image_snapshot']),'--image-materialization',jobs[0]['image_materialization']] if jobs[0].get('image_snapshot') else [])],text=True)
        run=Path(result.splitlines()[0]);(run/'provision.log').write_text(result)
        shutil.copyfile(temporary.name,run/'payload.tar.gz')
        if not external:shutil.copyfile(gpu_payload.name,run/'gpu-payload.tar.gz')
    iteration.write(run/'batch.json',{'jobs':jobs,'payload_sha256':iteration.file_digest(run/'payload.tar.gz'),'gpu_payload_sha256':None if external else iteration.file_digest(run/'gpu-payload.tar.gz')})
    (run/'serve.key').write_text(credential if external else secrets.token_hex(32));(run/'serve.key').chmod(0o600)
    process=subprocess.Popen([sys.executable,'-u',str(HERE/'launch_iteration.py'),'--supervise',str(run)],stdout=(run/'supervisor.log').open('ab'),stderr=subprocess.STDOUT,start_new_session=True)
    (run/'supervisor.pid').write_text(str(process.pid))
    iteration.write(lab/'runs/latest.json',{'run_directory':str(run),'jobs':jobs,'supervisor_pid':process.pid,'phase':'provisioned; see registry progress and inspection'})
    print(json.dumps({'run':str(run),'supervisor_pid':process.pid,'experiments':len(jobs)}))
