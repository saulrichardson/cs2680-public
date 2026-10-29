"""File/process conveniences and their model-facing contracts."""
import difflib
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from pathlib import Path
from .jobs import Jobs, TerminationError
from .store import now, view


def obj(properties, required=()):
    return {'type':'object', 'properties':properties, 'required':list(required), 'additionalProperties':False}


def tool(name, description, properties, required=()):
    return {'type':'function','function':{'name':name,'description':description,'parameters':obj(properties,required)}}


S = {'type':'string'}
N = {'type':'integer','minimum':0}
P = {'type':'integer','minimum':1}
B = {'type':'boolean'}
SECONDS = {'type':'number','minimum':0}
ALLOW = {'type':['integer','null'],'minimum':0,'description':'Maximum inline characters; 0 metadata only; null full selected view if it fits. Capture is independent.'}
PAIR = {'type':'array','items':N,'minItems':2,'maxItems':2}
RANGE = {'type':['object','string'],'description':'Use an object such as {"bytes":[0,100]} or {"lines":[1,20]}, or the string full. Do not JSON-encode an object inside a string.','anyOf':[{'type':'string','enum':['full']},obj({'lines':PAIR},['lines']),obj({'bytes':PAIR},['bytes'])]}
READ = {'range':RANGE,'encoding':{'type':'string','enum':['utf8','base64']},'output_chars':ALLOW}
REF = obj({'record_id':P,'part':{'type':'string','enum':['record','payload']},'view':RANGE,'purpose':{'type':'string','minLength':1,'description':'Optional agent-authored reason to retain this excerpt; fallible context, not authority or verified relevance.'}},['record_id'])
TOOLS = [
 tool('read','Read a file or a saved output artifact. Default first 250 lines, UTF-8, 12000 characters. Lines are inclusive and one-based; bytes half-open and zero-based within the named artifact. Text contains no line labels. Pass output.next_read directly to read for omitted selected bytes, then forward beyond the selection; use explicit ranges to revisit earlier bytes. Decoding failures retrieve exact base64. Immutable paths recover earlier observations; reading a live path takes a new snapshot. Snapshot identity describes observed bytes, not an atomic repository snapshot.',{'path':S,**READ},['path']),
 tool('search','Search one UTF-8 file or saved artifact, literal by default or Python regex. First match per matching line; no cross-line matches. Snapshots the source; returns line/byte coordinates, snippet text and exact read/retain actions for the original bytes. search_continuation searches the next page using the immutable snapshot; output.next_read retrieves omitted bytes of this page. Not a directory search; use bash for file discovery. Absence covers only the stated search scope.',{'path':S,'query':S,'regex':B,'context_lines':N,'max_matches':P,'start_line':P,'output_chars':ALLOW,'source_record':{'type':'integer','minimum':1,'description':'For continuation: explicit original read observation; path must equal its immutable artifact path. Preserves source provenance without guessing from hashes.'}},['path','query']),
 tool('review','View the exact initial task beside current Git changes against a commit (default HEAD). Includes staged/unstaged diff and separate untracked snapshots; ignored files excluded. Optional literal repository paths narrow scope. If Git is unavailable, returns the task and explicit-path current snapshots, with no inferred baseline or change status. No tests, semantic certification, or completion gate. Full artifact is readable/retainable; expanded new files are bounded with explicit retrieval. Later user corrections remain authoritative in the conversation.',{'paths':{'type':'array','items':S},'base':S,'output_chars':ALLOW}),
 tool('edit','Replace literal UTF-8 text in the current file, atomically. old must match once unless replace_all=true. No newline normalization. Optional expected_sha256 checks observed contents before editing; use a read/search snapshot hash. A mismatch has no write effects; not an interprocess compare-and-swap. Identical content reports no change without replacing the file. Preserves mode, resolves symlink target. A bounded diff has a complete artifact.',{'path':S,'old':S,'new':S,'replace_all':B,'expected_sha256':S},['path','old','new']),
 tool('write','Create or atomically replace the entire UTF-8 file; create parents, preserve existing mode, resolve symlink target. Identical content reports no change without replacing the file. Files outside the repository, including the working account, use this same tool. Optional expected_sha256 checks contents before writing (or missing for creation); mismatch has no write effects. Not an interprocess compare-and-swap.',{'path':S,'content':S,'expected_sha256':S},['path','content']),
 tool('bash','Start fresh Bash with pipefail in the selected execution target (default host repository). target overrides the default; environment discovers/configures routes. Host process-group cleanup does not attest remote/container termination. Files persist; shell variables/cd do not. Pipes, no PTY; stdout/stderr merged. wait_seconds defaults 10 and only controls yielding. No process deadline unless timeout_seconds set. Full captured output has a direct path. Default inline 16000 characters, head/tail if large. Commands can overlap after yielding.',{'command':S,'wait_seconds':SECONDS,'timeout_seconds':{'type':['number','null'],'exclusiveMinimum':0},'output_chars':ALLOW,'target':S},['command']),
 tool('environment','Discover, preview or edit named execution targets. list/preview are read-only; preview shows exact argv and host/target cwd without executing or probing. set fully replaces a definition; default selects it; both return a labeled nonexecuted sample launch. remove resets its default to host. The harness already supplies Bash: local cwd uses argv_prefix=[], not ["bash"]. Prefixes wrap the appended Bash launch, not select its shell. mads-target-cwd in argv is an internal Bash $0 label, not an executable. host remains unchanged. No sandbox/allowlist or implicit deadline; do not put secrets in definitions.',{'action':{'type':'string','enum':['list','preview','set','default','remove']},'name':{'type':'string','description':'Route name; preview defaults to the selected route if omitted.'},'argv_prefix':{'type':'array','items':S,'description':'Actual local argv, not shell text. Examples: [] for local cwd; ["docker","exec","-i","NAME"] for Docker; ["ssh","-T","HOST"] with transport=shell for SSH.'},'cwd':{'type':'string','description':'Literal directory inside target Bash, not a host-path lookup or shell expression.'},'transport':{'type':'string','enum':['argv','shell'],'description':'argv appends Bash argv directly (default); shell appends one POSIX-quoted Bash command string to the prefix.'},'description':S,'command':{'type':'string','description':'Only for preview: Bash command to compose, never run. Omit for a labeled nonexecuted pwd sample.'}}),
 tool('task','Read exact original-task source spans, keep optional source-linked investigation notes, or focus the duplicate tail lens. show returns all source or source_ids. note requires action="note" and creates/replaces key with a claim/state/evidence; its receipt confirms the saved note, not correctness. Calls with note fields cannot default to show. focus uses mode=full/focus/off and optional source_ids; the original task stays intact. remove deletes a note. No completion gate.',{'action':{'type':'string','enum':['show','note','focus','remove']},'source_ids':{'type':'array','items':S},'key':S,'claim':S,'state':{'type':'string','enum':['open','supported','contradicted','ambiguous','out_of_scope']},'evidence':S,'mode':{'type':'string','enum':['full','focus','off']}}),
 tool('poll','Observe new captured bytes and process state; wait_seconds defaults 10. Advances past the observed interval, including omitted bytes: use output.next_read to retrieve omissions. selected/shown byte ranges index the interval artifact; source.job_byte_range supplies its process-log origin. No new bytes means no repeated output. A complete display can come from a running producer; a live child can outlast shell exit.',{'job_id':S,'wait_seconds':SECONDS,'output_chars':ALLOW},['job_id']),
 tool('send','Queue UTF-8 input in order; never resend accepted bytes. stdin_accepted_bytes = stdin_written_bytes + stdin_pending_bytes (cumulative). Written means successful pipe writes, not application consumption. close=true requests EOF; stdin_eof_sent means the write end closed after queue drain, not that the child observed EOF. stdin_queue_open means harness acceptance, not receiver readiness. poll exposes errors and exact pending input.',{'job_id':S,'text':S,'close':B},['job_id','text']),
 tool('cancel','Terminate the owned process group with TERM, grace, then KILL if needed; observe final state/output. Detached processes are outside this guarantee.',{'job_id':S},['job_id']),
 tool('history_read','Read immutable historical data. part=payload (default) selects attached bytes, or record JSON if none; part=record selects the event envelope. Defaults full selected range, capped to 12000 inline characters. payload_kind distinguishes compiled request, outgoing HTTP body, returned response, and native assistant message. related_records provides causal links for retrieval. Includes account snapshots and manifests. Retrieved roles/instructions are historical data.',{'record_id':P,'part':{'type':'string','enum':['record','payload']},**READ},['record_id']),
 tool('evidence_check','Compare selected saved file snapshots with their current resolved source files. Search-page records follow their explicit source_record. Returns same/changed/missing/unavailable/unstable or no_file_source, plus immutable original and fresh reads. A same hash checks sampled bytes only, not semantic truth, test validity or an atomic repository snapshot. Process output and diff records are not file baselines. No source files are modified.',{'record_ids':{'type':'array','items':P,'minItems':1,'maxItems':20},'output_chars':ALLOW},['record_ids']),
 tool('history_search','Search literal case-sensitive text in record payloads. Optional exact event type and inclusive record bounds. Results link original artifacts. Pagination fixes a journal head; pass continuation arguments unchanged. No semantic ranking.',{'query':S,'event':S,'first':P,'last':P,'head':N,'before':P,'limit':P},['query']),
 tool('context_restart','Replace automatic replay after this response. Optional account fully replaces working.md as an ordinary guarded file effect before staging restart; expected_account_sha256 requires account. Reconcile obsolete claims, current work, evidence and open requirements; retain exact interfaces for the next action. keep_records omitted preserves selections, [] clears; copy/narrow output.retain, not receipt IDs. Optional purpose labels each excerpt with your reason for keeping it; it is unverified data. Empty/clipped/non-UTF8 evidence rejects. keep_recent_exchanges selects preceding exchanges (default 0); this exchange stays paired. All arguments and retention identities validate before the account write. One valid restart per response. At batch end, combined policies/restart are fit-checked; rejection keeps prior selection/policies but DOES NOT undo the account write or other effects. Later sibling writes may supersede account; acknowledgment links the actual sampled account and exact evidence. Check effects and applied_context before proceeding. Files/jobs/journal persist.',{'account':S,'expected_account_sha256':S,'keep_records':{'type':'array','items':REF},'keep_recent_exchanges':N}),
 tool('context_status','Expand the compact tail context facts: inspect the exact account snapshot, full selections and policies of the last finalized request, with direct account/request reads and a manifest history_read action. Use when unsure what is injected or will survive restart. Distinguishes automatic inclusion, retrieval-only evidence and persistent files/jobs. Pending sibling calls and working-file edits are not reflected until the batch compiles; check their outcomes before relying on new notes.',{}),
 tool('context_configure','Stage presentation/context policies until all calls in this response finish. Multiple calls merge in order and are validated together with restart against min(input ceiling,total window-output reserve); rejection retains old policies/selection and ordinary effects. Body masking only bounds tool output; use context_restart to replace completed investigations. reasoning_replay=all (default), latest, or none controls only native Chat string reasoning_content/reasoning, not structured reasoning_details or Responses output. latest keeps plain fields only on the newest retained exchange (not the latest that has reasoning); none hides all plain reasoning. Stored assistant history stays intact; all restores surviving fields without reexecution. Exact evidence and other text are not filtered. Newest-exchange tool output is protected from body-policy masking even with keep_recent=0; requested output limits and hard-cap paging still apply. mask_at_tokens=0 restores surviving policy-masked outputs to their prior bounded displays; increasing keep_recent restores that protected suffix. Removed exchanges are not restored and tools are not rerun. pressure_at_tokens only advises, never forces restart. output_tokens reserves generation including counted reasoning. preview_head_percent controls command preview split.',{'mask_at_tokens':N,'pressure_at_tokens':P,'keep_recent':N,'output_tokens':P,'preview_head_percent':{'type':'integer','minimum':0,'maximum':100},'reasoning_replay':{'type':'string','enum':['all','latest','none']}}),
 tool('verification','Version and execute requirement verification intentions. plan/revise require source_ids from task(show) OR an exact requirement quote, plus public entrypoint, concrete scenario, expected result and reusable shell command. With source_ids the harness preserves original text; optional requirement describes your fallible interpretation. Later user instructions can still be cited by exact quote. revise needs id and reason; omitted fields keep their prior values, while old versions and executions remain historical. target optionally records/selects an execution route; run needs action and id; an identical repeated command is accepted, a changed command requires revise. Execution uses normal process capture; poll its job. interpret records a fallible judgment, evidence paths and reasoning, NOT certified correctness. status is read-only. Intentions persist in runtime across context replacement; no all-green gate.',{'action':{'type':'string','enum':['plan','revise','run','interpret','status']},'id':S,'source_ids':{'type':'array','items':S,'minItems':1},'requirement':S,'entrypoint':S,'scenario':S,'expected':S,'command':S,'reason':S,'assessment':{'type':'string','enum':['supported','contradicted','blocked','ambiguous','unresolved','obsolete']},'evidence':S,'wait_seconds':{'type':'number','minimum':0},'output_chars':ALLOW,'target':S},['action']),
 tool('finish','Request completion with an honest assessment of delivered behavior, executed checks, and unresolved issues. The ordinary solve-review workflow starts one fresh same-model review/repair owner at the solver finish; the reviewer finish ends the run. The harness manages that transition, not a model-selected delegation. New user input can defer stale calls; active process teardown returns effects to the same final owner before completion is accepted. No passing-test gate. Later sibling actions are skipped; claim, cleanup and export have separate outcomes.',{'summary':S},['summary']),
]


