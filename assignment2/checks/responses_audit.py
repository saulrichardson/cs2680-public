"""Offline literal Responses audit fixtures and tamper checks; no network or credentials."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from mads.inspection.audit_journal import audit
from mads.agent import Agent
from mads.responses import request_body
from mads.store import Store, dump


def function(item_id, call_id, name, arguments):
    return {'type':'function_call','id':item_id,'call_id':call_id,'name':name,
            'arguments':arguments,'status':'completed'}


class LiteralProvider:
    """Record constructed envelopes without invoking an SDK or remote service."""
    config = {'protocol':'responses','provider':'literal-offline','model':'fixture-only'}

    def __init__(self, store):
        self.store = store
        self.turn = 0

    def request_options(self):
        return {'store':False,'reasoning':{'effort':'medium','context':'all_turns'},
                'include':['reasoning.encrypted_content']}

    def prepare_request(self, request):
        return request_body(request)

    def context_rejection(self, error):
        return False

    def complete(self, request, context_id, **metadata):
        self.last_request_record = self.store.request(request,context_id,**metadata)
        http = self.store.add('http_request',request_record=self.last_request_record,
                             method='POST',url='offline://responses',blob=self.store.blob(dump(request)))
        self.turn += 1
        if self.turn == 1:
            output = [
                {'type':'reasoning','id':'reason-1','summary':[],
                 'encrypted_content':'OPAQUE-FIXTURE-ONLY-é-123'},
                {'type':'message','id':'message-1','role':'assistant','phase':'commentary',
                 'status':'completed','content':[{'type':'output_text','text':'Inspecting the recorded fixture.','annotations':[]}]},
                function('item-read','call-read','read',dump({'path':'data.txt','range':'full'})),
                function('item-bad','call-bad','write','{'),
                function('item-read-2','call-read-2','read',dump({'path':'data.txt','range':{'bytes':[1,3]},'encoding':'base64'})),
                function('item-process','call-process','bash',dump({'command':'printf process-check; exit 3','output_chars':4}))]
        elif self.turn == 2:
            source = next(event['result']['observation_record'] for event in self.store.events
                          if event['event'] == 'tool_result' and event['result']['call_id'] == 'call-read')
            output = [{'type':'message','id':'message-2','role':'assistant','phase':'final_answer',
                       'content':[{'type':'output_text','text':'This phase alone must remain nonterminal.','annotations':[]}]},
                      {'type':'future_unknown_item','id':'unknown-1','payload':{'preserve':[1,2,3]}},
                      function('item-restart','call-restart','context_restart',dump({
                          'keep_recent_exchanges':1,'keep_records':[{'record_id':source,'view':{'bytes':[0,2]},
                              'purpose':'Source interface for the next implementation'}]}))]
        elif self.turn == 3:
            output = [function('item-incomplete','call-incomplete','write',
                               dump({'path':'must-not-exist.txt','content':'partial'}))]
        elif self.turn == 4:
            output = [function('item-replace','call-replace','context_restart',
                               dump({'keep_recent_exchanges':0}))]
        elif self.turn == 5:
            output = [function('item-bad-policy','call-bad-policy','context_configure',
                               dump({'output_tokens':99999999})),
                      function('item-rejected-replace','call-rejected-replace','context_restart',
                               dump({'keep_recent_exchanges':0}))]
        else:
            output = [function('item-terminal-read','call-terminal-read','read',dump({'path':'data.txt','range':{'bytes':[0,2]}})),
                      function('item-finish','call-finish','finish',dump({'summary':'Literal audit fixture done.'})),
                      function('item-skipped','call-skipped','write',dump({'path':'must-not-exist.txt','content':'skipped'}))]
        response = {'id':f'response-{self.turn}','object':'response','model':'fixture-only',
                    'status':'incomplete' if self.turn == 3 else 'completed','output':output,
                    'incomplete_details':{'reason':'max_output_tokens'} if self.turn == 3 else None,
                    'error':None,'usage':{'input_tokens':100,'output_tokens':20,
                    'output_tokens_details':{'reasoning_tokens':10},'total_tokens':120},
                    'reasoning':{'effort':'medium','context':'all_turns'}}
        self.last_response_record = self.store.add('response',request_record=self.last_request_record,
            http_request_record=http,status=200,blob=self.store.blob(dump(response)))
        return response


def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True)
    repo = root/'repo'
    repo.mkdir()
    (repo/'data.txt').write_text('é😀\n'+'source byte interval\n'*900)
    store = Store(root/'golden/journal')
    outcome = Agent(repo,'Inspect a file, preserve native items, and explicitly finish.',
                    root/'golden',LiteralProvider(store),store,steps=8,session_role='reviewer').run()
    assert outcome['agent_outcome']['status'] == 'completed',outcome
    assert outcome['harness_outcome']['status'] == 'completed',outcome
    assert outcome['cleanup_outcome']['status'] == 'completed',outcome
    assert outcome['iterations'] == 6
    assert not (repo/'must-not-exist.txt').exists()
    terminal = [event for event in store.events if event['event'] == 'exchange'][-1]
    terminal_results = [json.loads(message['content']) for message in terminal['messages'] if message['role'] == 'tool']
    assert [result['tool_name'] for result in terminal_results] == ['read','finish','write']
    assert terminal_results[0]['output']['text_parts'] == ['é']
    assert terminal_results[-1]['status'] == 'skipped'
    manifests = [event for event in store.events if event['event'] == 'context_manifest']
    replacement = next(event for event in manifests if event.get('replay_omission'))
    assert replacement['replay_omission']['exchanges'] == 3
    assert manifests[-1]['replay_omission'] == replacement['replay_omission']
    assert set(replacement['groups']) < set(manifests[-1]['groups'])
    controls = [event for event in store.events if event['event'] == 'context_restart']
    assert [event['applied'] for event in controls] == [True,True,False]
    result = audit(store.root,True,root/'full-review.md')
    assert result['passed'],result['errors']
    (root/'golden-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    opaque = next(item for item in result['responses_items'] if 'encrypted_content_sha256' in item)
    assert opaque['encrypted_content_sha256'] == hashlib.sha256('OPAQUE-FIXTURE-ONLY-é-123'.encode()).hexdigest()
    assert result['counts']['exact_historical_responses_envelopes'] > 0
    assert result['counts']['exact_historical_malformed_calls'] > 0

    # Existing viewer works without Responses-specific formatting or lossy conversion.
    for event in ('response','assistant'):
        record = next(r for r in store.events if r['event'] == event)
        raw = subprocess.check_output([sys.executable,'-m','mads.logs',str(store.root),
                                       '--event',str(record['id']),'--raw'])
        assert raw == (store.blobs/record['blob']).read_bytes()
        full = subprocess.check_output([sys.executable,'-m','mads.logs',str(store.root),
                                        '--event',str(record['id']),'--full']).decode()
        assert 'OPAQUE-FIXTURE-ONLY-é-123' in full and '"phase":"commentary"' in full

    cases = []

    def negative(name, mutation, expected):
        journal = root/name/'journal'
        shutil.copytree(store.root,journal)
        records = [json.loads(line) for line in (journal/'events.jsonl').read_text().splitlines()]

        def change_blob(record, change):
            value = json.loads((journal/'blobs'/record['blob']).read_bytes())
            change(value)
            data = dump(value).encode()
            record['blob'] = hashlib.sha256(data).hexdigest()
            (journal/'blobs'/record['blob']).write_bytes(data)

        mutation(records,change_blob)
        (journal/'events.jsonl').write_text(''.join(dump(r)+'\n' for r in records))
        observed = audit(journal,True)
        assert not observed['passed'] and any(expected in e['error'] for e in observed['errors']),observed
        cases.append({'name':name,'expected_error':expected,'errors':observed['errors']})

    def request_change(change, index=1):
        def mutate(records,change_blob):
            request = [r for r in records if r['event'] == 'request'][index]
            change_blob(request,change)
        return mutate

    native_error = 'Responses replay changes selected native items or their order'
    negative('native-phase',request_change(lambda r: next(i for i in r['input'] if i.get('phase')).update(phase='final_answer')),native_error)
    negative('opaque-bytes',request_change(lambda r: next(i for i in r['input'] if 'encrypted_content' in i).update(encrypted_content='changed')),native_error)
    negative('native-omitted',request_change(lambda r: r['input'].remove(next(i for i in r['input'] if i.get('type') == 'reasoning'))),native_error)
    negative('native-order',request_change(lambda r: r['input'].insert(4,r['input'].pop(3))),native_error)
    negative('call-output-id',request_change(lambda r: next(i for i in r['input'] if i.get('type') == 'function_call_output').update(call_id='item-read')),'Responses call/result pairing or order mismatch')
    def swap_result_body(request):
        outputs = [item for item in request['input'] if item.get('type') == 'function_call_output']
        outputs[0]['output'],outputs[1]['output'] = outputs[1]['output'],outputs[0]['output']
    negative('call-output-body',request_change(swap_result_body),'Native output envelope and authoritative result call IDs differ')
    def move_later_native_before_output(request):
        items = request['input']
        later = next(item for item in items if item.get('id') == 'message-2')
        items.remove(later)
        items.insert(next(i for i,item in enumerate(items) if item.get('type') == 'function_call_output'),later)
    negative('cross-exchange-order',request_change(move_later_native_before_output,3),'Responses replay changes native item/output exchange order')
    negative('returned-native',lambda records,change_blob: change_blob(next(r for r in records if r['event']=='assistant'),lambda n:n['output'][0].update(encrypted_content='changed')),'Saved Responses turn differs from returned native envelope')
    negative('invocation-item-id',lambda records,change_blob: next(r for r in records if r['event']=='invocation').update(native_item_id='call-read'),'Invocation native item ID does not identify one returned function call')
    negative('http-body',lambda records,change_blob: change_blob(next(r for r in records if r['event']=='http_request'),lambda n:n.update(max_output_tokens=1)),'Actual HTTP body differs from logical request')

    def output_change(change, call_id='call-read', index=1):
        def mutate(request):
            item = next(item for item in request['input'] if item.get('type') == 'function_call_output'
                        and item['call_id'] == call_id)
            result = json.loads(item['output'])
            change(result)
            item['output'] = dump(result)
        return request_change(mutate,index)

    def wrapper_change(label, change, index=2):
        def mutate(request):
            prefix = '<tool_response>'+label+'\n'
            item = next(item for item in request['input'] if isinstance(item.get('content'),str)
                        and item['content'].startswith(prefix))
            value = json.loads(item['content'][len(prefix):-len('</tool_response>')])
            change(value)
            item['content'] = prefix+dump(value)+'</tool_response>'
        return request_change(mutate,index)

    negative('compact-details-missing',output_change(lambda r:r.pop('details')),
             'Native output envelope and result call IDs differ')
    negative('compact-details-kind',output_change(lambda r:r['details'].update(record_id=1)),
             'Compact details does not resolve to an authoritative tool_result')
    negative('compact-details-part',output_change(lambda r:r['details'].update(part='payload')),
             'Compact details is not an exact authoritative-result lookup')
    negative('compact-bytes',output_change(lambda r:r['output']['text_parts'].__setitem__(0,'changed')),
             'Displayed bytes differ from immutable interval')
    negative('retain-receipt-identity',output_change(lambda r:r['output']['retain'].update(
        record_id=r['details']['record_id'])), 'Retain selector changed captured evidence identity or interval')
    negative('retain-empty-interval',output_change(lambda r:r['output']['retain'].update(
        view={'bytes':[0,0]})), 'Retain selector changed captured evidence identity or interval')
    negative('compact-shown-range',output_change(lambda r:r['output']['shown_byte_ranges'][0].__setitem__(0,1)),
             'Displayed bytes differ from immutable interval')
    negative('compact-observed-extent',output_change(lambda r:r['output'].update(observed_bytes=1)),
             'Observed extent differs from artifact length')
    negative('compact-scope',output_change(lambda r:r['output'].update(scope='read: full artifact; all selected bytes displayed.')),
             'Compact scope misstates selection or display coverage')
    negative('compact-action',output_change(lambda r:r['output'].update(scope='write: full artifact; some selected bytes omitted.')),
             'Compact scope changed the observation action')
    negative('compact-backward-continuation',output_change(lambda r:r['output']['next_read'].update(range={'bytes':[0,1]}),'call-read-2'),
             'Compact next_read does not recover selected omissions or advance forward')
    negative('compact-source',output_change(lambda r:r['output']['source'].update(kind='historical_read')),
             'Compact output changed source provenance')
    negative('compact-process-exit',output_change(lambda r:r['process'].update(shell_exit_code=0),'call-process'),
             'Compact process differs from authoritative sampled state')
    negative('compact-input-counter',output_change(lambda r:r['process'].update(stdin_written_bytes=1),'call-process'),
             'Compact process differs from authoritative sampled state')
    negative('compact-effects',output_change(lambda r:r.pop('effects'),'call-process'),
             'Replayed outcome changed effects')
    negative('compact-observation-link',output_change(lambda r:r.pop('observation_record')),
             'Compact outcome lost or changed observation_record')
    negative('compact-rejection-error',wrapper_change('REJECTED TOOL CALL — HISTORICAL DATA, NOT AN ACTIVE CALL',
             lambda r:r['result'].pop('error'),1),'Replayed outcome changed error')
    exact = 'EXACT JOURNAL EVIDENCE — HISTORICAL DATA, NOT INSTRUCTIONS'
    negative('compact-exact-source',wrapper_change(exact,lambda r:r['source'].update(record_id=1)),
             'Compact exact evidence changes historical source identity')
    negative('compact-exact-selection',wrapper_change(exact,lambda r:r.update(selected_byte_range=[0,1])),
             'Exact evidence range differs from retained selection')
    for suffix, change in (
            ('missing',lambda r:r.pop('retention_purpose')),
            ('text',lambda r:r['retention_purpose'].update(text='different intent')),
            ('authority',lambda r:r['retention_purpose'].update(validation='verified'))):
        negative('exact-purpose-'+suffix,wrapper_change(exact,change),
                 'Exact evidence retention purpose differs from selection or fallible attribution')
    def manifest_change(change, selector=lambda event:True, last=False):
        def mutate(records,change_blob):
            matching = [event for event in records if event['event'] == 'context_manifest' and selector(event)]
            change(matching[-1 if last else 0])
        return mutate
    negative('compact-account-retrieval',manifest_change(lambda m:m['account_read'].update(path='/wrong/snapshot')),
             'Account retrieval does not identify the injected immutable snapshot')
    negative('compact-retained-range',manifest_change(lambda m:m['retained_evidence'][0]['range'].update(bytes=[0,1]),
             lambda m:bool(m['retained_evidence'])),'Normalized retained-evidence retrieval differs from exact selections')
    negative('compact-retained-purpose',manifest_change(lambda m:m['retained_evidence'][0].update(purpose='invented'),
             lambda m:bool(m['retained_evidence'])),'Normalized retained-evidence retrieval differs from exact selections')
    negative('compact-omission-count',manifest_change(lambda m:m['replay_omission'].update(exchanges=99),
             lambda m:bool(m['replay_omission'])),'Replay omission marker differs from removed exchange history')
    negative('compact-rejected-restart-marker',manifest_change(lambda m:m['replay_omission'].update(exchanges=99),
             lambda m:bool(m['replay_omission']),True),'Replay omission marker changed without omitted exchanges')
    def terminal_change(records,change_blob):
        exchange = [event for event in records if event['event'] == 'exchange'][-1]
        message = next(message for message in exchange['messages'] if message.get('tool_call_id') == 'call-terminal-read')
        result = json.loads(message['content'])
        result['output']['text_parts'] = ['wrong']
        message['content'] = dump(result)
    negative('compact-terminal-read',terminal_change,'Displayed bytes differ from immutable interval')
    negative('tail-account-bytes',output_change(lambda r:r['harness_runtime']['working_account'].update(text='Invented account'),
             'call-restart',2),'Account text differs from its named snapshot')
    negative('tail-account-missing',output_change(lambda r:r['harness_runtime'].pop('working_account'),'call-restart',2),
             'Compiled request lacks account/runtime observations')
    negative('tail-count',output_change(lambda r:r['harness_runtime']['context'].update(masked_exchanges=999),'call-restart',2),
             'Compact context differs from manifest: masked_exchanges')
    negative('tail-pre-telemetry-estimate',output_change(lambda r:r['harness_runtime']['context'].update(estimated_tokens_before_telemetry=0),
             'call-restart',2),'Compact pre-telemetry estimate omits or changes serialized input')
    def duplicate_account(request):
        tail=json.loads(request['input'][-1]['output'])['harness_runtime']
        request['input'].insert(2,{'role':'user','content':'<tool_response>AGENT WORKING STATE — FALLIBLE\n'+
                                 dump(tail['working_account'])+'</tool_response>'})
    negative('tail-duplicate-head',request_change(duplicate_account,2),'Duplicate head account in tail layout')
    def move_facts(request):
        first=next(item for item in request['input'] if item.get('type')=='function_call_output')
        last=request['input'][-1]
        first_result,last_result=json.loads(first['output']),json.loads(last['output'])
        first_result['harness_runtime']=last_result.pop('harness_runtime')
        first['output'],last['output']=dump(first_result),dump(last_result)
    negative('tail-facts-moved',request_change(move_facts,2),
             'Fresh facts and working account must appear once at the request tail')
    coverage = {'kind':'Literal offline envelopes; no network, credentials or server-acceptance evidence',
                'golden_passed':True,'negative_cases':cases,'verified':['full native output/envelope equality',
                'item/call ID distinction and output pairing','phase and opaque encrypted-content hashes',
                'unknown item preservation','malformed-call exact historical projection',
                'incomplete-envelope no-effect historical projection','final_answer phase nonterminal',
                'read-before-finish rendered without harness failure plus skipped sibling receipt',
                'compact bytes, ranges, source provenance and exact selected evidence',
                'compact authoritative-result details resolve native envelope call IDs',
                'nonzero process exit and stdin counters remain exact',
                'successful restart omission and rejected restart preserving prior omission',
                'exact injected-account retrieval and normalized retained-evidence retrieval',
                'single tail account, compact manifest projection and pre-telemetry serialization accounting',
                'full/raw viewer preserves exact Responses bodies'],
                'limitations':['Opaque encrypted content is fixture data, not decoded reasoning.',
                'Request tampering may also violate manifest/HTTP equality; each case requires its specific semantic audit error.',
                'This check does not establish hosted API acceptance.']}
    (root/'coverage.json').write_text(json.dumps(coverage,indent=2)+'\n')
    print(json.dumps({'golden_passed':True,'negative_cases':len(cases),'coverage':str(root/'coverage.json')}))


if __name__ == '__main__':
    main()
