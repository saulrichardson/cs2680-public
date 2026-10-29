"""Tail-account layout contracts on both protocols. Local fixtures, not provider proof."""
import copy
import json
import sys
from pathlib import Path

from mads.context import Context
from mads.responses import execution_view, request_body
from mads.store import Store, dump


def main(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    verified = []
    for protocol in ('chat', 'responses'):
        ctx = Context(Store(root/protocol/'journal'), root/protocol/'memory',
                      mask_at=0, pressure_at=1)
        transform = request_body if protocol == 'responses' else None

        def compile(current=None, restart=None):
            return ctx.compile('SYSTEM', 'AUTHORITATIVE TASK', [], 'literal',
                               {'running_jobs':[], 'capture_failures':[], 'completed_jobs':2},
                               current, restart, request_transform=transform)

        def items(request):
            return request['input' if protocol == 'responses' else 'messages']

        def facts(request):
            tail = items(request)[-1]
            if tail.get('role') == 'tool' or tail.get('type') == 'function_call_output':
                return json.loads(tail.get('output', tail.get('content')))['harness_runtime']
            return json.loads(tail['content'].split('\n', 1)[1].removesuffix('</tool_response>'))

        def check(request, expected):
            sequence = items(request)
            assert sequence[:2] == [{'role':'system', 'content':'SYSTEM'},
                                    {'role':'user', 'content':'AUTHORITATIVE TASK'}]
            assert all('AGENT WORKING STATE' not in dump(item) for item in sequence)
            carriers = []
            for index,item in enumerate(sequence):
                if item.get('role') == 'tool' or item.get('type') == 'function_call_output':
                    result = json.loads(item.get('output',item.get('content')))
                    if 'harness_runtime' in result:
                        carriers.append((index,result['harness_runtime']))
                elif item.get('role') == 'user' and item.get('content','').startswith('<tool_response>CURRENT HARNESS FACTS\n'):
                    carriers.append((index,json.loads(item['content'].split('\n',1)[1].removesuffix('</tool_response>'))))
            runtime = facts(request)
            assert carriers == [(len(sequence)-1,runtime)]
            account = runtime['working_account']
            status = ctx.context_status()
            manifest = ctx.store.record(ctx.context_id)
            assert account['text'] == expected
            assert account['authorship'] == 'agent' and account['validation'] == 'unverified'
            assert account['state'] == status['injected_account']['state'] == manifest['account_state']
            assert account['record'] == status['injected_account']['record_id'] == manifest['account_record']
            assert Path(status['injected_account']['read']['path']).read_text() == expected
            assert json.loads(Path(status['compiled_request_read']['path']).read_text()) == request
            assert runtime['sampled_at'] == manifest['runtime_sampled_at']
            assert set(runtime['context']) == {'exchanges', 'effective_input_limit_tokens', 'output_tokens',
                'replay_omission', 'reasoning_replay', 'reasoning_omission', 'masked_exchanges', 'exact_evidence_selections',
                'estimated_tokens_before_telemetry', 'estimate_scope', 'continuation_headroom_estimate_tokens'}
            for key in ('exchanges','effective_input_limit_tokens','output_tokens','replay_omission',
                        'reasoning_replay','reasoning_omission'):
                assert runtime['context'][key] == manifest[key]
            assert runtime['context']['masked_exchanges'] == len(manifest['masked_groups'])
            assert runtime['context']['exact_evidence_selections'] == len(manifest['kept_records'])
            assert runtime['notice']  # final accounting must include notice + whole account
            assert manifest['estimated_tokens'] == ctx.tokens(request) <= ctx.limit
            assert all('harness_runtime' not in result for group in ctx.groups for result in group['results'])
            return sequence

        ctx.working.write_text('Old interpretation: UTC grouping')
        request = compile()
        check(request, 'Old interpretation: UTC grouping')
        assert items(request)[-1]['role'] == 'user'

        # A literal account-writing call stays historical without becoming current.
        identity = ctx.store.add('literal_exchange')
        call = {'id':'call-1', 'type':'function', 'function':{'name':'write',
            'arguments':dump({'path':str(ctx.working), 'content':'Old interpretation: UTC grouping'})}}
        native = [
            {'type':'reasoning', 'id':'reason-1', 'summary':[], 'encrypted_content':'opaque-fixture-+/='},
            {'type':'message', 'id':'message-1', 'role':'assistant', 'phase':'commentary',
             'content':[{'type':'output_text', 'text':'Inspect evidence.', 'annotations':[]}]},
            {'type':'function_call', 'id':'item-1', 'call_id':'call-1', 'name':'write',
             'arguments':call['function']['arguments'], 'status':'completed'}]
        assistant = execution_view({'status':'completed','output':native}) if transform else {
            'role':'assistant', 'reasoning_content':'literal reasoning', 'tool_calls':[call]}
        original = copy.deepcopy(assistant)
        group = {'id':identity, 'assistant':assistant, 'results':[
            {'call_id':'call-1', 'status':'completed', 'effects':'file_replaced'}], 'fresh':True}
        ctx.working.write_text('Corrected: group by local day; refuting check N. Next fix aggregator.')
        request = compile(group)
        sequence = check(request, ctx.working.read_text())
        if transform:
            assert sequence[2:-1] == native  # opaque bytes, IDs, phase, order unchanged
            assert sequence[-1]['type'] == 'function_call_output' and sequence[-1]['call_id'] == 'call-1'
        else:
            assert sequence[2] == original
            assert sequence[-1]['role'] == 'tool' and sequence[-1]['tool_call_id'] == 'call-1'
        assert group['assistant'] == original
        assert 'Old interpretation' in dump(request)  # history deliberately not selectively pruned
        ctx.working.write_text('Next action: verify date boundaries.')
        request = compile()
        check(request, ctx.working.read_text())
        assert dump(request).count('Corrected: group by local day') == 0
        assert 'Old interpretation' in dump(request)
        assert ctx.context_status()['groups'] == [identity]

        # Non-tool continuation uses the same wrapper; no new native item types.
        prose = {'id':ctx.store.add('literal_prose'), 'assistant':{'role':'assistant', 'content':'A new observation.'},
                 'results':[], 'fresh':True}
        if transform:
            prose['assistant'] = {'role':'assistant', 'responses_output':[native[1]]}
        request = compile(prose)
        check(request, ctx.working.read_text())
        assert items(request)[-1]['role'] == 'user'
        assert 'CURRENT HARNESS FACTS' in items(request)[-1]['content']
        verified.append(protocol+'-tail-account-native-continuity-history-versus-current')
    (root/'coverage.json').write_text(dump({'verified':verified,'provider_integration':False})+'\n')
    print(dump({'verified':verified,'provider_integration':False}))


if __name__ == '__main__':
    main(sys.argv[1])
