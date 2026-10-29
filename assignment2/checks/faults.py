"""Run as an unprivileged user with an isolated small capture tmpfs."""
import json, os
from pathlib import Path
from mads.store import Store,EvidenceError
from mads.tools import Tools
from mads.context import Context

if __name__ == '__main__':
    root=Path('/work');repo=root/'repo';repo.mkdir()
    store=Store(root/'run/journal');tools=Tools(repo,root/'run',store)
    tools.jobs.root=Path('/capture')
    job=tools.jobs.start("python3 -u -c 'import os;\nwhile True: os.write(1,b\"x\"*65536)'",repo)
    assert tools.jobs.fault.wait(10),'Expected isolated tmpfs exhaustion'
    # The failure flag is published before the reader terminates the group. Wait for
    # that owned cleanup, rather than racing its first signal with an exit assertion.
    assert tools.jobs.jobs[job]['capture_done'].wait(5),'Capture failure cleanup did not finish'
    state,artifact=tools.jobs.observe(job,0)
    assert state['capture_status']=='incomplete' and state['capture_error']
    assert state['state']=='exited'
    print('O8',json.dumps({'state':state,'artifact':artifact}),flush=True)
    store.add('invocation',tool_name='write',path=str(repo/'effect'))
    os.chmod(store.blobs,0o500)
    try:
     tools.write('effect','mutation succeeded before diff recording')
    except EvidenceError as exc:
     assert tools.effects=='file_replaced'
     assert (repo/'effect').read_text()=='mutation succeeded before diff recording'
     print('F4',json.dumps({'effects':tools.effects,'exception':str(exc),'actual_file':(repo/'effect').read_text()}),flush=True)
    else:raise AssertionError('Expected real recording failure')
    finally:os.chmod(store.blobs,0o700)
    c=Context(store,root/'run/memory')
    c.working.write_text('account before permission failure')
    c.compile('system','original',[],'model',{})
    os.chmod(c.working,0)
    request=c.compile('system','original',[],'model',{})
    facts=json.loads(request['messages'][-1]['content'].split('\n',1)[1].removesuffix('</tool_response>'))
    assert facts['working_account']['state']=='stale'
    print('M2-unreadable',request['messages'][-1]['content'],flush=True)
    os.chmod(c.working,0o600)
    print('cleanup',json.dumps(tools.jobs.close()),flush=True)

    import shutil
    shutil.copytree('/work','/evidence/work',dirs_exist_ok=True)
    shutil.copytree('/capture','/evidence/capture',dirs_exist_ok=True)
