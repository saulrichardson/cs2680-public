"""SSH access for one tagged local replica; no inference or experiment retries."""
import ipaddress
import json
from pathlib import Path
import subprocess
import time
import urllib.request

class Transport:
    def __init__(self, run, log=lambda *args,**kwargs:None):
        self.run=Path(run);self.state=json.loads((self.run/'resources.json').read_text());self.log=log
        self.resources=self.state['resources'];self.last_refresh=0
    def aws(self,*args):
        raw=subprocess.check_output(['aws',*args,'--region',self.state['region'],'--output','json'])
        return json.loads(raw) if raw.strip() else {}
    def instance(self,role):
        instance=self.aws('ec2','describe-instances','--instance-ids',self.resources[role]['id'])['Reservations'][0]['Instances'][0]
        tags={t['Key']:t['Value'] for t in instance.get('Tags',[])}
        if tags.get('Campaign')!=self.state['name'] or tags.get('Owner')!='a4-local-replica':raise RuntimeError('Instance ownership mismatch')
        return instance
    def refresh_access(self):
        if time.monotonic()-self.last_refresh<15:return
        self.last_refresh=time.monotonic()
        with urllib.request.urlopen('https://checkip.amazonaws.com',timeout=10) as response:
            address=str(ipaddress.IPv4Address(response.read().decode().strip()))+'/32'
        group=self.aws('ec2','describe-security-groups','--group-ids',self.resources['security_group'])['SecurityGroups'][0]
        tags={t['Key']:t['Value'] for t in group.get('Tags',[])}
        if tags.get('Campaign')!=self.state['name'] or tags.get('Owner')!='a4-local-replica':raise RuntimeError('Security-group ownership mismatch')
        existing=[rule for rule in group.get('IpPermissions',[]) if rule.get('IpProtocol')=='tcp' and rule.get('FromPort')==22 and rule.get('ToPort')==22]
        if any(address==item['CidrIp'] for rule in existing for item in rule.get('IpRanges',[])):return
        permission={'IpProtocol':'tcp','FromPort':22,'ToPort':22,'IpRanges':[{'CidrIp':address}]}
        self.aws('ec2','authorize-security-group-ingress','--group-id',group['GroupId'],'--ip-permissions',json.dumps([permission]))
        self.log('ssh_access_refreshed',cidr=address)
        # This dedicated run owns all SSH ingress; remove obsolete public sources.
        for rule in existing:
            old=[item for item in rule.get('IpRanges',[]) if item['CidrIp']!=address]
            if old:
                self.aws('ec2','revoke-security-group-ingress','--group-id',group['GroupId'],'--ip-permissions',json.dumps([{'IpProtocol':'tcp','FromPort':22,'ToPort':22,'IpRanges':old}]))
    def args(self,role,command):
        return ['ssh','-i',str(self.run/'ssh-key'),'-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3','-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(self.run/'known_hosts'),'ubuntu@'+self.resources[role]['public_ip'],command]
    def ssh(self,role,command,timeout=60,input=None):
        # A failed command is not blindly executed again. Read-only callers or
        # receipt-protected operations decide whether repetition is safe.
        try:result=subprocess.run(self.args(role,command),capture_output=True,timeout=timeout,input=input)
        except subprocess.TimeoutExpired:
            self.refresh_access();raise
        if result.returncode==255:self.refresh_access()
        return result
