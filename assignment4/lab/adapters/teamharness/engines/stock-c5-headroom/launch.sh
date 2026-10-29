#!/bin/bash
set -euo pipefail
exec a4-serve --flags-file "$A4_ENGINE_DIR/flags.txt"
