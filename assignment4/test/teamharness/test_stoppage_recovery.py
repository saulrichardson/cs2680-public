"""Focused witnesses for late returns, interrupted compaction and final capture.

Local scripted inference/dispatcher fixtures establish mechanics, not solve quality.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.api import InferenceStopped
from src.store import Store
from src.teamharness.agents import AgentManager
from src.teamharness.main import Investigation
from src.teamharness.provider import HTTPProvider
from src.teamharness.session import Session
from src.teamharness.tools import ToolRuntime
from receipt_fixture import original_receipt
from test_capacity import CapacityTests, CapacityProvider
from test_main_flow import repository, finish
from test_main_fallback import MainFallback, worker
from test_session import Script, answer, tools


FAILED_GENERATION = {'choices': [{'finish_reason': 'error',
    'message': {'role': 'assistant', 'content': None}}]}


@contextmanager
def http_model(reply):
    wire = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            wire.append((self.headers['X-A4-Request-ID'], request))
            response, headers = reply(request)
            raw = json.dumps(response).encode()
            self.send_response(400 if response.get('error') else 200)
            self.send_header('Content-Length', str(len(raw)))
            for key, value in headers.items(): self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.dict(os.environ, {'A4_MODEL': 'same-qwen-fixture',
                'A4_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                'A4_API_KEY': 'local-fixture', 'A4_API_PROTOCOL': 'chat_completions'}):
            yield wire
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


class StoppageRecovery(unittest.TestCase):
    def test_sequential_recovery_reads_saved_result_without_replaying_action(self):
        for saved_result in (True, False):
            with self.subTest(saved_result=saved_result), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); repo = root / 'source'; repo.mkdir()
                (repo / 'value.txt').write_text('before')
                arguments = {'path': 'value.txt', 'replacements': [{'old': 'before', 'new': 'after'}]}
                model = Script([tools(('edit_file', arguments)), answer('Observed the original outcome.')])
                manager = AgentManager(root / 'agents', lambda _: model)
                try:
                    ident = manager.create('investigator', 'Edit value.txt.', repo)
                    session = manager._session(ident, model)
                    method = '__call__' if saved_result else 'execute'
                    original = getattr(ToolRuntime, method)
                    def interrupt(runtime, *args):
                        original(runtime, *args)
                        raise SystemExit('Interrupted after the real file edit')
                    with patch.object(ToolRuntime, method, interrupt):
                        with self.assertRaises(SystemExit):
                            session.run()
                    pending = session._load()['pending']['calls'][0]
                    effect_root = Path(manager.descriptor(ident)['root']) / 'effects'
                    saved = effect_root / (hashlib.sha256(pending['effect_id'].encode()).hexdigest() + '.json')
                    expected = json.loads(saved.read_text()) if saved_result else None
                    self.assertEqual((repo / 'value.txt').read_text(), 'after')
                    self.assertEqual(saved.exists(), saved_result)
                    with patch.object(ToolRuntime, '__call__', side_effect=AssertionError('Recovery dispatched an action')):
                        resumed = manager._session(ident, model)
                        self.assertEqual(resumed.run()['status'], 'completed')
                    observed = original_receipt(next(m['content'] for m in model.requests[-1]['messages'] if m['role'] == 'tool'))
                    if saved_result:
                        self.assertEqual(observed, expected)
                        self.assertTrue(observed['applied'])
                        self.assertEqual(json.loads(saved.read_text()), expected)
                    else:
                        self.assertEqual(observed['execution_state'], 'unknown')
                        self.assertFalse(saved.exists())
                    self.assertEqual((repo / 'value.txt').read_text(), 'after')
                    mismatch = resumed.executor.recover_effect('edit_file', {**arguments, 'path': 'other.txt'}, pending['effect_id'])
                    self.assertFalse(mismatch['executed'])
                    self.assertFalse((repo / 'other.txt').exists())
                finally:
                    manager.shutdown()

    def capacity_fixture(self):
        fixture = CapacityTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_late_return_after_two_handoffs_keeps_parent_and_reaches_current_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = repository(root); requests = []
            def model(request):
                requests.append(request)
                return finish('Observed the late helper evidence.')
            manager = AgentManager(root / 'run/agents', lambda _: model, deadline=time.time() + 60)
            workflow = Investigation(manager, root / 'run', feedback_grader=lambda _: None)
            owners = []
            for index in range(3):
                ident = manager.create('main', 'Continue repair.', repo)
                owners.append(ident)
                workflow.report['agents']['main'] = ident
                workflow.report['agents'].setdefault('original_main', ident)
                manager._session(ident)  # Record the grading-capable main while it owns the task.
                if index == 1:
                    helper = manager.create('reviewer', 'Inspect inherited source.', repo, parent=ident)
                if index:
                    workflow.report.setdefault('handoff_history', []).append({
                        'state': 'completed', 'previous_main': owners[index - 1], 'new_main': ident})
                    (Path(manager.descriptor(owners[index - 1])['root']) / 'completion-closed.json').write_text('{}')
            workflow.persist()
            try:
                manager.start(helper)
                manager.wait(helper, 5)
                manager.shutdown()
                self.assertEqual(manager.inspect(owners[-1])['status'], 'completed')
                self.assertEqual(len(requests), 2)  # Helper and current owner only.
                self.assertIn(helper, str(requests[-1]))
                self.assertIn(owners[1], str(requests[-1]))
                for ident in (owners[1], owners[2]):
                    with Store(Path(manager.descriptor(ident)['root']) / 'session.sqlite3', readonly=True) as store:
                        controls = [json.loads(row[0]) for row in store.db.execute("SELECT body FROM objects WHERE kind='control'")]
                    self.assertEqual(len(controls), 1)
                    self.assertEqual(controls[0]['observation']['parent_agent_id'], owners[1])
                workflow.reconcile_returns()
                self.assertEqual(len(requests), 2)
                self.assertFalse((Path(manager.descriptor(helper)['root']) / 'notification-error.json').exists())
                self.assertFalse(workflow.evaluate(owners[1], 'retired-grade')['executed'])
            finally:
                manager.shutdown()

    def test_middle_slice_http_failure_retries_only_that_slice_without_replaying_tools(self):
        fixture = self.capacity_fixture(); model = CapacityProvider(); failed = []
        def reply(request):
            is_slice = ('response_format' in request and
                        request['messages'][-1]['content'].startswith('{'))
            if is_slice and len(model.drafts) == 2 and not failed:
                failed.append(copy.deepcopy(request))
                return copy.deepcopy(FAILED_GENERATION), {}
            return model.complete(request), {}
        directory = fixture.root / 'session'; directory.mkdir()
        with http_model(reply) as wire:
            session = fixture.session(HTTPProvider(directory))
            self.assertEqual(session.run()['status'], 'completed')
        failure_index = next(i for i, (_, request) in enumerate(wire) if request == failed[0])
        self.assertEqual(wire[failure_index][1], wire[failure_index + 1][1])
        self.assertNotEqual(wire[failure_index][0], wire[failure_index + 1][0])
        self.assertEqual((fixture.workspace / 'result.txt').read_text(), 'recovered')
        self.assertEqual(len(fixture.effects), 1)
        self.assertTrue(fixture.events('compaction_result')[-1][1]['coverage_complete'])
        with Store(directory / 'context.sqlite3', readonly=True) as store:
            failures = [json.loads(row[0]) for row in store.db.execute("SELECT body FROM objects WHERE kind='transport_response'")
                        if json.loads(row[0]).get('response') == FAILED_GENERATION]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['usage_state'], 'unknown')

    def test_explicit_compaction_resume_reuses_durable_drafts_and_full_coverage(self):
        fixture = self.capacity_fixture(); model = CapacityProvider(invalid_slice=3)
        session = fixture.session(model)
        original = copy.deepcopy(session.state)
        failed = session.compact(max_request_bytes=5000)
        self.assertEqual(failed['status'], 'compaction_failed')
        self.assertEqual(len(failed['draft_refs']), 2)
        self.assertEqual(session.state['messages'], original['messages'])
        model.invalid_slice = None
        reopened = Session(root=session.root, provider=model, executor=fixture.execute,
                           **Session.recorded_config(session.root))
        result = reopened.compact(max_request_bytes=5000)
        self.assertEqual(result['status'], 'compacted')
        self.assertEqual(result['resumed_drafts'], 2)
        self.assertEqual(result['draft_refs'][:2], failed['draft_refs'])
        positions = [0] * len(original['messages'])
        for part in result['coverage']:
            index = part['message_index']
            self.assertEqual(part['start_byte'], positions[index])
            positions[index] = part['end_byte']
        self.assertEqual(positions, [len(json.dumps(m, ensure_ascii=False).encode()) for m in original['messages']])
        self.assertEqual(fixture.effects, [])
        self.assertEqual(reopened.state['session_id'], original['session_id'])
        self.assertEqual(reopened.state['config'], original['config'])

    def test_no_retry_signal_stop_and_original_deadline_bound_failed_generation(self):
        request = {'model': 'same-qwen-fixture', 'messages': [{'role': 'user', 'content': 'Summarize.'}]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with http_model(lambda _: (FAILED_GENERATION, {'X-A4-No-Retry': '1', 'X-A4-Relay-State': 'uncertain'})) as wire:
                observed = HTTPProvider(root / 'uncertain').complete(request, recover_no_tools=True)
                self.assertEqual(observed, FAILED_GENERATION)
                self.assertEqual(len(wire), 1)
            with http_model(lambda _: (FAILED_GENERATION, {})) as wire:
                with self.assertRaises(InferenceStopped) as stopped:
                    HTTPProvider(root / 'deadline').complete(request, deadline=time.time() + .4, recover_no_tools=True)
                self.assertEqual(stopped.exception.reason, 'deadline')
                self.assertEqual(len(wire), 1)
            cancelled = threading.Event()
            def stop_during_response(_):
                cancelled.set()
                return FAILED_GENERATION, {}
            with http_model(stop_during_response) as wire:
                with self.assertRaises(InferenceStopped) as stopped:
                    HTTPProvider(root / 'stop').complete(request, recover_no_tools=True,
                                                        cancelled=cancelled.is_set)
                self.assertEqual(stopped.exception.reason, 'cancelled')
                self.assertEqual(len(wire), 1)

    def test_candidate_submission_precedes_bulk_export_and_closure_follows_collection(self):
        from src import bridge
        with patch.dict(sys.modules, {'lab.adapters.teamharness.bridge': bridge}):
            adapter = importlib.import_module('lab.adapters.teamharness.agentic_loop')
        for fault in (None, 'candidate-proof', 'export', 'submission', 'bulk-identity'):
            with self.subTest(fault=fault):
                fixture = MainFallback(); fixture.setUp()
                try:
                    fixture.accept()
                    events = []
                    state = fixture.state
                    config = state / 'config.json'; config.write_text(json.dumps(fixture.config))
                    archive = fixture.fixture.root / 'collected'; archive.mkdir()
                    class Workspace:
                        base = fixture.config['course_base']
                        def facts(self): return {'root': str(fixture.fixture.source), 'base': self.base}
                        def effect(self, name, args):
                            raw = Path(args['path']).read_bytes()
                            start = args['offset']; end = min(len(raw), start + args['bytes'])
                            return {'base64': base64.b64encode(raw[start:end]).decode(),
                                    'offset': start, 'next_offset': end, 'total_bytes': len(raw)}
                    class Remote(adapter.Remote):
                        def read_json(self, path):
                            value = json.loads(Path(path).read_text())
                            if fault == 'candidate-proof' and Path(path).name == 'candidate-transfer.json':
                                value['files']['candidate.patch']['sha256'] = '0' * 64
                            return value
                        def command(self, operation):
                            events.append(operation)
                            if operation == 'export' and fault == 'export':
                                return {'state': 'exited', 'exit_code': 1}
                            if operation == 'export' and fault == 'bulk-identity':
                                (state / 'candidate.patch').write_bytes(b'different bytes')
                            with patch.object(sys, 'argv', ['worker', operation, '--config', str(config)]):
                                worker.main()
                            return {'state': 'exited', 'exit_code': 0}
                        def download(self, path, dest, size, expected):
                            events.append('bulk' if Path(path).name == 'evidence.tar.gz' else 'core')
                            return super().download(path, dest, size, expected)
                    class Dispatcher:
                        def submit_patch(self, ident, text):
                            events.append('submit')
                            self.submitted = text.encode()
                            if fault == 'submission': raise ConnectionError('Submission reply lost')
                        def logger(self, ident): return SimpleNamespace(api_request=lambda *a: None)
                        def done(self, *args):
                            events.append('done')
                            self.received = (archive / 'candidate.patch').read_bytes()
                            return {'fixture': 'closure only; no benchmark grade'}
                    dispatcher = Dispatcher()
                    attempt = adapter.TaskAttempt.__new__(adapter.TaskAttempt)
                    attempt.archive, attempt.state = archive, str(state)
                    attempt.remote = Remote(Workspace(), archive / 'commands')
                    attempt.dispatcher, attempt.grades = dispatcher, None
                    attempt.call = '{operation}'; attempt.result = {'state': 'exited', 'exit_code': 124}
                    attempt.deadline = time.time() - 1; attempt.installed = True
                    attempt.ident = 0; attempt.model = 'fixture'; attempt.model_drain = None
                    attempt.relay = None; attempt.planning_failure = None
                    attempt.owner = SimpleNamespace(model_scheduler=None, active_tasks={0}, facts_lock=threading.Lock())
                    if fault:
                        with self.assertRaises((RuntimeError, ConnectionError)): attempt.finish()
                    else:
                        attempt.finish()
                        self.assertEqual(dispatcher.submitted, dispatcher.received)
                    self.assertEqual(events.count('capture'), 1)
                    if fault == 'candidate-proof':
                        self.assertNotIn('submit', events)
                        self.assertNotIn('done', events)
                        self.assertIn('bulk', events)
                        self.assertTrue((archive / 'candidate-transfer-error.json').exists())
                        with self.assertRaises(FileExistsError): attempt.finish()
                        continue
                    self.assertEqual(events.count('submit'), 1)
                    self.assertLess(events.index('submit'), events.index('export'))
                    self.assertEqual(dispatcher.submitted, (archive / 'candidate.patch').read_bytes())
                    final = json.loads((archive / 'finalization.json').read_text())
                    self.assertEqual(hashlib.sha256(dispatcher.submitted).hexdigest(), final['candidate_sha256'])
                    if fault:
                        self.assertNotIn('done', events)
                        with self.assertRaises(FileExistsError): attempt.finish()
                        self.assertEqual(events.count('submit'), 1)
                        if fault in ('submission', 'bulk-identity'): self.assertIn('bulk', events)
                    else:
                        self.assertLess(events.index('bulk'), events.index('done'))
                finally:
                    fixture.doCleanups()
