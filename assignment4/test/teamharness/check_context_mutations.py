"""Bounded sensitivity check of context invariants, editing disposable copies only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
MUTANTS=[
 ('splice-replaceable-facts-into-history','src/teamharness/session.py',
  'if context_policy_of(state) == APPEND_ONLY:\n        return messages',
  'if False:\n        return messages',
  'ActualContext.test_http_needle_retrieval_notes_and_compaction'),
 ('hide-candidate-change','src/teamharness/context.py','    return value\n',
  "    value.pop('candidate_evidence', None)\n    return value\n",
  'ContextLaws.test_clock_changes_do_not_erase_execution_changes'),
 ('repeat-every-clock-tick','src/teamharness/context.py',"    value.pop('observed_at', None)\n",'',
  'ContextLaws.test_clock_changes_do_not_erase_execution_changes'),
 ('discard-recent-evidence','src/teamharness/session.py',
  'recent = compaction_evidence(state, self.database, self._record, keep_recent)','recent = None',
  'ActualContext.test_http_needle_retrieval_notes_and_compaction'),
 ('let-summary-erase-execution-facts','src/teamharness/session.py',
  "            state['context_delivery'] = {'jobs': terminal}\n            self._prepare_context(state)",
  "            state['context_delivery'] = {'jobs': terminal}",
  'ActualContext.test_compaction_keeps_failed_facts_independent_of_favorable_summary'),
 ('end-main-on-prose','src/teamharness/session.py',"if state['config']['role'] == 'main':",'if False:',
  'ActualContext.test_http_needle_retrieval_notes_and_compaction'),
]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False);results=[]
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        for name in ('src','test/teamharness'):
            shutil.copytree(ROOT/name,root/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        env={**os.environ,'PYTHONPATH':str(root/'test/teamharness')+os.pathsep+str(root),'CONTEXT_PROPERTY_SEED':'2680'}
        for name,path,old,new,target in MUTANTS:
            file=root/path;raw=file.read_text();assert raw.count(old)==1,(name,'anchor changed')
            file.write_text(raw.replace(old,new))
            try:
                run=subprocess.run([sys.executable,'-B','-m','unittest','-v','test_context_properties.'+target],
                    cwd=root,env=env,capture_output=True,text=True,timeout=40)
                output=run.stdout+run.stderr;(args.output/(name+'.log')).write_text(output)
                caught=(run.returncode!=0 and 'AssertionError' in output and 'FAIL: '+target.split('.')[-1] in output and 'ERROR:' not in output)
                results.append({'mutation':name,'test':target,'caught':caught});print(name,caught,flush=True)
            finally:file.write_text(raw)
    (args.output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    if not all(r['caught'] for r in results):raise SystemExit('An invariant violation was not caught by its intended assertion')


if __name__=='__main__':main()
