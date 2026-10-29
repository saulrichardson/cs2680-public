#!/usr/bin/env python3
"""Bundle model_patch_<instance_id>.diff files into patches.json for the
SWE-bench Pro evaluation script (scaleapi/SWE-bench_Pro-os).

Pro's swe_bench_pro_eval.py expects a JSON array:
    [{"instance_id": ..., "model_patch": ..., "prefix": ...}, ...]

TASKS_JSON selects the task mapping, defaulting to agent_task_input.json next to
this script; the patches are read from — and
patches.json written to — the agent repo root: the current working
directory, or $MADSLOOP_REPO if set.
"""

import hashlib
import json
import os
import pathlib
import sys

script_dir = pathlib.Path(__file__).resolve().parent
repo_root = pathlib.Path(os.environ.get("MADSLOOP_REPO", os.getcwd())).resolve()
tasks = json.load(open(os.environ.get("TASKS_JSON", script_dir / "agent_task_input.json")))

patches = []
missing = []
for instance_id in tasks:
    receipt = pathlib.Path(os.environ.get('MADS_BATCH_DIR', repo_root / '.runs/latest-attempts')) / (instance_id + '.json')
    try:
        attempt = json.loads(receipt.read_text())
        if attempt['instance_id'] != instance_id or attempt['export_outcome']['status'] != 'completed':
            raise ValueError('Current attempt has no completed export')
        p = pathlib.Path(attempt['patch'])
        if hashlib.sha256(p.read_bytes()).hexdigest() != attempt['patch_sha256']:
            raise ValueError('Export hash mismatch')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        missing.append(instance_id)
        print(f'{instance_id}: no attested current export: {exc}', file=sys.stderr)
        continue
    patches.append({
        "instance_id": instance_id,
        "model_patch": p.read_text(),
        "prefix": "madsLoop",
    })

out = pathlib.Path(os.environ.get("MADS_OUTPUT_ROOT",repo_root)) / "patches.json"
json.dump(patches, open(out, "w"), indent=2)
print(f"wrote {out} ({len(patches)}/{len(tasks)} patches)")
if missing:
    print("missing patches for: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
