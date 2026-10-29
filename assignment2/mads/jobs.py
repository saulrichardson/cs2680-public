"""Owned process groups with continuous, bounded capture and ordered pipe input."""
import copy
import logging
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from .store import CHUNK, now


def _strip_boot_pythonpaths(env):
    """Remove explicitly declared literal boot-only entries; never mutate env.

    Unset/empty marker is opt-out. A malformed nonempty declaration leaves
    PYTHONPATH unchanged, with a warning. Empty marker components are ignored;
    empty PYTHONPATH components are user entries and are always retained.
    """
    declaration = env.get('MADS_BOOT_PYTHONPATH', '')
    if not declaration or 'PYTHONPATH' not in env:
        return env
    boot = {entry for entry in declaration.split(os.pathsep) if entry}
    if any(not os.path.isabs(entry) for entry in boot):
        logging.getLogger(__name__).warning(
            'Invalid MADS_BOOT_PYTHONPATH: non-absolute entry; PYTHONPATH unchanged')
        return env
    # No normalization, trimming, symlink resolution or directory-name guesses:
    # an undeclared spelling is a user entry even if it resolves to the same path.
    components = env['PYTHONPATH'].split(os.pathsep)
    kept = [entry for entry in components if entry not in boot]
    if kept == components:
        return env
    result = dict(env)
    if kept:
        result['PYTHONPATH'] = os.pathsep.join(kept)
    else:
        result.pop('PYTHONPATH')  # don't invent an empty/current-directory entry
    return result


class TerminationError(RuntimeError):
    """An operational stop failure, distinct from programming/persistence errors."""
    def __init__(self, message, attempts):
        super().__init__(message)
        self.attempts = attempts


