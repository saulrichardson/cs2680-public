"""Move closed fleet or explicitly selected replica archives to verified private S3 objects; restore on demand.

Machine-specific bucket/path bindings live in ~/.codex/evidence-storage/config.json.
SHA256 is verified with S3 (full object or ordered multipart composition), and a
durable remote receipt plus local pointer precede removal of the local archive.
No source files, active campaign inputs or extracted workspaces are removed.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

SINGLE_PUT_LIMIT = 5_000_000_000
PART_BYTES = 64 * 1024**2


def multipart_identity(path):
    whole = hashlib.sha256(); parts = []
    with path.open('rb') as stream:
        while raw := stream.read(PART_BYTES):
            whole.update(raw); parts.append(hashlib.sha256(raw).digest())
    if not 1 <= len(parts) <= 10000:
        raise ValueError('Unsupported multipart count; original retained')
    return whole.digest(), {'type': 'COMPOSITE', 'part_bytes': PART_BYTES,
        'sha256': base64.b64encode(hashlib.sha256(b''.join(parts)).digest()).decode() + '-' + str(len(parts)),
        'parts': [base64.b64encode(p).decode() for p in parts]}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').digest()


def save(path, value):
    temp = path.with_name(path.name + '.pending')
    with temp.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    temp.replace(path)


def files(root, minimum_age=900):
    closed = {}
    for sweep in root.iterdir():
        manifest = sweep / 'sweep.json'
        if not manifest.is_file():
            continue
        try:
            hosts = json.loads(manifest.read_text()).get('hosts', [])
            closed[sweep.name] = bool(hosts) and all(h.get('terminated') for h in hosts)
        except (OSError, ValueError):
            closed[sweep.name] = False
    result = []
    for path in root.rglob('*.tar.zst'):
        relative = path.relative_to(root)
        if (closed.get(relative.parts[0]) and not path.is_symlink()
                and path.is_file() and time.time() - path.stat().st_mtime >= minimum_age):
            result.append(path)
    # Reclaim space early rather than occupy every upload slot with multi-GB
    # files before the first verified local removal can occur.
    return sorted(result, key=lambda p: (p.stat().st_size > 128 * 1024**2, -p.stat().st_size))


def replica_files(run, minimum_age=900):
    """Only original collected archives of an exactly closed replica allocation."""
    try:
        state = json.loads((run / 'resources.json').read_text())
        cleanup = json.loads((run / 'cleanup.json').read_text())
        events = [json.loads(line) for line in (run / 'controller-events.jsonl').read_text().splitlines()]
    except (OSError, ValueError):
        return []
    hosts = {role: row['id'] for role, row in state['resources'].items() if role in ('cpu','gpu')}
    actual = [i for r in cleanup.get('instances', {}).get('Reservations', []) for i in r['Instances']]
    if (state.get('name') != run.name or not hosts or not cleanup.get('completed_at')
            or cleanup.get('security_group_deleted') is not True or cleanup.get('key_pair_deleted') is not True
            or {i['InstanceId'] for i in actual} != set(hosts.values())
            or any(i['State']['Name'] != 'terminated' or
                   {t['Key']: t['Value'] for t in i.get('Tags', [])}.get('Campaign') != run.name for i in actual)
            or not any(e.get('event') == 'cleanup_verified' for e in events)):
        return []
    result = []
    for role in hosts:
        path = run / (role + '-evidence.tar.gz')
        receipts = [e for e in events if e.get('event') == 'evidence_collected' and e.get('role') == role]
        if not receipts or not path.is_file() or path.is_symlink() or time.time() - path.stat().st_mtime < minimum_age:
            continue
        receipt = receipts[-1]
        if path.stat().st_size != receipt['bytes'] or digest(path).hex() != receipt['sha256']:
            raise ValueError('Replica archive differs from its original collection receipt: ' + role)
        result.append(path)
    return result


class Archive:
    def __init__(self, config):
        self.config = config
        self.root = Path(config['root']).resolve()
        self.catalog = Path(config['catalog_root'])
        self.bucket = config['bucket']
        self.s3 = boto3.client('s3', region_name=config['region'],
                              config=Config(max_pool_connections=12, read_timeout=180,
                                            retries={'mode': 'standard', 'max_attempts': 4}))
        self.lock = threading.Lock()
        self.object_locks = {}

    def checked_head(self, key, raw_sha, size, version=None, multipart=None):
        kwargs = dict(Bucket=self.bucket, Key=key, ChecksumMode='ENABLED')
        if version:
            kwargs['VersionId'] = version
        head = self.s3.head_object(**kwargs)
        if (head['ContentLength'] != size
                or head.get('ChecksumSHA256') != (multipart['sha256'] if multipart else base64.b64encode(raw_sha).decode())
                or (multipart and (head.get('ChecksumType') != 'COMPOSITE'
                                   or head.get('Metadata', {}).get('sha256') != raw_sha.hex()))
                or head.get('ServerSideEncryption') != 'AES256'
                or not head.get('VersionId') or head['VersionId'] == 'null'):
            raise ValueError('Remote object size, SHA256, encryption or version differs')
        return head

    def multipart_put(self, path, key, raw_sha, size, identity):
        """One owned upload; a durable intent prevents replay after uncertain dispatch."""
        journal = self.catalog / ('multipart-' + raw_sha.hex() + '.json')
        previous = json.loads(journal.read_text()) if journal.exists() else None
        if previous and previous['state'] != 'aborted':
            raise ValueError('Unsettled multipart intent; reconcile the recorded upload before retrying')
        record = {'state': 'creating', 'key': key, 'bucket': self.bucket,
                  'sha256': raw_sha.hex(), 'bytes': size, 'started_at': time.time(),
                  'previous': previous, 'parts_uploaded': 0}
        save(journal, record)
        upload_id = None
        try:
            response = self.s3.create_multipart_upload(Bucket=self.bucket, Key=key,
                ServerSideEncryption='AES256', ChecksumAlgorithm='SHA256',
                ChecksumType='COMPOSITE', Metadata={'sha256': raw_sha.hex()})
            upload_id = response['UploadId']; record.update(state='uploading', upload_id=upload_id)
            save(journal, record)
            parts = []
            with path.open('rb') as stream:
                for number, checksum in enumerate(identity['parts'], 1):
                    raw = stream.read(identity['part_bytes'])
                    if base64.b64encode(hashlib.sha256(raw).digest()).decode() != checksum:
                        raise ValueError('Local multipart source changed; original retained')
                    response = self.s3.upload_part(Bucket=self.bucket, Key=key, UploadId=upload_id,
                        PartNumber=number, Body=raw, ChecksumSHA256=checksum)
                    if response.get('ChecksumSHA256') != checksum:
                        raise ValueError('S3 part checksum differs; original retained')
                    parts.append({'PartNumber': number, 'ETag': response['ETag'], 'ChecksumSHA256': checksum})
                    record['parts_uploaded'] = number; save(journal, record)
                if stream.read(1):
                    raise ValueError('Local multipart source grew; original retained')
            record['state'] = 'completing'; save(journal, record)
            response = self.s3.complete_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id,
                MultipartUpload={'Parts': parts}, ChecksumType='COMPOSITE',
                ChecksumSHA256=identity['sha256'], MpuObjectSize=size, IfNoneMatch='*')
            record.update(state='completed', version_id=response['VersionId'], finished_at=time.time())
            save(journal, record)
            return response
        except Exception as error:
            record.update(state='uncertain', error=type(error).__name__ + ': ' + str(error))
            # Abort only this known upload. A completed object is never deleted.
            if upload_id:
                try:
                    self.s3.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
                    record['state'] = 'aborted'
                except Exception as abort_error:
                    record['abort_error'] = type(abort_error).__name__ + ': ' + str(abort_error)
            save(journal, record)
            raise

    def offload(self, path):
        before = path.stat()
        raw_sha, multipart = multipart_identity(path) if before.st_size > SINGLE_PUT_LIMIT else (digest(path), None)
        hex_sha = raw_sha.hex()
        relative = str(path.relative_to(self.root))
        suffix = '.tar.gz' if path.name.endswith('.tar.gz') else '.tar.zst'
        key = 'objects/sha256/' + hex_sha[:2] + '/' + hex_sha + suffix
        with self.lock:
            object_lock = self.object_locks.setdefault(key, threading.Lock())
        with object_lock:
            try:
                head = self.checked_head(key, raw_sha, before.st_size, multipart=multipart)
            except ClientError as error:
                if error.response['Error']['Code'] not in ('404', 'NoSuchKey'):
                    raise
                if multipart:
                    response = self.multipart_put(path, key, raw_sha, before.st_size, multipart)
                else:
                    with path.open('rb') as body:
                        response = self.s3.put_object(Bucket=self.bucket, Key=key, Body=body,
                            ContentLength=before.st_size, ChecksumSHA256=base64.b64encode(raw_sha).decode(),
                            ServerSideEncryption='AES256', IfNoneMatch='*', Metadata={'sha256': hex_sha})
                head = self.checked_head(key, raw_sha, before.st_size, response['VersionId'], multipart)
        receipt = {'schema': 1, 'original_path': str(path), 'relative_path': relative,
            'bytes': before.st_size, 'sha256': hex_sha, 'bucket': self.bucket, 'key': key,
            'version_id': head['VersionId'], 'region': self.config['region'],
            's3_uri': 's3://' + self.bucket + '/' + key, 'verified_at': time.time(),
            'verification': ('S3-validated ordered part SHA256 checksums and exact-version composite SHA256/length; full-source SHA256 retained'
                             if multipart else 'S3-validated full-object SHA256, exact-version HEAD checksum and length'),
            'source_mtime_ns': before.st_mtime_ns}
        if multipart: receipt['multipart'] = multipart
        receipt_key = 'receipts/' + self.config['prefix'] + '/' + relative + '.json'
        data = (json.dumps(receipt, indent=2) + '\n').encode()
        # Content-addressed immutable object + versioned path receipt. The object
        # exists and its checksum is verified before any local bytes are removed.
        response = self.s3.put_object(Bucket=self.bucket, Key=receipt_key, Body=data,
            ChecksumSHA256=base64.b64encode(hashlib.sha256(data).digest()).decode(),
            ServerSideEncryption='AES256', ContentType='application/json')
        self.checked_head(receipt_key, hashlib.sha256(data).digest(), len(data), response['VersionId'])
        receipt.update(remote_receipt_key=receipt_key, remote_receipt_version=response['VersionId'])
        pointer = Path(str(path) + '.remote.json')
        save(pointer, receipt)
        with self.lock:
            with (self.catalog / 'catalog.jsonl').open('a') as stream:
                stream.write(json.dumps(receipt) + '\n'); stream.flush(); os.fsync(stream.fileno())
        now = path.stat()
        if ((now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns)
                != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)):
            raise ValueError('Local archive changed during upload; local file retained')
        path.unlink()
        return receipt

    def restore(self, pointer, destination=None):
        receipt = json.loads(pointer.read_text())
        if receipt['bucket'] != self.bucket:
            raise ValueError('Pointer belongs to a different configured bucket')
        destination = Path(destination or receipt['original_path'])
        if destination.exists():
            raise ValueError('Restore destination exists; inspect it instead of overwriting')
        self.checked_head(receipt['key'], bytes.fromhex(receipt['sha256']), receipt['bytes'], receipt['version_id'], receipt.get('multipart'))
        response = self.s3.get_object(Bucket=self.bucket, Key=receipt['key'], VersionId=receipt['version_id'])
        temp = destination.with_name(destination.name + '.restoring')
        try:
            with temp.open('xb') as stream:
                for chunk in response['Body'].iter_chunks(1024 * 1024):
                    stream.write(chunk)
                stream.flush(); os.fsync(stream.fileno())
            if temp.stat().st_size != receipt['bytes'] or digest(temp).hex() != receipt['sha256']:
                raise ValueError('Downloaded archive checksum/size differs')
            os.link(temp, destination)  # Exclusive publication, never clobber.
            os.utime(destination, ns=(receipt['source_mtime_ns'], receipt['source_mtime_ns']))
        finally:
            response['Body'].close()
            temp.unlink(missing_ok=True)
        return {'restored': str(destination), 'sha256': receipt['sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path.home()/'.codex/evidence-storage/config.json')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--plan', action='store_true')
    group.add_argument('--offload', action='store_true')
    group.add_argument('--restore', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--replica-run', type=Path, help='Explicit completed replica run; preserve frozen inputs and offload only verified collected archives')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.replica_run:
        if args.restore: parser.error('--replica-run applies to plan/offload only')
        args.replica_run = args.replica_run.resolve()
        config = dict(config, root=str(args.replica_run.parent), prefix='replica')
    archive = Archive(config)
    if args.restore:
        print(json.dumps(archive.restore(args.restore, args.destination), indent=2)); return
    paths = replica_files(args.replica_run) if args.replica_run else files(archive.root)
    plan = {'files': len(paths), 'bytes': sum(p.stat().st_size for p in paths),
            'bucket': archive.bucket, 'started_at': time.time()}
    if args.plan:
        print(json.dumps(plan, indent=2)); return
    with (archive.catalog/'offload.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = dict(plan, removed=0, removed_bytes=0, errors=[])
        save(archive.catalog/'status.json', status)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(archive.offload, path): path for path in paths}
            for future in as_completed(futures):
                try:
                    receipt = future.result()
                    status['removed'] += 1; status['removed_bytes'] += receipt['bytes']
                except Exception as error:
                    status['errors'].append({'path': str(futures[future]), 'error': str(error)})
                status['updated_at'] = time.time()
                save(archive.catalog/'status.json', status)
        status['finished_at'] = time.time(); save(archive.catalog/'status.json', status)
        print(json.dumps(status, indent=2))


if __name__ == '__main__':
    main()
