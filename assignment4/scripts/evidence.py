#!/usr/bin/env python3
"""Read-only database exploration and exact Haskell request reconstruction."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.store import Store


def reconstruct(store, binary, ref):
    record = store.get(ref,kind='request')
    links = {x['relation']:x['ref'] for x in record['links']}
    config = dict(mode='render',view_ref=links['view'],tools_ref=links['tools'])
    result = read_only_projection(store, binary, config)
    if result != record['body']:
        raise ValueError('Reconstructed request differs from recorded request')
    return result


def read_only_projection(store, binary, config):
    """Fail closed: the child may read stored objects or return a value, nothing else."""
    with subprocess.Popen([str(binary)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,text=True) as process:
        process.stdin.write(json.dumps(config)+'\n')
        process.stdin.flush()
        for line in process.stdout:
            call = json.loads(line)
            if call['op'] == 'complete':
                process.stdin.close()
                if process.wait():
                    raise RuntimeError(process.stderr.read())
                return call['payload']
            # This executable mode must never request effects or modify the database.
            if call['op'] != 'store_get':
                process.kill()
                raise RuntimeError('Unexpected effect during read-only rendering: '+call['op'])
            result = store.get(**call['payload'])
            process.stdin.write(json.dumps({'op':call['op'],'result':result})+'\n')
            process.stdin.flush()
        raise RuntimeError(process.stderr.read())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    sub = parser.add_subparsers(dest='command',required=True)
    query = sub.add_parser('query')
    query.add_argument('--kind')
    query.add_argument('--text',default='')
    query.add_argument('--scope')
    query.add_argument('--after',type=int,default=0)
    query.add_argument('--limit',type=int,default=50)
    get = sub.add_parser('get'); get.add_argument('ref'); get.add_argument('--scope')
    blob = sub.add_parser('blob'); blob.add_argument('ref'); blob.add_argument('--offset',type=int,default=0)
    blob.add_argument('--bytes',type=int,default=65536); blob.add_argument('--raw',action='store_true')
    render = sub.add_parser('render'); render.add_argument('ref'); render.add_argument('--binary',type=Path,required=True)
    sub.add_parser('audit')
    args = parser.parse_args()
    with Store(args.database,readonly=True) as store:
        if args.command == 'query':
            result = store.query(args.scope,args.kind,args.text,args.after,args.limit)
        elif args.command == 'get':
            result = store.get(args.ref,scope=args.scope)
        elif args.command == 'blob':
            result = store.blob(args.ref,args.offset,args.bytes)
            if args.raw:
                sys.stdout.buffer.write(base64.b64decode(result['base64']))
                return
        elif args.command == 'render':
            result = reconstruct(store,args.binary,args.ref)
        else:
            problems = store.db.execute('PRAGMA foreign_key_check').fetchall()
            if problems:
                raise ValueError('Dangling database references')
            counts = {}
            for row in store.db.execute('SELECT id,kind FROM objects ORDER BY id'):
                store.get('obj:'+str(row['id']))
                counts[row['kind']] = counts.get(row['kind'],0)+1
            for row in store.db.execute('SELECT object_id FROM blobs'):
                store.blob('obj:'+str(row['object_id']),0,1)
            result = dict(integrity='checked',objects_by_kind=counts)
        print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
