"""Regression fixtures for first delivery and exact-retention identity on both protocols.

These compile real tool observations; native envelopes are local fixtures, not live proof.
"""
import copy
import json
import sys
from pathlib import Path
from mads.context import Context
from mads.responses import execution_view, request_body
from mads.store import Store, compact_result, dump
from mads.tools import Tools


def main(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    verified = []
    for protocol in ('chat', 'responses'):
        run = root/protocol
        repo = run/'repo'
        repo.mkdir(parents=True)
        store = Store(run/'journal')
        context = Context(store, run/'memory', mask_at=1)
        context.keep_recent = 0
        tools = Tools(repo, run, store)
        text = 'interface: apply(records, state) -> state\ncontradiction: duplicates do not increment\n'
        (repo/'api.txt').write_text(text)

        def group(results):
            identity = store.add('fixture_exchange')
            calls = []
            native = []
            for index, result in enumerate(results):
                call_id = f'call-{identity}-{index}'
                result.update(call_id=call_id)
                result.setdefault('status','completed')
                result.setdefault('tool_name',result.get('output',{}).get('action','context_restart'))
                result.setdefault('effects','none')
                result['result_record'] = store.add('tool_result', result=copy.deepcopy(result))
                calls.append({'id':call_id, 'type':'function', 'function':{'name':result['tool_name'],'arguments':'{}'}})
                native.append({'type':'function_call', 'id':f'item-{identity}-{index}',
                               'call_id':call_id, 'name':result['tool_name'], 'arguments':'{}'})
            assistant = {'role':'assistant', 'tool_calls':calls}
            if protocol == 'responses':
                assistant = execution_view({'status':'completed', 'output':native})
            return {'id':identity, 'assistant':assistant, 'results':results, 'fresh':True}

        def compile(current, restart=None):
            return context.compile('SYSTEM', 'UNCHANGED USER REQUIREMENTS', [], 'fixture', {},
                                   current, restart, request_transform=request_body if protocol == 'responses' else None)

        def displayed(request):
            texts = []
            for item in request.get('messages', request.get('input', [])):
                body = item.get('output', item.get('content', ''))
                if not isinstance(body, str):
                    continue
                if body.startswith('<tool_response>EXACT JOURNAL EVIDENCE'):
                    body = body.split('\n', 1)[1].removesuffix('</tool_response>')
                try:
                    data = json.loads(body)
                except ValueError:
                    continue
                texts.extend(data.get('output', data).get('text_parts', []))
            return texts

        first = group([tools.read('api.txt')])
        request = compile(first)
        assert text in displayed(request)
        assert first['id'] not in context.masked
        shown = compact_result(first['results'][0])
        assert 'prior_request_shown_byte_ranges' not in shown['output']
        selector = shown['output']['retain']
        assert selector['record_id'] == first['results'][0]['observation_record']
        assert selector['record_id'] != shown['details']['record_id']
        context.validate_restart({'keep_records':[selector]})
        assert context.exact_view(selector)['selected_byte_range'] == [0,len(text.encode())]
        (repo/'api.txt').write_text('file changed')
        second = group([tools.history_read(selector['record_id'])])
        request = compile(second)
        assert first['id'] in context.masked and second['id'] not in context.masked
        assert second['results'][0]['output']['text_parts'] == [text]
        first_replay = compact_result(context.groups[0]['results'][0])['output']
        assert first_replay['text_parts'] == []
        assert first_replay['omission_reason'] == 'historical_output_masked'
        assert 'prior_request_shown_byte_ranges' not in first_replay
        assert 'prior_request_shown_byte_ranges' not in compact_result(second['results'][0])['output']
        # Rendering metadata is not retroactively added to original evidence.
        original = store.record(first['results'][0]['result_record'])['result']['output']
        assert 'prior_request_shown_byte_ranges' not in original
        assert compact_result(second['results'][0])['output']['retain'] == selector
        (run/'fresh-retrieval-request.json').write_text(dump(request))
        verified.append(protocol+': fresh first delivery, historical masking, immutable retrieval, observation selector')

        receipt_id = first['results'][0]['result_record']
        receipt_bytes = store.history(receipt_id)['observed_bytes']
        for selection in (
                {'record_id':receipt_id, 'view':{'bytes':[receipt_bytes+10, receipt_bytes+30]}},
                {'record_id':selector['record_id'], 'view':{'bytes':[0,len(text)+1]}},
                {'record_id':selector['record_id'], 'view':{'bytes':[2,2]}}):
            try:
                context.validate_restart({'keep_records':[selection]})
            except ValueError as exc:
                if selection['record_id'] == receipt_id:
                    assert 'observation_record '+str(selector['record_id']) in str(exc)
            else:
                raise AssertionError('invalid exact selection accepted')
        # Direct compiler path also rolls back invalid staged controls, not sibling effects.
        old_groups = [g['id'] for g in context.groups]
        previous_policy = context.policy()
        sibling = tools.write('effect.txt', 'effect remains')
        sibling.update(tool_name='write',effects=tools.effects)
        current = group([{}, sibling])
        current['configurations'] = [{'index':0, 'arguments':{'keep_recent':5}}]
        bad = {'record_id':receipt_id, 'view':{'bytes':[receipt_bytes+1,receipt_bytes+2]}}
        request = compile(current, {'index':0, 'arguments':{'keep_records':[bad]}})
        assert current['results'][0]['context_change'] == 'unchanged'
        assert [g['id'] for g in context.groups] == old_groups+[current['id']]
        assert context.policy() == previous_policy and context.records == []
        assert (repo/'effect.txt').read_text() == 'effect remains'
        assert current['results'][1]['effects'] == 'file_replaced'
        (run/'rejected-retention-request.json').write_text(dump(request))

        current = group([{}])
        request = compile(current, {'index':0, 'arguments':{'keep_records':[selector]}})
        assert context.records == [selector] and len(context.groups) == 1
        assert 'EXACT JOURNAL EVIDENCE' in dump(request)
        assert text in displayed(request)
        assert context.retained_evidence([selector])[0]['range'] == selector['view']
        (run/'retained-request.json').write_text(dump(request))
        # Ordinary paging remains tolerant at EOF; base64 and empty outputs have no selector.
        assert tools.read('api.txt', range={'bytes':[1000,2000]})['output']['selected_byte_range'] == [12,12]
        empty = group([tools.read('api.txt', range={'bytes':[0,0]})])
        assert 'retain' not in compact_result(empty['results'][0])['output']
        base64_read = group([tools.read('api.txt', range='full', encoding='base64')])
        assert 'retain' not in compact_result(base64_read['results'][0])['output']
        (repo/'empty.txt').write_bytes(b'')
        empty_artifact = tools.read('empty.txt')
        for selection in ({'record_id':empty_artifact['observation_record']},
                          {'record_id':selector['record_id'],'view':{'lines':[999,1000]}}):
            try:
                context.validate_restart({'keep_records':[selection]})
            except ValueError as exc:
                assert 'selects no bytes' in str(exc)
            else:
                raise AssertionError('empty exact evidence accepted')
        # Nonempty line ranges keep the established EOF normalization contract.
        assert context.exact_view({'record_id':selector['record_id'],'view':{'lines':[1,999]}})['selected_byte_range'] == [0,len(text.encode())]
        verified.append(protocol+': invalid retention rejects without losing history/effects; exact replay and EOF paging')

        # Retain selects the captured interval, including bytes not displayed yet.
        (repo/'long.txt').write_text('x'*80)
        partial = group([tools.read('long.txt', output_chars=9)])
        compile(partial)
        initial_ranges = copy.deepcopy(partial['results'][0]['output']['shown_byte_ranges'])
        assert initial_ranges == [[0,9]]
        partial_wire = compact_result(partial['results'][0])['output']
        assert partial_wire['retain']['view'] == {'bytes':[0,80]}
        assert 'prior_request_shown_byte_ranges' not in partial_wire
        latest = group([tools.read('api.txt')])
        compile(latest)
        replay = compact_result(partial['results'][0])['output']
        assert 'prior_request_shown_byte_ranges' not in replay
        assert replay['shown_byte_ranges'] == []
        # Finalized requests preserve delivered ranges; runtime adds no coverage ledger.
        request = compile(None)
        replay = compact_result(partial['results'][0])['output']
        assert 'prior_request_shown_byte_ranges' not in replay
        assert replay['shown_byte_ranges'] == []
        (run/'masked-prefix-request.json').write_text(dump(request))
        verified.append(protocol+': bounded prefix display and whole selected-interval handle remain distinct; no runtime prior-coverage metadata')
        # No subprocesses were started in this fixture.
    (root/'coverage.json').write_text(dump({'verified':verified,'live_provider_proof':False})+'\n')
    print('\n'.join(verified))


if __name__ == '__main__':
    main(sys.argv[1])
