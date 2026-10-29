"""Prose investigation plans, selected frozen guidance and exact-source test receipts.

Interpretations belong to the model. This module validates executable inputs and
compares observations, without inferring a diagnosis from logs or a review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from .candidates import Grader, grade_candidate, save
from .completion import command_passed


class PlanValidationError(ValueError):
    """Validation completed before any plan publication or test execution."""

    def __init__(self, error, **recovery):
        super().__init__(error)
        self.recovery = recovery


def validate_checks(checks):
    if not isinstance(checks, list):
        raise ValueError('checks must be an array')
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) - {'name', 'argv', 'cwd', 'expectation', 'result_format'}:
            raise ValueError('Checks accept name, argv, cwd, expectation and result_format')
        Grader(tuple(check['argv']), check['name'])
        if not isinstance(check['argv'], list) or check['name'] in seen:
            raise ValueError('Checks need argv arrays and unique names')
        seen.add(check['name'])
        cwd = Path(check.get('cwd', '.'))
        if cwd.is_absolute() or '..' in cwd.parts:
            raise ValueError('Check cwd must stay relative to the captured repository root')
        if check.get('result_format', 'exit') not in ('exit', 'cases_json'):
            raise ValueError('result_format must be exit or cases_json')
        if check.get('expectation', 'pass') not in ('pass', 'no_new_failures'):
            raise ValueError('expectation must be pass or no_new_failures')
        if check.get('expectation') == 'no_new_failures' and check.get('result_format') != 'cases_json':
            raise ValueError('no_new_failures requires cases_json; exit codes/counts cannot identify failures')
    return checks


def selected_guidance(root, selections):
    """Select actual sections; title, body and hash travel together as authored advice."""
    result = []
    root = Path(root).resolve()
    for selection in selections:
        path = (root / selection['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix != '.md':
            raise PlanValidationError('Select an existing Markdown file inside the frozen guidance root',
                code='invalid_guidance_path', guide_root=str(root),
                available_paths=sorted(str(p.relative_to(root)) for p in root.rglob('*.md')
                                       if p.resolve().is_relative_to(root)))
        raw = path.read_bytes()
        lines = raw.decode().splitlines()
        heading = selection.get('heading')
        if heading:
            index = next((i for i, line in enumerate(lines)
                          if line.lstrip('#').strip() == heading and line.startswith('#')), None)
            if index is None:
                raise PlanValidationError('Guidance heading not found: ' + heading,
                    code='unknown_guidance_heading', path=str(path),
                    available_headings=[line.lstrip('#').strip() for line in lines if line.startswith('#') and line.lstrip('#').strip()],
                    message='Choose an exact heading below, or omit heading to select the whole chapter. The previous plan is unchanged.')
            level = len(lines[index]) - len(lines[index].lstrip('#'))
            end = next((i for i in range(index + 1, len(lines)) if lines[i].startswith('#')
                        and len(lines[i]) - len(lines[i].lstrip('#')) <= level), len(lines))
            text = '\n'.join(lines[index:end])
        else:
            text = raw.decode()
        result.append({'author': 'frozen repair guidance', 'source_path': str(path),
                       'source_sha256': hashlib.sha256(raw).hexdigest(), 'section': heading,
                       'text': text, 'meaning': 'Authored advice selected by the model; not execution evidence.'})
    return result


def current(root):
    path = Path(root) / 'repair-plans/current.json'
    return json.loads(path.read_text()) if path.exists() else None


def register(root, guide_root, identity, prose, checks, guidance):
    try:
        if not isinstance(prose, str) or not prose.strip():
            raise ValueError('Write a useful prose investigation plan')
        validate_checks(checks)
        passages = selected_guidance(guide_root, guidance)
    except PlanValidationError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as error:
        raise PlanValidationError(str(error), code='invalid_plan',
            message='No plan was published and no checks ran. Correct the input and submit explicitly; the previous plan is unchanged.') from error
    record = {'id': identity, 'author': 'main model', 'plan': prose, 'checks': checks,
              'guidance': passages, 'created_at': time.time()}
    path = Path(root) / 'repair-plans' / (identity + '.json')
    save(path, record)
    save(Path(root) / 'repair-plans/current.json', record)
    return {'registered': True, 'plan_id': identity, 'plan_path': str(path),
            'plan': prose, 'checks': checks, 'authored_guidance': passages,
            'registered_check_count': len(checks),
            'verification_readiness': 'checks_registered_not_run' if checks else 'no_executable_checks',
            'verification_message': ('Commands are registered, not execution evidence.' if checks else
                                     'Zero executable checks are registered. This plan records reasoning only; verification is incomplete.'),
            'next': 'Run informative baseline checks when they help distinguish existing failures. Revise this plan when evidence changes. '
                    'Optionally use checkpoint(review=true) when an independent challenge would help.'}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Ambiguous duplicate JSON key: ' + key)
        result[key] = value
    return result


def cases(receipt):
    if receipt.get('state') != 'exited' or not receipt.get('source_unchanged'):
        return None
    try:
        value = json.loads(Path(receipt['stdout_path']).read_text(), object_pairs_hook=unique_object)
        result = value['cases']
        if (not isinstance(result, dict) or not result
                or any(not isinstance(k, str) or not k or v not in ('pass', 'fail', 'skip')
                       for k, v in result.items())):
            return None
        # A report saying everything passed cannot contradict the process exit.
        if receipt['exit_code'] != 0 and not any(v == 'fail' for v in result.values()):
            return None
        if not any(v in ('pass', 'fail') for v in result.values()):
            return None
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None


def assess(check, candidate, baseline=None):
    if check.get('result_format', 'exit') == 'exit':
        return {'passed': command_passed(candidate),
                'meaning': 'Process exit and source integrity only; test discovery/counts are not inferred.'}
    observed = cases(candidate)
    if observed is None:
        return {'passed': False, 'meaning': 'No valid cases_json execution report; verification is incomplete.'}
    if check.get('expectation', 'pass') == 'pass':
        passed = command_passed(candidate) and all(v == 'pass' for v in observed.values())
        return {'passed': passed, 'cases': observed, 'meaning': 'Every reported case must execute and pass.'}
    original = cases(baseline or {})
    if original is None:
        return {'passed': False, 'cases': observed, 'meaning': 'A valid saved baseline is required.'}
    regressions = sorted(k for k, v in observed.items() if v == 'fail' and original.get(k) != 'fail')
    missing = sorted(k for k, v in original.items() if v != 'skip' and observed.get(k) not in ('pass', 'fail'))
    skipped = sorted(k for k, v in observed.items() if v == 'skip' and original.get(k) != 'skip')
    return {'passed': not regressions and not missing and not skipped,
            'cases': observed, 'baseline_cases': original, 'new_failures': regressions,
            'missing_executions': missing, 'new_skips': skipped,
            'meaning': 'Reported case identities compared; surviving baseline failures remain unresolved. '
                       'This does not waive configured grader checks or establish task correctness.'}


def run(workflow, agent_id, plan, candidate, root, *, baseline=False):
    """Runs are append-only effects; an interrupted intent is observed, never replayed."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    result = {'plan_id': plan['id'], 'candidate': candidate, 'target': 'baseline' if baseline else 'candidate',
              'state': 'running', 'checks': [], 'started_at': time.time(),
              'evidence_scope': workflow.evidence_scope()}
    save(root / 'receipt.json', result)
    agent_root = Path(workflow.manager.descriptor(agent_id)['root'])
    for index, check in enumerate(plan['checks']):
        workflow.check_deadline()
        receipt = grade_candidate(workflow.starting_workspace(), candidate['base'], candidate,
            Grader(tuple(check['argv']), check['name']), root / str(index),
            cwd=check.get('cwd', '.'), deadline=workflow.manager.deadline)
        baseline_path = agent_root / 'repair-plans' / (plan['id'] + '.baseline.json')
        original = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
        originals = {c['name']: c['execution'] for c in original.get('checks', [])}
        result['checks'].append({'name': check['name'], 'execution': receipt,
            'definition_sha256': hashlib.sha256(json.dumps(check, sort_keys=True).encode()).hexdigest(),
            'evidence_scope': workflow.evidence_scope(),
            'assessment': assess(check, receipt, originals.get(check['name'])) if not baseline else
                          {'passed': None, 'meaning': 'Starting-source observation, not an acceptance decision.'}})
        save(root / 'receipt.json', result)
    result.update(state='finished', finished_at=time.time(), receipt_path=str(root / 'receipt.json'))
    if not baseline:
        result['passed'] = bool(result['checks']) and all(c['assessment']['passed'] for c in result['checks'])
        if not result['checks']:
            result['meaning'] = 'No executable checks were registered; verification is incomplete.'
    save(root / 'receipt.json', result)
    if baseline:
        save(agent_root / 'repair-plans' / (plan['id'] + '.baseline.json'), result)
    return result
