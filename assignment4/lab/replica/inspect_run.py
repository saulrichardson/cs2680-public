"""Read-only inspection of one registry-owned environment, preserving dated observations."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
from owned_transport import Transport
import subprocess

p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path);a=p.parse_args();run=a.run.resolve();state=json.loads((run/'resources.json').read_text())
transport=Transport(run)
report={'at':datetime.now(timezone.utc).isoformat(),'campaign':state['name'],'hosts':{}}
for role in ['gpu','cpu']:
    resource=state['resources'][role]
    raw=subprocess.check_output(['aws','ec2','describe-instances','--region',state['region'],'--instance-ids',resource['id'],'--output','json'])
    instance=json.loads(raw)['Reservations'][0]['Instances'][0];tags={t['Key']:t['Value'] for t in instance.get('Tags',[])}
    if tags.get('Campaign')!=state['name'] or tags.get('Owner')!='a4-local-replica':raise SystemExit('Ownership mismatch; no SSH inspection performed')
    row={'id':resource['id'],'state':instance['State']['Name']}
    if row['state']=='running':
        script="""import json,subprocess
from pathlib import Path
r=Path('/opt/a4-replica');key=(r/'serve.key').read_text().strip() if (r/'serve.key').exists() else 'UNAVAILABLE_KEY';result={}
for n in ['experiment.json','experiment.complete','experiment.failed','bootstrap.failed','server.failed']:
 p=r/n
 if p.exists():result[n]=p.read_text()[-4000:]
for n in ['bootstrap.log','bootstrap-retry.log','server.log','experiment.log','iteration-logs/replay.log','iteration-logs/task.log']:
 p=r/n
 if p.exists():
  with p.open('rb') as f:
   f.seek(max(0,p.stat().st_size-3000));result[n]=f.read().decode(errors='replace').replace(key,'[REDACTED]')
for job in sorted((r/'jobs').glob('*')) if (r/'jobs').exists() else []:
 details={}
 for relative in ['experiment.json','experiment.complete','experiment.failed','iteration-logs/events.jsonl','iteration-logs/replay/summary.json','runtime.json','runtime-supplement-receipt.json','runtime-supplement.log','iteration/serving-ready.json','iteration/task-stages.json','iteration/task-completion.json','iteration/prepare.log','iteration/preflight.log','iteration/task.log','iteration/execution/course/run_all_results.md','iteration/execution/course/pro_eval/eval_results.json','server.log','native-events.jsonl','native/native-cohort-sweep.log','native/sweep/progress.json']:
  p=job/relative
  if p.is_file():
   with p.open('rb') as f:
    f.seek(max(0,p.stat().st_size-4000));details[relative]=f.read().decode(errors='replace').replace(key,'[REDACTED]')
 details['native_attempts']=[]
 for receipt in sorted((job/'native/sweep').glob('*-workers-*/task-*/case-0/*/run.json')):
  item=json.loads(receipt.read_text());item['path']=str(receipt.relative_to(r))
  calls=[]
  for wire in sorted((receipt.parent/'wire').glob('*.receipt.json')):
   try:
    observed=json.loads(wire.read_text());calls.append({'call':wire.stem,'state':observed.get('state'),'status':observed.get('status'),'elapsed_s':observed.get('elapsed_s'),'request_id':observed.get('request_id')})
   except ValueError:pass
  item['last_calls']=calls[-3:];item['unsettled_calls']=sum(c.get('state')=='running' for c in calls)
  details['native_attempts'].append(item)
 result['job-'+job.name]=details
for n,cmd in [('containers',['sudo','docker','ps','--format','{{.Names}} {{.Status}}']),('disk',['df','-h','/'])]:
 p=subprocess.run(cmd,capture_output=True,text=True);result[n]=p.stdout
print(json.dumps(result))
"""
        result=transport.ssh(role,'python3 -',input=script.encode(),timeout=30)
        result.stdout=result.stdout.decode(errors='replace');result.stderr=result.stderr.decode(errors='replace')
        if result.returncode==0:row['observation']=json.loads(result.stdout)
        else:row['inspection_error']=result.stderr[-1000:]
    report['hosts'][role]=row
if (run/'cleanup.json').exists():report['cleanup']=json.loads((run/'cleanup.json').read_text())
out=run/'observations';out.mkdir(exist_ok=True);path=out/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json');path.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'snapshot':str(path),'hosts':report['hosts']},indent=2))
