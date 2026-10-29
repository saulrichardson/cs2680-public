import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError
from lab.storage.evidence_archive import Archive, files, replica_files


class Store:
    def __init__(self):
        self.objects = {}
        self.bad_receipt = False

    def put_object(self, **args):
        raw = args['Body'].read() if hasattr(args['Body'], 'read') else args['Body']
        checksum = base64.b64encode(hashlib.sha256(raw).digest()).decode()
        assert args['ChecksumSHA256'] == checksum
        self.objects[args['Key']] = raw
        return {'VersionId': 'version-1'}

    def head_object(self, **args):
        if args['Key'] not in self.objects:
            raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        raw = self.objects[args['Key']]
        checksum = base64.b64encode(hashlib.sha256(raw).digest()).decode()
        if self.bad_receipt and args['Key'].startswith('receipts/'):
            checksum = 'wrong'
        return {'ContentLength': len(raw), 'ChecksumSHA256': checksum,
                'VersionId': 'version-1', 'ServerSideEncryption': 'AES256'}


class MultipartStore(Store):
    def __init__(self):
        super().__init__(); self.parts = {}; self.multipart = {}; self.aborted = False
        self.bad_part = False; self.bad_head = False

    def create_multipart_upload(self, **args):
        self.metadata = args['Metadata']; return {'UploadId': 'owned-upload'}

    def upload_part(self, **args):
        raw = args['Body']; checksum = base64.b64encode(hashlib.sha256(raw).digest()).decode()
        assert checksum == args['ChecksumSHA256']
        self.parts[args['PartNumber']] = raw
        return {'ETag': str(args['PartNumber']), 'ChecksumSHA256': 'wrong' if self.bad_part else checksum}

    def complete_multipart_upload(self, **args):
        entries = args['MultipartUpload']['Parts']
        assert [e['PartNumber'] for e in entries] == list(range(1, len(entries) + 1))
        data = b''.join(self.parts[e['PartNumber']] for e in entries)
        checksums = b''.join(hashlib.sha256(self.parts[e['PartNumber']]).digest() for e in entries)
        composite = base64.b64encode(hashlib.sha256(checksums).digest()).decode() + '-' + str(len(entries))
        assert composite == args['ChecksumSHA256'] and len(data) == args['MpuObjectSize']
        assert args['IfNoneMatch'] == '*'
        self.objects[args['Key']] = data; self.multipart[args['Key']] = composite
        return {'VersionId': 'version-1'}

    def head_object(self, **args):
        result = super().head_object(**args)
        if args['Key'] in self.multipart:
            result.update(ChecksumSHA256='wrong' if self.bad_head else self.multipart[args['Key']],
                          ChecksumType='COMPOSITE', Metadata=self.metadata)
        return result

    def abort_multipart_upload(self, **args):
        assert args['UploadId'] == 'owned-upload'; self.aborted = True

    def get_object(self, **args):
        from botocore.response import StreamingBody
        raw = self.objects[args['Key']]
        return {'Body': StreamingBody(io.BytesIO(raw), len(raw))}


class EvidenceStorageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root/'completed/archive.tar.zst'
        self.source.parent.mkdir()
        self.source.write_bytes(b'exact original archived evidence')
        self.archive = object.__new__(Archive)
        self.archive.root = self.root
        self.archive.catalog = self.root
        self.archive.bucket = 'private-test-store'
        self.archive.config = {'region': 'us-east-1', 'prefix': 'fleet'}
        self.archive.s3 = Store()
        self.archive.lock = threading.Lock()
        self.archive.object_locks = {}

    def test_verified_object_and_remote_receipt_precede_local_removal(self):
        original = self.source.read_bytes()
        record = self.archive.offload(self.source)
        self.assertFalse(self.source.exists())
        pointer = Path(str(self.source)+'.remote.json')
        self.assertEqual(json.loads(pointer.read_text())['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.archive.s3.objects[record['key']], original)
        self.assertIn(record['remote_receipt_key'], self.archive.s3.objects)
        self.assertEqual(len((self.root/'catalog.jsonl').read_text().splitlines()), 1)

    def test_failed_remote_receipt_verification_keeps_original(self):
        self.archive.s3.bad_receipt = True
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Remote object'):
            self.archive.offload(self.source)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(Path(str(self.source)+'.remote.json').exists())

    def test_changed_local_file_is_retained_after_upload(self):
        original_put = self.archive.s3.put_object
        def change(**args):
            result = original_put(**args)
            if args['Key'].startswith('receipts/'):
                self.source.write_bytes(b'new writer changed this archive')
            return result
        self.archive.s3.put_object = change
        with self.assertRaisesRegex(ValueError, 'Local archive changed'):
            self.archive.offload(self.source)
        self.assertEqual(self.source.read_bytes(), b'new writer changed this archive')

    def test_multipart_offload_restore_and_exact_version_checksum(self):
        self.archive.s3 = MultipartStore(); original = self.source.read_bytes()
        with patch('lab.storage.evidence_archive.SINGLE_PUT_LIMIT', 1), patch('lab.storage.evidence_archive.PART_BYTES', 8):
            record = self.archive.offload(self.source)
        self.assertFalse(self.source.exists())
        self.assertGreater(len(record['multipart']['parts']), 1)
        self.archive.restore(Path(str(self.source) + '.remote.json'))
        self.assertEqual(self.source.read_bytes(), original)
        self.archive.s3.bad_head = True
        with self.assertRaisesRegex(ValueError, 'Remote object'):
            self.archive.restore(Path(str(self.source) + '.remote.json'), self.root / 'wrong.tar.zst')

    def test_failed_part_aborts_only_owned_upload_and_retains_original(self):
        self.archive.s3 = MultipartStore(); self.archive.s3.bad_part = True
        original = self.source.read_bytes()
        with patch('lab.storage.evidence_archive.SINGLE_PUT_LIMIT', 1), patch('lab.storage.evidence_archive.PART_BYTES', 8):
            with self.assertRaisesRegex(ValueError, 'part checksum'):
                self.archive.offload(self.source)
        self.assertTrue(self.archive.s3.aborted)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(Path(str(self.source) + '.remote.json').exists())

    def test_uncertain_creation_is_not_repeated(self):
        self.archive.s3 = MultipartStore(); calls = []
        def uncertain(**args):
            calls.append(args); raise TimeoutError('lost create response')
        self.archive.s3.create_multipart_upload = uncertain
        with patch('lab.storage.evidence_archive.SINGLE_PUT_LIMIT', 1), patch('lab.storage.evidence_archive.PART_BYTES', 8):
            with self.assertRaises(TimeoutError): self.archive.offload(self.source)
            with self.assertRaisesRegex(ValueError, 'Unsettled multipart intent'): self.archive.offload(self.source)
        self.assertEqual(len(calls), 1)
        self.assertTrue(self.source.exists())

    def test_active_campaign_is_not_eligible(self):
        manifest = self.source.parent/'sweep.json'
        manifest.write_text(json.dumps({'hosts': [{'terminated': False}]}))
        self.assertEqual(files(self.root, minimum_age=0), [])
        manifest.write_text(json.dumps({'hosts': [{'terminated': True}]}))
        self.assertEqual(files(self.root, minimum_age=0), [self.source])

    def test_replica_offload_requires_matching_closed_hosts_and_collected_bytes(self):
        run = self.root / 'replica-example'; run.mkdir()
        payload = run / 'cpu-evidence.tar.gz'; payload.write_bytes(b'original gzip fixture')
        state = {'name':run.name,'resources':{'cpu':{'id':'owned-cpu'}}}
        cleanup = {'completed_at':1,'security_group_deleted':True,'key_pair_deleted':True,
                   'instances':{'Reservations':[{'Instances':[{'InstanceId':'owned-cpu',
                       'State':{'Name':'running'},'Tags':[{'Key':'Campaign','Value':run.name}]}]}]}}
        (run / 'resources.json').write_text(json.dumps(state))
        (run / 'cleanup.json').write_text(json.dumps(cleanup))
        events = [{'event':'evidence_collected','role':'cpu','bytes':payload.stat().st_size,
                   'sha256':hashlib.sha256(payload.read_bytes()).hexdigest()}, {'event':'cleanup_verified'}]
        (run / 'controller-events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(replica_files(run, minimum_age=0), [])
        cleanup['instances']['Reservations'][0]['Instances'][0]['State']['Name']='terminated'
        (run / 'cleanup.json').write_text(json.dumps(cleanup))
        self.assertEqual(replica_files(run, minimum_age=0), [payload])
        payload.write_bytes(b'changed payload')
        with self.assertRaisesRegex(ValueError,'collection receipt'):
            replica_files(run, minimum_age=0)
        payload.write_bytes(b'original gzip fixture')
        record = self.archive.offload(payload)
        self.assertTrue(record['key'].endswith('.tar.gz'))
        self.assertFalse(payload.exists())


if __name__ == '__main__':
    unittest.main()