def validate(name, arguments, tools=TOOLS):
    spec = next((t['function']['parameters'] for t in tools if t['function']['name']==name),None)
    if spec is None:
        raise ValueError(f'Unknown tool: {name}')
    def check(value, schema, path):
        if 'anyOf' in schema:
            for alternative in schema['anyOf']:
                try:
                    check(value,alternative,path)
                    return
                except ValueError:
                    pass
            raise ValueError(f'{path}: expected full or an object such as '+str({'bytes':[0,100]})+'; do not stringify the object')
        expected = schema['type']
        kinds = expected if isinstance(expected,list) else [expected]
        types = {'string':(str,), 'integer':(int,), 'number':(int,float),'boolean':(bool,), 'array':(list,), 'object':(dict,), 'null':(type(None),)}
        if not any(type(value) in types[k] for k in kinds):
            raise ValueError(f'{path} must be {expected}')
        if value is None:
            return
        if type(value) is float and not math.isfinite(value):
            raise ValueError(f'{path} must be finite')
        for bound, invalid in (('minimum',lambda a,b:a<b),('maximum',lambda a,b:a>b),('exclusiveMinimum',lambda a,b:a<=b)):
            if bound in schema and invalid(value,schema[bound]):
                raise ValueError(f'{path} violates {bound}={schema[bound]}')
        if 'enum' in schema and value not in schema['enum']:
            raise ValueError(f'{path}: expected one of {schema["enum"]}')
        if isinstance(value,dict):
            extra, missing = set(value)-set(schema['properties']),set(schema['required'])-set(value)
            if extra or missing:
                raise ValueError(f'{path}: unexpected {sorted(extra)}, missing {sorted(missing)}')
            for key,item in value.items():
                check(item,schema['properties'][key],path+'.'+key)
        if isinstance(value,str):
            if len(value)<schema.get('minLength',0) or len(value)>schema.get('maxLength',len(value)):
                raise ValueError(f'{path}: wrong string length')
        if isinstance(value,list):
            if len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',len(value)):
                raise ValueError(f'{path}: wrong number of items')
            for item in value:
                check(item,schema['items'],path+'[]')
    check(arguments,spec,'arguments')


