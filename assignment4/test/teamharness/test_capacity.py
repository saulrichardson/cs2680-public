"""Context capacity receipts and automatic model-authored recovery, no paid calls."""
from contextlib import closing
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.teamharness.provider import HTTPProvider, parse_completion
from src.teamharness.session import Session, context_status, request_bytes, CONTROL_TOOLS
from src.teamharness.types import CompletionKind


ERROR = {'error': {'code': 'context_length_exceeded', 'message': 'Maximum context length exceeded: request rejected before generation.'}}
SHELL = {'type': 'function', 'function': {'name': 'exec_command', 'parameters': {'type': 'object', 'properties': {'command': {'type': 'string'}}}}}


def answer(text):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': text}}]}


def calls(items):
    return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
        'tool_calls': [{'id': 'c' + str(i), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}
                       for i, (name, args) in enumerate(items)]}}]}


def archive_slices(content):
    """Recover each slice's identity and exact text from the bounded recovery request."""
    import re
    header, _, rest = content.partition('\n\n')
    source_ref, epoch = re.match(r'Archived transcript slices from (obj:\d+) \(epoch (\d+)\)', header).groups()
    lines, pieces, index = rest.split('\n'), [], 0
    while index + 1 < len(lines):
        match = re.fullmatch(r'Slice of message (\d+) \(serialized bytes (\d+)-(\d+) of (\d+)\):', lines[index])
        if not match:
            break
        pieces.append({'source_ref': source_ref, 'epoch': int(epoch), 'message_index': int(match[1]),
                       'start_byte': int(match[2]), 'end_byte': int(match[3]), 'message_bytes': int(match[4]),
                       'text': lines[index + 1]})
        index += 3
    return pieces


class CapacityProvider:
    model = 'same-qwen-fixture'

    def __init__(self, invalid_slice=None, oversized_draft=False):
        self.requests = []
        self.drafts = []
        self.slices = []
        self.invalid_slice = invalid_slice
        self.oversized_draft = oversized_draft
        self.agent_calls = 0

    def complete(self, request):
        self.requests.append(copy.deepcopy(request))
        if request_bytes(request) > 5500:
            return copy.deepcopy(ERROR)
        if 'response_format' in request:
            payload = request['messages'][-1]['content']
            if payload.startswith('Archived transcript slices from '):
                self.slices.append(archive_slices(payload))
                if self.invalid_slice == len(self.slices):
                    return answer('invalid model handoff')
                draft = ('MODEL DRAFT ' + str(len(self.slices)) + ': preserve task Ω and inspect existing result.txt.')
                if self.oversized_draft:
                    draft += ' model content' * 2000
                self.drafts.append(draft)
                return answer(json.dumps({'continuation': draft}))
            return answer('{"continuation":"full model handoff"}')
        self.agent_calls += 1
        if self.agent_calls == 1:
            return calls([('exec_command', {'command': 'printf recovered > result.txt'})])
        return calls([('finish_role', {'outcome': 'complete', 'report': 'Recovered with original evidence retained.'})])


