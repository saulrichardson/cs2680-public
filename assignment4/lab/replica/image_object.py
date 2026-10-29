"""One private versioned image archive; no bucket service or implicit retries.

Publication happens on the controller before rentals. Only a short-lived exact
version GET capability reaches the archive control, outside payload/evidence.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SCHEMA='a4-image-object-v1'
DESCRIPTOR_SCHEMA='a4-image-object-v2'
OWNER='a4-image-snapshot'
PART_BYTES=1024*1024*1024
CAPABILITY_NAME='image-object.read-capability.json'


def validate(value, descriptor, now=None):
    from image_snapshot import storage_reference
    expected={'schema','bucket','key','version_id','owner_account','region','artifact_id','sha256','bytes','retain_until'}
    if not isinstance(value,dict):raise ValueError('Exact image object descriptor required')
    storage=storage_reference(value)
    expected_schema=SCHEMA if 'storage_liability' in storage else DESCRIPTOR_SCHEMA
    if set(value)!=expected | set(storage) or value['schema']!=expected_schema:raise ValueError('Exact image object descriptor required')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',value['bucket']):raise ValueError('Use a standard DNS bucket without dots')
    archive=descriptor['archive']
    if value['region']!='us-east-1' or value['owner_account']!=descriptor['owner_account'] or value['artifact_id']!=descriptor['artifact_id']:raise ValueError('Object account/region/artifact differs')
    if value['key']!='a4-images/'+value['artifact_id']+'/'+archive['sha256']+'.tar' or value['sha256']!=archive['sha256'] or value['bytes']!=archive['bytes']:raise ValueError('Object bytes/key differ from image export')
    if not isinstance(value['version_id'],str) or not value['version_id'] or value['version_id']=='null' or len(value['version_id'])>1024:raise ValueError('An exact non-null object version is required')
    if type(value['retain_until']) not in (int,float) or not math.isfinite(value['retain_until']) or not (time.time() if now is None else now)<value['retain_until']<=descriptor['retain_until']:raise ValueError('Object retention expired or exceeds its image artifact')
    return value


def aws(*args, optional_absence=None):
    # Disable SDK retry as well as controller retry for mutations. A lost response
    # leaves an intent to reconcile, not permission to repeat an effect.
    env=dict(os.environ,AWS_MAX_ATTEMPTS='1',AWS_RETRY_MODE='standard',AWS_PAGER='')
    result=subprocess.run(['aws',*args,'--region','us-east-1','--output','json','--no-cli-pager'],capture_output=True,timeout=120,env=env)
    if result.returncode:
        if optional_absence and ('('+optional_absence+')').encode() in result.stderr:return None
        raise RuntimeError('Object API command failed; response retained only by its operation journal')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def verify_bucket(bucket, account, call=aws):
    if call('sts','get-caller-identity')['Account']!=account:raise ValueError('Object publisher account differs')
    scope=['--bucket',bucket,'--expected-bucket-owner',account]
    location=call('s3api','get-bucket-location',*scope).get('LocationConstraint')
    if location not in (None,'us-east-1'):raise ValueError('Object source must be in us-east-1')
    if call('s3api','get-bucket-versioning',*scope).get('Status')!='Enabled':raise ValueError('Object versioning must be enabled')
    blocks=call('s3api','get-public-access-block',*scope)['PublicAccessBlockConfiguration']
    if blocks!={name:True for name in ('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets')}:raise ValueError('All bucket public-access blocks required')
    ownership=call('s3api','get-bucket-ownership-controls',*scope)['OwnershipControls']['Rules']
    if ownership!=[{'ObjectOwnership':'BucketOwnerEnforced'}]:raise ValueError('Bucket-owner-enforced ownership required')
    acl=call('s3api','get-bucket-acl',*scope)
    if len(acl['Grants'])!=1 or acl['Grants'][0]['Permission']!='FULL_CONTROL' or acl['Grants'][0]['Grantee'].get('ID')!=acl['Owner']['ID']:raise ValueError('Bucket ACL is not owner-only')
    policy=call('s3api','get-bucket-policy',*scope,optional_absence='NoSuchBucketPolicy')
    if policy is not None:
        statements=json.loads(policy['Policy']).get('Statement',[])
        if isinstance(statements,dict):statements=[statements]
        if any(statement.get('Effect')!='Deny' for statement in statements):raise ValueError('Only absent or deny-only bucket policies supported')
    tags=call('s3api','get-bucket-tagging',*scope)['TagSet']
    if {row['Key']:row['Value'] for row in tags}.get('Owner')!=OWNER:raise ValueError('Bucket ownership tag differs')
    return {'account':account,'bucket':bucket,'region':'us-east-1','versioning':'Enabled','public_access_block':blocks,'ownership':'BucketOwnerEnforced','policy':'absent' if policy is None else 'deny-only'}


def scope(value):
    return ['--bucket',value['bucket'],'--key',value['key'],'--expected-bucket-owner',value['owner_account']]


def inventory(value,call=aws):
    args=['--bucket',value['bucket'],'--prefix',value['key'],'--expected-bucket-owner',value['owner_account']]
    versions=call('s3api','list-object-versions',*args)
    uploads=call('s3api','list-multipart-uploads',*args)
    if versions.get('IsTruncated') or uploads.get('IsTruncated'):raise ValueError('Incomplete object inventory; retirement/publication withheld')
    return {'versions':[row for row in versions.get('Versions',[]) if row['Key']==value['key']],
            'delete_markers':[row for row in versions.get('DeleteMarkers',[]) if row['Key']==value['key']],
            'uploads':[row for row in uploads.get('Uploads',[]) if row['Key']==value['key']]}


def verify_version(value,call=aws):
    head=call('s3api','head-object',*scope(value),'--version-id',value['version_id'])
    if head.get('VersionId')!=value['version_id'] or head.get('ContentLength')!=value['bytes'] or head.get('ServerSideEncryption')!='AES256':raise ValueError('Object version/size/encryption differs')
    if head.get('Metadata')!={'sha256':value['sha256'],'artifact-id':value['artifact_id']}:raise ValueError('Object provenance metadata differs')
    tags=call('s3api','get-object-tagging',*scope(value),'--version-id',value['version_id'])['TagSet']
    if {row['Key']:row['Value'] for row in tags}!={'Owner':OWNER,'Artifact':value['artifact_id'],'sha256':value['sha256']}:raise ValueError('Object version tags differ')
    return {'version_id':head['VersionId'],'bytes':head['ContentLength'],'metadata':head['Metadata'],'encryption':head['ServerSideEncryption'],
            'full_content_validation':'Publication part checksums and later complete download SHA; ETag is not a full-content SHA'}


def mutate(actions,name,*args):
    # Actions retains original stdout/stderr and uncertain outcomes. No capability
    # or signing credential exists during these object-storage operations.
    command=['aws',*args,'--region','us-east-1','--output','json','--no-cli-pager']
    raw=actions.once(name,command,runner=lambda argv,**kw:subprocess.run(argv,capture_output=True,timeout=None,
        env=dict(os.environ,AWS_MAX_ATTEMPTS='1',AWS_RETRY_MODE='standard',AWS_PAGER='')))
    return json.loads(raw) if raw.strip() else {}


def publish(descriptor_path,bucket,storage_owner,claim_path,retain_until,receipts):
    from image_snapshot import validate as image_validate,claim_storage,Actions,write
    descriptor=image_validate(json.loads(Path(descriptor_path).read_text()));archive=descriptor['archive'];source=Path(archive['path'])
    if source.is_symlink() or not source.is_file() or source.stat().st_size!=archive['bytes']:raise ValueError('Exact original export required')
    receipts=Path(receipts);receipts.mkdir(parents=True,exist_ok=False)
    claim=claim_storage(claim_path,storage_owner,descriptor['artifact_id'],retain_until,receipts,binding='publication_receipts')
    value={'schema':DESCRIPTOR_SCHEMA,'bucket':bucket,'key':'a4-images/'+descriptor['artifact_id']+'/'+archive['sha256']+'.tar','version_id':'pending',
        'owner_account':descriptor['owner_account'],'region':'us-east-1','artifact_id':descriptor['artifact_id'],'sha256':archive['sha256'],'bytes':archive['bytes'],
        'retain_until':retain_until,'storage_owner':storage_owner,'storage_claim_sha256':claim['sha256']}
    validate(value,descriptor)
    write(receipts/'publication.json',{'object':value,'source_descriptor_sha256':hashlib.sha256(Path(descriptor_path).read_bytes()).hexdigest(),'storage_claim':claim,
        'ownership':'Includes retained versions and incomplete multipart parts; immutable claim is never reset'})
    write(receipts/'bucket-verification.json',verify_bucket(bucket,descriptor['owner_account']))
    prior=inventory(value);write(receipts/'before.json',prior)
    if any(prior.values()):raise ValueError('Object key already has versions/markers/uploads; reconcile, do not publish again')
    actions=Actions(receipts/'actions')
    created=mutate(actions,'create-multipart','s3api','create-multipart-upload',*scope(value),'--server-side-encryption','AES256',
        '--checksum-algorithm','SHA256','--metadata',json.dumps({'sha256':value['sha256'],'artifact-id':value['artifact_id']}),
        '--tagging',urllib.parse.urlencode({'Owner':OWNER,'Artifact':value['artifact_id'],'sha256':value['sha256']}))
    upload=created['UploadId'];write(receipts/'upload.json',{'upload_id':upload,'object':value})
    parts=[];full=hashlib.sha256();number=0;read=0;before=source.stat()
    with source.open('rb') as stream:
        while block:=stream.read(PART_BYTES):
            number+=1
            if number>10000:raise ValueError('Archive exceeds supported multipart count')
            full.update(block);read+=len(block)
            part=receipts/'part.pending';part.write_bytes(block);part.chmod(0o400)
            checksum=base64.b64encode(hashlib.sha256(block).digest()).decode()
            result=mutate(actions,'part-'+str(number),'s3api','upload-part',*scope(value),'--upload-id',upload,'--part-number',str(number),
                '--checksum-sha256',checksum,'--body',str(part.resolve()))
            if result.get('ChecksumSHA256')!=checksum:raise ValueError('Part checksum response differs; publication incomplete')
            parts.append({'PartNumber':number,'ETag':result['ETag'],'ChecksumSHA256':checksum})
            part.unlink()
    after=source.stat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or read!=value['bytes'] or full.hexdigest()!=value['sha256']:
        raise ValueError('Original export changed; uploaded parts remain incomplete')
    write(receipts/'parts.json',{'Parts':parts});write(receipts/'full-input.json',{'bytes':read,'sha256':full.hexdigest(),'part_count':number})
    completed=mutate(actions,'complete','s3api','complete-multipart-upload',*scope(value),'--upload-id',upload,'--if-none-match','*',
        '--multipart-upload','file://'+str((receipts/'parts.json').resolve()))
    value['version_id']=completed['VersionId'];validate(value,descriptor)
    write(receipts/'version-verification.json',verify_version(value))
    descriptor['archive']['object']=value;write(receipts/'image-snapshot.json',descriptor)
    write(receipts/'after.json',inventory(value))
    return descriptor


def reconcile(receipts):
    """Read exact owned key after uncertainty; no mutation or automatic retry."""
    from image_snapshot import storage_reference,write
    receipts=Path(receipts);value=json.loads((receipts/'publication.json').read_text())['object']
    verified=verify_bucket(value['bucket'],value['owner_account']);observed=inventory(value)
    result={'at':time.time(),'bucket':verified,'inventory':observed,**storage_reference(value),'state':'inventory_only_no_replay'}
    if len(observed['versions'])==1:
        value=dict(value,version_id=observed['versions'][0]['VersionId']);result['version_verification']=verify_version(value)
        result['object']=value
    write(receipts/'reconciliation.json',result);return result


def retire(receipts):
    from image_snapshot import Actions,storage_reference,write
    receipts=Path(receipts);record=json.loads((receipts/'publication.json').read_text());value=record['object']
    verify_bucket(value['bucket'],value['owner_account'])
    current=inventory(value);write(receipts/'retirement-before.json',current)
    actions=Actions(receipts/'retirement-actions')
    # The exact key was absent before this single publication. Refuse ambiguous
    # additional versions/markers; never clear another publisher's work.
    if current['delete_markers'] or len(current['versions'])>1 or len(current['uploads'])>1:raise ValueError('Ambiguous object retirement; preserve all owned storage')
    if current['versions']:
        version=current['versions'][0]['VersionId'];verify_version(dict(value,version_id=version))
        mutate(actions,'delete-version','s3api','delete-object',*scope(value),'--version-id',version)
    if current['uploads']:
        upload=current['uploads'][0]['UploadId']
        known=json.loads((receipts/'upload.json').read_text())['upload_id'] if (receipts/'upload.json').exists() else None
        raw=receipts/'actions/create-multipart.stdout'
        if known is None and raw.exists():
            try:known=json.loads(raw.read_text()).get('UploadId')
            except (ValueError,TypeError):pass
        if known is not None and upload!=known:raise ValueError('Multipart identity differs')
        if known is None:raise ValueError('Multipart ID was never confirmed; inventory alone cannot authorize abort')
        mutate(actions,'abort-upload','s3api','abort-multipart-upload',*scope(value),'--upload-id',upload)
    after=inventory(value)
    write(receipts/'retirement.json',{'at':time.time(),'state':'absence_observed' if not any(after.values()) else 'remaining_storage_unknown',
        'inventory':after,**storage_reference(value),'ownership':'Claim remains immutable; retirement records observed absence and does not rewrite history'})


def signed_url(value,credentials,expires,now=None):
    """SigV4 exact-version GET. Credentials and result stay out of receipts."""
    now=datetime.now(timezone.utc) if now is None else now
    stamp=now.strftime('%Y%m%dT%H%M%SZ');day=now.strftime('%Y%m%d');region='us-east-1'
    host=value['bucket']+'.s3.'+region+'.amazonaws.com';uri='/'+urllib.parse.quote(value['key'],safe='/-_.~')
    credential_scope=day+'/'+region+'/s3/aws4_request'
    query={'X-Amz-Algorithm':'AWS4-HMAC-SHA256','X-Amz-Credential':credentials['AccessKeyId']+'/'+credential_scope,
        'X-Amz-Date':stamp,'X-Amz-Expires':str(expires),'X-Amz-SignedHeaders':'host','versionId':value['version_id']}
    if credentials.get('SessionToken'):query['X-Amz-Security-Token']=credentials['SessionToken']
    encoded='&'.join(urllib.parse.quote(k,safe='-_.~')+'='+urllib.parse.quote(v,safe='-_.~') for k,v in sorted(query.items()))
    canonical='GET\n'+uri+'\n'+encoded+'\nhost:'+host+'\n\nhost\nUNSIGNED-PAYLOAD'
    string='AWS4-HMAC-SHA256\n'+stamp+'\n'+credential_scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
    key=('AWS4'+credentials['SecretAccessKey']).encode()
    for part in (day,region,'s3','aws4_request'):key=hmac.new(key,part.encode(),hashlib.sha256).digest()
    signature=hmac.new(key,string.encode(),hashlib.sha256).hexdigest()
    return 'https://'+host+uri+'?'+encoded+'&X-Amz-Signature='+signature


def issue_capability(descriptor,output):
    value=validate(descriptor['archive']['object'],descriptor);verify_bucket(value['bucket'],value['owner_account']);verify_version(value)
    # This one command's output is secret. Never journal argv/stdout/stderr through
    # Actions or include the exception body. The credential provider stays local.
    result=subprocess.run(['aws','configure','export-credentials','--format','process'],capture_output=True,timeout=30,
        env=dict(os.environ,AWS_MAX_ATTEMPTS='1',AWS_PAGER=''))
    if result.returncode:raise RuntimeError('Local signing credential resolution failed')
    try:
        credentials=json.loads(result.stdout);expires=3600
        if credentials.get('Expiration'):
            remaining=datetime.fromisoformat(credentials['Expiration'].replace('Z','+00:00')).timestamp()-time.time()-30
            expires=min(expires,int(remaining))
        if expires<60:raise ValueError('Credential lifetime too short')
        url=signed_url(value,credentials,expires)
    except Exception:raise RuntimeError('Local signing credential is unavailable or unsupported') from None
    path=Path(output)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as out:json.dump({'url':url,'expires_at':time.time()+expires,'version_id':value['version_id']},out);out.flush();os.fsync(out.fileno())
    return {'version_id':value['version_id'],'expires_in_s':expires,'capability_path':path.name,'secret_persisted_in_receipt':False}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def download(descriptor_path,capability,output,receipt,opener=None):
    from image_snapshot import validate as image_validate,write
    descriptor=image_validate(json.loads(Path(descriptor_path).read_text()));value=validate(descriptor['archive']['object'],descriptor)
    cap=Path(capability);target=Path(output);partial=target.with_name(target.name+'.partial');start=time.time();h=hashlib.sha256();count=0;status=None
    result={'started_at_unix':start,'object':value,'state':'failed_or_partial'}
    try:
        if cap.is_symlink() or not cap.is_file() or stat.S_IMODE(cap.stat().st_mode)&0o077:raise ValueError('Read capability must be a protected regular file')
        data=json.loads(cap.read_text());parsed=urllib.parse.urlsplit(data['url']);query=urllib.parse.parse_qs(parsed.query)
        if parsed.scheme!='https' or parsed.netloc!=value['bucket']+'.s3.us-east-1.amazonaws.com' or urllib.parse.unquote(parsed.path)!='/'+value['key'] or parsed.fragment or query.get('versionId')!=[value['version_id']] or data.get('version_id')!=value['version_id'] or data.get('expires_at',0)<=time.time():raise ValueError('Capability is expired or names a different object')
        if target.exists():raise ValueError('Archive destination already exists')
        client=opener or urllib.request.build_opener(NoRedirect())
        request=urllib.request.Request(data['url'],method='GET')
        with partial.open('xb') as out,client.open(request,timeout=120) as response:
            status=response.status
            if status!=200 or response.headers.get('x-amz-version-id')!=value['version_id'] or response.headers.get('Content-Length')!=str(value['bytes']):raise ValueError('Download version/status/length differs')
            while block:=response.read(min(8*1024*1024,value['bytes']-count+1)):
                out.write(block);h.update(block);count+=len(block)
                if count>value['bytes']:raise ValueError('Download exceeds declared size')
            out.flush();os.fsync(out.fileno())
        if count!=value['bytes'] or h.hexdigest()!=value['sha256']:raise ValueError('Complete archive SHA/size differs')
        partial.replace(target);result['state']='download_verified'
    except BaseException as error:
        if isinstance(error,urllib.error.HTTPError):status=error.code
        # Never retain exception strings, URLs, response bodies or headers that
        # can echo the temporary capability. Raw partial archive stays excluded.
        result['error_type']=type(error).__name__
        raise RuntimeError('Image object download failed; see sanitized receipt') from None
    finally:
        result.update(finished_at_unix=time.time(),elapsed_s=time.time()-start,http_status=status,bytes_written=count,observed_sha256=h.hexdigest())
        write(receipt,result)
        cap.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    pub=sub.add_parser('publish');pub.add_argument('--descriptor',type=Path,required=True);pub.add_argument('--bucket',required=True);pub.add_argument('--storage-owner',required=True);pub.add_argument('--claim',type=Path,required=True);pub.add_argument('--retain-until',type=float,required=True);pub.add_argument('--receipts',type=Path,required=True)
    for action in ('reconcile','retire'):
        p=sub.add_parser(action);p.add_argument('--receipts',type=Path,required=True)
    p=sub.add_parser('download');p.add_argument('--descriptor',type=Path,required=True);p.add_argument('--capability',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.action=='publish':publish(args.descriptor,args.bucket,args.storage_owner,args.claim,args.retain_until,args.receipts)
        elif args.action=='reconcile':reconcile(args.receipts)
        elif args.action=='retire':retire(args.receipts)
        elif args.action=='download':download(args.descriptor,args.capability,args.output,args.receipt)
    except Exception as error:
        print('Image object operation failed ('+type(error).__name__+'); inspect retained sanitized/operation receipts.',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':sys.exit(main())
