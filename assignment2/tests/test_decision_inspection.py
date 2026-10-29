"""Offline decision inspection fixtures using Store/Context and recorded Agent shapes.

No Provider/Agent construction: the synthetic HTTP envelopes below are evidence, not
model runs. The compile and native Responses/execution projections are the real ones.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mads.inspection.inspect_run import Journal, sha
from mads.context import Context
from mads.responses import execution_view, native_turn, request_body
from mads.store import Store, dump, present, view


ROOT = Path(__file__).resolve().parents[1]
TASK = 'Original assignment: inspect café decisions; preserve the original requirements. 🧭'
NOTICE = ('STUDY PARTICIPANT MESSAGE — attributed contribution, not a replacement for original requirements\n'
          + dump({'id': 'notice-1', 'sender': 'primary', 'recipient': 'participant',
                  'created_unix': 12, 'content': 'A fallible suggestion, not authority.',
                  'authority': 'participant contribution'}))
SCHEMAS = [{'type': 'function', 'function': {'name': 'read', 'description': 'Read evidence',
            'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}}]


def ids(page):
    return [item['source']['record_id'] for item in page['items']]


def issue_codes(decision):
    return {item['code'] for item in decision['issues']['items']}


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.store = Store(self.root / 'journal')
        self.store.path.touch(exist_ok=True)
        self.context = Context(self.store, self.root / 'memory')
        self.account = 'Current action: inspect exactly.\nThis account is fallible.\n'
        self.context.working.write_text(self.account)
        self.start = self.store.add('run_start', task=TASK)

    def compile(self, protocol='chat', current=None):
        body = self.context.compile('System guidance', TASK, SCHEMAS, 'fixture-model', {},
            current=current, request_transform=request_body if protocol == 'responses' else None)
        request = self.store.request(body, self.context.context_id, iteration=1)
        return request, body

    def wire(self, request, body, attempt=1):
        attempt_id = self.store.add('api_attempt', request_record=request, attempt=attempt)
        # This is the saved transport body, intentionally NOT Store.dump serialization.
        raw = (json.dumps(body, indent=1, ensure_ascii=False) + '\n').encode()
        wire_id = self.store.add('http_request', request_record=request, attempt_record=attempt_id,
            attempt=attempt, method='POST', blob=self.store.blob(raw), matches_compiled_request=True)
        return wire_id, attempt_id, raw

    def response(self, request, wire, attempt_id, protocol='chat', arguments='{"path":"source.txt"}',
                 call_id='call-reused', status=200, with_assistant=True, envelope=None):
        if envelope is None:
            if protocol == 'responses':
                envelope = {'id': 'resp-native', 'status': 'completed', 'model': 'fixture-model',
                    'output': [
                        {'type': 'reasoning', 'id': 'reason-1', 'summary': [], 'encrypted_content': 'opaque' * 200},
                        {'type': 'message', 'id': 'message-1', 'role': 'assistant', 'phase': 'commentary',
                         'content': [{'type': 'output_text', 'text': 'Inspect first.'}]},
                        {'type': 'function_call', 'id': 'native-item-1', 'call_id': call_id,
                         'name': 'read', 'arguments': arguments, 'status': 'completed'}],
                    'usage': {'input_tokens': 19, 'output_tokens': 7, 'total_tokens': 26}}
            else:
                envelope = {'id': 'chat-native', 'model': 'fixture-model', 'choices': [
                    {'index': 0, 'finish_reason': 'tool_calls', 'message': {'role': 'assistant',
                     'content': None, 'reasoning_content': 'Inspect exact evidence. ' * 100,
                     'tool_calls': [{'id': call_id, 'type': 'function',
                         'function': {'name': 'read', 'arguments': arguments}}]}}],
                    'usage': {'prompt_tokens': 19, 'completion_tokens': 7, 'total_tokens': 26}}
        response_id = self.store.add('response', request_record=request, http_request_record=wire,
            attempt_record=attempt_id, status=status, blob=self.store.blob(json.dumps(envelope, indent=2)))
        if not with_assistant:
            return response_id, None, None
        native = native_turn(envelope) if protocol == 'responses' else envelope['choices'][0]['message']
        assistant_id = self.store.add('assistant', request_record=request, response_record=response_id,
            blob=self.store.blob(dump(native)), finish_reason='completed' if protocol == 'responses' else 'tool_calls',
            **({'protocol': 'responses'} if protocol == 'responses' else {}))
        execution = execution_view(native) if protocol == 'responses' else native
        return response_id, assistant_id, execution

    def calls(self, assistant_id, execution, outcomes=True):
        group_id = self.store.add('exchange_started', assistant_record=assistant_id)
        group = {'id': group_id, 'assistant': execution, 'results': [], 'fresh': True}
        invocations, results, observations = [], [], []
        for call in execution.get('tool_calls', []):
            invocation = self.store.add('invocation', assistant_record=assistant_id, exchange_id=group_id,
                call_id=call['id'], tool_name=call['function']['name'], raw_arguments=call['function']['arguments'],
                **({'native_item_id': call['native_item_id']} if 'native_item_id' in call else {}))
            invocations.append(invocation)
            if not outcomes:
                continue
            path = self.root / 'source.txt'
            path.write_text('observed source evidence café\n' * 60)
            observation = self.store.evidence(self.store.snapshot(path), kind='file_read')
            result = {'invocation_id': invocation, 'call_id': call['id'], 'tool_name': 'read',
                      'status': 'completed', 'effects': 'none', 'observation_record': observation,
                      'output': present(view(self.store.history(observation), 'full'), 120)}
            record = self.store.add('tool_result', invocation_id=invocation, call_id=call['id'], result=result)
            result['result_record'] = record
            group['results'].append(result)
            results.append(record)
            observations.append(observation)
        return invocations, results, observations, group

    def turn(self, protocol='chat', arguments='{"path":"source.txt"}', current=None, outcomes=True):
        request, body = self.compile(protocol, current)
        wire, attempt, raw = self.wire(request, body)
        response, assistant, execution = self.response(request, wire, attempt, protocol, arguments)
        invocations, results, observations, group = self.calls(assistant, execution, outcomes)
        return dict(request=request, body=body, wire=wire, attempt=attempt, raw=raw,
                    response=response, assistant=assistant, invocations=invocations,
                    results=results, observations=observations, group=group)


class DecisionInspectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='decision-inspection-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.f = Fixture(self.root)

    def journal(self):
        return Journal(self.root)

    def cli(self, *arguments):
        env = {k: v for k, v in os.environ.items() if not any(
            token in k.upper() for token in ('API_KEY', 'TOKEN', 'SECRET', 'CREDENTIAL'))}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        return subprocess.run([sys.executable, '-S', '-m', 'mads', 'inspect', str(self.root), *arguments],
            cwd=ROOT, env=env, capture_output=True, timeout=20)

    def test_chat_causal_chain_arguments_outcomes_and_exact_raw(self):
        args = dump({'path': 'café/' + 'x' * 5000})
        turn = self.f.turn(arguments=args)
        # Same native call ID in a later turn must not steal the outcome or request.
        later = self.f.turn(current=turn['group'])
        self.assertNotEqual(turn['request'], later['request'])
        j = self.journal()
        for anchor in (turn['assistant'], turn['response'], turn['invocations'][0], turn['results'][0], turn['wire']):
            with self.subTest(anchor=anchor):
                result = j.decision(anchor, preview_chars=80)
                self.assertEqual(result['state'], 'available')
                self.assertEqual(result['chain']['wire_request']['record_id'], turn['wire'])
                self.assertEqual(result['chain']['response']['record_id'], turn['response'])
                wire = result['chain']['wire_request']['payload']
                self.assertEqual(wire['sha256'], sha(turn['raw']))
                self.assertEqual(Path(wire['path']).read_bytes(), turn['raw'])
                compiled = result['chain']['compiled_request']['payload']
                self.assertNotEqual(compiled['sha256'], wire['sha256'])
                self.assertEqual(json.loads(Path(compiled['path']).read_bytes()), json.loads(turn['raw']))
                self.assertEqual(ids(result['invocations']), turn['invocations'])
                call = result['invocations']['items'][0]
                self.assertEqual(call['arguments']['text'], args[:80])
                self.assertEqual(call['arguments']['omitted_chars'], len(args) - 80)
                outcome = call['outcomes']['items'][0]
                self.assertEqual(outcome['source']['recorded_links']['invocation_id'], turn['invocations'][0])
                self.assertEqual(outcome['evidence']['items'][0]['source']['record_id'], turn['observations'][0])
                self.assertEqual(outcome['output_artifact']['state'], 'verified')
                self.assertTrue(all(edge['valid'] for edge in result['causal_links']['items']))
        focus = j.decision(turn['results'][0])
        self.assertEqual(focus['focused_invocation']['source']['record_id'], turn['invocations'][0])
        self.assertEqual(focus['selected_outcome']['source']['record_id'], turn['results'][0])

    def test_task_notice_account_controls_and_retained_source(self):
        first = self.f.turn()
        first['group']['notices'] = [{'role': 'user', 'content': NOTICE}]
        self.f.context.records = [{'record_id': first['observations'][0], 'part': 'payload',
                                  'view': {'bytes': [0, 16]}, 'purpose': 'fallible relevance claim'}]
        second = self.f.turn(current=first['group'])
        # Later edits must not become the account for the selected request.
        self.f.context.working.write_text('FUTURE ACCOUNT')
        result = self.journal().decision(second['assistant'], item=1, preview_chars=240)
        task = result['request']['original_task']
        self.assertEqual(task['state'], 'matched')
        self.assertEqual(task['positions']['items'], [1])
        self.assertEqual(task['source']['record_id'], self.f.start)
        self.assertEqual(task['source_pointer'], '/task')
        item = result['request']['selected_item']
        self.assertEqual(item['pointer'], '/messages/1')
        self.assertEqual(json.loads(item['preview']['text'])['content'], TASK)
        notice = result['request']['participant_notices']['items'][0]
        self.assertEqual(second['body']['messages'][notice['index']]['content'], NOTICE)
        self.assertEqual(json.loads(notice['claimed_metadata']['text'])['sender'], 'primary')
        self.assertIn('unverified', notice['attribution'])
        context = result['context']
        self.assertEqual(Path(context['account']['snapshot']['path']).read_text(), self.f.account)
        self.assertEqual(context['account']['validation'], 'unverified')
        self.assertIn('effective_input_limit_tokens', context['controls']['text'])
        self.assertEqual(context['retained_evidence']['items'][0]['source']['record_id'], first['observations'][0])
        self.assertEqual(context['source']['record_id'], result['chain']['context_manifest']['record_id'])

    def test_account_attribution_uses_selected_snapshot_after_seed_is_replaced(self):
        attribution = 'operator-supplied; fallible, not original requirements'
        self.f.context.seed_account = {
            'sha256': sha(self.f.context.working.read_bytes()),
            'authorship': attribution, 'validation': 'unverified'}
        seeded = self.f.turn()
        self.f.context.working.write_text('Agent revised its understanding after new evidence.')
        revised = self.f.turn(current=seeded['group'])
        journal = self.journal()
        old_account = journal.decision(seeded['assistant'])['context']['account']
        new_account = journal.decision(revised['assistant'])['context']['account']
        self.assertEqual(old_account['authorship'], attribution)
        self.assertEqual(new_account['authorship'], 'agent')
        self.assertEqual(old_account['source']['record_id'], old_account['record_id'])
        self.assertNotEqual(old_account['record_id'], new_account['record_id'])
        self.assertEqual(old_account['validation'], 'unverified')

    def test_responses_native_reasoning_calls_and_protocol_pairing(self):
        first = self.f.turn('responses')
        second = self.f.turn('responses', current=first['group'])
        result = self.journal().decision(first['assistant'], preview_chars=100)
        self.assertEqual(result['state'], 'available')
        native = result['response']['native_items']['items']
        self.assertEqual([row['pointer'] for row in native], ['/output/0', '/output/1', '/output/2'])
        self.assertIn('reasoning', native[0]['metadata']['text'])
        self.assertGreater(native[0]['preview']['omitted_chars'], 0)
        self.assertEqual(native[2]['invocation_records']['items'], first['invocations'])
        self.assertEqual(native[2]['arguments_pointer'], '/output/2/arguments')
        self.assertEqual(result['invocations']['items'][0]['source']['recorded_links']['assistant_record'], first['assistant'])
        self.assertEqual(Path(result['response']['native_source']['payload']['path']).read_bytes(),
                         (self.f.store.blobs / self.f.store.record(first['assistant'])['blob']).read_bytes())
        inputs = second['body']['input']
        self.assertTrue(any(row.get('type') == 'function_call_output' and row['call_id'] == 'call-reused' for row in inputs))
        index = next(i for i, row in enumerate(inputs) if row.get('type') == 'function_call_output')
        item = self.journal().decision(second['assistant'], item=index, preview_chars=40)['request']['selected_item']
        self.assertEqual(item['pointer'], f'/input/{index}')
        self.assertEqual(item['preview']['shown_chars'], 40)
        self.assertGreater(item['preview']['omitted_chars'], 0)

    def test_retry_selects_exact_failed_or_successful_attempt_not_nearest(self):
        request, body = self.f.compile()
        wire1, attempt1, _ = self.f.wire(request, body)
        failed, _, _ = self.f.response(request, wire1, attempt1, status=503, with_assistant=False,
                                      envelope={'error': {'message': 'synthetic transient overload'}})
        self.f.store.add('api_error', request_record=request, attempt_record=attempt1, response_record=failed,
                         retryable=True, retry_scheduled=True)
        self.f.store.add('api_retry', request_record=request, attempt_record=attempt1, next_attempt=2)
        wire2, attempt2, _ = self.f.wire(request, body, attempt=2)
        good, assistant, execution = self.f.response(request, wire2, attempt2)
        self.f.calls(assistant, execution)
        wire3, _, _ = self.f.wire(request, body, attempt=3)  # An observed, unfinished sibling.
        j = self.journal()
        success = j.decision(assistant)
        self.assertEqual(success['chain']['wire_request']['record_id'], wire2)
        self.assertEqual(success['chain']['response']['record_id'], good)
        self.assertEqual(json.loads(success['response']['usage']['text'])['total_tokens'], 26)
        self.assertNotIn('usage', json.loads(success['response']['envelope_metadata']['text']))
        self.assertEqual(success['response']['http_status'], 200)
        self.assertEqual(success['request_attempts']['total'], 10)
        failure = j.decision(failed)
        self.assertEqual(failure['chain']['wire_request']['record_id'], wire1)
        self.assertIsNone(failure['chain']['assistant'])
        self.assertEqual(failure['invocations']['total'], 0)
        latest = j.decision()
        self.assertEqual(latest['selection']['record']['record_id'], wire3)
        self.assertIsNone(latest['chain']['response'])
        self.assertEqual(latest['state'], 'partial')
        logical = j.decision(request, view='compiled')
        self.assertIsNone(logical['chain']['wire_request'])
        self.assertIsNone(logical['chain']['response'])
        self.assertEqual(logical['request']['source']['record_id'], request)
        self.assertIn('response_not_selected', issue_codes(logical))

    def test_empty_and_only_partial_journal(self):
        self.f.store.path.write_bytes(b'')
        empty = self.journal().decision()
        self.assertEqual(empty['state'], 'unavailable')
        self.assertIsNone(empty['selection']['record'])
        self.f.store.path.write_bytes(b'{"id":1,"event":"assistant"')
        tail = self.journal().decision()
        self.assertEqual(tail['state'], 'unavailable')
        self.assertEqual(tail['journal']['incomplete_tail_bytes'], len(self.f.store.path.read_bytes()))
        self.assertIn('incomplete_journal_tail', issue_codes(tail))

    def test_live_prefix_snapshot_and_pending_latest_request(self):
        first = self.f.turn()
        j = self.journal()
        pending, _ = self.f.compile(current=first['group'])
        with self.f.store.path.open('ab') as out:
            out.write(b'{"id":999,"event":"http_request","broken":"\xc3')
        # The Journal instance is a prefix, not a re-read or temporal nearest lookup.
        self.assertEqual(j.decision()['selection']['record']['record_id'], first['assistant'])
        current = self.journal().decision()
        self.assertEqual(current['selection']['record']['record_id'], pending)
        self.assertEqual(current['state'], 'partial')
        self.assertIsNone(current['chain']['response'])
        self.assertIsNone(current['chain']['wire_request'])
        self.assertGreater(current['journal']['incomplete_tail_bytes'], 0)
        self.assertEqual(current['journal']['observed_sha256'], sha(self.f.store.path.read_bytes()))

    def test_partial_at_each_agent_stage(self):
        request, body = self.f.compile()
        self.assertEqual(self.journal().decision()['state'], 'partial')
        wire, attempt, _ = self.f.wire(request, body)
        self.assertEqual(self.journal().decision()['chain']['wire_request']['record_id'], wire)
        response, _, _ = self.f.response(request, wire, attempt, with_assistant=False)
        before_assistant = self.journal().decision()
        self.assertEqual(before_assistant['selection']['record']['record_id'], response)
        self.assertEqual(before_assistant['response']['native_source']['record_id'], response)
        self.assertEqual(before_assistant['response']['native_items']['items'][-1]['pointer'], '/choices/0/message/tool_calls/0')
        self.assertIn('assistant_not_recorded_or_ambiguous', issue_codes(before_assistant))
        native = json.loads((self.f.store.blobs / self.f.store.record(response)['blob']).read_bytes())['choices'][0]['message']
        assistant = self.f.store.add('assistant', request_record=request, response_record=response,
                                    blob=self.f.store.blob(dump(native)))
        self.assertIn('native_call_without_recorded_invocation', issue_codes(self.journal().decision()))
        invocations, _, _, _ = self.f.calls(assistant, native, outcomes=False)
        result = self.journal().decision(invocations[0])
        self.assertEqual(result['focused_invocation']['outcome_state'], 'unavailable')
        self.assertIn('invocation_outcome_not_recorded', issue_codes(result))

    def test_no_request_is_captured_does_not_borrow_future_request(self):
        assistant = self.f.store.add('assistant', blob=self.f.store.blob(dump({'role': 'assistant', 'content': 'old'})))
        self.f.turn()
        result = self.journal().decision(assistant)
        self.assertEqual(result['state'], 'partial')
        self.assertIsNone(result['chain']['compiled_request'])
        self.assertIsNone(result['chain']['wire_request'])
        self.assertIsNone(result['chain']['response'])
        self.assertIn('no_captured_causal_http_request', issue_codes(result))
        self.assertEqual(result['request']['state'], 'unavailable')

    def test_missing_http_link_does_not_substitute_compiled_or_related_wire(self):
        request, body = self.f.compile()
        self.f.wire(request, body)
        response = self.f.store.add('response', request_record=request,
                                   blob=self.f.store.blob('{"choices":[]}'))
        result = self.journal().decision(response)
        self.assertIsNone(result['chain']['wire_request'])
        self.assertEqual(result['chain']['compiled_request']['record_id'], request)
        self.assertEqual(result['request']['state'], 'unavailable')
        self.assertEqual(self.journal().decision(response, view='compiled')['request']['state'], 'available')

    def test_invalid_and_conflicting_links_are_not_guessed(self):
        first = self.f.turn()
        second = self.f.turn()
        conflicting = self.f.store.add('assistant', request_record=second['request'], response_record=first['response'],
                                      blob=self.f.store.blob('{"role":"assistant","content":"conflict"}'))
        result = self.journal().decision(conflicting)
        self.assertIsNone(result['chain']['compiled_request'])
        self.assertEqual(result['chain']['wire_request']['record_id'], first['wire'])
        self.assertIn('conflicting_request_links', issue_codes(result))
        for target in (99999, first['invocations'][0], 'wrong-type'):
            record = self.f.store.add('assistant', request_record=target, response_record=target,
                                     blob=self.f.store.blob('{"role":"assistant"}'))
            bad = self.journal().decision(record)
            self.assertIsNone(bad['chain']['response'])
            self.assertIsNone(bad['chain']['compiled_request'])
            self.assertIn('invalid_or_unavailable_link', issue_codes(bad))
        # The target exists, but only after the response that allegedly references it.
        future = len(self.f.store.events) + 2
        bad_response = self.f.store.add('response', request_record=first['request'], http_request_record=future,
                                       blob=self.f.store.blob('{}'))
        self.f.store.add('http_request', request_record=first['request'], blob=self.f.store.blob('{}'))
        self.assertIsNone(self.journal().decision(bad_response)['chain']['wire_request'])

    def test_ambiguous_children_do_not_pick_last_or_first(self):
        turn = self.f.turn()
        self.f.response(turn['request'], turn['wire'], turn['attempt'])
        result = self.journal().decision(turn['wire'])
        self.assertIsNone(result['chain']['response'])
        self.assertIn('ambiguous_children', issue_codes(result))
        # An explicit response remains selectable, regardless of sibling ambiguity.
        exact = self.journal().decision(turn['response'])
        self.assertEqual(exact['chain']['response']['record_id'], turn['response'])

    def test_rejected_response_retains_native_calls_without_executions(self):
        request, body = self.f.compile('responses')
        wire, attempt, _ = self.f.wire(request, body)
        response, assistant, _ = self.f.response(request, wire, attempt, protocol='responses')
        rejection = self.f.store.add('response_rejected', response_record=assistant,
                                    error='Synthetic replay validation failure; no calls executed')
        result = self.journal().decision(response)
        self.assertEqual(ids(result['response_rejections']), [rejection])
        self.assertEqual(result['response']['native_items']['total'], 3)
        self.assertEqual(result['invocations']['total'], 0)
        self.assertEqual(result['state'], 'partial')

    def test_corrupt_missing_and_non_json_blobs_report_partial_without_rebuilding(self):
        turn = self.f.turn()
        path = self.f.store.blobs / self.f.store.record(turn['wire'])['blob']
        path.write_bytes(b'corrupted')
        corrupt = self.journal().decision(turn['assistant'])
        self.assertEqual(corrupt['chain']['wire_request']['payload']['state'], 'unavailable')
        self.assertEqual(corrupt['request']['state'], 'unavailable')
        self.assertEqual(corrupt['state'], 'partial')
        self.assertIn('blob_unavailable', issue_codes(corrupt))
        path.unlink()
        missing = self.journal().decision(turn['assistant'], item=100)
        self.assertIn('request_item_unavailable', issue_codes(missing))
        invalid = self.f.store.add('http_request', request_record=turn['request'], blob=self.f.store.blob(b'not JSON\xff'))
        self.assertIn('payload_not_json', issue_codes(self.journal().decision(invalid)))
        invalid_hash = self.f.store.add('http_request', request_record=turn['request'], blob='../not-a-blob')
        self.assertIn('invalid_blob_identity', issue_codes(self.journal().decision(invalid_hash)))

    def test_non_object_body_and_input_string_are_inspectable(self):
        request, _ = self.f.compile('responses')
        for raw in ('[]', '"text"', 'null'):
            wire = self.f.store.add('http_request', request_record=request, blob=self.f.store.blob(raw))
            result = self.journal().decision(wire, item=0)
            self.assertEqual(result['request']['state'], 'unavailable')
            self.assertIn('request_item_unavailable', issue_codes(result))
            self.assertIn('payload_not_object', issue_codes(result))
        wire = self.f.store.add('http_request', request_record=request, blob=self.f.store.blob('{"input":"text"}'))
        result = self.journal().decision(wire, item=0)
        self.assertEqual(result['request']['selected_item']['pointer'], '/input')
        self.assertEqual(result['request']['original_task']['state'], 'not_matched')

    def test_pagination_bounds_and_focused_outcomes(self):
        turn = self.f.turn()
        # A large sibling call batch is real Agent-shaped even without actual execution.
        assistant = turn['assistant']
        call = {'id': 'many', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}
        more = {'role': 'assistant', 'tool_calls': [dict(call, id=f'call-{n}') for n in range(25)]}
        invocations, results, _, _ = self.f.calls(assistant, more)
        page = self.journal().decision(assistant, offset=20, limit=4, preview_chars=0)
        self.assertEqual(page['invocations']['total'], 26)
        self.assertEqual(page['invocations']['omitted_before'], 20)
        self.assertEqual(page['invocations']['omitted_after'], 2)
        self.assertEqual(page['invocations']['next_offset'], 24)
        self.assertEqual(page['invocations']['items'][0]['outcomes']['offset'], 0)
        self.assertEqual(page['invocations']['items'][0]['outcomes']['total'], 1)
        self.assertEqual(page['invocations']['items'][0]['arguments']['shown_chars'], 0)
        focused = self.journal().decision(invocations[-1], limit=1)
        self.assertEqual(focused['focused_invocation']['source']['record_id'], invocations[-1])
        self.assertEqual(ids(focused['focused_invocation']['outcomes']), [results[-1]])
        beyond = self.journal().decision(assistant, offset=500, limit=1)['invocations']
        self.assertEqual(beyond['items'], [])
        self.assertEqual(beyond['omitted_before'], 26)
        self.assertEqual(beyond['omitted_after'], 0)
        self.assertIsNone(beyond['next_offset'])

    def test_all_preview_and_page_counts_are_explicit(self):
        turn = self.f.turn(arguments=dump({'path': 'x' * 200000}))
        result = self.journal().decision(turn['assistant'], preview_chars=31, limit=3)
        def check(value):
            if isinstance(value, dict):
                if 'shown_chars' in value:
                    self.assertEqual(len(value['text']), value['shown_chars'])
                    self.assertLessEqual(value['shown_chars'], 31)
                    self.assertEqual(value['chars'], value['shown_chars'] + value['omitted_chars'])
                if 'omitted_before' in value:
                    self.assertLessEqual(len(value['items']), 3)
                    self.assertEqual(value['total'], value['omitted_before'] + len(value['items']) + value['omitted_after'])
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(result)
        self.assertLess(len(json.dumps(result)), 30000)  # Not proportional to the 200k argument.
        self.assertNotIn('x' * 100, json.dumps(result))

    def test_invalid_selection_and_options_fail_cleanly(self):
        turn = self.f.turn()
        j = self.journal()
        for record in (0, -1, 999999, self.f.start, 'nope', True, 1.5):
            with self.subTest(record=record), self.assertRaises(ValueError):
                j.decision(record)
        for options in ({'item': -1}, {'item': True}, {'item': 999}, {'preview_chars': -1},
                        {'preview_chars': 4001}, {'offset': -1}, {'limit': 0}, {'limit': 101}, {'view': 'bad'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                j.decision(turn['assistant'], **options)
        result = self.cli('decision', '--record', '999999')
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'Unknown decision record', result.stderr)
        self.assertNotIn(b'Traceback', result.stderr)
        result = self.cli('decision', '--limit', '0')
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'limit must be 1..100', result.stderr)

    def test_cli_no_site_packages_credentials_or_source_mutations(self):
        turn = self.f.turn()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.cli('decision', '--record', str(turn['invocations'][0]), '--item', '1', '--preview-chars', '80')
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed['chain']['wire_request']['record_id'], turn['wire'])
        ref = parsed['chain']['wire_request']
        recovered = subprocess.run([sys.executable, '-S', *ref['retrieve_argv'][1:]], cwd=ROOT,
                                   capture_output=True, timeout=20)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(recovered.stdout, turn['raw'])
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_record_byte_ranges_and_old_views_remain_compatible(self):
        turn = self.f.turn(arguments='a' * 600)
        j = self.journal()
        result = j.decision(turn['assistant'])
        ref = result['invocations']['items'][0]['source']['record_source']
        start, end = ref['byte_range']
        line = Path(ref['path']).read_bytes()[start:end]
        self.assertTrue(line.endswith(b'\n'))
        self.assertEqual(json.loads(line)['raw_arguments'], 'a' * 600)
        self.assertEqual(len(j.timeline()[0]['arguments_preview']), 400)
        self.assertEqual(j.timeline()[0]['arguments_chars'], 600)
        self.assertIn('Current action', j.accounts()[0]['diff'])
        self.assertEqual(j.summary()['recorded_response_usage']['total_tokens'], 26)
        old = j.request(out=self.root / 'export')
        self.assertEqual(old['request_record'], turn['wire'])
        self.assertEqual((self.root / 'export/request.json').read_bytes(), turn['raw'])
        self.assertEqual(self.cli('request', '--raw').stdout, turn['raw'])
        readable = self.cli('request', '--readable')
        self.assertEqual(readable.returncode, 0, readable.stderr)
        self.assertEqual(json.loads(readable.stdout), turn['body'])


    def test_missing_context_and_dangling_evidence_are_explicit(self):
        turn = self.f.turn()
        # A missing causal context cannot be replaced by a future manifest.
        request = self.f.store.add('request', context_id=99999, blob=self.f.store.blob(dump(turn['body'])))
        wire, attempt, _ = self.f.wire(request, turn['body'])
        _, assistant, execution = self.f.response(request, wire, attempt)
        self.f.calls(assistant, execution)
        missing_context = self.journal().decision(assistant)
        self.assertEqual(missing_context['context']['state'], 'unavailable')
        self.assertEqual(missing_context['state'], 'partial')
        self.assertIn('invalid_or_unavailable_link', issue_codes(missing_context))
        result_record = self.f.store.add('tool_result', invocation_id=turn['invocations'][0],
            result={'status': 'failed', 'effects': 'none', 'observation_record': 99999})
        missing_source = self.journal().decision(result_record)
        evidence = missing_source['selected_outcome']['evidence']['items'][0]
        self.assertEqual(evidence['record_id'], 99999)
        self.assertIsNone(evidence['source'])
        self.assertIn('outcome_evidence_unavailable', issue_codes(missing_source))
        manifest = self.f.store.add('context_manifest', retained_evidence=[{'record_id': 99999}])
        request = self.f.store.add('request', context_id=manifest, blob=self.f.store.blob(dump(turn['body'])))
        missing_retention = self.journal().decision(request, view='compiled')
        self.assertIn('retained_evidence_unavailable', issue_codes(missing_retention))
        self.assertIsNone(missing_retention['context']['retained_evidence']['items'][0]['source'])

    def test_final_message_and_usage_are_distinct_from_source_identity(self):
        (self.root / 'source-manifest.json').write_text(dump({'source': 'fixture'}))
        request, body = self.f.compile()
        wire, attempt, _ = self.f.wire(request, body)
        envelope = {'choices': [{'message': {'role': 'assistant', 'content': 'Done.'},
                                'finish_reason': 'stop'}], 'usage': {'total_tokens': 8}}
        response, assistant, _ = self.f.response(request, wire, attempt, envelope=envelope)
        result = self.journal().decision(response)
        self.assertEqual(result['state'], 'available')
        self.assertEqual(result['invocations']['total'], 0)
        self.assertEqual(json.loads(result['response']['usage']['text']), {'total_tokens': 8})
        identity = result['source_identity']['files']['source-manifest.json']
        self.assertEqual(identity['sha256'], sha(Path(identity['path']).read_bytes()))
        self.assertNotIn('usage', identity)
        self.assertEqual(result['chain']['assistant']['record_id'], assistant)

    def test_pending_native_call_outside_page_is_not_reported_complete(self):
        request, body = self.f.compile()
        wire, attempt, _ = self.f.wire(request, body)
        _, assistant, execution = self.f.response(request, wire, attempt)
        pending = self.journal().decision(assistant, limit=1)
        self.assertEqual(pending['response']['native_items']['total'], 4)
        self.assertEqual(pending['state'], 'partial')
        self.assertIn('native_call_without_recorded_invocation', issue_codes(pending))
        first, _, _, _ = self.f.calls(assistant, execution)
        invocation = self.f.store.add('invocation', assistant_record=assistant,
            call_id='pending', raw_arguments='{}', tool_name='read')
        pending = self.journal().decision(assistant, limit=1)
        self.assertEqual(ids(pending['invocations']), first)
        self.assertEqual(pending['issues']['items'][0]['record_id'], invocation)
        self.assertEqual(pending['state'], 'partial')

    def test_malformed_complete_line_and_duplicate_records_are_errors_not_empty(self):
        original = self.f.store.path.read_bytes()
        self.f.store.path.write_bytes(original + b'{invalid}\n')
        with self.assertRaises(ValueError):
            self.journal()
        bad = self.cli('decision')
        self.assertEqual(bad.returncode, 2)
        self.assertNotIn(b'Traceback', bad.stderr)
        self.f.store.path.write_bytes(original + original)
        with self.assertRaisesRegex(ValueError, 'Duplicate journal record IDs'):
            self.journal()


if __name__ == '__main__':
    unittest.main()
