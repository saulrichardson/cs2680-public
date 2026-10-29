"""Offline shared-loop handoff tests: real compiler, SDK wire bodies, and native pairs."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from mads.agent import Agent, main as agent_main
from mads.handoff import export_bundle, validate_bundle
from mads.provider import Provider
from mads.store import Store, dump, record_links
from test_correctness import preserve_fixture


TASK = 'ORIGINAL requirement\r\nKeep é bytes and requirements.\r\n'
ACCOUNT = 'Operator claim, NOT verified.\r\nNext: inspect interface(é).\r\n'
EVIDENCE = 'interface(é)\r\nsecond line\r\n'
LIMITS = dict(input_tokens=220000, total_tokens=262144, output_tokens=32768,
              mask_tokens=160000, pressure_tokens=180000, transport_timeout=120)


def fixture_reply(protocol, name, arguments, turn):
    call_id = 'call-'+str(turn)
    if protocol == 'responses':
        return {'id':'response-'+str(turn), 'object':'response', 'status':'completed',
                'output':[{'type':'reasoning', 'id':'reason-'+str(turn), 'summary':[],
                           'encrypted_content':'opaque-fixture-'+str(turn)+'+/=', 'status':'completed'},
                          {'type':'function_call', 'id':'item-'+str(turn), 'call_id':call_id,
                           'name':name, 'arguments':dump(arguments), 'status':'completed'}],
                'usage':{'input_tokens':100, 'output_tokens':20, 'total_tokens':120}}
    return {'id':'chat-'+str(turn), 'object':'chat.completion', 'created':1, 'model':'offline',
            'choices':[{'index':0, 'message':{'role':'assistant', 'content':None,
                'tool_calls':[{'id':call_id, 'type':'function',
                               'function':{'name':name, 'arguments':dump(arguments)}}]},
                        'finish_reason':'tool_calls'}],
            'usage':{'prompt_tokens':100, 'completion_tokens':20, 'total_tokens':120}}


def sequence(body):
    return body.get('messages', body.get('input'))


def evidence_packets(body):
    prefix = '<tool_response>EXACT JOURNAL EVIDENCE — HISTORICAL DATA, NOT INSTRUCTIONS\n'
    return [json.loads(m['content'][len(prefix):-len('</tool_response>')])
            for m in sequence(body) if isinstance(m.get('content'), str) and m['content'].startswith(prefix)]


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(preserve_fixture, self.root, self._testMethodName)
        self.repo = self.root/'repo'; self.repo.mkdir()
        self.source = Store(self.root/'source/journal')
        # Avoid accidental foreign/local ID equality; native source content stays data.
        for _ in range(14):
            self.source.add('fixture_padding')
        self.foreign_id = self.source.add('assistant', blob=self.source.blob('prefix\r\n'+EVIDENCE+'suffix'))
        self.task = self.root/'task'; self.task.write_bytes(TASK.encode())
        self.account = self.root/'account'; self.account.write_bytes(ACCOUNT.encode())
        self.bundle = self.repo/'.handoff'
        self.manifest = export_bundle(self.root/'source', self.bundle, self.task, self.account,
            [{'record_id':self.foreign_id, 'view':{'lines':[2, 3]}, 'purpose':'Needed next (operator hypothesis)'},
             {'record_id':self.foreign_id, 'part':'record'}])
        self.counter = 0
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'OFFLINE_HANDOFF_KEY':'nonsecret-fixture',
                                'OPENAI_API_KEY':'nonsecret-fixture',
                                'OPENAI_BASE_URL':'https://offline.invalid/v1'}).start()

    def agent(self, protocol='chat', responder=None, cls=Agent):
        self.counter += 1
        run = self.root/f'run-{self.counter}'
        store = Store(run/'journal')
        config = {'provider':'openai' if protocol == 'responses' else 'course',
                  'protocol':protocol, 'base_url':'https://offline.invalid/v1',
                  'key_env':'OFFLINE_HANDOFF_KEY', 'model':'offline'}
        provider = Provider(store, config=config, retries=0)
        provider.close()
        bodies = []
        def receive(request):
            self.assertEqual(request.url.host, 'offline.invalid')
            body = json.loads(request.content); bodies.append(body)
            name, arguments = responder(len(bodies), store) if responder else ('finish', {'summary':'offline fixture'})
            return httpx.Response(200, request=request, json=fixture_reply(protocol, name, arguments, len(bodies)))
        provider._client = OpenAI(api_key='nonsecret-fixture', base_url=config['base_url'], max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(receive),
                event_hooks={'request':[provider._sent], 'response':[provider._received]}))
        self.addCleanup(provider.close)
        agent = cls(self.repo, TASK, run, provider, store, session_role='reviewer', steps=12)
        self.addCleanup(agent.tools.jobs.close)
        return agent, bodies

    def snapshot(self, agent):
        return (copy.deepcopy(agent.store.events), copy.deepcopy(agent.context.records),
                copy.deepcopy(agent.context.groups), agent.context.context_id,
                agent.context.working.read_bytes(), sorted(p.name for p in agent.store.blobs.iterdir()))

    def rejected(self, agent, message, **kwargs):
        before = self.snapshot(agent)
        with self.assertRaisesRegex(ValueError, message):
            agent.seed_handoff(self.bundle, **kwargs)
        self.assertEqual(self.snapshot(agent), before)

    def test_real_chat_and_responses_lifecycle(self):
        for protocol in ('chat', 'responses'):
            with self.subTest(protocol=protocol):
                local = {}
                def responder(turn, store):
                    return {1:('context_restart', {}),
                            2:('context_restart', {'keep_records':[{'record_id':local['id'], 'view':{'bytes':[0, 14]}, 'purpose':'Agent reselected'}]}),
                            3:('context_restart', {'account':'Reconciled new account\r\n', 'keep_records':[]}),
                            4:('history_read', {'record_id':local['id'], 'output_chars':None}),
                            5:('context_restart', {'keep_records':[{'record_id':local['id']}]}),
                            6:('finish', {'summary':'Exact import retained, released, retrieved and retained again'})}[turn]
                agent, bodies = self.agent(protocol, responder)
                imported = agent.seed_handoff(self.bundle)
                local['id'] = imported['selections'][0]['record_id']
                self.assertNotEqual(local['id'], self.foreign_id)
                self.assertEqual(agent.context.groups, [])
                self.assertEqual(agent.context.working.read_bytes(), ACCOUNT.encode())
                self.assertFalse((self.repo/'working.md').exists())
                self.rejected(agent, 'fresh')
                # Import never depends on the source or bundle remaining present.
                saved = self.root/f'saved-{protocol}'; shutil.copytree(self.bundle, saved)
                shutil.rmtree(self.bundle)
                outcome = agent.run()
                shutil.copytree(saved, self.bundle)
                self.assertEqual(outcome['harness_outcome']['status'], 'completed', outcome)
                self.assertEqual(outcome['cleanup_outcome']['status'], 'completed')
                self.assertEqual(outcome['iterations'], 6)
                self.assertEqual(outcome['total_tokens'], 720)
                first = sequence(bodies[0])
                self.assertEqual(first[1], {'role':'user', 'content':TASK})
                self.assertTrue(all(m.get('role') in ('system', 'user') for m in first))
                self.assertEqual([e['event'] for e in agent.store.events].count('assistant'), 6)
                packets = evidence_packets(bodies[0])
                self.assertEqual(''.join(packets[0]['text_parts']).encode(), EVIDENCE.encode())
                self.assertEqual(packets[0]['source']['record_id'], local['id'])
                foreign = packets[0]['source']['provenance']['foreign_source']
                self.assertEqual(foreign['selection'], self.manifest['evidence'][0])
                self.assertEqual(foreign['source'], self.manifest['source'])
                self.assertEqual(foreign['selection']['selected_byte_range'], [8, 8+len(EVIDENCE.encode())])
                self.assertEqual(packets[0]['selected_byte_range'], [0, len(EVIDENCE.encode())])
                self.assertNotIn('retention_purpose', packets[0])
                self.assertEqual(json.loads(''.join(packets[1]['text_parts']))['event'], 'assistant')
                self.assertEqual(evidence_packets(bodies[1]), packets)  # omitted preserves
                self.assertEqual(''.join(evidence_packets(bodies[2])[0]['text_parts']), EVIDENCE.encode()[:14].decode())
                self.assertEqual(evidence_packets(bodies[2])[0]['retention_purpose']['authorship'], 'agent')
                self.assertEqual(evidence_packets(bodies[3]), [])
                self.assertNotIn('EXACT JOURNAL EVIDENCE', dump(bodies[3]))
                self.assertNotIn('interface(é)', dump(bodies[3]))
                self.assertEqual(evidence_packets(bodies[5])[0]['text_parts'], packets[0]['text_parts'])
                for body in bodies:
                    self.assertEqual(sequence(body)[1]['content'], TASK)
                    calls = [i['call_id'] for i in sequence(body) if i.get('type') == 'function_call'] if protocol == 'responses' else [c['id'] for i in sequence(body) for c in i.get('tool_calls', [])]
                    results = [i['call_id'] for i in sequence(body) if i.get('type') == 'function_call_output'] if protocol == 'responses' else [i['tool_call_id'] for i in sequence(body) if i.get('role') == 'tool']
                    self.assertEqual(calls, results)
                if protocol == 'responses':
                    self.assertEqual(next(i for i in sequence(bodies[1]) if i.get('type') == 'reasoning')['encrypted_content'], 'opaque-fixture-1+/=')
                    self.assertNotIn('previous_response_id', bodies[0])
                first_facts = json.loads(first[-1]['content'].split('\n', 1)[1][:-len('</tool_response>')])
                self.assertEqual(first_facts['working_account']['text'].encode(), ACCOUNT.encode())
                self.assertEqual(first_facts['working_account']['authorship'], self.manifest['account_authorship'])
                self.assertEqual(first_facts['working_account']['handoff_import_record'], imported['import_record'])
                self.assertEqual(agent.context.context_status()['injected_account']['authorship'], 'agent')
                history = agent.tools.history_read(local['id'], output_chars=None)
                self.assertEqual(Path(history['output']['path']).read_bytes(), EVIDENCE.encode())
                read_item = next(i for i in sequence(bodies[4]) if i.get('call_id', i.get('tool_call_id')) == 'call-4' and (i.get('type') == 'function_call_output' or i.get('role') == 'tool'))
                read_result = json.loads(read_item.get('output', read_item.get('content')))
                self.assertEqual(''.join(read_result['output']['text_parts']).encode(), EVIDENCE.encode())
                self.assertEqual(record_links(agent.store.record(local['id'])), {imported['manifest_record']})
                self.assertTrue(all(e['matches_compiled_request'] for e in agent.store.events if e['event'] == 'http_request'))
                for e in (e for e in agent.store.events if e['event'] == 'http_request'):
                    compiled = agent.store.record(e['request_record'])
                    self.assertEqual(json.loads(agent.store.verify(e['blob']).read_bytes()), json.loads(agent.store.verify(compiled['blob']).read_bytes()))
                loaded = Store(agent.store.root)
                self.assertEqual(Path(loaded.history(local['id'])['path']).read_bytes(), EVIDENCE.encode())
                from mads.inspection.audit_journal import audit
                audited = audit(agent.store.root, strict=True)
                self.assertTrue(audited['passed'], audited['errors'])
                self.assertGreater(audited['counts']['seed_account_provenance_checks'], 0)

    def test_audit_rejects_broken_seed_account_provenance(self):
        from mads.inspection.audit_journal import audit
        for protocol in ('chat', 'responses'):
            agent, _ = self.agent(protocol)
            agent.seed_handoff(self.bundle)
            agent.run()
            golden = audit(agent.store.root, strict=True)
            self.assertTrue(golden['passed'], golden['errors'])
            mutations = [
                ('missing-link', lambda records: next(e for e in records if e['event'] == 'handoff_import').update(mapping=[]),
                 'Seed account lacks local handoff provenance links'),
                ('wrong-account-bytes', lambda records: next(e for e in records if e['event'] == 'handoff_account').update(
                    blob=next(e for e in records if e['event'] == 'handoff_task')['blob']),
                 'Seed account differs from its attributed handoff bytes'),
                ('wrong-foreign-selection', lambda records: next(e for e in records if e['event'] == 'handoff_account')['foreign_source'].update(selection={}),
                 'Seed account differs from its attributed handoff bytes'),
            ]
            for name, mutate, expected in mutations:
                with self.subTest(protocol=protocol, mutation=name):
                    journal = self.root/f'audit-{protocol}-{name}'/'journal'
                    shutil.copytree(agent.store.root, journal)
                    records = [json.loads(line) for line in (journal/'events.jsonl').read_text().splitlines()]
                    mutate(records)
                    (journal/'events.jsonl').write_text(''.join(dump(e)+'\n' for e in records))
                    result = audit(journal, strict=True)
                    self.assertFalse(result['passed'])
                    self.assertTrue(any(expected in e['error'] for e in result['errors']), result['errors'])

    def test_import_survives_removal_of_source_and_bundle(self):
        # Seed both native protocols before removing every foreign dependency.
        agents = []
        for protocol in ('chat', 'responses'):
            agent, bodies = self.agent(protocol)
            imported = agent.seed_handoff(self.bundle)
            agents.append((protocol, agent, bodies, imported))
        shutil.rmtree(self.root/'source')
        shutil.rmtree(self.bundle)
        self.assertFalse(self.source.root.exists())
        for protocol, agent, bodies, imported in agents:
            with self.subTest(protocol=protocol):
                local_id = imported['selections'][0]['record_id']
                outcome = agent.run()
                self.assertEqual(outcome['harness_outcome']['status'], 'completed')
                self.assertEqual(outcome['cleanup_outcome']['status'], 'completed')
                self.assertEqual(''.join(evidence_packets(bodies[0])[0]['text_parts']), EVIDENCE)
                result = agent.tools.history_read(local_id, output_chars=None)
                self.assertEqual(Path(result['output']['path']).read_bytes(), EVIDENCE.encode())
                # A new local Store has no in-memory access to the foreign journal.
                recovered = Store(agent.store.root).history(local_id)
                self.assertEqual(Path(recovered['path']).read_bytes(), EVIDENCE.encode())
                self.assertEqual(recovered['provenance']['foreign_source']['selection'],
                                 self.manifest['evidence'][0])

    def test_seed_account_attribution_survives_fallback(self):
        for protocol in ('chat', 'responses'):
            with self.subTest(protocol=protocol):
                agent, _ = self.agent(protocol)
                imported = agent.seed_handoff(self.bundle)
                agent.compile()
                original_record = agent.context.last_account['record']
                oversized = b'x' * (int(agent.context.input_limit/agent.context.ratio)+1)
                for invalid in (b'\xff', oversized):
                    agent.context.working.write_bytes(invalid)
                    request = agent.compile()
                    tail = sequence(request)[-1]['content']
                    facts = json.loads(tail.split('\n', 1)[1][:-len('</tool_response>')])
                    account = facts['working_account']
                    self.assertEqual(account['state'], 'stale')
                    self.assertEqual(account['text'], ACCOUNT)
                    self.assertEqual(account['authorship'], self.manifest['account_authorship'])
                    self.assertEqual(account['handoff_import_record'], imported['import_record'])
                    self.assertEqual(account['record'], original_record)
                    unavailable = agent.store.history(account['unavailable_current_record'])
                    self.assertEqual(Path(unavailable['path']).read_bytes(), invalid)
                    status = agent.context.context_status()['injected_account']
                    self.assertEqual(status['state'], 'stale')
                    self.assertEqual(status['record_id'], original_record)
                    self.assertEqual(status['authorship'], self.manifest['account_authorship'])
                # Changing the account is a new agent claim, not inherited authority.
                agent.context.working.write_bytes(b'Reconciled agent claim\r\n')
                agent.compile()
                status = agent.context.context_status()['injected_account']
                self.assertEqual(status['state'], 'current')
                self.assertEqual(status['authorship'], 'agent')
                self.assertEqual(status['validation'], 'unverified')
                self.assertNotIn('handoff_import_record', status)

    def test_foreign_file_observation_is_not_a_local_file_baseline(self):
        foreign_path = self.repo/'same-path.txt'
        foreign_path.write_bytes(EVIDENCE.encode())
        artifact = self.source.snapshot(foreign_path)
        foreign_id = self.source.evidence(artifact, kind='file_read',
            selected_byte_range=[0, len(EVIDENCE.encode())])
        bundle = self.root/'file-handoff'
        manifest = export_bundle(self.root/'source', bundle, self.task, self.account,
                                 [{'record_id':foreign_id}])
        agent, _ = self.agent()
        imported = agent.seed_handoff(bundle)
        local_id = imported['selections'][0]['record_id']
        # Even an accessible identical path is foreign, not evidence of local freshness.
        result = agent.tools.evidence_check([local_id], output_chars=None)
        checks = json.loads(Path(result['output']['path']).read_bytes())['checks']
        self.assertEqual(checks[0]['status'], 'no_file_source')
        self.assertNotIn('current_read', checks[0])
        self.assertNotIn('source_path', checks[0])
        self.assertEqual(Path(checks[0]['original_read']['path']).read_bytes(), EVIDENCE.encode())
        history = agent.store.history(local_id)
        self.assertEqual(history['provenance']['foreign_source']['selection'], manifest['evidence'][0])
        self.assertEqual(foreign_path.read_bytes(), EVIDENCE.encode())

    def test_invalid_manifest_no_partial_seed(self):
        path = self.bundle/'manifest.json'
        original = path.read_bytes()
        mutations = [lambda m:m.pop('source'), lambda m:m.update(account_authorship='agent'),
            lambda m:m['evidence'][0].update(source_record_id=True),
            lambda m:m['evidence'][0].update(part='wrong'),
            lambda m:m['evidence'][0].update(selected_byte_range=[8, 999]),
            lambda m:m['evidence'][0].update(source_event=[]),
            lambda m:m['evidence'][0].update(source_payload_sha256='bogus'),
            lambda m:m['evidence'][0].update(purpose_authorship='agent'),
            lambda m:m['evidence'][0].update(purpose=1),
            lambda m:m['evidence'][0].update(source_bytes=True),
            lambda m:m['source'].update(journal_head=1),
            lambda m:m['source'].update(files={'x':{'path':'x','sha256':'bad'}}),
            lambda m:m['task'].update(bytes=True),
            lambda m:m['account'].update(file=m['task']['file']),
            lambda m:m['account'].update(file='../account'),
            lambda m:m['account'].update(sha256='0'*64),
            lambda m:m['evidence'][1].update(source_payload_sha256='0'*64)]
        agent, _ = self.agent()
        for mutate in mutations:
            value = json.loads(original); mutate(value); path.write_text(json.dumps(value))
            with self.subTest(manifest=value):
                self.rejected(agent, '.')
        path.write_bytes(original)
        target = self.bundle/'account.md'; data = target.read_bytes(); target.unlink(); target.symlink_to(self.account)
        self.rejected(agent, 'ordinary')
        target.unlink(); target.write_bytes(data)
        path.unlink(); external = self.root/'manifest'; external.write_bytes(original); path.symlink_to(external)
        self.rejected(agent, 'ordinary')
        path.unlink(); path.write_bytes(original.replace(b'"format":', b'"format":"duplicate", "format":', 1))
        self.rejected(agent, 'Duplicate')
        # Integrity-consistent but non-UTF-8 payload still rejects before effects.
        from mads.handoff import sha
        bad = b'\xff'; target.write_bytes(bad)
        value = json.loads(original); value['account'].update(bytes=1, sha256=sha(bad)); path.write_text(json.dumps(value))
        self.rejected(agent, 'utf-8')
        target.write_bytes(data); path.write_bytes(original)
        agent.seed_handoff(self.bundle)  # ordinary validation errors did not poison fresh agent

    def test_binding_and_account_conflicts(self):
        agent, _ = self.agent()
        agent.task = 'Different original requirement'
        self.rejected(agent, 'original task')
        agent.task = 'Wrapper\r\n'+TASK+'\r\nHost facts'
        self.rejected(agent, 'original task')
        self.rejected(agent, 'does not match', original_task=TASK.replace('\r\n', '\n').encode())
        self.rejected(agent, 'Conflicting', original_task=TASK.encode(), initial_account=b'newer claim')
        self.rejected(agent, 'must be exact', original_task=TASK.encode(), initial_account=ACCOUNT)
        result = agent.seed_handoff(self.bundle, original_task=TASK.encode(), initial_account=ACCOUNT.encode())
        self.assertEqual(result['task_binding']['mode'], 'explicit_original_in_wrapper')
        self.assertEqual(agent.compile()['messages'][1]['content'], agent.task)
        self.assertEqual(agent.context.context_status()['injected_account']['authorship'], self.manifest['account_authorship'])
        other, _ = self.agent(); other.task = 'wrapper without original'
        self.rejected(other, 'occur exactly', original_task=TASK.encode())

    def test_initialization_guards_preserve_existing_state(self):
        for modify in (lambda a:a.compile(), lambda a:a.context.working.write_text('existing account'),
                       lambda a:a.context.records.append({'record_id':1}),
                       lambda a:a.store.add('existing'), lambda a:a.store.blob('unattached existing bytes'),
                       lambda a:setattr(a, 'iterations', 1), lambda a:setattr(a.tools, 'done', 'already done'),
                       lambda a:a.context.groups.append({'id':1})):
            agent, _ = self.agent(); modify(agent); self.rejected(agent, 'fresh')
        agent, _ = self.agent()
        agent.context.working.write_text('Do not erase')
        other = Agent(self.repo, TASK, agent.run_root, agent.provider, agent.store, session_role='reviewer')
        self.assertEqual(other.context.working.read_text(), 'Do not erase')
        self.rejected(other, 'fresh')

    def test_empty_journal_and_symlink_are_not_fresh(self):
        for symlink in (False, True):
            agent, _ = self.agent()
            target = self.root/'not-created'
            if symlink:
                agent.store.path.symlink_to(target)
            else:
                agent.store.path.touch()
            self.rejected(agent, 'fresh')
            self.assertFalse(target.exists())
            self.assertEqual(agent.store.path.is_symlink(), symlink)
            if symlink:
                self.assertEqual(os.readlink(agent.store.path), str(target))
                # The shared optional fixture copier follows links; preserve the
                # assertion above, then remove this owned dangling test link.
                agent.store.path.unlink()

    def test_seed_results_are_detached_and_persistence_failure_forbids_retry(self):
        agent, _ = self.agent()
        imported = agent.seed_handoff(self.bundle)
        original = copy.deepcopy(agent.context.records)
        imported['selections'][0]['record_id'] = -1
        self.assertEqual(agent.context.records, original)
        other, _ = self.agent()
        with patch.object(other.tools, 'write', side_effect=OSError('fixture disk failure')):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                other.seed_handoff(self.bundle)
        # Disk errors are explicitly not transactional. Do not retry uncertain effects.
        self.assertTrue(other.store.events)
        self.assertEqual(other.context.records, [])
        self.assertEqual(other.context.working.read_bytes(), b'')
        self.rejected(other, 'fresh')

    def test_capacity_failure_does_not_truncate_or_send(self):
        for protocol in ('chat', 'responses'):
            agent, bodies = self.agent(protocol)
            agent.seed_handoff(self.bundle)
            selections = copy.deepcopy(agent.context.records)
            agent.context.input_limit = 1
            outcome = agent.run()
            self.assertEqual(outcome['harness_outcome']['status'], 'capacity_blocked')
            self.assertEqual(outcome['agent_outcome']['status'], 'not_completed')
            self.assertEqual(outcome['cleanup_outcome']['status'], 'completed')
            self.assertEqual(bodies, [])
            self.assertEqual(agent.context.records, selections)
            self.assertEqual(agent.context.working.read_bytes(), ACCOUNT.encode())
            self.assertEqual(Path(agent.store.history(selections[0]['record_id'])['path']).read_bytes(), EVIDENCE.encode())

    def test_empty_evidence_and_tolerant_line_end(self):
        other = self.root/'empty-bundle'
        export_bundle(self.root/'source', other, self.task, self.account, [])
        agent, _ = self.agent(); imported = agent.seed_handoff(other)
        self.assertEqual(imported['selections'], [])
        self.assertEqual(evidence_packets(agent.compile()), [])
        lines = self.root/'line-bundle'
        manifest = export_bundle(self.root/'source', lines, self.task, self.account,
            [{'record_id':self.foreign_id, 'view':{'lines':[2, 999]}}])
        self.assertEqual((lines/manifest['evidence'][0]['file']).read_bytes(), (EVIDENCE+'suffix').encode())
        self.assertEqual(validate_bundle(lines), manifest)

    def test_cli_activation_and_dry_run(self):
        agent, bodies = self.agent()
        # CLI constructs its own fresh Agent but uses this real SDK/MockTransport provider.
        args = [str(self.repo), '--task', str(self.task), '--run-dir', str(self.root/'cli'),
                '--provider', 'openai', '--handoff-bundle', str(self.bundle), '--transport-retries', '0']
        def provider_for_store(store, **kwargs):
            agent.provider.store = store
            return agent.provider
        def reviewer_provider(store, **kwargs):
            return Provider(store, transport=agent.provider._client._client._transport, **kwargs)
        with patch('mads.agent.Provider', side_effect=provider_for_store), patch('mads.provider.Provider', side_effect=reviewer_provider):
            self.assertEqual(agent_main([*args]), 0)
        self.assertEqual(sequence(bodies[0])[1]['content'].encode(), TASK.encode())
        self.assertEqual(''.join(evidence_packets(bodies[0])[0]['text_parts']).encode(), EVIDENCE.encode())
        args[args.index('--run-dir')+1] = str(self.root/'dry')
        self.assertEqual(agent_main(args+['--dry-run']), 0)
        self.task.write_text('mismatch')
        with self.assertRaises(SystemExit) as exc:
            agent_main(args+['--dry-run'])
        self.assertEqual(exc.exception.code, 2)
        self.assertFalse((self.root/'dry').exists())


if __name__ == '__main__':
    unittest.main()
