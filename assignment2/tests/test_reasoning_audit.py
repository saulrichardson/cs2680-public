"""Independent audit of real compiler/wire replay, including semantically forged journals.

Mutations rehash their blobs and update serialization/telemetry. Rejection must
therefore challenge the declared projection/source, not merely an old checksum.
These fixtures do not establish hosted-provider acceptance or task-solving gains.
"""
import copy
import hashlib
import json
import math
import shutil
import unittest

from mads.inspection.audit_journal import audit, encoded
from tests import test_reasoning_replay as fixtures


class ReasoningAuditTests(unittest.TestCase):
    setUp = fixtures.ReplayTests.setUp
    execute = fixtures.ReplayTests.execute
    assert_integrity = fixtures.ReplayTests.assert_integrity

    def accepted(self, agent):
        result = audit(agent.store.root, strict=True)
        self.assertTrue(result['passed'], result['errors'])
        return result

    def basic(self, mode='all', plain_last=True, protocol='chat'):
        if protocol == 'responses':
            replies = [fixtures.responses_turn(1, []), fixtures.responses_turn(2, []),
                       fixtures.responses_turn(3, [('finish', {'summary': 'fixture'})])]
        else:
            replies = [fixtures.chat_turn(1), fixtures.chat_turn(2, plain=plain_last),
                       fixtures.chat_turn(3, [('finish', {'summary': 'fixture'})])]
        agent, _ = self.execute(replies, protocol=protocol, reasoning_replay=mode)
        self.accepted(agent)
        return agent

    def clone(self, agent, label):
        journal = self.root / ('mutated-' + label) / 'journal'
        shutil.copytree(agent.store.root, journal)
        events = [json.loads(line) for line in (journal / 'events.jsonl').read_text().splitlines()]
        return journal, events

    def save(self, journal, events):
        (journal / 'events.jsonl').write_bytes(b''.join(encoded(event) + b'\n' for event in events))

    def put(self, journal, value):
        raw = encoded(value)
        digest = hashlib.sha256(raw).hexdigest()
        (journal / 'blobs' / digest).write_bytes(raw)
        return digest

    def rewrite(self, journal, events, manifest, mutate=lambda body, metadata: None):
        """Change a finalized body and all its logical/HTTP links consistently."""
        body = json.loads((journal / 'blobs' / manifest['request_blob']).read_bytes())
        mutate(body, manifest)
        sequence = body.get('messages', body.get('input'))
        tail = sequence[-1]
        self.assertEqual(tail.get('role'), 'user')  # these mutation fixtures have no tool tail
        facts = json.loads(tail['content'].split('\n', 1)[1].removesuffix('</tool_response>'))
        for key in ('reasoning_replay', 'reasoning_omission'):
            if key in manifest:
                facts['context'][key] = copy.deepcopy(manifest[key])
            else:
                facts['context'].pop(key, None)
        for key in ('estimated_tokens_before_telemetry', 'estimate_scope',
                    'continuation_headroom_estimate_tokens'):
            facts['context'].pop(key, None)
        facts.pop('notice', None)
        def encode_tail():
            tail['content'] = '<tool_response>CURRENT HARNESS FACTS\n' + encoded(facts).decode() + '</tool_response>'
        encode_tail()
        estimate = math.ceil(len(encoded(body)) * manifest['tokens_per_byte'])
        facts['context'].update(estimated_tokens_before_telemetry=estimate,
            estimate_scope='compiled request before estimate, continuation-headroom fields, and pressure notice',
            continuation_headroom_estimate_tokens=manifest['effective_input_limit_tokens'] - estimate)
        encode_tail()
        manifest['bytes'] = len(encoded(body))
        manifest['estimated_tokens'] = math.ceil(manifest['bytes'] * manifest['tokens_per_byte'])
        manifest['request_blob'] = self.put(journal, body)
        request_ids = set()
        for event in events:
            if event['event'] == 'request' and event.get('context_id') == manifest['id']:
                event['blob'] = manifest['request_blob']
                request_ids.add(event['id'])
        wire = copy.deepcopy(body)
        wire.update(wire.pop('extra_body', {}))
        for event in events:
            if event['event'] == 'http_request' and event.get('request_record') in request_ids:
                event['blob'] = self.put(journal, wire)
        return body

    def rejected(self, journal, events, expected):
        self.save(journal, events)
        result = audit(journal, strict=True)
        self.assertFalse(result['passed'])
        self.assertTrue(any(expected in error['error'] for error in result['errors']), result['errors'])
        # The attack has internally consistent bytes and telemetry. A hash-only
        # rejection would not test the intended reasoning/source invariant.
        mechanical = ('hash mismatch', 'serialized length', 'estimate differs',
                      'pre-telemetry estimate', 'differs from finalized compiler blob',
                      'Actual HTTP body differs')
        self.assertFalse(any(any(text in error['error'] for text in mechanical)
                             for error in result['errors']), result['errors'])
        return result

    def test_all_modes_native_and_newest_without_plain_fields(self):
        for protocol in ('chat', 'responses'):
            for mode in ('all', 'latest', 'none'):
                with self.subTest(protocol=protocol, mode=mode):
                    agent = self.basic(mode, plain_last=False, protocol=protocol)
                    result = self.accepted(agent)
                    self.assertGreater(result['counts']['reasoning_omission_source_checks'], 0)
                    if protocol == 'chat':
                        self.assertGreater(result['counts']['selected_chat_source_checks'], 0)

    def test_staging_restore_restart_and_malformed_siblings_audit(self):
        first = fixtures.chat_turn(1, [('write', {'path': 'bad', 'content': 'not executed'}),
                                       ('write', {'path': 'good', 'content': 'executed'})])
        first['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = '{'
        replies = [first,
            fixtures.chat_turn(2, [('context_configure', {'reasoning_replay': 'none'})]),
            fixtures.chat_turn(3, [('context_configure', {'reasoning_replay': 'latest'}),
                                   ('context_restart', {'keep_recent_exchanges': 1})]),
            fixtures.chat_turn(4, plain=False),
            fixtures.chat_turn(5, [('context_configure', {'reasoning_replay': 'all'})]),
            fixtures.chat_turn(6, [('finish', {'summary': 'fixture'})])]
        agent, _ = self.execute(replies)
        result = self.accepted(agent)
        self.assertGreater(result['counts']['explicit_malformed_call_projections'], 0)
        self.assertFalse((agent.tools.repo / 'bad').exists())
        self.assertEqual((agent.tools.repo / 'good').read_text(), 'executed')

    def test_well_hashed_unauthorized_omission_mutation_and_source_substitution(self):
        agent = self.basic('all')
        def assistants(body):
            return [item for item in body['messages'] if item['role'] == 'assistant']
        mutations = {
            'remove-reasoning': lambda b, m: assistants(b)[0].pop('reasoning_content'),
            'alter-reasoning': lambda b, m: assistants(b)[0].update(reasoning='invented'),
            'remove-content': lambda b, m: assistants(b)[0].pop('content'),
            'alter-structured': lambda b, m: assistants(b)[0]['reasoning_details'][0].update(data='invented'),
            'alter-calls': lambda b, m: assistants(b)[0].update(tool_calls=[]),
            # Both raw messages really exist earlier. An "any earlier native"
            # match would incorrectly accept this replacement of selected source.
            'duplicate-prior-source': lambda b, m: assistants(b)[1].update(copy.deepcopy(assistants(b)[0])),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                journal, events = self.clone(agent, label)
                manifest = [e for e in events if e['event'] == 'context_manifest'][-1]
                self.rewrite(journal, events, manifest, mutate)
                self.rejected(journal, events, 'Chat replay differs from selected native source')

    def test_wrong_policy_and_omission_metadata_not_just_hash(self):
        agent = self.basic('latest', plain_last=False)
        def wrong_policy(body, manifest):
            manifest['reasoning_replay'] = 'all'
            manifest['reasoning_omission'].update(fields=0, exchanges=0, exchange_ids=[])
        mutations = {
            'wrong-policy': (wrong_policy, 'Chat replay differs from selected native source'),
            'unknown-policy': (lambda b, m: m.update(reasoning_replay='typo'), 'Unknown reasoning replay policy'),
            'false-field-count': (lambda b, m: m['reasoning_omission'].update(fields=1), 'Reasoning omission metadata differs'),
            'false-exchange-count': (lambda b, m: m['reasoning_omission'].update(exchanges=0), 'Reasoning omission metadata differs'),
            'newest-eligibility': (lambda b, m: m['reasoning_omission'].update(exchange_ids=[m['groups'][-1]]), 'Reasoning omission metadata differs'),
            'wrong-scope': (lambda b, m: m['reasoning_omission'].update(scope='all provider reasoning'), 'Malformed reasoning omission metadata'),
            'missing-declaration': (lambda b, m: m.pop('reasoning_replay'), 'must be declared together'),
        }
        for label, (mutate, expected) in mutations.items():
            with self.subTest(label=label):
                journal, events = self.clone(agent, label)
                manifest = [e for e in events if e['event'] == 'context_manifest'][-1]
                self.rewrite(journal, events, manifest, mutate)
                self.rejected(journal, events, expected)

    def test_representation_metadata_and_source_are_checked(self):
        agent = self.basic('none')
        mutations = {
            'representation-policy': lambda r: r.update(reasoning_replay='all'),
            'representation-fields': lambda r: r.update(omitted_reasoning_fields=['reasoning_details']),
            'representation-content': lambda r: r['messages'][0].update(content='invented'),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                journal, events = self.clone(agent, label)
                record = next(e for e in events if e['event'] == 'exchange' and 'reasoning_replay' in e)
                mutate(record)
                expected = 'Chat replay differs' if label.endswith('content') else 'Representation'
                self.rejected(journal, events, expected)

    def test_responses_native_reasoning_not_authorized_for_omission(self):
        agent = self.basic('none', protocol='responses')
        journal, events = self.clone(agent, 'responses-native')
        manifest = [e for e in events if e['event'] == 'context_manifest'][-1]
        def mutate(body, metadata):
            item = next(item for item in body['input'] if item.get('type') == 'reasoning')
            item.pop('encrypted_content')
        self.rewrite(journal, events, manifest, mutate)
        self.rejected(journal, events, 'Responses replay changes selected native items')

    def test_old_journal_without_policy_is_all_not_permission_to_omit(self):
        agent = self.basic('all')
        journal, events = self.clone(agent, 'old')
        for event in events:
            if event['event'] in ('exchange', 'exchange_representation', 'context_manifest'):
                event.pop('reasoning_replay', None)
                event.pop('omitted_reasoning_fields', None)
                event.pop('reasoning_omission', None)
            if event['event'] == 'context_manifest':
                self.rewrite(journal, events, event)
        self.save(journal, events)
        checked = audit(journal, strict=True)
        self.assertTrue(checked['passed'], checked['errors'])
        manifest = [e for e in events if e['event'] == 'context_manifest'][-1]
        self.rewrite(journal, events, manifest, lambda body, m: next(
            item for item in body['messages'] if item['role'] == 'assistant').pop('reasoning'))
        self.rejected(journal, events, 'Chat replay differs from selected native source')

    def test_literal_fixture_limitation_is_not_native_attestation(self):
        agent = self.basic('all')
        journal, events = self.clone(agent, 'literal-fixture')
        # Deliberately emulate an old compiler-only fixture with no assistant
        # records, not a real solver trace claimed to have independent sources.
        for event in events:
            if event['event'] == 'assistant':
                event['event'] = 'literal_fixture'
        self.save(journal, events)
        checked = audit(journal, strict=True)
        self.assertTrue(checked['passed'], checked['errors'])
        self.assertEqual(checked['counts'].get('selected_chat_source_checks', 0), 0)
        self.assertIn('Literal compiler fixtures without assistant source records cannot establish native replay fidelity',
                      checked['limitations'])


if __name__ == '__main__':
    unittest.main()
