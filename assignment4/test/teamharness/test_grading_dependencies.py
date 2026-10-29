"""Grader-only network access retains exact patch, scripts and Docker arguments."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from lab.adapters.teamharness.grading_dependencies import grade_dependency_access, install


class GradingDependencies(unittest.TestCase):
    def test_grader_patch_mounts_and_script_stay_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'model.patch').write_bytes(b'exact candidate\n')
            script = 'git apply /replay/model.patch; bash /tests/test.sh'
            argv = ['run', '--rm', '--name', 'a3grade_fixture', '--network', 'none',
                    '-v', '/official/tests:/tests:ro', '-v', str(root)+':/replay:ro',
                    '-v', '/output:/logs/verifier', 'sha256:original', '-c', script]
            result, record = grade_dependency_access(argv)
            self.assertEqual(argv[argv.index('--network')+1], 'none')
            self.assertEqual(result[result.index('--network')+1], 'bridge')
            self.assertEqual(result[-3:], ['sha256:original', '-c', script])
            self.assertEqual(record['candidate_sha256'], hashlib.sha256(b'exact candidate\n').hexdigest())
            self.assertEqual((root/'model.patch').read_bytes(), b'exact candidate\n')
            self.assertFalse(record['solver_network_changed'])
            self.assertFalse(record['official_offline_environment_parity'])
            for changed in [argv[:3]+['solver']+argv[4:], [a.replace('/tests:ro','/tests:rw') for a in argv]]:
                with self.assertRaises(ValueError):grade_dependency_access(changed)

    def test_unknown_wrapper_refuses_instrumentation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'dev').mkdir();p=root/'dev/grading_docker.py';p.write_text('# unknown\n')
            with self.assertRaisesRegex(ValueError,'Unrecognized'):install(root)
            self.assertEqual(p.read_text(),'# unknown\n')

    def test_installed_wrapper_executes_and_retains_exact_network_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'dev').mkdir();(root/'bin').mkdir()
            source=Path(__file__).resolve().parents[2]/'.local/evaluations/team-handoff-fail32-20261027/run/fleet-source/dev/grading_docker.py'
            # This fixture is the frozen wrapper used by the existing controller.
            if not source.exists():
                self.skipTest('Frozen fleet wrapper fixture unavailable')
            shutil.copy2(source,root/'dev/grading_docker.py');install(root)
            docker=root/'bin/docker'
            docker.write_text('#!/usr/bin/env python3\nimport os,sys,json\nfrom pathlib import Path\na=sys.argv[1:]\nPath(os.environ["OBSERVATION"]).write_text(json.dumps(a))\n')
            docker.chmod(0o755)
            output=root/'result';(output/'replay').mkdir(parents=True)
            (output/'replay/model.patch').write_bytes(b'exact patch\n')
            argv=['python3',str(root/'dev/grading_docker.py'),'run','--rm','--name','a3grade_fixture',
                  '--network','none','-v','/official:/tests:ro','-v',str(output/'replay')+':/replay:ro',
                  '-v',str(output)+':/logs/verifier','original-image','-c','bash /tests/test.sh']
            subprocess.run(argv,check=True,env={**os.environ,'PATH':str(root/'bin')+os.pathsep+os.environ['PATH'],
                'OBSERVATION':str(root/'docker-args.json'),'A3_GRADING_ENVIRONMENT':'stock'})
            args=json.loads((root/'docker-args.json').read_text())
            self.assertEqual(args[args.index('--network')+1],'bridge')
            meta=json.loads((output/'grading-environment.json').read_text())
            self.assertEqual(meta['mode'],'aws_dependency_network')
            sha=hashlib.sha256(b'exact patch\n').hexdigest()
            self.assertEqual(meta['dependency_access']['candidate_sha256'],sha)
            receipt=json.loads((root/'attempts/result/a3grade_fixture.receipt.json').read_text())
            self.assertEqual(receipt['patch_sha256'],sha)


if __name__ == '__main__':
    unittest.main()
