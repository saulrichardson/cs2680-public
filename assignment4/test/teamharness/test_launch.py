import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import shutil


SPEC = importlib.util.spec_from_file_location('team_launch', Path(__file__).resolve().parents[2] / 'lab/adapters/teamharness/launch.py')
launch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launch)


class LaunchPreparationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.source = self.root / 'source'
        for sub in ('dev/tasks', 'dev/fleet/host', 'dev/teamharness-validation', 'starter_code'):
            (self.source / sub).mkdir(parents=True)
        manifest = self.source / 'dev/tasks/hard51.json'
        manifest.write_text(json.dumps({str(n): {'instance_id': f'instance-{n}'} for n in range(51)}))
        controller = self.source / 'dev/fleet/fleet.py'; controller.write_text('# pinned fleet\n')
        runner = self.source / 'dev/fleet/host/runner.original.py'; runner.write_text('# pinned runner\n')
        (self.source / 'dev/fleet/host/runner.py').write_text('# validation wrapper\n')
        (self.source / 'dev/teamharness-validation/manifest.json').write_text('{}')
        for name, value in [('HARD51_SHA256', launch.digest(manifest)), ('CONTROLLER_SHA256', launch.digest(controller)), ('HOST_RUNNER_SHA256', launch.digest(runner))]:
            p = patch.object(launch, name, value); p.start(); self.addCleanup(p.stop)

    def args(self, profile):
        package = self.root / (profile + '.zip')
        with zipfile.ZipFile(package, 'w') as z:
            for name in ('team-runtime.tar.gz', 'agentic_loop.py', 'teamharness/course_worker.py'):
                z.writestr('src/' + name, b'fixture')
            z.writestr('src/campaign.json', json.dumps({'workflow': 'main', 'workers': 1, 'solve_seconds': 900 if profile == 'preflight' else 3600}))
        if profile == 'preflight':
            bundle = self.source / 'dev/teamharness-validation'
            for name in ('repository', 'package'):
                (bundle / name).mkdir(exist_ok=True)
                (bundle / name / 'fixture.py').write_text('# frozen\n')
            support = ['dev/fleet/fleet.py', 'dev/fleet/host/runner.py', 'dev/fleet/host/runner.original.py',
                       'dev/teamharness-validation/run_suite.py', 'dev/teamharness-validation/src-validation.zip',
                       'dev/course_origin.py', 'dev/openrouter_origin.py', 'dev/course-routes/openrouter-fleet.json']
            for relative in support:
                path = self.source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text('fixture')
            shutil.copy2(package, bundle / 'src-validation.zip')
            manifest = {'profile': 'teamharness-preflight', 'task_keys': ['32'], 'task_ids': ['instance-32'],
                'solver_limit_seconds': 900, 'outer_lane_limit_seconds': 1800, 'host_max_minutes': 60,
                'minimum_course_remaining_seconds': 2100, 'controller_sha256': launch.CONTROLLER_SHA256,
                'hard51_sha256': launch.HARD51_SHA256, 'original_runner_sha256': launch.HOST_RUNNER_SHA256,
                'wrapper_sha256': launch.digest(self.source/'dev/fleet/host/runner.py'),
                'package_zip_sha256': launch.digest(package), 'deployed_route_sha256': launch.digest(self.source/'dev/course_origin.py'),
                'support_hashes': {name: launch.digest(self.source/name) for name in support}}
            for name in ('repository', 'package'):
                manifest[name + '_inventory'] = {k: v['sha256'] for k, v in launch.inventory(bundle/name).items()}
            (bundle/'manifest.json').write_text(json.dumps(manifest))
        return argparse.Namespace(profile=profile, run=self.root/profile, fleet_source=self.source, package=package,
            max_minutes=60 if profile == 'preflight' else 120, reserve_usd=3 if profile == 'preflight' else 60,
            hourly_ceiling=1.5 if profile == 'preflight' else 28, sweep='test-' + profile,
            manifest='dev/tasks/hard51.json', evidence_root=self.root/'evidence', swe_v2=self.root/'assets')

    def test_reference_dependent_preparation_is_rejected_before_freeze(self):
        args = self.args('hard51')
        scripts = self.source / 'starter_code/evaluation_scripts'
        scripts.mkdir()
        (scripts / 'prepare_images.sh').write_text('cat solution/gold_patch.diff\n')
        with self.assertRaisesRegex(ValueError, 'Reference-dependent'):
            launch.prepare(args)
        self.assertFalse(args.run.exists())

    def test_full_sweep_preserves_all_51_hour_deadline_and_controller(self):
        args = self.args('hard51'); plan = launch.prepare(args)
        self.assertEqual(len(plan['task_ids']), 51)
        self.assertEqual(plan['solver_limit_seconds'], 3600)
        self.assertEqual(plan['outer_lane_limit_seconds'], 5400)
        cmd = plan['command']
        self.assertEqual(cmd[cmd.index('--hosts') + 1], '4')
        self.assertEqual(cmd[cmd.index('--lanes') + 1], '13')
        self.assertNotIn('--tasks', cmd)
        self.assertEqual(launch.validate_plan(args.run), plan)
        self.assertFalse((args.run/'launch.intent.json').exists())

    def test_full_profile_excludes_only_selected_known_passes(self):
        args = self.args('hard51')
        args.exclude_task_keys = [str(n) for n in range(19)]
        plan = launch.prepare(args)
        self.assertEqual(len(plan['task_ids']), 32)
        self.assertEqual(set(plan['task_ids']), {f'instance-{n}' for n in range(19, 51)})
        self.assertEqual(set(plan['command'][plan['command'].index('--tasks') + 1].split(',')), set(plan['task_ids']))
        self.assertEqual(launch.validate_plan(args.run), plan)

    def test_explicit_no_budget_limit_with_30_minute_task_window(self):
        args = self.args('hard51')
        args.no_budget_limit = True
        args.solve_seconds = 1800
        args.reserve_usd = args.hourly_ceiling = 0
        with zipfile.ZipFile(args.package) as z:
            members = {n: z.read(n) for n in z.namelist()}
        members['src/campaign.json'] = json.dumps({'workflow': 'main', 'workers': 1, 'solve_seconds': 1800})
        with zipfile.ZipFile(args.package, 'w') as z:
            for name, data in members.items():
                z.writestr(name, data)
        plan = launch.prepare(args)
        self.assertFalse(plan['budget_limit_enabled'])
        self.assertIsNone(plan['reserve_usd'])
        self.assertEqual(plan['solver_limit_seconds'], 1800)
        self.assertEqual(launch.validate_plan(args.run), plan)

    def test_sample2_reruns_only_interrupted_tasks(self):
        args = self.args('sample2')
        args.task_keys = ['18', '28']
        shutil.copy2(self.source/'dev/fleet/host/runner.original.py', self.source/'dev/fleet/host/runner.py')
        args.hourly_ceiling, args.reserve_usd = 7, 15
        plan = launch.prepare(args)
        cmd = plan['command']
        self.assertEqual(cmd[cmd.index('--lanes')+1], '2')
        self.assertEqual(cmd[cmd.index('--prep-jobs')+1], '2')
        self.assertEqual(set(plan['task_ids']), {'instance-18', 'instance-28'})
        self.assertEqual(launch.validate_plan(args.run), plan)

    def test_sample3_selects_only_failed_task_ids(self):
        args = self.args('sample3')
        args.task_keys = ['18', '25', '28']
        shutil.copy2(self.source/'dev/fleet/host/runner.original.py', self.source/'dev/fleet/host/runner.py')
        args.hourly_ceiling, args.reserve_usd = 7, 15
        plan = launch.prepare(args)
        cmd = plan['command']
        self.assertEqual(cmd[cmd.index('--lanes')+1], '3')
        self.assertEqual(set(plan['task_ids']), {'instance-'+k for k in args.task_keys})
        self.assertEqual(launch.validate_plan(args.run), plan)

    def test_sample_selects_only_five_exact_tasks_without_changing_full_manifest(self):
        args = self.args('sample5')
        args.task_keys = ['25', '18', '28', '42', '48']
        shutil.copy2(self.source/'dev/fleet/host/runner.original.py', self.source/'dev/fleet/host/runner.py')
        args.hourly_ceiling, args.reserve_usd = 7, 15
        plan = launch.prepare(args)
        cmd = plan['command']
        self.assertEqual(set(cmd[cmd.index('--tasks')+1].split(',')),
                         {'instance-'+k for k in args.task_keys})
        self.assertEqual(cmd[cmd.index('--hosts')+1], '1')
        self.assertEqual(cmd[cmd.index('--lanes')+1], '5')
        self.assertEqual(plan['solver_limit_seconds'], 3600)
        self.assertEqual(len(json.loads((args.run/'fleet-source/dev/tasks/hard51.json').read_text())), 51)
        self.assertEqual(launch.validate_plan(args.run), plan)

    def test_sample6_preserves_exact_selection_under_reordering(self):
        args = self.args('sample6')
        shutil.copy2(self.source/'dev/fleet/host/runner.original.py', self.source/'dev/fleet/host/runner.py')
        args.no_budget_limit = True
        keys = ['8', '19', '48', '16', '35', '41']
        for index, selection in enumerate((keys, list(reversed(keys)), keys[2:] + keys[:2])):
            with self.subTest(selection=selection):
                args.run = self.root / ('balanced-' + str(index))
                args.task_keys = selection
                plan = launch.prepare(args)
                cmd = plan['command']
                self.assertEqual(set(plan['task_ids']), {'instance-' + k for k in keys})
                self.assertEqual(cmd[cmd.index('--hosts') + 1], '1')
                self.assertEqual(cmd[cmd.index('--lanes') + 1], '6')
                self.assertEqual(cmd[cmd.index('--prep-jobs') + 1], '6')
                self.assertFalse(plan['budget_limit_enabled'])
                self.assertEqual(launch.validate_plan(args.run), plan)

    def test_sample_rejects_duplicate_or_unknown_selection_before_freeze(self):
        args = self.args('sample5')
        for keys in (['25'] * 5, ['25', '18', '28', '42', '999']):
            args.task_keys = keys
            with self.assertRaisesRegex(ValueError, 'distinct keys'):
                launch.prepare(args)
        self.assertFalse(args.run.exists())

    def test_preflight_freezes_one_exact_task_and_shorter_explicit_limits(self):
        args = self.args('preflight'); plan = launch.prepare(args)
        self.assertEqual(plan['task_ids'], ['instance-32'])
        self.assertEqual(plan['solver_limit_seconds'], 900)
        self.assertEqual(plan['outer_lane_limit_seconds'], 1800)
        cmd = plan['command']
        self.assertEqual(cmd[cmd.index('--instance-type') + 1], 'm7i.2xlarge')
        self.assertEqual(cmd[cmd.index('--tasks') + 1], 'instance-32')
        self.assertEqual(launch.validate_plan(args.run), plan)
        self.assertFalse((args.run/'reservation.json').exists())

    def test_changed_frozen_bytes_cannot_launch(self):
        args = self.args('preflight'); launch.prepare(args)
        (args.run/'fleet-source/dev/fleet/host/runner.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            launch.validate_plan(args.run)
        self.assertFalse((args.run/'launch.intent.json').exists())

    def test_preflight_requires_original_runner_and_validation_manifest(self):
        args = self.args('preflight')
        (self.source/'dev/fleet/host/runner.original.py').write_text('unreviewed replacement')
        with self.assertRaisesRegex(ValueError, 'original host runner differs'):
            launch.prepare(args)
        self.assertFalse(args.run.exists())

    def test_wrong_package_deadline_or_inadequate_envelope_is_rejected(self):
        args = self.args('preflight')
        with self.assertRaisesRegex(ValueError, '3600'):
            launch.check_package(args.package)
        with self.assertRaises(ValueError):
            launch.check_envelope(60, 2.25, 1.5, 'preflight')
        with self.assertRaises(ValueError):
            launch.check_envelope(60, 60, 28, 'hard51')

    def test_validation_manifest_is_bound_to_exact_package_and_runtime(self):
        args = self.args('preflight')
        (self.source/'dev/teamharness-validation/repository/fixture.py').write_text('changed runtime')
        with self.assertRaisesRegex(ValueError, 'input inventory mismatch'):
            launch.prepare(args)
        self.assertFalse(args.run.exists())


if __name__ == '__main__':
    unittest.main()
