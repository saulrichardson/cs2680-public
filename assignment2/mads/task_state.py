"""Lossless task source and an optional, attributable investigation board.

Source units are paragraph-sized navigation spans, NOT generated requirements.
Notes are model claims. Neither source coverage nor supported notes prove a fix.
"""
import copy
import hashlib
import re
from pathlib import Path
from .store import atomic_json


class TaskState:
    MODES = ('full', 'focus', 'off')
    STATES = ('open', 'supported', 'contradicted', 'ambiguous', 'out_of_scope')

    def __init__(self, text, root, mode='full'):
        if mode not in self.MODES:
            raise ValueError('task_context must be full, focus or off')
        self.text = text
        self.sha256 = hashlib.sha256(text.encode('utf-8')).hexdigest()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.mode, self.focus_ids, self.notes = mode, [], {}
        self.units = []
        offset = 0
        for match in re.finditer(r'.+?(?:\n[ \t]*\n|\Z)', text, re.S):
            content = match.group()
            end = offset + len(content.encode('utf-8'))
            self.units.append(dict(id=f'T{len(self.units)+1}', bytes=[offset, end], text=content))
            offset = end
        assert ''.join(unit['text'] for unit in self.units) == text

    def _ids(self, ids):
        by_id = {unit['id']: unit for unit in self.units}
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in by_id for i in ids):
            raise ValueError('source_ids must contain existing task source IDs; use task(show)')
        if len(set(ids)) != len(ids):
            raise ValueError('source_ids must not contain duplicates')
        return [copy.deepcopy(by_id[i]) for i in ids]

    def _persist(self):
        atomic_json(self.root/'state.json', dict(source_sha256=self.sha256, mode=self.mode,
                    focus_ids=self.focus_ids, notes=self.notes))

    def focus(self, mode, source_ids=None):
        if mode not in self.MODES:
            raise ValueError('mode must be full, focus or off')
        ids = [] if source_ids is None else source_ids
        self._ids(ids)
        if mode != 'focus' and ids:
            raise ValueError('source_ids apply only to mode=focus')
        self.mode, self.focus_ids = mode, list(ids)
        self._persist()
        return self.packet()

    def note(self, key, source_ids, claim, state='open', evidence=''):
        if not key or not claim or state not in self.STATES:
            raise ValueError('Provide a note key, claim and valid state')
        self._ids(source_ids)
        self.notes[key] = dict(source_ids=list(source_ids), claim=claim, state=state,
                               evidence=evidence, authorship='agent', validation='unverified')
        self._persist()
        return self.packet()

    def remove(self, key):
        if key not in self.notes:
            raise ValueError(f'Unknown task note {key!r}')
        del self.notes[key]
        self._persist()
        return self.packet()

    def packet(self, source_ids=None):
        if source_ids is not None:
            units = self._ids(source_ids)
        elif self.mode == 'full':
            units = copy.deepcopy(self.units)
        else:
            units = self._ids(self.focus_ids) if self.mode == 'focus' else []
        return dict(source_sha256=self.sha256, mode=self.mode, source_units=units,
                    total_source_units=len(self.units), omitted_source_ids=[u['id'] for u in self.units
                        if u['id'] not in {v['id'] for v in units}], notes=copy.deepcopy(self.notes),
                    meaning='Exact original task spans, not a generated checklist. The original task remains '
                            'in the request regardless of this focus. All problem, requirements and interface '
                            'text has equal authority; a named interface does not narrow requested behavior. '
                            'Notes are fallible agent judgments, not certified coverage or completion.')
