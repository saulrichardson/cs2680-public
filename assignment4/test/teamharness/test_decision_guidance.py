"""Real HTTP requests, file ranges, running processes and exact graded checkpoints."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.teamharness.agents import AgentManager
from src.teamharness.candidates import Grader
from src.teamharness.main import Investigation
from src.teamharness.provider import HTTPProvider
from lab.adapters.teamharness.build import freeze_repair_guidance
from lab.adapters.teamharness.report_fleet import _session_metrics
from src.bridge import Workspace
from test_context_properties import latest_runtime
from test_session import answer
from test_main_flow import repository, call, finish, last_tool, http_script, audit_requests


ROOT = Path(__file__).resolve().parents[2]


class DecisionScript:
    def __init__(self):
        self.requests, self.errors = [], []
        self.step = 0
        self.jobs = []
        self.checkpoint = None
        self.checked_grade = None

    def reply(self, request):
        if 'response_format' in request:
            # Deliberately favorable model prose must not erase contrary grade facts.
            return answer(json.dumps({'continuation': 'Local checks look successful; the change seems correct. Continue.'}))
        step = self.step
        self.step += 1
        library = self.library
        if step == 0:
            return call('read_file', {'path': str(library/'SKILLS.md')})
        if step == 1:
            catalog = last_tool(request)['content']
            links = re.findall(r'\]\(([^)]+)\)', catalog)
            assert links and all(Path(link.split('#')[0]).is_file() for link in links)
            self.reference = next(link.split('#')[0] for link in links if Path(link.split('#')[0]).suffix=='.md')
            return call('read_file', {'path': self.reference})
        if step == 2:
            assert last_tool(request)['content']
            lines=Path(self.reference).read_text().splitlines(keepends=True)
            self.start_line=max(1,len(lines)//2)
            self.expected_lines=''.join(lines[self.start_line-1:self.start_line+2])
            return call('read_file', {'path': self.reference, 'start_line': self.start_line, 'line_count': 3})
        if step == 3:
            receipt = last_tool(request)
            assert receipt['first_displayed_line'] == self.start_line and receipt['range_kind'] == 'lines'
            assert receipt['content'] == self.expected_lines
            return call('exec_command', {'command': 'printf x >> launch-count; sleep 2; printf done', 'yield_time_ms': 0})
        if step in (4, 5):
            receipt = last_tool(request)
            assert receipt['state'] == 'running'
            self.jobs.append(receipt['id'])
            rendered = next(m['content'] for m in reversed(request['messages']) if m['role'] == 'tool')
            assert 'RUNNING:' in rendered and 'another process' in rendered
            if step == 4:
                return call('exec_command', {'command': 'printf x >> launch-count; sleep 2; printf done', 'yield_time_ms': 0})
            assert receipt['other_running_jobs_with_same_command'] == self.jobs[:1]
            assert self.jobs[0] != self.jobs[1]
            current = '\n'.join(m.get('content') or '' for m in request['messages'])
            assert self.jobs[0] in current and self.jobs[1] in current
            return call('job', {'id': self.jobs[0], 'yield_time_ms': 3000})
        if step == 6:
            return call('job', {'id': self.jobs[1], 'yield_time_ms': 3000})
        if step == 7:
            return call('apply_patch', {'patch': '*** Begin Patch\n*** Update File: answer.py\n@@\n-    return -1\n+    return n if n < 7 else -1\n*** End Patch\n'})
        if step == 8:
            return call('evaluate_candidate', {})
        if step == 9:
            receipt = last_tool(request)
            assert receipt['grade_facts']['tests_failed'] == 3
            advice=receipt['authored_guidance'][0]
            assert advice['text'] and advice['author']
            assert hashlib.sha256(Path(advice['source_path']).read_bytes()).hexdigest()==advice['source_sha256']
            rendered = next(m['content'] for m in reversed(request['messages']) if m['role'] == 'tool')
            assert advice['author'] in rendered and advice['source_sha256'] in rendered
            assert receipt['grade_facts']['tests_total']==10
            assert advice['text'] in rendered
            return call('compact', {})
        if step == 10:
            current = latest_runtime(request['messages'])['candidate_evidence']
            assert current['tests_failed'] == 3 and current['grader_reported_failure'] is True
            assert 'Local checks look successful' in str(request['messages'])
            return call('apply_patch', {'patch': '*** Begin Patch\n*** Update File: answer.py\n@@\n-    return n if n < 7 else -1\n+    return n\n*** End Patch\n'})
        if step == 11:
            current = latest_runtime(request['messages'])['candidate_evidence']
            assert current['source_write_observed_after_graded_capture']
            assert current['live_source_comparison'] == 'not_performed'
            return call('checkpoint', {'label': 'Correct but not yet graded'})
        if step == 12:
            receipt = last_tool(request)
            advice=receipt['authored_guidance'][0]
            assert advice['text'] and advice['author']
            assert hashlib.sha256(Path(advice['source_path']).read_bytes()).hexdigest()==advice['source_sha256']
            assert receipt['candidate']['sha256'] != receipt['previous_grade']['candidate_sha256']
            self.checkpoint = receipt['checkpoint_id']
            return call('evaluate_candidate', {'checkpoint_id': self.checkpoint})
        if step == 13:
            receipt = last_tool(request)
            assert receipt['grade_facts']['tests_failed'] == 0
            assert 'authored_guidance' not in receipt
            self.checked_grade = receipt['candidate']['sha256']
            return call('apply_patch', {'patch': '*** Begin Patch\n*** Update File: answer.py\n@@\n-    return n\n+    return n + 1\n*** End Patch\n'})
        if step == 14:
            current = latest_runtime(request['messages'])['candidate_evidence']
            assert current['candidate_sha256'] == self.checked_grade and current['source_write_observed_after_graded_capture']
            return finish('Return the tested checkpoint; later live edit is separate.', checkpoint_id=self.checkpoint)
        raise AssertionError('Unexpected request')


class DecisionGuidance(unittest.TestCase):
    def test_unknown_later_attempt_preserves_prior_count_bearing_grade(self):
        from src.teamharness.decision_context import grade_context
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with sqlite3.connect(root/'session.sqlite3') as db:
                db.execute('CREATE TABLE objects(id INTEGER PRIMARY KEY, kind TEXT, body TEXT, created REAL)')
                for index, receipt in enumerate([
                    {'candidate': {'sha256': 'first'}, 'grade_facts': {'candidate_sha256': 'first', 'tests_failed': 3, 'tests_total': 10}},
                    {'candidate': {'sha256': 'second'}, 'grade_facts': {'tests_failed': None, 'tests_total': None}, 'grading_state': 'received'}], 1):
                    db.execute('INSERT INTO objects VALUES (?,?,?,?)', (index, 'tool_result', json.dumps({'name': 'evaluate_candidate', 'receipt': receipt}), index))
            observed = grade_context(root)
            self.assertEqual(observed['candidate_sha256'], 'first')
            self.assertTrue(observed['grader_reported_failure'])
            self.assertEqual(observed['latest_grading_attempt']['candidate_sha256'], 'second')
            self.assertFalse(observed['latest_grading_attempt']['counts_available'])

    def test_byte_and_line_receipts_and_helpful_path_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = 'αβ\nsecond\nthird\n'.encode()
            (root/'chapter.md').write_bytes(raw)
            ws = Workspace(str(root), str(root/'state'), capture_patch=False)
            def read(**args):
                return ws.effect('read_file', dict(workspace=ws.facts(), **args))
            lines = read(path='chapter.md', start_line=2, line_count=1)
            self.assertEqual(lines['content'], 'second\n')
            self.assertEqual(lines['range_kind'], 'lines')
            self.assertEqual(lines['offset'], len('αβ\n'.encode()))
            page = read(path='chapter.md', offset=2, max_output_bytes=2)
            self.assertEqual(page['range_kind'], 'bytes')
            self.assertEqual(page['content'], 'β')
            self.assertEqual(page['offset_unit'], 'bytes')
            missing = read(path='chapter.md#section')
            self.assertIn('before #', missing['error'])
            directory = read(path=str(root))
            self.assertIn('directory', directory['error'])

    def test_actual_reads_feedback_source_changes_jobs_and_selected_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = repository(root)
            guides = root/'guides'
            shutil.copytree(ROOT/'src/teamharness/guides', guides)
            freeze_repair_guidance(ROOT/'research/repair-guidance/solver', guides/'portable-repair')
            script = DecisionScript()
            grader = Grader((sys.executable, '-B', '-c',
                'import json; from answer import answer; print(json.dumps({"tests_failed":sum(answer(n)!=n for n in range(10)),"tests_total":10}))'),
                'real local fixture', False)
            with http_script(script) as env, patch.dict(os.environ, env), patch('src.teamharness.prompts.GUIDES', guides):
                manager = AgentManager(root/'run/agents', HTTPProvider)
                script.library=manager.guide_root/'portable-repair'
                workflow = Investigation(manager, root/'run', completion_gate=False)
                try:
                    result = workflow.run('Implement the integer identity function.', repo, grader)
                    self.assertEqual(result['state'], 'completed', (script.errors, result))
                    self.assertEqual(script.errors, [])
                    self.assertEqual((repo/'launch-count').read_text(), 'xx')
                    self.assertEqual((repo/'answer.py').read_text(), 'def answer(n):\n    return n + 1\n')
                    self.assertEqual(result['candidate']['sha256'], script.checked_grade)
                    descriptor = manager.descriptor(result['agents']['main'])
                    requests = audit_requests(Path(descriptor['root'])/'session.sqlite3')
                    self.assertEqual(requests, script.requests)
                    metrics = _session_metrics(Path(descriptor['root'])/'session.sqlite3')
                    self.assertEqual(metrics['shell_results'], 2)
                    self.assertEqual(metrics['transport_errors'], 0)
                    self.assertEqual(metrics['compactions'], 1)
                    manifest = json.loads((manager.guide_root/'manifest.json').read_text())
                    self.assertEqual(manifest['version'], 2)
                    name = 'portable-repair/SKILLS.md'
                    self.assertNotEqual(manifest['files'][name], manifest['source_files'][name])
                finally:
                    manager.shutdown()


if __name__ == '__main__':
    unittest.main()
