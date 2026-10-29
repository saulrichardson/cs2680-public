"""Explicit model-authored planning decisions under a frozen exploration limit.

The runtime records a choice, not a verdict about the quality of its reasoning.
Planning keeps the ordinary investigation tools, including the trusted shell.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from .candidates import save
from .repair import PlanValidationError


PROMPT = """Initial repository investigation and planning
Use the exploration responses to investigate the actual repository before writing
your plan. Batch independent read_file, search_files and exec_command calls in
one response: ordinary tool calls execute in parallel. Read several independent
files together rather than spending one response per file. Keep dependent actions
in separate responses and poll existing jobs instead of relaunching them.

Trace the human's required operation through its entry point, callers, state owner
and consumers. Inspect relevant implementation and tests, and use focused
experiments to distinguish plausible explanations. Retrieve guidance that helps
resolve a concrete uncertainty. The shell and source edits remain available.

After the recorded exploration allowance, you receive one planning response with
the last observations. Only repair_plan is available in that response. Return
one continue decision with a detailed prose plan, or skip with an evidence-grounded
reason. A useful next experiment is a valid reason to continue even when the exact
fix is not yet known. The plan must explain:
- Required behavior: concrete input/operation, expected result and constraints.
- Repository investigation: inspected files/functions, entry point, callers,
  state owner and consumers; distinguish inspected facts from assumptions.
- Diagnosis and uncertainty: evidence for the explanation and unresolved questions.
- Next work: proposed changes or a distinguishing experiment, with reasons.
- Verification: exercise the real operation and expose a plausible incomplete fix.

The plan is model interpretation, not proof that the diagnosis is correct. Prose
verification is required in the plan; register executable checks with action=set
after continuation when you know valid commands. No draft-publication step is
needed. Continue preserves your conversation, source, jobs and original deadline.
Skip recommends stopping this attempt. No valid decision in the planning response
records planning_timeout, not infeasibility. Reopening and compaction do not renew
the exploration allowance or the original deadline.

