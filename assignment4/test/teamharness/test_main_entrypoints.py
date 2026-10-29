"""Actual CLI/package entry points retain one maintained investigation."""
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from lab.adapters.teamharness.build import build
from src.teamharness.main import Investigation
from src.teamharness.candidates import capture
from src.teamharness.baseline import freeze
from types import SimpleNamespace
import test_candidates as candidate_fixture

ROOT = Path(__file__).resolve().parents[2]


class MainEntrypoints(unittest.TestCase):
    def test_package_contains_only_main_runtime_and_imports_it(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'package'
            build(package)
            config = json.loads((package / 'src/campaign.json').read_text())
            self.assertEqual(config['workflow'], 'main')
            with tarfile.open(package / 'src/team-runtime.tar.gz') as archive:
                names = set(archive.getnames())
            self.assertIn('src/teamharness/candidates.py', names)
            self.assertIn('src/teamharness/main.py', names)
            result = subprocess.run([sys.executable, '-B', '-m', 'src.teamharness', 'run', '--help'],
                                    cwd=package, capture_output=True, text=True, check=True)
            self.assertTrue(result.stdout.strip())
            # Historical campaigns fail before task/archive or provider effects.
            code = ('from src.agentic_loop import Agent\n'
                    'try: Agent(None).solve({}, {"workflow":"staged"})\n'
                    'except ValueError as e: print(e)\n'
                    'else: raise AssertionError("Historical workflow was executed")\n')
            result = subprocess.run([sys.executable, '-B', '-c', code], cwd=package,
                                    capture_output=True, text=True, check=True)
            self.assertIn('frozen runtime', result.stdout)
            self.assertFalse((package / 'madsServe_logs').exists())

    def test_historical_workflow_rejected_before_manager_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agents = root/'agents'
            (agents/'agents').mkdir(parents=True)
            (agents/'guides').mkdir()
            for path in (agents/'manager.json', agents/'role-prompts.json', agents/'guides/manifest.json'):
                path.write_text('{}')
            (root/'workflow.json').write_text(json.dumps({'workflow':'staged'}))
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            result = subprocess.run([sys.executable, '-B', '-m', 'src.teamharness', 'resume', '--output', str(root)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('frozen runtime', result.stderr)
            self.assertEqual(before, {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_main_restores_original_baseline_and_preserves_drift(self):
        fixture = candidate_fixture.CandidateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        frozen = {**fixture.frozen,
                  'archive': freeze(fixture.root/'baseline', fixture.root/'baseline.tar')}
        investigation = Investigation(SimpleNamespace(workflow=None), fixture.root/'investigation')
        investigation.report['frozen'] = frozen
        original = fixture.root/'baseline/value.py'
        original.write_text('external drift\n')
        restored = investigation.starting_workspace()
        self.assertNotEqual(restored, fixture.root/'baseline')
        self.assertEqual(original.read_text(), 'external drift\n')
        self.assertEqual((restored/'value.py').read_text(), 'value = 2\n')
        self.assertEqual(capture(restored, fixture.root/'restored.patch', frozen['revision'])['bytes'], 0)
        self.assertEqual(investigation.starting_workspace(), restored)
