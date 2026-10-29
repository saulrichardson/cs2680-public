"""Exact reconstruction and end-of-batch control checks using real files and saved observations.

The native envelopes below are literal compiler fixtures, not claims about provider behavior.
"""
import json
import sys
from pathlib import Path
from mads.agent import Agent
from mads.context import Context, CapacityBlocked, render_exchange
from mads.store import Store, dump, view


if __name__ == '__main__':
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True)
    verified = []
    artifacts = {}


    def fixture(context, results, reasoning='native fixture reasoning'):
        identity = context.store.add('literal_compiler_fixture')
        calls = []
        for i, result in enumerate(results):
            result.update(call_id=f'{identity}-{i}', status=result.get('status', 'completed'))
            calls.append({'id':result['call_id'], 'type':'function',
                          'function':{'name':'read', 'arguments':'{}'}})
        return {'id':identity, 'assistant':{'role':'assistant', 'reasoning_content':reasoning,
                'tool_calls':calls}, 'results':results, 'fresh':True}


    def context(name, **settings):
        return Context(Store(root/name/'journal'), root/name/'memory', **settings)


    def compile(context, current=None, restart=None, **kw):
        return context.compile('system', 'ORIGINAL TASK', [], 'literal-fixture',
                               {'jobs':[]}, current, restart, **kw)


    def facts(request):
        last = request['messages'][-1]
        if last['role'] == 'tool':
            return json.loads(last['content'])['harness_runtime']
        return json.loads(last['content'].split('\n', 1)[1].removesuffix('</tool_response>'))


    def assert_manifest(context, request, label):
        manifest = context.store.record(context.context_id)
        payload = context.store.verify(manifest['request_blob']).read_text()
        assert payload == dump(request)
        assert manifest['bytes'] == len(payload.encode())
        assert manifest['estimated_tokens'] == context.tokens(request) <= context.limit
        displayed = facts(request)['context']
        for key in ('exchanges', 'effective_input_limit_tokens', 'output_tokens', 'replay_omission'):
            assert displayed[key] == manifest[key], key
        assert displayed['masked_exchanges'] == len(manifest['masked_groups'])
        assert displayed['exact_evidence_selections'] == len(manifest['kept_records'])
        assert facts(request)['working_account']['record'] == manifest['account_record']
        assert facts(request)['working_account']['state'] == manifest['account_state']
        assert facts(request)['sampled_at'] == manifest['runtime_sampled_at']
        artifacts[label] = {'manifest':str(context.store.path), 'record_id':context.context_id,
                            'request':str(context.store.verify(manifest['request_blob']))}
        return manifest


    # A short selected interval stays exact while the associated full result can shrink.
    c = context('excerpt', input_limit=20000, total_limit=26000, output_tokens=4000,
                mask_at=0, pressure_at=19000)
    raw = 'exact excerpt\n' + 'x'*26000
    artifact = c.store.artifact(c.store.blob(raw))
    record = c.store.evidence(artifact, kind='test_observation')
    old = fixture(c, [{'observation_record':record, 'output':view(artifact, output_chars=None)}])
    compile(c, old)
    c.input_limit = 6000
    c.pressure_at = 5500
    current = fixture(c, [{'context_change':'pending'}])
    request = compile(c, current, {'index':0, 'arguments':{
        'keep_recent_exchanges':1, 'keep_records':[{'record_id':record, 'view':{'bytes':[0,14]}}]}})
    assert len(c.groups) == 2
    selection = json.loads(request['messages'][2]['content'].split('\n', 1)[1].removesuffix('</tool_response>'))
    assert selection['text_parts'] == [raw[:14]] and selection['shown_byte_ranges'] == [selection['selected_byte_range']]
    assert not old['results'][0]['output']['display_complete']
    acknowledgement = json.loads(request['messages'][-1]['content'])
    manifest = assert_manifest(c, request, '5.1-5.2-5.4')
    assert acknowledgement['context_change'] == 'applied'
    for key, value in acknowledgement['applied_context'].items():
        assert value == manifest[key]
    assert old['assistant'] in request['messages']
    assert Path(old['results'][0]['output']['path']).read_text() == raw
    verified += ['5.1-candidate-metadata', '5.2-excerpt-granularity', '5.4-finalized-body']
    previous_ids = [g['id'] for g in c.groups]
    request = compile(c, fixture(c, [{'context_change':'pending'}]),
                      {'index':0, 'arguments':{'keep_records':[{'record_id':record, 'view':'full'}]}})
    assert [g['id'] for g in c.groups][:-1] == previous_ids
    assert json.loads(request['messages'][-1]['content'])['context_change'] == 'unchanged'
    assert_manifest(c, request, 'oversized-full-selection-rejection')
    verified.append('5.2-full-selection-rejects-recoverably')


    # Finalized metadata, acknowledgements and notices are counted even at the exact boundary.
    c = context('exact-boundary', mask_at=0, pressure_at=1)
    current = fixture(c, [{'context_change':'pending'}])
    request = compile(c, current, {'index':0, 'arguments':{}})
    assert facts(request)['notice']
    assert_manifest(c, request, 'pressure-and-restart')
    boundary = c.tokens(request)
    # _fit isolates final serialization without changing the account snapshot or journal identity.
    account = c.last_account
    policy = c.policy()
    sampled = '2026-10-07T00:00:00.000000+00:00'
    for _ in range(3):
        c.input_limit = boundary
        request, _, _ = c._fit('system', 'ORIGINAL TASK', [], 'literal-fixture', account, [],
                               c.groups, {}, policy, sampled, len(c.store.events), {})
        boundary = c.tokens(request)
    c.input_limit = boundary-1
    try:
        c._fit('system', 'ORIGINAL TASK', [], 'literal-fixture', account, [], c.groups, {},
               policy, sampled, len(c.store.events), {})
    except CapacityBlocked:
        verified.append('5.4-final-byte-boundary')
    else:
        raise AssertionError('Final serialized body one estimated token over boundary must block')


    # An invalid current account and now-too-large prior account leave an explicit repair request.
    c = context('account', input_limit=12000, total_limit=20000, output_tokens=4000,
                mask_at=0, pressure_at=11000)
    c.working.write_text('last fitting account '*350)
    compile(c)
    previous_record = c.last_account['record']
    c.working.write_bytes(b'\xff')
    c.input_limit, c.pressure_at = 2200, 1800
    request = compile(c)
    account = facts(request)['working_account']
    assert account['state'] == 'absent' and account['text'] == '' and account['record'] is None
    assert 'Neither account was injected' in account['recovery_reason']
    assert previous_record in account['omitted_account_records']
    assert c.working.read_bytes() == b'\xff'
    assert c.store.history(previous_record)['observed_bytes'] > 7000
    assert_manifest(c, request, '5.5-absent-account-repair')
    c.working.write_text('repaired small account')
    request = compile(c)
    assert 'repaired small account' in facts(request)['working_account']['text']
    assert_manifest(c, request, 'account-repaired')
    verified.append('5.5-absent-account-recovery')


    # These exercise the actual tool batch with filesystem effects; no provider is simulated.
    class CompilerRoute:
        config = {'model':'literal-fixture'}

        @staticmethod
        def request_options():
            return {'provider':{'order':['fixture-upstream'], 'allow_fallbacks':False, 'require_parameters':True}}


    run = root/'batch'
    repo = run/'repo'
    repo.mkdir(parents=True)
    agent = Agent(repo, 'ORIGINAL TASK', run/'run', CompilerRoute(), input_limit=32000,
                  total_limit=40000, output_tokens=4000, mask_at=0, pressure_at=31000)
    agent.system = 'system'
    agent.context.working.write_text('initial account')
    agent.compile(fixture(agent.context, [{}], reasoning='large retained native history '*1500))


    def batch(calls):
        message = {'role':'assistant', 'tool_calls':[
            {'id':f'batch-{len(agent.store.events)}-{i}', 'type':'function',
             'function':{'name':name, 'arguments':dump(arguments)}}
            for i, (name, arguments) in enumerate(calls)]}
        record = agent.store.add('literal_batch_fixture', blob=agent.store.blob(dump(message)))
        return agent.batch(message, record)


    ordinary = ('write', {'path':'effect.txt', 'content':'ordinary effect survives rejection'})
    group, restart = batch([('context_configure', {'output_tokens':39999}), ordinary])
    assert agent.context.output_tokens == 4000  # Staged, never mutated during effects.
    request = agent.compile(group, restart)
    assert agent.context.output_tokens == 4000
    assert group['results'][0]['context_change'] == 'unchanged'
    assert group['results'][1]['effects'] == 'file_replaced'
    assert (repo/'effect.txt').read_text() == ordinary[1]['content']
    assert_manifest(agent.context, request, '5.3-invalid-config-effects-preserved')
    verified.append('5.3-invalid-effective-capacity-and-ordinary-effect')

    configuration = {'output_tokens':25000, 'mask_at_tokens':0, 'pressure_at_tokens':14000}
    group, restart = batch([('context_configure', configuration)])
    request = agent.compile(group, restart)
    assert group['results'][0]['context_change'] == 'unchanged'
    assert agent.context.output_tokens == 4000
    assert_manifest(agent.context, request, '5.3-requires-history-selection')

    # Separate configure calls may be individually inconsistent yet have a valid final combination.
    large_file = repo/'large.txt'
    large_file.write_text('large retrieval\n'+'z'*90000)
    group, restart = batch([
        ('context_configure', {'output_tokens':25000}),
        ('write', {'path':str(agent.context.working), 'content':'final account after all sibling calls'}),
        ('context_restart', {'keep_recent_exchanges':0, 'keep_records':[]}),
        ('read', {'path':'large.txt', 'range':'full', 'output_chars':None}),
        ('context_configure', {'mask_at_tokens':0, 'pressure_at_tokens':14000}),
        ('write', {'path':'independent.txt', 'content':'independent effect exactly once'}),
    ])
    request = agent.compile(group, restart)
    assert agent.context.output_tokens == 25000 and len(agent.context.groups) == 1
    assert all(group['results'][i]['context_change'] == 'applied' for i in (0, 2, 4))
    assert 'final account after all sibling calls' in facts(request)['working_account']['text']
    assert not group['results'][3]['output']['display_complete']
    assert request['provider']['order'] == ['fixture-upstream']
    assert (repo/'independent.txt').read_text() == 'independent effect exactly once'
    manifest = assert_manifest(agent.context, request, '5.3-combined-configuration-restart-effects')
    for i in (0, 2, 4):
        for key, value in group['results'][i]['applied_context'].items():
            assert value == manifest[key]
    assert len([e for e in agent.store.events if e['event']=='invocation' and
                json.loads(e['raw_arguments']).get('path')=='effect.txt']) == 1
    verified.append('5.3-combined-staged-configurations-and-restart')

    # A route-rejected malformed argument string remains exact historical data. Valid siblings
    # still execute once, and their native calls/results remain paired without repaired arguments.
    bad_arguments = '{"path":"must-not-exist.txt","content":"bad"'
    native = {'role':'assistant', 'reasoning_content':'preserved native reasoning', 'tool_calls':[
        {'id':'malformed', 'type':'function', 'function':{'name':'write', 'arguments':bad_arguments}},
        {'id':'valid-sibling', 'type':'function', 'function':{'name':'write',
            'arguments':dump({'path':'valid-sibling.txt', 'content':'valid sibling ran once'})}},
    ]}
    record = agent.store.add('literal_malformed_envelope', blob=agent.store.blob(dump(native)))
    group, restart = agent.batch(native, record)
    assert group['results'][0]['status'] == 'rejected'
    assert group['results'][0]['effects'] == 'none' and not (repo/'must-not-exist.txt').exists()
    assert (repo/'valid-sibling.txt').read_text() == 'valid sibling ran once'
    request = agent.compile(group, restart)
    native_replay = next(m for m in request['messages'] if m.get('reasoning_content') == native['reasoning_content'])
    assert native_replay['tool_calls'] == [native['tool_calls'][1]]
    assert [m['tool_call_id'] for m in request['messages'] if m['role']=='tool'][-1] == 'valid-sibling'
    historical = next(m for m in request['messages'] if 'REJECTED TOOL CALL' in m.get('content', ''))
    saved = json.loads(historical['content'].split('\n', 1)[1].removesuffix('</tool_response>'))
    assert saved['tool_call'] == native['tool_calls'][0]
    assert saved['result']['status'] == 'rejected' and saved['native_replay'] == 'invalid_json_object_arguments'
    assert group['assistant'] == native
    assert json.loads(Path(agent.store.history(record)['path']).read_text()) == native
    assert_manifest(agent.context, request, 'malformed-call-valid-sibling')
    only_invalid = {**native, 'tool_calls':[native['tool_calls'][0]]}
    projection = render_exchange(only_invalid, [group['results'][0]])
    assert projection[0]['reasoning_content'] == native['reasoning_content']
    assert 'tool_calls' not in projection[0] and not any(m['role']=='tool' for m in projection)
    assert json.loads(projection[1]['content'].split('\n', 1)[1].removesuffix('</tool_response>'))['tool_call']['function']['arguments'] == bad_arguments
    verified.append('malformed-json-replay-with-native-valid-siblings')
    for i, invalid in enumerate(('null', '[]', '{"path":NaN}', '{"path":Infinity}')):
        native_invalid = {'role':'assistant', 'tool_calls':[{'id':f'invalid-shape-{i}',
            'type':'function', 'function':{'name':'write', 'arguments':invalid}}]}
        record = agent.store.add('literal_invalid_arguments', blob=agent.store.blob(dump(native_invalid)))
        group, _ = agent.batch(native_invalid, record)
        assert group['results'][0]['status'] == 'rejected' and group['results'][0]['effects'] == 'none'
        projection = render_exchange(native_invalid, group['results'])
        assert 'tool_calls' not in projection[0] and not any(m['role']=='tool' for m in projection)
        historical = json.loads(projection[1]['content'].split('\n', 1)[1].removesuffix('</tool_response>'))
        assert historical['tool_call']['function']['arguments'] == invalid
    verified.append('nonobject-and-nonstandard-json-rejected-with-safe-replay')
    cleanup = agent.tools.jobs.close()
    assert all(not item['errors'] for item in cleanup)

    (root/'coverage.json').write_text(json.dumps({'verified':verified, 'artifacts':artifacts,
        'provider_integration':False, 'cleanup':cleanup}, indent=2)+'\n')
    print(json.dumps({'verified':verified, 'artifacts':artifacts}, indent=2))
