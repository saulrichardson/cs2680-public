"""Freeze the team runtime, validation dependency and course adapter."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def freeze_validation(output):
    """Bundle pinned pure Python validation; task images need no pip or ABI wheel."""
    import fastjsonschema
    from importlib.metadata import distribution
    from src.teamharness.contracts import VERSION
    if fastjsonschema.VERSION != VERSION:
        raise ValueError('Install the pinned src/teamharness/requirements.txt before building')
    target = output / 'fastjsonschema'
    shutil.copytree(Path(fastjsonschema.__file__).parent, target,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    dist = distribution('fastjsonschema')
    license_file = next(p for p in dist.files if p.name == 'LICENSE')
    shutil.copy2(dist.locate_file(license_file), target / 'LICENSE')
    return {'library': 'fastjsonschema', 'version': VERSION,
            'delivery': 'pinned Python source in runtime archive; no task-side installation'}


def freeze_repair_guidance(solver, destination):
    """Copy only the publication's hash-identified solver tree, never its evidence."""
    solver = Path(solver).resolve()
    manifest = json.loads((solver.parent / 'manifest.json').read_text())
    if manifest.get('solver_root') != solver.name:
        raise ValueError('Repair guidance must name the publication solver directory')
    expected = manifest['solver_files']
    actual = {}
    for path in sorted(solver.rglob('*')):
        if path.is_symlink():
            raise ValueError('Repair guidance must contain regular files, not symlinks')
        if path.is_file():
            actual[str(path.relative_to(solver.parent))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected or not {'solver/SYSTEM_PROMPT.md', 'solver/SKILLS.md'}.issubset(actual):
        raise ValueError('Repair guidance differs from its publication manifest')
    shutil.copytree(solver, destination)
    return {'publication': manifest['publication'], 'version': manifest['version'],
            'mode': 'catalog-and-selective-passages', 'solver_files': actual}


def build(output, *, solve_seconds=3600, max_agents=None, grade_feedback=True, task_guidance=None,
          repair_guidance='default', completion_gate=True, completion_checks=None, require_diff=True,
          planning_boundary=True, planning_turns=15, select_tasks=None, skip_tasks=None,
          planning_workers=3, model_slots=None, engine_profile=None, workers=1):
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from lab.adapters.teamharness.engines import PROFILES, install_engine
    if engine_profile is not None and engine_profile not in PROFILES:
        raise ValueError('Unknown self-hosted engine profile')
    if model_slots is not None and (type(model_slots) is not int or model_slots < 1):
        raise ValueError('Shared model slots must be a positive integer')
    if type(workers) is not int or workers < 1:
        raise ValueError('Task workers must be a positive integer')
    if select_tasks is not None and skip_tasks is not None:
        raise ValueError('Choose either a number to select or a number to skip')
    allocation = select_tasks is not None or skip_tasks is not None
    target = select_tasks if select_tasks is not None else skip_tasks
    if allocation and (type(target) is not int or target < 0 or not planning_boundary
            or type(planning_workers) is not int or planning_workers < 1):
        raise ValueError('Task allocation requires enabled planning, a nonnegative target and positive worker counts')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    if str(repair_guidance) == 'default':
        repair_guidance = root / 'research/repair-guidance/solver'
    src = output / 'src'
    src.mkdir()
    for name in ('api.py', 'bridge.py', 'workspace.py', 'store.py', 'transport.py', 'sandbox_server.py', '__init__.py'):
        shutil.copy2(root / 'src' / name, src / name)
    for name in ('agentic_loop.py', 'backend.py', 'course_feedback.py', 'request_scheduler.py'):
        shutil.copy2(here / name, src / name)
    validation_receipt = freeze_validation(output)
    shutil.copytree(root / 'src/teamharness', src / 'teamharness', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(here / 'course_worker.py', src / 'teamharness/course_worker.py')
    guidance_receipt = None
    if repair_guidance is not None:
        guidance_receipt = freeze_repair_guidance(repair_guidance, src / 'teamharness/guides/portable-repair')
    if task_guidance is not None:
        from src.teamharness.task_guidance import validate_manifest
        guidance = json.loads(Path(task_guidance).read_text())
        validate_manifest(guidance)
        (src / 'teamharness/task-guidance.json').write_text(json.dumps(guidance, indent=2))
    campaign = {'workflow': 'main', 'workers': workers, 'solve_seconds': solve_seconds,
                'max_agents': max_agents, 'grade_feedback': grade_feedback}
    if model_slots is not None:
        campaign['model_slots'] = model_slots
    from src.teamharness.completion import policy
    campaign['completion_gate'] = policy({'enabled': completion_gate, 'require_diff': require_diff,
                                         'checks': completion_checks or []})
    from src.teamharness.planning import policy as planning_policy
    campaign['planning_boundary'] = planning_policy({'enabled': planning_boundary, 'max_turns': planning_turns,
                                                    'pause_after_planning': allocation})
    if allocation:
        campaign['allocation'] = {('skip_count' if skip_tasks is not None else 'count'): target,
                                  'planning_workers': planning_workers}
    (src / 'campaign.json').write_text(json.dumps(campaign, indent=2))
    with tarfile.open(src / 'team-runtime.tar.gz', 'w:gz') as tar:
        for path in sorted([*src.rglob('*'), *(output / 'fastjsonschema').rglob('*')]):
            if path.is_file() and path.name != 'team-runtime.tar.gz':
                tar.add(path, arcname=str(path.relative_to(output)), recursive=False)
    shutil.copy2(here / 'agent.Dockerfile', output / 'agent.Dockerfile')
    (output / 'requirements.lock').write_text('# Task runtime includes pinned fastjsonschema source in team-runtime.tar.gz.\n')
    engine_receipt = install_engine(output, engine_profile) if engine_profile is not None else None
    receipt = {'revision': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        'campaign': campaign, 'repair_guidance': guidance_receipt, 'validation': validation_receipt,
        'engine': engine_receipt,
        'files': {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sorted(output.rglob('*')) if p.is_file()}}
    (output / 'build.json').write_text(json.dumps(receipt, indent=2))
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--solve-seconds', type=int, default=3600)
    parser.add_argument('--max-agents', type=int, help='Optional actual session capacity; no default quota')
    parser.add_argument('--grade-feedback', action=argparse.BooleanOptionalAction, default=True,
                        help='Expose counts-only course evaluation (enabled by default)')
    parser.add_argument('--task-guidance', type=Path, help='Explicit task-matched authored prompt supplements')
    parser.add_argument('--repair-guidance', type=Path, default='default',
                        help='Publication solver/ directory; defaults to maintained library, exposed for selective retrieval')
    parser.add_argument('--no-repair-guidance', dest='repair_guidance', action='store_const', const=None,
                        help='Explicit control package without the portable library')
    parser.add_argument('--completion-gate', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--planning-boundary', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--planning-turns', type=int, default=15)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--select-tasks', type=int, help='Opt in: plan all tasks, then allocate implementation to this many')
    selection.add_argument('--skip-tasks', type=int, help='Opt in: plan all tasks, skip this many, and allocate the rest')
    parser.add_argument('--planning-workers', type=int, default=3)
    parser.add_argument('--workers', type=int, default=1, help='Concurrent ordinary task owners; allocation uses its own planning/selection path')
    parser.add_argument('--model-slots', type=int, help='Optional shared in-flight model capacity across tasks and helpers')
    parser.add_argument('--engine-profile', choices=['stock-c5', 'stock-c5-headroom'], help='Include the pinned self-hosted engine in this package')
    parser.add_argument('--require-diff', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--completion-check-argv', action='append', default=[], help='Additional named check as a JSON argv array')
    args = parser.parse_args()
    build(args.output, solve_seconds=args.solve_seconds, max_agents=args.max_agents,
          grade_feedback=args.grade_feedback, task_guidance=args.task_guidance,
          repair_guidance=args.repair_guidance, completion_gate=args.completion_gate,
          planning_boundary=args.planning_boundary, planning_turns=args.planning_turns,
          select_tasks=args.select_tasks, skip_tasks=args.skip_tasks,
          planning_workers=args.planning_workers, workers=args.workers,
          model_slots=args.model_slots, engine_profile=args.engine_profile,
          require_diff=args.require_diff,
          completion_checks=[{'name': f'check-{i+1}', 'argv': json.loads(raw)}
                             for i, raw in enumerate(args.completion_check_argv)])
