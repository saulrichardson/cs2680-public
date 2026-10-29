"""Real output/range recovery and literal causal-link checks; no provider claims."""
import base64
import copy
import json
import shlex
import subprocess
import sys
from pathlib import Path

from mads.logs import request_chain
from mads.store import Store, dump, present
from mads.tools import Tools


def main(root):
    root = Path(root).resolve()
    root.mkdir(parents=True)
    repo = root/'repo'
    repo.mkdir()
    store = Store(root/'run/journal')
    tools = Tools(repo,root/'run',store)
    verified, examples = [], {}

    def shown(result, allowance=12000):
        descriptor = result['output']
        before = copy.deepcopy(descriptor)
        rendered = present(descriptor,allowance)
        assert descriptor == before, 'Presentation must not rewrite authoritative observations'
        assert list(rendered)[:2] == ['observation_scope','text_parts']
        return rendered

    def recover(descriptor, allowance):
        recovered = b''
        for _ in range(1000):
            rendered = present(descriptor,allowance)
            text = ''.join(rendered['text_parts'])
            recovered += base64.b64decode(text) if rendered['encoding'] == 'base64' else text.encode()
            if rendered['display_complete']:
                return recovered
            descriptor = tools.read(**rendered['continuation'],output_chars=allowance)['output']
        raise AssertionError('Continuation did not make progress')

    try:
        small = tools.bash("printf 'failure on line 1\ndetail on line 2\n'; exit 1",2)
        examples['small_failure'] = shown(small)
        assert small['process']['shell_exit_code'] == 1
        assert small['process']['capture_status'] == 'complete'
        assert examples['small_failure']['text_parts'] == ['failure on line 1\ndetail on line 2\n']
        assert examples['small_failure']['artifact_display_complete']
        empty = shown(tools.bash('true',2))
        metadata = shown({**small,'output':{**small['output'],'output_chars':0}})
        assert empty['display_status'] == 'empty_artifact'
        assert metadata['display_status'] == 'metadata_only' and not metadata['display_complete']
        assert empty['observation_scope']['next_read'] is None
        assert metadata['observation_scope']['unshown_byte_ranges'] == [[0,small['output']['observed_bytes']]]
        selected = shown(tools.read(small['output']['path'],range={'bytes':[0,7]}))
        assert selected['display_complete'] and not selected['artifact_display_complete']
        assert shown(tools.read(small['output']['path'],range={'bytes':[7,7]}))['display_status'] == 'empty_selection'
        verified.append('small full output; empty, zero presentation and selected completeness are distinct')

        # Fully displaying a selected prefix must not imply observing the full file.
        original = ('PREFIX\n'+('0123456789'*2000))[:19329].encode()
        (repo/'scope.txt').write_bytes(original)
        prefix = shown(tools.read('scope.txt',range={'bytes':[0,500]}))
        scope = prefix['observation_scope']
        assert prefix['display_complete'] and not prefix['artifact_display_complete']
        assert 'Selected only prefix [0,500) of 19329 captured bytes' in scope['statement']
        assert '18829 captured bytes are not displayed in this observation' in scope['statement']
        assert scope['unshown_byte_ranges'] == [[500,19329]] and 'continuation' not in prefix
        assert scope['next_read'] == {'path':prefix['path'],'range':{'bytes':[500,19329]},'encoding':'utf8'}
        (repo/'scope.txt').write_text('CHANGED AFTER CAPTURE')
        assert recover(tools.read(**scope['next_read'],output_chars=1000)['output'],1000) == original[500:]
        assert shown(tools.read('scope.txt',range='full'))['text_parts'] == ['CHANGED AFTER CAPTURE']
        assert 'file snapshot of' in scope['statement']
        middle = shown(tools.read(prefix['path'],range={'bytes':[100,200]}))
        assert middle['observation_scope']['unshown_byte_ranges'] == [[0,100],[200,19329]]
        assert 'only an interior interval [100,200)' in middle['observation_scope']['statement']
        assert 'did not take a new source snapshot' in middle['observation_scope']['statement']
        suffix = shown(tools.read(prefix['path'],range={'bytes':[19000,19329]}))
        assert 'only suffix [19000,19329)' in suffix['observation_scope']['statement']
        masked = present(prefix,0,'historical_output_masked')
        assert masked['text_parts'] == [] and masked['observation_scope']['unshown_byte_ranges'] == [[0,19329]]
        assert masked['observation_scope']['statement'] == ('read: No content displayed (historical output masked). '
            'Selected only prefix [0,500) of 19329 captured bytes. next_read retrieves saved bytes.')
        assert masked['observation_scope']['next_read']['range'] == {'bytes':[0,500]}
        assert masked['source_path'] == prefix['source_path'] and masked['observed_at'] == prefix['observed_at']
        assert recover(tools.read(**masked['observation_scope']['next_read'])['output'],1000) == original[:500]
        restored = present(masked,12000)
        assert restored['text_parts'] == prefix['text_parts']
        assert 'omission_reason' not in restored and restored['observation_scope'] == scope
        examples.update(selected_prefix=prefix,selected_middle=middle,selected_suffix=suffix,masked_prefix=masked,
                        empty_output=empty,metadata_only=metadata)
        verified.append('primary scope distinguishes selected prefix/middle/suffix from full artifact; immutable next_read survives source mutation and masking')

        long_line = ('HEAD\n'+'a'*20000+'🙂β'*3000+'\r\nTAIL\n').encode()
        (repo/'long.txt').write_bytes(long_line)
        read = tools.read('long.txt',range='full',output_chars=73)
        assert recover(read['output'],73) == long_line
        preview = present({**read['output'],'preview_tail':True},73)
        assert preview['text_parts'][0].startswith('HEAD') and preview['text_parts'][-1].endswith('TAIL\n')
        for part,(a,b) in zip(preview['text_parts'],preview['shown_byte_ranges']):
            assert part.encode() == long_line[a:b]
        assert recover(tools.read(**preview['continuation'],output_chars=73)['output'],73) == long_line[slice(*preview['omitted_byte_ranges'][0])]
        examples['long_line_preview'] = preview
        verified.append('long line and multibyte sequential continuation; exact head/tail omission recovery')

        binary = b'valid\xffinvalid\xf0\x9f'
        (repo/'binary').write_bytes(binary)
        invalid = shown(tools.read('binary',range='full'))
        assert invalid['display_status'] == 'partial' and invalid['text_parts'] == ['valid']
        assert invalid['shown_byte_ranges'] == [[0,5]]
        assert invalid['continuation']['encoding'] == 'base64'
        assert invalid['observation_scope']['next_read'] == invalid['continuation']
        assert ''.join(invalid['text_parts']).encode()+recover(
            tools.read(**invalid['continuation'],output_chars=12)['output'],12) == binary
        (repo/'split').write_bytes('A🙂B'.encode())
        split = shown(tools.read('split',range={'bytes':[1,3]}))
        assert split['decoding_issue']
        assert recover(tools.read(**split['continuation'],output_chars=4)['output'],4) == 'A🙂B'.encode()[1:3]
        examples['invalid_utf8'] = invalid
        masked_invalid = present(invalid,0,'historical_output_masked')
        assert masked_invalid['display_status'] == 'metadata_only' and 'decoding_issue' not in masked_invalid
        assert 'historical output masked' in masked_invalid['observation_scope']['statement']
        verified.append('invalid UTF-8 and selected codepoint fragment continue with exact base64 bytes')

        code = "import sys;sys.stdout.write('FIRST\\n');sys.stdout.flush();sys.stdin.readline();sys.stdout.write('SECOND🙂\\n');sys.stdout.flush()"
        first = tools.bash(shlex.quote(sys.executable)+' -u -c '+shlex.quote(code),.2)
        assert shown(first)['text_parts'] == ['FIRST\n'] and first['process']['state'] == 'running'
        assert 'process running, capture in_progress' in shown(first)['observation_scope']['statement']
        first_bytes = Path(first['output']['path']).read_bytes()
        receipt = tools.send(first['process']['job_id'],'🙂\n',True)
        assert list(receipt)[0] == 'receipt' and receipt['accepted_bytes'] == 5
        assert 'accepted 5 UTF-8 bytes' in receipt['receipt'] and 'does not establish delivery or consumption' in receipt['receipt']
        assert receipt['eof_requested'] and receipt['next_poll'] == {'job_id':first['process']['job_id']}
        second = tools.poll(first['process']['job_id'],2,output_chars=4)
        page = shown(second)
        assert page['selected_byte_range'] == [0,len('SECOND🙂\n'.encode())]
        assert page['selected_job_byte_range'] == [len(first_bytes),len(first_bytes)+len('SECOND🙂\n'.encode())]
        for local,job in zip(page['shown_byte_ranges'],page['shown_job_byte_ranges']):
            assert job == [n+len(first_bytes) for n in local]
        historical = shown(tools.history_read(second['observation_record']))
        assert historical['job_byte_range'] == second['output']['job_byte_range']
        assert historical['capture_status'] == 'complete'
        assert historical['text_parts'] == ['SECOND🙂\n']
        assert 'Historical captured observation from journal record' in historical['observation_scope']['statement']
        assert 'log bytes [6,17)' in historical['observation_scope']['statement']
        assert 'process exited, capture complete' in historical['observation_scope']['statement']
        assert Path(first['output']['path']).read_bytes() == first_bytes
        live = tools.read(second['process']['live_path'],range='full')
        assert live['read_kind'] == 'new_live_process_snapshot'
        assert shown(live)['text_parts'] == ['FIRST\nSECOND🙂\n']
        assert 'file snapshot of' in shown(live)['observation_scope']['statement']
        assert 'log bytes [0,17)' in shown(live)['observation_scope']['statement']
        examples['polled_interval'] = page
        examples['historical_polled_interval'] = historical
        examples['running_interval'] = shown(first)
        examples['send_receipt'] = receipt
        examples['live_process_snapshot'] = shown(live)
        verified.append('real input-gated polls preserve snapshots and distinguish interval-local/process-log coordinates')

        # A real receiver closes stdin while its process continues running.
        closed_code = "import os,time;os.close(0);print('INPUT CLOSED',flush=True);time.sleep(3)"
        closed = tools.bash(shlex.quote(sys.executable)+' -u -c '+shlex.quote(closed_code),.2)
        assert 'INPUT CLOSED' in ''.join(shown(closed)['text_parts'])
        closed_job = closed['process']['job_id']
        accepted = tools.send(closed_job,'undelivered🙂\n')
        failed = tools.poll(closed_job,.2)
        pending = len('undelivered🙂\n'.encode())
        assert accepted['accepted_bytes'] == pending and failed['process']['stdin_pending_bytes'] == pending
        assert failed['process']['stdin_error'] and Path(failed['process']['undelivered_input']['path']).read_bytes() == 'undelivered🙂\n'.encode()
        failed_view = shown(failed)
        failure_fact = (f"Recorded stdin error: {failed['process']['stdin_error']}; "
                        f"{pending} input bytes pending at this observation.")
        assert failure_fact in failed_view['observation_scope']['statement']
        failed_history = shown(tools.history_read(failed['observation_record']))
        assert failure_fact in failed_history['observation_scope']['statement']
        failed_live = shown(tools.read(failed['process']['live_path'],range='full'))
        assert failure_fact in failed_live['observation_scope']['statement']
        failed_masked = present(failed_live,0,'historical_output_masked')
        assert failure_fact in failed_masked['observation_scope']['statement']
        assert 'Recorded stdin error' not in shown(first)['observation_scope']['statement']
        tools.cancel(closed_job)
        examples.update(stdin_failure=failed_view,stdin_failure_historical=failed_history,
                        stdin_failure_live=failed_live,stdin_failure_masked=failed_masked)
        verified.append('real closed stdin retains exact queued bytes and prominently reports only recorded failure facts across poll/history/live/masked views')

        changed = shown(tools.write('written.txt','ONE\n'))
        edited = shown(tools.edit('written.txt','ONE','TWO'))
        no_change = shown(tools.write('written.txt','TWO\n'))
        assert 'write: File-change diff for' in changed['observation_scope']['statement']
        assert 'edit: File-change diff for' in edited['observation_scope']['statement']
        assert no_change['text_parts'] == [] and 'diff for' in no_change['observation_scope']['statement']
        examples.update(write_diff=changed,edit_diff=edited,empty_diff=no_change)
        verified.append('write/edit observations identify captured diffs, including empty diffs, without claiming task verification')

        # Literal causality fixture. Bodies have distinct bytes and roles; no API call is implied.
        manifest = store.add('context_manifest',account={'record':first['observation_record']})
        request = store.request({'messages':[{'role':'user','content':'REQUEST UNIQUE'}]},manifest)
        http = store.add('http_request',request_record=request,blob=store.blob('HTTP UNIQUE'))
        response = store.add('response',request_record=request,http_request_record=http,blob=store.blob('RESPONSE UNIQUE'))
        assistant = store.add('assistant',request_record=request,response_record=response,blob=store.blob('ASSISTANT UNIQUE'))
        exchange = store.add('exchange_started',assistant_record=assistant)
        invocation = store.add('invocation',assistant_record=assistant,exchange_id=exchange,tool_name='read')
        observed = tools.read('long.txt',range={'lines':[1,1]})
        result = store.add('tool_result',invocation_id=invocation,result=observed)
        following = store.request({'messages':[]},manifest)
        following_response = store.add('response',request_record=following,blob=store.blob('UNRELATED RESPONSE'))
        ids = request_chain(store.events,request)
        assert {manifest,request,http,response,assistant,exchange,invocation,observed['observation_record'],result} <= ids
        assert following not in ids and following_response not in ids
        related = tools.history_read(request)
        assert related['output']['payload_kind'] == 'compiled_api_request'
        assert {http,response,assistant} <= {r['record_id'] for r in related['related_records']}
        assert tools.history_read(response)['output']['payload_kind'] == 'returned_http_body'
        assert tools.history_read(response,part='record')['output']['payload_kind'] == 'journal_event_record'
        body = shown(tools.history_read(response))
        assert 'Historical returned http body from journal record' in body['observation_scope']['statement']
        search = shown(tools.history_search(query='UNIQUE',limit=1))
        assert 'One page of literal historical journal search results' in search['observation_scope']['statement']
        examples.update(historical_response=body,history_search_page=search)
        process = subprocess.run([sys.executable,'-m','mads.logs',str(store.root),'--request',str(request),'--full'],check=True,capture_output=True)
        text = process.stdout.decode()
        assert 'RESPONSE UNIQUE' in text and 'ASSISTANT UNIQUE' in text and f'#{result} ' in text
        assert 'UNRELATED RESPONSE' not in text
        raw = subprocess.run([sys.executable,'-m','mads.logs',str(store.root),'--event',str(response),'--raw'],check=True,capture_output=True)
        assert raw.stdout == b'RESPONSE UNIQUE'
        (root/'request-chain.txt').write_bytes(process.stdout)
        verified.append('literal request/HTTP/response/assistant/invocation/outcome/evidence links; exact raw viewer output')
    finally:
        cleanup = tools.jobs.close()
    assert all(not item['errors'] and item['process']['capture_status'] == 'complete' for item in cleanup)
    (root/'examples.json').write_text(json.dumps(examples,ensure_ascii=False,indent=2))
    (root/'coverage.json').write_text(json.dumps({'verified':verified,'provider_integration':False,'cleanup':cleanup},indent=2))
    print(dump({'verified':verified,'evidence':str(root)}))


if __name__ == '__main__':
    main(sys.argv[1])
