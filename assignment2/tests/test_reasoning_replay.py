"""Reversible plain Chat replay through the shared compiler and actual SDK transport.

Responses and signed/structured blocks are not candidates for this presentation
policy. These are offline mechanical fixtures, not evidence of model-solving gains.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import httpx

from mads.agent import Agent, main as agent_main
from mads.context import Context
from mads.course_logs import project
from mads.handoff import export_bundle
from mads.provider import Provider
from mads.store import Store, dump
from mads.tools import validate


TASK = 'Original fixture task: keep effects and native evidence faithful.'
PLAIN = ('reasoning_content', 'reasoning')


def chat_turn(number, actions=(), plain=True):
    message = dict(role='assistant', content=f'commentary-{number}', reasoning_details=[
        dict(type='reasoning.encrypted', data=f'opaque-{number}+/=', signature=f'sig-{number}',
             nested={'reasoning': 'do not filter structured fields'})])
    if plain:
        message.update(reasoning_content=f'plain-content-{number}\r\né',
                       reasoning=f'plain-alias-{number}')
    if actions:
        message['tool_calls'] = [dict(id=f'call-{number}-{i}', type='function',
            function=dict(name=name, arguments=dump(args))) for i, (name, args) in enumerate(actions)]
    return dict(choices=[dict(finish_reason='tool_calls' if actions else 'stop', message=message)])


def responses_turn(number, actions):
    return dict(id=f'response-{number}', status='completed', output=[
        dict(type='reasoning', id=f'reason-{number}', encrypted_content=f'opaque-{number}+/=',
             summary=[dict(type='summary_text', text=f'native summary {number}')],
             provider_extension={'signature': 'keep exactly', 'reasoning_content': 'nested'}),
        dict(type='message', role='assistant', id=f'message-{number}', status='completed',
             phase='commentary', content=[dict(type='output_text', text=f'text-{number}', annotations=[])]),
        *[dict(type='function_call', id=f'item-{number}-{i}', call_id=f'call-{number}-{i}',
               name=name, arguments=dump(args), status='completed')
          for i, (name, args) in enumerate(actions)]])


def sequence(body):
    return body.get('messages', body.get('input'))


def facts(body):
    last = sequence(body)[-1]
    if last.get('role') == 'tool' or last.get('type') == 'function_call_output':
        return json.loads(last.get('content', last.get('output')))['harness_runtime']
    return json.loads(last['content'].split('\n', 1)[1].removesuffix('</tool_response>'))


def receipts(body):
    return [json.loads(item.get('content', item.get('output'))) for item in sequence(body)
            if item.get('role') == 'tool' or item.get('type') == 'function_call_output']


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.serial = 0
        env = patch.dict(os.environ, {'PATH': os.environ.get('PATH', os.defpath),
            'MADS_TEST_KEY': 'local-fixture-only', 'CS2680_API_KEY': 'local-fixture-only'}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def execute(self, replies, protocol='chat', **options):
        self.serial += 1
        root = self.root/str(self.serial)
        repo = root/'repo'; repo.mkdir(parents=True)
        run = root/'run'; store = Store(run/'journal')
        bodies = []
        def handle(request):
            index = len(bodies)
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=replies[index])
        class FixtureProvider(Provider):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, transport=httpx.MockTransport(handle), **kwargs)
        config = dict(provider='fixture', protocol=protocol, model='fixture',
                      key_env='MADS_TEST_KEY', base_url='http://fixture.invalid/v1')
        # The real fork path constructs another Provider with its own store/client.
        with patch('mads.provider.Provider', FixtureProvider), FixtureProvider(
                store, config=config, retries=0) as provider:
            options.setdefault('session_role', 'reviewer')
            agent = Agent(repo, TASK, run, provider, store, **options)
            self.addCleanup(agent.tools.jobs.close)
            outcome = agent.run()
        self.assertEqual(outcome['harness_outcome']['status'], 'completed', outcome)
        self.assertEqual(outcome['cleanup_outcome']['status'], 'completed', outcome)
        self.assertEqual(len(bodies), len(replies))
        self.assert_integrity(agent, bodies)
        return agent, bodies

    def assert_integrity(self, agent, bodies):
        sent = []
        journals = [agent.store.root, *agent.run_root.glob('reviewer/journal')]
        invocations = 0
        for path in journals:
            store = Store(path)
            for row in store.events:
                if row['event'] == 'http_request':
                    self.assertTrue(row['matches_compiled_request'])
                    logical = store.record(row['request_record'])
                    manifest = store.record(logical['context_id'])
                    body = json.loads(store.verify(row['blob']).read_bytes())
                    self.assertEqual(body, json.loads(store.verify(logical['blob']).read_bytes()))
                    self.assertEqual(body, json.loads(store.verify(manifest['request_blob']).read_bytes()))
                    self.assertEqual(facts(body)['context']['reasoning_replay'], manifest['reasoning_replay'])
                    self.assertEqual(facts(body)['context']['reasoning_omission'], manifest['reasoning_omission'])
                    sent.append((row['timestamp'], body))
                elif row['event'] == 'assistant':
                    native = json.loads(store.verify(row['blob']).read_bytes())
                    response = store.record(row['response_record'])
                    original = json.loads(store.verify(response['blob']).read_bytes())
                    if row.get('protocol') == 'responses':
                        self.assertEqual(native, {'protocol': 'responses', **{k: original.get(k) for k in
                            ('id', 'status', 'output', 'incomplete_details', 'error')}})
                    else:
                        self.assertEqual(native, original['choices'][0]['message'])
                elif row['event'] == 'invocation':
                    invocations += 1
        self.assertEqual([body for _, body in sorted(sent, key=lambda item: item[0])], bodies)
        # Course rows remain literal wire presentations; repeated views are not new executions.
        rows, _ = project(agent.run_root)
        self.assertEqual(len([row for row in rows if row['event'] == 'tool_call']), invocations)
        expected = []
        for body in bodies:
            for item in sequence(body):
                if item.get('role') == 'tool':
                    expected.append(item['content'])
                elif item.get('type') == 'function_call_output':
                    expected.append(item['output'])
                elif item.get('role') == 'user' and item.get('content', '').startswith(
                        '<tool_response>REJECTED TOOL CALL'):
                    expected.append(item['content'])
        self.assertEqual([row['result'] for row in rows if row['event'] == 'tool_result'], expected)

    def assert_chat_replay(self, body, originals, kept):
        actual = [item for item in body['messages'] if item['role'] == 'assistant']
        self.assertEqual(len(actual), len(originals))
        for index, (shown, native) in enumerate(zip(actual, originals)):
            # All non-plain fields, including signed/structured provider fields, stay exact.
            self.assertEqual({k: v for k, v in shown.items() if k not in PLAIN},
                             {k: v for k, v in native.items() if k not in PLAIN})
            for field in PLAIN:
                if index in kept and field in native:
                    self.assertEqual(shown[field], native[field])
                else:
                    self.assertNotIn(field, shown)

    def test_wire_round_trip_restores_surviving_reasoning_without_reexecuting(self):
        turns = [
            chat_turn(1, [('bash', {'command': 'printf once >> counter; printf observed'})]),
            chat_turn(2, [('context_configure', {'reasoning_replay': 'none'})]),
            chat_turn(3, [('context_status', {})]),
            chat_turn(4, [('context_configure', {'reasoning_replay': 'all'}),
                          ('context_configure', {'reasoning_replay': 'latest', 'keep_recent': 100})]),
            chat_turn(5, [('context_configure', {'reasoning_replay': 'all'})]),
            chat_turn(6, [('finish', {'summary': 'fixture complete'})]),
        ]
        agent, bodies = self.execute(turns)
        raw = [turn['choices'][0]['message'] for turn in turns]
        self.assert_chat_replay(bodies[1], raw[:1], {0})  # default remains exact
        self.assert_chat_replay(bodies[2], raw[:2], set())
        self.assert_chat_replay(bodies[3], raw[:3], set())
        self.assert_chat_replay(bodies[4], raw[:4], {3})  # last staged value wins
        self.assert_chat_replay(bodies[5], raw[:5], {0, 1, 2, 3, 4})
        self.assertEqual([g['assistant'] for g in agent.context.groups], raw[:5])
        self.assertEqual((agent.tools.repo/'counter').read_text(), 'once')
        self.assertEqual(len([e for e in agent.store.events
            if e['event'] == 'invocation' and e['tool_name'] == 'bash']), 1)
        manifests = [e for e in agent.store.events if e['event'] == 'context_manifest']
        self.assertEqual([(m['reasoning_replay'], m['reasoning_omission']['fields']) for m in manifests],
                         [('all', 0), ('all', 0), ('none', 4), ('none', 6), ('latest', 6), ('all', 0)])
        for index in (2, 4, 5):
            acks = [r for r in receipts(bodies[index]) if 'applied_context' in r]
            current = acks[-2:] if index == 4 else acks[-1:]
            for result in current:
                for key, value in result['applied_context'].items():
                    self.assertEqual(value, manifests[index][key])
        status = receipts(bodies[3])[-1]
        self.assertEqual(status['reasoning_replay'], 'none')
        self.assertEqual(status['reasoning_omission'], manifests[2]['reasoning_omission'])
        self.assertEqual(status['reasoning_omission']['exchange_ids'], manifests[2]['groups'])
        self.assertEqual(agent.context.context_status()['reasoning_omission']['fields'], 0)
        first = agent.context.groups[0]['id']
        representations = [e for e in agent.store.events if e['event'] == 'exchange_representation'
                           and e['exchange_id'] == first]
        self.assertTrue(any(e['reasoning_replay'] == 'none' and e['omitted_reasoning_fields'] == list(PLAIN)
                            for e in representations))
        self.assertTrue(any(e['reasoning_replay'] == 'all' and e['messages'][0] == raw[0]
                            for e in representations))

    def test_latest_tracks_newest_exchange_after_restart_even_without_plain_fields(self):
        turns = [
            chat_turn(1, [('write', {'path': 'dropped', 'content': 'effect persists'})]),
            chat_turn(2, [('context_status', {})]),
            chat_turn(3, [('context_configure', {'reasoning_replay': 'latest'}),
                          ('context_restart', {'keep_recent_exchanges': 1, 'keep_records': []})]),
            chat_turn(4, plain=False),
            chat_turn(5, [('context_configure', {'reasoning_replay': 'all'})]),
            chat_turn(6, [('finish', {'summary': 'fixture complete'})]),
        ]
        agent, bodies = self.execute(turns, reasoning_replay='latest')
        raw = [turn['choices'][0]['message'] for turn in turns]
        self.assert_chat_replay(bodies[1], raw[:1], {0})
        self.assert_chat_replay(bodies[2], raw[:2], {1})
        self.assert_chat_replay(bodies[3], raw[1:3], {1})
        self.assert_chat_replay(bodies[4], raw[1:4], set())
        self.assert_chat_replay(bodies[5], raw[1:5], {0, 1, 2, 3})
        self.assertEqual([g['assistant'] for g in agent.context.groups], raw[1:5])
        self.assertEqual((agent.tools.repo/'dropped').read_text(), 'effect persists')
        self.assertEqual(facts(bodies[3])['context']['replay_omission']['exchanges'], 1)
        self.assertEqual(facts(bodies[4])['context']['reasoning_omission']['fields'], 4)
        # A compile without a new exchange uses the same newest retained group.
        again = agent.compile()
        self.assert_chat_replay(again, raw[1:5], {0, 1, 2, 3})

    def test_responses_items_are_exact_under_every_mode_and_changes(self):
        for mode in ('all', 'latest', 'none'):
            with self.subTest(mode=mode):
                turns = [responses_turn(1, [('write', {'path': 'data', 'content': 'value'})]),
                         responses_turn(2, [('context_configure', {'reasoning_replay': 'none'})]),
                         responses_turn(3, [('context_configure', {'reasoning_replay': 'latest'})]),
                         responses_turn(4, [('context_configure', {'reasoning_replay': 'all'})]),
                         responses_turn(5, [('finish', {'summary': 'fixture complete'})])]
                agent, bodies = self.execute(turns, protocol='responses', reasoning_replay=mode)
                for index, body in enumerate(bodies):
                    items = [item for item in body['input'] if item.get('type') in
                             ('reasoning', 'message', 'function_call')]
                    self.assertEqual(items, [item for turn in turns[:index] for item in turn['output']])
                    self.assertEqual(facts(body)['context']['reasoning_omission']['fields'], 0)
                    self.assertEqual(body['reasoning'], {'effort': 'medium', 'context': 'all_turns', 'mode': 'standard'})
                self.assertEqual([g['assistant']['responses_output'] for g in agent.context.groups],
                                 [t['output'] for t in turns[:-1]])
                from mads.inspection.audit_journal import audit
                checked = audit(agent.store.root, strict=True)
                self.assertTrue(checked['passed'], checked['errors'])

    def test_invalid_controls_keep_previous_policy_and_restart_selection_but_not_undo_effects(self):
        turns = [chat_turn(1, [('write', {'path': 'first', 'content': 'first'})]),
                 chat_turn(2, [('context_configure', {'reasoning_replay': 'latest', 'pressure_at_tokens': 999999}),
                               ('context_restart', {'account': 'account effect survives', 'keep_recent_exchanges': 0}),
                               ('write', {'path': 'second', 'content': 'second'})]),
                 chat_turn(3, [('context_configure', {'reasoning_replay': 'typo'})]),
                 chat_turn(4, [('finish', {'summary': 'fixture complete'})])]
        agent, bodies = self.execute(turns, reasoning_replay='none')
        self.assertEqual(agent.context.reasoning_replay, 'none')
        self.assertEqual(len(agent.context.groups), 3)
        rejected = receipts(bodies[2])[-3:-1]
        self.assertEqual([r['status'] for r in rejected], ['rejected', 'rejected'])
        self.assertTrue(all(r['context_change'] == 'unchanged' for r in rejected))
        self.assertTrue(all(r['applied_context']['reasoning_replay'] == 'none' for r in rejected))
        self.assertEqual(receipts(bodies[3])[-1]['status'], 'rejected')
        self.assertIn('expected one of', receipts(bodies[3])[-1]['error'])
        self.assertEqual(agent.context.working.read_text(), 'account effect survives')
        self.assertEqual((agent.tools.repo/'second').read_text(), 'second')
        self.assert_chat_replay(bodies[3], [t['choices'][0]['message'] for t in turns[:3]], set())

    def test_restoration_that_does_not_fit_is_rejected_without_reexecuting(self):
        first = chat_turn(1, [('bash', {'command': 'printf once >> counter'})])
        first['choices'][0]['message']['reasoning_content'] = 'large native reasoning ' * 6000
        turns = [first,
                 chat_turn(2, [('context_configure', {'reasoning_replay': 'all'}),
                               ('context_restart', {'account': 'surviving account', 'keep_recent_exchanges': 1}),
                               ('write', {'path': 'effect', 'content': 'survives'})]),
                 chat_turn(3, [('finish', {'summary': 'fixture complete'})])]
        # Leave room for the rich shared engineering prompt and ordinary receipts.
        # Restored reasoning alone is >50k estimated tokens, so the attempted
        # restoration must still fail; rejecting ordinary continuation would test
        # an undersized fixture, not reversible-presentation behavior.
        agent, bodies = self.execute(turns, reasoning_replay='none', input_limit=50000,
                                    total_limit=70000, output_tokens=8000, mask_at=0, pressure_at=45000)
        self.assertEqual(agent.context.reasoning_replay, 'none')
        self.assertEqual(agent.context.groups[0]['assistant'], first['choices'][0]['message'])
        for result in receipts(bodies[2])[-3:-1]:
            self.assertEqual(result['context_change'], 'unchanged')
            self.assertIn('Irreducible finalized input estimate', result['error'])
            self.assertEqual(result['applied_context']['reasoning_replay'], 'none')
        self.assertEqual((agent.tools.repo/'counter').read_text(), 'once')
        self.assertEqual((agent.tools.repo/'effect').read_text(), 'survives')
        self.assertEqual(agent.context.working.read_text(), 'surviving account')
        self.assert_chat_replay(bodies[2], [t['choices'][0]['message'] for t in turns[:2]], set())

    def test_malformed_call_projection_still_preserves_raw_history_and_valid_sibling(self):
        first = chat_turn(1, [('write', {'path': 'invalid', 'content': 'must not happen'}),
                             ('bash', {'command': 'printf once >> counter'})])
        calls = first['choices'][0]['message']['tool_calls']
        calls[0]['function']['arguments'] = '{'
        turns = [first, chat_turn(2, [('context_configure', {'reasoning_replay': 'all'})]),
                 chat_turn(3, [('finish', {'summary': 'fixture complete'})])]
        agent, bodies = self.execute(turns, reasoning_replay='none')
        for body in bodies[1:]:
            assistant = next(item for item in body['messages'] if item['role'] == 'assistant')
            self.assertEqual(assistant['tool_calls'], calls[1:])
            historical = next(item['content'] for item in body['messages'] if item.get('content', '').startswith(
                '<tool_response>REJECTED TOOL CALL'))
            packet = json.loads(historical.split('\n', 1)[1].removesuffix('</tool_response>'))
            self.assertEqual(packet['tool_call'], calls[0])
            self.assertEqual(packet['result']['effects'], 'none')
        self.assertNotIn('reasoning_content', bodies[1]['messages'][2])
        self.assertEqual(bodies[2]['messages'][2]['reasoning_content'], first['choices'][0]['message']['reasoning_content'])
        self.assertEqual(agent.context.groups[0]['assistant'], first['choices'][0]['message'])
        self.assertFalse((agent.tools.repo/'invalid').exists())
        self.assertEqual((agent.tools.repo/'counter').read_text(), 'once')

    def test_fresh_reviewer_inherits_committed_not_initial_policy(self):
        for initial, selected in (('all', 'none'), ('none', 'latest'), ('latest', 'all')):
            with self.subTest(initial=initial, selected=selected):
                turns = [chat_turn(1, [('context_configure', {'reasoning_replay': selected})]),
                         chat_turn(2, [('finish', {'summary': 'Read fixture only.'})]),
                         chat_turn(3, [('write', {'path': 'child', 'content': 'child effect'})]),
                         chat_turn(4, [('read', {'path': 'child'})]),
                         chat_turn(5, [('finish', {'summary': 'child fixture complete'})])]
                agent, bodies = self.execute(turns, reasoning_replay=initial, session_role='solver')
                self.assertEqual(agent.context.reasoning_replay, selected)
                for body in bodies[2:5]:
                    self.assertEqual(facts(body)['context']['reasoning_replay'], selected)
                self.assertFalse(any(m['role'] == 'assistant' for m in bodies[2]['messages']))
                self.assert_chat_replay(bodies[4], [turns[2]['choices'][0]['message'], turns[3]['choices'][0]['message']],
                                        {'all': {0, 1}, 'latest': {1}, 'none': set()}[selected])
                self.assertEqual((agent.tools.repo/'child').read_text(), 'child effect')
                child = Store(next(agent.run_root.glob('reviewer/journal')))
                self.assertTrue(all(e['reasoning_replay'] == selected for e in child.events
                                    if e['event'] == 'context_manifest'))

    def test_exact_handoff_evidence_is_not_filtered_and_policy_is_a_new_launch_option(self):
        turns = [chat_turn(1, [('context_status', {})]),
                 chat_turn(2, [('finish', {'summary': 'source fixture complete'})])]
        source, _ = self.execute(turns, reasoning_replay='none')
        record = next(e['id'] for e in source.store.events if e['event'] == 'assistant')
        task = self.root/'task.txt'; task.write_text(TASK)
        account = self.root/'account.md'; account.write_text('fallible handoff account')
        bundle = self.root/'bundle'
        export_bundle(source.run_root, bundle, task, account, [{'record_id': record}])
        for index, selected in enumerate((None, 'none', 'latest')):
            run = self.root/f'seed-{index}'
            store = Store(run/'journal')
            class Route:
                config = dict(model='fixture')
                def request_options(self): return {}
            options = {} if selected is None else {'reasoning_replay': selected}
            agent = Agent(source.tools.repo, TASK, run, Route(), store, **options, session_role='reviewer')
            self.addCleanup(agent.tools.jobs.close)
            imported = agent.seed_handoff(bundle)
            self.assertEqual(agent.context.reasoning_replay, selected or 'all')
            self.assertEqual(agent.context.groups, [])
            body = agent.compile()
            evidence = next(item['content'] for item in body['messages'] if item.get('content', '').startswith(
                '<tool_response>EXACT JOURNAL EVIDENCE'))
            packet = json.loads(evidence.split('\n', 1)[1].removesuffix('</tool_response>'))
            self.assertEqual(json.loads(''.join(packet['text_parts'])), turns[0]['choices'][0]['message'])
            self.assertEqual(agent.context.records, imported['selections'])

    def test_context_scope_and_validation_without_a_provider(self):
        store = Store(self.root/'journal')
        for value in ('bad', '', True, 1, None, []):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'reasoning_replay'):
                Context(store, self.root/'memory-invalid', reasoning_replay=value)
        for value in ('all', 'latest', 'none'):
            validate('context_configure', {'reasoning_replay': value})
        for value in ('bad', '', True, 1, None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate('context_configure', {'reasoning_replay': value})
        context = Context(store, self.root/'memory', reasoning_replay='none')
        # Literal compiler fixture: only strings are eligible. Agent validation
        # separately rejects non-text Chat fields before tool execution.
        assistant = dict(role='assistant', content='unchanged', reasoning={'opaque': 'not plain text'},
                         reasoning_content=None, reasoning_details=[{'signature': 'unchanged'}])
        group = dict(id=store.add('fixture'), assistant=copy.deepcopy(assistant), results=[], fresh=True)
        body = context.compile('system', TASK, [], 'fixture', {}, group)
        self.assertEqual(body['messages'][2], assistant)
        self.assertEqual(group['assistant'], assistant)
        self.assertEqual(context.context_status()['reasoning_omission']['fields'], 0)
        with self.assertRaises(ValueError):
            context._validate_config({'reasoning_replay': 'invalid'})
        self.assertEqual(context.reasoning_replay, 'none')

    def test_cli_and_agent_environment_defaults_override_and_validation(self):
        repo = self.root/'repo'; repo.mkdir()
        for index, (env, flags, selected) in enumerate([
                ({}, [], 'all'), ({'MADS_REASONING_REPLAY': 'latest'}, [], 'latest'),
                ({'MADS_REASONING_REPLAY': 'none'}, ['--reasoning-replay', 'all'], 'all'),
                ({'MADS_REASONING_REPLAY': 'invalid'}, ['--reasoning-replay', 'none'], 'none')]):
            bodies = []
            def handle(request):
                bodies.append(json.loads(request.content))
                return httpx.Response(200, json=chat_turn(1, [('finish', {'summary': 'CLI fixture'})]))
            def provider(store, **kwargs):
                return Provider(store, transport=httpx.MockTransport(handle), **kwargs)
            with patch.dict(os.environ, env), patch('mads.agent.Provider', side_effect=provider), patch('mads.provider.Provider', side_effect=provider), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = agent_main([str(repo), '-p', TASK, '--run-dir', str(self.root/f'cli-{index}'), *flags])
            self.assertEqual(result, 0)
            self.assertEqual(facts(bodies[0])['context']['reasoning_replay'], selected)
        for env, flags in [({'MADS_REASONING_REPLAY': 'invalid'}, []),
                           ({'MADS_REASONING_REPLAY': ''}, []), ({}, ['--reasoning-replay', 'invalid'])]:
            run = self.root/'must-not-start'
            with patch.dict(os.environ, env), patch('mads.agent.Provider') as provider, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                agent_main([str(repo), '-p', TASK, '--run-dir', str(run), '--dry-run', *flags])
            self.assertEqual(exc.exception.code, 2)
            provider.assert_not_called()
            self.assertFalse(run.exists())
        with patch.dict(os.environ, {'MADS_REASONING_REPLAY': 'none'}):
            agent, bodies = self.execute([chat_turn(1, [('finish', {'summary': 'env fixture'})])])
            self.assertEqual(agent.context.reasoning_replay, 'none')
            self.assertEqual(facts(bodies[0])['context']['reasoning_replay'], 'none')

    def test_assignment_runner_forwards_environment_name(self):
        from mads.runner import run
        checkout = self.root/'checkout'; checkout.mkdir()
        source = Path(__file__).resolve().parents[1]
        from mads.runner import stage_source
        stage_source(source, checkout)
        tasks = checkout/'tasks.json'
        tasks.write_text(dump({'fixture': dict(instance_id='fixture', base_commit='abc', docker_image='fixture')}))
        with patch.dict(os.environ, {'MADSLOOP_REPO': str(checkout), 'TASKS_JSON': str(tasks),
                                     'MADS_REASONING_REPLAY': 'latest'}), \
                patch('mads.runner.subprocess.call', return_value=1) as launch, \
                patch('mads.runner.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stderr='')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(0), 1)  # No container or patch was produced by this fixture.
        args = launch.call_args.args[0]
        self.assertEqual(args[args.index('MADS_REASONING_REPLAY')-1], '-e')
        self.assertNotIn('MADS_REASONING_REPLAY=latest', args)


if __name__ == '__main__':
    unittest.main()
