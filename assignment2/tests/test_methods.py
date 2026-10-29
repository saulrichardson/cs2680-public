"""Task-disjoint examples stay runnable without changing their source fixtures."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from mads.methods.run import consumer, relations, minimize


class WorkedMethodsTests(unittest.TestCase):
    def test_real_consumer_and_temporary_edit(self):
        root = Path(__file__).resolve().parents[1]/'mads/methods'
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('examples/*.py')}
        result = consumer()
        self.assertTrue(result['helper_passed'])
        self.assertEqual(result['public_before'], 300)
        self.assertEqual(result['public_after'], 270)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_independent_oracle_representations_and_revision(self):
        result = relations()
        self.assertGreater(result['prior_failure_cases'], 0)
        self.assertEqual(result['relation_checks'], result['generated_cases']*4)
        self.assertNotEqual(result['public_before'], result['public_expected'])
        self.assertEqual(result['public_after'], result['public_expected'])
        self.assertEqual(result['rejected_oracle']['observed'], 0)
        self.assertEqual(len(result['reduced_witness']), 2)

    def test_absolute_entrypoint_works_without_package_environment(self):
        script = Path(__file__).resolve().parents[1]/'mads/methods/run.py'
        result = subprocess.run([sys.executable, '-I', str(script), 'consumer'],
                                cwd=script.parent.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['public_after'], 270)

    def test_minimizer_preserves_predicate_and_does_not_mutate_input(self):
        values = [(-5, -1), (-3, -2), (8, 10)]
        before = list(values)
        fails = lambda xs: any(a < c <= d < b for a, b in xs for c, d in xs)
        result = minimize(values, fails)
        self.assertTrue(fails(result))
        self.assertEqual(values, before)
        self.assertEqual(len(result), 2)
