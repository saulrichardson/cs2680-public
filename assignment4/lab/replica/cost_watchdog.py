"""Independent campaign cost cutoff; owned hosts also get an absolute shutdown timer."""
import argparse,json,os,subprocess,time
from pathlib import Path
import budget
from course_api import roles


def owned_instances(state,aws):
    result=[]
    for role in ('gpu','cpu'):
        if role not in state['resources']:continue
        ident=state['resources'][role]['id']
        raw=aws('ec2','describe-instances','--instance-ids',ident)
        item=raw['Reservations'][0]['Instances'][0]
        tags={x['Key']:x['Value'] for x in item.get('Tags',[])}
        if item['InstanceId']!=ident or tags.get('Owner')!='a4-local-replica' or tags.get('Campaign')!=state['name']:raise RuntimeError('Cost watchdog refuses unrelated resource')
        result.append(item)
    return result


def step(run,policy,now,aws,soft):
    state=json.loads((run/'resources.json').read_text());items=owned_instances(state,aws)
    if now>=policy['hard_stop_at']:
        active=[x['InstanceId'] for x in items if x['State']['Name'] not in ('terminated','shutting-down')]
        if active:aws('ec2','terminate-instances','--instance-ids',*active)
        return 'hard_stop_requested'
    if now>=policy['soft_stop_at']:
        soft();return 'collection_requested'
    return 'within_commitment'


def request_collection(run,state):
    """Only the declared CPU owner is signalled; missing access is not success."""
    if state.get('resource_profile') == 'image-bake-gpu':
        # A never-secret image builder has no solver or ordinary collector. Its
        # owned bake process retains local receipts; soft cutoff ends compute.
        def aws(*args):
            raw=subprocess.check_output(['aws',*args,'--region',state['region'],'--output','json'],timeout=25)
            return json.loads(raw) if raw.strip() else {}
        active=[item['InstanceId'] for item in owned_instances(state,aws) if item['State']['Name'] not in ('terminated','shutting-down')]
        if active:aws('ec2','terminate-instances','--instance-ids',*active)
        (run/'image-bake-budget-stop.json').write_text(json.dumps({'at':time.time(),'terminated_requested':active}))
        return True
    if not all(x in state['resources'] and 'public_ip' in state['resources'][x] for x in roles(state)):return False
    from owned_transport import Transport
    t=Transport(run)
    cmd="found=0; for p in /opt/a4-replica/jobs/*/executor.pid; do if test -f \"$p\"; then found=1; kill -TERM \"$(cat \"$p\")\" || true; fi; done; echo 'Campaign rental budget cutoff; not a model decision' > /opt/a4-replica/budget-stop.requested; if test \"$found\" = 0; then cp /opt/a4-replica/budget-stop.requested /opt/a4-replica/experiment.failed; fi"
    return t.ssh('cpu',cmd,timeout=25).returncode==0


def run_guard(run):
    run=run.resolve();lock=run/'cost-watchdog.lock'
    fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as out:out.write(str(os.getpid()))
    config=json.loads((run/'cost-policy.json').read_text());policy=config['commitment'];ledger=Path(config['ledger'])
    (run/'cost-watchdog.ready').write_text(str(os.getpid()))
    requested=False
    def event(**value):
        with (run/'cost-watchdog-events.jsonl').open('a') as out:out.write(json.dumps(dict(at=time.time(),**value))+'\n')
    while True:
        if (run/'cleanup.json').exists():
            done=json.loads((run/'cleanup.json').read_text())
            if done.get('security_group_deleted') and done.get('key_pair_deleted'):
                budget.observe(ledger,policy['id'],cleaned=True);event(state='cleanup_verified');return
        try:
            state=json.loads((run/'resources.json').read_text())
            def aws(*args):
                raw=subprocess.check_output(['aws',*args,'--region',state['region'],'--output','json'],timeout=25)
                return json.loads(raw) if raw.strip() else {}
            def soft():
                nonlocal requested
                if requested:return
                requested=request_collection(run,state)
            action=step(run,policy,time.time(),aws,soft)
            budget.observe(ledger,policy['id']);
            if action!='within_commitment':event(state=action)
        except Exception as error:event(error=type(error).__name__+': '+str(error))
        time.sleep(5)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);a=p.parse_args();run_guard(a.run)
