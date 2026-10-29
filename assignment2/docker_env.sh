#!/usr/bin/env bash
# Harness dependencies only. Repository tests still use the task image's Python.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${MADS_AGENT_PY:-}"
if [[ -z "$PY" ]]; then
  for c in python3.13 python3.12 python3.11 python3.10 /opt/miniconda3/bin/python /opt/conda/bin/python python3; do
    if "$c" -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then PY="$c"; break; fi
  done
fi
if [[ -z "$PY" ]]; then
  echo 'docker_env.sh: select a Python >=3.10 via MADS_AGENT_PY (run_task bootstraps it)' >&2
  exit 1
fi
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}" PIP_BREAK_SYSTEM_PACKAGES=1
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  "$PY" -m ensurepip --upgrade >&2 || true
fi
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  BOOTSTRAP="$(mktemp)"
  trap 'rm -f "$BOOTSTRAP"' EXIT
  "$PY" -c 'import sys,urllib.request; urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py",sys.argv[1])' "$BOOTSTRAP"
  "$PY" "$BOOTSTRAP" --quiet >&2
fi
"$PY" -m pip install --quiet -r "$ROOT/requirements.txt" >&2
