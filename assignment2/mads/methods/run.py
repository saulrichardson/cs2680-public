"""Run isolated, task-independent worked investigations with the actual fixtures.

Only a temporary copy is patched. No solver repository is read or modified.
Observations concern these toy contracts, not correctness of another repository.
"""
import argparse
import importlib.util
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


EXAMPLES = Path(__file__).with_name('examples')


def load(path):
    spec = importlib.util.spec_from_file_location('worked_fixture', path)
    module = importlib.util.module_from_spec(spec)
    # Compile actual file bytes, avoiding same-second stale bytecode after a patch.
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError('Teaching fixture no longer matches the explained change')
    path.write_text(text.replace(old, new))


def consumer():
    with tempfile.TemporaryDirectory(prefix='mads-method-consumer-') as directory:
        path = Path(directory)/'invoice.py'
        shutil.copy2(EXAMPLES/'invoice.py', path)
        helper = load(path).total_cents
        expected = 270  # 100 + 200 - 30, from the command's stated contract.
        assert helper([100, 200], 30) == expected
        def invoke(discount):
            proc = subprocess.run([sys.executable, '-B', str(path), '100', '200',
                                   '--discount-cents', str(discount)], text=True, capture_output=True)
            if proc.returncode:
                raise RuntimeError(f'CLI execution failed: {proc.stderr}')
            return int(proc.stdout.strip())
        before = invoke(30)
        assert before == 300 and before != expected
        assert invoke(0) == 300
        replace_once(path, 'print(sum(args.amounts))',
                     'print(total_cents(args.amounts, args.discount_cents))')
        after = invoke(30)
        assert after == expected and invoke(0) == 300
        assert invoke(400) == 0
        return dict(method='consumer', helper_passed=True, public_before=before,
                    public_expected=expected, public_after=after,
                    old_behavior_without_option=300, exhausted_total=0,
                    explanation='The helper was correct but unreachable from the changed behavior. '
                                'The CLI check, not a helper test, discriminated the implementations.')


def points(spans):
    # Independent finite-domain oracle: literal membership, not interval merging.
    return set().union(*(set(range(start, stop)) for start, stop in spans))


def minimize(spans, fails):
    """Delete pieces while failure survives, then normalize translation.

    This is a local reducer, not a claim of globally minimal input. Domain
    constraints are preserved. Actual fixture calls decide whether failure stays.
    """
    spans = list(spans)
    changed = True
    while changed:
        changed = False
        for i in range(len(spans)):
            smaller = spans[:i] + spans[i+1:]
            if fails(smaller):
                spans, changed = smaller, True
                break
    if not spans:
        return spans
    origin = min(start for start, _ in spans)
    normalized = [(start-origin, stop-origin) for start, stop in spans]
    return normalized if fails(normalized) else spans


def relations():
    with tempfile.TemporaryDirectory(prefix='mads-method-relations-') as directory:
        path = Path(directory)/'intervals.py'
        shutil.copy2(EXAMPLES/'intervals.py', path)
        before = load(path).covered_length
        displayed = [([], 0), ([(0, 2)], 2), ([(0, 2), (1, 3)], 3), ([(0, 1), (3, 5)], 3)]
        for spans, expected in displayed:
            assert before(spans) == expected
        intervals = [(a, b) for a in range(-2, 4) for b in range(a, 4)]
        # A deliberately small complete toy domain, not an agent test/time budget.
        cases = [list(spans) for count in range(4) for spans in itertools.product(intervals, repeat=count)]
        failures = [spans for spans in cases if before(spans) != len(points(spans))]
        assert failures
        witness = minimize(failures[0], lambda spans: before(spans) != len(points(spans)))
        old_value, want = before(witness), len(points(witness))
        replace_once(path, 'right = stop  # BUG: can shrink an already covered interval.',
                     'right = max(right, stop)')
        after = load(path).covered_length
        relation_checks = 0
        for spans in cases:
            value = after(spans)
            assert value == len(points(spans))
            assert after(list(reversed(spans))) == value  # permutation
            assert after(spans + spans) == value  # duplicate representation
            translated = [(a+11, b+11) for a, b in spans]
            assert after(translated) == value
            split = [(a, (a+b)//2) for a, b in spans] + [((a+b)//2, b) for a, b in spans]
            assert after(split) == value  # partition represents the same union
            relation_checks += 4
        # Also challenge the check itself: inclusive-stop convention is wrong here.
        oracle_example = [(2, 2)]
        incorrect_expectation, actual = 1, after(oracle_example)
        assert actual != incorrect_expectation and actual == 0
        return dict(method='relations', displayed_passed=len(displayed), generated_cases=len(cases),
                    prior_failure_cases=len(failures), reduced_witness=witness,
                    public_before=old_value, public_expected=want, public_after=after(witness),
                    relation_checks=relation_checks,
                    rejected_oracle=dict(input=oracle_example, old_expectation=incorrect_expectation,
                                         observed=actual, corrected_expectation=0,
                                         reason='[start, stop) excludes stop; equal endpoints cover no units.'),
                    explanation='The maintained boundary must never shrink while adding covered points. '
                                'The independent membership oracle and equivalent representations '
                                'exposed the assumption missed by the displayed overlap examples.')


METHODS = {'consumer': consumer, 'relations': relations}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('method', choices=[*METHODS, 'all'])
    args = parser.parse_args(argv)
    chosen = METHODS.values() if args.method == 'all' else [METHODS[args.method]]
    for method in chosen:
        print(json.dumps(method(), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
