"""Observed processes, exact evidence, Unicode retrieval and shared archive bytes."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from hypothesis import given, settings, strategies as st

from src.bridge import Workspace
from src.store import Store, content_pieces, encoded
from src.teamharness.context import append_updates
from src.teamharness.decision_context import candidate_comparison, observed_jobs, job_observations, verification_context, uncertain_effects
from src.teamharness.session import Session
from src.teamharness.tools import ToolRuntime
from src.workspace import snapshot_bytes, utf8_prefix
from test_session import Script, answer


TEXT = st.text(alphabet=st.characters(blacklist_categories=['Cs']), min_size=1, max_size=80)


class EvidenceLaws(unittest.TestCase):
    @settings(max_examples=35, deadline=None)
    @given(text=TEXT, page_size=st.integers(1, 31), changed=st.booleans())
    def test_exact_pages_and_candidate_relations(self, text, page_size, changed):
        raw = json.dumps({'original': text * 20}, ensure_ascii=False).encode()
        chunks = []; offset = 0
        while offset < len(raw):
            part = utf8_prefix(raw[offset:offset + page_size + 3], page_size)
            self.assertTrue(part)
            chunks.append(part.decode())
            offset += len(part)
        self.assertEqual(''.join(chunks).encode(), raw)
        a = {'base': 'base', 'sha256': hashlib.sha256(raw).hexdigest()}
        b = {**a, 'base': 'other'} if changed else dict(a)
        self.assertEqual(candidate_comparison(a, b), 'different' if changed else 'same')
        self.assertEqual(candidate_comparison(a, None), 'not_compared')

    @settings(max_examples=25, deadline=None)
    @given(text=TEXT, repeats=st.integers(2, 8))
    def test_shared_content_reconstructs_original_objects_without_runtime_helpers(self, text, repeats):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'archive.sqlite3'
            payload = (text + ' \"\\\n') * 80
            expected = []
            with Store(path) as store:
                for n in range(repeats):
                    body = {'epoch': n, 'messages': [{'role': 'tool', 'content': payload}] * (n + 1)}
                    ref = store.put('session_state', 'session', body)
                    expected.append((ref, body))
                    self.assertEqual(''.join(content_pieces(encoded(body))), encoded(body))
                raw = payload.encode()
                for n in range(repeats):
                    store.put_blob('raw_request', 'session', raw, {'attempt': n})
                self.assertEqual(store.db.execute('SELECT count(*) FROM byte_contents').fetchone()[0], 1)
                # Independent plain SQLite, with no Store import or SQL extension.
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as reader:
                    rows = reader.execute("SELECT id,body,sha256 FROM objects WHERE kind='session_state' ORDER BY id").fetchall()
                    for (ident, raw_body, digest), (ref, body) in zip(rows, expected):
                        self.assertEqual(ref, 'obj:' + str(ident))
                        self.assertEqual(raw_body, encoded(body))
                        self.assertEqual(json.loads(raw_body), body)
                        self.assertEqual(hashlib.sha256(raw_body.encode()).hexdigest(), digest)
                full = store.db.execute('SELECT sum(length(cast(body AS blob))) FROM objects').fetchone()[0]
                shared = store.db.execute('SELECT sum(length(cast(text AS blob))) FROM content_pieces').fetchone()[0]
                self.assertLess(shared, full)
                original = snapshot_bytes({'state': tmp}, raw, purpose='first read')
                again = snapshot_bytes({'state': tmp}, raw, purpose='second read')
                self.assertEqual(original['path'], again['path'])
                self.assertNotEqual(original['purpose'], again['purpose'])
                self.assertEqual(Path(original['path']).read_bytes(), raw)


class EvidenceBoundaries(unittest.TestCase):
    def test_observed_exit_is_delivered_once_and_missing_supervisor_stays_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); workspace = Workspace(tmp, str(root / 'state'), capture_patch=False)
            ws = workspace.facts()
            launch = workspace.effect('shell', {'workspace': ws,
                'command': 'read line; printf completed; exit 7', 'yield_time_ms': 0})
            self.assertEqual(launch['state'], 'running')
            state = {'epoch': 0, 'config': {'context_policy': 'append_only_v1'}, 'messages': []}
            archived = []
            def record(kind, body):
                archived.append((kind, copy.deepcopy(body))); return 'obj:' + str(len(archived))
            def observe():
                state['runtime_facts'] = {'observed_at': 1, 'job_observations': job_observations(ws, observed_jobs(ws))}
                append_updates(state, root / 'archive.sqlite3', record)
            observe()
            workspace.effect('job', {'workspace': ws, 'id': launch['id'], 'action': 'input',
                                    'chars': 'continue\n', 'yield_time_ms': 2000})
            observe(); count = len(state['messages']); observe()
            self.assertEqual(len(state['messages']), count)
            changes = [b['jobs'] for kind, b in archived if kind == 'job_observation']
            self.assertEqual([rows[0]['state'] for rows in changes], ['running', 'exited'])
            self.assertEqual(changes[-1][0]['exit_code'], 7)
            self.assertIn('output_path', changes[-1][0])
            self.assertIn('read line', changes[-1][0]['command_preview'])
            # Simulate the lost durable result after actual process completion.
            (root / 'state/jobs' / launch['id'] / 'result.json').unlink()
            (root / 'state/jobs' / launch['id'] / 'started.json').write_text(json.dumps({'pid': 99999999}))
            observe()
            self.assertEqual(archived[-1][1]['jobs'][0]['state'], 'unknown')
            self.assertNotIn('exit_code', archived[-1][1]['jobs'][0])

    def test_plan_revision_and_capture_identity_determine_applicability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); directory = root / 'test-plan-runs' / 'run'; directory.mkdir(parents=True)
            a = {'base': 'base', 'sha256': 'A'}; b = {**a, 'sha256': 'B'}
            (directory / 'receipt.json').write_text(json.dumps({'plan_id': 'P', 'candidate': a,
                'target': 'candidate', 'started_at': 1, 'state': 'finished', 'passed': True, 'checks': []}))
            for candidate, expected in [(a, 'same'), (b, 'different'), (a, 'same')]:
                (root / 'latest-checkpoint.json').write_text(json.dumps({'candidate': candidate}))
                facts = verification_context(root, {'id': 'P'})
                self.assertEqual(facts['candidate']['comparison_to_latest_capture'], expected)
                self.assertEqual(facts['live_source_comparison'], 'not_compared')
            self.assertEqual(verification_context(root, {'id': 'new plan'})['candidate']['state'], 'unavailable')

    def test_uncertain_effect_and_check_evidence_survive_favorable_compaction(self):
        class Executor:
            def runtime_facts(self):
                return {'repair_plan': {'verification': {'candidate': {'state': 'finished', 'passed': False,
                    'candidate': {'base': 'base', 'sha256': 'A'}, 'receipt_path': '/archive/check'}}},
                    'uncertain_effects': uncertain_effects(root / 'session')}
            def __call__(self, *_): raise AssertionError('Compaction must not execute effects')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = Script([answer(json.dumps({'continuation': 'My interpretation: everything passed.'}))])
            session = Session(root / 'session', 'investigator', 'system', 'Literal human requirement',
                              provider, [], Executor(), root, public_task='Literal human requirement')
            with Store(session.database) as store:
                store.put('tool_result', 'session', {'name': 'apply_patch', 'effect_id': 'entered-action',
                    'receipt': {'state': 'unknown', 'executed': None}})
            self.assertEqual(session.compact(retain_recent_exchanges=0)['status'], 'compacted')
            messages = session._load()['messages']
            self.assertEqual(messages[1]['content'], 'Literal human requirement')
            factual = '\n'.join(m.get('content') or '' for m in messages)
            self.assertIn('passed: false', factual)
            self.assertIn('/archive/check', factual)
            self.assertIn('entered-action', factual)
            self.assertIn('replay safe: false', factual)

    def test_archive_paging_delivers_identity_and_cursor_before_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root / 'session.sqlite3'
            text = 'α🌿 original evidence\n' * 3000
            with Store(path) as store:
                ref = store.put('evidence', 'session', {'text': text})
            runtime = object.__new__(ToolRuntime); runtime.root = root
            offset = 0; parts = []
            while True:
                result = runtime.execute('read_archive', {'path': str(path), 'ref': ref,
                    'json_pointer': '/body/text', 'offset': offset, 'max_output_bytes': 65536}, 'read')
                from src.teamharness.presentation import render_result
                rendered = render_result(root, path, 'obj:' + str(offset + 1), 'read_archive', result)
                self.assertLess(rendered.index('next offset:'), rendered.index('Archived content:'))
                self.assertNotIn('Read more with read_archive', rendered)
                parts.append(result['content']); self.assertGreater(result['next_offset'], offset)
                offset = result['next_offset']
                if result['complete']: break
            self.assertEqual(json.loads(''.join(parts)), text)

    def test_legacy_archive_is_not_migrated_when_reopened(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE objects(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,scope TEXT NOT NULL,body TEXT NOT NULL,sha256 TEXT NOT NULL,created REAL NOT NULL,alias TEXT UNIQUE)')
            with Store(path) as store:
                ref = store.put('facts', 'session', {'original': True})
            with Store(path, readonly=True) as store:
                self.assertEqual(store.get(ref)['body'], {'original': True})
                self.assertEqual(store.db.execute("SELECT type FROM sqlite_master WHERE name='objects'").fetchone()[0], 'table')


if __name__ == '__main__': unittest.main()
