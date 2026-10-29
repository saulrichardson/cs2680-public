"""Verified starting-source archives; restoration never overwrites a workspace."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import tarfile


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def freeze(source, destination):
    destination = Path(destination)
    with destination.open('xb') as stream:
        with tarfile.open(fileobj=stream, mode='w', dereference=False) as archive:
            archive.add(source, arcname='baseline')
    return {'path': str(destination), 'sha256': digest(destination)}


def restore(record, destination):
    """Read the checked bytes once; preserve absolute symlinks without traversing them."""
    destination = Path(destination)
    with Path(record['path']).open('rb') as stream:
        checksum = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
        if checksum.hexdigest() != record['sha256']:
            raise ValueError('Starting-source archive hash differs; cannot restore')
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode='r:') as archive:
            members = archive.getmembers()
            by_name = {}
            for member in members:
                name = PurePosixPath(member.name)
                if (name.is_absolute() or '..' in name.parts or not name.parts
                        or name.parts[0] != 'baseline' or str(name) in by_name
                        or not (member.isdir() or member.isfile() or member.issym() or member.islnk())):
                    raise ValueError('Unsafe starting-source archive member')
                by_name[str(name)] = member
            if 'baseline' not in by_name or not by_name['baseline'].isdir():
                raise ValueError('Starting-source archive needs a directory root')
            for name, member in by_name.items():
                for parent in PurePosixPath(name).parents:
                    if str(parent) in by_name and not by_name[str(parent)].isdir():
                        raise ValueError('Archive member traverses a non-directory')
                if member.islnk():
                    target = by_name.get(member.linkname)
                    if target is None or not target.isfile():
                        raise ValueError('Archive hardlink must name an archived regular file')
            destination.mkdir(parents=True, exist_ok=False)
            for member in members:
                path = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('xb') as output, archive.extractfile(member) as source:
                        import shutil
                        shutil.copyfileobj(source, output)
                    path.chmod(member.mode & 0o777)
                elif member.issym():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.symlink_to(member.linkname)
            for member in members:
                if member.islnk():
                    path = destination.joinpath(*PurePosixPath(member.name).parts)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.link(destination / member.linkname, path)
            for member in reversed(members):
                if member.isdir():
                    destination.joinpath(*PurePosixPath(member.name).parts).chmod(member.mode & 0o777)
    return destination / 'baseline'
