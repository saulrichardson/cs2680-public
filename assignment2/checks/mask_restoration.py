"""Reversible policy masks, using real immutable captures in both native projections.

No provider calls: these are compiler/ordinary-tool checks, not live Course proof.
"""
import copy
import json
import sys
from pathlib import Path
from mads.context import Context
from mads.responses import execution_view, request_body
from mads.store import Store, dump
from mads.tools import Tools


def main(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    verified = []
    for protocol in ('chat', 'responses'):
        def setup(name):
            run = root/protocol/name
            repo = run/'repo'
            repo.mkdir(parents=True)
            store = Store(run/'journal')
            context = Context(store, run/'memory', input_limit=60000, total_limit=70000,
                              output_tokens=4000, mask_at=1, pressure_at=50000)
            context.keep_recent = 0
            tools = Tools(repo, run, store)

            def group(results, configuration=None):
                identity = store.add('fixture_exchange')
                calls, native = [], []
                for i, result in enumerate(results):
                    call_id = f'{identity}-{i}'
                    name = 'context_configure' if configuration is not None and i == 0 else 'read'
                    args = dump(configuration) if name == 'context_configure' else '{}'
                    result.update(call_id=call_id, status='completed', tool_name=name, effects='none')
                    result['result_record'] = store.add('tool_result', result=copy.deepcopy(result))
                    calls.append({'id':call_id, 'type':'function', 'function':{'name':name, 'arguments':args}})
                    native.append({'type':'function_call', 'id':f'item-{call_id}',
                                   'call_id':call_id, 'name':name, 'arguments':args})
                assistant = ({'role':'assistant', 'tool_calls':calls} if protocol == 'chat' else
                             execution_view({'status':'completed', 'output':native}))
                g = {'id':identity, 'assistant':assistant, 'results':results, 'fresh':True}
                if configuration is not None:
                    g['configurations'] = [{'index':0, 'arguments':configuration}]
                return g

            def compile(current, restart=None):
                request = context.compile('SYSTEM', 'ORIGINAL USER REQUIREMENTS', [], 'fixture', {}, current,
                    restart, request_transform=request_body if protocol == 'responses' else None)
                assert context.tokens(request) <= context.limit
                manifest = store.record(context.context_id)
                assert json.loads(store.verify(manifest['request_blob']).read_text()) == request
                return request

            return run, repo, store, context, tools, group, compile

        run, repo, store, context, tools, group, compile = setup('restore')
        text = 'original 🙂 capture, not the changed file\n'*12
        (repo/'file.txt').write_text(text)
        first = group([tools.read('file.txt', output_chars=17)])
        compile(first)
        shown = copy.deepcopy(first['results'][0]['output'])
        original_receipt = store.record(first['results'][0]['result_record'])
        second = group([tools.read('file.txt')])
        compile(second)
        assert first['id'] in context.masked and not first['results'][0]['output']['text_parts']
        (repo/'file.txt').write_text('changed file must NOT appear in restoration')
        control = group([{'context_change':'pending'}], {'mask_at_tokens':0})
        request = compile(control)
        assert not context.masked
        assert first['results'][0]['output']['text_parts'] == shown['text_parts']
        assert first['results'][0]['output']['shown_byte_ranges'] == shown['shown_byte_ranges']
        assert second['results'][0]['output']['text_parts'] == [text]
        assert store.record(first['results'][0]['result_record']) == original_receipt
        assert 'prior_request_shown_byte_ranges' not in first['results'][0]['output']
        (run/'disabled-request.json').write_text(dump(request))
        verified.append(protocol+': disable restores surviving pre-mask bounded views, not changed file; original receipt unchanged')

        # Re-enable, then reject a combined policy change: tentative restoration must not commit.
        compile(group([{'context_change':'pending'}], {'mask_at_tokens':1}))
        assert first['id'] in context.masked
        rejected = group([{'context_change':'pending'}], {'mask_at_tokens':0, 'pressure_at_tokens':999999})
        compile(rejected)
        assert rejected['results'][0]['status'] == 'rejected'
        assert context.mask_at == 1 and first['id'] in context.masked
        assert not first['results'][0]['output']['text_parts']
        # Raising recency protects this suffix; its already masked body is rehydrated.
        keep = len(context.groups)+1
        request = compile(group([{'context_change':'pending'}], {'keep_recent':keep}))
        assert context.mask_at == 1 and not context.masked
        assert first['results'][0]['output']['text_parts'] == shown['text_parts']
        (run/'recent-request.json').write_text(dump(request))
        # Replacement removes history. Subsequent configuration is not an undo of replacement.
        replacement = group([{'context_change':'pending'}])
        compile(replacement, {'index':0, 'arguments':{'keep_records':[]}})
        compile(group([{'context_change':'pending'}], {'mask_at_tokens':0}))
        assert first['id'] not in {g['id'] for g in context.groups}
        assert len(context.groups) == 2
        verified.append(protocol+': rejected configuration retains masks; recency increase restores suffix; restart removal is irreversible')

        run, repo, store, context, tools, group, compile = setup('capacity')
        (repo/'large.txt').write_text('large capture\n'*2400)
        large = group([tools.read('large.txt', range='full', output_chars=None)])
        compile(large)
        assert large['results'][0]['output']['display_complete']
        compile(group([tools.read('large.txt', range={'bytes':[0,14]})]))
        assert large['id'] in context.masked
        context.input_limit, context.pressure_at = 10000, 9000
        control = group([{'context_change':'pending'}], {'mask_at_tokens':0})
        request = compile(control)
        output = large['results'][0]['output']
        assert control['results'][0]['status'] == 'completed'
        assert not context.masked and output['text_parts']
        assert not output['display_complete'] and output['omission_reason'] == 'request_capacity'
        assert 'prior_request_shown_byte_ranges' not in output
        assert Path(output['path']).read_text() == 'large capture\n'*2400
        (run/'paged-restoration-request.json').write_text(dump(request))
        bounded = copy.deepcopy(output['shown_byte_ranges'])
        # A later capacity increase must not erase the committed paging bound.
        context.input_limit, context.pressure_at = 60000, 50000
        compile(group([{'context_change':'pending'}], {'mask_at_tokens':1}))
        assert large['id'] in context.masked
        compile(group([{'context_change':'pending'}], {'mask_at_tokens':0}))
        assert large['results'][0]['output']['shown_byte_ranges'] == bounded
        verified.append(protocol+': restoration obeys hard capacity with truthful paging and unchanged immutable capture')
    (root/'coverage.json').write_text(dump({'verified':verified, 'live_provider_proof':False})+'\n')
    print('\n'.join(verified))


if __name__ == '__main__':
    main(sys.argv[1])
