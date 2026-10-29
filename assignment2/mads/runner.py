"""Assignment setup/export; the solver retains ordinary shell and Git access."""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from .routes import route
from .store import dump, atomic_json, Store

CONTAINER = r'''
set -euo pipefail
ulimit -S -c 0
export PYTHONDONTWRITEBYTECODE=1 PIP_INDEX_URL=https://pypi.org/simple PIP_BREAK_SYSTEM_PACKAGES=1
WD=/app
[ -d /app/.git ] || WD=/testbed
cd "$WD"
git reset --hard "$BASE_COMMIT" >&2
git clean -fd >&2
PY=""
for candidate in python3.12 python3.11 python3.10 /opt/miniconda3/bin/python /opt/conda/bin/python python3; do
  if "$candidate" -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then PY="$candidate"; break; fi
done
bootstrap_pip() {
  "$1" -m pip --version >/dev/null 2>&1 && return
  "$1" -m ensurepip >/dev/null 2>&1 || true
  "$1" -m pip --version >/dev/null 2>&1 && return
  "$1" -c 'import urllib.request; urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", "/tmp/mads-get-pip.py")'
  "$1" /tmp/mads-get-pip.py --quiet >&2
}
if [ -z "$PY" ]; then
  bootstrap_pip python3
  python3 -m pip install --quiet uv >&2
  export UV_PYTHON_INSTALL_DIR=/tmp/mads-python
  uv python install 3.12 >&2
  PY="$(uv python find 3.12)"
fi
bootstrap_pip "$PY"
MADS_AGENT_PY="$PY" bash /agent/docker_env.sh
status=0
"$PY" /agent/madsLoop.py "$WD" --task /agent/task.txt --run-dir /output/run --log --log-dir /output/madsLoop_logs --steps "$MADS_STEPS" --mask-tokens "$MADS_MASK_TOKENS" --pressure-tokens "$MADS_PRESSURE_TOKENS" > /output/agent-stdout.log || status=$?
PYTHONPATH=/agent "$PY" -m mads.runner export "$WD" "$BASE_COMMIT" /output/run || status=1
exit "$status"
'''


def load_outcome(run):
    """Recover a complete recorded run_end, never infer cleanup from absence."""
    run = Path(run)
    path = run/'result.json'
    try:
        result = json.loads(path.read_bytes())
        if not isinstance(result, dict) or any(not isinstance(result.get(key), dict)
                for key in ('agent_outcome', 'harness_outcome', 'cleanup_outcome')):
            raise ValueError('Missing outcome objects')
        return result
    except (OSError, ValueError, UnicodeError) as exc:
        diagnostic = f'Outcome file unavailable or malformed: {exc}'
        if path.exists():
            # Retain the exact malformed file even when a new result replaces it.
            saved = Store(run/'recovery').snapshot(path)
            diagnostic += '; original bytes: '+saved['path']
        try:
            journal = Store(run/'journal')
            end = next((row for row in reversed(journal.events) if row['event']=='run_end'), None)
        except (OSError, RuntimeError):
            end = None
        if end is not None and all(isinstance(end.get(key), dict) for key in
                ('agent_outcome', 'harness_outcome', 'cleanup_outcome')):
            result = {k:v for k,v in end.items() if k not in ('id','timestamp','event')}
            result['outcome_recovery'] = {'diagnostic':diagnostic, 'run_end_record':end['id']}
            return result
        return {'agent_outcome':{'status':'unknown'},
                'harness_outcome':{'status':'error','error':diagnostic},
                'cleanup_outcome':{'status':'unknown'}}


def export(repo, base, run):
    repo,run=Path(repo),Path(run)
    result_path=run/'result.json'
    run.mkdir(parents=True, exist_ok=True)
    result=load_outcome(run)
    try:
        if result['cleanup_outcome']['status']!='completed':
            raise RuntimeError('Cannot attest to final repository state: owned-process cleanup incomplete')
        with tempfile.TemporaryDirectory(prefix='mads-export-') as temporary:
            env={**os.environ,'GIT_INDEX_FILE':str(Path(temporary)/'index')}
            def git(*args,**kwargs):
                return subprocess.run(['git','-C',str(repo),*args],env=env,check=True,**kwargs)
            git('read-tree',base)
            git('add','-A')
            with (run/'patch.diff').open('wb') as patch:
                git('diff','--cached','--binary',base,stdout=patch)
        result['export_outcome']={'status':'completed','patch':'patch.diff','partial_work':result['agent_outcome']['status']!='completed' or result['harness_outcome']['status']!='completed'}
    except Exception as exc:
        result['export_outcome']={'status':'failed','error':str(exc)}
    mappings={}
    for path in run.rglob('*'):
        if path.is_file():
            mappings[str(path)]=str(path.relative_to(run))
    atomic_json(run/'export-manifest.json', {'container_root':str(run),'path_mapping':mappings})
    atomic_json(result_path,result)
    return 0 if result['export_outcome']['status']=='completed' else 1


