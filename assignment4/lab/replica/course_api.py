"""Exact course packages on one CPU host; the external service owns inference.

This is the original course prepare/preflight/run path, not a native adapter or
self-hosted serving benchmark. Credentials are supplied only at launch. The
original runner may lose uncaptured work on interruption; this module does not
claim the native adapter's interrupted-workspace preservation guarantee.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import stat
import subprocess
import sys
import time
import zipfile

from iteration import file_digest, hashes, verify, write

HERE = Path(__file__).resolve().parent
PROFILE = 'course-api-cpu'
DEFAULT_PROFILE = 'gpu-cpu'
ORIGIN = 'https://api.cs2680.com'
GRADER_COMMIT = '66f92766bba642462d4bbe5479e83f91f9211862'


def roles(value):
    profile = value.get('resource_profile', DEFAULT_PROFILE)
    if profile not in (DEFAULT_PROFILE, PROFILE, 'image-bake-gpu'):
        raise ValueError('Unknown resource profile: '+str(profile))
    if profile == 'image-bake-gpu': return ('gpu',)
    return ('cpu',) if profile == PROFILE else ('gpu', 'cpu')


def validate(config):
    """Reject an external route disguised as a self-hosted serving experiment."""
    if config.get('resource_profile', DEFAULT_PROFILE) != PROFILE:
        if config.get('stages') == ['course_api']:
            raise ValueError('Course API requires the CPU-only resource profile')
        return False
    if config.get('stages') != ['course_api']:
        raise ValueError('CPU-only course profile requires exactly the course_api stage')
    route = config['course_api']
    if route != {'origin': ORIGIN, 'model': route.get('model')} or route['model'] not in ('standard','expert','starter'):
        raise ValueError('Use an explicit documented course origin/model alias')
    # Historical frozen hourly_ceiling metadata is readable, never an admission gate.
    if config.get('cpu_type') not in ('m7i.2xlarge','m7i.4xlarge','m7i.8xlarge'):
        raise ValueError('Unsupported CPU-only host type')
    expected = environment(config['workers'], config['graders'])
    if config.get('environment') != expected:
        raise ValueError('Course task resource or execution policy changed')
    if type(config['task_count']) is not int or config['task_count'] < 1:
        raise ValueError('Positive task count required')
    return True


def environment(workers, graders):
    if type(workers) is not int or not 1 <= workers <= 4 or type(graders) is not int or not 1 <= graders <= 4:
        raise ValueError('This CPU course profile supports 1–4 workers/graders')
    return {'A4_WORKERS':str(workers), 'A3_MAX_LIVE':str(workers),
            'A3_MAX_GRADING':str(graders), 'A3_RUN_LIMIT_S':'0',
            'A3_SBX_CPUS':'1', 'A3_SBX_MEM':'4g', 'A3_EVAL_TIMEOUT':'2100',
            'A3_CLOCK_FROM':'start', 'DOCKER_DEFAULT_PLATFORM':'linux/amd64'}


def protected_key(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('Course credential must be a private regular file')
    if not path.read_text().strip():
        raise ValueError('Empty course credential')
    return path


def runtime_key(root, path):
    """The launcher owns this exact link; its private target stays out of jobs."""
    path=Path(path)
    target=root.parent.parent/'serve.key'
    if path!=root/'serve.key' or not path.is_symlink() or path.resolve()!=target:
        raise ValueError('Course credential is not the launch-owned job link')
    return protected_key(target)


def forward_workers(course):
    """Forward the selected host worker count; never edit the solver package."""
    runner = course/'evaluation_scripts/run_all.py'
    before = file_digest(runner)
    old = '           "-e", "MADSSERVE_MAX_ITERATIONS",'
    text = runner.read_text()
    if text.count(old) != 1 or '"-e", "A4_WORKERS"' in text:
        raise ValueError('Original course worker-forwarding boundary drifted')
    runner.write_text(text.replace(old, '           "-e", "A4_WORKERS",\n'+old))
    return {'file':'evaluation_scripts/run_all.py', 'before_sha256':before,
            'after_sha256':file_digest(runner), 'environment_key':'A4_WORKERS',
            'scope':'Agent container environment only; solver and task containers unchanged'}


def adapt_common(course):
    """The two already-checked host adaptations; package members stay exact."""
    forward_workers(course)
    proxy = course/'dispatcher/egress_proxy.py'
    old = '                    ua = re.sub(r"\\s*cs2680-session/\\S*", "", self.headers.get("User-Agent", "")).strip()'
    text = proxy.read_text()
    if text.count(old) != 1:
        raise ValueError('Original course HTTP client boundary drifted')
    proxy.write_text(text.replace(old, '                    ua = "cs2680-research-client/1.0"'))


def freeze(args):
    """Prepare locally without credentials, Docker, network or resource admission."""
    public = json.loads(args.manifest.read_text())
    if not args.keys or len(set(args.keys)) != len(args.keys) or any(k not in public for k in args.keys):
        raise ValueError('Select distinct existing public keys')
    entries = {key:public[key] for key in args.keys}
    with zipfile.ZipFile(args.package) as archive:
        members = archive.infolist()
        if not members or len({m.filename for m in members}) != len(members):
            raise ValueError('Package must have distinct members')
        for member in members:
            path = Path(member.filename)
            mode = member.external_attr >> 16
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] not in ('src','engine') or stat.S_ISLNK(mode):
                raise ValueError('Unsafe course package member')
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    course = root/'course'; course.mkdir()
    for name in ('dispatcher','evaluation_scripts'):
        shutil.copytree(args.course/name, course/name, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copy2(args.course/'madsServe.py', course/'madsServe.py')
    shutil.copy2(args.package, root/'package.zip')
    with zipfile.ZipFile(root/'package.zip') as archive:
        for member in members:
            target = course/member.filename
            if member.is_dir(): target.mkdir(parents=True,exist_ok=True); continue
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.read(member)); target.chmod((member.external_attr >> 16) & 0o777 or 0o644)
    write(course/'evaluation_scripts/agent_task_input.json',entries)
    write(course/'dispatcher/student_name.json',{'first_name':args.first_name,'last_name':args.last_name})
    adapt_common(course)
    tools = root/'tools'; tools.mkdir()
    for tool in HERE.iterdir():
        if tool.is_file() and (tool.suffix in ('.py','.sh') or tool.name in ('a4-serve','Dockerfile.gpu')):
            shutil.copy2(tool, tools/tool.name)
    config = {'format':1, 'resource_profile':PROFILE, 'stages':['course_api'],
              'lab_root':str(HERE.parent), 'created_at':datetime.now(timezone.utc).isoformat(),
              'course_api':{'origin':ORIGIN,'model':args.model}, 'workers':args.workers,'graders':args.graders,
              'environment':environment(args.workers,args.graders),'cpu_type':args.cpu_type,
              'task_count':len(entries),
              'stop_batch_on_failure':True, 'package_sha256':file_digest(root/'package.zip'),
              'public_manifest_sha256':file_digest(args.manifest),'source_keys':args.keys,
              'grader_commit':GRADER_COMMIT,
              'tooling_files':{p.name:file_digest(p) for p in tools.iterdir()},
              'input_roots':['course','tools'], 'files':hashes(root),
              'course_file_modes':{str(p.relative_to(course)):stat.S_IMODE(p.stat().st_mode) for p in course.rglob('*') if p.is_file()},
              'claim':'External course alias; not fixed Assignment4 checkpoint/GPU evidence.',
              'interruption_limit':'Original course runner may lose uncaptured workspace on interruption; native preservation does not apply.'}
    validate(config); write(root/'experiment.json',config)
    verify_course(root,config)
    return {'path':str(root),'manifest_sha256':file_digest(root/'experiment.json'),'resource_profile':PROFILE}


def verify_course(root, config=None):
    config = verify(root) if config is None else config
    if not validate(config): raise ValueError('Not a course-API snapshot')
    if file_digest(root/'package.zip') != config['package_sha256']:
        raise ValueError('Course package identity changed')
    with zipfile.ZipFile(root/'package.zip') as archive:
        for member in archive.infolist():
            if not member.is_dir() and (root/'course'/member.filename).read_bytes() != archive.read(member):
                raise ValueError('Extracted package member changed: '+member.filename)
    for name,mode in config['course_file_modes'].items():
        if stat.S_IMODE((root/'course'/name).stat().st_mode) != mode:
            raise ValueError('Frozen course file mode changed: '+name)
    return config


def run(root, key_file):
    root = root.resolve(); experiment=root/'iteration'; config=verify_course(experiment)
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64','AMD64'):
        raise RuntimeError('Course CPU profile requires an actual Linux x86-64 host')
    key_file=runtime_key(root,key_file)
    execution=root/'course-execution'; execution.mkdir()
    course=execution/'course'; shutil.copytree(experiment/'course',course)
    cache=(root/'cache').resolve(); cache.mkdir(parents=True,exist_ok=True)
    assets=cache/'course-assets'; grader=cache/'SWE-bench_Pro-os'
    # A stable recipe-specific tag shares the same actual image between warm jobs.
    image='a4-course-api-agent:'+file_digest(course/'dispatcher/agent.Dockerfile')[:20]
    env={k:v for k,v in os.environ.items() if not k.startswith(('A3_','A4_','CS2680_','SWEBENCH_'))}
    env.update(config['environment'], A3_CACHE=str(cache), A3_ASSETS=str(assets),
               SWEBENCH_PRO_OS=str(grader), SWEBENCH_PRO_OS_COMMIT=config['grader_commit'],
               A3_HOSTLOG=str(execution/'hostlogs'), A3_AGENT_IMAGE=image,
               A3_DOCKER='docker', A3_API_UPSTREAM=config['course_api']['origin'],
               CS2680_MODEL_STANDARD=config['course_api']['model'],
               CS2680_MODEL_EXPERT=config['course_api']['model'],CS2680_MODEL_STARTER=config['course_api']['model'],
               PIP_BREAK_SYSTEM_PACKAGES='1',PYTHONDONTWRITEBYTECODE='1')
    write(execution/'environment.json',{k:v for k,v in env.items() if k.startswith(('A3_','A4_','CS2680_','SWEBENCH_','DOCKER_'))})
    cancelled=False
    def interrupt(signum,frame):
        nonlocal cancelled
        cancelled=True  # The existing executor signals this entire owned group.
    previous=signal.signal(signal.SIGTERM,interrupt)
    stages=[]
    try:
        for name,argv in [('prepare',['bash','evaluation_scripts/prepare_images.sh']),
                          ('preflight',[sys.executable,'-B','evaluation_scripts/run_all.py','--limit','0']),
                          ('task',[sys.executable,'-B','evaluation_scripts/run_all.py','--limit',str(config['task_count'])])]:
            if cancelled:raise RuntimeError('Course stage interrupted; no later stage admitted')
            if name=='preflight':
                head=subprocess.check_output(['git','-C',str(grader),'rev-parse','HEAD'],text=True).strip()
                dirty=subprocess.check_output(['git','-C',str(grader),'status','--porcelain','--untracked-files=no'],text=True).strip()
                if head!=config['grader_commit'] or dirty:raise RuntimeError('Original grader checkout identity mismatch')
                images=[image,*dict.fromkeys(t.get('sandbox_image') or t['docker_image'] for t in json.loads((course/'evaluation_scripts/agent_task_input.json').read_text()).values())]
                inspected=json.loads(subprocess.check_output(['docker','image','inspect',*images],text=True))
                identities=[{k:item.get(k) for k in ('Id','RepoDigests','Architecture','Os')} for item in inspected]
                if len(identities)!=len(images) or any(item['Architecture']!='amd64' or item['Os']!='linux' for item in identities):
                    raise RuntimeError('Course image platform/identity incomplete')
                write(execution/'runtime-identities.json',{'host':platform.uname()._asdict(),'grader_commit':head,'images':dict(zip(images,identities))})
                binding=cache/('course-agent-'+file_digest(course/'dispatcher/agent.Dockerfile')+'.json')
                if binding.exists() and json.loads(binding.read_text())!=identities[0]:
                    raise RuntimeError('Shared course agent image changed between jobs')
                if not binding.exists():write(binding,identities[0])
                env['CS2680_API_KEY']=key_file.read_text().strip()
            started=time.time()
            with (execution/(name+'.log')).open('wb') as output:
                child=subprocess.Popen(argv,cwd=course,env=env,stdout=output,stderr=subprocess.STDOUT)
                code=child.wait()
            stages.append({'stage':name,'argv':argv,'started_at_unix':started,'finished_at_unix':time.time(),'exit_code':code})
            write(execution/'stages.json',stages)
            if code or cancelled:raise RuntimeError('Original course '+name+' did not complete successfully')
        return {'stages':stages,'scope':config['claim']}
    finally:
        write(execution/'completion.json',{'stages':stages,'cancelled':cancelled,'at_unix':time.time(),
              'interruption_limit':config['interruption_limit'],'note':'Read original task grading; stage exit zero is not a solve.'})
        signal.signal(signal.SIGTERM,previous)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('freeze')
    for name in ('course','package','manifest','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--keys',nargs='+',required=True);p.add_argument('--model',choices=('standard','expert','starter'),required=True)
    p.add_argument('--workers',type=int,default=2);p.add_argument('--graders',type=int,default=1)
    p.add_argument('--cpu-type',choices=('m7i.2xlarge','m7i.4xlarge','m7i.8xlarge'),default='m7i.4xlarge')
    p.add_argument('--first-name',default='Development');p.add_argument('--last-name',default='Run')
    p=sub.add_parser('run');p.add_argument('--root',type=Path,required=True);p.add_argument('--key-file',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(freeze(args) if args.action=='freeze' else run(args.root,args.key_file)))
