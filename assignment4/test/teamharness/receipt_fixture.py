"""Inspect original evidence named by a readable tool result, never parse its prose."""
import json
from src.store import Store


def original_receipt(content):
    lines = [line for line in content.splitlines() if line.startswith('Original JSON: ')]
    if lines:
        # Readable views end with their evidence references; the last one is this result's.
        path, ref = lines[-1].removeprefix('Original JSON: ').rsplit(' ref=', 1)
        store = Store(path, readonly=True)
        try:
            return store.get(ref, scope='session')['body']['receipt']
        finally:
            store.db.close()
    return json.loads(content)


def session_facts(request):
    """Labelled session facts a scripted provider sees, as nested values."""
    text = request['messages'][0]['content'].rsplit('Session facts (runtime):\n', 1)[-1]
    def value(raw):
        return {'true': True, 'false': False}.get(raw, int(raw) if raw.isdigit() else raw)
    root, stack = {}, [(-1, None)]
    stack[0] = (-1, root)
    for line in text.splitlines():
        depth = len(line) - len(line.lstrip(' '))
        key, separator, raw = line.strip().partition(':')
        if not separator:
            continue
        while stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1]
        name = key.replace(' ', '_')
        if raw.strip():
            parent[name] = value(raw.strip())
        else:
            parent[name] = {}
            stack.append((depth, parent[name]))
    if str(root.get('parent', '')).startswith('none ('):
        root['parent'] = None
    return root
