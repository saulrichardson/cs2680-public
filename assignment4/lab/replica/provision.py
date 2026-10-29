"""Provision only the tagged two-host validation environment; persist every identity."""
from datetime import datetime, timezone
import argparse,json
from pathlib import Path
import subprocess,urllib.request,uuid
from course_api import PROFILE,DEFAULT_PROFILE,roles
from image_snapshot import PROFILE as IMAGE_PROFILE,BASE_AMI
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run-root',type=Path,required=True)
parser.add_argument('--workers',type=int,default=1)
parser.add_argument('--task-count',type=int,default=1)
parser.add_argument('--distinct-task-count',type=int,help='Unique frozen task environments across sequential experiments; attempts remain task-count')
parser.add_argument('--experiments',type=int,default=1)
parser.add_argument('--resource-profile',choices=[DEFAULT_PROFILE,PROFILE,IMAGE_PROFILE],default=DEFAULT_PROFILE)
parser.add_argument('--image-snapshot-json')
parser.add_argument('--image-materialization',choices=['snapshot','archive'])
parser.add_argument('--cpu-type',choices=['m7i.2xlarge','m7i.4xlarge','m7i.8xlarge','m7i.24xlarge'])
parser.add_argument('--cpu-disk-gb',type=int,help='Explicit retained batch/evidence capacity, GiB; performance profile is unchanged')
args=parser.parse_args()
distinct_tasks=args.task_count if args.distinct_task_count is None else args.distinct_task_count
if not 1<=distinct_tasks<=args.task_count or args.task_count>distinct_tasks*args.experiments:parser.error('Distinct task and planned attempt counts disagree')
external=args.resource_profile==PROFILE
builder=args.resource_profile==IMAGE_PROFILE
artifact=None;artifact_verified=None
if args.image_snapshot_json:
 if external or builder:parser.error('Only an ordinary GPU campaign can consume an image snapshot')
 from image_snapshot import validate,verify_cloud
 artifact=validate(json.loads(args.image_snapshot_json),Path(__file__).with_name('Dockerfile.gpu'))
 if not args.image_materialization:parser.error('Explicit materialization mode required')
 if args.image_materialization=='archive' and 'archive' not in artifact:parser.error('Exact image archive required')
 artifact_verified=verify_cloud(artifact)
elif args.image_materialization:parser.error('Image materialization requires descriptor')
if external and (args.workers>4 or args.cpu_type=='m7i.24xlarge'):parser.error('Unsupported CPU-only worker/host profile')
if builder and (args.workers!=1 or args.task_count!=1 or args.experiments!=1 or args.cpu_type or args.cpu_disk_gb is not None):parser.error('Image builder has no tasks, CPU companion or experiment matrix')
if not 1<=args.workers<=51 or args.task_count<1 or args.experiments<1:parser.error('Positive task count and 1–51 lab workers required')
if args.workers>25 and args.cpu_type!='m7i.24xlarge':parser.error('More than 25 lab workers require the 384-GiB fleet host')
safety_hours=max(8,6*args.experiments)
cpu_type=args.cpu_type or ('m7i.2xlarge' if args.workers<=5 else 'm7i.4xlarge' if args.workers<=13 else 'm7i.8xlarge')
base_disk=2000 if cpu_type=='m7i.24xlarge' else 200 if args.task_count==1 else 400
if args.cpu_disk_gb is not None and args.cpu_disk_gb<base_disk:parser.error('CPU disk cannot be smaller than the existing task/image capacity profile')
cpu_disk=args.cpu_disk_gb if args.cpu_disk_gb is not None else base_disk
# Capacity/image-storage limits apply independently of financial policy.
if not external and not builder and not (
 (cpu_type=='m7i.2xlarge' and args.workers<=5 and distinct_tasks<=26) or
 (cpu_type=='m7i.4xlarge' and distinct_tasks<=26) or
 (cpu_type=='m7i.8xlarge' and distinct_tasks<=26 and args.workers<=13) or
 (cpu_type=='m7i.24xlarge' and distinct_tasks<=51)):
 parser.error('Unsupported host/task capacity profile')
