"""Keep preselection expiry distinct from successful planning and missing grades."""
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lab/adapters/teamharness'))
from report_gpu import Archive, cell, digest, file_digest, report, report_member


class GPUReport(unittest.TestCase):
    def test_terminal_selection_overrides_preselection_eligibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = 'jobs/000/iteration/'
            trace = prefix + 'execution/course/.tasks/trace/madsServe_logs/'
            tasks = {str(i): {'instance_id': 'case-' + str(i), 'base_commit': 'base',
                'problem_statement': 'repair', 'requirements': '', 'interface': ''} for i in range(5)}
            manifest = json.dumps(tasks).encode()
            frozen = {'name': 'fixture', 'source_commit': 'commit', 'submission_sha256': 'package',
                'files': {'course/evaluation_scripts/agent_task_input.json': digest(manifest)},
                'build_receipt': {'campaign': {}}}
            frozen_raw = json.dumps(frozen).encode()
            plans = [{'task_id': i, 'eligible': True, 'recommendation': 'continue'} for i in range(5)]
            plans[2] = {'task_id': 2, 'eligible': False, 'error': 'planning exited'}
            plans[3]['recommendation'] = 'skip'
            plans[4] = {'task_id': 4, 'eligible': True, 'recommendation': None, 'assessment': None,
                        'planning': {'state': 'planning_complete', 'planning': {'result': 'planning_timeout'}}}
            expired = dict(plans[1], eligible=False, ineligible_reason='campaign_cutoff')
            selection = {'selected': [plans[0]], 'reserve': [expired, plans[2], plans[3], plans[4]], 'requested': 2}
            files = {prefix + 'experiment.json': frozen_raw,
                     prefix + 'course/evaluation_scripts/agent_task_input.json': manifest,
                     trace + 'allocation/selection.json': json.dumps(selection).encode(),
                     trace + '0/teamharness/run/source/baseline.tar': b'unneeded dependency bytes' * 400000}
            for i, plan in enumerate(plans):
                files[trace + str(i) + '/teamharness/planning.json'] = json.dumps(plan).encode()
            path = root / 'cpu-evidence.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                for name, raw in files.items():
                    member = tarfile.TarInfo(name); member.size = len(raw)
                    archive.addfile(member, io.BytesIO(raw))
            events = [{'event': 'evidence_collected', 'role': 'cpu',
                'sha256': file_digest(path), 'bytes': path.stat().st_size}]
            archive = Archive(root, 'cpu', events, retain=report_member)
            try:
                result = cell(archive, {'id': '000', 'manifest_sha256': digest(frozen_raw)})
            finally:
                archive.close()
            self.assertEqual(result['planning_states'], {'completed': 2, 'expired': 1, 'failed': 1, 'planning_timeout': 1})
            self.assertEqual(result['selection_states'], {'selected': 1, 'ineligible': 2, 'reserve': 2})
            self.assertEqual(result['eligibility_shortage'], 1)
            self.assertEqual((result['original_passes'], result['original_failures'], result['unknown_grades']), (0, 0, 5))
            self.assertTrue(result['tasks'][1]['planning']['eligible'])  # Original retained unchanged.
            self.assertFalse(result['tasks'][1]['selection_record']['eligible'])
            (root / 'controller-events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
            (root / 'batch.json').write_text(json.dumps({'jobs': [{'id': '000', 'manifest_sha256': digest(frozen_raw)}]}))
            report(root, root / 'selected.json')
            with patch('report_gpu.Archive', side_effect=lambda run, role, events, **kwargs: Archive(run, role, events)):
                report(root, root / 'reference.json')
            selected = json.loads((root / 'selected.json').read_text())
            reference = json.loads((root / 'reference.json').read_text())
            self.assertLess(selected.pop('archive_view')['retained_bytes'], 10000)
            reference.pop('archive_view')
            self.assertEqual(selected, reference)

    def test_selected_view_keeps_safety_checks_and_refuses_unretained_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / 'cpu-evidence.tar.gz'
            def write(names):
                with tarfile.open(path, 'w:gz') as tar:
                    for name in names:
                        member = tarfile.TarInfo(name); member.size = 4
                        tar.addfile(member, io.BytesIO(b'test'))
                return [{'event':'evidence_collected', 'role':'cpu',
                         'sha256':file_digest(path), 'bytes':path.stat().st_size}]
            events = write(['keep', 'skip'])
            archive = Archive(root, 'cpu', events, retain=lambda name:name == 'keep')
            try:
                self.assertEqual(archive.read('keep'), b'test')
                self.assertEqual(archive.read('keep'), b'test')
                with self.assertRaisesRegex(ValueError, 'not selected'): archive.read('skip')
            finally: archive.close()
            for names in [['keep', 'keep'], ['../outside']]:
                with self.assertRaisesRegex(ValueError, 'Unsafe or duplicate'):
                    Archive(root, 'cpu', write(names), retain=lambda name:True)


if __name__ == '__main__':
    unittest.main()
