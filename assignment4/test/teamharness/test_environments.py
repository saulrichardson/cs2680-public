"""Reference-free preparation runs without solutions; frozen staging uses exact images."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('team_environments',
    Path(__file__).resolve().parents[2] / 'lab/adapters/teamharness/environments.py')
environments = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(environments)

# Minimal executable course boundary: the forbidden branch would fail if reached.
PREPARE = '''#!/bin/bash
# Historical warmer description.
set -euo pipefail
TASKS="$1"
V2="$2"
python3 - "$TASKS" "$V2" <<'PYIN'
import json, os, sys
tasks, v2 = sys.argv[1:]
for t in json.load(open(tasks)).values():
    d = os.path.join(v2, t["instance_id"])
    need = ["tests/test.sh", "tests/config.json"] + (["solution/gold_patch.diff"] if t.get("sandbox_image") else [])
    for f in need:
        if not os.path.isfile(os.path.join(d, f)): raise RuntimeError(f)
PYIN
# --- 5. pre-warmed sandbox images ---------------------------------------------------
cat "$V2/task/solution/gold_patch.diff"
exit 99
log "all images present"
'''
# Define log before invoking the converted script; no container runtime is used.
PREPARE = PREPARE.replace('TASKS="$1"', 'log() { echo "$*"; }; TASKS="$1"')


class ReferenceFreeEnvironments(unittest.TestCase):
    def test_preparation_executes_with_verifier_assets_but_no_solution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = root / 'v2/task/tests'; tests.mkdir(parents=True)
            for name in ('test.sh', 'config.json'):
                (tests / name).write_text('fixture')
            manifest = root / 'tasks.json'
            manifest.write_text(json.dumps({'0': {'instance_id': 'task', 'sandbox_image': 'old-warm'}}))
            script = root / 'prepare.sh'
            script.write_text(environments.reference_free_prepare(PREPARE))
            result = subprocess.run(['bash', str(script), str(manifest), str(root/'v2')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('all images present', result.stdout)
            self.assertFalse((root/'v2/task/solution').exists())
            (tests/'test.sh').unlink()
            failed = subprocess.run(['bash', str(script), str(manifest), str(root/'v2')], capture_output=True)
            self.assertNotEqual(failed.returncode, 0)

    def test_unrecognized_preparation_is_not_silently_rewritten(self):
        with self.assertRaises(ValueError):
            environments.reference_free_prepare('#!/bin/bash\necho unknown\n')

    def test_frozen_stock_projection_uses_original_image_for_solver_and_grader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'source'; package = root/'package'
            scripts = source/'starter_code/evaluation_scripts'; scripts.mkdir(parents=True)
            (scripts/'prepare_images.sh').write_text(PREPARE)
            from test_course_submission import CONTROLLER
            (scripts/'run_all.py').write_text(CONTROLLER)
            (source/'dev/tasks').mkdir(parents=True)
            row = {'instance_id': 'task', 'docker_image': 'original:task', 'sandbox_image': 'reference-warm:task'}
            (source/'dev/tasks/hard51.json').write_text(json.dumps({'0': row}))
            (source/'dev/run_evaluation.py').write_text('import json\nfrom pathlib import Path\ndef stage(manifest, scaffold):\n' +
                "    (scaffold / 'evaluation_scripts/agent_task_input.json').write_text(json.dumps(manifest, indent=2))\n")
            (package/'src').mkdir(parents=True)
            (package/'src/fixture.py').write_text('# runtime\n')
            frozen = root/'frozen'
            config = environments.freeze(source, package, ['0'], frozen)
            self.assertEqual(config['dependency_mode'], 'stock')
            self.assertFalse(config['reference_solutions_used'])
            self.assertTrue(config['final_submission']['original_evaluator_unchanged'])
            self.assertEqual((scripts/'run_all.py').read_text(), CONTROLLER)
            self.assertNotIn('extras', config)
            self.assertEqual((scripts/'prepare_images.sh').read_text(), PREPARE)
            self.assertEqual(json.loads((frozen/'dev/tasks/hard51.json').read_text())['0'], row)
            receipt = root/'receipt.json'
            receipt.write_text(json.dumps({'passed': True, 'images': {'task': {'image_id': 'sha256:original'}}}))
            module = runpy.run_path(str(frozen/'dev/run_evaluation.py'))
            stage = module['stage']
            stage.__globals__['Path'] = lambda p: receipt if p == '/data/environment-checks/RESULT.json' else Path(p)
            scaffold = root/'scaffold'; (scaffold/'evaluation_scripts').mkdir(parents=True)
            stage({'0': dict(row)}, scaffold)
            staged = json.loads((scaffold/'evaluation_scripts/agent_task_input.json').read_text())['0']
            self.assertEqual(staged['docker_image'], 'original:task')
            self.assertEqual(staged['sandbox_image'], 'sha256:original')
            # Both provided runner paths select this same sandbox_image.
            self.assertEqual(staged.get('sandbox_image') or staged['docker_image'], 'sha256:original')
            receipt.write_text(json.dumps({'passed': False}))
            with self.assertRaisesRegex(RuntimeError, 'not passed'):
                stage({'0': dict(row)}, scaffold)