class CapacityTests(unittest.TestCase):
    def test_rejected_compacted_successor_stops_without_a_recovery_cycle(self):
        requests = []
        def provider(request):
            requests.append(copy.deepcopy(request))
            if 'response_format' in request:
                return answer('{"continuation":"x"}')
            return copy.deepcopy(ERROR)
        session = self.session(provider, task='Small task with irreducible tool overhead.')
        original_config = copy.deepcopy(session.state['config'])
        self.assertEqual(session.run()['status'], 'context_blocked')
        self.assertLessEqual(len(requests), 4)
        count = len(requests)
        self.assertEqual(session.run()['status'], 'context_blocked')
        self.assertEqual(len(requests), count)
        self.assertEqual(session.state['config'], original_config)
        self.assertEqual(self.effects, [])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.effects = []

    def execute(self, name, args, effect):
        self.effects.append(effect)
        result = subprocess.run(['/bin/bash', '-c', args['command']], cwd=self.workspace, capture_output=True, text=True)
        return {'exit_code': result.returncode, 'stdout': result.stdout}

    def session(self, provider, task=None, metadata=None):
        session = Session(self.root / 'session', 'implementer', 'Stable system: preserve exact evidence.',
            task or 'Recover the investigation and inspect result.txt.', provider, [SHELL], self.execute, self.workspace,
            metadata=metadata or {'context_limit_tokens': 262144, 'context_limit_source': 'declared fixture model listing',
                                 'shared_solver_deadline_unix': 2000000000, 'child': 'keep-existing-child'})
        if task is None:
            # Accumulated model history is compressible; the assignment must fit.
            state = session._load()
            state['messages'].append({'role': 'assistant', 'content': 'Prior exploration Ω🚀 ' * 1100})
            session._save(state)
        return session

    def events(self, kind):
        with closing(sqlite3.connect(self.root / 'session/session.sqlite3')) as db:
            return [(ident, json.loads(body)) for ident, body in db.execute('SELECT id,body FROM objects WHERE kind=? ORDER BY id', (kind,))]

    def test_invalid_model_summary_blocks_without_rewriting_or_repeated_inference(self):
        provider = CapacityProvider(invalid_slice=1)
        session = self.session(provider)
        original = copy.deepcopy(session.state['messages'])
        self.assertEqual(session.run()['status'], 'context_blocked')
        self.assertEqual(session._load()['messages'], original)
        self.assertEqual(session.run()['status'], 'context_blocked')
        session.append_user('Additional task information, not a request to erase history.')
        self.assertEqual(session.run()['status'], 'context_blocked')
        session.request_stop()
        session.append_user('Resume when explicit compaction succeeds.')
        self.assertEqual(session.run()['status'], 'context_blocked')
        request_count = len(provider.requests)
        self.assertGreater(request_count, 2)
        self.assertEqual(session.run()['status'], 'context_blocked')
        self.assertEqual(len(provider.requests), request_count)
        self.assertEqual(self.effects, [])
        status = session.context_status()
        self.assertEqual(status['capacity_error']['error'], ERROR['error'])
        self.assertTrue(status['capacity_error']['request_ref'].startswith('obj:'))
        self.assertEqual(self.events('model_response')[0][1]['response'], ERROR)
        self.assertEqual(status['context_limit_tokens'], 262144)
        self.assertIsNone(status['current_input_tokens'])
        self.assertEqual(status['shared_solver_deadline_unix'], 2000000000)

    def test_automatic_full_then_adaptive_bounded_recovery_covers_all_bytes_and_executes_tools(self):
        provider = CapacityProvider()
        session = self.session(provider)
        original = copy.deepcopy(session._load())
        final = session.run()
        self.assertEqual(final['status'], 'completed')
        attempts = [body['result'] for _, body in self.events('automatic_capacity_recovery')]
        self.assertGreater(len(attempts), 2)
        self.assertEqual(attempts[0]['status'], 'compaction_failed')
        self.assertEqual(attempts[0]['method'], 'full_history')
        self.assertEqual(attempts[0]['failure_kind'], 'context_capacity')
        result = attempts[-1]
        self.assertEqual(result['status'], 'compacted')
        self.assertTrue(result['coverage_complete'])
        budgets = [attempt['max_request_bytes'] for attempt in attempts[1:]]
        self.assertTrue(all(right == max(1, left // 2) for left, right in zip(budgets, budgets[1:])))
        self.assertIn({'role': 'assistant', 'content': provider.drafts[-1]}, session._load()['messages'])
        self.assertEqual(session._load()['config']['metadata'], original['config']['metadata'])
        self.assertEqual(session._load()['epoch'], 1)
        self.assertIsNone(session._load()['capacity_error'])
        self.assertGreater(len(provider.slices), 1)
        sources = self.events('compaction_source')
        source = next((ident, body) for ident, body in sources if 'obj:' + str(ident) == result['source_ref'])
        source_messages = source[1]['messages']
        self.assertEqual(source_messages, original['messages'])
        reconstructed = [bytearray() for _ in source_messages]
        source_ref = result['source_ref']
        for group in provider.slices:
            for piece in group:
                self.assertEqual(piece['source_ref'], source_ref)
                self.assertEqual(piece['epoch'], 0)
                output = reconstructed[piece['message_index']]
                self.assertEqual(piece['start_byte'], len(output))
                output.extend(piece['text'].encode('utf-8'))
                self.assertEqual(piece['end_byte'], len(output))
        for message, raw in zip(source_messages, reconstructed):
            self.assertEqual(bytes(raw), json.dumps(message, ensure_ascii=False).encode('utf-8'))
        self.assertEqual(result['source_bytes'], sum(map(len, reconstructed)))
        recovery_requests = [q for q in provider.requests if 'response_format' in q and request_bytes(q) <= 5500]
        for i, request in enumerate(recovery_requests):
            self.assertLessEqual(request_bytes(request), result['max_request_bytes'])
            self.assertEqual(request['model'], provider.model)
            self.assertEqual(request['messages'][0], original['messages'][0])
            self.assertNotIn('tools', request)
            self.assertNotIn('tool_choice', request)
            if i:
                self.assertIn({'role': 'assistant', 'content': provider.drafts[i - 1]}, request['messages'])
        request_count = len(provider.requests)
        self.assertEqual(session.run()['status'], 'completed')
        self.assertEqual(len(provider.requests), request_count)
        self.assertEqual((self.workspace / 'result.txt').read_text(), 'recovered')
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(final['epoch'], 1)
        self.assertEqual(self.events('context_capacity_error')[0][1]['error'], ERROR['error'])

    def test_oversized_assignment_remains_explicitly_blocked_without_effects(self):
        provider = CapacityProvider()
        task = 'Literal human requirement Ω🚀 ' * 1100
        session = self.session(provider, task=task)
        self.assertEqual(session.run()['status'], 'context_blocked')
        self.assertIn(task, str(session._load()['messages']))
        self.assertEqual(self.effects, [])
        count = len(provider.requests)
        self.assertEqual(session.run()['status'], 'context_blocked')
        self.assertEqual(len(provider.requests), count)

    def test_invalid_middle_slice_keeps_original_epoch_and_messages(self):
        provider = CapacityProvider(invalid_slice=2)
        session = self.session(provider)
        original = copy.deepcopy(session._load())
        self.assertEqual(session.run()['status'], 'context_blocked')
        result = self.events('compaction_result')[-1][1]
        self.assertEqual(result['status'], 'compaction_failed')
        self.assertEqual(session._load()['messages'], original['messages'])
        self.assertEqual(session._load()['epoch'], original['epoch'])
        self.assertEqual(session._load()['config'], original['config'])
        self.assertEqual(len(provider.slices), 2)
        self.assertEqual(len(result['draft_refs']), 1)
        self.assertNotIn('coverage_complete', result)
        self.assertEqual(self.effects, [])
        request_count = len(provider.requests)
        self.assertEqual(session.run()['status'], 'context_blocked')
        self.assertEqual(len(provider.requests), request_count)

    def test_existing_blocked_session_resumes_with_full_no_tools_handoff_without_replaying_effects(self):
        class Provider:
            model = 'same-qwen-fixture'
            def __init__(self): self.requests = []; self.normal = 0
            def complete(self, request):
                self.requests.append(copy.deepcopy(request))
                if 'response_format' in request:
                    return answer(json.dumps({'continuation': 'MODEL: once.txt already contains x. Inspect it; do not append again.'}))
                self.normal += 1
                if self.normal == 1:
                    return calls([('exec_command', {'command': 'printf x >> once.txt'})])
                if self.normal == 2:
                    return copy.deepcopy(ERROR)
                if self.normal == 3:
                    return calls([('exec_command', {'command': 'test "$(cat once.txt)" = x'})])
                return calls([('finish_role', {'outcome': 'complete', 'report': 'Prior effect inspected, not replayed.'})])
        provider = Provider()
        session = self.session(provider, task='Make exactly one mark, verify it, and finish.')
        # This is the durable state produced by the previous runtime at capacity.
        # Actual first tool and HTTP-like capacity observation are retained.
        with patch.object(Session, '_recover_capacity', return_value=False):
            self.assertEqual(session.run()['status'], 'context_blocked')
        before = copy.deepcopy(session._load())
        original_events = self.events('model_request') + self.events('model_response')
        first_effect = self.effects[0]
        resumed = self.session(provider, task='Make exactly one mark, verify it, and finish.')
        self.assertEqual(resumed.run()['status'], 'completed')
        self.assertEqual(resumed._load()['epoch'], 1)
        self.assertEqual(resumed._load()['config'], before['config'])
        self.assertEqual((self.workspace / 'once.txt').read_text(), 'x')
        self.assertEqual(self.effects.count(first_effect), 1)
        self.assertEqual(len(self.effects), 2)
        compact = provider.requests[2]
        self.assertNotIn('tools', compact)
        self.assertNotIn('tool_choice', compact)
        self.assertEqual(compact['model'], provider.model)
        self.assertEqual(compact['messages'][:-1], before['messages'])
        after_events = self.events('model_request') + self.events('model_response')
        self.assertTrue(all(row in after_events for row in original_events))
        self.assertEqual(len(self.events('automatic_capacity_recovery')), 1)

    def test_deadline_between_bounded_slices_retains_epoch_and_sends_no_more_requests(self):
        clock = [1000.0]
        class Provider(CapacityProvider):
            def complete(self, request):
                result = super().complete(request)
                if self.drafts:
                    clock[0] = 1011.0
                return result
        provider = Provider()
        metadata = {'task_deadline_unix': 1010.0, 'child': 'unchanged'}
        with patch('src.teamharness.session.time.time', side_effect=lambda: clock[0]):
            session = self.session(provider, metadata=metadata)
            before = copy.deepcopy(session._load())
            result = session.run()
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(session._load()['epoch'], 0)
        self.assertEqual(session._load()['messages'], before['messages'])
        self.assertEqual(session._load()['config'], before['config'])
        self.assertEqual(len(provider.drafts), 1)
        self.assertEqual(self.effects, [])
        last = self.events('compaction_result')[-1][1]
        self.assertEqual(last['failure_kind'], 'task_deadline')
        self.assertNotIn('coverage_complete', last)
        count = len(provider.requests)
        with patch('src.teamharness.session.time.time', return_value=1012.0):
            self.assertEqual(session.run()['status'], 'stopped')
        self.assertEqual(len(provider.requests), count)

    def test_stop_arriving_during_automatic_summary_never_activates_or_executes(self):
        class Provider:
            model = 'same-qwen-fixture'
            def __init__(self): self.requests = []
            def complete(self, request):
                self.requests.append(copy.deepcopy(request))
                if 'response_format' not in request:
                    return copy.deepcopy(ERROR)
                session.request_stop(identity='stop-during-summary')
                return answer('{"continuation":"MODEL: continue with a new shell action."}')
        provider = Provider()
        session = self.session(provider, task='Small task.')
        before = copy.deepcopy(session._load())
        result = session.run()
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(session._load()['epoch'], 0)
        self.assertEqual(session._load()['messages'], before['messages'])
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.events('compaction_result')[-1][1]['status'], 'compaction_failed')

    def test_budget_never_silently_clips_system_or_model_draft(self):
        provider = CapacityProvider(oversized_draft=True)
        session = self.session(provider)
        original = copy.deepcopy(session._load())
        result = session.compact(max_request_bytes=1)
        self.assertEqual(result['status'], 'compaction_failed')
        self.assertEqual(len(provider.requests), 0)
        self.assertEqual(session._load(), original)
        result = session.compact(max_request_bytes=4000)
        self.assertEqual(result['status'], 'compaction_failed')
        self.assertIn('nothing was clipped', result['error'])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(session._load(), original)
        self.assertEqual(self.events('compaction_draft')[0][1]['continuation'], provider.drafts[0])

    def test_operator_queue_recovers_blocked_role_and_identity_covers_budget(self):
        provider = CapacityProvider(invalid_slice=1)
        session = self.session(provider)
        self.assertEqual(session.run()['status'], 'context_blocked')
        provider.invalid_slice = None
        before = copy.deepcopy(session._load()['messages'])
        session.request_compaction(identity='full')
        self.assertEqual(session.run()['status'], 'context_blocked')
        self.assertEqual(session._load()['messages'], before)
        session.request_compaction(identity='bounded', max_request_bytes=4000)
        session.request_compaction(identity='bounded', max_request_bytes=4000)
        with self.assertRaises(ValueError):
            session.request_compaction(identity='bounded', max_request_bytes=4100)
        self.assertEqual(session.run()['status'], 'completed')
        self.assertEqual(session._load()['epoch'], 1)
        self.assertEqual((self.workspace / 'result.txt').read_text(), 'recovered')
        for invalid in (True, 0, -1, 1.5, '4000'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                session.request_compaction(max_request_bytes=invalid)

    def test_model_requested_bounded_compaction_waits_for_complete_tool_batch(self):
        outer = self
        class Provider:
            model = 'same-model'
            def __init__(self): self.requests = []; self.normal = 0
            def complete(self, request):
                self.requests.append(copy.deepcopy(request))
                if 'response_format' in request:
                    outer.assertTrue((outer.workspace / 'before').exists())
                    outer.assertTrue((outer.workspace / 'after').exists())
                    outer.assertNotIn('tools', request)
                    outer.assertLessEqual(request_bytes(request), 5000)
                    return answer('{"continuation":"Model authored complete-batch handoff."}')
                self.normal += 1
                if self.normal == 1:
                    return calls([('exec_command', {'command': 'touch before'}),
                                  ('compact', {'max_request_bytes': 5000}),
                                  ('exec_command', {'command': 'touch after'})])
                return calls([('finish_role', {'outcome': 'complete', 'report': 'Finished.'})])
        provider = Provider()
        metadata = {'task_deadline_unix': 2000000001, 'child': 'preserved'}
        session = self.session(provider, task='Small task', metadata=metadata)
        self.assertEqual(session.run()['status'], 'completed')
        self.assertEqual(session._load()['epoch'], 1)
        self.assertEqual(session._load()['config']['metadata'], metadata)
        self.assertEqual(session.context_status()['task_deadline_unix'], 2000000001)
        source = self.events('compaction_source')[0][1]['messages']
        self.assertEqual(len([m for m in source if m['role'] == 'tool']), 3)
        self.assertEqual(len(self.effects), 2)

    def test_real_http_recovery_transmits_bounded_no_tools_requests(self):
        provider = CapacityProvider()
        wire = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                request = json.loads(raw)
                wire.append((len(raw), copy.deepcopy(request)))
                response = provider.complete(request)
                encoded = json.dumps(response).encode()
                self.send_response(400 if response.get('error') else 200)
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            def log_message(self, *_): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        directory = self.root / 'session'
        directory.mkdir()
        with patch.dict(os.environ, {'A4_MODEL': provider.model, 'A4_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                                      'A4_API_KEY': 'local-fixture-key', 'A4_API_PROTOCOL': 'chat_completions'}):
            session = self.session(HTTPProvider(directory))
            self.assertEqual(session.run()['status'], 'completed')
        for size, request in wire:
            self.assertEqual(size, request_bytes(request))
            if 'response_format' in request and request['messages'][-1]['content'].startswith('Archived transcript slices from '):
                self.assertNotIn('tools', request)
                self.assertNotIn('tool_choice', request)
                self.assertEqual(request['model'], provider.model)
        self.assertEqual((self.workspace / 'result.txt').read_text(), 'recovered')
        self.assertEqual(self.events('compaction_result')[-1][1]['coverage_complete'], True)

    def test_context_status_is_read_only_and_usage_is_attributed_to_exact_request(self):
        response = answer('finished')
        response['usage'] = {'prompt_tokens': 123, 'completion_tokens': 9, 'total_tokens': 132,
                             'prompt_tokens_details': {'cached_tokens': 80}}
        class Provider:
            model = 'fixture'
            def complete(self, _request): return copy.deepcopy(response)
        session = self.session(Provider(), task='short task')
        session.run()
        database = self.root / 'session/session.sqlite3'
        before = database.read_bytes()
        status = context_status(self.root / 'session')
        self.assertEqual(status, context_status(database))
        self.assertEqual(database.read_bytes(), before)
        usage = status['last_reported_usage']
        self.assertEqual(usage['usage'], response['usage'])
        self.assertEqual(usage['request_ref'], 'obj:' + str(self.events('model_request')[0][0]))
        self.assertEqual(usage['purpose'], 'agent')
        self.assertEqual(usage['epoch'], 0)
        self.assertGreater(status['active_request_bytes'], 0)
        self.assertIsNone(status['current_input_tokens'])
        self.assertEqual(status['context_limit_source'], 'declared fixture model listing')

    def test_capacity_classifier_does_not_relabel_other_errors(self):
        self.assertEqual(parse_completion(ERROR).kind, CompletionKind.CONTEXT_CAPACITY)
        self.assertEqual(parse_completion({'error': {'code': 500, 'message': 'temporarily unavailable'}}).kind, CompletionKind.ERROR)
        self.assertEqual(parse_completion({'choices': []}).kind, CompletionKind.ERROR)
        self.assertEqual(parse_completion({'error': 'input too long'}).kind, CompletionKind.CONTEXT_CAPACITY)
        self.assertIn('max_request_bytes', CONTROL_TOOLS[0]['function']['parameters']['properties'])

    def test_http_capacity_preserves_original_error_and_transport_evidence(self):
        raw = json.dumps(ERROR).encode()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(400)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *_): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        directory = self.root / 'session'
        directory.mkdir()
        with patch.dict(os.environ, {'A4_MODEL': 'fixture', 'A4_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                                      'A4_API_KEY': 'local-fixture-key', 'A4_API_PROTOCOL': 'chat_completions'}):
            session = self.session(HTTPProvider(directory), task='provider declared limit exceeded')
            status = session.run()
        self.assertEqual(status['status'], 'context_blocked')
        observed = self.events('model_response')[0][1]
        self.assertEqual(observed['response'], ERROR)
        self.assertEqual(observed['transport']['http_status'], 400)
        ref = observed['transport']['raw_response_ref']
        with closing(sqlite3.connect(directory / 'context.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT bytes FROM blobs WHERE object_id=?', (int(ref[4:]),)).fetchone()[0], raw)
        self.assertEqual(status['context_status']['capacity_error']['transport']['raw_response_ref'], ref)


if __name__ == '__main__':
    unittest.main()
