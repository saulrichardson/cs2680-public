"""Native screening retains the frozen planning/admission/completion policy."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from lab.adapters.teamharness.harvard import inventory
from test_main_flow import call, facts, finish, http_script, repository, TASK


class HarvardAdapter(unittest.TestCase):
    def test_frozen_policy_reaches_admission_and_exact_nonofficial_grade(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo = repository(root); bundle = root / 'bundle'
            for directory in ('experiment/course/src', 'tools', 'screens/range'):
                (bundle / directory).mkdir(parents=True)
            project = Path(__file__).resolve().parents[2]
            shutil.copytree(project / 'src', bundle / 'experiment/course/src', dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copy2(project / 'lab/adapters/teamharness/harvard.py', bundle / 'tools/harvard.py')
            campaign = {'max_agents': None, 'completion_gate': {'enabled': True, 'require_diff': True,
                'checks': [], 'version': 2}, 'planning_boundary': {'enabled': True, 'max_turns': 15,
                'version': 2, 'pause_after_planning': True}}
            (bundle / 'experiment/course/src/campaign.json').write_text(json.dumps(campaign))
            fixture = bundle / 'screens/range'
            (fixture / 'task.txt').write_text(TASK)
            (fixture / 'check.py').write_text('import sys,json\nsys.path.insert(0,sys.argv[1])\nfrom answer import answer\nprint(json.dumps({"tests_total":3,"tests_failed":sum(answer(x)!=x for x in [-3,0,7])}))\n')
            (bundle / 'inputs.json').write_text(json.dumps({'files': inventory(bundle)}))

            class Script:
                def __init__(self): self.requests=[]; self.errors=[]; self.owner=None
                def reply(self, request):
                    ident = facts(request)['agent_id']; self.owner = self.owner or ident
                    assert ident == self.owner, 'Admission must keep the conversation owner'
                    n = len(self.requests)
                    if n <= 15: return call('read_file', {'path':'answer.py'})
                    if n == 16:
                        assert len(request['tools']) == 1
                        assert 'assessment' in request['tools'][0]['function']['parameters']['properties']
                        return call('repair_plan', {'action':'continue',
                            'plan':'Inspected answer.py. Return the input and verify negative, zero and positive values through its real API.',
                            'assessment':{'readiness':'repair','effort':'small'}})
                    if n == 17:
                        return call('edit_file', {'path':'answer.py',
                            'replacements':[{'old':'return -1', 'new':'return n'}]})
                    assert n == 18, 'Unexpected extra planning or repair response'
                    return finish()

            script = Script(); output = root / 'output'
            with http_script(script) as env:
                result = subprocess.run([sys.executable, '-I', str(bundle / 'tools/harvard.py'),
                    'native-task', '--bundle', str(bundle), '--fixture', str(fixture),
                    '--workspace', str(repo), '--output', str(output), '--deadline', str(time.time()+60)],
                    env=dict(os.environ, **env), cwd=root, capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-3000:])
            self.assertFalse(script.errors)
            self.assertEqual(len(script.requests), 18)
            result = json.loads((output/'workflow.json').read_text())
            self.assertEqual(result['state'], 'completed')
            self.assertEqual(result['planning_boundary'], campaign['planning_boundary'])
            self.assertTrue(list((output/'agents/agents'/script.owner).glob('planning-admission.json')))
            grade = next(iter(result['evaluations'].values()))['result']
            self.assertIs(grade['passed'], True)
            self.assertIs(grade['official'], False)
            self.assertEqual(grade['candidate_sha256'], result['candidate']['sha256'])


if __name__ == '__main__':
    unittest.main()
