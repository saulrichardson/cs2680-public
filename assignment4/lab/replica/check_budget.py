"""No AWS allocation: commitments, exact ownership and independent watchdog execution."""
import concurrent.futures,json,os,subprocess,sys,tempfile,time
from pathlib import Path
from unittest.mock import Mock
import budget,cost_watchdog

with tempfile.TemporaryDirectory() as folder:
 root=Path(folder);ledger=root/'STATE.json'
 initial={'total_additional_budget_usd':5.,'settled_usd':0.,'commitments':[],'campaigns':[]}
 ledger.write_text(json.dumps(initial))
 def attempt(i):
  try:return budget.reserve(ledger,root/str(i),amount=4.5,now=100)
  except ValueError:return None
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:answers=list(ex.map(attempt,range(8)))
 values=[x for x in answers if x];assert len(values)==1
 policy=values[0];assert policy['soft_stop_at']<policy['hard_stop_at']
 value=budget.observe(ledger,policy['id'],now=110,cleaned=True)
 assert value['billing_state']=='unreconciled' and value['estimated_exposure_usd']>0
 assert json.loads(ledger.read_text())['settled_usd']==0
 assert attempt(99) is None,'Cleanup must not refund unknown billing'
 # A new explicit authorization can commit a larger warm session without resetting prior ledgers.
 expanded=root/'expanded.json';expanded.write_text(json.dumps(dict(initial,total_additional_budget_usd=10.,campaign_commitment_usd=9.5)))
 longer=budget.reserve(expanded,root/'longer',now=100)
 assert longer['amount_usd']==9.5 and longer['hard_stop_at']==100+7200
 try:budget.reserve(expanded,root/'duplicate-session',now=100)
 except ValueError:pass
 else:raise AssertionError('A second full session exceeded the new cumulative ceiling')
 # Exact tags are required before mutation; repeated hard cutoff does not touch stopped hosts.
 run=root/'owned';run.mkdir();state={'name':'fixture','region':'test','resources':{'gpu':{'id':'i-owned'}}};(run/'resources.json').write_text(json.dumps(state))
 instance={'InstanceId':'i-owned','State':{'Name':'running'},'Tags':[{'Key':'Owner','Value':'a4-local-replica'},{'Key':'Campaign','Value':'fixture'}]};calls=[]
 def aws(*args):
  calls.append(args)
  if args[1]=='describe-instances':return {'Reservations':[{'Instances':[instance]}]}
  assert args==('ec2','terminate-instances','--instance-ids','i-owned');return {}
 soft=Mock();assert cost_watchdog.step(run,policy,101,aws,soft)=='within_commitment';soft.assert_not_called()
 cost_watchdog.step(run,policy,policy['soft_stop_at'],aws,soft);soft.assert_called_once()
 cost_watchdog.step(run,policy,policy['hard_stop_at'],aws,soft);assert calls[-1][1]=='terminate-instances'
 instance['Tags'][1]['Value']='other';calls.clear()
 try:cost_watchdog.step(run,policy,policy['hard_stop_at'],aws,soft)
 except RuntimeError:pass
 else:raise AssertionError('Cross-campaign termination admitted')
 assert all(c[1]!='terminate-instances' for c in calls)
 # A genuinely separate process starts its watchdog and survives parent-style orchestration boundaries.
 # A completed owned cleanup lets it settle without any AWS or SSH invocation.
 run2=root/'process';run2.mkdir();(run2/'cost-policy.json').write_text(json.dumps({'ledger':str(ledger),'commitment':policy}))
 (run2/'cleanup.json').write_text(json.dumps({'security_group_deleted':True,'key_pair_deleted':True}))
 p=subprocess.Popen([sys.executable,str(Path(cost_watchdog.__file__)),str(run2)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
 stdout,stderr=p.communicate(timeout=10);assert p.returncode==0,stderr
 assert (run2/'cost-watchdog.ready').exists()
 p=subprocess.run([sys.executable,str(Path(cost_watchdog.__file__)),str(run2)],capture_output=True);assert p.returncode!=0,'Duplicate guard accepted'
 # Provision source must compile, generated default script remains valid.
 source=Path(__file__).with_name('provision.py').read_text();compile(source,'provision.py','exec')
print('PASS: concurrent cumulative admission, no estimate refunds, exact resource ownership, soft/hard cutoff, independent process and duplicate-guard refusal; no paid calls')

# The real independent watchdog reaches its hard cutoff without model/check-in assistance.
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);ledger=root/'STATE.json';ledger.write_text(json.dumps(initial));campaign=root/'run';campaign.mkdir()
 item=budget.reserve(ledger,campaign,now=time.time()-4000)
 (campaign/'cost-policy.json').write_text(json.dumps({'ledger':str(ledger),'commitment':item}))
 (campaign/'resources.json').write_text(json.dumps({'name':'owned','region':'test','resources':{'cpu':{'id':'i-owned'}}}))
 fake=root/'aws';fake.write_text('''#!/usr/bin/env python3
import json,sys,os
with open(os.environ['AWS_TEST_LOG'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[2]=='describe-instances':print(json.dumps({'Reservations':[{'Instances':[{'InstanceId':'i-owned','State':{'Name':'running'},'Tags':[{'Key':'Owner','Value':'a4-local-replica'},{'Key':'Campaign','Value':'owned'}]}]}]}))
else:print('{}')
''');fake.chmod(0o755)
 env=dict(os.environ,PATH=str(root)+os.pathsep+os.environ['PATH'],AWS_TEST_LOG=str(root/'calls'))
 child=subprocess.Popen([sys.executable,str(Path(cost_watchdog.__file__)),str(campaign)],env=env,start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 try:
  until=time.monotonic()+10
  while time.monotonic()<until:
   if (root/'calls').exists() and 'terminate-instances' in (root/'calls').read_text():break
   time.sleep(.05)
  else:raise AssertionError('Independent watchdog did not terminate exact owned host')
  (campaign/'cleanup.json').write_text(json.dumps({'security_group_deleted':True,'key_pair_deleted':True}));child.communicate(timeout=10);assert child.returncode==0
 finally:
  if child.poll() is None:child.terminate();child.wait(timeout=5)
print('PASS: real watchdog child independently invokes exact owned termination through fake AWS, then exits on cleanup; no real allocation')
