"""Real files/jobs and literal compiler exchanges; not a provider conversation."""
import json
import math
import sys
from pathlib import Path

from mads.context import Context
from mads.store import Store, dump
from mads.tools import TOOLS, Tools


def main(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = root/'repo'
    repo.mkdir()
    store = Store(root/'run/journal')
    tools = Tools(repo, root/'run', store)
    ctx = Context(store, root/'run/memory', input_limit=40000, total_limit=50000,
                  output_tokens=6000, mask_at=0, pressure_at=35000)
    verified, examples = [], {}

    def compile(current=None, restart=None):
        return ctx.compile('SYSTEM', 'ORIGINAL TASK', TOOLS, 'literal', tools.jobs.status(), current, restart)

    def inspect(name):
        head = len(store.events)
        files = sorted(str(p) for p in store.root.rglob('*') if p.is_file())
        working = ctx.working.read_bytes() if ctx.working.exists() else None
        result = ctx.context_status()
        assert len(store.events) == head
        assert sorted(str(p) for p in store.root.rglob('*') if p.is_file()) == files
        assert (ctx.working.read_bytes() if ctx.working.exists() else None) == working
        examples[name] = result
        return result

    def group(calls):
        identity = store.add('literal_compiler_exchange')
        native, results = [], []
        for index, (name, arguments, result) in enumerate(calls):
            call_id = f'{identity}-{index}'
            native.append({'id':call_id, 'type':'function',
                           'function':{'name':name, 'arguments':dump(arguments)}})
            results.append({'call_id':call_id, 'status':'completed', 'effects':'none', **result})
        return {'id':identity, 'assistant':{'role':'assistant', 'tool_calls':native},
                'results':results, 'fresh':True}

    try:
        status = inspect('before-first-request')
        assert status['context_id'] is None and status['injected_account']['state'] == 'not_compiled'
        assert not status['automatically_included'] and status['compiled_request_read'] is None
        assert status['manifest_read'] is None
        verified.append('uncompiled-state-is-explicit-and-inspection-is-read-only')

        account = ''.join(f'account line {i}: α\n' for i in range(400))
        ctx.working.write_text(account)
        request = compile()
        original_id = ctx.context_id
        finalized = store.record(original_id)
        ctx.calibrate(request, math.ceil(len(dump(request).encode())*.5))
        assert ctx.ratio > finalized['tokens_per_byte']
        status = inspect('after-response-calibration-before-next-compile')
        assert status['tokens_per_byte'] == finalized['tokens_per_byte']
        assert status['estimated_tokens'] == finalized['estimated_tokens']
        assert status['context_id'] == original_id
        assert status['manifest_read'] == {'record_id':original_id,'part':'record'}
        recovered_manifest = tools.history_read(**status['manifest_read'])['output']
        assert json.loads(Path(recovered_manifest['path']).read_text()) == finalized
        assert ctx.ratio > status['tokens_per_byte'], 'Inspection must not reset future calibration'
        verified.append('last-finalized-estimate-ratio-and-direct-full-manifest-retrieval-survive-post-response-calibration')
        ctx.working.write_text('new working file, not yet injected')
        status = inspect('current-snapshot-after-file-changed')
        injected = status['injected_account']
        assert status['context_id'] == original_id and injected['state'] == 'current'
        snapshot = tools.read(**injected['read'])['output']
        assert Path(snapshot['path']).read_bytes() == account.encode()
        assert snapshot['selected_byte_range'] == [0, len(account.encode())]
        assert injected['bytes'] == len(account.encode())
        assert json.loads(Path(status['compiled_request_read']['path']).read_text()) == request
        assert store.record(injected['record_id'])['kind'] == 'working_account'
        assert injected['authorship'] == 'agent' and injected['validation'] == 'unverified'
        assert 'not validated' in injected['state_meaning']
        verified.append('exact-injected-account-path-does-not-resample-edited-working-file')

        ctx.working.write_bytes(b'\xff')
        compile()
        assert store.record(ctx.context_id)['tokens_per_byte'] == ctx.ratio
        stale = inspect('stale-account')['injected_account']
        assert stale['state'] == 'stale' and stale['record_id'] == injected['record_id']
        assert stale['read'] == injected['read'] and stale['recovery_reason']
        ctx.last_account = None
        compile()
        absent = inspect('absent-account')['injected_account']
        assert absent['state'] == 'absent' and absent['read'] is None and absent['text'] == ''
        ctx.working.unlink()
        compile()
        missing = inspect('missing-account')['injected_account']
        assert missing['state'] == 'missing' and missing['read'] is None and missing['text'] == ''
        verified.append('stale-absent-and-missing-injected-accounts-identified-exactly')

        ctx.working.write_text('before restart')
        old = group([('read', {'path':'old'}, {'status':'rejected', 'error':'OLD EXCHANGE MARKER'})])
        compile(old)
        support = ctx.memory/'support.md'
        support.write_text('RETRIEVAL_ONLY_SUPPORT_WITNESS')
        tools.write('committed.txt', 'COMMITTED_ONCE')
        job = tools.bash("printf READY; read -r value; printf '%s' \"$value\"", wait_seconds=.1)
        job_id = job['process']['job_id']
        assert job['process']['state'] == 'running'
        current = group([
            ('context_configure', {'keep_recent':1}, {'context_change':'pending'}),
            ('context_restart', {'keep_recent_exchanges':0, 'keep_records':[]}, {'context_change':'pending'})])
        current['configurations'] = [{'index':0, 'arguments':{'keep_recent':1}}]
        ctx.working.write_text('after siblings, before restart compile')
        before = inspect('during-pending-response')
        assert before['groups'] == [old['id']] and before['keep_recent'] == 10
        request = compile(current, {'index':1, 'arguments':{'keep_recent_exchanges':0, 'keep_records':[]}})
        after = inspect('after-accepted-restart')
        assert after['groups'] == [current['id']] and after['keep_recent'] == 1
        assert after['kept_records'] == [] and 'OLD EXCHANGE MARKER' not in dump(request)
        assert Path(after['injected_account']['read']['path']).read_text() == 'after siblings, before restart compile'
        assert request['messages'][0]['content'] == 'SYSTEM' and request['messages'][1]['content'] == 'ORIGINAL TASK'
        assert request['tools'] == TOOLS
        receipt = json.loads(request['messages'][-1]['content'])
        ack = receipt['applied_context']
        assert ack['account_read'] == after['injected_account']['read']
        assert ack['retained_evidence'] == []
        marker = ack['replay_omission']
        assert marker == after['replay_omission'] and marker['exchanges'] == 1
        assert marker['exchange_id_bounds'] == [old['id'], old['id']]
        search = tools.history_search(**marker['history_search'])
        matches = json.loads(Path(search['output']['path']).read_text())['matches']
        assert any('OLD EXCHANGE MARKER' in Path(match['artifact']['path']).read_text() for match in matches)
        assert receipt['harness_runtime']['kind'] == 'fresh_global_state'
        verified.append('pending-batch-status-is-committed-and-restart-selection-is-explicit')

        # A line selection is normalized to exact bytes; account reads retain the
        # injected immutable snapshot even after the live file changes again.
        evidence_path = repo/'interface.txt'
        evidence_path.write_text('discarded header\ninterface α\nretrievable remainder\n')
        observed = tools.read(str(evidence_path), range='full')
        reference = observed['observation_record']
        selection = {'record_id':reference, 'view':{'lines':[2,2]}}
        next_group = group([('context_restart', {'keep_records':[selection]}, {'context_change':'pending'})])
        request = compile(next_group, {'index':0, 'arguments':{'keep_records':[selection]}})
        ack = json.loads(request['messages'][-1]['content'])['applied_context']
        retained = ack['retained_evidence']
        assert retained == [{'record_id':reference, 'part':'payload', 'range':{'bytes':[17,30]}}]
        recovered = tools.history_read(**retained[0])['output']
        assert recovered['selected_byte_range'] == [17,30]
        assert Path(recovered['path']).read_bytes()[17:30] == 'interface α\n'.encode()
        ctx.working.write_text('not injected yet')
        assert Path(ack['account_read']['path']).read_text() == 'after siblings, before restart compile'
        # Only the most recent replacement marker remains, not an accumulating log.
        assert ack['replay_omission']['exchange_id_bounds'] == [current['id'], current['id']]
        assert ack['replay_omission']['exchanges'] == 1
        examples['normalized-restart-request'] = request
        verified.append('restart-ack-exact-account-normalized-evidence-and-bounded-retrievable-omission')

        assert (repo/'committed.txt').read_text() == 'COMMITTED_ONCE'
        assert 'RETRIEVAL_ONLY_SUPPORT_WITNESS' not in dump(request)
        recovered = tools.read(str(support), range='full')['output']
        assert Path(recovered['path']).read_text() == 'RETRIEVAL_ONLY_SUPPORT_WITNESS'
        assert tools.poll(job_id, wait_seconds=0)['process']['state'] == 'running'
        tools.send(job_id, 'SURVIVED_RESTART\n', close=True)
        completed = tools.poll(job_id, wait_seconds=3)
        assert completed['process']['state'] == 'exited'
        assert Path(completed['output']['path']).read_text() == 'SURVIVED_RESTART'
        verified.append('restart-preserves-files-jobs-and-retrieval-only-supporting-memory')
    finally:
        cleanup = tools.jobs.close()
    (root/'examples.json').write_text(json.dumps(examples, ensure_ascii=False, indent=2)+'\n')
    result = {'verified':verified, 'provider_integration':False, 'cleanup':cleanup}
    (root/'coverage.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'verified':verified, 'provider_integration':False}))


if __name__ == '__main__':
    main(sys.argv[1])
