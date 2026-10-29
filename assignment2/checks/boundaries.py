"""Real filesystem/process exercises. Compiler inputs are literal data, not fake provider responses."""
import base64
import hashlib
import json
import os
import shlex
import sys
import time
from pathlib import Path
from mads.store import Store, present, view, dump
from mads.tools import Tools, TOOLS
from mads.context import Context


def main(root):
    root=Path(root).resolve();root.mkdir(parents=True)
    repo=root/'repo';repo.mkdir()
    store=Store(root/'run/journal');tools=Tools(repo,root/'run',store)
    passed=[]
    def done(*ids):
        passed.extend(ids);print('Verified',','.join(ids),flush=True)
    def command(code):
        return shlex.quote(sys.executable)+' -u -c '+shlex.quote(code)
    def output(result,cap=12000):
        return present(result['output'],cap)
    short=tools.bash("printf 'short failure\n'; exit 1",2)
    assert short['process']['shell_exit_code']==1
    assert output(short)['text_parts']==['short failure\n']
    done('O1')
    noisy=tools.bash(command("import sys;sys.stdout.write('START\\n'+'x'*180000+'MIDDLE FAILURE\\n'+'z'*180000+'END\\n')"),2,output_chars=200)
    artifact=noisy['output'];full=Path(artifact['path']).read_bytes()
    page=output(noisy,200)
    assert 'START' in page['text_parts'][0] and 'END' in page['text_parts'][-1]
    assert 'MIDDLE' not in ''.join(page['text_parts'])
    for cap in (0,80,400000):
        descriptor={**artifact,'output_chars':cap}
        shown=present(descriptor,500000)
        assert sum(len(s) for s in shown['text_parts'])<=cap
        assert Path(shown['path']).read_bytes()==full
    full_view=present({**artifact,'output_chars':None},500000)
    assert full_view['display_complete'] and ''.join(full_view['text_parts']).encode()==full
    done('O2','O3')
    weird=('a'*30000+'🙂'*5000+'\r\nβ\nlast\r\n').encode()
    (repo/'weird').write_bytes(weird)
    first=tools.read('weird',range={'lines':[1,1]},output_chars=199)
    recovered=b'';descriptor=first['output']
    while True:
        shown=present(descriptor,199)
        recovered+=''.join(shown['text_parts']).encode()
        if shown['display_complete']:break
        continuation=shown['continuation']
        descriptor=tools.read(**continuation,output_chars=199)['output']
    assert recovered==weird[:weird.index(b'\n')+1]
    done('O6')
    binary=b'\x00\xff\xc3'+bytes(range(256))*40
    (repo/'binary').write_bytes(binary)
    shown=output(tools.read('binary',range='full'))
    assert shown['decoding_issue']
    descriptor=tools.read('binary',range='full',encoding='base64',output_chars=200)['output'];recovered=b''
    while True:
        shown=present(descriptor,200)
        recovered+=base64.b64decode(''.join(shown['text_parts']))
        if shown['display_complete']:break
        descriptor=tools.read(**shown['continuation'],output_chars=200)['output']
    assert recovered==binary
    done('O7')
    slow=tools.bash(command("import os,time;os.write(1,b'A\\xf0\\x9f');time.sleep(.2);os.write(1,b'\\x99\\x82B');time.sleep(.2);os.write(1,b'C')"),0)
    captured=b'';snapshots=[]
    while True:
        artifact=slow['output'];raw=Path(artifact['path']).read_bytes();captured+=raw
        snapshots.append((artifact['path'],raw))
        shown=output(slow)
        assert not shown.get('decoding_issue')
        if slow['process']['state']=='exited':break
        slow=tools.poll(slow['process']['job_id'],.05)
    assert captured=='A🙂BC'.encode()
    assert all(Path(p).read_bytes()==data for p,data in snapshots)
    done('O5')
    tools.bash('mkdir sub; cd sub; export MADS_TEST_VALUE=private; touch persisted',2)
    shell=tools.bash('printf "%s:%s" "$PWD" "${MADS_TEST_VALUE-unset}"; test -f sub/persisted',2)
    assert ''.join(output(shell)['text_parts'])==str(repo)+':unset'
    done('P1')
    key_names=('CS2680_API_KEY','OPENROUTER_API_KEY','OPENAI_API_KEY')
    previous={name:os.environ.get(name) for name in key_names}
    try:
        os.environ.update({name:'dummy-offline-credential' for name in key_names})
        child_env=tools.bash(command('import os; print(any(name in os.environ for name in '+repr(key_names)+'))'),2)
        assert output(child_env)['text_parts']==['False\n']
    finally:
        for name,value in previous.items():
            if value is None:os.environ.pop(name,None)
            else:os.environ[name]=value
    done('provider-key-environment')
    child=tools.bash('(sleep 1; printf CHILD) & exit 0',.1)
    assert child['process']['state']=='running' and child['process']['shell_exit_code']==0
    child=tools.poll(child['process']['job_id'],2)
    assert output(child)['text_parts']==['CHILD'] and child['process']['capture_status']=='complete'
    done('P2')
    data='α🙂x'*60000
    reader=tools.bash(command("import sys,hashlib;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"),0)
    sent=tools.send(reader['process']['job_id'],data,True)
    assert sent['accepted_bytes']==len(data.encode())
    reader=tools.poll(reader['process']['job_id'],5)
    assert ''.join(output(reader)['text_parts']).strip()==hashlib.sha256(data.encode()).hexdigest()
    done('P3')
    early=tools.bash(command('import time;time.sleep(.2)'),0)
    sent=tools.send(early['process']['job_id'],'Z'*2000000,True)
    early=tools.poll(early['process']['job_id'],2)
    # Process/capture completion does not synchronize the input worker. Wait
    # for its independent broken-pipe observation; never resend accepted data.
    deadline=time.monotonic()+2
    while not early['process']['stdin_error'] and time.monotonic()<deadline:
        time.sleep(.01)
        early=tools.poll(early['process']['job_id'],0)
    assert early['process']['stdin_error'] and early['process']['stdin_pending_bytes']>0
    assert Path(early['process']['undelivered_input']['path']).stat().st_size==early['process']['stdin_pending_bytes']
    done('P4')
    long=tools.bash('sleep 30 & wait',0)
    assert tools.cancel(long['process']['job_id'])['process']['state']=='exited'
    timed=tools.bash('sleep 30',2,.1)
    assert timed['process']['termination_reason']=='timeout' and timed['process']['state']=='exited'
    done('P5')
    (repo/'edit').write_bytes(b'a\r\na\r\n');os.chmod(repo/'edit',0o755)
    for old in ('missing','a'):
        try:tools.edit('edit',old,'b')
        except ValueError:pass
        else:raise AssertionError('Expected edit rejection')
    tools.edit('edit','a','b',True)
    assert (repo/'edit').read_bytes()==b'b\r\nb\r\n'
    (repo/'link').symlink_to('edit')
    tools.edit('link','b\r\nb','c\r\nb')
    assert (repo/'edit').stat().st_mode&0o777==0o755 and (repo/'link').is_symlink()
    before=tools.read('edit');tools.bash("printf 'new state' > edit",2)
    try:tools.edit('edit','c\r\nb','stale')
    except ValueError:pass
    else:raise AssertionError('Expected stale literal rejection')
    tools.write('link','replacement')
    assert (repo/'edit').read_text()=='replacement' and (repo/'link').is_symlink()
    done('F1','F2','F3')
    ctx=Context(store,root/'run/memory',input_limit=18000,total_limit=24000,output_tokens=4000,mask_at=12000,pressure_at=15000)
    ctx.working.write_text('working v1')
    req=ctx.compile('system','ORIGINAL TASK',TOOLS,'model',tools.jobs.status())
    assert 'working v1' in req['messages'][2]['content']
    ctx.working.unlink();req=ctx.compile('system','ORIGINAL TASK',TOOLS,'model',tools.jobs.status())
    assert '"state":"missing"' in req['messages'][2]['content']
    ctx.working.write_text('working v2');ctx.compile('system','ORIGINAL TASK',TOOLS,'model',tools.jobs.status())
    ctx.working.write_text('oversized'*100000)
    req=ctx.compile('system','ORIGINAL TASK',TOOLS,'model',tools.jobs.status())
    assert '"state":"stale"' in req['messages'][2]['content'] and 'working v2' in req['messages'][2]['content']
    ctx.working.write_text('working v3')
    (ctx.memory/'support.md').write_text('SUPPORT SECRET '*30000)
    req=ctx.compile('system','ORIGINAL TASK',TOOLS,'model',tools.jobs.status())
    assert 'SUPPORT SECRET' not in dump(req)
    assert 'SUPPORT SECRET' in ''.join(output(tools.read(str(ctx.memory/'support.md')))['text_parts'])
    done('M1','M3')
    # Literal native envelopes exercise compilation; this is not provider integration evidence.
    invocation=store.add('invocation',tool_name='history_read')
    result={'call_id':'boundary','status':'completed','result_record':invocation,
            'output':view(artifact=noisy['output'],output_chars=None)}
    native={'role':'assistant','tool_calls':[{'id':'boundary','type':'function','function':{'name':'history_read','arguments':'{}'}}]}
    group={'id':invocation,'assistant':native,'results':[result],'fresh':True}
    req=ctx.compile('system','ORIGINAL TASK',TOOLS,'model',tools.jobs.status(),group)
    last=json.loads(req['messages'][-1]['content'])
    assert last['output']['display_status'] != 'complete' and last['output']['next_read']
    assert ctx.tokens(req)<ctx.limit
    # Page the whole artifact to prove all omitted bytes remain accessible.
    recovered=b'';descriptor=view(noisy['output'],output_chars=None)
    while True:
        shown=present(descriptor,10000);recovered+=''.join(shown['text_parts']).encode()
        if shown['display_complete']:break
        descriptor=tools.read(**shown['continuation'],output_chars=None)['output']
    assert recovered==full
    done('O4')
    head=len(store.events)
    a=store.search('',event='evidence',head=head,limit=2)
    store.add('after_search')
    b=store.search(**a['continuation'])
    assert a['head']==b['head']==head and not set(x['record_id'] for x in a['matches'])&set(x['record_id'] for x in b['matches'])
    request_record=store.request(req,ctx.context_id)
    original=tools.history_read(request_record,part='payload',output_chars=None)
    assert json.loads(''.join(output(original,100000)['text_parts']))==req
    done('H2')
    cleanup=tools.jobs.close()
    assert all(not row['errors'] and row['process']['capture_status']=='complete' for row in cleanup)
    (root/'coverage.json').write_text(json.dumps({'verified':passed,'partial':['M2','H1'],
        'note':'No provider integration claimed by this script','cleanup':cleanup},indent=2))
    print('Boundary exercise completed:',len(passed),'scenarios')


if __name__=='__main__':
    main(sys.argv[1])
