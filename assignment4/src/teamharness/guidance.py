"""Derived navigation and selected authored passages from the frozen library."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


def absolute_navigation(content: dict[str, bytes], destination: Path) -> dict[str, bytes]:
    """Resolve publication links in a new snapshot; preserve original source hashes."""
    result = dict(content)
    prefix = 'portable-repair/'
    for name, raw in content.items():
        if not name.startswith(prefix) or not name.endswith('.md'):
            continue
        source = destination.resolve() / name
        library = destination.resolve() / 'portable-repair'
        fenced = False
        lines = []
        def link(match):
            target = match.group(2).strip('<>')
            url = urlsplit(target)
            if url.scheme or target.startswith('/'):
                return match.group(0)
            path = (source.parent / unquote(url.path)).resolve() if url.path else source
            if not path.is_relative_to(library):
                raise ValueError('Portable guidance link leaves its solver library')
            relative = str(path.relative_to(destination.resolve()))
            if relative not in content:
                raise ValueError('Portable guidance link has no frozen target: ' + target)
            return match.group(1) + str(path) + ('#' + url.fragment if url.fragment else '') + match.group(3)
        for line in raw.decode().splitlines(keepends=True):
            if line.lstrip().startswith(('```', '~~~')):
                fenced = not fenced
            if not fenced:
                line = re.sub(r'(\[[^\]]+\]\()([^)]+)(\))', link, line)
                line = re.sub(r'`(skills/[^`]+\.md)`', lambda m: '`' + str(library / m.group(1)) + '`', line)
            lines.append(line)
        result[name] = ''.join(lines).encode()
    return result


def passage(root: Path, relative: str, heading: str) -> dict | None:
    """Select existing prose as authored advice; never as a grader diagnosis."""
    path = Path(root) / 'portable-repair' / relative
    if not path.is_file():
        return None
    text = path.read_text()
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line == '## ' + heading), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
    selected = '\n'.join(lines[start:end]).strip()
    return {'author': 'portable repair guidance', 'source_path': str(path), 'section': heading,
            'source_sha256': hashlib.sha256(text.encode()).hexdigest(), 'text': selected,
            'meaning': 'Authored investigation advice, not an observation or explanation from the grader.'}