from .execution import ExecutionTargets
from .task_state import TaskState


class Tools:
    def __init__(self, repo, run, store, original_task='', execution=None, task_context='full'):
        self.repo_alias = str(Path(repo).absolute())
        self.repo, self.run, self.store = Path(repo).resolve(), Path(run).resolve(), store
        self.jobs = Jobs(self.run/'artifacts'/'jobs',store)
        self.original_task = original_task
        self.execution = ExecutionTargets(self.repo, **(execution or {}))
        self.task_state = TaskState(original_task, self.run/'task', task_context)
        self.intentions = None
        self.done = None
        self.effects = 'none'

    def path(self, name):
        return (self.repo/Path(name).expanduser()).resolve()

    def read(self, path, range=None, encoding='utf8', output_chars=12000):
        path = self.path(path)
        if not path.is_file():
            raise ValueError(f'Not a regular readable file: {path}; use bash to list directories')
        # Reading a named immutable blob also verifies its identity.
        if path.parent == self.store.blobs:
            artifact = self.store.artifact(path.name)
            read_kind = 'immutable_artifact'
        else:
            job = next((j for j in self.jobs.jobs.values() if j['path']==path),None)
            state = self.jobs.state(job) if job else None
            artifact = self.store.snapshot(path, state['observed_bytes'] if state else None)
            read_kind = 'new_live_process_snapshot' if job else 'new_file_snapshot'
            if state:
                artifact.update(job_id=state['job_id'], job_byte_range=[0,artifact['observed_bytes']],
                                process_state=state['state'], capture_status=state['capture_status'],
                                process_observed_at=state['observed_at'])
                if state['stdin_error']:
                    artifact.update(stdin_error=state['stdin_error'], stdin_pending_bytes=state['stdin_pending_bytes'])
        artifact['observation_kind'] = read_kind
        output = {**view(artifact,{'lines':[1,250]} if range is None else range,output_chars,encoding), 'action':'read'}
        record = self.store.evidence(artifact,kind='file_read',selected_byte_range=output['selected_byte_range'])
        return {'output':output,'path':str(path),'read_kind':read_kind,'observation_record':record}

    def search(self, path, query, regex=False, context_lines=2, max_matches=20,
               start_line=1, output_chars=12000, source_record=None):
        if not query:
            raise ValueError('query must not be empty')
        try:
            pattern = re.compile(query if regex else re.escape(query))
        except re.error as exc:
            raise ValueError(f'Invalid search regex: {exc}') from exc
        if source_record is None:
            captured = self.read(path, range='full', output_chars=0)
            record = captured['observation_record']
            source = self.store.history(record)
        else:
            # A continuation carries its explicit observation, not merely an
            # equal-content blob. Never infer a live path from a shared hash.
            source = self.store.history(source_record)
            if source.get('observation_kind') not in {'new_file_snapshot', 'immutable_artifact', 'new_live_process_snapshot'}:
                raise ValueError('source_record must name an original read observation')
            if self.path(path) != Path(source['path']):
                raise ValueError('source_record requires its immutable artifact path; no live file was read')
            record = source_record
        data = Path(source['path']).read_bytes()
        # Keep LF/CRLF source bytes exact; Python splitlines would also split on
        # non-newline Unicode separators, unlike file line/range navigation.
        text = data.decode('utf-8')
        parts = text.split('\n')
        lines = [line+'\n' for line in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1]+len(line.encode('utf-8')))
        matches, next_line = [], None
        for index in range(start_line-1, len(lines)):
            line = lines[index].rstrip('\r\n')
            found = pattern.search(line)
            if found is None:
                continue
            if len(matches) == max_matches:
                next_line = index+1
                break
            first, last = max(0,index-context_lines), min(len(lines),index+context_lines+1)
            bounds = [offsets[first],offsets[last]]
            match_start = offsets[index]+len(line[:found.start()].encode('utf-8'))
            match_end = offsets[index]+len(line[:found.end()].encode('utf-8'))
            matches.append({'line':index+1,'match_byte_range':[match_start,match_end],
                            'snippet_lines':[first+1,last],'snippet_byte_range':bounds,
                            'text':data[bounds[0]:bounds[1]].decode('utf-8'),
                            'read':{'path':source['path'],'range':{'bytes':bounds}},
                            'retain':{'record_id':record,'part':'payload','view':{'bytes':bounds}}})
        page = {'source':source,'query':query,'regex':regex,'line_count':len(lines),
                'scope':{'start_line':start_line,'through_line':next_line-1 if next_line else len(lines),
                         'exhausted':next_line is None},'matches':matches,
                'continuation':dict(path=source['path'],query=query,regex=regex,context_lines=context_lines,
                                     max_matches=max_matches,start_line=next_line,output_chars=output_chars,
                                     source_record=record) if next_line else None}
        artifact = self.store.artifact(self.store.blob(json.dumps(page,ensure_ascii=False)))
        artifact.update(observation_kind='file_search_page',source_record=record)
        page_record = self.store.evidence(artifact,kind='file_search',observation_record=record)
        return {'output':{**view(artifact,output_chars=output_chars),'action':'search'},
                'observation_record':page_record,'source_record':record,'source_sha256':source['artifact_id'],
                'search_scope':page['scope'],'search_continuation':page['continuation'],
                'pagination':'search_continuation searches the next page; output.next_read retrieves omitted bytes of this page'}

    @staticmethod
    def check_version(before, expected_sha256):
        actual = hashlib.sha256(before).hexdigest() if before is not None else 'missing'
        if expected_sha256 is not None:
            if expected_sha256 != 'missing' and not re.fullmatch('[0-9a-f]{64}', expected_sha256):
                raise ValueError('expected_sha256 must be a lowercase SHA256 or missing')
            if actual != expected_sha256:
                raise ValueError(f'File version mismatch: expected {expected_sha256}, observed {actual}; file unchanged')
        return actual

    def replace(self, path, content, before):
        encoded = content.encode('utf-8')
        if before is not None and before == encoded:
            self.effects = 'none'
            artifact = self.store.artifact(self.store.blob(
                'No change: supplied content is identical to the observed file; no replacement was performed.\n'))
            artifact.update(observation_kind='file_unchanged', target_path=str(path))
            record = self.store.evidence(artifact, kind='file_unchanged', path=str(path))
            digest = self.check_version(before, None)
            return {'output': view(artifact, output_chars=12000), 'path': str(path),
                    'created': False, 'changed': False, 'observation_record': record,
                    'before_sha256': digest, 'after_sha256': digest,
                    'notice': 'No change: supplied content equals the observed file.'}
        self.effects = 'parents_may_have_been_created'
        path.parent.mkdir(parents=True,exist_ok=True)
        fd, name = tempfile.mkstemp(dir=path.parent,prefix='.mads-')
        temporary = Path(name)
        try:
            with os.fdopen(fd,'wb') as output:
                if before is not None:
                    mode = stat.S_IMODE(path.stat().st_mode)
                else:
                    mask = os.umask(0); os.umask(mask)
                    mode = 0o666 & ~mask
                os.fchmod(output.fileno(),mode)
                output.write(content.encode('utf-8'))
                output.flush(); os.fsync(output.fileno())
            os.replace(temporary,path)
            self.effects = 'file_replaced'
        finally:
            temporary.unlink(missing_ok=True)
        try:
            old = before.decode('utf-8') if before is not None else ''
            lossy = False
        except UnicodeDecodeError:
            old = before.decode('utf-8',errors='replace')
            lossy = True
        diff = ''.join(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n'
            for line in difflib.unified_diff(old.splitlines(keepends=True),content.splitlines(keepends=True),str(path),str(path)))
        artifact = self.store.artifact(self.store.blob(diff))
        artifact.update(observation_kind='file_change_diff', target_path=str(path))
        record = self.store.evidence(artifact,kind='file_change',path=str(path),diff_encoding='utf8',
                                     before_bytes=len(before) if before is not None else None, diff_lossy_before=lossy)
        return {'output':view(artifact,output_chars=12000),
                'path':str(path),'created':before is None,'changed':True,'observation_record':record,'diff_lossy_before':lossy,
                'before_sha256':self.check_version(before,None),'after_sha256':hashlib.sha256(content.encode('utf-8')).hexdigest()}

    def edit(self, path, old, new, replace_all=False, expected_sha256=None):
        path = self.path(path)
        if not old:
            raise ValueError('old must not be empty')
        before = path.read_bytes()
        self.check_version(before,expected_sha256)
        text = before.decode('utf-8')
        count = text.count(old)
        if count == 0 or count > 1 and not replace_all:
            raise ValueError(f'Literal old matched {count} times; file unchanged')
        result = self.replace(path,text.replace(old,new,-1 if replace_all else 1),before)
        result['output']['action'] = 'edit'
        return {**result,'matched_occurrences':count,
                'replacements':(count if replace_all else 1) if result['changed'] else 0}

    def write(self, path, content, expected_sha256=None):
        path = self.path(path)
        before = path.read_bytes() if path.exists() else None
        self.check_version(before,expected_sha256)
        result = self.replace(path,content,before)
        result['output']['action'] = 'write'
        return result

    def bash(self, command, wait_seconds=10, timeout_seconds=None, output_chars=16000, target=None):
        plan = self.execution.plan(command, target)
        self.effects = 'process_launch_attempted'
        count=len(self.jobs.jobs)
        try:
            job_id = self.jobs.start(command,plan.cwd,timeout_seconds, argv=plan.argv, launch=plan.provenance())
        except BaseException:
            if len(self.jobs.jobs)>count:
                self.effects = 'process_started'
            raise
        self.effects = 'process_started'
        result = self.poll(job_id,wait_seconds,output_chars)
        result['output']['action'] = 'bash'
        return result

    def environment(self, action='list', name=None, argv_prefix=None, cwd=None,
                    transport='argv', description='', command=None):
        self.effects = 'none'
        if command is not None and action != 'preview':
            raise ValueError('command is only for environment action=preview; use bash to execute it')
        if action == 'list':
            return self.execution.describe()
        if action == 'preview':
            return {'launch_preview': self.execution.preview(command, name)}
        if action == 'set':
            result = self.execution.set(name, argv_prefix=[] if argv_prefix is None else argv_prefix,
                                        cwd=cwd, transport=transport, description=description)
        elif action == 'default':
            result = self.execution.select(name)
        elif action == 'remove':
            result = self.execution.remove(name)
        else:
            raise ValueError('Unknown environment action')
        self.effects = 'execution_configuration_changed'
        self.store.add('execution_configuration', configuration=self.execution.snapshot())
        if action in ('set', 'default'):
            result['launch_preview'] = self.execution.preview(target=name)
        return result

    def task(self, action='show', source_ids=None, key=None, claim=None,
             state=None, evidence=None, mode=None):
        allowed = {'show': {'source_ids'}, 'focus': {'source_ids', 'mode'},
                   'note': {'source_ids', 'key', 'claim', 'state', 'evidence'},
                   'remove': {'key'}}
        if action not in allowed:
            raise ValueError('Unknown task action')
        supplied = dict(source_ids=source_ids, key=key, claim=claim,
                        state=state, evidence=evidence, mode=mode)
        extra = [name for name, value in supplied.items()
                 if value is not None and name not in allowed[action]]
        if extra:
            raise ValueError(f'task(action={action!r}) does not accept {extra}; '
                             'to save a note use action="note", key and claim. No note was changed.')
        if action == 'show':
            return self.task_state.packet(source_ids if source_ids is not None else
                                          [u['id'] for u in self.task_state.units])
        if action == 'focus':
            result = self.task_state.focus(mode, source_ids)
        elif action == 'note':
            result = self.task_state.note(key, [] if source_ids is None else source_ids,
                                          claim, 'open' if state is None else state,
                                          '' if evidence is None else evidence)
        elif action == 'remove':
            result = self.task_state.remove(key)
        else:
            raise ValueError('Unknown task action')
        self.effects = 'task_notes_or_focus_changed'
        self.store.add('task_state', state=result)
        if action == 'note':
            return {'receipt': f'Saved task note {key!r}.',
                    'note': {'key': key, **result['notes'][key]},
                    'meaning': 'Stored agent interpretation, not verified task completion.'}
        return result

    def poll(self, job_id, wait_seconds=10, output_chars=16000):
        state, artifact = self.jobs.observe(job_id,wait_seconds)
        artifact['process_state'] = state['state']
        if state['stdin_error']:
            artifact.update(stdin_error=state['stdin_error'], stdin_pending_bytes=state['stdin_pending_bytes'])
        record = self.store.evidence(artifact,kind='process_output',process=state)
        return {'output':{**view(artifact,output_chars=output_chars,tail=True), 'action':'poll'},
                'process':state,'observation_record':record}

    def send(self, job_id, text, close=False):
        job = self.jobs.jobs[job_id]
        # An accepted queue must remain an effect even if archiving its failed
        # delivery later raises. Hold the reentrant lock across this comparison.
        with job['lock']:
            before = (job['input_accepted'], job['input_closed'])
            try:
                result = self.jobs.send(job_id,text,close)
            except BaseException:
                if (job['input_accepted'], job['input_closed']) != before:
                    self.effects = 'input_accepted'
                raise
        self.effects = 'input_accepted'
        receipt = (f"send: accepted {result['accepted_bytes']} UTF-8 bytes into job {job_id}'s input queue; "
                   f"{result['pending_bytes']} bytes were pending at this observation. "
                   f"EOF {'requested after queued input drains' if result['eof_requested'] else 'not requested'}. "
                   'Acceptance does not establish delivery or consumption. Do not resend accepted bytes; poll for process and input state.')
        if result.get('stdin_error'):
            receipt += ' Input error: '+result['stdin_error']
        return {'receipt':receipt, **result, 'next_poll':{'job_id':job_id}}

    def cancel(self, job_id):
        job=self.jobs.jobs[job_id]
        self.effects = 'termination_requested'
        failure = None
        try:
            self.jobs.terminate(job,'cancelled')
        except TerminationError as exc:
            failure = exc
        result = self.poll(job_id,0)
        result['termination_attempts'] = job.get('termination_attempts', [])
        if failure is not None:
            result.update(status='failed', error=str(failure), termination_attempts=failure.attempts)
        result['output']['action'] = 'cancel'
        return result

    def history_read(self, record_id, part='payload', range='full', encoding='utf8', output_chars=12000):
        artifact = self.store.history(record_id,part)
        return {'output':{**view(artifact,range,output_chars,encoding), 'action':'history_read'},
                'historical_data':True,'source_record':record_id,'related_records':self.store.related(record_id)}

    def evidence_check(self, record_ids, output_chars=12000):
        # Validate every reference before observing current files. A search page
        # explicitly names its source; do not guess provenance from equal hashes.
        sources = []
        for record_id in record_ids:
            artifact = self.store.history(record_id)
            if artifact.get('observation_kind') == 'file_search_page':
                artifact = self.store.history(artifact['source_record'])
            sources.append((record_id, artifact))
        checks = []
        for record_id, source in sources:
            entry = {'record_id':record_id, 'source_record':source['record_id'],
                     'original_read':{'path':source['path'],'range':'full'},
                     'original_sha256':source['artifact_id']}
            if source.get('observation_kind') != 'new_file_snapshot' or not source.get('source_path'):
                entry.update(status='no_file_source', reason='Not a saved whole-file observation with a live source path')
            else:
                path = source['source_path']
                entry.update(source_path=path, original_observed_at=source.get('observed_at'))
                try:
                    mode = Path(path).stat().st_mode
                    if not stat.S_ISREG(mode):
                        raise OSError('Current source is not a regular file')
                    current = self.store.snapshot(path)
                    current['observation_kind'] = 'new_file_snapshot'
                    fresh = self.store.evidence(current,kind='file_read',selected_byte_range=[0,current['observed_bytes']])
                    entry.update(current_sha256=current['artifact_id'], checked_at=current['observed_at'],
                                 current_record=fresh, current_read={'path':current['path'],'range':'full'},
                                 current_retain={'record_id':fresh,'part':'payload','view':'full'})
                    if source.get('changed_during_capture') or current.get('changed_during_capture'):
                        entry.update(status='unstable', reason='Source changed during at least one capture; no stable version conclusion')
                    else:
                        entry['status'] = 'same' if current['artifact_id'] == source['artifact_id'] else 'changed'
                except FileNotFoundError:
                    entry.update(status='missing', checked_at=now())
                except OSError as exc:
                    entry.update(status='unavailable', checked_at=now(), reason=str(exc))
            checks.append(entry)
        page = {'checks':checks, 'scope':'Sampled bytes at resolved source paths only; not semantic truth, test validity or an atomic repository snapshot'}
        artifact = self.store.artifact(self.store.blob(json.dumps(page,ensure_ascii=False)))
        artifact['observation_kind'] = 'evidence_check_page'
        record = self.store.evidence(artifact,kind='evidence_check')
        return {'output':{**view(artifact,output_chars=output_chars),'action':'evidence_check'},'observation_record':record}

    def history_search(self, **arguments):
        result = self.store.search(**arguments)
        # Search results themselves can be large; use the same bounded delivery path.
        artifact = self.store.artifact(self.store.blob(json.dumps(result,ensure_ascii=False)))
        artifact.update(observation_kind='journal_search_page', search_head=result['head'])
        return {'output':{**view(artifact), 'action':'history_search'},
                'historical_data':True,'search_head':result['head'],'search_continuation':result['continuation']}

    def review(self, paths=None, base='HEAD', output_chars=18000):
        from .review import changes
        return changes(self, paths, base, output_chars)

    def verification(self, **arguments):
        if self.intentions is None:
            raise ValueError('Verification-intent mode is not enabled for this run')
        return self.intentions.action(self, **arguments)

    def finish(self, summary):
        self.done = summary
        return {'completion_claim':summary}
