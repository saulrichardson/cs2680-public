"""Real local stdin accounting checks; no provider calls or application-ACK inference."""
import hashlib
import json
import shlex
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch
from mads.store import EvidenceError, Store
from mads.tools import Tools


def main(directory):
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = root/'repo'
    repo.mkdir()
    store = Store(root/'run/journal')
    tools = Tools(repo, root/'run', store)
    examples, checks = {}, []

    def invariant(state):
        assert state['stdin_accepted_bytes'] == state['stdin_written_bytes'] + state['stdin_pending_bytes']
        assert state['stdin_accepted_bytes'] >= state['stdin_written_bytes'] >= 0
        if state['stdin_eof_sent']:
            assert state['stdin_eof_requested'] and state['stdin_pending_bytes'] == 0
        if state['stdin_error'] or state['stdin_eof_requested']:
            assert not state['stdin_queue_open']
        return state

    def wait_for(job_id, predicate, seconds=5):
        deadline = time.monotonic()+seconds
        while True:
            state = invariant(tools.jobs.state(tools.jobs.jobs[job_id]))
            if predicate(state):
                return state
            assert time.monotonic() < deadline, state
            time.sleep(.01)

    def launch(code):
        result = tools.bash('exec '+shlex.quote(sys.executable)+' -u -c '+shlex.quote(code), 0)
        job_id = result['process']['job_id']
        wait_for(job_id, lambda state: b'READY\n' in Path(state['live_path']).read_bytes())
        return job_id

    def gated_reader(gate):
        return launch("import sys,time,hashlib; from pathlib import Path; print('READY',flush=True)\n"
                      f"while not Path({str(gate)!r}).exists(): time.sleep(.01)\n"
                      "data=sys.stdin.buffer.read(); print(len(data),hashlib.sha256(data).hexdigest(),flush=True)")

    def receipt(job_id, text, close=False):
        value = invariant(tools.send(job_id, text, close))
        assert value['accepted_bytes'] == len(text.encode())
        assert value['pending_bytes'] == value['stdin_pending_bytes']
        assert value['eof_requested'] is close
        return value

    def reject_more(job_id, before):
        try:
            tools.send(job_id, 'must-not-be-accepted')
        except ValueError:
            pass
        else:
            raise AssertionError('Input should reject further sends')
        after = invariant(tools.jobs.state(tools.jobs.jobs[job_id]))
        assert after['stdin_accepted_bytes'] == before['stdin_accepted_bytes']

    def completed(job_id, data):
        result = tools.poll(job_id, 5)
        state = invariant(result['process'])
        assert state['state'] == 'exited' and state['shell_exit_code'] == 0
        assert state['capture_status'] == 'complete'
        assert state['stdin_eof_sent'] and not state['stdin_queue_open']
        assert state['stdin_accepted_bytes'] == state['stdin_written_bytes'] == len(data)
        assert f"{len(data)} {hashlib.sha256(data).hexdigest()}\n".encode() in Path(state['live_path']).read_bytes()
        return state

    try:
        # Writes can finish while this child has provably not entered its read.
        gate = repo/'small.release'
        jid = gated_reader(gate)
        first = receipt(jid, 'α🙂')
        second = receipt(jid, 'x\n')
        written = wait_for(jid, lambda s: s['stdin_written_bytes'] == 8)
        assert written['stdin_queue_open'] and not written['stdin_eof_requested']
        assert Path(written['live_path']).read_bytes() == b'READY\n'
        eof = receipt(jid, '', True)
        assert eof['stdin_eof_requested'] and not eof['stdin_eof_sent']
        issued = wait_for(jid, lambda s: s['stdin_eof_sent'])
        reject_more(jid, issued)
        gate.touch()
        examples['utf8_and_written_not_consumed'] = dict(first=first, second=second, before_read=written,
                                                       eof_receipt=eof, final=completed(jid, 'α🙂x\n'.encode()))
        checks.append('UTF-8 cumulative multi-send counts; pipe writes do not imply child consumption')

        gate = repo/'large.release'
        jid = gated_reader(gate)
        payload = ('α🙂x'*300000).encode()
        receipt(jid, payload.decode())
        blocked = wait_for(jid, lambda s: 0 < s['stdin_written_bytes'] < len(payload))
        assert blocked['stdin_pending_bytes'] > 0 and blocked['stdin_queue_open']
        eof = receipt(jid, 'END', True)
        assert eof['stdin_eof_requested'] and not eof['stdin_eof_sent'] and eof['stdin_pending_bytes'] > 0
        reject_more(jid, eof)
        gate.touch()
        examples['queued_eof_after_partial_pipe_write'] = dict(blocked=blocked, eof_receipt=eof,
                                                             final=completed(jid, payload+b'END'))
        checks.append('Partial writes preserve ordered queued bytes; requested EOF follows complete drain')

        gate = repo/'empty.release'
        jid = gated_reader(gate)
        empty = receipt(jid, '', True)
        issued = wait_for(jid, lambda s: s['stdin_eof_sent'])
        assert issued['stdin_accepted_bytes'] == issued['stdin_written_bytes'] == 0
        gate.touch()
        examples['empty_eof'] = dict(receipt=empty, issued=issued, final=completed(jid, b''))
        checks.append('Empty EOF has distinct requested and issued states without inventing data bytes')

        jid = launch("import os,time; os.close(0); print('READY',flush=True); time.sleep(30)")
        accepted = receipt(jid, 'once🙂\n', True)
        failed = wait_for(jid, lambda s: s['stdin_error'] is not None)
        result = tools.poll(jid, 0)
        failed = invariant(result['process'])
        assert failed['stdin_written_bytes'] == 0 and failed['stdin_pending_bytes'] == 9
        assert failed['stdin_eof_requested'] and not failed['stdin_eof_sent']
        assert Path(failed['undelivered_input']['path']).read_bytes() == 'once🙂\n'.encode()
        reject_more(jid, failed)
        cancelled = tools.cancel(jid)
        assert cancelled['process']['stdin_error'] == failed['stdin_error']
        examples['broken_pipe'] = dict(receipt=accepted, failed=failed, cancelled=cancelled['process'])
        checks.append('Closed receiver preserves exact pending bytes and error; no resend or false issued EOF')

        gate = repo/'concurrent.release'
        jid = gated_reader(gate)
        sends, errors = [], []
        def concurrent_send(n):
            try:
                text = f'{n}:🙂\n'
                sends.append((receipt(jid, text), text.encode()))
            except BaseException as exc:
                errors.append(repr(exc))
        threads = [threading.Thread(target=concurrent_send, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
        assert not errors, errors
        ordered = sorted(sends, key=lambda pair: pair[0]['stdin_accepted_bytes'])
        total = 0
        for sent, data in ordered:
            total += len(data)
            assert sent['stdin_accepted_bytes'] == total
        receipt(jid, '', True)
        gate.touch()
        examples['concurrent_queue_acceptance'] = dict(receipts=[sent for sent, _ in ordered],
                                                      final=completed(jid, b''.join(data for _, data in ordered)))
        checks.append('Concurrent acceptance receipts are consistent; pipe receives exact lock-serialized order')

        # Literal OS-resource failure around a real owned child; not provider evidence.
        jid = launch("import time; print('READY',flush=True); time.sleep(30)")
        tools.effects = 'none'
        with patch('mads.jobs.threading.Thread.start', side_effect=RuntimeError('literal thread startup failure')):
            failed = receipt(jid, 'accepted before writer🙂\n', True)
        assert tools.effects == 'input_accepted'
        assert failed['stdin_written_bytes'] == 0 and failed['stdin_pending_bytes'] == failed['accepted_bytes']
        assert 'Input writer startup failed' in failed['stdin_error']
        assert tools.jobs.jobs[jid]['writer'] is None
        assert Path(failed['undelivered_input']['path']).read_bytes() == 'accepted before writer🙂\n'.encode()
        reject_more(jid, failed)
        polled = tools.poll(jid, 0)['process']
        assert polled['undelivered_input'] == failed['undelivered_input']
        cancelled = tools.cancel(jid)['process']
        assert cancelled['stdin_error'] == failed['stdin_error'] and not cancelled['stdin_eof_sent']
        examples['writer_start_failure'] = dict(receipt=failed, polled=polled, cancelled=cancelled)
        checks.append('Writer startup failure preserves accepted effect and exact pending artifact; cleanup never joins an unstarted thread')

        jid = launch("import time; print('READY',flush=True); time.sleep(30)")
        tools.effects = 'none'
        with patch('mads.jobs.threading.Thread.start', side_effect=RuntimeError('literal thread startup failure')), \
                patch.object(store, 'blob', side_effect=EvidenceError('literal pending artifact persistence failure')):
            try:
                tools.send(jid, 'accepted despite archive fault🙂', True)
            except EvidenceError:
                pass
            else:
                raise AssertionError('Expected pending-artifact persistence failure')
        assert tools.effects == 'input_accepted'
        failed = invariant(tools.jobs.state(tools.jobs.jobs[jid]))
        assert failed['stdin_accepted_bytes'] == len('accepted despite archive fault🙂'.encode())
        assert failed['stdin_written_bytes'] == 0 and failed['stdin_eof_requested']
        polled = tools.poll(jid, 0)['process']
        assert Path(polled['undelivered_input']['path']).read_bytes() == 'accepted despite archive fault🙂'.encode()
        tools.cancel(jid)
        examples['pending_artifact_persistence_failure'] = dict(after_failure=failed, recovered=polled)
        checks.append('Pending-artifact persistence failure retains accepted effect; later poll recovers unchanged bytes')

        # Cleanup closes input without pretending an unrequested or undrained EOF was fulfilled.
        jid = launch("import time; print('READY',flush=True); time.sleep(30)")
        payload = b'pending-after-cleanup\n'*100000
        accepted = receipt(jid, payload.decode())
        pending = wait_for(jid, lambda s: 0 < s['stdin_written_bytes'] < len(payload))
        examples['cleanup_pending'] = dict(job_id=jid, receipt=accepted, before_cleanup=pending)
    finally:
        cleanup = tools.jobs.close()
        (root/'cleanup.json').write_text(json.dumps(cleanup, indent=2)+'\n')
    assert all(not row['errors'] and row['process']['capture_status'] == 'complete' for row in cleanup)
    state = invariant(next(row['process'] for row in cleanup if row['job_id'] == jid))
    assert state['stdin_error'] and state['stdin_pending_bytes'] > 0 and not state['stdin_queue_open']
    assert not state['stdin_eof_requested'] and not state['stdin_eof_sent']
    assert Path(state['undelivered_input']['path']).read_bytes() == payload[state['stdin_written_bytes']:]
    examples['cleanup_pending']['after_cleanup'] = state
    checks.append('Cleanup preserves exact unsent remainder and consistent counts without inventing requested EOF')
    (root/'examples.json').write_text(json.dumps(examples, indent=2)+'\n')
    (root/'coverage.json').write_text(json.dumps({'verified':checks,'provider_integration':False}, indent=2)+'\n')
    print(json.dumps({'verified':checks,'evidence':str(root)}))


if __name__ == '__main__':
    main(sys.argv[1])