During exploration, repair_plan is not available: investigate with the advertised
tools rather than trying to publish a plan early. In the decision response use
exactly {"action":"continue","plan":"your detailed prose plan"} or
{"action":"skip","reason":"your grounded reason"}. Do not add checks,
guidance, target or checkpoint_id. assessment is included only when the task
allocation instruction explicitly requests it. Write verification commands in
the plan prose; register their executable form after continuation.
"""

BATCH_PROMPT = """Task allocation (runtime policy)
This investigation pauses after its planning decision so the course controller
can compare all tasks. Include assessment={"readiness": ..., "effort": ...} in
your sole repair_plan continue/skip call. Readiness is repair (a concrete repair
path), experiment (a bounded distinguishing experiment), search (an open question),
or blocked (an observed environment blocker). Effort is small, medium, or large
remaining work. Ground these estimates in the plan or skip reason and saved
observations. They are your judgments, not measured probabilities or verdicts.
In this mode skip recommends deferral; it does not close the course task.
The controller may allocate further work despite that recommendation. Admission
preserves this conversation, work, spent planning responses and original deadline.
No further model request is made while waiting for admission.
After admission, proceed with ordinary investigation and implementation tools.
Use action=set to create or revise a plan; no second continue/skip vote is needed.
"""

READINESS = ('repair', 'experiment', 'search', 'blocked')
EFFORT = ('small', 'medium', 'large')


def assessment(value):
    if (not isinstance(value, dict) or set(value) != {'readiness', 'effort'}
            or value['readiness'] not in READINESS or value['effort'] not in EFFORT):
        raise PlanValidationError('Assessment needs readiness=repair/experiment/search/blocked and effort=small/medium/large.',
                                  code='invalid_planning_assessment')
    return dict(value)


def policy(value=None):
    if value is None or isinstance(value, bool):
        value = {'enabled': bool(value)}
    if not isinstance(value, dict) or set(value) - {'enabled', 'max_turns', 'pause_after_planning', 'version'}:
        raise ValueError('Planning policy accepts enabled, max_turns, pause_after_planning and version')
    enabled, turns = value.get('enabled', True), value.get('max_turns', 15)
    if type(enabled) is not bool or type(turns) is not int or turns < 1:
        raise ValueError('Planning needs a boolean enabled and a positive integer max_turns')
    paused = value.get('pause_after_planning', False)
    if type(paused) is not bool or (paused and not enabled):
        raise ValueError('Paused allocation requires an enabled planning boundary')
    version = value.get('version', 2)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unknown planning policy version')
    return {'enabled': enabled, 'max_turns': turns, **({'version': 2} if version == 2 else {}),
            **({'pause_after_planning': True} if paused else {})}


def current(root):
    path = Path(root) / 'planning-decision.json'
    return json.loads(path.read_text()) if path.exists() else None


def ensure_open(root):
    if (Path(root) / 'planning-admission.json').exists():
        raise PlanValidationError('The controller already admitted this investigation; revise its plan with action=set.',
                                  code='planning_already_decided')
    previous = current(root)
    if previous and previous['decision'] == 'continue':
        raise PlanValidationError('Implementation has already started; use action=set to revise the plan.',
                                  code='planning_already_decided')


def decide(root, identity, decision, *, plan=None, reason=None, allocation_assessment=None, paused=False):
    ensure_open(root)
    if decision == 'continue':
        if plan is None:
            raise PlanValidationError('Register a plan with action=set, or supply plan with action=continue.',
                                      code='missing_repair_plan')
        details = {'plan_id': plan['id'], 'explanation': plan['plan']}
    elif decision == 'skip':
        if not isinstance(reason, str) or not reason.strip():
            raise PlanValidationError('A skip needs a nonempty reason grounded in the investigation.',
                                      code='missing_skip_reason')
        details = {'reason': reason}
    else:
        raise ValueError('Unknown planning decision')
    record = {'id': identity, 'decision': decision, 'author': 'main model',
              'observed_at': time.time(), **details}
    if allocation_assessment is not None:
        record['assessment'] = assessment(allocation_assessment)
    elif paused:
        raise PlanValidationError('Include a readiness/effort assessment for task allocation.', code='missing_planning_assessment')
    save(Path(root) / 'planning-decisions' / (identity + '.json'), record)
    save(Path(root) / 'planning-decision.json', record)
    if paused:
        phase = 'ready'
        meaning = 'Planning is complete; the controller has not admitted implementation. This recommendation does not close the course task.'
    elif decision == 'continue':
        phase = 'implementation'
        meaning = 'The model chose to implement this plan; feasibility and correctness are not established.'
    else:
        phase = 'skipped'
        meaning = 'The model declined this attempt. Work and evidence remain; no repair is accepted and no takeover is requested.'
    return {'planning_decision': record, 'phase': phase, 'meaning': meaning}


def admit(root, facts, reason):
    """Called by the workflow owner under its existing flock, never by a model tool."""
    path = Path(root) / 'planning-admission.json'
    reference = {'decision_id': facts.get('decision_id'), 'turns_used': facts['turns_used'],
                 'max_turns': facts['max_turns']}
    if path.exists():
        previous = json.loads(path.read_text())
        if previous['planning'] != reference or previous['reason'] != reason:
            raise ValueError('Admission identity cannot be reused for a different planning result or reason')
        return previous
    if facts['phase'] != 'ready' or not isinstance(reason, str) or not reason.strip():
        raise ValueError('Admission requires a paused planning result and an explicit allocation reason')
    record = {'author': 'course allocation controller', 'planning': reference,
              'reason': reason, 'observed_at': time.time(),
              'meaning': 'Compute allocation only; the original model recommendation and uncertainty remain unchanged.'}
    save(path, record)
    return record


def observe(state, root):
    """Project durable decisions; returning terminal state never replays effects."""
    config = state['config']
    frozen = config.get('metadata', {}).get('planning_boundary', {})
    if config['role'] != 'main' or not frozen.get('enabled'):
        return None
    facts = state.setdefault('planning', {'phase': 'planning', 'turns_used': 0,
                                        'max_turns': frozen['max_turns']})
    paused = frozen.get('pause_after_planning', False)
    decision = current(root)
    if decision and decision['id'] != facts.get('decision_id'):
        facts.update(phase='ready' if paused else 'implementation' if decision['decision'] == 'continue' else 'skipped',
                     decision_id=decision['id'], decision=decision)
        if decision['decision'] == 'skip' and not paused:
            state.update(status='skipped', outcome='skip', report=decision['reason'])
    admission_path = Path(root) / 'planning-admission.json'
    if paused and admission_path.exists():
        admission = json.loads(admission_path.read_text())
        expected = {'decision_id': facts.get('decision_id'), 'turns_used': facts['turns_used'], 'max_turns': facts['max_turns']}
        if admission['planning'] != expected:
            raise ValueError('Admission does not identify this planning result')
        facts.update(phase='implementation', admission=admission)
    elif paused and facts['phase'] == 'ready':
        state.update(status='planning_complete', outcome=None,
                     report='Planning completed; waiting for controller allocation. No repair is accepted.')
    if frozen.get('version') == 2:
        facts['policy_version'] = 2
    facts['turns_remaining'] = max(0, facts['max_turns'] - facts['turns_used'])
    facts['meaning'] = ('Choose repair_plan action=continue with a plan, or action=skip with a reason, '
                        'as a sole call in the planning response after exploration. '
                        'Ordinary tools remain available for investigation and experiments; no read-only shell sandbox is implied.')
    if facts['phase'] == 'decision':
        facts['meaning'] = 'The single decision response exposes only repair_plan: continue with the detailed plan, or skip with a grounded reason.'
    return facts


def before_request(state, root):
    facts = observe(state, root)
    if facts and facts['phase'] == 'skipped' and state['status'] == 'running':
        # Only explicit operator continuation can reopen a closed skipped owner.
        # Preserve the spent turns and all previous decision records.
        facts['phase'] = 'planning'
    frozen = state['config'].get('metadata', {}).get('planning_boundary', {})
    if facts and facts['phase'] == 'planning' and facts['turns_remaining'] == 0 and frozen.get('version') == 2:
        facts['phase'] = 'decision'
        state['messages'].append({'role': 'user', 'content':
            'Exploration is complete. Read the last tool observations above. This is your single planning response. '
            'Call repair_plan exactly once: action=continue with a detailed repository-grounded plan covering '
            'required behavior, inspected code path, diagnosis/uncertainty, next work and verification; '
            'or action=skip with an evidence-grounded reason. No investigation tools, checks or guidance selection '
            'are available in this response. Register executable checks after continuation.'})
    exhausted = facts and ((facts['phase'] == 'decision' and facts.get('decision_response_used')) or
                           (facts['phase'] == 'planning' and facts['turns_remaining'] == 0))
    if exhausted:
        paused = frozen.get('pause_after_planning', False)
        if paused:
            facts.update(phase='ready', result='planning_timeout')
        state.update(status='planning_complete' if paused else 'planning_timeout', outcome=None,
                     report='Planning ended without a valid continue or skip decision. Evidence and work are preserved.')
    return state['status'] in ('skipped', 'planning_timeout', 'planning_complete')


def count_response(state):
    facts = state.get('planning')
    if facts and facts['phase'] == 'planning':
        facts['turns_used'] += 1
    elif facts and facts['phase'] == 'decision':
        facts['decision_response_used'] = True


def request_tools(state, tools):
    """Pure projection: saved phase selects the exact tools used on the wire."""
    frozen = state['config'].get('metadata', {}).get('planning_boundary', {})
    phase = state.get('planning', {}).get('phase')
    if frozen.get('version') != 2 or not frozen.get('enabled') or phase not in ('planning', 'decision'):
        return tools
    if phase == 'planning':
        return [tool for tool in tools if tool['function']['name'] not in ('repair_plan', 'finish_role')]
    properties = {
        'action': {'type': 'string', 'enum': ['continue', 'skip']},
        'plan': {'type': 'string', 'minLength': 1, 'description':
            'Detailed prose: required behavior; inspected repository files/functions and actual caller/state/consumer path; '
            'evidence-backed diagnosis and uncertainty; proposed changes or next experiment; verification through the real operation.'},
        'reason': {'type': 'string', 'minLength': 1, 'description': 'For skip: what was inspected and why no credible next step was found.'}}
    required = ['action']
    if frozen.get('pause_after_planning'):
        properties['assessment'] = {'type': 'object', 'properties': {
            'readiness': {'type': 'string', 'enum': list(READINESS)},
            'effort': {'type': 'string', 'enum': list(EFFORT)}},
            'required': ['readiness', 'effort'], 'additionalProperties': False}
        required.append('assessment')
    schema = {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
    if state.get('tool_contract_version', 1) >= 2:
        schema['oneOf'] = [
            {'properties': {'action': {'const': 'continue'}, 'plan': {'pattern': r'\S'}},
             'required': ['plan'], 'not': {'required': ['reason']}},
            {'properties': {'action': {'const': 'skip'}, 'reason': {'pattern': r'\S'}},
             'required': ['reason'], 'not': {'required': ['plan']}}]
        schema['examples'] = [
            {'action': 'continue', 'plan': 'Required behavior: ... Repository findings: ... Diagnosis and uncertainty: ... Next work: ... Verification: ...'},
            {'action': 'skip', 'reason': 'Describe the inspected evidence and why no credible next step was found.'}]
        if frozen.get('pause_after_planning'):
            for example in schema['examples']:
                example['assessment'] = {'readiness': 'search', 'effort': 'medium'}
    return [{'type': 'function', 'function': {'name': 'repair_plan',
        'description': 'Return the investigation decision. Continue requires plan; skip requires reason. Call exactly once.',
        'parameters': schema}}]
