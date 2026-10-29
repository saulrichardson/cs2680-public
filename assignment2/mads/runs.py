"""Small portable run manifests and read-only legacy-layout resolution."""
import hashlib
import json
from pathlib import Path
import uuid
from .store import atomic_json, dump, now


def resolve_run(path):
    root = Path(path).expanduser().resolve()
    if root.name == 'journal' and (root/'events.jsonl').exists():
        return root.parent
    manifest = root/'run-manifest.json'
    if manifest.is_file():
        data = json.loads(manifest.read_bytes())
        journal = (root/data['artifacts']['journal']).resolve()
        if not journal.is_relative_to(root):
            raise ValueError('Run manifest journal escapes the run root')
        return journal.parent
    for candidate in (root, root/'run', root/'output/run'):
        if (candidate/'journal/events.jsonl').exists():
            return candidate
    raise ValueError(f'No recorded run at {root}')


def create_manifest(run, task, config):
    """Snapshot source bytes at startup; no Git, secrets, or second working account."""
    run = Path(run)
    source = Path(__file__).resolve().parent.parent
    hashes = {}
    for path in sorted([*source.joinpath('mads').rglob('*.py'),
                        *source.joinpath('mads').rglob('*.md'),
                        source/'madsLoop.py',source/'requirements.txt']):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        data = path.read_bytes()
        destination = run/'source'/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data)
        hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    # JSON preserves the exact task string even if it contains a surrogate.
    atomic_json(run/'task.json', {'task':task})
    from .prompting import identities
    manifest = dict(schema_version=1,run_id=uuid.uuid4().hex,kind='agent',created_at=now(),
        route={key:config[key] for key in ('provider','model','protocol','base_url','upstream','reasoning','transport') if key in config},
        source={'files':hashes,'sha256':hashlib.sha256(dump(hashes).encode()).hexdigest(),
                'scope':'source files sampled at startup; snapshot does not certify previously loaded code'},
        loaded_prompt_resources=identities(),
        task_sha256=hashlib.sha256(dump(task).encode()).hexdigest(),
        artifacts={'journal':'journal','task':'task.json','source':'source','result':'result.json',
                   'working_account':'memory/working.md'})
    atomic_json(run/'run-manifest.json',manifest)
    return manifest
