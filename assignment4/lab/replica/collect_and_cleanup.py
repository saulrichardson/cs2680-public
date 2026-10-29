"""One persistent controller; seal, resume, verify and promptly release owned hosts."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time
from owned_transport import Transport
from collection import receive
from course_api import roles

def main(run):
    run=run.resolve();fd=os.open(run/'controller.lock',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
    (run/'controller.pid').write_text(str(os.getpid()))
    def log(event,**fields):
        with (run/'controller-events.jsonl').open('a') as f:f.write(json.dumps({'at':time.time(),'event':event,**fields})+'\n')
    transport=Transport(run,log);state=transport.state;resources=transport.resources
    host_roles=roles(state)
    if set(r for r in ('gpu','cpu') if r in resources)!=set(host_roles):raise ValueError('Resource inventory differs from declared profile')
    for role in host_roles:transport.instance(role)
    try:transport.refresh_access()
    except Exception as error:log('access_refresh_pending',error=type(error).__name__+': '+str(error))
    log('monitoring',pid=os.getpid())
    while True:
        try:
            result=transport.ssh('cpu','if test -f /opt/a4-replica/experiment.failed; then echo failed; elif test -f /opt/a4-replica/experiment.complete; then echo complete; else echo active; fi')
            if result.returncode==0 and result.stdout.strip() in (b'complete',b'failed'):
                log('experiment_ended',outcome=result.stdout.decode().strip());break
        except Exception as error:log('inspection_error',error=type(error).__name__+': '+str(error))
        if time.time()-datetime.fromisoformat(state['created_at']).timestamp()>state.get('infrastructure_safety_shutdown_hours',8)*3600:
            log('infrastructure_lifetime_reached');break
        time.sleep(15)
    # GPU evidence is small. Verify and terminate that expensive host before a
    # large CPU transfer, so a later interrupted download does not retain it.
    for role in host_roles:
        attempt=0
        while True:
            try:
                instance=transport.instance(role)
                prior=[json.loads(line) for line in (run/'controller-events.jsonl').read_text().splitlines()]
                previous=[e for e in prior if e.get('event')=='evidence_collected' and e.get('role')==role]
                if previous and (run/(role+'-evidence.tar.gz')).exists():
                    receipt=previous[-1];destination=receive(transport,role,run,receipt)
                else:
                    if instance['State']['Name'] in ('terminated','shutting-down'):
                        raise RuntimeError('Host ended without a verified local archive; evidence unavailable')
                    launch='nohup python3 /opt/a4-replica/seal_evidence.py --role '+role+' > /opt/a4-replica/evidence-seal.log 2>&1 < /dev/null &'
                    result=transport.ssh(role,launch)
                    if result.returncode:raise ConnectionError('Could not confirm archive sealer launch')
                    while True:
                        result=transport.ssh(role,'if test -f /opt/a4-replica/evidence-receipt.json; then cat /opt/a4-replica/evidence-receipt.json; elif test -f /opt/a4-replica/evidence-seal.failed.json; then cat /opt/a4-replica/evidence-seal.failed.json; fi')
                        if result.returncode:raise ConnectionError('Archive receipt read failed')
                        if result.stdout.strip():
                            receipt=json.loads(result.stdout)
                            if 'error' in receipt:raise RuntimeError('Remote seal failed: '+receipt['error'])
                            break
                        time.sleep(5)
                    destination=receive(transport,role,run,receipt)
                log('evidence_collected',role=role,bytes=destination.stat().st_size,sha256=receipt['sha256'])
                transport.aws('ec2','terminate-instances','--instance-ids',resources[role]['id'])
                log('termination_requested',role=role,ids=[resources[role]['id']]);break
            except Exception as error:
                log('collection_error',role=role,attempt=attempt,error=type(error).__name__+': '+str(error));attempt+=1
                try:
                    if transport.instance(role)['State']['Name'] in ('terminated','shutting-down'):
                        log('cleanup_deferred',reason='Host unavailable before verified evidence: '+role);raise SystemExit(1)
                except subprocess.CalledProcessError:pass
                # No retry-count cliff. Source-IP changes and partial streams are
                # recoverable without re-running tasks or discarding prior bytes.
                time.sleep(min(30,5*attempt))
    ids=[resources[role]['id'] for role in host_roles]
    subprocess.run(['aws','ec2','wait','instance-terminated','--region',state['region'],'--instance-ids',*ids],check=True)
    while True:
        try:transport.aws('ec2','delete-security-group','--group-id',resources['security_group']);break
        except subprocess.CalledProcessError:time.sleep(10)
    transport.aws('ec2','delete-key-pair','--key-name',resources['key_name'])
    final=transport.aws('ec2','describe-instances','--instance-ids',*ids)
    (run/'cleanup.json').write_text(json.dumps({'completed_at':time.time(),'instances':final,'security_group_deleted':True,'key_pair_deleted':True},indent=2));log('cleanup_verified')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);a=p.parse_args();main(a.run)
