"""Read-only inspector fixtures, no model dispatch."""
import json
from pathlib import Path
import tempfile
from mads.inspection.inspect_run import Journal, sha

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp); journal=root/'run/journal'; blobs=journal/'blobs'; blobs.mkdir(parents=True)
        def blob(value):
            data=value.encode() if isinstance(value,str) else json.dumps(value).encode()
            key=sha(data); (blobs/key).write_bytes(data); return key
        account=blob('Current action: implement café.\n')
        request=blob({'model':'test','input':[{'role':'user','content':'original'}, {'type':'reasoning','encrypted_content':'opaque'}], 'tools':[{'type':'function','name':'search'}]})
        rows=[{'id':1,'event':'context_manifest','account_record':99,'account_read':{'path':'/run/journal/blobs/'+account}},
              {'id':2,'event':'request','context_id':1,'blob':request},
              {'id':3,'event':'http_request','request_record':2,'blob':request},
              {'id':4,'event':'invocation','tool_name':'search','raw_arguments':'{}'},
              {'id':5,'event':'response','blob':blob({'usage':{'input_tokens':8,'output_tokens':2,'total_tokens':10}})}]
        path=journal/'events.jsonl'; path.write_text(''.join(json.dumps(e)+'\n' for e in rows)+'{"partial":')
        before={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        j=Journal(root)
        assert j.incomplete_tail_bytes == 11 and len(j.events)==5
        assert j.summary()['tools']=={'search':1}
        assert j.summary()['recorded_response_usage']['total_tokens']==10
        assert j.timeline()[0]['tool_name']=='search'
        assert 'café' in j.accounts()[0]['diff']
        r=j.request(); assert r['request_record']==3 and r['tool_names']==['search']
        assert 'opaque' not in json.dumps(r)
        assert before=={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        out=root/'export'; j.request(out=out); assert (out/'request.json').read_bytes()==(blobs/request).read_bytes()
        assert (out/'account.md').read_text()=='Current action: implement café.\n'
        try: j.request(out=out)
        except FileExistsError: pass
        else: raise AssertionError('must not overwrite an export')
        (blobs/request).write_text('{}')
        try: j.request()
        except ValueError as e: assert 'integrity' in str(e)
        else: raise AssertionError('must verify blobs')
    print('inspection: read-only, live tail, usage, account diff, exact export and integrity checks passed')
