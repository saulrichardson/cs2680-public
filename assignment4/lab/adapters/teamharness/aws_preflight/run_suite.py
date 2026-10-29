"""Run the native stdlib suite, retaining a machine-readable actual result."""
import argparse
import json
from pathlib import Path
import sys
import time
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(Path.cwd()))
started = time.time()
suite = unittest.defaultTestLoader.discover('test/teamharness', pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
receipt = {'tests_run': result.testsRun, 'passed': result.wasSuccessful(),
           'failures': [{'test': str(test), 'traceback': text} for test, text in result.failures],
           'errors': [{'test': str(test), 'traceback': text} for test, text in result.errors],
           'skipped': [{'test': str(test), 'reason': text} for test, text in result.skipped],
           'started_at': started, 'finished_at': time.time(), 'platform': sys.platform,
           'python': sys.version, 'executable': sys.executable}
args.output.write_text(json.dumps(receipt, indent=2) + '\n')
raise SystemExit(0 if receipt['passed'] and not receipt['skipped'] and receipt['tests_run'] else 1)
