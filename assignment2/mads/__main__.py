"""Launch, inspect, and verify the shared harness. No provider calls from inspection."""
import argparse
import json
from pathlib import Path
import sys
from .runs import resolve_run


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    for name,help_text in (
        ('run','Run a task through the shared agent'),('assignment','Container assignment launch/export'),
        ('logs','Follow events or read exact payload bytes'),('inspect','Timeline, accounts, or API request inspection'),
        ('handoff','Export or validate an explicit fresh-session evidence bundle'),
        ('check','Run explicit offline checks'),('audit','Independently audit recorded evidence'),
        ('status','Show observed run outcomes and last activity'),('runs','List recorded runs under a root')):
        commands.add_parser(name,help=help_text,add_help=False)
    if not argv or argv[0] in ('-h','--help'):
        parser.parse_args(argv)
    parser.parse_args(argv[:1])
    command,args=argv[0],argv[1:]
    if command=='run':
        from .agent import main as launch
        return launch(args)
    if command=='assignment':
        from .runner import main as launch
        return launch(args)
    if command=='logs':
        from .logs import main as show
        return show(args)
    if command=='inspect':
        from mads.inspection.inspect_run import main as inspect
        return inspect(args)
    if command=='handoff':
        from .handoff import main as handoff
        return handoff(args)
    if command=='check':
        from experiments.run_checks import main as checks
        if '--profile' in args:
            at=args.index('--profile')
            if args[at+1:at+2] != ['offline']:
                parser.error('Only the offline profile is supported; live studies are explicit separate commands')
            del args[at:at+2]
        return checks(args)
    if command=='audit':
        from mads.inspection.audit_journal import audit
        p=argparse.ArgumentParser(description='Independent journal audit')
        p.add_argument('run'); p.add_argument('--strict',action='store_true'); p.add_argument('--output',type=Path)
        options=p.parse_args(args)
        try:
            result=audit(resolve_run(options.run)/'journal',options.strict)
            text=json.dumps(result,indent=2)+'\n'
            if options.output: options.output.write_text(text)
            print(text,end='')
            return 0 if result['passed'] else 1
        except (OSError,ValueError) as exc: p.error(str(exc))
    if command=='status':
        from mads.inspection.inspect_run import Journal
        p=argparse.ArgumentParser(description='Recorded state, not proof of current process liveness')
        p.add_argument('run'); p.add_argument('--json',action='store_true')
        options=p.parse_args(args)
        try: result=Journal(options.run).summary()
        except (OSError,ValueError) as exc: p.error(str(exc))
        if options.json: print(json.dumps(result,indent=2))
        else:
            print(f"Run: {result['run_directory']}\nJournal head: {result['journal_head']}\nLast recorded activity: {result['last_recorded_at']}")
            print('Outcomes: '+json.dumps(result['result']))
            print('Host outcome: '+json.dumps(result['host_result']))
            print('Liveness: unknown from saved evidence; no automatic resume')
        return 0
    p=argparse.ArgumentParser(description='List run manifests and legacy journals without launching anything')
    p.add_argument('action',choices=('list',)); p.add_argument('--root',type=Path,required=True)
    options=p.parse_args(args)
    if not options.root.is_dir(): p.error('Run root must be an existing directory')
    rows=[]
    for journal in sorted(options.root.rglob('journal/events.jsonl')):
        run=journal.parent.parent
        rows.append({'run':str(run.resolve()),'manifest':(run/'run-manifest.json').is_file(),
                     'has_result':(run/'result.json').is_file()})
    print(json.dumps(rows,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
