"""Copy a stopped workspace for evaluation; identify bytes, never claim replay.

Use the controller to confirm the producer has stopped first. This utility cannot
freeze a running filesystem. Keep the source archive; checks may modify the copy.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


def inventory(root):
    root = Path(root).resolve()
    entries = []
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        item = {'path': str(path.relative_to(root)), 'mode': stat.S_IMODE(mode)}
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            if Path(target).is_absolute() or not path.resolve().is_relative_to(root):
                raise ValueError(f'Escaping symlink: {item["path"]}')
            item.update(kind='symlink', target=target)
        elif stat.S_ISDIR(mode):
            item.update(kind='directory')
        elif stat.S_ISREG(mode):
            item.update(kind='file', sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            raise ValueError(f'Unsupported file kind: {item["path"]}')
        entries.append(item)
    encoded = json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()
    return {'tree_sha256': hashlib.sha256(encoded).hexdigest(), 'entries': entries}


def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if not source.is_dir():
        raise ValueError('Source must be a directory')
    if destination.exists() or destination.is_symlink():
        raise ValueError('Destination already exists; refusing overwrite')
    if destination.resolve().is_relative_to(source):
        raise ValueError('Destination must not be inside source')
    before = inventory(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.snapshot-', dir=destination.parent))
    try:
        shutil.copytree(source, temporary/'tree', symlinks=True)
        after, copied = inventory(source), inventory(temporary/'tree')
        if before != after or before != copied:
            raise ValueError('Source/copy changed during capture; no destination published')
        os.rename(temporary/'tree', destination)
    finally:
        shutil.rmtree(temporary)
    return {'source':str(source), 'destination':str(destination), **copied,
            'scope':'Matched before/after file inventories and copy; not an atomic snapshot or continuation replay. Processes, account, policies and native history are not reconstructed.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.source, args.destination), indent=2))


if __name__ == '__main__':
    main()
