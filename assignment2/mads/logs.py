"""Read-only journal viewer. --full removes display shortening; --raw returns exact bytes."""
import argparse
import json
import sys
import time
from pathlib import Path
from .store import record_links, dump
from .runs import resolve_run


def records(root, follow=False):
    position=0
    while True:
        path=Path(root)/'events.jsonl'
        if path.exists():
            with path.open('rb') as source:
                source.seek(position)
                for line in source:
                    if not line.endswith(b'\n'):
                        break
                    position+=len(line)
                    yield json.loads(line)
        if not follow:
            return
        time.sleep(.2)


def request_chain(history, request_id):
    """Follow one API decision and its effects, stopping before the next API call."""
    by_id = {record['id']:record for record in history}
    request = by_id.get(request_id)
    if request is None:
        return set()
    if request['event'] != 'request':
        raise ValueError('--request requires the record ID of a compiled API request')
    end = next((record['id'] for record in history
                if record['id'] > request_id and record['event'] == 'request'), len(history)+1)
    selected = {request_id}
    for record in history:
        if request_id < record['id'] < end and record_links(record) & selected:
            selected.add(record['id'])
    # An outcome refers back to evidence captured before that outcome was recorded.
    selected.update(link for record_id in list(selected) for link in record_links(by_id[record_id])
                    if link in by_id)
    return selected


def selected_records(root, follow=False, request_id=None):
    if request_id is not None and not follow:
        history = list(records(root))
        selected = request_chain(history,request_id)
        yield from (record for record in history if record['id'] in selected)
        return
    history, shown = [], set()
    for record in records(root,follow):
        if request_id is None:
            yield record
            continue
        history.append(record)
        if record['id'] < request_id:
            continue
        if record['id'] > request_id and record['event'] == 'request':
            return
        selected = request_chain(history,request_id)
        for candidate in history:
            if candidate['id'] in selected and candidate['id'] not in shown:
                shown.add(candidate['id'])
                yield candidate


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('journal',type=Path)
    parser.add_argument('--full',action='store_true')
    parser.add_argument('--follow',action='store_true')
    parser.add_argument('--view',choices=('flow','api'),default='flow')
    selector=parser.add_mutually_exclusive_group()
    selector.add_argument('--event',type=int)
    selector.add_argument('--request',type=int,help='compiled request ID: include manifest, transport, native calls, outcomes and their evidence')
    parser.add_argument('--raw',action='store_true')
    args=parser.parse_args(argv)
    try:
        args.journal = resolve_run(args.journal)/'journal'
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not args.follow and (args.event is not None or args.request is not None):
        selected = args.event if args.event is not None else args.request
        if selected not in {row['id'] for row in records(args.journal)}:
            parser.error(f'Unknown journal record: {selected}')
    if args.raw and args.event is None:
        parser.error('--raw requires --event')
    try:
        for record in selected_records(args.journal,args.follow,args.request):
            if args.event is not None and record['id']!=args.event:
                continue
            if args.view=='api' and record['event'] not in ('request','http_request','response','api_error'):
                continue
            body=(args.journal/'blobs'/record['blob']).read_bytes() if 'blob' in record else None
            if args.raw:
                sys.stdout.buffer.write(body if body is not None else dump(record).encode())
                return
            print(f"#{record['id']} {record['timestamp']} {record['event']}")
            if body is not None:
                print(f"Exact {record['event']} payload: {args.journal/'blobs'/record['blob']} ({len(body)} bytes)")
            values=[json.dumps(record,ensure_ascii=True,indent=2)]
            if body is not None:
                values.append(body.decode('utf-8',errors='backslashreplace'))
            for value in values:
                print(value if args.full or args.event else value[:1200]+(
                    f'\n[viewer excerpt; python -m mads.logs {str(args.journal)!r} --event {record["id"]} expands; add --raw for exact payload bytes]'
                    if len(value)>1200 else ''))
            print(flush=True)
            if args.event is not None:
                return
    except (KeyboardInterrupt,BrokenPipeError):
        return
    except (ValueError,OSError) as exc:
        parser.error(str(exc))


if __name__=='__main__':
    main()
