"""Select explicitly packaged authored guidance by exact public problem bytes."""
import hashlib
import json
from pathlib import Path


def problem_digest(task):
    text = task.get('problem_statement')
    if not isinstance(text, str) or not text:
        raise ValueError('Task needs a nonempty public problem statement')
    return hashlib.sha256(text.encode()).hexdigest()


def validate_manifest(value):
    if value.get('version') != 1 or not isinstance(value.get('entries'), list) or not value['entries']:
        raise ValueError('Invalid task guidance manifest')
    seen = set()
    for row in value['entries']:
        digest, text = row['problem_sha256'], row['text']
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in '0123456789abcdef' for c in digest) or digest in seen
                or not isinstance(text, str) or not text.strip()
                or hashlib.sha256(text.encode()).hexdigest() != row['text_sha256']):
            raise ValueError('Invalid, duplicate, or altered task guidance')
        seen.add(digest)


def select_guidance(path: Path, task):
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    validate_manifest(value)
    matches = [row for row in value['entries'] if row['problem_sha256'] == problem_digest(task)]
    if len(matches) != 1:
        raise ValueError('Packaged guidance does not match the public task; refusing wrong prompt')
    return matches[0]['text']
