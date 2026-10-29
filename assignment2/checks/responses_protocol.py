"""Offline production-loop Responses checks with real files and SDK MockTransport."""
import copy
import json
import os
import sys
from pathlib import Path

import httpx
from openai import OpenAI

from mads.agent import Agent
from mads.provider import Provider
from mads.responses import execution_view, native_turn
from mads.store import Store, dump


def call(name, arguments, identity):
    return {'type':'function_call','id':'item-'+identity,'call_id':'call-'+identity,
            'name':name,'arguments':dump(arguments),'status':'completed'}


def reasoning(identity):
    return {'type':'reasoning','id':'reason-'+identity,'summary':[],
            'encrypted_content':'opaque-fixture-'+identity+'-+/=','status':'completed'}


def message(text, identity, phase='commentary'):
    return {'type':'message','id':'message-'+identity,'role':'assistant','status':'completed',
            'phase':phase,'content':[{'type':'output_text','text':text,'annotations':[]}]}


def response(items, identity, **overrides):
    input_tokens=10000 if identity=='one' else 100
    return {'id':'response-'+identity,'object':'response','status':'completed','output':items,
            'usage':{'input_tokens':input_tokens,'output_tokens':20,'total_tokens':input_tokens+20,
                     'output_tokens_details':{'reasoning_tokens':10}},
            'reasoning':{'effort':'medium','context':'all_turns','mode':'standard'},**overrides}


def exercise(root, replies):
    root.mkdir(parents=True,exist_ok=False)
    repo=root/'repo';repo.mkdir();run=root/'run';store=Store(run/'journal')
    os.environ['MADS_RESPONSES_FIXTURE_KEY']='nonsecret-offline-fixture'
    config={'provider':'openai','protocol':'responses','model':'gpt-6.1-sol',
            'base_url':'https://offline.invalid/v1','key_env':'MADS_RESPONSES_FIXTURE_KEY'}
    provider=Provider(store,config=config)
    provider.close()
    bodies,returned=[],[]
    def receive(request):
        body=json.loads(request.content);bodies.append(body)
        reply=replies(len(bodies),store,repo,run)
        returned.append(copy.deepcopy(reply))
        return httpx.Response(200,json=reply,request=request)
    provider._client=OpenAI(api_key='nonsecret-offline-fixture',base_url=config['base_url'],
        max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(receive),
        event_hooks={'request':[provider._sent],'response':[provider._received]}))
    try:
        # Explicit adapter-only fixture; normal solve-review has separate lifecycle coverage.
        agent=Agent(repo,'Exercise ordinary tools; only finish completes.',run,provider,store,
                    session_role='reviewer',steps=8,input_limit=80000,total_limit=100000,output_tokens=4000,
                    mask_at=1,pressure_at=25000)
        agent.context.keep_recent=1
        outcome=agent.run()
    finally:
        provider.close()
    assert outcome['harness_outcome']['status']=='completed',outcome
    assert outcome['cleanup_outcome']['status']=='completed'
    assert all(e.get('matches_compiled_request') for e in store.events if e['event']=='http_request')
    for entry in (e for e in store.events if e['event']=='http_request'):
        request_record=store.record(entry['request_record'])
        manifest=store.record(request_record['context_id'])
        assert (store.blobs/entry['blob']).stat().st_size==manifest['bytes']
    for entry in (e for e in store.events if e['event']=='context_manifest'):
        body=json.loads((store.blobs/entry['request_blob']).read_text())
        assert 'input' in body and 'messages' not in body
        assert entry['bytes']==len(dump(body).encode())
        assert body['max_output_tokens']==4000
        assert body['store'] is False and body['reasoning']=={'effort':'medium','context':'all_turns','mode':'standard'}
        assert body['service_tier']=='default'
        assert all(tool['strict'] is False for tool in body['tools'])
        read=next(t for t in body['tools'] if t['name']=='read')
        assert read['parameters']['required']==['path']
    return store,repo,bodies,returned,outcome


