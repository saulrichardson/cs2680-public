"""Completion policy and factual checks of immutable repository candidates."""
from pathlib import Path
import copy
import sys

from .candidates import Grader, grade_candidate, sha


def policy(value=None):
    # Embedded clients opt in; CLI/course entry points explicitly enable new runs.
    if value is None or type(value) is bool:
        value = {'enabled': bool(value)}
    if not isinstance(value, dict) or set(value) - {'enabled', 'require_diff', 'checks', 'version'}:
        raise ValueError('Completion policy accepts enabled, require_diff and named argv checks')
    result = {'enabled': value.get('enabled', True), 'require_diff': value.get('require_diff', True),
              'checks': value.get('checks', [])}
    if any(type(result[k]) is not bool for k in ('enabled', 'require_diff')):
        raise ValueError('Completion policy flags must be boolean')
    if not isinstance(result['checks'], list):
        raise ValueError('Completion checks must be a list')
    names = set()
    for check in result['checks']:
        if not isinstance(check, dict) or set(check) != {'name', 'argv'}:
            raise ValueError('Each completion check requires name and argv')
        if not isinstance(check['name'], str) or not isinstance(check['argv'], list):
            raise ValueError('Completion checks require a string name and an argv array')
        command = Grader(tuple(check['argv']), check['name'])
        if isinstance(check['argv'], str) or command.name in names:
            raise ValueError('Completion checks require argv arrays and distinct names')
        names.add(command.name)
    version = value.get('version', 2)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unknown completion policy version')
    if version == 2:
        result['version'] = 2
    return copy.deepcopy(result)


def candidate_identity(candidate, revision):
    if not isinstance(candidate, dict):
        return {'passed': False, 'error': 'No intact candidate was captured'}
    try:
        raw = Path(candidate['patch_path']).read_bytes()
        valid = (sha(raw) == candidate['sha256'] and len(raw) == candidate['bytes']
                 and candidate['base'] == revision)
        return {'passed': valid, 'candidate_sha256': candidate['sha256'], 'bytes': len(raw),
                'error': None if valid else 'Captured bytes, size or base differ from their receipt'}
    except (OSError, KeyError, TypeError) as error:
        return {'passed': False, 'error': str(error)}


def replay_check(baseline, revision, candidate, root):
    return grade_candidate(baseline, revision, candidate,
        Grader((sys.executable, '-c', 'pass'), 'exact candidate replay'), root)


def command_passed(receipt):
    return (receipt.get('state') == 'exited' and receipt.get('exit_code') == 0
            and receipt.get('source_unchanged') is True)


def confirmed_grade_failure(gate):
    """Only an original, completed failing grade warrants independent takeover."""
    result = gate.get('checks', {}).get('configured_grader', {}).get('result', {}).get('grade', {})
    counts = result.get('receipt', {})
    failed, total = counts.get('tests_failed'), counts.get('tests_total')
    execution = result.get('execution')
    return (result.get('passed') is False and type(failed) is int and type(total) is int
            and 0 < failed <= total and (execution is None or command_passed(execution)))