run=args.run_root.resolve()/('replica-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
run.mkdir(parents=True);run.chmod(0o700)
region='us-east-1';name=run.name
state={'name':name,'region':region,'created_at':datetime.now(timezone.utc).isoformat(),'resources':{},'resource_profile':args.resource_profile,'purpose':'external course API on one x86 CPU host' if external else 'frozen optimization batch on one pinned GPU','cleanup':'terminate owned instances and remove owned security group and key after evidence collection','infrastructure_safety_shutdown_hours':safety_hours,'task_workers':args.workers,'task_count':args.task_count,'distinct_task_count':distinct_tasks}
if builder:state.update(purpose='Dedicated never-secret image-only builder',cleanup='Image bake owns builder cleanup; AMI/snapshot retained and retired separately',task_workers=0,task_count=0)
if artifact:state.update(image_snapshot=artifact,image_materialization=args.image_materialization,image_snapshot_verified=artifact_verified,original_gpu_ami=BASE_AMI)
def save(): (run/'resources.json').write_text(json.dumps(state,indent=2))
def aws(*args):
 p=subprocess.run(['aws',*args,'--region',region,'--output','json'],capture_output=True,text=True)
 if p.returncode:raise RuntimeError(p.stderr.strip())
 return json.loads(p.stdout) if p.stdout.strip() else {}
def tags(role):return [{'Key':'Name','Value':name+'-'+role},{'Key':'Owner','Value':'a4-local-replica'},{'Key':'Campaign','Value':name}]
save()
try:
 subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(run/'ssh-key')],check=True)
 aws('ec2','import-key-pair','--key-name',name,'--public-key-material','fileb://'+str(run/'ssh-key.pub'),*(['--tag-specifications',json.dumps([{'ResourceType':'key-pair','Tags':tags('access')}])] if builder else []))
 state['resources']['key_name']=name;save()
 vpc=aws('ec2','describe-vpcs','--filters','Name=is-default,Values=true')['Vpcs'][0]['VpcId']
 sg=aws('ec2','create-security-group','--group-name',name,'--description','Owned local Assignment 4 research','--vpc-id',vpc)['GroupId']
 state['resources']['security_group']=sg;save()
 aws('ec2','create-tags','--resources',sg,'--tags',json.dumps(tags('network')))
 ip=urllib.request.urlopen('https://checkip.amazonaws.com').read().decode().strip()
 permissions=[{'IpProtocol':'tcp','FromPort':22,'ToPort':22,'IpRanges':[{'CidrIp':ip+'/32'}]}]
 if not external and not builder:permissions.append({'IpProtocol':'tcp','FromPort':30000,'ToPort':30000,'UserIdGroupPairs':[{'GroupId':sg}]})
 aws('ec2','authorize-security-group-ingress','--group-id',sg,'--ip-permissions',json.dumps(permissions))
 # A G7-compatible official DLAMI supplies the supported NVIDIA host driver.
 gpu_ami=artifact['ami_id'] if artifact and args.image_materialization=='snapshot' else BASE_AMI
 cpu_ami=None if builder else aws('ssm','get-parameter','--name','/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id')['Parameter']['Value']
 userdata='''#!/bin/bash
set -euo pipefail
systemd-run --unit=a4-infrastructure-safety-shutdown --on-active=8h /usr/bin/systemctl poweroff
mkdir -p /opt/a4-replica
chown ubuntu:ubuntu /opt/a4-replica
'''
 userdata=userdata.replace('--on-active=8h','--on-active='+str(safety_hours)+'h')
 if builder:userdata=userdata.replace('mkdir -p /opt/a4-replica\nchown ubuntu:ubuntu /opt/a4-replica\n','')
 (run/'user-data.sh').write_text(userdata)
 subnets=aws('ec2','describe-subnets','--filters','Name=default-for-az,Values=true')['Subnets']
 allowed={'us-east-1a','us-east-1b','us-east-1c','us-east-1d'}
 host_profiles={'gpu':('g7.4xlarge',gpu_ami,300),'cpu':(cpu_type,cpu_ami,cpu_disk)}
 for role in roles(state):
  typ,ami,disk=host_profiles[role]
  error=None
  for subnet in sorted(subnets,key=lambda s:s['AvailabilityZone']):
   if subnet['AvailabilityZone'] not in allowed:continue
   if role=='cpu' and 'subnet' in state and subnet['SubnetId']!=state['subnet']:continue
   try:
    if builder:
     state['allocation_intent']={'role':role,'client_token':name+'-'+role,'subnet':subnet['SubnetId'],'state':'dispatch_uncertain'};save()
    instance=aws('ec2','run-instances','--image-id',ami,'--instance-type',typ,'--key-name',name,
       '--network-interfaces',json.dumps([{'DeviceIndex':0,'SubnetId':subnet['SubnetId'],'Groups':[sg],'AssociatePublicIpAddress':True}]),
       '--block-device-mappings',json.dumps([{'DeviceName':'/dev/sda1','Ebs':{'VolumeSize':disk,'VolumeType':'gp3','Throughput':500 if role=='gpu' else 1000 if cpu_type=='m7i.24xlarge' else 125,'Iops':6000 if role=='gpu' else 16000 if cpu_type=='m7i.24xlarge' else 3000,'DeleteOnTermination':True,'Encrypted':True}}]),
       '--metadata-options','HttpTokens=required,HttpEndpoint=enabled',
       '--instance-initiated-shutdown-behavior','terminate','--user-data','file://'+str(run/'user-data.sh'),
       '--tag-specifications',json.dumps([{'ResourceType':'instance','Tags':tags(role)},{'ResourceType':'volume','Tags':tags(role)}]),
       '--client-token',name+'-'+role)['Instances'][0]
    state['resources'][role]={'id':instance['InstanceId'],'type':typ,'ami':ami,'disk_gb':disk}
    if builder:state['allocation_intent']['state']='returned'
    state['subnet']=subnet['SubnetId'];save();break
   except RuntimeError as e:
    error=str(e)
    if 'InsufficientInstanceCapacity' not in error and 'Unsupported' not in error:raise
    if builder:state['allocation_intent']['state']='known_capacity_rejection';save()
  else:raise RuntimeError(error or 'No suitable subnet')
 print(run,flush=True);print(json.dumps(state,indent=2),flush=True)
