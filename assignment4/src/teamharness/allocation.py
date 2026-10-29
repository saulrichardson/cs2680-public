"""Deterministic allocation of model-assessed tasks; no model or course effects.

Unknown assessments stay unknown. The count is a compute-allocation target, not
a claim that that many tasks are feasible. Fingerprints break ties only; no
task-specific knowledge or prior evaluation results enter selection.
"""
from __future__ import annotations

import hashlib
import json

from .planning import READINESS, EFFORT, assessment


def fingerprint(task):
    public = {key: task[key] for key in ('problem_statement', 'requirements', 'interface') if key in task}
    return hashlib.sha256(json.dumps(public, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def select(records, count):
    """Return a stable partition; negative recommendations cannot shrink the target."""
    if type(count) is not int or count < 0:
        raise ValueError('Task allocation count must be a nonnegative integer')
    records = list(records)
    identities = [row['task_id'] for row in records]
    if len(set(identities)) != len(identities):
        raise ValueError('Each task must have exactly one planning result')

    def key(row):
        value = row.get('assessment')
        if value is not None:
            value = assessment(value)
        return (READINESS.index(value['readiness']) if value else len(READINESS),
                EFFORT.index(value['effort']) if value else len(EFFORT),
                0 if row.get('recommendation') == 'continue' else 1,
                row['fingerprint'], str(row['task_id']))

    ranked = sorted((row for row in records if row['eligible']), key=key)
    selected = ranked[:count]
    chosen = {row['task_id'] for row in selected}
    reserve = sorted((row for row in records if row['task_id'] not in chosen), key=key)
    return {'selected': selected, 'reserve': reserve,
            'requested': count, 'eligible': len(ranked),
            'meaning': 'Readiness, effort, recommendation, then public-content fingerprint; assessments remain model judgments.'}
