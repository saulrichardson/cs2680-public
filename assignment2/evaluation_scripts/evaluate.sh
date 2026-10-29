#!/bin/bash
# Evaluate patches.json with the official SWE-bench Pro evaluation script
# (scaleapi/SWE-bench_Pro-os, local-Docker mode). Runs on the HOST; it drives
# your local Docker daemon.
#
# Run from your agent repo root (where patches.json was written), or set
# MADSLOOP_REPO to point at it. The Scale repo is cloned on first use into
# $REPO_ROOT/SWE-bench_Pro-os (override with $SWEBENCH_PRO_OS).
#
# Outputs: $REPO_ROOT/pro_eval/eval_results.json  ({instance_id: true/false})
#          $REPO_ROOT/pro_eval/<instance_id>/     (per-instance logs)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_JSON="${TASKS_JSON:-$SCRIPT_DIR/agent_task_input.json}"
TEST_JSON="${TESTS_JSON:-$SCRIPT_DIR/task_test.json}"
REPO_ROOT="$(cd "${MADSLOOP_REPO:-$PWD}" && pwd)"
EVAL_REPO="${SWEBENCH_PRO_OS:-$REPO_ROOT/SWE-bench_Pro-os}"
OUTPUT_ROOT="${MADS_OUTPUT_ROOT:-$REPO_ROOT}"
OUTPUT_DIR="$OUTPUT_ROOT/pro_eval"

if [[ ! -f "$OUTPUT_ROOT/patches.json" ]]; then
  echo "error: no patches.json in $REPO_ROOT — run make_patches.py first" >&2
  exit 1
fi

# Validate the selected tasks and build evaluator input before network/setup work.
# TESTS_JSON is separate evaluator metadata; it is never a solver input.
mkdir -p "$OUTPUT_DIR"
python3 - "$AGENT_JSON" "$TEST_JSON" "$OUTPUT_DIR/raw_samples.csv" <<'EOF'
import csv, json, sys
agent = json.load(open(sys.argv[1]))
test = json.load(open(sys.argv[2]))
missing = sorted(set(agent) - set(test))
if missing:
    raise SystemExit('Missing evaluation metadata for selected tasks: ' + ', '.join(missing)
                     + '. Set TESTS_JSON to matching metadata; solver patches remain available.')
cols = ["instance_id", "repo", "base_commit", "before_repo_set_cmd",
        "selected_test_files_to_run", "fail_to_pass", "pass_to_pass"]
with open(sys.argv[3], "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for iid, a in agent.items():
        t = {**a, **test[iid]}
        w.writerow({c: t[c] for c in cols})
print(f"wrote {sys.argv[3]} ({len(agent)} instances)")
EOF

# The official evaluator and its dependencies remain unchanged.
if [[ ! -f "$EVAL_REPO/swe_bench_pro_eval.py" ]]; then
  echo "== cloning scaleapi/SWE-bench_Pro-os into $EVAL_REPO" >&2
  git clone --depth 1 https://github.com/scaleapi/SWE-bench_Pro-os.git "$EVAL_REPO"
fi
python3 -m pip install -q pandas tqdm docker

# 4. Run the official evaluation. Must run from the eval repo root: it reads
#    run_scripts/ and dockerfiles/ by relative path and imports helper_code.
cd "$EVAL_REPO"
python3 swe_bench_pro_eval.py \
  --raw_sample_path="$OUTPUT_DIR/raw_samples.csv" \
  --patch_path="$OUTPUT_ROOT/patches.json" \
  --output_dir="$OUTPUT_DIR" \
  --scripts_dir=run_scripts \
  --dockerhub_username=jefzda \
  --use_local_docker \
  ${DOCKER_PLATFORM:+--docker_platform="$DOCKER_PLATFORM"} \
  --num_workers="${NUM_WORKERS:-2}"

echo
echo "== results ($OUTPUT_DIR/eval_results.json):"
python3 - "$OUTPUT_DIR/eval_results.json" <<'PYRESULTS'
import json, sys
r = json.load(open(sys.argv[1]))
for k, v in r.items():
    print(('RESOLVED  ' if v else 'unresolved'), k)
print(f'{sum(r.values())}/{len(r)} resolved')
PYRESULTS
