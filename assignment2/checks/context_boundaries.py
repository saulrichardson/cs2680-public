"""Compiler boundary checks with real files and literal protocol data, not provider claims."""
import json,sys
from pathlib import Path
from mads.store import Store, view
from mads.context import Context,CapacityBlocked
from mads.tools import TOOLS

if __name__ == '__main__':
    root=Path(sys.argv[1]).resolve();root.mkdir(parents=True)
    store=Store(root/'journal')
    ctx=Context(store,root/'memory',input_limit=20000,total_limit=26000,output_tokens=4000,mask_at=9000,pressure_at=14000)
    passed=[]
    def group(results,reasoning='native reasoning'):
     identity=store.add('literal_compiler_fixture')
     calls=[]
     for i,r in enumerate(results):
      r.update(call_id=f'{identity}-{i}',status=r.get('status','completed'))
      calls.append({'id':r['call_id'],'type':'function','function':{'name':'read','arguments':'{}'}})
     return {'id':identity,'assistant':{'role':'assistant','reasoning_content':reasoning,'tool_calls':calls},'results':results,'fresh':True}
    def compile(current=None,restart=None):
     return ctx.compile('system','ORIGINAL TASK UNCHANGED',TOOLS,'model',{},current,restart)
    def account(request):
     tail=request['messages'][-1]
     facts=(json.loads(tail['content'])['harness_runtime'] if tail['role']=='tool' else
            json.loads(tail['content'].split('\n',1)[1].removesuffix('</tool_response>')))
     return facts['working_account']
    raw='first contradiction\n'+'X'*180000+'\nlast'
    a=store.artifact(store.blob(raw));record=store.evidence(a,kind='test_observation')
    ctx.working.write_text('account initial')
    first=group([{'observation_record':record,'output':view(a,output_chars=12000)}]);compile(first)
    ctx.working.write_text('account after siblings')
    current=group([{'context_change':'pending'},{'output':view(a,output_chars=None)}])
    req=compile(current,{'index':0,'arguments':{'keep_records':[{'record_id':record,'view':{'bytes':[0,20]}}]}})
    assert len(ctx.groups)==1 and account(req)['text']=='account after siblings'
    assert json.loads(req['messages'][-2]['content'])['context_change']=='applied'
    assert json.loads(req['messages'][-1]['content'])['output']['display_status'] != 'complete'
    assert ctx.tokens(req)<ctx.limit
    passed.append('C1-compiler')
    previous=[g['id'] for g in ctx.groups];selection=list(ctx.records)
    current=group([{'context_change':'pending'}])
    req=compile(current,{'index':0,'arguments':{'keep_records':[{'record_id':record,'view':'full'}]}})
    assert [g['id'] for g in ctx.groups][:-1]==previous and ctx.records==selection
    assert json.loads(req['messages'][-1]['content'])['context_change']=='unchanged'
    assert req['messages'][1]['content']=='ORIGINAL TASK UNCHANGED'
    passed.append('C2')
    ctx.keep_recent=1;ctx.mask_at=1
    req=compile(group([{'output':view(a,{'bytes':[0,20]},20)}]))
    # Explicit selection protects the selected observation; a new retrieval is recent regardless of source age.
    new=json.loads(req['messages'][-1]['content'])
    assert ''.join(new['output']['text_parts'])==raw[:20]
    assert first['assistant']['reasoning_content']=='native reasoning'
    assert any('first contradiction' in m['content'] for m in req['messages'] if m['role']=='user')
    masked_ids=[g['id'] for g in ctx.groups if g['id'] in ctx.masked]
    assert masked_ids
    for g in ctx.groups:
     if g['id'] in masked_ids:
      assert g['assistant'] in req['messages']
      for r in g['results']:
       if 'output' in r:
        assert not r['output']['text_parts'] and Path(r['output']['path']).read_bytes()==raw.encode()
    passed.extend(['C3','C4'])
    try:compile(group([],reasoning='native'*100000))
    except CapacityBlocked:passed.append('C5')
    else:raise AssertionError('Irreducible reasoning must block')
    # Missing and invalid UTF-8 account, then recovery. Permission failure is exercised in faults.py.
    ctx2=Context(store,root/'memory2')
    ctx2.working.write_text('last fitting')
    ctx2.compile('system','task',TOOLS,'model',{})
    ctx2.working.write_bytes(b'\xff')
    r=ctx2.compile('system','task',TOOLS,'model',{})
    assert account(r)['state']=='stale'
    ctx2.working.unlink();r=ctx2.compile('system','task',TOOLS,'model',{})
    assert account(r)['state']=='missing'
    ctx2.working.write_text('recreated');r=ctx2.compile('system','task',TOOLS,'model',{})
    assert account(r)['text']=='recreated'
    passed.append('M2-file-states')
    # A previously delivered full result must not make the next ordinary exchange fail.
    ctx3=Context(store,root/'memory3',input_limit=12000,total_limit=20000,output_tokens=4000,mask_at=0,pressure_at=10000)
    large=group([{'output':view(a,output_chars=None)}])
    ctx3.compile('system','task',TOOLS,'model',{},large)
    shown=sum(len(s) for s in large['results'][0]['output']['text_parts'])
    next_group=group([],reasoning='new reasoning '*900)
    req=ctx3.compile('system','task',TOOLS,'model',{},next_group)
    assert len(ctx3.groups)==2 and large['assistant'] in req['messages']
    assert sum(len(s) for s in large['results'][0]['output']['text_parts'])<shown
    assert ctx3.tokens(req)<=ctx3.limit
    assert any(e['event']=='exchange_representation' for e in store.events)
    passed.append('O4-next-exchange')
    (root/'coverage.json').write_text(json.dumps({'verified':passed,'provider_integration':False},indent=2))
    print(passed)
