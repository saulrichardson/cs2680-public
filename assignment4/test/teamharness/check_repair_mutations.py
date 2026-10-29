"""Prove the repair tests detect chosen invariant violations in disposable copies.

No live source is edited. A mutant counts as caught only on an assertion failure
in its designated test, never on a syntax/import/dependency error. This is a
bounded mutation screen, not an exhaustive mutation score.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
MUTATIONS=[
    ('ignore-new-failing-identities','src/teamharness/repair.py',
     "not regressions and not missing and not skipped", "not missing and not skipped",
     'RepairLaws.test_case_identity_contract'),
    ('ignore-disappearing-tests','src/teamharness/repair.py',
     "not regressions and not missing and not skipped", "not regressions and not skipped",
     'RepairLaws.test_case_identity_contract'),
    ('ignore-new-skips','src/teamharness/repair.py',
     "not regressions and not missing and not skipped", "not regressions and not missing",
     'RepairLaws.test_case_identity_contract'),
    ('accept-duplicate-json-identities','src/teamharness/repair.py',
     ', object_pairs_hook=unique_object', '',
     'RepairLaws.test_ambiguous_duplicate_case_identity_is_not_evidence'),
    ('reuse-tests-for-other-bytes','src/teamharness/main.py',
     "and observed.get('candidate', {}).get('sha256') == candidate['sha256']", 'and True',
     'RepairReachability.test_success_and_recovery_are_reachable_without_accepting_stale_evidence'),
    ('replay-uncertain-command','src/teamharness/main.py',
     'if uncertain else repair.run', 'if False else repair.run',
     'RepairReachability.test_success_and_recovery_are_reachable_without_accepting_stale_evidence'),
    ('let-review-override-test-failure','src/teamharness/main.py',
     "check('executable_test_plan', tests.get('passed') is True, result=tests)",
     "check('executable_test_plan', True, result=tests)",
     'RepairReachability.test_reviewer_opinion_cannot_make_a_bad_candidate_pass'),
]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    records=[]
    with tempfile.TemporaryDirectory() as temp:
        sandbox=Path(temp)
        shutil.copytree(ROOT/'src',sandbox/'src',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copytree(ROOT/'test/teamharness',sandbox/'test/teamharness',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        env={**os.environ,'PYTHONPATH':str(sandbox/'test/teamharness')+os.pathsep+str(sandbox),
             'REPAIR_PROPERTY_DATABASE':str(sandbox/'hypothesis')}
        for name,file,old,new,test in MUTATIONS:
            path=sandbox/file; original=path.read_text()
            assert original.count(old)==1, ('Mutation anchor changed',name)
            path.write_text(original.replace(old,new)); started=time.monotonic()
            try:
                # Fresh interpreters and no bytecode prevent cross-mutant module reuse.
                result=subprocess.run([sys.executable,'-B','-m','unittest','-v',
                    'test_repair_properties.'+test],cwd=sandbox,env=env,
                    capture_output=True,text=True,timeout=45)
                output=result.stdout+result.stderr
                (args.output/(name+'.log')).write_text(output)
                caught=(result.returncode!=0 and 'AssertionError' in output and
                        ('FAIL: '+test.split('.')[-1]) in output and 'ERROR:' not in output)
                records.append(dict(mutation=name,caught=caught,test=test,
                                    elapsed_seconds=round(time.monotonic()-started,3)))
                print(name, 'CAUGHT' if caught else 'NOT VERIFIED',flush=True)
            finally: path.write_text(original)
    (args.output/'results.json').write_text(json.dumps(records,indent=2)+'\n')
    if not all(r['caught'] for r in records): raise SystemExit('Mutation screen failed; inspect logs')


if __name__=='__main__': main()