class Jobs:
    def __init__(self, root, store):
        self.root, self.store = Path(root), store
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs = {}
        self.fault = threading.Event()

    def inherit(self, predecessor):
        """Share stable process handles at final ownership transfer, not old context.

        Capture threads and stdin queues remain attached to their original process
        objects. Each controller archives observations into its own journal. The
        predecessor is paused permanently except for emergency/final cleanup.
        """
        if self.jobs:
            raise ValueError('Cannot replace a nonempty process registry')
        self.jobs = predecessor.jobs
        self.fault = predecessor.fault
        origin = dict(predecessor_journal=str(predecessor.store.root),
                      controller_journal=str(self.store.root), handles=list(self.jobs))
        record = predecessor.store.add('job_control_transferred', **origin)
        self.store.add('job_control_received', **origin, predecessor_record=record)
        return origin

    def unsettled(self):
        """Owned groups/captures which could still change output at final teardown."""
        states = [self.state(job) for job in self.jobs.values()]
        return [state for state in states if state['state'] == 'running' or
                state['capture_status'] != 'complete']

    def start(self, command, cwd, timeout_seconds=None, *, argv=None, launch=None):
        job_id = f'job-{len(self.jobs)+1}'
        path = self.root / f'{job_id}.out'
        # Open before launch: a rejected destination must not launch the command.
        output = path.open('wb', buffering=0)
        env = {k:v for k,v in os.environ.items() if k not in
               ('CS2680_API_KEY', 'OPENROUTER_API_KEY', 'OPENAI_API_KEY')}
        env = _strip_boot_pythonpaths(env)
        try:
            proc = subprocess.Popen(list(argv) if argv is not None else ['bash', '-o', 'pipefail', '-c', command], cwd=cwd,
                                    env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, start_new_session=True, bufsize=0)
        except BaseException:
            output.close()
            raise
        job = dict(id=job_id, proc=proc, path=path, command=command, cwd=str(cwd),
                   started=now(), observed=0, cursor=0, launch=copy.deepcopy(launch),
                   origin={'journal':str(self.store.root), 'launch_record':None},
                   eof=False, capture_error=None, capture_done=threading.Event(),
                   capture_stop=threading.Event(),
                   lock=threading.RLock(), closed_group=False, input=bytearray(),
                   input_closed=False, input_error=None, input_accepted=0, input_written=0,
                   input_eof_sent=False, writer=None, timer=None, reason=None)
        self.jobs[job_id] = job
        def capture():
            try:
                while True:
                    if job['capture_stop'].is_set():
                        job['capture_error'] = 'Capture pipe remained open after group shutdown; possible detached holder. Reader closed; output incomplete.'
                        self.fault.set()
                        break
                    if not select.select([proc.stdout], [], [], .1)[0]:
                        continue
                    data = os.read(proc.stdout.fileno(), CHUNK)
                    if not data:
                        job['eof'] = True
                        break
                    offset = 0
                    while offset < len(data):
                        written = output.write(data[offset:])
                        if not written:
                            raise OSError('Capture write made no progress')
                        offset += written
                        with job['lock']:
                            job['observed'] += written
                os.fsync(output.fileno())
            except OSError as exc:
                job['capture_error'] = str(exc)
                self.fault.set()
                try:
                    self.terminate(job, 'capture_failure')
                except Exception as stop_error:
                    job['stop_error'] = str(stop_error)
            finally:
                output.close()
                proc.stdout.close()
                job['capture_done'].set()
        job['reader'] = threading.Thread(target=capture, daemon=True)
        try:
            os.set_blocking(proc.stdin.fileno(), False)
            job['reader'].start()
        except BaseException as exc:
            job['capture_error'] = f'Capture startup failed: {exc}'
            self.fault.set()
            output.close()
            proc.stdout.close()
            job['capture_done'].set()
            self.terminate(job, 'capture_failure')
            raise
        if timeout_seconds is not None:
            def expire():
                try:
                    self.terminate(job, 'timeout')
                except Exception as exc:
                    job['stop_error'] = str(exc)
            job['timer'] = threading.Timer(timeout_seconds, expire)
            job['timer'].daemon = True
            job['timer'].start()
        job['origin']['launch_record'] = self.store.add('job_started', job_id=job_id, pid=proc.pid, process_group=proc.pid,
                       command=command, cwd=str(cwd), started_at=job['started'], output_path=str(path), launch=job['launch'])
        return job_id

    def members(self, job):
        with job['lock']:
            if job['closed_group']:
                return []
            job['proc'].poll()
            if sys.platform.startswith('linux'):
                found = []
                for path in Path('/proc').glob('[0-9]*/stat'):
                    try:
                        fields = path.read_text().rsplit(')', 1)[1].split()
                        if int(fields[2]) == job['proc'].pid and fields[0] != 'Z':
                            found.append(int(path.parent.name))
                    except (FileNotFoundError, ProcessLookupError):
                        continue
            else:
                output = subprocess.check_output(['ps', '-axo', 'pid=,pgid=,stat='], text=True)
                found = [int(pid) for pid, pgid, state in (r.split() for r in output.splitlines())
                         if int(pgid) == job['proc'].pid and not state.startswith('Z')]
            if job['proc'].returncode is not None and job['proc'].pid in found:
                # A reaped leader cannot be alive again; this group identity was reused.
                job['closed_group'] = True
                job['group_identity_reused'] = True
                return []
            if not found and job['proc'].returncode is not None:
                job['closed_group'] = True
            return found

    def input_state(self, job):
        # These are queue/pipe facts, not evidence of application consumption.
        with job['lock']:
            return dict(stdin_accepted_bytes=job['input_accepted'],
                        stdin_written_bytes=job['input_written'],
                        stdin_pending_bytes=len(job['input']), stdin_error=job['input_error'],
                        stdin_eof_requested=job['input_closed'], stdin_eof_sent=job['input_eof_sent'],
                        stdin_queue_open=not (job['input_closed'] or job['input_error'] or
                                              job['proc'].stdin.closed))

    def state(self, job):
        members = self.members(job)
        # members() polls the leader before scanning descendants. Do not poll
        # again here: a shell can fork/exit during that scan, leaving neither
        # parent nor new child in its /proc enumeration. A later exit sample
        # would incorrectly combine an empty earlier scan with a dead leader.
        # Keep the conservative running sample until the next complete scan.
        running = bool(members) or job['proc'].returncode is None
        capture = ('incomplete' if job['capture_error'] else
                   'complete' if not running and job['eof'] and job['capture_done'].is_set() else
                   'in_progress' if running else 'unknown')
        return dict(job_id=job['id'], state='running' if running else 'exited',
                    launch_origin=copy.deepcopy(job.get('origin')), controller_journal=str(self.store.root),
                    shell_exit_code=job['proc'].returncode, live_pids=members,
                    initial_pid=job['proc'].pid, group_identity_reused=job.get('group_identity_reused',False),
                    capture_status=capture, capture_error=job['capture_error'],
                    observed_bytes=job['observed'], live_path=str(job['path']),
                    termination_reason=job['reason'], eof_observed=job['eof'],
                    containment=(job['launch']['process_scope'] if job.get('launch') else
                                 'owned process group; detached descendants are not attested'),
                    execution_target=(job['launch']['target'] if job.get('launch') else 'host'),
                    observed_at=now(), **self.input_state(job), stop_error=job.get('stop_error'))

    def observe(self, job_id, wait_seconds=10):
        job = self.jobs[job_id]
        deadline = time.monotonic()+wait_seconds
        while True:
            state = self.state(job)
            if state['state'] != 'running' or job['capture_error'] or time.monotonic() >= deadline:
                break
            time.sleep(min(.05, max(0, deadline-time.monotonic())))
        if state['state'] != 'running':
            job['capture_done'].wait(2)
            if job['timer']:
                job['timer'].cancel()
        state = self.state(job)
        with job['lock']:
            start, end = job['cursor'], job['observed']
        # Keep incomplete UTF-8 code points for the next poll, without discarding bytes.
        if not job['capture_done'].is_set() and end > start:
            with job['path'].open('rb') as source:
                source.seek(max(start, end-4))
                tail = source.read(end-source.tell())
            cut = len(tail)
            while cut and tail[cut-1] & 0xc0 == 0x80:
                cut -= 1
            if cut:
                lead = tail[cut-1]
                wanted = 2 if 0xc2 <= lead <= 0xdf else 3 if 0xe0 <= lead <= 0xef else 4 if 0xf0 <= lead <= 0xf4 else 1
                if len(tail)-(cut-1) < wanted:
                    end -= len(tail)-(cut-1)
        with job['path'].open('rb') as source:
            source.seek(start)
            artifact = self.store.ingest(source, end-start)
        if artifact['observed_bytes'] != end-start:
            job['capture_error'] = 'Capture file shortened outside the managed reader'
            self.fault.set()
            self.terminate(job, 'capture_failure')
            state = self.state(job)
            end = start + artifact['observed_bytes']
        artifact.update(live_path=str(job['path']), job_id=job_id, job_byte_range=[start,end],
                        capture_status=state['capture_status'], observed_at=state['observed_at'])
        job['cursor'] = end
        with job['lock']:
            state.update(self.input_state(job))
            pending = bytes(job['input']) if job['input_error'] else b''
        if pending:
            state['undelivered_input'] = self.store.artifact(self.store.blob(pending))
        return state, artifact

    def send(self, job_id, text, close=False):
        job = self.jobs[job_id]
        data = text.encode('utf-8')
        with job['lock']:
            if job['input_closed'] or job['input_error'] or job['proc'].stdin.closed:
                raise ValueError('Input closed or failed; accepted earlier bytes must not be resent')
            job['input'].extend(data)
            job['input_accepted'] += len(data)
            job['input_closed'] = close
            if job['writer'] is None:
                def drain():
                    while True:
                        with job['lock']:
                            try:
                                if job['input_error']:
                                    job['writer'] = None
                                    return
                                if not job['input']:
                                    if job['input_closed']:
                                        job['proc'].stdin.close()
                                        job['input_eof_sent'] = True
                                    job['writer'] = None
                                    return
                                written = os.write(job['proc'].stdin.fileno(), job['input'][:CHUNK])
                                if not written:
                                    raise OSError('Input pipe write made no progress')
                                job['input_written'] += written
                                del job['input'][:written]
                            except BlockingIOError:
                                pass
                            except (OSError, ValueError) as exc:
                                job['input_error'] = str(exc)
                                job['writer'] = None
                                return
                        time.sleep(.01)
                try:
                    job['writer'] = threading.Thread(target=drain, daemon=True)
                    job['writer'].start()
                except (RuntimeError, OSError) as exc:
                    # Acceptance already happened. Preserve the queue and return
                    # its failure explicitly rather than hiding that effect.
                    job['input_error'] = f'Input writer startup failed: {exc}'
                    job['writer'] = None
            result = dict(job_id=job_id, accepted_bytes=len(data), pending_bytes=len(job['input']),
                          eof_requested=close, **self.input_state(job))
            if job['input_error'] and job['input']:
                result['undelivered_input'] = self.store.artifact(self.store.blob(bytes(job['input'])))
            return result

    def terminate(self, job, reason):
        if not self.members(job):
            return
        job['reason'] = reason
        attempts = []
        job['termination_attempts'] = attempts
        for sig, grace in ((signal.SIGTERM,.2), (signal.SIGKILL,2)):
            with job['lock']:
                if not self.members(job):
                    return
                try:
                    attempts.append({'signal':signal.Signals(sig).name,'status':'attempted'})
                    os.killpg(job['proc'].pid, sig)
                    attempts[-1]['status'] = 'sent'
                except ProcessLookupError:
                    attempts[-1]['status'] = 'already_exited'
                except OSError as exc:
                    attempts[-1].update(status='failed', error=str(exc))
                    raise TerminationError(f'Cannot signal owned group: {exc}', attempts) from exc
            deadline = time.monotonic()+grace
            while time.monotonic() < deadline:
                if not self.members(job):
                    return
                time.sleep(.02)
        raise TerminationError(f'Owned group remains alive: {job["id"]}', attempts)

    def status(self):
        states = [self.state(job) for job in self.jobs.values()]
        return {'running_jobs': [s for s in states if s['state']=='running'],
                'capture_failures': [s for s in states if s['capture_error']],
                'completed_jobs': sum(s['state']=='exited' for s in states)}

    def close(self):
        outcomes = []
        for job in self.jobs.values():
            errors = []
            if job['timer']:
                job['timer'].cancel()
            try:
                self.terminate(job, job['reason'] or 'cleanup')
            except Exception as exc:
                errors.append(str(exc))
            try:
                with job['lock']:
                    if job['input'] and not job['input_error']:
                        job['input_error'] = 'Input closed during cleanup with queued bytes remaining'
                    job['proc'].stdin.close()
                    if job['input_closed'] and not job['input']:
                        job['input_eof_sent'] = True
                    writer = job['writer']
                if writer:
                    writer.join(2)
                if not job['capture_done'].wait(2):
                    job['capture_stop'].set()
                    job['reader'].join(2)
                    errors.append('Capture did not reach EOF after shutdown; detached processes may remain')
                state, artifact = self.observe(job['id'],0)
                record = self.store.evidence(artifact, kind='final_process_output', process=state)
                outcomes.append(dict(job_id=job['id'], process=state, journal=str(self.store.root), output_record=record, errors=errors))
            except Exception as exc:
                outcomes.append(dict(job_id=job['id'], errors=[*errors,str(exc)]))
        return outcomes
