#!/usr/bin/env bash
# Editable Assignment adapter; other downloaded evaluation scripts stay unchanged.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export TASKS_JSON="${TASKS_JSON:-$SCRIPT_DIR/agent_task_input.json}"
# Resolve a caller-relative task path before changing directory.
TASKS_JSON="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$TASKS_JSON")"
export MADSLOOP_REPO="$(cd "${MADSLOOP_REPO:-$PWD}" && pwd)"
cd "$MADSLOOP_REPO"
python3 -m mads.runner "${1:?usage: run_task.sh TASK_INDEX}"
