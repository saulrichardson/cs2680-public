"""Shared prompt resources, included directly in actual model requests.

These are ordinary editable files, not discovered skills or a prompt service.
Like Python source, changes require a new import/runtime to become active.
"""
import hashlib
from pathlib import Path

ROOT = Path(__file__).with_name('prompts')

def read_prompt(name):
    return (ROOT/name).read_bytes().decode('utf-8')

CODING = read_prompt('coding.md')
RUNTIME = read_prompt('runtime.md')
REVIEW_RICH = read_prompt('review-rich.md')
SYSTEM = CODING + '\n' + RUNTIME


def identities():
    """Identify loaded strings, not later on-disk edits."""
    return {name: dict(bytes=len(text.encode()), sha256=hashlib.sha256(text.encode()).hexdigest())
            for name, text in [('coding.md', CODING), ('runtime.md', RUNTIME),
                               ('review-rich.md', REVIEW_RICH)]}