def main(directory):
    root=Path(directory).resolve();root.mkdir(parents=True,exist_ok=False)
    verified=[]
    def normal(n,store,repo,run):
        if n==1:
            # Force upward calibration from the actual compiled size, rather
            # than assuming a fixed token count exceeds every future toolset.
            manifest=next(e for e in reversed(store.events) if e['event']=='context_manifest')
            input_tokens=int(manifest['bytes']*.5)+1
            return response([reasoning('one'),message('Inspect, then continue.','one'),
                call('write',{'path':str(repo/'effect.txt'),'content':'COMMITTED'},'write'),
                call('write',{'path':str(run/'memory/working.md'),'content':'Useful account'},'account')],'one',
                usage={'input_tokens':input_tokens,'output_tokens':20,'total_tokens':input_tokens+20,
                       'output_tokens_details':{'reasoning_tokens':10}})
        if n==2:
            return response([reasoning('two'),message('This text is nonterminal.','two','final_answer')],'two')
        if n==3:
            result=next(e['result'] for e in store.events if e['event']=='tool_result' and e['result']['call_id']=='call-write')
            return response([reasoning('three'),call('read',{'path':str(repo/'effect.txt')},'read'),
                call('context_restart',{'keep_recent_exchanges':1,
                     'keep_records':[{'record_id':result['observation_record'],'view':{'bytes':[0,5]}}]},'restart')],'three')
        assert n==4
        return response([message('Checked the retained context.','four'),
            call('finish',{'summary':'Fixture completed'},'finish'),
            call('write',{'path':str(repo/'skipped.txt'),'content':'NO'},'skipped')],'four')
    store,repo,bodies,replies,outcome=exercise(root/'continuity',normal)
    assert (repo/'effect.txt').read_text()=='COMMITTED' and not (repo/'skipped.txt').exists()
    assert outcome['iterations']==4 and outcome['total_tokens']==sum(r['usage']['total_tokens'] for r in replies)
    manifests=[e for e in store.events if e['event']=='context_manifest']
    assert manifests[0]['tokens_per_byte']==.4 and manifests[1]['tokens_per_byte']>.4
    assert replies[0]['output']==[x for x in bodies[1]['input'] if x.get('id') in {i['id'] for i in replies[0]['output']}]
    assert replies[1]['output']==[x for x in bodies[2]['input'] if x.get('id') in {i['id'] for i in replies[1]['output']}]
    assert not any(x.get('id') in {i['id'] for i in replies[0]['output']} for x in bodies[3]['input'])
    assert all(any(x==item for x in bodies[3]['input']) for item in replies[1]['output']+replies[2]['output'])
    functions=[x for x in bodies[1]['input'] if x.get('type')=='function_call_output']
    assert [x['call_id'] for x in functions]==['call-write','call-account']
    masked=[json.loads(x['output']) for x in bodies[2]['input'] if x.get('type')=='function_call_output']
    assert all(r['output']['omission_reason']=='historical_output_masked' for r in masked)
    assert 'Useful account' in dump(bodies[3]) and 'EXACT JOURNAL EVIDENCE' in dump(bodies[3])
    assert len([e for e in store.events if e['event']=='invocation' and e['tool_name']=='write'])==3
    assert all(e['native_item_id'].startswith('item-') for e in store.events if e['event']=='invocation')
    assert any(e['event']=='context_restart' and e['applied'] for e in store.events)
    verified.extend(['finalized-wire-body-and-optional-schema-preserved','all-native-output-items-phase-and-encrypted-content-replayed',
        'function-item-id-distinct-from-call-id','tool-output-masking-with-native-history-intact',
        'nonterminal-final-answer-phase','account-and-exact-evidence-restart-without-effect-replay',
        'explicit-finish-skips-later-effects','Responses-input-usage-calibration'])

    def invalid(n,store,repo,run):
        if n==1:
            return response([reasoning('cutoff'),call('write',{'path':str(repo/'never1'),'content':'NO'},'cutoff')],
                            'cutoff',status='incomplete',incomplete_details={'reason':'max_output_tokens'})
        if n==2:
            duplicate=call('write',{'path':str(repo/'never2'),'content':'NO'},'duplicate')
            sibling=copy.deepcopy(duplicate);sibling['call_id']='distinct-call-same-native-id'
            return response([reasoning('duplicate'),duplicate,sibling],'duplicate')
        if n==3:
            bad=call('write',{},'bad');bad['arguments']='{"path":'
            return response([reasoning('mixed'),message('Reject only the invalid call.','mixed'),bad,
                call('write',{'path':str(repo/'once'),'content':'ONCE'},'valid')],'mixed')
        assert n==4
        return response([call('finish',{'summary':'Rejected invalid inputs safely'},'end')],'end')
    store,repo,bodies,replies,outcome=exercise(root/'rejections',invalid)
    assert not (repo/'never1').exists() and not (repo/'never2').exists() and (repo/'once').read_text()=='ONCE'
    assert len([e for e in store.events if e['event']=='response_rejected'])==2
    for n in (0,1):
        assert dump(native_turn(replies[n])) in next(x['content'] for x in bodies[n+1]['input']
            if x.get('role')=='user' and 'invalid_response_envelope' in x.get('content','') and dump(native_turn(replies[n])) in x['content'])
    retained=[x for x in bodies[3]['input'] if x.get('id') in {i['id'] for i in replies[2]['output']}]
    assert retained==[i for i in replies[2]['output'] if i.get('call_id')!='call-bad']
    assert 'invalid_json_object_arguments' in dump(bodies[3])
    assert not any(x.get('call_id')=='call-bad' for x in bodies[3]['input'])
    results=[e['result'] for e in store.events if e['event']=='tool_result']
    assert results[0]['status']=='rejected' and results[0]['effects']=='none'
    assert results[1]['status']=='completed'
    verified.extend(['incomplete-envelope-no-effects-and-exact-historical-rejection',
                     'malformed-envelope-no-effects-and-exact-historical-rejection',
                     'duplicate-native-item-id-rejection-before-effects',
                     'invalid-argument-item-explicit-historical-projection-valid-siblings-native'])
    duplicate_call=call('read',{'path':'a'},'duplicate-call')
    duplicate_sibling=copy.deepcopy(duplicate_call);duplicate_sibling['id']='other-native-id'
    for invalid_output in ([None],[{'type':'message','role':'assistant','content':'not-blocks'}],
                           [duplicate_call,duplicate_sibling],
                           [reasoning('same'),message('same native ID','same') | {'id':'reason-same'}],
                           [dict(call('read',{'path':'a'},'missing'),id=None)]):
        try:execution_view(native_turn(response(invalid_output,'invalid')))
        except ValueError:pass
        else:raise AssertionError('Invalid envelope accepted')
    verified.append('invalid-native-envelope-shapes-rejected-before-effects')
    (root/'coverage.json').write_text(json.dumps({'verified':verified,'provider_integration':False,
        'transport':'Offline SDK MockTransport; no network or model POSTs'},indent=2))
    print(json.dumps({'verified':verified,'provider_integration':False,'evidence':str(root)}))


if __name__=='__main__':
    main(sys.argv[1])