except BaseException as e:
 state['error']=str(e);save()
 if builder and state.get('allocation_intent',{}).get('state')=='dispatch_uncertain':
  # A lost run-instances response is not a failed allocation. Preserve intent;
  # only a read may recover the exact client-token result, never a second launch.
  try:
   recovered=aws('ec2','describe-instances','--filters','Name=client-token,Values='+state['allocation_intent']['client_token'])
   matches=[item for reservation in recovered['Reservations'] for item in reservation['Instances']]
   if len(matches)!=1:raise RuntimeError('Builder allocation remains uncertain')
   instance=matches[0];observed={t['Key']:t['Value'] for t in instance.get('Tags',[])}
   if observed.get('Campaign')!=name or observed.get('Owner')!='a4-local-replica':raise RuntimeError('Recovered builder ownership mismatch')
   state['resources']['gpu']={'id':instance['InstanceId'],'type':'g7.4xlarge','ami':BASE_AMI,'disk_gb':300};save()
  except BaseException as recovery_error:
   state['allocation_reconciliation_error']=str(recovery_error);save();raise
 ids=[v['id'] for v in state['resources'].values() if isinstance(v,dict) and 'id' in v]
 if ids:
  aws('ec2','terminate-instances','--instance-ids',*ids)
  subprocess.run(['aws','ec2','wait','instance-terminated','--region',region,'--instance-ids',*ids],check=True)
 if 'security_group' in state['resources']:aws('ec2','delete-security-group','--group-id',state['resources']['security_group'])
 if 'key_name' in state['resources']:aws('ec2','delete-key-pair','--key-name',state['resources']['key_name'])
 (run/'cleanup.json').write_text(json.dumps({'completed_at':__import__('time').time(),'security_group_deleted':True,'key_pair_deleted':True,'provisioning_failed':True,'instances':aws('ec2','describe-instances','--instance-ids',*ids) if ids else {'Reservations':[]}},indent=2))
 # Resource identities remain recorded if termination or dependent cleanup is pending.
 raise
