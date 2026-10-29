"""Effects adapter for the Haskell investigator; no reasoning or workflow policy here."""
import base64
import hashlib
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import urllib.request
import urllib.error
import uuid
import time

from .store import Store
from .transport import SandboxTransportError, sandbox_call

HERE = Path(__file__).resolve().parent


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Retain the server's diagnostic; a failed transport cannot establish that
        # the requested mutation did not execute. Never replay it here.
        with error:
            body = error.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"Uncertain sandbox operation; not replayed. HTTP {error.code}: {body}") from error


class Workspace:
    def __init__(self, root, state, sandbox_url=None, *, capture_patch=True):
        self.root, self.state, self.url = root, state, sandbox_url
        self.capture_patch = capture_patch
        self.script = str(Path(state) / 'workspace.py')
        self.python = 'python3'
        if sandbox_url:
            # Bootstrap with the course-mounted interpreter before workspace
            # capture. Task images need not have a usable Python on PATH.
            detected = post(sandbox_url + '/exec', {'cwd': root, 'timeout_s': 30,
                'command': 'if [ -f /lib/ld-musl-x86_64.so.1 ] && [ -x /opt/agentpy_musl/bin/python3 ]; then printf /opt/agentpy_musl/bin/python3; '
                           'elif [ -x /opt/agentpy/bin/python3 ]; then printf /opt/agentpy/bin/python3; '
                           'else command -v python3; fi'})
            self.python = detected.get('output', '').strip()
            if detected.get('exit_code') != 0 or not self.python or '\n' in self.python:
                raise RuntimeError('No usable Python interpreter for workspace bootstrap: ' + str(detected))
            post(sandbox_url + '/write', {'path': self.script,
                 'cwd': root, 'content': (HERE / 'workspace.py').read_text()})
        else:
            Path(state).mkdir(parents=True, exist_ok=True)
            Path(self.script).write_bytes((HERE / 'workspace.py').read_bytes())
        if capture_patch:
            result = self.effect('capture', {'workspace': self.facts()})
            if 'error' in result:
                raise RuntimeError('Workspace initialization failed: ' + result['error'])
            self.base = result['base']

    def facts(self):
        return {'root': self.root, 'state': self.state, 'role': 'owner',
                'base': getattr(self, 'base', None), 'capture_patch': self.capture_patch}

    # Each course RPC stays well below its 900-second transport lifetime.
    # This is a polling interval, never a job or investigation deadline.
    RPC_WAIT_MS = 60000

    def effect(self, op, payload):
        wait_ms = payload.get('yield_time_ms', 10000)
        if (self.url and op in ('shell', 'job') and type(wait_ms) is int
                and wait_ms > self.RPC_WAIT_MS):
            deadline = time.monotonic() + wait_ms / 1000
            # Execute launch/input/signal exactly once. Defer page delivery so
            # internal waiting does not consume output or move its cursor.
            initial = self._effect(op, dict(payload, _defer_output=True))
            if 'error' in initial:
                return initial
            ident = initial['id']
            current = initial
            try:
                while current['state'] == 'running':
                    remaining = max(0, int((deadline-time.monotonic()) * 1000))
                    if not remaining:
                        break
                    current = self._effect('wait', {'workspace':payload['workspace'],
                        'id':ident, 'yield_time_ms':min(remaining,self.RPC_WAIT_MS)})
                    if 'error' in current:
                        raise SandboxTransportError(str(current['error']))
                result = self._effect('job', {'workspace':payload['workspace'],
                    'id':ident, 'action':'poll', 'yield_time_ms':0,
                    'max_output_bytes':payload.get('max_output_bytes',16000),
                    **({'offset':payload['offset']} if 'offset' in payload else {})})
            except SandboxTransportError as error:
                # Preserve any acknowledged input/control even when a later
                # read loses its reply. Never repeat the original operation.
                result = dict(initial, **error.observation(), state='unknown', id=ident)
            if 'control' in initial:
                result['control'] = initial['control']
            return result
        return self._effect(op, payload)

    def _effect(self, op, payload):
        encoded = base64.b64encode(json.dumps({'op': op, 'payload': payload}).encode()).decode()
        if self.url:
            # Only this short launcher is bounded by the course's /exec transport.
            # Actual shell jobs are detached and have no execution timeout.
            if len(encoded) > 32000:
                request_file = str(Path(self.state)/('rpc-'+uuid.uuid4().hex+'.json'))
                sandbox_call(post, self.url + '/write', {'path':request_file, 'cwd':self.root,
                    'content':json.dumps({'op':op,'payload':payload})})
                arguments = '--request-file ' + shlex.quote(request_file)
            else:
                arguments = shlex.quote(encoded)
            command = shlex.quote(self.python) + ' ' + shlex.quote(self.script) + ' ' + arguments
            result = sandbox_call(post, self.url + '/exec', {'command': command, 'cwd': self.root, 'timeout_s': 900})
            if result.get('timed_out') or result.get('exit_code') != 0:
                raise SandboxTransportError('Uncertain sandbox operation; not replayed: ' + str(result))
            raw = result['output']
        else:
            p = subprocess.run(['python3', self.script, '--stdin'],
                input=json.dumps({'op':op,'payload':payload}).encode(), capture_output=True)
            if p.returncode:
                raise RuntimeError(p.stderr.decode(errors='replace'))
            raw = p.stdout
        try:
            return json.loads(raw)
        except ValueError as error:
            if self.url:
                raise SandboxTransportError('Invalid sandbox response; execution unknown; not replayed') from error
            raise




