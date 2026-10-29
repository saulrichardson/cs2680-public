"""Main-only finalization keeps incomplete-source and uncertain-stop boundaries."""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import test_candidates as candidate_fixture
from src.teamharness.candidates import capture

SPEC = importlib.util.spec_from_file_location('src.teamharness.course_worker_fixture',
    Path(__file__).resolve().parents[2] / 'lab/adapters/teamharness/course_worker.py')
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class MainFallback(unittest.TestCase):
    def setUp(self):
        self.fixture = candidate_fixture.CandidateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = self.fixture.root/'state'
        self.run = self.state/'run'
        self.agent = self.run/'agents/agents/main-agent'
        self.agent.mkdir(parents=True)
        (self.agent/'agent.json').write_text(json.dumps({'workspace':str(self.fixture.work)}))
        (self.agent/'runner.json').write_text(json.dumps({'status':'interrupted'}))
        (self.run/'workflow.json').write_text(json.dumps({'workflow':'main', 'state':'interrupted',
            'agents':{'main':'main-agent','helper':'other-agent'}}))
        shutil.copytree(self.fixture.root/'freeze', self.run/'source')
        self.config = {'state':str(self.state), 'task':{'workdir':str(self.fixture.source)},
                       'course_base':self.fixture.frozen['original_base']}

    def test_interrupted_main_captures_exact_current_bytes_against_course_base(self):
        selected, parts = worker.choose_candidate(self.config)
        expected = capture(self.fixture.work, self.fixture.root/'expected.patch', self.config['course_base'])
        self.assertEqual(selected['kind'], 'unreviewed_fallback')
        self.assertEqual(selected['source_role'], 'main')
        self.assertEqual(selected['source_status'], 'interrupted')
        self.assertFalse(selected['reviewed'])
        self.assertEqual(hashlib.sha256(b''.join(parts)).hexdigest(), expected['sha256'])
        self.assertEqual(selected['selected_base'], self.config['course_base'])
        self.assertEqual(selected['selected_sha256'], expected['sha256'])

    def test_uncertain_cleanup_does_not_block_stable_live_capture(self):
        before = (self.fixture.source/'value.py').read_bytes()
        observation = {'state':'unknown','group_stopped':False,'job_id':'unconfirmed'}
        with patch.object(worker, 'stop_jobs', return_value=[observation]):
            worker.finalize(self.config)
        self.assertEqual((self.fixture.source/'value.py').read_bytes(), before)
        self.assertEqual(json.loads((self.state/'owned-jobs-stop.json').read_text()), [observation])
        self.assertIn(b'value = 3', (self.state/'candidate.patch').read_bytes())
        self.assertEqual(json.loads((self.state/'selection.json').read_text())['kind'], 'unreviewed_fallback')

    def accept(self):
        workflow = json.loads((self.run/'workflow.json').read_text())
        workflow.update(state='completed', candidate=self.fixture.candidate, frozen=self.fixture.frozen)
        (self.run/'workflow.json').write_text(json.dumps(workflow))

    def test_immutable_candidate_replays_while_original_source_has_a_live_writer(self):
        self.accept()
        process = subprocess.Popen([sys.executable, '-u', '-c',
            'from pathlib import Path; import time; Path("value.py").write_text("value = 99\\n"); print("ready",flush=True); time.sleep(60)'],
            cwd=self.fixture.source, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            observation = {'state':'unknown','group_stopped':False}
            with patch.object(worker, 'stop_jobs', return_value=[observation]):
                worker.finalize(self.config)
            self.assertIsNone(process.poll())
            self.assertEqual((self.fixture.source/'value.py').read_text(), 'value = 99\n')
            self.assertEqual((self.state/'final-replay/value.py').read_text(), 'value = 3\n')
            final = json.loads((self.state/'finalization.json').read_text())
            self.assertEqual(final['selected_sha256'], self.fixture.candidate['sha256'])
            self.assertTrue(final['exact_course_capture'])
        finally:
            process.terminate(); process.communicate(timeout=5)

    def test_changed_selected_bytes_are_rejected_before_cleanup_or_replay(self):
        self.accept()
        Path(self.fixture.candidate['patch_path']).write_text('tampered')
        with patch.object(worker, 'stop_jobs') as stop:
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                worker.finalize(self.config)
            stop.assert_not_called()
        self.assertFalse((self.state/'candidate.json').exists())

    def test_verified_archive_supplies_baseline_when_mutable_copy_drifted(self):
        from src.teamharness.baseline import freeze
        self.fixture.frozen['archive'] = freeze(Path(self.fixture.frozen['workspace']), self.state/'baseline.tar')
        (self.run/'source/snapshot.json').write_text(json.dumps(self.fixture.frozen))
        self.accept()
        (Path(self.fixture.frozen['workspace'])/'value.py').write_text('baseline changed\n')
        with patch.object(worker, 'stop_jobs', return_value=[]):
            worker.finalize(self.config)
        self.assertEqual((self.state/'final-replay/baseline/value.py').read_text(), 'value = 3\n')
        self.assertEqual((Path(self.fixture.frozen['workspace'])/'value.py').read_text(), 'baseline changed\n')

    def test_cleanup_exception_does_not_replace_immutable_candidate(self):
        self.accept()
        with patch.object(worker, 'stop_jobs', side_effect=OSError('observer unavailable')):
            worker.finalize(self.config)
        self.assertIn('observer unavailable', (self.state/'owned-jobs-stop.json').read_text())
        self.assertEqual((self.state/'final-replay/value.py').read_text(), 'value = 3\n')
