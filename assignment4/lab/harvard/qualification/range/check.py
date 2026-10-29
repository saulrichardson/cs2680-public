"""External native qualification check; never an original course benchmark grade."""
import importlib.util
import json
from pathlib import Path
import sys

path = Path(sys.argv[1]) / 'ranges.py'
spec = importlib.util.spec_from_file_location('candidate_ranges', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
cases = [(1, 3, 3), (2, 2, 1), (-4, -1, 4), (3, 1, 0), (0, 0, 1)]
failed = sum(module.inclusive_count(lo, hi) != expected for lo, hi, expected in cases)
print(json.dumps({'tests_total': len(cases), 'tests_failed': failed}))
raise SystemExit(1 if failed else 0)