class Bridge:
    def __init__(self, workspace, archive, provider, dispatcher=None, task_id=None, logger=None, *, review_enabled=False):
        self.ws, self.archive, self.provider = workspace, Path(archive), provider
        self.dispatcher, self.task_id, self.logger = dispatcher, task_id, logger
        self.review_enabled = review_enabled
        self.selected = None
        self.finished = False
        self.prepared = None
        self.finalization_attempted = False
        self.jobs = {}
        self.output_refs = {}
        self.scopes = {}
        self.review_outputs = {}
        self.review_blobs = {}
        self.store = Store(self.archive / "context.sqlite3")

    def restore_completed_outputs(self):
        # Called after the conversation lock is acquired, alongside other resume state.
        # Only terminal captures are authoritative full outputs. Earlier snapshots
        # remain archived, but cannot replace live observation of a running job.
        for row in self.store.db.execute("SELECT id FROM objects WHERE kind='output' ORDER BY id"):
            ref = 'obj:' + str(row['id'])
            body = self.store.get(ref,kind='output')['body']
            if body['status'].get('state') == 'exited':
                self.output_refs.setdefault(body['job'], ref)

    def observe_job(self, ident, workspace):
        if ident in self.output_refs:
            return self.store.get(self.output_refs[ident],kind='output')['body']['status']
        try:
            result = self.ws.effect('observe',{'workspace':workspace,'id':ident})
            return dict(result, id=ident, state='unknown') if 'error' in result else result
        except SandboxTransportError as error:
            return dict(error.observation(), id=ident, state='unknown')

    def recorded_job_completions(self):
        """Published results already carry these completions in conversation.

        A background job ending is different from a completed tool result. Keep
        the former visible on resume until a result records its terminal state.
        Merely capturing output or writing a checkpoint is not notification.
        """
        completed = set()
        for row in self.store.db.execute(
            "SELECT o.body FROM objects o WHERE o.kind='observation' AND EXISTS ("
            "SELECT 1 FROM links l JOIN objects e ON e.id=l.source "
            "WHERE l.target=o.id AND l.relation='observation' AND e.kind='exchange')"
        ):
            result = json.loads(row['body'])
            jobs = [result] + result.get('jobs', [])
            completed.update(job['id'] for job in jobs if isinstance(job, dict)
                             and job.get('state') == 'exited' and 'id' in job)
        return completed

    def evaluate(self, payload):
        if not self.ws.capture_patch:
            if self.dispatcher:
                raise RuntimeError('Course grading requires patch capture')
            return {'graded': False, 'reason': 'no_grader',
                    'workspace': payload['workspace']['root'],
                    'message': 'Files remain in the workspace. No Git patch or official grade is required for local work.'}
        captured = self.ws.effect('capture', payload)
        if 'error' in captured:
            return captured
        chunks, offset = [], 0
        while offset < captured['bytes']:
            page = self.ws.effect('patch', {'workspace':payload['workspace'],
                'path':captured['patch_path'], 'offset':offset, 'bytes':65536})
            chunk = base64.b64decode(page['base64'])
            if not chunk:
                raise RuntimeError('Captured patch shortened in transport')
            chunks.append(chunk)
            offset += len(chunk)
        raw = b''.join(chunks)
        patch = raw.decode('utf-8')
        if hashlib.sha256(raw).hexdigest() != captured['sha256']:
            raise RuntimeError('Patch transport changed captured bytes')
        target = self.archive / ('candidate-' + captured['sha256'] + '.patch')
        target.write_bytes(raw)
        candidate_ref = self.store.put_blob('candidate','owner',raw,{'base':captured['base']})
        if self.selected and self.selected['candidate']['sha256'] == captured['sha256']:
            return dict(self.selected, reused=True)
        if not patch or not self.dispatcher:
            return {'candidate': captured, 'graded': False, 'reason': 'empty_patch' if not patch else 'no_grader',
                    'artifact': str(target), 'candidate_ref':candidate_ref}
        self.dispatcher.submit_patch(self.task_id, patch)
        # Never retry uncertain evaluation. Its receipt might already have been committed.
        grade = self.dispatcher.evaluate(self.task_id)
        result = {'candidate': captured, 'graded': True, 'receipt': grade, 'reused': False,
                  'passed': grade['tests_total'] > 0 and grade['tests_failed'] == 0, 'artifact': str(target), 'candidate_ref':candidate_ref}
        result['receipt_ref'] = self.store.put('grade','owner',result,
                            [{'relation':'candidate','ref':candidate_ref}])
        self.selected = result
        self.dispatcher.continue_task(self.task_id)
        return result

    def handle(self, op, payload):
        if op == 'continuation':
            return {'ref': self.store.put('continuation', 'owner', dict(payload,
                jobs=self.jobs, scopes=self.scopes, selected=self.selected))}
        if op.startswith('store_'):
            return self.store.handle(op[6:], payload)
        if op == 'review_evidence':
            granted = set()
            blobs = set()
            for event in payload['evidence']:
                if 'error' in event:
                    continue
                # Access follows immutable event/exchange identities, never a
                # model-facing rendering of the selected evidence.
                recorded = self.store.get(event['ref'], kind='event')['body']
                exchange = self.store.get(recorded['exchange_ref'], kind='exchange')['body']
                for message_ref in exchange:
                    message = self.store.get(message_ref, kind='message')['body']
                    if message.get('role') != 'tool':
                        continue
                    observation = json.loads(message['content']).get('observation',{})
                    blobs.update(v[5:] for k,v in observation.items()
                                 if k.endswith('_ref') and isinstance(v,str) and v.startswith('blob:obj:'))
                    ref = observation.get('output_ref')
                    if isinstance(ref,str) and ref.startswith('job:') and ref[4:] in self.jobs:
                        granted.add(ref[4:])
            self.review_outputs[payload['scope']] = granted
            self.review_blobs[payload['scope']] = blobs
            return {'grant_ref':self.store.put('access',payload['scope'],{'selected_job_outputs':sorted(granted),'selected_blobs':sorted(blobs)})}
        if op == 'evidence_get':
            try:
                return self.store.get(**payload)
            except ValueError as e:
                return {'error':str(e),'executed':False}
        if op == 'model':
            return self.provider.call(payload)
        if op == 'trace_tool':
            if self.logger:
                n = self.provider.calls
                self.logger.tool_call(n, payload['name'], payload['arguments'])
                self.logger.tool_result(n, payload['name'], json.dumps(payload['result']),
                                        'error' in payload['result'].get('observation', {}))
            return {'recorded': True}
        if op == 'list_jobs':
            state = self.handle('state', payload)
            return {'workspace':payload['workspace']['root'],
                    'jobs':[{k:v for k,v in job.items() if k != 'completion_recorded'} for job in state['jobs']]}
        if op == 'state':
            workspace = payload['workspace']
            completed = self.recorded_job_completions()
            jobs = [dict(self.observe_job(ident,scope), completion_recorded=ident in completed)
                    for ident, scope in self.jobs.items() if scope.get('state') == workspace.get('state')]
            # No bulky command output in the checkpoint; originals remain addressable.
            for job in jobs:
                job.pop('output', None)
            return {'workspace': workspace, 'jobs': jobs,
                    'selected_candidate': self.selected if workspace['role'] == 'owner' else None}
        if op == 'evaluate':
            return self.evaluate(payload)
        if op == 'finish':
            result = self.evaluate(payload)
            if 'error' in result:
                return dict(result, ready_to_finalize=False)
            if self.dispatcher and not result['graded'] and self.selected:
                return {'error': 'Empty current patch cannot replace an earlier official grade. Restore a candidate or continue repairing.', 'ready_to_finalize': False}
            self.export_outputs()
            # Haskell records the finish exchange and the course tool trace before
            # done() closes the logger and destroys the sandbox.
            self.prepared = dict(result, conclusion=payload['conclusion'],
                                 execution=self.handle('state', payload),
                                 process_lifetime='Local jobs remain running; course finalization may remove its sandbox.')
            return dict(self.prepared, ready_to_finalize=True, finalized=False)
        if op in {'shell', 'job', 'read_output', 'snapshot', 'read_file', 'edit_file', 'search_files'}:
            scope = payload.get('_scope','owner')
            if op == 'read_output' and payload.get('ref','').startswith('blob:obj:'):
                ref = payload['ref'][5:]
                access = None if scope == 'owner' or ref in self.review_blobs.get(scope,set()) else scope
                try:
                    record = self.store.get(ref, scope=access)
                    if payload.get('present_archive') and record['body'].get('purpose') == 'search_results':
                        raw = self.store.db.execute('SELECT bytes FROM blobs WHERE object_id=?', (self.store.number(ref),)).fetchone()[0]
                        if hashlib.sha256(raw).hexdigest() != record['body']['sha256']:
                            raise RuntimeError('Saved search integrity check failed')
                        return {'saved_search': [json.loads(line) for line in raw.splitlines()],
                                'ref': 'blob:' + ref, 'raw_ref': 'raw:blob:' + ref}
                    return self.store.blob(ref,payload.get('offset',0),payload.get('bytes',16000),access)
                except ValueError as e:
                    return {'error':str(e),'executed':False}
            if op == 'read_output':
                ident = payload.get('ref','')[4:]
                if ident in self.review_outputs.get(scope,set()):
                    payload = dict(payload,workspace=self.jobs[ident])
                if ident in self.output_refs and (scope == 'owner' or self.scopes[ident] == scope or ident in self.review_outputs.get(scope,set())):
                    return self.store.blob(self.output_refs[ident],payload.get('offset',0),payload.get('bytes',16000))
            if op in {'job', 'read_output'} and payload['workspace']['role'] == 'owner':
                ident = payload.get('id') or payload.get('ref','')[4:]
                if ident in self.jobs:
                    payload = dict(payload, workspace=self.jobs[ident])
            launch_id = uuid.uuid4().hex if op == 'shell' else None
            if launch_id:
                payload = dict(payload, _job_id=launch_id)
            try:
                result = self.ws.effect(op, payload)
            except SandboxTransportError as error:
                if launch_id:
                    self.jobs[launch_id] = payload['workspace']
                    self.scopes[launch_id] = scope
                    self.store.put('job', scope, {'id':launch_id, 'workspace':payload['workspace']})
                    error.job_id = launch_id
                elif op == 'job':
                    error.job_id = payload['id']
                raise
            for key, artifact in result.pop('_artifacts', {}).items():
                result[key] = 'blob:' + self.archive_artifact(artifact, payload['workspace'], scope)
            if op == 'shell' and 'id' in result:
                self.jobs[result['id']] = payload['workspace']
                self.scopes[result['id']] = scope
                self.store.put('job', scope, {'id':result['id'], 'workspace':payload['workspace']})
            if op in {'shell','job'} and result.get('state') == 'exited':
                result = dict(result, output_object_ref=self.archive_output(result['id'],payload['workspace'],result,scope))
            return result
        raise ValueError('Unknown effect ' + op)

    def archive_artifact(self, artifact, workspace, scope):
        chunks, offset = [], 0
        while offset < artifact['bytes']:
            page = self.ws.effect('patch', {'workspace':workspace,'path':artifact['path'],
                'offset':offset,'bytes':min(65536,artifact['bytes']-offset)})
            chunk = base64.b64decode(page['base64'])
            if not chunk:
                raise RuntimeError('Artifact shortened during export; operation must not be replayed')
            chunks.append(chunk)
            offset += len(chunk)
        raw = b''.join(chunks)
        if hashlib.sha256(raw).hexdigest() != artifact['sha256']:
            raise RuntimeError('Artifact bytes changed during export; operation must not be replayed')
        return self.store.put_blob('artifact',scope,raw,artifact)

    def archive_output(self, ident, workspace, status, scope):
        if ident in self.output_refs:
            return self.output_refs[ident]
        total, offset, chunks = status['output']['total_bytes'], 0, []
        while offset < total:
            data = self.ws.effect('read_output', {'workspace':workspace,'ref':'job:'+ident,
                'offset':offset,'bytes':min(65536,total-offset),'raw':True})
            chunk = base64.b64decode(data['base64'])
            if not chunk:
                raise RuntimeError('Saved output shortened during export')
            chunks.append(chunk)
            offset += len(chunk)
        raw = b''.join(chunks)
        ref = self.store.put_blob('output',self.scopes.get(ident,scope),raw,{'job':ident,'status':status})
        # The database is authoritative; these files are convenient exported artifacts.
        outputs = self.archive/'outputs'
        outputs.mkdir(exist_ok=True)
        (outputs/(ident+'.bin')).write_bytes(raw)
        if status.get("state") == "exited":
            self.output_refs[ident] = ref
        return ref

    def export_outputs(self):
        """Preserve original bytes before the course removes its task sandbox."""
        for ident, workspace in self.jobs.items():
            status = self.observe_job(ident,workspace)
            self.archive_output(ident,workspace,status,self.scopes[ident])

    def run(self, binary, task, system, *, request_settings=None):
        # One writer per conversation; different conversations may share a workspace.
        self.run_lock = (self.archive/'run.lock').open('a')
        try:
            fcntl.flock(self.run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.run_lock.close()
            raise RuntimeError('This conversation already has an active executor')
        resume = self.resume_state(task)
        facts = dict(self.ws.facts(), grading_available=self.dispatcher is not None,
                     review_available=self.review_enabled)
        foundation = system
        if self.dispatcher is not None:
            system += '\n\n' + (HERE / 'grading-guidance.txt').read_text()
        if self.review_enabled:
            system += '\n\n' + (HERE / 'review-guidance.txt').read_text()
        config = { 'workspace': facts, 'system': system, 'review_system': foundation, 'task': task,
                   'request_settings': request_settings if request_settings is not None else
                       {'model': self.provider.model, 'parallel_tool_calls': False}}
        config['provider_route'] = self.provider.route
        if resume is not None:
            config['resume'] = resume
        self.store.put('session_start', 'owner', config)
        if not (self.archive/'initial.json').exists():
            (self.archive / 'initial.json').write_text(json.dumps(config))
        with (self.archive / 'haskell.stderr').open('w') as errors:
            p = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=errors, text=True, bufsize=1)
            try:
                p.stdin.write(json.dumps(config) + '\n')
                p.stdin.flush()
                for line in p.stdout:
                    req = json.loads(line)
                    if req['op'] == 'complete':
                        p.stdin.close()
                        code = p.wait()
                        if code or self.prepared is None:
                            raise RuntimeError('Invalid finalization; see Haskell log')
                        self.finalization_attempted = True
                        self.store.put('finalization_intent', 'owner', {'course':self.dispatcher is not None})
                        final = self.dispatcher.done(self.task_id, 'owner_finished', self.provider.calls) if self.dispatcher else {'local_only':True}
                        outcome = dict(self.prepared, finalized=True, dispatcher=final)
                        self.store.put('finalization','owner',outcome,
                            [{'relation':'candidate','ref':self.prepared['candidate_ref']}] if self.prepared.get('candidate_ref') else [])
                        (self.archive/'final.json').write_text(json.dumps(outcome))
                        self.finished = True
                        return outcome
                    try:
                        result = self.handle(req['op'], req['payload'])
                        response = {'op': req['op'], 'result': result}
                    except SandboxTransportError as e:
                        if req['op'] in {'shell', 'job', 'read_output', 'snapshot', 'read_file', 'edit_file', 'search_files', 'evaluate', 'finish'}:
                            response = {'op': req['op'], 'result': e.observation()}
                        else:
                            response = {'op': req['op'], 'fatal': str(e)}
                    except Exception as e:
                        # Preserve outcome as infrastructure failure; never model give-up.
                        response = {'op': req['op'], 'fatal': str(e)}
                    p.stdin.write(json.dumps(response) + '\n')
                    p.stdin.flush()
                code = p.wait()
                raise RuntimeError(f'Haskell runtime exited {code}; see {self.archive / "haskell.stderr"}')
            finally:
                if p.poll() is None:
                    p.terminate()
                    p.wait()
                p.stdout.close()
                if not p.stdin.closed:
                    p.stdin.close()


    def resume_state(self, followup):
        row = self.store.db.execute("SELECT id,body FROM objects WHERE kind='continuation' AND scope='owner' ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            # Older archives have durable request views but no explicit continuation.
            row = self.store.db.execute("SELECT id,body FROM objects WHERE kind='view' AND scope='owner' ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                if self.store.query(limit=1)['objects']:
                    raise RuntimeError('Archive has no saved context view to resume; evidence remains readable')
                return None
            state = dict(json.loads(row['body']), visible=[], next_id=1)
        else:
            state = json.loads(row['body'])
        if self.dispatcher and self.store.db.execute("SELECT 1 FROM objects WHERE kind='finalization_intent' LIMIT 1").fetchone():
            raise RuntimeError('Course finalization was requested; inspect its receipt before attempting another dispatcher action')
        self.jobs.update(state.get('jobs', {}))
        self.scopes.update(state.get('scopes', {}))
        self.selected = state.get('selected')
        self.restore_completed_outputs()
        for record in self.store.db.execute("SELECT scope,body FROM objects WHERE kind='job' ORDER BY id"):
            job = json.loads(record['body'])
            self.jobs[job['id']], self.scopes[job['id']] = job['workspace'], record['scope']
        grades = self.store.db.execute("SELECT body FROM objects WHERE kind='grade' AND scope='owner' ORDER BY id DESC LIMIT 1").fetchone()
        if grades:
            self.selected = json.loads(grades['body'])
        # Recover launches that occurred before observation recording or checkpointing.
        # Inspect files only: never execute a stored intent or recreate a process.
        if not self.ws.url:
            for request in Path(self.ws.state).glob('**/jobs/*/request.json'):
                job = request.parent.name
                if job not in self.jobs:
                    state_dir = request.parent.parent.parent
                    if state_dir == Path(self.ws.state):
                        scope = self.ws.facts()
                        owner = 'owner'
                    else:
                        # Reviewer work remains attributed to its own historical scope.
                        continue
                    self.jobs[job], self.scopes[job] = scope, owner
        tail = self.store.db.execute("SELECT id,kind,scope,alias FROM objects WHERE id>? ORDER BY id", (row['id'],)).fetchall()
        index = [dict(ref='obj:'+str(r['id']), kind=r['kind'], scope=r['scope'], alias=r['alias']) for r in tail]
        events = self.store.db.execute("SELECT alias FROM objects WHERE kind='event'").fetchall()
        numbers = [int(r['alias'].split(':')[1]) for r in events if r['alias'] and r['alias'].startswith('event:')]
        reviews = self.store.db.execute("SELECT DISTINCT scope FROM objects WHERE scope LIKE 'reviewer:%'").fetchall()
        numbers += [int(r['scope'].split(':')[1]) for r in reviews]
        state['next_id'] = max([state['next_id']] + [n+1 for n in numbers])
        execution = self.handle('state', {'workspace': self.ws.facts()})
        state['execution'] = execution
        # Report receipt/publication facts, never infer whether the operation ran.
        # Ordinary finalization records are not uncertain tool operations.
        operations = []
        intents = self.store.db.execute(
            "SELECT o.id FROM objects o WHERE o.kind='intent' AND o.scope='owner' "
            "AND (o.id>? OR NOT EXISTS (SELECT 1 FROM links l JOIN objects r ON r.id=l.source "
            "WHERE l.target=o.id AND l.relation='intent' AND r.kind='observation')) ORDER BY o.id",
            (row['id'],)).fetchall()
        for record in intents:
            intent_ref = 'obj:' + str(record['id'])
            observed = self.store.db.execute(
                "SELECT o.id FROM objects o JOIN links l ON l.source=o.id "
                "WHERE o.kind='observation' AND l.relation='intent' AND l.target=?",
                (record['id'],)).fetchone()
            published = None if observed is None else self.store.db.execute(
                "SELECT o.id FROM objects o JOIN links l ON l.source=o.id "
                "WHERE o.kind='exchange' AND l.relation='observation' AND l.target=?",
                (observed['id'],)).fetchone()
            operations.append(dict(intent_ref=intent_ref,
                operation=self.store.get(intent_ref)['body']['name'],
                observation_ref=None if observed is None else 'obj:'+str(observed['id']),
                exchange_ref=None if published is None else 'obj:'+str(published['id'])))
        # Preserve the full post-checkpoint index in the session_start archive;
        # Haskell presents relevant operation facts separately from literal user text.
        state['records_after_checkpoint'] = index
        state['unintegrated_operations'] = operations
        state['followup'] = followup
        return state

    def close(self):
        if getattr(self, 'run_lock', None) is not None and not self.run_lock.closed:
            self.run_lock.close()
        self.store.close()
        if hasattr(self.provider, 'store'):
            self.provider.store.close()
