"""Real Git export and cleanup ordering, independent of a model completion claim."""
import json,os,subprocess,sys
from pathlib import Path
from mads.store import Store,dump
from mads.tools import Tools
from mads.runner import export
if __name__ == '__main__':
    root=Path(sys.argv[1]).resolve();repo=root/'repo';repo.mkdir(parents=True)
    run=root/'run';store=Store(run/'journal');tools=Tools(repo,run,store)
    def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.STDOUT)
    git('init');git('config','user.email','harness-check@example.invalid');git('config','user.name','Harness boundary check')
    (repo/'source').write_text('base\n');(repo/'remove').write_text('remove\n');(repo/'binary').write_bytes(b'\x00base\x01')
    git('add','-A');git('commit','-m','base');base=git('rev-parse','HEAD').decode().strip();index=(repo/'.git/index').read_bytes()
    tools.write('source','changed\n');os.chmod(repo/'source',0o755);tools.write('new-source','new\n')
    (repo/'remove').unlink();(repo/'binary').write_bytes(b'\x00changed\xff');(repo/'link').symlink_to('source')
    (run/'memory').mkdir();(run/'memory/working.md').write_text('outside submitted repo')
    job=tools.bash("printf started; sleep 30; printf late > late-file",.1)
    assert job['process']['state']=='running'
    cleanup=tools.jobs.close()
    assert all(row['process']['state']=='exited' and row['process']['capture_status']=='complete' for row in cleanup)
    (run/'result.json').write_text(dump({'agent_outcome':{'status':'completed','claim':'literal export fixture, not a model verdict'},'harness_outcome':{'status':'completed'},'cleanup_outcome':{'status':'completed','jobs':cleanup}}))
    assert export(repo,base,run)==0
    assert not (repo/'late-file').exists()
    assert (repo/'.git/index').read_bytes()==index
    patch=(run/'patch.diff').read_text()
    for marker in ('new mode 100755','deleted file mode','new file mode 120000','GIT binary patch','new-source'):assert marker in patch,marker
    assert 'working.md' not in patch
    # A real export failure must remain distinct from a model's completed claim.
    assert export(repo,'does-not-exist',run)==1
    result=json.loads((run/'result.json').read_text())
    assert result['agent_outcome']['status']=='completed' and result['export_outcome']['status']=='failed'
    (root/'coverage.json').write_text(json.dumps({'verified':['E1-mechanical','E2'],'result':result},indent=2))
    print('Verified cleanup-before-export, Git-supported changes, unchanged original index, and explicit export failure')
