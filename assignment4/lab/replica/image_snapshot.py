"""Explicit private image-only AMIs; campaigns never own the retained snapshot.

No action runs on import. The bake command uses the existing rental provisioner
with an immutable ownership claim for retained storage until explicit retirement.
Unknown mutations are recorded before dispatch and are never automatically retried.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import uuid

HERE = Path(__file__).resolve().parent
PROFILE = 'image-bake-gpu'
BASE_AMI = 'ami-0e56f571f15c88d23'
OWNER = 'a4-image-snapshot'
SCHEMA = 'a4-image-snapshot-v1'  # Existing baked-content manifests remain unchanged.
DESCRIPTOR_SCHEMA = 'a4-image-snapshot-v2'
CACHE = Path('/opt/a4-image-cache')
# These empty instance-store mappings are inherited from the pinned BASE_AMI.
# They are not additional EBS snapshots or retained data.
BASE_INSTANCE_STORE = [{'DeviceName': '/dev/sdb', 'VirtualName': 'ephemeral0'},
                       {'DeviceName': '/dev/sdc', 'VirtualName': 'ephemeral1'}]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path); pending = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with pending.open('x') as stream:
        json.dump(value, stream, indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    pending.replace(path)


def utc():
    return datetime.now(timezone.utc).isoformat()


def validate(value, recipe=None, now=None):
    """An inline immutable descriptor, never a mutable AMI selector."""
    required = {'schema', 'artifact_id', 'region', 'owner_account', 'base_ami',
                'ami_id', 'snapshot_id', 'root_device', 'volume_gib', 'recipe_sha256',
                'manifest_sha256', 'manifest', 'retain_until'}
    if not isinstance(value, dict):
        raise ValueError('Exact image snapshot descriptor required')
    storage = storage_reference(value)
    expected_schema = SCHEMA if 'storage_liability' in storage else DESCRIPTOR_SCHEMA
    if set(value)-{'archive'} != required | set(storage) or value['schema'] != expected_schema:
        raise ValueError('Exact image snapshot descriptor required')
    for key, pattern in [('artifact_id', r'[0-9a-f]{32}'), ('owner_account', r'[0-9]{12}'),
                         ('ami_id', r'ami-[0-9a-f]+'), ('snapshot_id', r'snap-[0-9a-f]+'),
                         ('recipe_sha256', r'[0-9a-f]{64}'), ('manifest_sha256', r'[0-9a-f]{64}')]:
        if not isinstance(value[key], str) or not re.fullmatch(pattern, value[key]):
            raise ValueError('Invalid snapshot '+key)
    if value['region'] != 'us-east-1' or value['base_ami'] != BASE_AMI or value['root_device'] != '/dev/sda1' or value['volume_gib'] != 300:
        raise ValueError('Snapshot changed the pinned host/storage profile')
    if type(value['retain_until']) not in (float, int) or not math.isfinite(value['retain_until']) or value['retain_until'] <= (time.time() if now is None else now):
        raise ValueError('Image artifact retention has expired')
    manifest = value['manifest']
    if set(manifest) != {'schema', 'artifact_id', 'recipe_sha256', 'image', 'host', 'created_at'}:
        raise ValueError('Unexpected baked manifest')
    if manifest['schema'] != SCHEMA or manifest['artifact_id'] != value['artifact_id'] or manifest['recipe_sha256'] != value['recipe_sha256']:
        raise ValueError('Baked manifest identity mismatch')
    image = manifest['image']
    if set(image) != {'Id', 'Architecture', 'Os', 'RootFS', 'Config'} or not re.fullmatch(r'sha256:[0-9a-f]{64}', image['Id']) or image['Architecture'] != 'amd64' or image['Os'] != 'linux':
        raise ValueError('An immutable Linux amd64 image is required')
    if not isinstance(image['RootFS'], dict) or image['RootFS'].get('Type') != 'layers' or not image['RootFS'].get('Layers'):
        raise ValueError('Image rootfs digests missing')
    if any(not re.fullmatch(r'sha256:[0-9a-f]{64}', layer) for layer in image['RootFS']['Layers']):
        raise ValueError('Invalid rootfs digest')
    if set(manifest['host']) != {'kernel', 'driver', 'docker', 'containerd', 'store', 'configuration'}:
        raise ValueError('Pinned host/store identity missing')
    if recipe is not None and digest(recipe) != value['recipe_sha256']:
        raise ValueError('Snapshot recipe differs from the frozen Dockerfile')
    if 'archive' in value:
        archive=value['archive']
        if set(archive)-{'object'}!={'path','sha256','bytes','config_sha256','config','format'} or archive['format']!='docker-save-linux-amd64':raise ValueError('Unsupported image archive')
        if any(not re.fullmatch(r'[0-9a-f]{64}',archive[key]) for key in ('sha256','config_sha256')) or type(archive['bytes']) is not int or archive['bytes']<=0:raise ValueError('Invalid image archive identity')
        if not Path(archive['path']).is_absolute() or Path(archive['path']).name!=archive['sha256']+'.tar':raise ValueError('Content-addressed absolute image archive required')
        compare_config(archive['config'],image)
        if 'object' in archive:
            from image_object import validate as object_validate
            object_validate(archive['object'],value,now=now)
    return value


def policy(config, recipe=None):
    value = config.get('image_snapshot')
    if value is None:
        if config.get('image_materialization') is not None:raise ValueError('Materialization mode requires an explicit image artifact')
        return None
    if config.get('resource_profile', 'gpu-cpu') != 'gpu-cpu':
        raise ValueError('Image snapshot is only for self-hosted GPU campaigns')
    if config.get('image_materialization') not in ('snapshot','archive'):raise ValueError('Explicit snapshot or archive materialization required')
    value=validate(value, recipe)
    if config['image_materialization']=='archive':
        if 'archive' not in value:raise ValueError('Control requires the same image export')
        if 'object' not in value['archive']:
            path=Path(value['archive']['path'])
            if path.is_symlink() or path.stat().st_size!=value['archive']['bytes'] or file_hash(path)!=value['archive']['sha256']:raise ValueError('Image archive changed')
    return value


def aws(*args, region='us-east-1'):
    proc = subprocess.run(['aws', *args, '--region', region, '--output', 'json'], capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip())
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def tags(artifact):
    return [{'Key': 'Owner', 'Value': OWNER}, {'Key': 'Artifact', 'Value': artifact}]


def owned(row, artifact):
    actual = {t['Key']: t['Value'] for t in row.get('Tags', [])}
    if actual.get('Owner') != OWNER or actual.get('Artifact') != artifact:
        raise ValueError('Image/snapshot ownership mismatch')


def root_mapping(blocks, value):
    root = [b for b in blocks if b.get('DeviceName') == value['root_device']]
    other = [b for b in blocks if b.get('DeviceName') != value['root_device']]
    if (len(root) != 1 or (other and sorted(other, key=lambda b: b.get('DeviceName', '')) != BASE_INSTANCE_STORE)
            or set(root[0]) != {'DeviceName', 'Ebs'}
            or root[0]['Ebs'].get('SnapshotId') != value['snapshot_id']
            or root[0]['Ebs'].get('VolumeSize') != value['volume_gib']):
        raise ValueError('Unexpected image block devices')
    return root[0]


def verify_cloud(value, call=aws, now=None):
    validate(value,now=now)
    if call('sts', 'get-caller-identity')['Account'] != value['owner_account']:
        raise ValueError('Artifact account mismatch')
    images = call('ec2', 'describe-images', '--owners', 'self', '--image-ids', value['ami_id'])['Images']
    if len(images) != 1:
        raise ValueError('Expected one private owned image')
    image = images[0]; owned(image, value['artifact_id'])
    if image['ImageId'] != value['ami_id'] or image['OwnerId'] != value['owner_account'] or image.get('Public') is not False or image['State'] != 'available' or image['Architecture'] != 'x86_64' or image['RootDeviceName'] != value['root_device']:
        raise ValueError('AMI is not the admitted available private root')
    root_mapping(image['BlockDeviceMappings'], value)
    if call('ec2', 'describe-image-attribute', '--image-id', value['ami_id'], '--attribute', 'launchPermission').get('LaunchPermissions'):
        raise ValueError('AMI must not be shared')
    snapshots = call('ec2', 'describe-snapshots', '--owner-ids', 'self', '--snapshot-ids', value['snapshot_id'])['Snapshots']
    if len(snapshots) != 1:
        raise ValueError('Expected one owned root snapshot')
    snapshot = snapshots[0]; owned(snapshot, value['artifact_id'])
    if snapshot['SnapshotId'] != value['snapshot_id'] or snapshot['OwnerId'] != value['owner_account'] or snapshot['State'] != 'completed' or snapshot['Encrypted'] is not True or snapshot['VolumeSize'] != 300:
        raise ValueError('Snapshot is not the exact completed encrypted root')
    if call('ec2', 'describe-snapshot-attribute', '--snapshot-id', value['snapshot_id'], '--attribute', 'createVolumePermission').get('CreateVolumePermissions'):
        raise ValueError('Snapshot must not be shared')
    return {'image': image, 'snapshot': snapshot, 'checked_at': utc()}


def output(argv):
    return subprocess.check_output(argv, text=True, timeout=60).strip()


def host_identity():
    config = {}
    for name in ('/etc/docker/daemon.json', '/etc/containerd/config.toml'):
        path = Path(name)
        config[name] = digest(path) if path.is_file() else None
    return {'kernel': output(['uname', '-r']),
            'driver': output(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader']),
            'docker': output(['docker', '--version']), 'containerd': output(['containerd', '--version']),
            'store': json.loads(output(['docker', 'info', '--format', '{{json .}}'])) | {},
            'configuration': config}


def stable_host():
    value = host_identity()
    # Exclude host name, counters, IDs, memory and per-boot operational state.
    info = value['store']
    value['store'] = {key: info.get(key) for key in ('DockerRootDir', 'Driver', 'DriverStatus', 'ServerVersion', 'CgroupDriver', 'CgroupVersion')}
    return value


def image_identity(image):
    rows = json.loads(output(['docker', 'image', 'inspect', image]))
    if len(rows) != 1:
        raise ValueError('Exact image missing')
    return {key: rows[0][key] for key in ('Id', 'Architecture', 'Os', 'RootFS', 'Config')}


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def compare_config(config,image):
    rootfs={'type':image['RootFS']['Type'],'diff_ids':image['RootFS']['Layers']}
    if config.get('architecture')!=image['Architecture'] or config.get('os')!=image['Os'] or config.get('rootfs')!=rootfs or config.get('config')!=image['Config']:
        raise ValueError('Export/import changed runnable configuration or ordered rootfs')


def archive_identity(path,image):
    # Read data members only; never extract paths or follow archive links.
    with tarfile.open(path,'r:') as archive:
        member=archive.getmember('manifest.json')
        if not member.isfile() or member.size>2*1024*1024:raise ValueError('Unsupported Docker save manifest')
        manifests=json.load(archive.extractfile(member))
        if len(manifests)!=1:raise ValueError('Exactly one runnable platform image required')
        member=archive.getmember(manifests[0]['Config'])
        if not member.isfile() or member.size>8*1024*1024:raise ValueError('Unsupported image config member')
        raw=archive.extractfile(member).read();config=json.loads(raw)
        compare_config(config,image)
        if len(manifests[0]['Layers'])!=len(image['RootFS']['Layers']):raise ValueError('Image layer count changed')
    checksum=file_hash(path)
    return {'path':str(Path(path).resolve()),'sha256':checksum,'bytes':Path(path).stat().st_size,'config_sha256':hashlib.sha256(raw).hexdigest(),'config':config,'format':'docker-save-linux-amd64'}


def host_load(descriptor,archive,recipe,receipt):
    value=validate(json.loads(Path(descriptor).read_text()),recipe);expected=value['archive']
    if Path(archive).is_symlink() or Path(archive).stat().st_size!=expected['bytes'] or file_hash(archive)!=expected['sha256']:raise ValueError('Transferred image archive differs')
    actual=archive_identity(archive,value['manifest']['image'])
    if any(actual[key]!=expected[key] for key in ('sha256','bytes','config_sha256','config','format')):raise ValueError('Archive config differs')
    if stable_host()!=value['manifest']['host']:raise ValueError('Stock control host stack differs')
    actions=Actions(Path(receipt).parent/'image-load-actions')
    raw=actions.once('load',['docker','load','--input',str(archive)],runner=lambda argv,**kw:subprocess.run(argv,capture_output=True,timeout=None))
    # Index/attestation wrappers may differ. Resolve the exported config ID and
    # record it separately; equality here is runnable Config + ordered diffIDs.
    loaded=re.findall(rb'^Loaded image ID: (sha256:[0-9a-f]{64})$',raw,re.MULTILINE)
    if len(loaded)!=1:raise ValueError('Load did not report one exact runnable ID; no inferred tag/config fallback')
    image=image_identity(loaded[0].decode())
    compare_config(expected['config'],image)
    write(receipt,{'state':'loaded_verified','at':utc(),'archive_sha256':expected['sha256'],'export_config_sha256':expected['config_sha256'],
        'baked_image_id':value['manifest']['image']['Id'],'loaded_image':image,'wrapper_id_equal':image['Id']==value['manifest']['image']['Id'],
        'identity_claim':'Exact exported config bytes and ordered rootfs; wrapper equality recorded separately'})
    print(image['Id'])


def host_verify(descriptor, recipe, receipt, baked=CACHE/'manifest.json'):
    value = validate(json.loads(Path(descriptor).read_text()), recipe)
    if Path(baked).is_symlink() or digest(baked) != value['manifest_sha256'] or json.loads(Path(baked).read_text()) != value['manifest']:
        raise ValueError('Baked manifest differs from admitted artifact')
    actual = {'image': image_identity(value['manifest']['image']['Id']), 'host': stable_host()}
    if actual['image'] != value['manifest']['image'] or actual['host'] != value['manifest']['host']:
        raise ValueError('Materialized image or host stack differs; no fallback rebuild')
    write(receipt, {'state': 'verified', 'at': utc(), 'descriptor_sha256': digest(descriptor), 'baked_manifest_sha256': digest(baked), **actual})
    print(actual['image']['Id'])


def check_aws_directory(path, name):
    """Only absence/empty real directories; never read contents or clear state."""
    record={'path':name,'state':'unknown'}
    try:
        metadata=path.lstat()
        record.update(mode=stat.S_IMODE(metadata.st_mode),uid=metadata.st_uid,gid=metadata.st_gid)
        if stat.S_ISLNK(metadata.st_mode):record.update(type='symlink',state='refused_symlink')
        elif not stat.S_ISDIR(metadata.st_mode):record.update(type='non_directory',state='refused_non_directory')
        else:
            record['type']='directory'
            descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                opened=os.fstat(descriptor)
                if (metadata.st_dev,metadata.st_ino)!=(opened.st_dev,opened.st_ino):
                    record['state']='refused_changed_identity'
                else:
                    with os.scandir(descriptor) as entries:nonempty=next(entries,None) is not None
                    record.update(state='refused_nonempty' if nonempty else 'empty_directory',
                                  entries_observed=1 if nonempty else 0,entry_count_is_lower_bound=nonempty)
            finally:os.close(descriptor)
    except FileNotFoundError:
        # Only a missing initial lstat establishes absence. A path disappearing
        # after its metadata was observed is an uncertain change, not permission.
        record['state']='absent' if 'type' not in record else 'refused_changed_identity'
    except OSError as error:
        record.update(state='refused_unreadable',error_type=type(error).__name__,errno=error.errno)
    finally:
        # Original build/seal Actions retain stderr even before a manifest exists.
        # No child names, target strings, contents or exception messages are logged.
        print('image-builder-aws-directory '+json.dumps(record,sort_keys=True),file=sys.stderr,flush=True)
    if record['state'] not in ('absent','empty_directory'):
        raise ValueError('Builder AWS path refused: '+name+' ('+record['state']+')')


def remove_base_cli_telemetry(root, system=Path('/')):
    """Remove only the official CLI's machine-local session IDs on a fresh builder.

    This is not credential sanitization. Any other AWS file/layout is refused.
    See aws/aws-cli v2 awscli/telemetry.py for the two-table database format.
    """
    if root != CACHE or root.is_symlink():
        raise ValueError('Only the dedicated image bake directory is supported')
    records = []
    removals = []
    receipt = root / 'base-cli-telemetry.json'
    if receipt.exists():
        raise ValueError('Base telemetry preparation already recorded; do not replay')
    for name in ('/root/.aws', '/home/ubuntu/.aws'):
        directory = system / name.lstrip('/')
        if not directory.exists() or (directory.is_dir() and not any(directory.iterdir())):
            continue
        expected = [directory, directory / 'cli', directory / 'cli/cache']
        if any(p.is_symlink() or not p.is_dir() for p in expected):
            raise ValueError('Unexpected builder CLI cache directory')
        if any(set(p.iterdir()) != {child} for p, child in zip(expected, expected[1:] + [directory / 'cli/cache/session.db'])):
            raise ValueError('Unexpected builder AWS state; no cleanup performed')
        database = directory / 'cli/cache/session.db'
        if database.is_symlink() or not database.is_file():
            raise ValueError('Unexpected builder CLI session database')
        before = database.stat(); original = digest(database)
        with sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
            if set(db.execute("SELECT type,name FROM sqlite_master")) != {
                    ('table','session'), ('table','host_id'),
                    ('index','sqlite_autoindex_session_1'), ('index','sqlite_autoindex_host_id_1')}:
                raise ValueError('Unknown CLI session database schema')
            for table, columns in [('session', ['key','session_id','timestamp']), ('host_id', ['key','id'])]:
                if [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')] != columns:
                    raise ValueError('Unknown CLI session columns')
            sessions = list(db.execute('SELECT key,session_id,timestamp FROM session'))
            hosts = list(db.execute('SELECT key,id FROM host_id'))
            if (len(hosts) != 1 or hosts[0][0] != 0 or str(uuid.UUID(hosts[0][1])) != hosts[0][1]
                    or any(not isinstance(k,str) or not re.fullmatch('[0-9a-f]{12}',k)
                           or not isinstance(v,str) or not re.fullmatch('[0-9a-f]{12}',v)
                           or type(t) is not int or t <= 0 for k,v,t in sessions)):
                raise ValueError('Unknown CLI session database contents')
        after = database.stat()
        if (before.st_ino,before.st_size,before.st_mtime_ns) != (after.st_ino,after.st_size,after.st_mtime_ns) or digest(database) != original:
            raise ValueError('CLI telemetry changed during observation')
        records.append({'path':name, 'state':'prepared_vendor_session_ids', 'sha256':original,
                        'session_rows':len(sessions), 'host_rows':len(hosts)})
        removals.append((database, expected))
    write(receipt, records)
    for record, (database, directories) in zip(records, removals):
        database.unlink()
        for path in reversed(directories): path.rmdir()
        record['state'] = 'removed_vendor_session_ids'
        write(receipt, records)


def check_clean_builder(root, system=Path('/')):
    if root != CACHE or root.is_symlink():
        raise ValueError('Only the dedicated image bake directory is supported')
    for name in ('/opt/a4-replica', '/weights', '/root/.aws', '/home/ubuntu/.aws', '/root/.huggingface', '/home/ubuntu/.cache/huggingface', '/root/.cache/huggingface'):
        if name in ('/root/.aws','/home/ubuntu/.aws'):
            check_aws_directory(system/name.lstrip('/'),name)
        elif (system/name.lstrip('/')).exists():
            raise ValueError('Builder is not image-only: '+name)
    if output(['docker', 'ps', '-aq']):
        raise ValueError('Dedicated builder has existing containers')
    for name in ('/root/.docker/config.json', '/home/ubuntu/.docker/config.json'):
        if (system/name.lstrip('/')).exists():
            raise ValueError('Builder has registry credential/config state')
    for home in ('root','home/ubuntu'):
        for name in ('.cache/torch','.cache/torch_extensions','.cache/triton','.triton','.cache/flashinfer','.cache/tilelang','.nv/ComputeCache'):
            if (system/home/name).exists():raise ValueError('Builder has model/compiler cache state: '+home+'/'+name)


def host_build(root, artifact, system=Path('/')):
    if (root/'manifest.json').exists() or (root/'build.started').exists():
        raise ValueError('Build was already dispatched; inspect retained outcome, do not replay')
    remove_base_cli_telemetry(root,system)
    check_clean_builder(root,system)
    if not re.fullmatch(r'[0-9a-f]{32}', artifact):
        raise ValueError('Invalid artifact identity')
    (root/'build.started').write_text(utc())
    tag = 'a4-image-snapshot:'+artifact
    subprocess.run(['docker', 'build', '--iidfile', str(root/'build.id'), '-t', tag, '-f', str(root/'Dockerfile.gpu'), str(root)], check=True)
    image = image_identity(tag)
    (root/'image-inspect.json').write_text(output(['docker', 'image', 'inspect', image['Id']])+'\n')
    # No model, mounted campaign state, network, or compiler cache in this probe.
    probe = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--gpus', 'all', '--entrypoint', 'python3', image['Id'], '-c',
                            'import json,torch,importlib.metadata as m; print(json.dumps({"torch":torch.__version__,"cuda":torch.version.cuda,"device":torch.cuda.get_device_name(0),"sglang":m.version("sglang"),"flashinfer":m.version("flashinfer-python")}))'], capture_output=True, check=True)
    (root/'software.json').write_bytes(probe.stdout)
    check_clean_builder(root,system)
    manifest = {'schema': SCHEMA, 'artifact_id': artifact, 'recipe_sha256': digest(root/'Dockerfile.gpu'), 'image': image, 'host': stable_host(), 'created_at': utc()}
    write(root/'manifest.json', manifest)


def host_seal(root, system=Path('/')):
    check_clean_builder(root,system)
    if not (root/'manifest.json').is_file():
        raise ValueError('No completed image build')
    subprocess.run(['systemctl', 'stop', 'docker.service', 'docker.socket', 'containerd.service'], check=True)
    for unit in ('docker.service', 'docker.socket', 'containerd.service'):
        if subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode == 0:
            raise ValueError('Daemon is still writing: '+unit)
    # Never-secrets builder only. This is not sanitization of a former campaign.
    for home in (system/'root', system/'home/ubuntu'):
        for name in ('.ssh/authorized_keys', '.ssh/known_hosts', '.bash_history', '.python_history'):
            (home/name).unlink(missing_ok=True)
    for path in (system/'etc/ssh').glob('ssh_host_*'):
        if path.is_file(): path.unlink()
    subprocess.run(['cloud-init', 'clean', '--logs', '--machine-id', '--seed'], check=True)
    subprocess.run(['sync'], check=True)
    write(root/'sealed.json', {'state': 'daemons_stopped_image_only', 'at': utc(), 'manifest_sha256': digest(root/'manifest.json')})


class Actions:
    """A durable one-dispatch journal, not a retry controller."""
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)

    def once(self, name, argv, runner=None):
        path = self.root/(name+'.json')
        with path.open('x') as out:
            json.dump({'state': 'dispatch_uncertain', 'at': utc(), 'argv': argv}, out)
            out.flush(); os.fsync(out.fileno())
        try:
            result = (runner or subprocess.run)(argv, capture_output=True, timeout=120)
            (self.root/(name+'.stdout')).write_bytes(result.stdout)
            (self.root/(name+'.stderr')).write_bytes(result.stderr)
            write(path, {'state': 'returned', 'at': utc(), 'argv': argv, 'exit_code': result.returncode})
            if result.returncode: raise RuntimeError(name+' failed; no automatic replay')
            return result.stdout
        except BaseException as error:
            if isinstance(error, subprocess.TimeoutExpired):
                (self.root/(name+'.stdout')).write_bytes(error.stdout or b'')
                (self.root/(name+'.stderr')).write_bytes(error.stderr or b'')
            old = json.loads(path.read_text()); old['error'] = type(error).__name__+': '+str(error)
            write(path, old); raise

    def export(self,name,argv,destination,image):
        """Stream once to a single local file; retain partial export on failure."""
        path=self.root/(name+'.json');start=time.time()
        with path.open('x') as out:json.dump({'state':'dispatch_uncertain','at':utc(),'argv':argv},out);out.flush();os.fsync(out.fileno())
        try:
            with Path(destination).open('xb') as out,(self.root/(name+'.stderr')).open('xb') as err:
                proc=subprocess.run(argv,stdout=out,stderr=err,timeout=None)
                out.flush();os.fsync(out.fileno())
            write(path,{'state':'returned','at':utc(),'elapsed_s':time.time()-start,'argv':argv,'exit_code':proc.returncode,'bytes':Path(destination).stat().st_size})
            if proc.returncode:raise RuntimeError('Image export failed; partial archive retained, no replay')
            identity=archive_identity(destination,image)
            target=Path(destination).with_name(identity['sha256']+'.tar')
            # Never overwrite an existing supposedly identical local artifact.
            os.link(destination,target);Path(destination).unlink();identity['path']=str(target.resolve())
            return identity
        except BaseException as error:
            state=json.loads(path.read_text());state['error']=type(error).__name__+': '+str(error);write(path,state);raise


def storage_reference(value):
    """Keep old financial references as history; new records name their owner/claim."""
    keys = {'storage_owner', 'storage_claim_sha256', 'storage_liability'} & set(value)
    if keys == {'storage_liability'}:
        if not isinstance(value['storage_liability'], str) or not value['storage_liability']:
            raise ValueError('Invalid historical storage reference')
    elif keys == {'storage_owner', 'storage_claim_sha256'}:
        if not isinstance(value['storage_owner'], str) or not value['storage_owner'].strip():
            raise ValueError('Retained storage owner required')
        if not isinstance(value['storage_claim_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['storage_claim_sha256']):
            raise ValueError('Immutable storage claim identity required')
    else:
        raise ValueError('Exact historical or nonfinancial storage ownership required')
    return {key:value[key] for key in sorted(keys)}


def claim_storage(path, owner, artifact, retain_until, location, binding='builder_run_root'):
    """Claim one new operation exclusively before dispatch, without financial gates.

    Reusing the same intent path is refused, including after uncertain creation.
    Claims are never removed or recycled by cleanup/retirement. Historical account
    ledgers and their funded claims are neither read nor repurposed.
    """
    if binding not in ('builder_run_root','publication_receipts'):
        raise ValueError('Unsupported storage operation owner')
    if not isinstance(owner, str) or not owner.strip():raise ValueError('Retained storage owner required')
    if not isinstance(artifact, str) or not re.fullmatch(r'[0-9a-f]{32}',artifact):raise ValueError('Exact artifact identity required')
    if type(retain_until) not in (int,float) or not math.isfinite(retain_until) or retain_until <= time.time():
        raise ValueError('Finite future retirement time required')
    path=Path(path).absolute();path.parent.mkdir(parents=True,exist_ok=True)
    claim={'schema':'a4-storage-claim-v2','at':utc(),'artifact_id':artifact,
           'storage_owner':owner,'retain_until':retain_until,binding:str(Path(location).resolve())}
    with path.open('x') as out:
        json.dump(claim,out,indent=2);out.flush();os.fsync(out.fileno())
    return {'path':str(path),'sha256':digest(path),'claim':claim}


def cleanup_builder(run, call=aws):
    state = json.loads((run/'resources.json').read_text()); resource = state['resources']
    if state.get('resource_profile') != PROFILE:
        raise ValueError('Not a dedicated image builder')
    instance = call('ec2', 'describe-instances', '--instance-ids', resource['gpu']['id'])['Reservations'][0]['Instances'][0]
    actual = {x['Key']: x['Value'] for x in instance['Tags']}
    if instance['InstanceId'] != resource['gpu']['id'] or actual.get('Owner') != 'a4-local-replica' or actual.get('Campaign') != state['name']:
        raise ValueError('Builder ownership changed')
    acts = Actions(run/'image-cleanup-actions')
    if instance['State']['Name'] != 'terminated':
        if instance['State']['Name'] != 'shutting-down':
            acts.once('terminate', ['aws', 'ec2', 'terminate-instances', '--instance-ids', instance['InstanceId'], '--region', state['region']])
        subprocess.run(['aws', 'ec2', 'wait', 'instance-terminated', '--instance-ids', instance['InstanceId'], '--region', state['region']], check=True, timeout=600)
    group = call('ec2', 'describe-security-groups', '--group-ids', resource['security_group'])['SecurityGroups'][0]
    actual = {x['Key']: x['Value'] for x in group['Tags']}
    if actual.get('Owner') != 'a4-local-replica' or actual.get('Campaign') != state['name']:
        raise ValueError('Builder network ownership changed')
    acts.once('group', ['aws', 'ec2', 'delete-security-group', '--group-id', resource['security_group'], '--region', state['region']])
    key=call('ec2','describe-key-pairs','--key-names',resource['key_name'])['KeyPairs'][0]
    actual={x['Key']:x['Value'] for x in key.get('Tags',[])}
    if key['KeyName']!=resource['key_name'] or actual.get('Owner')!='a4-local-replica' or actual.get('Campaign')!=state['name']:raise ValueError('Builder SSH key ownership changed')
    acts.once('key', ['aws', 'ec2', 'delete-key-pair', '--key-name', resource['key_name'], '--region', state['region']])
    write(run/'cleanup.json', {'completed_at': utc(), 'security_group_deleted': True, 'key_pair_deleted': True, 'instance_terminated': instance['InstanceId'], 'retained_artifact_owned_separately': True})


def bake(args):
    artifact = uuid.uuid4().hex
    claim=claim_storage(args.claim,args.storage_owner,artifact,args.retain_until,args.run_root)
    storage={'storage_owner':args.storage_owner,'storage_claim_sha256':claim['sha256']}
    result = subprocess.check_output([sys.executable, str(HERE/'provision.py'), '--run-root', str(args.run_root), '--resource-profile', PROFILE], text=True)
    run = Path(result.splitlines()[0]); (run/'image-provision.log').write_text(result)
    write(run/'image-bake.json', {'artifact_id': artifact, 'state': 'allocated_image_only', 'recipe_sha256': digest(HERE/'Dockerfile.gpu'),
        'retain_until': args.retain_until, **storage, 'storage_claim':claim,
        'tool_sha256':{name:digest(HERE/name) for name in ('image_snapshot.py','provision.py','owned_transport.py')}})
    from owned_transport import Transport
    transport = Transport(run); acts = Actions(run/'image-actions')
    def ssh(command):
        argv=transport.args('gpu',command)
        return [argv[0],'-F','/dev/null','-o','ForwardAgent=no',*argv[1:]]
    try:
        def within_retention():
            if time.time()>=args.retain_until:raise RuntimeError('Artifact retention ended before build completion')
        while True:
            within_retention()
            instance = transport.instance('gpu')
            if instance['State']['Name'] in ('terminated', 'shutting-down', 'stopped'): raise RuntimeError('Builder ended before access')
            if instance.get('PublicIpAddress'):
                transport.resources['gpu'].update(public_ip=instance['PublicIpAddress'], private_ip=instance['PrivateIpAddress'])
                write(run/'resources.json', transport.state); break
            time.sleep(5)
        # Reachability probes are read-only; no build or mutation is retried.
        while subprocess.run(ssh('true'), capture_output=True, timeout=20).returncode:
            within_retention();time.sleep(5)
        acts.once('mkdir', ssh('sudo mkdir /opt/a4-image-cache && sudo chown ubuntu:ubuntu /opt/a4-image-cache'))
        for name in ('image_snapshot.py', 'Dockerfile.gpu'):
            # No campaign payload, SSH private key, serve.key, model or API secret.
            argv = ['scp', '-F', '/dev/null', '-i', str(run/'ssh-key'), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile='+str(run/'known_hosts'), str(HERE/name), 'ubuntu@'+instance['PublicIpAddress']+':/opt/a4-image-cache/'+name]
            acts.once('upload-'+name, argv)
        # Build can be longer than a transport call. Exact dispatch is single-shot;
        # existing infrastructure host shutdown is the lifetime authority.
        build = ssh('sudo python3 /opt/a4-image-cache/image_snapshot.py host-build --artifact '+artifact)
        acts.once('build', build, runner=lambda argv, **kw: subprocess.run(argv, capture_output=True, timeout=None))
        for name in ('manifest.json', 'software.json', 'build.id', 'image-inspect.json', 'base-cli-telemetry.json'):
            raw = subprocess.check_output(ssh('cat /opt/a4-image-cache/'+name), timeout=30)
            (run/name).write_bytes(raw)
        archive=None
        if args.export_directory:
            args.export_directory.mkdir(parents=True,exist_ok=True)
            image=json.loads((run/'manifest.json').read_text())['image']
            archive=acts.export('export',ssh('sudo docker image save --platform linux/amd64 '+shlex.quote(image['Id'])),args.export_directory/(artifact+'.partial.tar'),image)
            write(run/'export.json',archive)
        acts.once('seal', ssh('sudo python3 /opt/a4-image-cache/image_snapshot.py host-seal'))
        acts.once('stop', ['aws', 'ec2', 'stop-instances', '--instance-ids', instance['InstanceId'], '--region', 'us-east-1'])
        subprocess.run(['aws', 'ec2', 'wait', 'instance-stopped', '--instance-ids', instance['InstanceId'], '--region', 'us-east-1'], check=True, timeout=600)
        stopped = transport.instance('gpu')
        if stopped['State']['Name'] != 'stopped': raise RuntimeError('Consistent stopped builder not confirmed')
        write(run/'stopped-builder.json', stopped)
        raw = acts.once('create-image', ['aws', 'ec2', 'create-image', '--instance-id', instance['InstanceId'], '--name', 'a4-image-'+artifact,
            '--tag-specifications', json.dumps([{'ResourceType': kind, 'Tags': tags(artifact)} for kind in ('image', 'snapshot')]), '--region', 'us-east-1', '--output', 'json'])
        ami = json.loads(raw)['ImageId']
        write(run/'created-image.json', {'ami_id': ami, 'artifact_id': artifact, **storage})
        subprocess.run(['aws', 'ec2', 'wait', 'image-available', '--image-ids', ami, '--region', 'us-east-1'], check=True, timeout=1200)
        image = aws('ec2', 'describe-images', '--owners', 'self', '--image-ids', ami)['Images'][0]
        descriptor = {'schema': DESCRIPTOR_SCHEMA, 'artifact_id': artifact, 'region': 'us-east-1', 'owner_account': aws('sts', 'get-caller-identity')['Account'],
            'base_ami': BASE_AMI, 'ami_id': ami, 'snapshot_id': next(b['Ebs']['SnapshotId'] for b in image['BlockDeviceMappings'] if b['DeviceName'] == '/dev/sda1'), 'root_device': '/dev/sda1', 'volume_gib': 300,
            'recipe_sha256': digest(HERE/'Dockerfile.gpu'), 'manifest_sha256': digest(run/'manifest.json'), 'manifest': json.loads((run/'manifest.json').read_text()),
            'retain_until': args.retain_until, **storage}
        if archive:descriptor['archive']=archive
        write(run/'artifact-cloud-verification.json', verify_cloud(descriptor))
        write(run/'image-snapshot.json', descriptor)
        print(json.dumps({'run': str(run), 'descriptor': str(run/'image-snapshot.json'), 'storage_remains_open': True}))
    finally:
        # Never route this builder through ordinary evidence collection. Retained
        # AMI/snapshot IDs stay outside cleanup and must be reconciled even on error.
        cleanup_builder(run)


def reconcile(run, call=aws):
    """Read-only discovery after uncertain create-image; never replay creation."""
    run=Path(run); record=json.loads((run/'image-bake.json').read_text()); artifact=record['artifact_id']
    images=call('ec2','describe-images','--owners','self','--filters','Name=tag:Artifact,Values='+artifact)['Images']
    snapshots=call('ec2','describe-snapshots','--owner-ids','self','--filters','Name=tag:Artifact,Values='+artifact)['Snapshots']
    for row in [*images,*snapshots]:owned(row,artifact)
    value={'at':utc(),'images':images,'snapshots':snapshots,'state':'pending_or_unknown',
        **storage_reference(record),'historical_claims_unchanged':True}
    if len(images)==1 and images[0]['State']=='available' and len(snapshots)==1:
        descriptor={'schema':SCHEMA if 'storage_liability' in record else DESCRIPTOR_SCHEMA,'artifact_id':artifact,'region':'us-east-1','owner_account':call('sts','get-caller-identity')['Account'],
            'base_ami':BASE_AMI,'ami_id':images[0]['ImageId'],'snapshot_id':snapshots[0]['SnapshotId'],'root_device':'/dev/sda1','volume_gib':300,
            'recipe_sha256':record['recipe_sha256'],'manifest_sha256':digest(run/'manifest.json'),'manifest':json.loads((run/'manifest.json').read_text()),
            'retain_until':record['retain_until'],**storage_reference(record)}
        if (run/'export.json').is_file():descriptor['archive']=json.loads((run/'export.json').read_text())
        value['verification']=verify_cloud(descriptor,call);value['state']='available_exact_artifact'
        write(run/'image-snapshot.json',descriptor)
    write(run/'image-reconciliation.json',value)
    return value


def retire(descriptor, directory):
    value = json.loads(Path(descriptor).read_text())
    # Expiry forbids use, not retirement.
    validate(value, now=0)
    if aws('sts','get-caller-identity')['Account'] != value['owner_account']:raise ValueError('Artifact account mismatch')
    images=aws('ec2','describe-images','--owners','self','--filters','Name=image-id,Values='+value['ami_id'])['Images']
    snapshots=aws('ec2','describe-snapshots','--owner-ids','self','--filters','Name=snapshot-id,Values='+value['snapshot_id'])['Snapshots']
    for row in [*images,*snapshots]:owned(row,value['artifact_id'])
    if len(images)>1 or len(snapshots)>1:raise ValueError('Ambiguous owned retirement')
    write(Path(directory)/('observed-'+uuid.uuid4().hex+'.json'),{'at':utc(),'images':images,'snapshots':snapshots})
    acts = Actions(directory)
    if images:
        if len(snapshots)!=1:raise ValueError('AMI present but expected root snapshot missing')
        verify_cloud(value,now=0)
        acts.once('deregister', ['aws', 'ec2', 'deregister-image', '--image-id', value['ami_id'], '--region', value['region']])
    if snapshots:acts.once('delete-snapshot', ['aws', 'ec2', 'delete-snapshot', '--snapshot-id', value['snapshot_id'], '--region', value['region']])
    remaining_images=aws('ec2','describe-images','--owners','self','--filters','Name=image-id,Values='+value['ami_id'])['Images']
    remaining_snapshots=aws('ec2','describe-snapshots','--owner-ids','self','--filters','Name=snapshot-id,Values='+value['snapshot_id'])['Snapshots']
    write(Path(directory)/'retirement.json', {'state': 'absence_observed' if not remaining_images and not remaining_snapshots else 'deletion_pending_or_unknown', 'at': utc(), 'ami_id': value['ami_id'], 'snapshot_id': value['snapshot_id'],
        **storage_reference(value), 'ownership': 'Claim remains immutable; retirement records observed absence and does not rewrite history'})


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    verify = sub.add_parser('host-verify'); verify.add_argument('--descriptor', type=Path, required=True); verify.add_argument('--recipe', type=Path, required=True); verify.add_argument('--receipt', type=Path, required=True)
    load=sub.add_parser('host-load');load.add_argument('--descriptor',type=Path,required=True);load.add_argument('--archive',type=Path,required=True);load.add_argument('--recipe',type=Path,required=True);load.add_argument('--receipt',type=Path,required=True)
    build = sub.add_parser('host-build'); build.add_argument('--artifact', required=True)
    sub.add_parser('host-seal')
    b = sub.add_parser('bake'); b.add_argument('--run-root', type=Path, required=True); b.add_argument('--claim', type=Path, required=True); b.add_argument('--storage-owner', required=True); b.add_argument('--retain-until', type=float, required=True)
    b.add_argument('--export-directory',type=Path)
    r = sub.add_parser('retire'); r.add_argument('--descriptor', type=Path, required=True); r.add_argument('--receipts', type=Path, required=True)
    rec=sub.add_parser('reconcile');rec.add_argument('--run',type=Path,required=True)
    args = parser.parse_args()
    if args.command == 'host-verify': host_verify(args.descriptor, args.recipe, args.receipt)
    elif args.command == 'host-load':host_load(args.descriptor,args.archive,args.recipe,args.receipt)
    elif args.command == 'host-build': host_build(CACHE, args.artifact)
    elif args.command == 'host-seal': host_seal(CACHE)
    elif args.command == 'bake': bake(args)
    elif args.command == 'retire': args.receipts.mkdir(parents=True, exist_ok=True); retire(args.descriptor, args.receipts)
    elif args.command == 'reconcile': print(json.dumps(reconcile(args.run),indent=2))


if __name__ == '__main__':
    main()
