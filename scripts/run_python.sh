#!/usr/bin/env bash
# Shared runtime for Ubuntu CLI, examples and your own Python programs.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    printf '%s\n' 'Prepare Ubuntu first: python3 scripts/bootstrap_ubuntu.py' >&2
    exit 1
fi
export LD_LIBRARY_PATH="$PROJECT_DIR/.venv/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONUTF8=1
cd -- "$PROJECT_DIR"
exec "$PROJECT_DIR/.venv/bin/python" "$@"
