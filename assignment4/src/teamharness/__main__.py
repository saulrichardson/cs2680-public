"""Local team workflow and explicit operator controls. No infrastructure launch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .candidates import Grader
from .main import Investigation


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='Start one continuing main investigation')
    run.add_argument('--task', type=Path, required=True)
    run.add_argument('--workspace', type=Path, required=True, help='Git repository root')
    run.add_argument('--output', type=Path, required=True, help='New directory outside the workspace')
    run.add_argument('--max-agents', type=int, help='Optional actual simultaneous-session capacity; no default quota')
    run.add_argument('--deadline-unix', type=float,
                     help='Actual shared task deadline as a Unix timestamp; inherited unchanged by all roles')
    run.add_argument('--grader-argv', help='JSON array; {workspace} and {patch} are substituted without a shell')
    run.add_argument('--grader-name', default='external-command')
    run.add_argument('--official-grader', action='store_true',
                     help='Declare that the supplied command is the original grader; not inferred from exit zero')
    run.add_argument('--completion-gate', action=argparse.BooleanOptionalAction, default=True,
                     help='Verify exact completion bytes; each recorded failing grade transfers once to a review-focused owner')
    run.add_argument('--planning-boundary', action=argparse.BooleanOptionalAction, default=True,
                     help='Require an explicit continue/skip decision before implementation completion')
    run.add_argument('--planning-turns', type=int, default=15,
                     help='Maximum exploration responses before a planning decision; compaction does not reset it')
    run.add_argument('--completion-check-argv', action='append', default=[],
                     help='Additional completion check as a JSON argv array; repeat for multiple checks')
    run.add_argument('--require-diff', action=argparse.BooleanOptionalAction, default=True,
                     help='Require solver changes relative to preserved starting source at completion')
    resume = sub.add_parser('resume', help='Continue the recorded main, workspace, jobs and grading state')
    resume.add_argument('--output', type=Path, required=True)
    resume.add_argument('--message', help='Literal user follow-up; without it continue from recorded state')
    for name in ('inspect', 'send', 'compact', 'stop', 'start', 'checkpoint', 'inspect-parallel', 'cancel-parallel'):
        command = sub.add_parser(name, help='Operate an existing agent session')
        command.add_argument('--output', type=Path, required=True, help='Existing workflow output directory')
        command.add_argument('--agent', required=True)
        if name == 'send':
            command.add_argument('--message', required=True)
        if name == 'start':
            command.add_argument('--max-agents', type=int)
        if name == 'compact':
            command.add_argument('--max-request-bytes', type=int, help='Explicit bounded model-written transcript traversal if the full no-tools request cannot fit')
            command.add_argument('--retain-inputs', action=argparse.BooleanOptionalAction, default=None,
                                 help='Keep original public task and follow-ups in the working view (default true)')
        if name == 'checkpoint':
            command.add_argument('--label', default='')
        if name.endswith('-parallel'):
            command.add_argument('--group', required=True)
    for name in ('list', 'status', 'wait'):
        command = sub.add_parser(name, help='Observe or wait on an existing team')
        command.add_argument('--output', type=Path, required=True)
        if name == 'wait':
            command.add_argument('--agents', nargs='+', required=True)
            command.add_argument('--mode', choices=('any', 'all'), default='all')
            command.add_argument('--timeout', type=float, default=30)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    from .agents import AgentManager
    from .provider import HTTPProvider

    output = args.output.resolve()
    if args.command == 'run':
        if output.exists():
            raise ValueError('Output already exists; use resume or session controls')
        workspace = args.workspace.resolve()
        if output == workspace or workspace in output.parents:
            raise ValueError('Output must be outside the task workspace')
        task = args.task.read_text()
        grader = None
        if args.grader_argv is not None:
            decoded = json.loads(args.grader_argv)
            if not isinstance(decoded, list):
                raise ValueError('--grader-argv must be a JSON array')
            grader = Grader(tuple(decoded), args.grader_name, args.official_grader)
        output.mkdir(parents=True)
        manager = AgentManager(output / 'agents', HTTPProvider, max_agents=args.max_agents,
                               deadline=args.deadline_unix)
        cancelled = False
        try:
            checks = [{'name': f'check-{i+1}', 'argv': json.loads(raw)}
                      for i, raw in enumerate(args.completion_check_argv)]
            workflow = Investigation(manager, output, completion_gate={'enabled': args.completion_gate,
                'require_diff': args.require_diff, 'checks': checks},
                planning_boundary={'enabled': args.planning_boundary, 'max_turns': args.planning_turns})
            result = workflow.run(task, workspace, grader)
        except KeyboardInterrupt:
            cancelled = True
            manager.cancel_owned()
            raise
        finally:
            manager.shutdown(wait=not cancelled)
        result = workflow.settled_report()
        print(json.dumps(result, indent=2))
        return 0 if result['state'] == 'completed' else 2
    if not output.is_dir():
        raise ValueError('Existing output directory required')
    recorded = output / 'agents'
    required_records = (recorded / 'manager.json', recorded / 'role-prompts.json',
                        recorded / 'guides' / 'manifest.json')
    if not all(path.is_file() for path in required_records) or not (recorded / 'agents').is_dir():
        raise ValueError('Existing recorded team required; observation and control commands do not initialize a campaign')
    recorded_workflow = json.loads((output / 'workflow.json').read_text()) if (output / 'workflow.json').exists() else {}
    if recorded_workflow and recorded_workflow.get('workflow') != 'main':
        raise ValueError('Recorded workflow is not a main investigation; use its frozen runtime')
    manager = AgentManager(output / 'agents', HTTPProvider,
                           max_agents=getattr(args, 'max_agents', None))
    workflow = Investigation(manager, output) if recorded_workflow else None
    cancelled = False
    try:
        if args.command == 'resume':
            if workflow is None:
                raise ValueError('CLI resume requires a recorded main investigation')
            result = workflow.resume(message=args.message)
        elif args.command == 'inspect':
            result = manager.inspect(args.agent)
        elif args.command == 'send':
            result = manager.send(args.agent, args.message)
        elif args.command == 'compact':
            if (workflow is not None and args.agent == workflow.report['agents']['main']
                    and manager.inspect(args.agent)['status'] != 'running'):
                with manager._locked():
                    manager._session(args.agent).request_compaction(max_request_bytes=args.max_request_bytes,
                                                                   retain_inputs=args.retain_inputs)
                result = workflow.resume()
            else:
                result = manager.compact(args.agent, max_request_bytes=args.max_request_bytes,
                                         retain_inputs=args.retain_inputs)
        elif args.command == 'stop':
            result = manager.stop(args.agent)
        elif args.command == 'list':
            result = manager.list_agents()
        elif args.command == 'status':
            result = manager.task_status()
        elif args.command == 'wait':
            result = manager.wait_agents(args.agents, args.mode, args.timeout)
        elif args.command == 'checkpoint':
            result = manager.checkpoint(args.agent, label=args.label)
        elif args.command in ('inspect-parallel', 'cancel-parallel'):
            from .tools import ToolRuntime
            groups = ToolRuntime(manager, args.agent, manager.descriptor(args.agent)).parallel_groups()
            result = groups.inspect(args.group) if args.command == 'inspect-parallel' else groups.cancel(args.group)
        else:
            if (workflow is not None and args.agent == workflow.report['agents']['main']
                    and manager.inspect(args.agent)['status'] != 'running'):
                result = workflow.resume()
            else:
                manager.start(args.agent)
                result = manager.wait(args.agent)
    except KeyboardInterrupt:
        cancelled = True
        manager.cancel_owned()
        raise
    finally:
        manager.shutdown(wait=not cancelled)
    if workflow is not None and args.command in ('resume', 'start', 'compact') and isinstance(result, dict) and result.get('workflow') == 'main':
        result = workflow.settled_report()
    print(json.dumps(result, indent=2))
    return 0 if args.command != 'resume' or result['state'] == 'completed' else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(2)