def stage_source(root, code):
    """Ship the portable CLI and its offline checks, without study/task inputs."""
    from experiments.run_checks import CHECKS
    shutil.copytree(root/'mads', code/'mads', ignore=shutil.ignore_patterns('__pycache__'))
    files = {'madsLoop.py', 'requirements.txt', 'docker_env.sh', 'docs/solve-review.md',
             'experiments/run_checks.py', 'experiments/snapshot_workspace.py'}
    files.update(str(check.script) for check in CHECKS.values())
    for name in sorted(files):
        destination = code/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root/name, destination)


def run(index):
    root=Path(os.environ.get('MADSLOOP_REPO',Path(__file__).resolve().parent.parent)).resolve()
    source=Path(os.environ.get('TASKS_JSON',root/'evaluation_scripts/agent_task_input.json')).resolve()
    task=list(json.loads(source.read_text()).values())[index]
    iid=task['instance_id']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',iid):
        raise ValueError('Invalid instance ID')
    # The unchanged staff bundler reads this exact filename. Do not leave a
    # previous attempt's patch looking like the current failed attempt.
    (root/f'model_patch_{iid}.diff').unlink(missing_ok=True)
    attempts=Path(os.environ.get('MADS_BATCH_DIR',root/'.runs'/'latest-attempts')).resolve()
    attempts.mkdir(parents=True,exist_ok=True)
    receipt=attempts/(iid+'.json')
    atomic_json(receipt,dict(instance_id=iid,status='starting',export_outcome={'status':'not_available'}))
    review_path = os.environ.get('MADS_REVIEW_PROMPT')
    review_bytes = Path(review_path).expanduser().read_bytes() if review_path else None
    if review_bytes is not None:
        review_bytes.decode('utf-8')  # validate before container launch
    config=route()
    if not os.environ.get(config['key_env']):
        raise ValueError(config['key_env']+' is required')
    run_id=uuid.uuid4().hex[:12]
    stage=Path(os.environ.get('MADS_RUN_ROOT',root/'.runs')).resolve()/run_id
    code,output=stage/'agent',stage/'output'
    code.mkdir(parents=True);output.mkdir()
    stage_source(root, code)
    sections=[]
    for field in ('problem_statement','requirements','interface'):
        value=task.get(field,'')
        try:
            decoded=json.loads(value)
            if isinstance(decoded,str):
                value=decoded
        except (ValueError,TypeError):
            pass
        sections.append(field+':\n'+value)
    (code/'task.txt').write_text('\n\n'.join(sections))
    if review_bytes is not None:
        (code/'review-instructions.md').write_bytes(review_bytes)
    atomic_json(stage/'launch.json', dict(instance_id=iid, base_commit=task['base_commit'],
        workflow='solve-review', task_source=str(source),
        task_input_sha256=__import__('hashlib').sha256(source.read_bytes()).hexdigest(),
        review_prompt_source=review_path,
        review_prompt_sha256=__import__('hashlib').sha256(review_bytes).hexdigest()
            if review_bytes is not None else None,
        source_copy=str(code), source_access='writable staged copy; loaded Python requires activation',
        image=task['docker_image']))
    name='mads-'+run_id
    args=['docker','run','--name',name,'--platform','linux/amd64','--entrypoint','bash',
          '-v',f'{code}:/agent:rw','-v',f'{output}:/output','-e',config['key_env'],
          '-e',f'MADS_PROVIDER={config["provider"]}','-e',f'MADS_MODEL={config["model"]}',
          *(['-e',f'MADS_REASONING={config["reasoning"]}'] if 'reasoning' in config else []),
          '-e',f'BASE_COMMIT={task["base_commit"]}',
          *(['-e','OPENROUTER_PROVIDER'] if os.environ.get('OPENROUTER_PROVIDER') else []),
          *(['-e','MADS_BASE_URL'] if os.environ.get('MADS_BASE_URL') else []),
          *(['-e','MADS_REASONING_REPLAY'] if 'MADS_REASONING_REPLAY' in os.environ else []),
          *(['-e','MADS_REVIEW_PROMPT=/agent/review-instructions.md'] if review_bytes is not None else []),
          '-e',f'MADS_STEPS={os.environ.get("MADS_STEPS","0")}',
          '-e',f'MADS_MASK_TOKENS={os.environ.get("MADS_MASK_TOKENS","160000")}',
          '-e',f'MADS_PRESSURE_TOKENS={os.environ.get("MADS_PRESSURE_TOKENS","180000")}',
          task['docker_image'],'-c',CONTAINER]
    print(f'Run {run_id}: {iid}; artifacts: {output}',flush=True)
    status=1
    interrupted_signal=None
    finalizing=False

    def interrupted(signum, frame):
        nonlocal interrupted_signal
        interrupted_signal = interrupted_signal or signum
        # A second signal must not interrupt owned-container cleanup and receipts.
        if not finalizing:
            raise KeyboardInterrupt

    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        for sig in previous:
            signal.signal(sig, interrupted)
        try:
            status=subprocess.call(args)
        except KeyboardInterrupt:
            interrupted_signal = interrupted_signal or signal.SIGINT
        finally:
            finalizing=True
            try:
                cleanup=subprocess.run(['docker','rm','-f',name],capture_output=True,text=True)
            except OSError as exc:
                cleanup=subprocess.CompletedProcess(['docker','rm','-f',name], 1, stderr=str(exc))
            result=load_outcome(output/'run')
            result['container_cleanup']={'status':'completed' if cleanup.returncode==0 else 'failed',
                                         'diagnostic':cleanup.stderr}
            try:
                patch=output/'run/patch.diff'
                if result['export_outcome']['status']=='completed':
                    shutil.copy2(patch,root/f'model_patch_{iid}.diff')
                else:
                    status=1
                # Output is already host-persistent; export maps container paths to these files.
                result['exported_run_root']=str(output/'run')
            except Exception as exc:
                result['export_outcome']={'status':'failed','error':str(exc)}
                status=1
            logs=output/'madsLoop_logs'
            if logs.exists():
                destination=root/'madsLoop_logs'/iid/run_id
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copytree(logs,destination)
                result['course_logs']=str(destination)
            if cleanup.returncode:
                status=1
            if interrupted_signal is not None:
                status = 128 + interrupted_signal
            result['launcher_outcome'] = dict(
                status='interrupted' if interrupted_signal is not None else 'completed' if status == 0 else 'failed',
                signal=interrupted_signal, exit_code=status)
            atomic_json(output/'result.json',result)
            atomic_json(receipt,dict(instance_id=iid,run_id=run_id,run_root=str(output/'run'),
                status=result['launcher_outcome']['status'], launcher_outcome=result['launcher_outcome'],
                container_cleanup=result['container_cleanup'],
                patch=str(patch),export_outcome=result['export_outcome'],
                patch_sha256=__import__('hashlib').sha256(patch.read_bytes()).hexdigest()
                    if result['export_outcome']['status']=='completed' else None))
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    return status


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].isdigit():
        argv.insert(0,'run')
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    launch=commands.add_parser('run',help='Launch an assignment task in Docker')
    launch.add_argument('index',type=int)
    launch.add_argument('--run-root',type=Path)
    exported=commands.add_parser('export',help='Export work only with recorded cleanup evidence')
    for name in ('repo','base','run'): exported.add_argument(name)
    args=parser.parse_args(argv)
    try:
        if args.command=='export': return export(args.repo,args.base,args.run)
        if args.index < 0: raise ValueError('Task index must be nonnegative')
        if args.run_root: os.environ['MADS_RUN_ROOT']=str(args.run_root.resolve())
        return run(args.index)
    except (OSError,ValueError,IndexError) as exc:
        parser.error(str(exc))


if __name__=='__main__':
    raise SystemExit(main())
