"""Preserve completed preflight outputs before the original course task launch."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def archive_preflight(course, destination):
    course, destination = Path(course), Path(destination)
    stages = json.loads((course.parent.parent / 'task-stages.json').read_text())
    if [(s['stage'], s['exit_code']) for s in stages] != [('prepare', 0), ('preflight', 0)]:
        raise ValueError('Expected this fresh execution copy to have completed preparation and preflight')
    destination.mkdir()  # One handoff; existing outputs are never overwritten.
    moved = []
    for path in sorted(course.iterdir()):
        if path.name in ('.tasks', 'run_logs', 'madsServe_logs', 'predictions.json',
                         'pro_eval', 'run_all_results.md') or path.name.startswith('model_patch_'):
            path.rename(destination / path.name)
            moved.append(path.name)
    (destination / 'handoff.json').write_text(json.dumps({'preserved': moved,
        'scope': 'Completed preflight only; the original task controller has not started.'}, indent=2) + '\n')
    return moved


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, required=True)
    args = parser.parse_args()
    course = Path.cwd()
    archive_preflight(course, course.parent / 'preflight-output')
    raise SystemExit(subprocess.call([sys.executable, 'evaluation_scripts/run_all.py', '--limit', str(args.limit)]))
