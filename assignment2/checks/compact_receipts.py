"""Compact wire receipts retain exact content and recovery; no provider calls."""
import base64
import copy
import json
import shlex
import sys
from pathlib import Path

from mads.store import Store, compact_output, compact_process, compact_result, dump, present, view
from mads.tools import Tools


def main(directory):
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = root / 'repo'
    repo.mkdir()
    store = Store(root / 'run/journal')
    tools = Tools(repo, root / 'run', store)
    checks, examples, sizes = [], {}, {}

    def call(name, *args, **kwargs):
        # Match Agent.batch's per-invocation reset, so a read cannot inherit
        # a previous cancellation's effect in these direct Tools fixtures.
        tools.effects = 'none'
        return getattr(tools, name)(*args, **kwargs)

    def output_check(full):
        before = copy.deepcopy(full)
        compact = compact_output(full)
        assert full == before
        assert list(compact)[:2] == ['scope', 'text_parts']
        assert compact['text_parts'] == full['text_parts']
        for key in ('encoding', 'observed_bytes', 'selected_byte_range', 'shown_byte_ranges', 'display_status'):
            assert compact[key] == full[key]
        path = compact.get('path') or compact['next_read']['path']
        assert path == full['path']
        captured = Path(path).read_bytes()
        for text, (a,b) in zip(compact['text_parts'], compact['shown_byte_ranges']):
            decoded = base64.b64decode(text) if compact['encoding'] == 'base64' else text.encode('utf-8')
            assert decoded == captured[a:b]
        assert compact.get('next_read') == full['observation_scope']['next_read']
        assert not ({'continuation', 'byte_retrieval', 'observation_scope', 'eof'} & compact.keys())
        if compact.get('next_read'):
            assert 'path' not in compact
        return compact

    def receipt(name, raw, cap=12000):
        authoritative = copy.deepcopy(raw)
        authoritative.update(invocation_id=500, call_id='native-call', native_item_id='native-item',
                             tool_name=name, status='running' if raw.get('process',{}).get('state') == 'running'
                             else 'completed', effects=tools.effects)
        identity = store.add('tool_result', invocation_id=500, call_id='native-call', result=authoritative)
        full = copy.deepcopy(authoritative)
        full['result_record'] = identity
        full['output'] = present(full['output'], cap)
        before = copy.deepcopy(full)
        compact = compact_result(full)
        assert full == before
        assert compact['details'] == {'record_id': identity, 'part': 'record'}
        assert store.record(compact['details']['record_id'])['result'] == authoritative
        assert not {'invocation_id', 'call_id', 'native_item_id', 'result_record'} & compact.keys()
        for key in ('tool_name', 'status', 'effects'):
            assert compact[key] == full[key]
        validated = output_check(full['output'])
        assert compact['output']['text_parts'] == validated['text_parts']
        start, end = full['output']['selected_byte_range']
        if start < end and full['output']['encoding'] == 'utf8' and 'decoding_issue' not in full['output']:
            selector = compact['output']['retain']
            expected_id = full['output'].get('record_id', full.get('observation_record'))
            assert selector == {'record_id':expected_id, 'part':full['output'].get('part','payload'),
                                'view':{'bytes':[start,end]}}
            assert selector['record_id'] != compact['details']['record_id']
            selected = store.history(selector['record_id'], selector['part'])
            assert Path(selected['path']).read_bytes()[start:end] == Path(full['output']['path']).read_bytes()[start:end]
        else:
            assert 'retain' not in compact['output']
        examples[name] = {'full': full, 'compact': compact}
        sizes[name] = {'full_bytes':len(dump(full).encode()), 'compact_bytes':len(dump(compact).encode())}
        assert sizes[name]['compact_bytes'] < sizes[name]['full_bytes']
        return full, compact

    try:
        code = "import sys,time; print('READY',flush=True); sys.stdin.readline(); print('ACK',flush=True); time.sleep(2)"
        ready = call('bash', shlex.quote(sys.executable) + ' -u -c ' + shlex.quote(code), .15)
        full_ready, compact_ready = receipt('bash', ready)
        assert compact_ready['output']['text_parts'] == ['READY\n']
        assert compact_ready['process']['state'] == 'running'
        assert compact_ready['process']['capture_status'] == 'in_progress'
        assert 'full artifact; all selected bytes displayed' in compact_ready['output']['scope']
        assert compact_ready['output']['source']['job_byte_range'] == [0,6]
        sent = call('send', ready['process']['job_id'], 'once\n', True)
        sent.update(tool_name='send', status='completed', effects='input_accepted')
        sent['result_record'] = store.add('tool_result', result=copy.deepcopy(sent))
        compact_sent = compact_result(sent)
        assert compact_sent['accepted_bytes'] == 5
        assert not {'receipt','pending_bytes','eof_requested'} & compact_sent.keys()
        for key, value in sent.items():
            if key.startswith('stdin_') and not (key == 'stdin_error' and value is None):
                assert compact_sent[key] == value
        assert 'stdin_error' not in compact_sent
        error_receipt = {**sent, 'stdin_error':'Broken pipe', 'receipt':sent['receipt']+' Input error: Broken pipe'}
        assert compact_result(error_receipt)['stdin_error'] == 'Broken pipe'
        assert 'receipt' not in compact_result(error_receipt)
        custom_receipt = {**sent, 'receipt':'Custom diagnostic must survive', 'pending_bytes':999}
        assert compact_result(custom_receipt)['receipt'] == custom_receipt['receipt']
        assert compact_result(custom_receipt)['pending_bytes'] == 999
        examples['send'] = {'full':sent,'compact':compact_sent}
        sizes['send'] = {'full_bytes':len(dump(sent).encode()), 'compact_bytes':len(dump(compact_sent).encode())}
        _, compact_poll = receipt('poll', call('poll', ready['process']['job_id'], .2))
        assert compact_poll['output']['text_parts'] == ['ACK\n']
        assert compact_poll['output']['source']['job_byte_range'] == [6,10]
        assert compact_poll['output']['retain']['view'] == {'bytes':[0,4]}
        call('cancel', ready['process']['job_id'])
        checks.append('real running READY: complete display stays distinct from producer/capture state')
        checks.append('send retains per-call accepted count and exact cumulative input states; only equal aliases/known prose removed')

        data = ('PREFIX\n' + '0123456789' * 2000)[:19329].encode()
        (repo / 'large.txt').write_bytes(data)
        prefix, compact_prefix = receipt('read', call('read', 'large.txt', range={'bytes':[0,500]}))
        assert prefix['effects'] == compact_prefix['effects'] == 'none'
        assert compact_prefix['output']['scope'] == 'read: only a prefix; all selected bytes displayed.'
        assert compact_prefix['output']['observed_bytes'] == 19329
        assert compact_prefix['output']['selected_byte_range'] == [0,500]
        assert compact_prefix['output']['next_read']['range'] == {'bytes':[500,19329]}
        partially_shown = {**prefix, 'output':present(prefix['output'], 9)}
        selected = compact_result(partially_shown)['output']
        assert selected['shown_byte_ranges'] == [[0,9]]
        assert selected['retain']['view'] == {'bytes':[0,500]}
        (repo / 'large.txt').write_text('source changed after captured observation')
        suffix = present(call('read', **compact_prefix['output']['next_read'], output_chars=None)['output'], 30000)
        assert ''.join(suffix['text_parts']).encode() == data[500:]
        assert output_check(suffix).get('next_read') is None
        assert suffix['observation_scope']['unshown_byte_ranges'] == [[0,500]]
        empty = present(view(store.artifact(prefix['output']['artifact_id']), {'bytes':[19329,19329]}), 100)
        assert output_check(empty).get('next_read') is None
        checks.append('prefix→suffix→stop uses immutable bytes despite source edit; completed suffix/EOF never wraps backward')

        artifact = store.artifact(store.blob(b'ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
        descriptor = view(artifact, {'bytes':[5,10]}, output_chars=2)
        recovered, intervals = b'', []
        for _ in range(30):
            rendered = present(descriptor, 2)
            compact = output_check(rendered)
            recovered += ''.join(compact['text_parts']).encode()
            intervals.extend(compact['shown_byte_ranges'])
            if not compact.get('next_read'):
                break
            descriptor = call('read', **compact['next_read'], output_chars=2)['output']
        else:
            raise AssertionError('Forward navigation did not terminate')
        assert recovered == b'FGHIJKLMNOPQRSTUVWXYZ'
        assert intervals[0][0] == 5 and intervals[-1][1] == 26
        assert all(a[1] == b[0] for a,b in zip(intervals,intervals[1:]))
        checks.append('partial interior selection is recovered before forward suffix, without gaps/repeats')

        masked = present(prefix['output'], 0, 'historical_output_masked')
        compact_masked = output_check(masked)
        assert compact_masked['text_parts'] == [] and 'no content displayed' in compact_masked['scope']
        assert compact_masked['omission_reason'] == 'historical_output_masked'
        assert compact_masked['next_read']['range'] == {'bytes':[0,500]}
        assert compact_result({**prefix,'output':masked})['output']['retain'] == compact_prefix['output']['retain']
        examples['masked'] = {'full':masked, 'compact':compact_masked}
        empty_artifact = present(view(store.artifact(store.blob(b''))), 100)
        assert output_check(empty_artifact)['display_status'] == 'empty_artifact'
        checks.append('masked selected bytes remain retrievable; empty artifact and metadata-only omission stay distinct')

        raw_artifact = store.artifact(store.blob('x' * 20001))
        raw_descriptor = view(raw_artifact, output_chars=None)
        raw = {'tool_name':'read','status':'completed','effects':'none','output':raw_descriptor}
        before = copy.deepcopy(raw)
        raw_compact = compact_result(raw)
        assert raw == before and 'display_status' not in raw_descriptor
        assert len(''.join(raw_compact['output']['text_parts'])) == 16000
        assert raw_compact['output']['next_read']['range'] == {'bytes':[16000,20001]}
        assert raw_compact['output']['omission_reason'] == 'presentation_allowance'
        assert compact_result({**raw,'output':view(raw_artifact,output_chars=0)})['output']['text_parts'] == []
        assert compact_result({'output':{'future_output':False}})['output'] == {'future_output':False}
        checks.append('raw finish-path descriptors use bounded deterministic presentation; internal descriptors/unknown output unchanged')

        binary = b'A\xffB\xf0\x9f'
        (repo / 'binary').write_bytes(binary)
        invalid = present(call('read', 'binary', range='full')['output'], 100)
        compact_invalid = output_check(invalid)
        assert compact_invalid['decoding_issue'] == invalid['decoding_issue']
        assert compact_invalid['omission_reason'] == 'decoding_issue'
        assert compact_invalid['next_read']['encoding'] == 'base64'
        repaired = present(call('read', **compact_invalid['next_read'], output_chars=None)['output'], 100)
        assert compact_invalid['shown_byte_ranges'] == [[0,1]]
        assert ''.join(compact_invalid['text_parts']).encode()+base64.b64decode(
            ''.join(output_check(repaired)['text_parts'])) == binary
        assert 'retain' not in compact_result({'output':invalid,'observation_record':1})['output']
        assert 'retain' not in compact_result({'output':repaired,'observation_record':1})['output']
        assert 'retain' not in compact_result({'output':empty,'observation_record':1})['output']
        assert 'retain' not in compact_result({'output':empty_artifact,'observation_record':1})['output']
        multibyte = store.artifact(store.blob('A🙂βZ'.encode()))
        fragment = present(view(multibyte, {'bytes':[2,4]}), 100)
        assert output_check(fragment)['decoding_issue']
        checks.append('invalid UTF8 and split codepoints retain exact diagnostics plus one base64 recovery action')

        diff, compact_diff = receipt('write', call('write', 'changed.txt','new\n'))
        assert compact_diff['output']['source']['kind'] == 'file_change_diff'
        assert compact_diff['output']['source']['target_path'] == str(repo / 'changed.txt')
        lossy = copy.deepcopy(diff)
        lossy['diff_lossy_before'] = True
        assert compact_result(lossy)['diff_lossy_before'] is True
        receipt('edit', call('edit', 'changed.txt', 'new', 'edited'))
        historical, compact_history = receipt('history_read', call('history_read', prefix['observation_record']))
        source = compact_history['output']['source']
        assert source['record_id'] == prefix['observation_record'] and source['part'] == 'payload'
        assert source['payload_kind'] == 'captured_observation' and source['recorded_at']
        assert compact_result({**historical,'observation_record':historical['result_record']})['output']['retain'] == compact_history['output']['retain']
        _, envelope = receipt('history_read', call('history_read', prefix['result_record'], part='record'))
        assert envelope['output']['retain']['record_id'] == prefix['result_record']
        assert envelope['output']['retain']['part'] == 'record'
        pinned = present(view(store.history(prefix['observation_record']), {'bytes':[3,21]}, None), 100)
        compact_pinned = output_check(pinned)
        assert compact_pinned['source']['record_id'] == prefix['observation_record']
        assert compact_pinned['source']['part'] == 'payload'
        assert ''.join(compact_pinned['text_parts']).encode() == data[3:21]
        examples['pinned'] = {'full':pinned,'compact':compact_pinned}
        sizes['pinned'] = {'full_bytes':len(dump(pinned).encode()),'compact_bytes':len(dump(compact_pinned).encode())}
        checks.append('diff and pinned/history provenance retains kind/target/record/part/time and exact selected bytes')
        checks.append('retain selectors use immutable observation/history identity and local selected bytes, including undisplayed bytes; empty/base64/observed decoder failures omit selectors')

        unknown = copy.deepcopy(masked)
        unknown.update(future_observation={'uncertain':False}, changed_during_capture=True,
                       capture_error='reader stopped', stdin_error='Broken pipe', stdin_pending_bytes=5)
        unknown['provenance'] = {'new_provenance_fact':False}
        projected = compact_output(unknown)
        for key in ('future_observation','changed_during_capture','capture_error','stdin_error','stdin_pending_bytes'):
            assert projected[key] == unknown[key]
        assert projected['source']['provenance']['new_provenance_fact'] is False
        state = dict(job_id='job-test', state='exited', shell_exit_code=0, live_pids=[], initial_pid=42,
            capture_status='incomplete', capture_error='reader failed', observed_bytes=6,
            observed_at='saved-sample-time', stdin_accepted_bytes=7, stdin_written_bytes=2,
            stdin_pending_bytes=5, stdin_eof_requested=True, stdin_eof_sent=False,
            stdin_queue_open=False, stdin_error='Broken pipe', stop_error='TERM uncertain',
            group_identity_reused=True, termination_reason='cancelled',
            undelivered_input=store.artifact(store.blob(b'once\n')), future_state=False)
        before = copy.deepcopy(state)
        compact_state = compact_process(state)
        assert state == before
        for key, value in state.items():
            if key not in ('initial_pid','undelivered_input'):
                assert compact_state[key] == value
        assert Path(compact_state['undelivered_input']['path']).read_bytes() == b'once\n'
        failed = {'result_record':500,'call_id':'c','tool_name':'send','status':'failed','effects':'input_accepted',
                  'error':'write failed after acceptance','process':state,'unknown_result':None}
        small = compact_result(failed)
        assert small['status'] == 'failed' and small['error'] == failed['error'] and small['effects'] == 'input_accepted'
        assert 'unknown_result' in small and small['unknown_result'] is None
        checks.append('actionable errors/uncertainty, all input states, lossy-diff flag, unknown fields and false/zero values survive')
    finally:
        cleanup = tools.jobs.close()
    assert all(not item['errors'] for item in cleanup)
    for item in sizes.values():
        item['reduction_percent'] = round(100 * (1-item['compact_bytes']/item['full_bytes']), 2)
    (root / 'examples.json').write_text(json.dumps(examples, ensure_ascii=False, indent=2))
    report = {'verified':checks,'sizes':sizes,'provider_integration':False,'cleanup':cleanup}
    (root / 'coverage.json').write_text(json.dumps(report, indent=2))
    print(dump({'verified':checks,'sizes':sizes,'evidence':str(root)}))


if __name__ == '__main__':
    main(sys.argv[1])
