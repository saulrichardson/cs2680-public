"""Oversized retention recovery, with real captures and ordinary sibling effects.

No provider calls or application evaluator.
Run: PYTHONPATH=. python checks/retention_recovery.py OUT
The reduced compiler budget intentionally omits production tool schemas.
"""
import copy
import json
import sys
from pathlib import Path

from mads.context import Context
from mads.store import Store, dump, compact_result
from mads.tools import Tools
from mads.responses import execution_view, request_body


def run(root):
    verified = []
    for protocol in ('chat', 'responses'):
        base = root/protocol
        repo = base/'repo'
        repo.mkdir(parents=True)
        store = Store(base/'journal')
        context = Context(store, base/'memory', input_limit=8000, total_limit=18000,
                          output_tokens=4000, mask_at=0, pressure_at=7500)
        tools = Tools(repo, base, store)

        def group(results):
            identity = store.add('literal_compiler_fixture')
            calls, native = [], []
            for i, result in enumerate(results):
                call_id = f'{identity}-{i}'
                result.update(call_id=call_id, status='completed')
                calls.append({'id':call_id, 'type':'function',
                              'function':{'name':'read', 'arguments':'{}'}})
                native.append({'type':'function_call', 'id':f'item-{call_id}',
                               'call_id':call_id, 'name':'read', 'arguments':'{}'})
            assistant = {'role':'assistant', 'tool_calls':calls}
            if protocol == 'responses':
                assistant = execution_view({'status':'completed','output':native})
            return {'id':identity,'assistant':assistant,'results':results,'fresh':True}

        def compile(current, restart=None):
            request = context.compile('SYSTEM', 'ORIGINAL TASK', [], 'fixture', {}, current,
                restart, request_transform=request_body if protocol == 'responses' else None)
            assert context.tokens(request) <= context.limit
            return request

        text = 'first error: expected idempotent merge\n' + 'αβγδ'*75000 + '\nFINAL SUMMARY: one test failed\n'
        (repo/'large.txt').write_text(text)
        first = group([tools.read('large.txt', range='full', output_chars=None)])
        request = compile(first)
        output = first['results'][0]['output']
        assert not output['display_complete'] and output['shown_byte_ranges']
        raw = text.encode()
        assert ''.join(output['text_parts']).encode() == b''.join(raw[a:b] for a,b in output['shown_byte_ranges'])
        compact = compact_result(first['results'][0])['output']
        assert compact['next_read']['range']['bytes'][0] == output['shown_byte_ranges'][-1][1]
        assert Path(compact['next_read']['path']).read_bytes() == raw
        assert not compact.get('prior_request_shown_byte_ranges')
        (base/'oversized-read-request.json').write_text(dump(request))
        verified.append(protocol+': 600KB Unicode read paged within 8K estimated tokens; exact visible ranges and recovery path')

        # A selected full body cannot fit. The compiler must reject without losing
        # old history or a real sibling write performed before the batch boundary.
        selector = compact['retain']
        old_ids = [g['id'] for g in context.groups]
        sibling = tools.write('effect.txt','ordinary effect survives')
        sibling.update(effects=tools.effects)
        current = group([{}, sibling])
        request = compile(current, {'index':0,'arguments':{'keep_records':[selector]}})
        assert current['results'][0]['context_change'] == 'unchanged'
        assert context.records == []
        assert [g['id'] for g in context.groups] == old_ids+[current['id']]
        assert (repo/'effect.txt').read_text() == 'ordinary effect survives'
        assert current['results'][1]['effects'] == 'file_replaced'
        (base/'oversized-retention-rejection.json').write_text(dump(request))
        verified.append(protocol+': oversized exact selection rejects; prior groups and real write survive')

        # Recover with an exact small UTF8 interval, not repeating that effect.
        selector['view'] = {'bytes':[0,len('first error: expected idempotent merge\n'.encode())]}
        current = group([{}])
        request = compile(current, {'index':0,'arguments':{'keep_records':[selector]}})
        assert current['results'][0]['context_change'] == 'applied'
        assert len(context.groups) == 1 and context.records == [selector]
        assert 'first error: expected idempotent merge' in dump(request)
        (base/'recovered-retention.json').write_text(dump(request))
        verified.append(protocol+': corrected excerpt restart applies after rejection without rerunning ordinary effect')

        # Another common exactness error: selected endpoints split a UTF8 codepoint.
        bad = copy.deepcopy(selector)
        prefix = selector['view']['bytes'][1]
        bad['view'] = {'bytes':[prefix,prefix+1]}
        current = group([{}])
        request = compile(current, {'index':0,'arguments':{'keep_records':[bad]}})
        assert current['results'][0]['context_change'] == 'unchanged'
        assert 'valid UTF-8 interval' in current['results'][0]['error']
        assert context.records == [selector]
        (base/'split-codepoint-rejection.json').write_text(dump(request))
        verified.append(protocol+': split UTF8 endpoint rejects recoverably; existing exact selection remains')

    (root/'results.json').write_text(json.dumps({'verified':verified,'live_provider_proof':False}, indent=2)+'\n')
    print('\n'.join(verified))


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
