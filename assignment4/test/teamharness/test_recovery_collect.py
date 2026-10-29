"""Offline ownership, finite-release and exact-copy checks. No process signals."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from lab.adapters.teamharness.recovery_collect import process_matches, ready_to_release, verify_copy


class RecoveryCollectionTests(unittest.TestCase):
    def test_process_identity_requires_exact_path_operation_and_sweep(self):
        run = Path('/tmp/owned-run').resolve()
        command = f'python3 -u {run}/fleet-source/dev/fleet/fleet.py sweep --sweep exact-sweep --hosts 4'
        self.assertTrue(process_matches(command, run, 'exact-sweep'))
        for changed in (command.replace('owned-run/', 'owned-run-other/'), command.replace('exact-sweep', 'exact-sweep-other'),
                        command.replace(' sweep ', ' status '), command + ' --sweep exact-sweep'):
            self.assertFalse(process_matches(changed, run, 'exact-sweep'))

    def test_release_requires_all_verified_or_hard_bound(self):
        cases = {'4': {'verified': True}, '18': {'verified': False}, '21': {'verified': True}}
        self.assertFalse(ready_to_release(cases, 99, 100))
        self.assertTrue(ready_to_release(cases, 100, 100))
        cases['18']['verified'] = True
        self.assertTrue(ready_to_release(cases, 99, 100))
        self.assertFalse(ready_to_release({}, 99, 100))

    def test_copied_archive_must_match_original_path_bytes_and_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = b'original public archive bytes'
            (root / 'recovery-evidence.tar.gz').write_bytes(data)
            receipt = {'path': '/data/recovery/task/recovery-evidence.tar.gz', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            (root / 'recovery-evidence.json').write_text(json.dumps(receipt))
            (root / 'recovery-status.json').write_text(json.dumps({'state': 'complete'}))
            self.assertTrue(verify_copy(root, '/data/recovery/task')['verified'])
            (root / 'recovery-evidence.tar.gz').write_bytes(data.replace(b'original', b'changed!'))
            with self.assertRaises(ValueError): verify_copy(root, '/data/recovery/task')
            (root / 'recovery-evidence.tar.gz').write_bytes(data)
            receipt['bytes'] += 1
            (root / 'recovery-evidence.json').write_text(json.dumps(receipt))
            with self.assertRaises(ValueError): verify_copy(root, '/data/recovery/task')
            with self.assertRaises(ValueError): verify_copy(root, '/data/recovery/other')


if __name__ == '__main__':
    unittest.main()
