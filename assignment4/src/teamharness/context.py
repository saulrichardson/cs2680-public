"""Append-only working views over attributed notes and dated execution observations.

Original snapshots stay archived. Equality below suppresses clock-only repetition,
not observations of changed execution. Unknown producer fields remain significant.
"""
from __future__ import annotations

import copy
from pathlib import Path

from src.store import Store
from .presentation import facts, runtime_observation

APPEND_ONLY = 'append_only_v1'
LEGACY = 'legacy'


def policy(state):
    return state['config'].get('context_policy', LEGACY)


def prefix_structure(previous, current):
    """Logical request reuse, not a claim about provider tokenization/cache hits."""
    if previous is None:
        return {'previous_request': False}
    leading = 0
    for old, new in zip(previous['messages'], current['messages']):
        if old != new:
            break
        leading += 1
    return {'previous_request': True, 'identical_leading_messages': leading,
            'previous_message_count': len(previous['messages']),
            'previous_messages_are_prefix': leading == len(previous['messages']),
            'tools_unchanged': previous.get('tools') == current.get('tools'),
            'model_unchanged': previous.get('model') == current.get('model'),
            'meaning': 'Logical message/schema comparison; actual cached tokens require provider usage receipts.'}


def runtime_identity(snapshot):
    value = copy.deepcopy(snapshot)
    value.pop('observed_at', None)
    value.pop('task_elapsed_seconds', None)
    deadline = value.get('task_deadline')
    if isinstance(deadline, dict):
        deadline.pop('observed_at', None)
        deadline.pop('remaining_seconds', None)
    run = value.get('run')
    if isinstance(run, dict):
        for key in ('observed_at', 'elapsed_seconds', 'remaining_seconds', 'task_elapsed_seconds', 'controller_elapsed_seconds'):
            run.pop(key, None)
    # Production job transitions have their own dated delivery below. Keep the
    # historical inventory comparison for executors that do not supply them.
    if 'job_observations' in value:
        value.pop('job_observations')
        value.pop('existing_jobs', None)
    for group in value.get('existing_jobs') or []:
        for job in group.get('jobs', []):
            job.pop('elapsed_seconds', None)
    return value


def append_observation(state, text):
    """Runtime-authored context at a complete exchange boundary.

    A tool result may carry a separately attributed runtime observation. This
    keeps ordinary tool loops in the same user turn for templates that discard
    earlier reasoning after a new user message. No synthetic tool call is made.
    """
    text = 'Runtime observation (supplied by the harness, separate from tool output):\n' + text
    if state['messages'] and state['messages'][-1]['role'] == 'tool':
        state['messages'][-1]['content'] += '\n\n' + text
    else:
        state['messages'].append({'role': 'user', 'content': text})


def append_updates(state, database: Path, record):
    """Called at complete request boundaries, never by read-only reconstruction."""
    if policy(state) != APPEND_ONLY:
        return
    delivered = state.setdefault('context_delivery', {})
    guidance = state['config'].get('guidance', [])
    if delivered.get('guidance') != guidance:
        if guidance or 'guidance' in delivered:
            state['messages'].append({'role': 'user', 'content':
                'Updated attributed guidance; authored interpretation, not human requirements or runtime verdicts. '
                'This version supersedes earlier configured guidance.\n' +
                facts(guidance)})
        delivered['guidance'] = copy.deepcopy(guidance)
    brief_ref = state.get('working_brief_ref')
    if brief_ref and delivered.get('brief_ref') != brief_ref:
        store = Store(database, readonly=True)
        try:
            brief = store.get(brief_ref, scope='session')['body']
        finally:
            store.db.close()
        state['messages'].append({'role': 'user', 'content':
            'Working task brief; model interpretation, not replacement human requirements. '
            'This note supersedes the previous working brief.\n' +
            facts({'reference': brief_ref, **brief})})
        delivered['brief_ref'] = brief_ref
    snapshot = state.get('runtime_facts')
    if snapshot:
        if 'job_observations' in snapshot:
            previous = delivered.setdefault('jobs', {})
            changed = []
            for job in snapshot['job_observations']:
                identity = {k: v for k, v in job.items() if k not in ('observed_at', 'elapsed_seconds')}
                if previous.get(job['id']) != identity:
                    changed.append(job)
                    previous[job['id']] = identity
            if changed:
                ref = record('job_observation', {'epoch': state['epoch'], 'jobs': changed})
                from .presentation import job_line
                summary = '\n'.join(job_line(job) + f" Command: {job.get('command_preview')}. Output: {job.get('output_path', 'not recorded')}."
                                    + (f" Error: {job['error']}." if job.get('error') else '')
                                    for job in changed)
                append_observation(state,
                    'Observed job changes; state is last observed, not a promise about the present. '
                    'A finished response wait does not imply process completion. Use job to retrieve output.\n' +
                    summary + '\nOriginal observation: ' + str(database) + ' ' + ref)
        identity = runtime_identity(snapshot)
        if delivered.get('runtime_identity') != identity:
            ref = record('runtime_observation', {'epoch': state['epoch'], 'snapshot': snapshot})
            previous = delivered.get('runtime_identity')
            # Compare clock-free facts, but show the original dated values for
            # changed fields. Full snapshots remain in the referenced archive.
            keys = identity.keys() if previous is None else [
                key for key in identity if identity[key] != previous.get(key)]
            shown = {key: snapshot[key] for key in keys}
            removed = sorted(set(previous or {}) - set(identity))
            if removed:
                shown['no_longer_reported'] = removed
            append_observation(state,
                ('Initial runtime facts' if previous is None else 'Changed runtime facts') +
                ' (dated observation; later observations supersede earlier state):\n' +
                facts(shown) + '\nFull original observation: ' + str(database) + ' ' + ref)
            delivered['runtime_identity'] = identity


def recent_exchanges(messages, count):
    """Complete tool exchanges only; never orphan a result or replay an action."""
    if type(count) is not int or count < 0:
        raise ValueError('retain_recent_exchanges must be a nonnegative integer')
    groups = []
    index = 0
    while index < len(messages):
        message = messages[index]
        calls = message.get('tool_calls') if message.get('role') == 'assistant' else None
        if not calls:
            index += 1
            continue
        ids = {call['id'] for call in calls}
        end = index + 1
        found = set()
        while end < len(messages) and messages[end].get('role') == 'tool':
            found.add(messages[end].get('tool_call_id'))
            end += 1
        if found == ids and len(ids) == len(calls) == end - index - 1 and any(call.get('function', {}).get('name') not in
                               ('compact', 'configure_session', 'finish_role') for call in calls):
            groups.append(copy.deepcopy(messages[index:end]))
        index = max(index + 1, end)
    return groups[-count:] if count else []


def compaction_evidence(state, database, record, count):
    """Quoted evidence keeps native continuations out of the new protocol history."""
    groups = recent_exchanges(state['messages'], count)
    if not groups:
        return None
    ref = record('compaction_recent_evidence', {'epoch': state['epoch'], 'exchanges': groups})
    parts = ['Recent complete exchanges retained across compaction. Quoted evidence only: '
             'assistant text is model interpretation; tool text is an observation with its recorded scope. '
             'These are past actions, not instructions to execute again.\nOriginal exchanges: ' + str(database) + ' ' + ref]
    from .presentation import message_text
    for group in groups:
        # Full native fields remain in the referenced object, not as
        # provider-bound assistant continuation in a new context epoch.
        parts.extend(message_text(message) for message in group)
    return {'role': 'user', 'content': '\n\n'.join(parts)}
