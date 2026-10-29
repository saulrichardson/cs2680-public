"""Reporter checks with archived journals, original grades and exact patch bytes."""
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest

from lab.adapters.teamharness.report_fleet import (NATIVE, SUBMITTED, GRADED, RESULTS,
    digest, read_attempt, read_environment, session_metrics, verify_selection, shell_job_ids, deadline_evidence)


class ReporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.expected = {'instance_id': 'task-example', 'base_commit': 'original-base'}

    def archive(self, *, passed=False, cutoff=False, patch=b'candidate patch\n', graded=None,
                include_candidate=True, empty_grade=False, finalization=True, empty_submission=False, interactive=False):
        selection = {'kind': 'unreviewed_fallback' if patch else 'empty', 'reviewed': False,
                     'selected_base': 'original-base', 'selected_sha256': digest(patch),
                     'source_role': 'implementer' if patch else None, 'source_agent_id': 'a' if patch else None}
        files = {'tasks.json': {'0': self.expected}, RESULTS: {'0': passed},
                 NATIVE + 'stop.json': {'cutoff': cutoff, 'exit_code': -15 if cutoff else 0},
                 NATIVE + 'grade.json': {'ok': True},
                 NATIVE + 'completed.json': {},
                 NATIVE + 'run/workflow.json': {'state': 'completed', 'agents': {'implementer': 'a'}}}
        if interactive:
            files[NATIVE + 'launch.json'] = {'grade_feedback': True}
            files[NATIVE + 'run/workflow.json']['interactive_grades'] = {'0': {'state': 'received', 'result': {'passed': False}}}
        if finalization:
            files[NATIVE + 'selection.json'] = selection
            files[NATIVE + 'finalization.json'] = {'course_base': 'original-base',
                'candidate_sha256': digest(patch), 'replayed_sha256': digest(patch),
                'selected_sha256': digest(patch), 'selection_kind': selection['kind'],
                'reviewed': False, 'exact_course_capture': True,
                'replay_components': [digest(patch)] if patch else []}
        if include_candidate:
            files[NATIVE + 'candidate.json'] = {'base': 'original-base', 'sha256': digest(patch), 'bytes': len(patch)}
            files[NATIVE + 'candidate.patch'] = patch
            if not empty_grade:
                files[SUBMITTED] = patch
                files[GRADED] = patch if graded is None else graded
            elif empty_submission:
                files[SUBMITTED] = b''
        path = self.root / 'lane.tar'
        with tarfile.open(path, 'w') as archive:
            for name, value in files.items():
                data = value if isinstance(value, bytes) else json.dumps(value).encode()
                member = tarfile.TarInfo('task-example/' + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return path

    def test_interactive_feedback_is_reported_separately(self):
        result = read_attempt(self.archive(passed=True, interactive=True), self.expected)
        self.assertEqual('interactive_counts', result['grading_mode'])
        self.assertFalse(result['interactive_grades']['0']['result']['passed'])
        self.assertTrue(result['solved'])

    def test_main_return_selection_reads_runner_and_checkpoint_from_archive(self):
        ident = 'a' * 32
        checkpoint = f'run/agents/agents/{ident}/checkpoints/returned.patch'
        raw = b'exact main candidate\n'
        selected = {'base': 'original-base', 'sha256': digest(raw), 'bytes': len(raw),
                    'patch_path': '/work/task/' + checkpoint}
        for kind in ('main_candidate', 'main_returned_candidate'):
            with self.subTest(kind=kind):
                original = self.archive(passed=True, patch=raw)
                with tarfile.open(original) as archive:
                    files = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
                prefix = 'task-example/' + NATIVE
                workflow = {'workflow': 'main', 'state': 'completed', 'agents': {'main': ident},
                            'frozen': {'revision': 'original-base', 'source_patch_sha256': digest(b'')},
                            'candidate': selected}
                selection = {'kind': kind, 'source_role': 'main', 'source_agent_id': ident,
                             'selected_sha256': digest(raw), 'selected_base': 'original-base', 'reviewed': False}
                final = json.loads(files[prefix + 'finalization.json'])
                final.update(selection_kind=kind, initial_patch_sha256=digest(b''),
                             replay_components=[digest(b''), digest(raw)])
                for name, value in {'run/workflow.json': workflow, 'selection.json': selection,
                                    'finalization.json': final,
                                    f'run/agents/agents/{ident}/runner.json': {'status': 'completed', 'patch': selected}}.items():
                    files[prefix + name] = json.dumps(value).encode()
                files[prefix + checkpoint] = raw
                files[prefix + 'run/source/source-before.patch'] = b''
                with tarfile.open(original, 'w') as archive:
                    for name, body in files.items():
                        member = tarfile.TarInfo(name); member.size = len(body)
                        archive.addfile(member, io.BytesIO(body))
                result = read_attempt(original, self.expected)
                self.assertTrue(result['solved'])
                self.assertTrue(result['selection_verified'])

    def test_qualified_solver_image_does_not_relax_original_task_or_grade_identity(self):
        original = dict(self.expected, docker_image='original-grader')
        image = 'sha256:' + 'a' * 64
        self.expected = dict(original, sandbox_image=image)
        archive = self.archive(passed=True)
        with self.assertRaisesRegex(ValueError, 'frozen manifest'):
            read_attempt(archive, original)
        result = read_attempt(archive, original, {'image_id': image})
        self.assertTrue(result['solved'])
        self.assertEqual(result['solver_environment']['image_id'], image)
        with self.assertRaisesRegex(ValueError, 'frozen manifest'):
            read_attempt(archive, dict(original, base_commit='foreign-base'), {'image_id': image})

    def test_failed_environment_checks_cannot_qualify_a_solver_image(self):
        expected = dict(self.expected, docker_image='stock', repo='flipt-io/flipt')
        path = self.root / 'environment.tar'
        record = {'passed': True, 'image_id': 'sha256:' + 'a' * 64,
                  'original_grading_image': 'stock',
                  'phases': [{'name': 'git-and-source', 'exit_code': 0},
                             {'name': 'offline-dependencies-and-compile', 'exit_code': 1}]}
        def write():
            data = json.dumps({'passed': True, 'images': {'task-example': record}}).encode()
            with tarfile.open(path, 'w') as archive:
                info = tarfile.TarInfo('environment-checks/RESULT.json'); info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        write()
        with self.assertRaisesRegex(ValueError, 'successful checks'):
            read_environment(path, expected)
        record['phases'][-1]['exit_code'] = 0
        write()
        self.assertEqual(read_environment(path, expected)['image_id'], record['image_id'])
        with self.assertRaisesRegex(ValueError, 'successful checks'):
            read_environment(path, dict(expected, docker_image='foreign-grader'))

    def test_reference_free_network_profile_requires_network_proof(self):
        expected=dict(self.expected,docker_image='stock',repo='ansible/ansible')
        path=self.root/'network-environment.tar'
        record={'passed':True,'image_id':'sha256:'+'a'*64,'original_grading_image':'stock',
                'phases':[{'name':'git-and-source','exit_code':0}]}
        def write():
            data=json.dumps({'passed':True,'reference_solutions_used':False,'dependency_mode':'stock',
                'grading_dependencies':'network','images':{'task-example':record}}).encode()
            with tarfile.open(path,'w') as archive:
                info=tarfile.TarInfo('environment-checks/RESULT.json');info.size=len(data)
                archive.addfile(info,io.BytesIO(data))
        write()
        with self.assertRaisesRegex(ValueError,'successful checks'):read_environment(path,expected)
        record['phases'].append({'name':'grader-dependency-network','exit_code':0});write()
        self.assertEqual(read_environment(path,expected)['image_id'],record['image_id'])
        # A paired profile cannot borrow the grader's proof for the solver.
        with tarfile.open(path) as archive:
            data=json.loads(archive.extractfile('environment-checks/RESULT.json').read())
        data['solver_dependencies']='network'
        def paired():
            raw=json.dumps(data).encode()
            with tarfile.open(path,'w') as archive:
                info=tarfile.TarInfo('environment-checks/RESULT.json');info.size=len(raw)
                archive.addfile(info,io.BytesIO(raw))
        paired()
        with self.assertRaisesRegex(ValueError,'successful checks'):read_environment(path,expected)
        data['images']['task-example']['phases'].append({'name':'solver-dependency-network','exit_code':0})
        paired()
        self.assertEqual(read_environment(path,expected)['image_id'],record['image_id'])

    def test_cutoff_grade_is_preserved_without_becoming_normal_completion(self):
        result = read_attempt(self.archive(passed=True, cutoff=True), self.expected)
        self.assertTrue(result['solved'])
        self.assertTrue(result['graded_patch_verified'])
        self.assertEqual(result['execution_state'], 'cutoff')

    def test_foreign_graded_bytes_rejected_even_when_course_says_pass(self):
        with self.assertRaisesRegex(ValueError, 'bytes differ'):
            read_attempt(self.archive(passed=True, graded=b'other candidate'), self.expected)

    def test_grade_without_candidate_is_unknown_not_failure(self):
        result = read_attempt(self.archive(include_candidate=False), self.expected)
        self.assertIsNone(result['solved'])
        self.assertEqual(result['grade_state'], 'unverified_patch_identity')

    def test_native_selection_missing_keeps_original_grade_but_does_not_verify_it(self):
        result = read_attempt(self.archive(passed=True, finalization=False), self.expected)
        self.assertTrue(result['original_passed'])
        self.assertTrue(result['graded_patch_verified'])
        self.assertIsNone(result['solved'])
        self.assertEqual(result['grade_state'], 'unverified_selection')

    def test_empty_dispatcher_failure_requires_verified_empty_capture(self):
        result = read_attempt(self.archive(patch=b'', empty_grade=True), self.expected)
        self.assertFalse(result['solved'])
        self.assertTrue(result['empty_candidate_grade_verified'])
        result = read_attempt(self.archive(passed=True, patch=b'', empty_grade=True), self.expected)
        self.assertIsNone(result['solved'])

    def test_empty_submitted_artifact_verifies_only_original_failure(self):
        result = read_attempt(self.archive(patch=b'', empty_grade=True, empty_submission=True), self.expected)
        self.assertFalse(result['solved'])
        self.assertEqual(result['grade_state'], 'failed')
        self.assertTrue(result['empty_candidate_grade_verified'])
        self.assertFalse(result['graded_patch_verified'])
        result = read_attempt(self.archive(passed=True, patch=b'', empty_grade=True, empty_submission=True), self.expected)
        self.assertIsNone(result['solved'])
        self.assertEqual(result['grade_state'], 'unverified_patch_identity')

    def test_metrics_keep_precompaction_tool_results_without_checkpoint_duplicates(self):
        path = self.root / 'session.sqlite3'
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE objects(id INTEGER PRIMARY KEY, kind TEXT, body TEXT, sha256 TEXT, created REAL)')
        def record(kind, body):
            raw = json.dumps(body)
            db.execute('INSERT INTO objects(kind,body,sha256,created) VALUES (?,?,?,?)', (kind, raw, digest(raw.encode()), 1.0))
        messages = [{'role': 'assistant', 'tool_calls': [{'id': 'call', 'function': {'name': 'exec_command'}}]},
                    {'role': 'tool', 'tool_call_id': 'call', 'content': json.dumps({'id': 'job', 'state': 'exited', 'exit_code': 1})}]
        state = {'epoch': 0, 'messages': messages, 'config': {'role': 'implementer'}, 'status': 'running', 'session_id': 's'}
        record('model_request', {'purpose': 'agent'})
        record('model_response', {'purpose': 'agent', 'response': {'usage': {'prompt_tokens': 7, 'completion_tokens': 3}}})
        record('session_state', state)
        record('session_state', state)
        record('compaction_result', {'status': 'compacted'})
        record('session_state', dict(state, epoch=1, messages=[], status='completed'))
        db.commit()
        db.close()
        result = session_metrics(path)
        self.assertEqual((result['requests'], result['responses'], result['tool_results'], result['shell_results'], result['compactions']), (1, 1, 1, 1, 1))
        self.assertEqual(result['missing_cost'], 1)
        self.assertEqual(result['input_tokens'], 7)

    def test_modified_journal_content_is_rejected(self):
        path = self.root / 'session.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE objects(id INTEGER PRIMARY KEY, kind TEXT, body TEXT, sha256 TEXT, created REAL)')
            db.execute('INSERT INTO objects VALUES (1,?,?,?,1.0)', ('model_request', '{}', 'wrong'))
        with self.assertRaisesRegex(ValueError, 'invalid content hash'):
            session_metrics(path)

    def test_parallel_receipts_deduplicate_jobs_across_inspections_and_epochs(self):
        path = self.root / 'session.sqlite3'
        members = [
            {'name': 'exec_command', 'job_id': 'predicted-only', 'state': 'pending'},
            {'name': 'exec_command', 'result': {'id': 'a', 'state': 'running'},
             'job_observation': {'id': 'a', 'state': 'exited', 'exit_code': 0, 'elapsed_seconds': 2.5}},
            {'name': 'exec_command', 'result': {'id': 'b', 'state': 'exited', 'exit_code': 1, 'elapsed_seconds': 4}},
            {'name': 'exec_command', 'result': {'id': 'unconfirmed', 'state': 'unknown'}},
            {'name': 'read_file', 'result': {'id': 'not-shell', 'state': 'exited'}},
        ]
        def exchange(ident, name, receipt):
            return [{'role': 'assistant', 'tool_calls': [{'id': ident, 'function': {'name': name}}]},
                    {'role': 'tool', 'tool_call_id': ident, 'content': json.dumps(receipt)}]
        first = exchange('direct', 'exec_command', {'id': 'a', 'state': 'running'})
        first += exchange('group', 'parallel', {'members': members})
        second = exchange('inspect', 'inspect_parallel', {'members': members})
        second += exchange('cancel', 'cancel_parallel', {'members': members + [
            {'name': 'exec_command', 'job_observation': {'id': 'c', 'state': 'exited', 'exit_code': -15}}]})
        config = {'role': 'implementer', 'metadata': {'task_deadline_unix': 1234.5}}
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE objects(id INTEGER PRIMARY KEY, kind TEXT, body TEXT, sha256 TEXT, created REAL)')
            for epoch, messages in [(0, first), (0, first), (1, second)]:
                body = json.dumps({'epoch': epoch, 'messages': messages, 'config': config,
                    'status': 'running', 'session_id': 'actual-session'})
                db.execute('INSERT INTO objects(kind,body,sha256,created) VALUES(?,?,?,1.0)',
                           ('session_state', body, digest(body.encode())))
        before = path.read_bytes()
        metrics = session_metrics(path)
        self.assertEqual(metrics['shell_job_ids'], ['a', 'b', 'c'])
        self.assertEqual((metrics['shell_results'], metrics['tool_results']), (3, 4))
        self.assertEqual(metrics['task_deadline_unix'], 1234.5)
        self.assertEqual(metrics['shell_timing']['completed_process_seconds'], 6.5)
        self.assertEqual(metrics['shell_timing']['missing_elapsed'], 1)
        self.assertEqual(path.read_bytes(), before)
        archive_path = self.archive()
        with tarfile.open(archive_path, 'a') as archive:
            for ident in ('a'*32, 'b'*32):
                archive.add(path, arcname='task-example/' + NATIVE + 'run/agents/agents/' + ident + '/session.sqlite3')
            launch = json.dumps({'deadline': 1234.5}).encode()
            info = tarfile.TarInfo('task-example/' + NATIVE + 'launch.json')
            info.size = len(launch)
            archive.addfile(info, io.BytesIO(launch))
        attempt = read_attempt(archive_path, self.expected)
        self.assertEqual(attempt['shell_results'], 3)
        self.assertEqual(attempt['deadline_evidence']['state'], 'matching')
        self.assertEqual(attempt['deadline_evidence']['observed_sessions'], 2)

    def test_group_call_without_returned_shell_receipt_does_not_count(self):
        for receipt in ({'calls': [{'name': 'exec_command'}]}, {'members': None},
                        {'members': [{'name': 'exec_command', 'job_id': 'predicted', 'state': 'running'}]}, None):
            self.assertEqual(shell_job_ids('parallel', receipt), set())
        self.assertEqual(shell_job_ids('exec_command', {'id': 'done', 'state': 'exited', 'exit_code': 2}), {'done'})

    def test_shared_deadline_requires_every_session_and_known_launch(self):
        sessions = [{'session_id': 'parent', 'task_deadline_unix': 100},
                    {'session_id': 'child', 'task_deadline_unix': 100}]
        self.assertTrue(deadline_evidence(sessions, 100)['verified'])
        sessions[1]['task_deadline_unix'] = 101
        mismatch = deadline_evidence(sessions, 100)
        self.assertEqual(mismatch['mismatched'], ['child'])
        self.assertEqual(mismatch['state'], 'mismatched')
        sessions[1].pop('task_deadline_unix')
        missing = deadline_evidence(sessions, 100)
        self.assertEqual(missing['state'], 'unavailable')
        self.assertEqual(missing['missing'], ['child'])
        self.assertFalse(missing['verified'])
        self.assertEqual(deadline_evidence(sessions[:1], None)['state'], 'unavailable')
        self.assertFalse(deadline_evidence([], 100)['verified'])

    def test_reviewed_to_course_capture_cannot_substitute_another_source(self):
        patch = b'reviewed patch'
        candidate = {'base': 'course', 'sha256': digest(patch), 'bytes': len(patch)}
        selection = {'kind': 'reviewed_candidate', 'source_agent_id': 'integrator-a', 'reviewed': True,
                     'selected_sha256': digest(patch), 'selected_base': 'frozen'}
        final = {'course_base': 'course', 'candidate_sha256': digest(patch), 'replayed_sha256': digest(patch),
                 'selected_sha256': digest(patch), 'selection_kind': 'reviewed_candidate', 'reviewed': True,
                 'exact_course_capture': True, 'initial_patch_sha256': digest(b''),
                 'replay_components': [digest(b''), digest(patch)]}
        workflow = {'state': 'completed', 'agents': {'integrator': 'integrator-a'},
                    'candidate': {'sha256': digest(patch), 'base': 'frozen'}, 'reviewed_sha256': digest(patch),
                    'frozen': {'revision': 'frozen', 'source_patch_sha256': digest(b'')}}
        files = {'selection.json': json.dumps(selection).encode(), 'finalization.json': json.dumps(final).encode(),
                 'run/candidate.patch': patch, 'run/source/source-before.patch': b''}
        self.assertTrue(verify_selection(files, workflow, candidate))
        files['run/candidate.patch'] = b'changed after review'
        with self.assertRaisesRegex(ValueError, 'Reviewed selection'):
            verify_selection(files, workflow, candidate)

    def test_sigkill_hot_journal_recovers_only_a_copy(self):
        path = self.root / 'session.sqlite3'
        state = {'epoch': 0, 'messages': [], 'config': {'role': 'implementer'},
                 'status': 'running', 'session_id': 'original-committed'}
        raw = json.dumps(state)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE objects(id INTEGER PRIMARY KEY, kind TEXT, body TEXT, sha256 TEXT, created REAL)')
            db.execute('CREATE TABLE spill(data BLOB)')
            db.execute('INSERT INTO objects VALUES(1,?,?,?,1.0)', ('session_state', raw, digest(raw.encode())))
            db.executemany('INSERT INTO spill VALUES(?)', [(b'committed' * 512,)] * 200)
        marker = self.root / 'transaction-spilled'
        script = '''import pathlib,sqlite3,sys,time
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA cache_size=10')
db.execute('PRAGMA cache_spill=10')
db.execute('PRAGMA synchronous=FULL')
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE objects SET body='uncommitted corruption'")
for i in range(1,201):db.execute('UPDATE spill SET data=? WHERE rowid=?',(b'uncommitted'*512,i))
pathlib.Path(sys.argv[2]).write_text('spilled')
time.sleep(120)
'''
        child = subprocess.Popen([sys.executable, '-c', script, str(path), str(marker)])
        try:
            limit = time.monotonic() + 10
            while not marker.exists() and child.poll() is None and time.monotonic() < limit:
                time.sleep(.01)
            self.assertTrue(marker.exists(), 'Child must dirty and spill a real open transaction')
            child.kill()
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        journal = Path(str(path) + '-journal')
        self.assertTrue(journal.exists())
        before = {p: p.read_bytes() for p in (path, journal)}
        # The header marks a real hot journal, not an empty preallocated file.
        self.assertEqual(before[journal][:8], bytes.fromhex('d9d505f920a163d7'))
        result = session_metrics(path)
        self.assertEqual(result['session_id'], 'original-committed')
        self.assertEqual(result['requests'], 0)
        self.assertIn('session.sqlite3-journal', result['sqlite_evidence']['raw_components_sha256'])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        archive_path = self.archive()
        with tarfile.open(archive_path, 'a') as archive:
            for source in (path, journal):
                archive.add(source, arcname='task-example/' + NATIVE + 'run/agents/agents/' + 'a'*32 + '/' + source.name)
        original_archive = digest(archive_path.read_bytes())
        attempt = read_attempt(archive_path, self.expected)
        self.assertEqual(attempt['sessions'][0]['session_id'], 'original-committed')
        self.assertEqual(digest(archive_path.read_bytes()), original_archive)


if __name__ == '__main__':
    unittest.main()
