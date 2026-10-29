"""Local-only frozen experiments: prepare once, replay inference, run stock task runner."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
import threading

HERE = Path(__file__).resolve().parent

def digest(data):
    return hashlib.sha256(data).hexdigest()

def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def write(path, value):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('w') as out:
        out.write(json.dumps(value, indent=2)+'\n');out.flush();os.fsync(out.fileno())
    os.replace(temporary,path)

def hashes(root):
    return {str(p.relative_to(root)): digest(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}

def require_serving_executable(path):
    # Docker bind-mounts this helper and engine/launch.sh executes it directly.
    # Byte hashes alone do not detect a source reconstruction losing Git's +x.
    if path.is_symlink() or not path.is_file() or not path.stat().st_mode & 0o111:
        raise ValueError('Serving helper must be a regular executable file: '+str(path))

def verify(root):
    frozen = json.loads((root/'experiment.json').read_text())
    for name, expected in frozen['files'].items():
        if digest((root/name).read_bytes()) != expected:
            raise ValueError('Frozen file changed: ' + name)
    for name in frozen.get('input_roots',[]):
        actual={str(Path(name)/n):h for n,h in hashes(root/name).items()}
        expected={n:h for n,h in frozen['files'].items() if n.startswith(name+'/')}
        if actual!=expected:raise ValueError('Frozen input inventory changed: '+name)
    if 'tools/a4-serve' in frozen['files']:
        require_serving_executable(root/'tools/a4-serve')
    return frozen

def prepare(args):
    from prepare_cohort import prepare as cohort
    if args.decode_tokens<1 or args.concurrency<1:
        raise ValueError('Replay decode length and concurrency must be positive')
    source = args.source.resolve()
    from build_harness import validate
    build_receipt = validate(source, args.binary)
    identity=json.loads((source/'engine/engine.json').read_text())
    if identity.get('engine')!='sglang' or identity.get('model')!='nvidia/Qwen3.6-35B-A3B-NVFP4@1355db6a052410cfd62085d94b58866fd0f2c3c5':
        raise ValueError('This replica profile supports the fixed course SGLang/checkpoint contract; add a calibrated serving profile before changing it')
    binary = args.binary.read_bytes()
    if binary[:4] != b'\x7fELF' or binary[18:20] != b'\x3e\x00':
        raise ValueError('Need the actual Linux x86-64 harness binary')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    selected = cohort(args.manifest, root/'cohort', args.count, args.seed, args.workers)
    course = root/'course'
    course.mkdir()
    for name in ['dispatcher', 'evaluation_scripts']:
        shutil.copytree(args.course/name, course/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(args.course/'madsServe.py', course/'madsServe.py')
    for name in ['src', 'engine']:
        shutil.copytree(source/name, course/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'investigator'))
    (course/'src/investigator').write_bytes(binary)
    (course/'src/investigator').chmod(0o755)
    shutil.copy2(root/'cohort/agent_task_input.json', course/'evaluation_scripts/agent_task_input.json')
    if 'task' in args.stages.split(','):
        from course_runtime import install
        install(course,HERE.parent/'assets/course-runtime/sandbox_server.py')
    tools = root/'tools'; tools.mkdir()
    for tool in HERE.iterdir():
        if tool.is_file() and (tool.suffix in ('.py','.sh') or tool.name in ['a4-serve','Dockerfile.gpu']):
            shutil.copy2(tool,tools/tool.name)
    workload = root/'workload'; workload.mkdir()
    database = sqlite3.connect(args.trace.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        # Read three representative objects rather than materializing the full history.
        count=database.execute("SELECT count(*) FROM objects WHERE kind='transport_request'").fetchone()[0]
        if not count:raise ValueError('No actual transport requests in trace')
        cases=[]
        for label,index in zip(['short','middle','long'],dict.fromkeys([0,count//2,count-1])):
            ident,body=database.execute("SELECT id,body FROM objects WHERE kind='transport_request' ORDER BY length(body),id LIMIT 1 OFFSET ?",(index,)).fetchone()
            request=json.loads(body)['request']
            write(workload/(label+'.json'),request)
            cases.append({'name':label,'file':label+'.json','source_object_id':ident,
                          'request_sha256':file_digest(workload/(label+'.json'))})
    finally:
        database.close()
    config = {
        'format':1, 'lab_root':str(HERE.parent), 'created_at':datetime.now(timezone.utc).isoformat(),
        'source_commit':subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip(),
        'build_receipt':build_receipt,
        'source_dirty':bool(subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip()),
        'model':json.loads((course/'engine/engine.json').read_text()),
        'submitted_flags':shlex.split((course/'engine/flags.txt').read_text(),comments=True),
        'trace_sha256':file_digest(args.trace), 'cases':cases,
        'tooling_files':{p.name:digest(p.read_bytes()) for p in HERE.iterdir() if p.is_file() and (p.suffix in ('.py','.sh') or p.name in ['a4-serve','Dockerfile.gpu'])},
        'environment':selected['environment'], 'runner':selected['runner'],
        'input_roots':['course/src','course/engine','course/dispatcher','course/evaluation_scripts','tools','workload','cohort'],
        'task_count':args.count, 'stages':args.stages.split(','),
        'replay':{'decode_tokens':args.decode_tokens,'concurrency':args.concurrency},
        'files':hashes(root),
        'authority':'Source and engine snapshots are frozen. Local observations never change submission policy.',
        'calibration':{'exact_course_runtime':False,
                       'known_differences':['Prior Docker PyTorch runtime is cu130; course CUDA compiler is 13.4.',
                         'Course driver, GPU power and clocks have not been independently measured.',
                         'Prior successful AWS run needed memory fraction 0.85; submitted flags remain 0.90.'],
                       'claim':'Matched GPU/checkpoint integration; not calibrated absolute leaderboard timing.'}}
    write(root/'experiment.json',config)
    return {'prepared':str(root),'tasks':args.count,'cases':len(cases),'model':config['model']}

class Client:
    def __init__(self, base, key):
        parsed=urllib.parse.urlsplit(base)
        if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.path not in ['', '/'] or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Use a server origin without /v1, credentials, query or fragment')
        self.base = base.rstrip('/'); self.key=key
    def call(self, path, body=None, method=None):
        request=urllib.request.Request(self.base+path, data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.key},method=method)
        try:
            with urllib.request.urlopen(request,timeout=600) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code,error.read()

def replay(args):
    root=args.experiment.resolve(); frozen=verify(root)
    if frozen.get('replay',{}).get('workload_sweep'):
        from workload_replay import validate_workload
        validate_workload(root,frozen,args.model)
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    key=args.key_file.read_text().strip();client=Client(args.base_url,key)
    # Endpoint must be exclusively owned. Never flush a shared production endpoint.
    status,raw=client.call('/server_info');(out/'server-info.json').write_bytes(raw.replace(key.encode(),b'[REDACTED]'))
    if status!=200:raise ValueError('Cannot inspect actual engine: HTTP '+str(status))
    status,raw=client.call('/v1/models');(out/'models.json').write_bytes(raw)
    if status!=200:raise ValueError('Cannot inspect model identity')
    model=json.loads(raw)['data']
    if len(model)!=1 or model[0]['id']!=args.model:raise ValueError('Unexpected served model identity')
    if frozen.get('replay',{}).get('workload_sweep'):
        from workload_replay import run
        return run(args,root,frozen,out,key,client)
    rows=[];flushes=[]
    concurrent_elapsed=None;concurrent_warm_elapsed=None
    concurrent_names=frozen.get('concurrent_cases',[frozen['cases'][-1]['name']])
    by_name={case['name']:case for case in frozen['cases']}
    if not concurrent_names or any(name not in by_name for name in concurrent_names):
        raise ValueError('Concurrent workload must name actual frozen cases')
    if args.concurrency>1 and len(concurrent_names) not in (1,args.concurrency):
        raise ValueError('Use one shared case or one distinct case per concurrent request')
    def flush(label):
        status,raw=client.call('/flush_cache',method='POST')
        flushes.append({'label':label,'status':status,'response':raw.decode(errors='replace')})
        write(out/'cache-resets.json',flushes)
        if status!=200:raise ValueError('Cache reset failed; refusing to label request cold')
    def run(case,phase,index):
        request=json.loads((root/'workload'/case['file']).read_text())
        # Explicit benchmark-only decode bound; original request is retained separately.
        request.update(model=args.model,stream=True,stream_options={'include_usage':True},temperature=0,max_tokens=args.decode_tokens)
        request['rid']=uuid.uuid4().hex
        request.pop('max_completion_tokens',None)
        request.pop('max_output_tokens',None)
        if frozen.get('fixed_decode',False):
            request.update(min_tokens=args.decode_tokens,ignore_eos=True)
            request.pop('stop',None);request.pop('stop_token_ids',None)
        name=f'{case["name"]}-{phase}-{index}'
        write(out/(name+'.request.json'),request)
        started=time.perf_counter(); first=None; last_chunk=None; max_chunk_gap=0.0; usage=None; finish=None; done=False; chunks=0;response_id=None
        record={'started_at':datetime.now(timezone.utc).isoformat(),'case':case['name'],'phase':phase,'index':index,'status':None,
                'source_request_sha256':case['request_sha256'],'decode_limit':args.decode_tokens}
        req=urllib.request.Request(client.base+'/v1/chat/completions',data=json.dumps(request).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
        try:
            with urllib.request.urlopen(req,timeout=600) as response, (out/(name+'.response.sse')).open('wb') as archive:
                record['status']=response.status
                for line in response:
                    archive.write(line)
                    if not line.startswith(b'data:'):continue
                    payload=line[5:].strip()
                    if payload==b'[DONE]':done=True;continue
                    value=json.loads(payload);usage=value.get('usage') or usage;response_id=value.get('id') or response_id
                    for choice in value.get('choices',[]):
                        delta=choice.get('delta') or {}
                        if delta.get('content') or delta.get('reasoning_content') or delta.get('tool_calls'):
                            chunks+=1
                            observed=time.perf_counter()
                            if first is None:first=observed-started
                            if last_chunk is not None:max_chunk_gap=max(max_chunk_gap,observed-last_chunk)
                            last_chunk=observed
                        finish=choice.get('finish_reason') or finish
                if not done or finish is None:raise ValueError('Incomplete stream')
                if frozen.get('fixed_decode',False) and (not usage or usage.get('completion_tokens')!=args.decode_tokens):
                    raise ValueError('Fixed-length throughput sample has missing or unequal generation usage')
        except urllib.error.HTTPError as error:
            record.update(status=error.code,error='HTTP error');(out/(name+'.response.error')).write_bytes(error.read())
        except Exception as error:
            record['error']=type(error).__name__+': '+str(error)
        if record.get('error'):
            status,raw=client.call('/abort_request',{'rid':request['rid']},method='POST')
            record['cancellation']={'status':status,'body':raw.decode(errors='replace')}
        elapsed=time.perf_counter()-started
        record.update(finished_at=datetime.now(timezone.utc).isoformat(),response_id=response_id,elapsed_s=elapsed,first_generated_chunk_s=first,usage=usage,finish_reason=finish,stream_complete=done,generated_chunks=chunks,max_generated_chunk_gap_s=max_chunk_gap)
        # Chunk count is not token count. Throughput uses only reported generation usage.
        record['completion_tokens_per_second']=usage['completion_tokens']/elapsed if usage and 'completion_tokens' in usage else None
        write(out/(name+'.receipt.json'),record)
        return record
    profile=frozen.get('profile');profile_receipts=[]
    if profile is not None:
        target='/root/.cache/profiles/'+uuid.uuid4().hex
        status,raw=client.call('/start_profile',dict(profile,output_dir=target),method='POST')
        profile_receipts.append({'operation':'start','status':status,'body':raw.decode(errors='replace'),'output_dir':target});write(out/'profile.json',profile_receipts)
        if status!=200:raise ValueError('Profiler did not start; no replay inference admitted')
    try:
        repetitions=frozen.get('replay',{}).get('repetitions',1)
        if type(repetitions)!=int or repetitions<1:raise ValueError('Positive integer replay repetitions required')
        batches=[]
        for trial in range(repetitions):
            for case in frozen['cases']:
                flush(case['name']+'-cold-'+str(trial));rows.append(run(case,'cold',trial))
                if rows[-1].get('error'):raise ValueError('Cold request failed')
                rows.append(run(case,'warm',trial))
                if rows[-1].get('error'):raise ValueError('Warm request failed')
            if args.concurrency>1:
                for phase in ['concurrent']+(['concurrent-warm'] if frozen.get('concurrent_warm',False) else []):
                    if phase=='concurrent':flush('concurrent-cold-'+str(trial))
                    batch_started=time.perf_counter()
                    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                        batch=list(pool.map(lambda i:run(by_name[concurrent_names[i % len(concurrent_names)]],phase,trial*args.concurrency+i),range(args.concurrency)))
                    elapsed=time.perf_counter()-batch_started;rows.extend(batch)
                    batches.append({'trial':trial,'phase':phase,'elapsed_s':elapsed,'request_indices':[r['index'] for r in batch]})
                    if any(r.get('error') for r in batch):raise ValueError('Concurrent request failed')
                    if repetitions==1:
                        if phase=='concurrent':concurrent_elapsed=elapsed
                        else:concurrent_warm_elapsed=elapsed
        summary={'completed_at':datetime.now(timezone.utc).isoformat(),'requests':rows,'passed':all(not r.get('error') for r in rows),
                 'configuration':{'repetitions':repetitions,'decode_tokens':args.decode_tokens,'concurrency':args.concurrency,'concurrent_cases':concurrent_names,'concurrent_warm':frozen.get('concurrent_warm',False),'fixed_decode':frozen.get('fixed_decode',False),'profile':profile},
                 'batch_samples':batches,'concurrent_batch_elapsed_s':concurrent_elapsed,'concurrent_warm_batch_elapsed_s':concurrent_warm_elapsed,
                 'claims':'HTTP streaming inference replay only. No generated tool calls execute; not a repair success measurement.',
                 'cache':'Cold means successful radix-cache reset; warm repeats the identical payload. Compiler caches are not cleared.',
                 'experiment_sha256':digest((root/'experiment.json').read_bytes())}
        write(out/'summary.json',summary)
        if not summary['passed']:raise ValueError('Replay has failed requests')
        return summary
    finally:
        write(out/'receipts.json',rows)
        if profile is not None:
            status,raw=client.call('/stop_profile',method='POST')
            profile_receipts.append({'operation':'stop','status':status,'body':raw.decode(errors='replace')});write(out/'profile.json',profile_receipts)
            if status!=200:raise ValueError('Profiler failed to stop; retained profile receipt')

def task(args):
    root=args.experiment.resolve();frozen=verify(root)
    # One use of the frozen course folder; all subsequent runs need a fresh preparation.
    lock=root/'task-run.lock'; fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    execution=root/'execution';execution.mkdir()
    course=execution/'course'
    shutil.copytree(root/'course',course)
    env={k:v for k,v in os.environ.items() if not k.startswith(('A3_','A4_','CS2680_','SWEBENCH_'))}
    env.update(frozen['environment'])
    for name in ['A3_SESSION_ID','A4_BASE_URL','A4_API_KEY','A4_MODEL','A4_INVESTIGATOR_BIN','A4_TRACE_ROOT','A3_ASSETS','A3_HOSTLOG','SWEBENCH_PRO_OS']:
        env.pop(name,None)
    env.update(A3_API_UPSTREAM=args.base_url.rstrip('/'), A3_CACHE=str(args.cache.resolve()),
               CS2680_API_KEY=args.key_file.read_text().strip(),CS2680_MODEL_STANDARD=args.model,
               CS2680_MODEL_EXPERT=args.model,CS2680_MODEL_STARTER=args.model)
    write(course/'dispatcher/student_name.json',{'first_name':args.first_name,'last_name':args.last_name})
    write(root/'task-environment.json',{k:v for k,v in env.items() if k.startswith(('A3_','A4_','CS2680_')) and k!='CS2680_API_KEY'})
    stages=[];active=None;cancelled=False
    def interrupt(signum,frame):
        nonlocal cancelled
        cancelled=True
        if active is not None and active.poll() is None:active.send_signal(signal.SIGTERM)
    previous=signal.signal(signal.SIGTERM,interrupt)
    # Both setup operations can proceed independently; the stock preflight waits for both.
    from readiness import wait as wait_for_serving,wait_for_launch,startup_deadline
    stop_readiness=threading.Event();ready_pool=ThreadPoolExecutor(max_workers=1)
    cancelled_setup=lambda:cancelled or stop_readiness.is_set() or (root.parent/'server.failed').exists()
    def serving_ready():
        # CPU preparation may precede GPU bootstrap completion. Its duration is
        # not model startup time, and even a healthy old endpoint cannot bypass
        # the accepted dispatch receipt for this job and frozen manifest.
        deadline=(wait_for_launch(root.parent,cancelled_setup)
                  if os.environ.get('A4_WAIT_FOR_MODEL_LAUNCH')=='1'
                  else float(os.environ.get('A4_ENGINE_STARTUP_DEADLINE') or startup_deadline()))
        return wait_for_serving(args.base_url,cancelled_setup,deadline)
    ready=ready_pool.submit(serving_ready)
    try:
        for name,command in [('prepare',['bash','evaluation_scripts/prepare_images.sh']),('preflight',['python3','evaluation_scripts/run_all.py','--limit','0']),('task',frozen['runner'])]:
            if cancelled_setup():raise RuntimeError('Task setup cancelled or infrastructure failed')
            if name=='preflight':
                write(root/'serving-ready.json',ready.result())
                if cancelled_setup():raise RuntimeError('Task setup cancelled or infrastructure failed')
            if name=='task':
                status,body=Client(args.base_url,args.key_file.read_text().strip()).call('/flush_cache',method='POST')
                write(root/'task-cache-reset.json',{'status':status,'response':body.decode(errors='replace'),'scope':'Once before the whole submission; compiler caches remain warm'})
                if status!=200:raise ValueError('Pre-submission cache reset failed; solving not started')
            if cancelled_setup():raise RuntimeError('Task setup cancelled or infrastructure failed')
            started=time.time()
            with (root/(name+'.log')).open('wb') as log:
                active=subprocess.Popen(command,cwd=course,env=env,stdout=log,stderr=subprocess.STDOUT)
                if cancelled_setup():active.terminate()
                returncode=active.wait()
            finished=time.time()
            stages.append({'stage':name,'exit_code':returncode,'started_at_unix':started,
                           'finished_at_unix':finished,'elapsed_s':finished-started})
            write(root/'task-stages.json',stages)
            if returncode or cancelled:raise ValueError('Stock '+name+' failed; inspect complete log')
        return {'stages':stages,'note':'Read stock host grading receipts for outcomes; process exit zero alone is not a passing repair.'}
    finally:
        stop_readiness.set();ready_pool.shutdown(wait=True)
        signal.signal(signal.SIGTERM,previous)
        # Export the entire evidence tree, including traces nested under .tasks/trace.
        write(root/'task-completion.json',{'stages':stages,'finished_at':datetime.now(timezone.utc).isoformat()})

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='action',required=True)
    q=sub.add_parser('prepare');q.add_argument('--source',type=Path,required=True);q.add_argument('--course',type=Path,required=True);q.add_argument('--binary',type=Path,required=True);q.add_argument('--manifest',type=Path,required=True);q.add_argument('--trace',type=Path,required=True);q.add_argument('--output',type=Path,required=True);q.add_argument('--count',type=int,default=1);q.add_argument('--seed',type=int,default=20261022);q.add_argument('--workers',type=int,default=1);q.add_argument('--stages',choices=['replay','task','replay,task'],default='replay,task');q.add_argument('--decode-tokens',type=int,default=128);q.add_argument('--concurrency',type=int,default=2)
    for command in ['replay','task']:
        q=sub.add_parser(command);q.add_argument('--experiment',type=Path,required=True);q.add_argument('--base-url',required=True);q.add_argument('--key-file',type=Path,required=True);q.add_argument('--model',default='qwen3.6-35b-a3b-fp4')
        if command=='replay':
            q.add_argument('--output',type=Path,required=True);q.add_argument('--decode-tokens',type=int,default=128);q.add_argument('--concurrency',type=int,default=1);q.add_argument('--exclusive-endpoint',action='store_true',required=True)
        else:
            q.add_argument('--cache',type=Path,required=True);q.add_argument('--first-name',required=True);q.add_argument('--last-name',required=True)
    args=p.parse_args()
    if args.action=='replay' and (args.decode_tokens<1 or args.concurrency<1):p.error('Positive decode tokens and concurrency required')
    print(json.dumps(globals()[args.action](args),indent=2))
