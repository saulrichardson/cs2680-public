"""Named, source-identified engine assets for the maintained Python builder.

The containing builder selects a profile explicitly. An omitted profile keeps
its runtime-only behavior; this module never selects or launches an engine.
"""
import hashlib
import json
import os
from pathlib import Path
import stat


PROFILES = ('stock-c5', 'stock-c5-headroom')
_ROOT = Path(__file__).resolve().parent
_FILES = {'engine.json', 'flags.txt', 'launch.sh'}


def install_engine(output, profile):
    """Copy the named profile to output/engine, returning its provenance.

    Validate the complete source before creating anything. Never overwrite an
    existing engine or install packages; the course/matched lab supplies a4-serve.
    """
    if profile not in PROFILES:
        raise ValueError('Unknown engine profile: ' + str(profile))
    metadata = json.loads((_ROOT / (profile + '.json')).read_text())
    source = _ROOT / profile
    if (metadata['profile'] != profile or set(metadata['files']) != _FILES
            or {p.name for p in source.iterdir()} != _FILES):
        raise ValueError('Engine profile must identify exactly its three source files')
    contents = {}
    for name, expected in metadata['files'].items():
        path = source / name
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError('Engine assets must be regular files: ' + name)
        data = path.read_bytes()
        if (len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']
                or stat.S_IMODE(mode) != expected['mode']):
            raise ValueError('Engine asset differs from its recorded source: ' + name)
        contents[name] = data
    if json.loads(contents['engine.json']) != metadata['engine']:
        raise ValueError('Engine/checkpoint identity differs from its profile')
    output = Path(output)
    if output.is_symlink():
        raise ValueError('Engine output must use a real package directory')
    destination = output / 'engine'
    if os.path.lexists(destination):
        raise FileExistsError('Refusing to replace existing engine: ' + str(destination))
    output.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    for name, data in contents.items():
        with (destination / name).open('xb') as stream:
            stream.write(data)
        (destination / name).chmod(metadata['files'][name]['mode'])
    return metadata
