#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_bin=${GUI_AGENT_PYTHON:-}
if [ -z "$python_bin" ]; then
  if command -v python3 >/dev/null 2>&1; then
    python_bin=$(command -v python3)
  elif command -v python >/dev/null 2>&1; then
    python_bin=$(command -v python)
  else
    echo "Python 3 is required. Set GUI_AGENT_PYTHON to a Python executable." >&2
    exit 1
  fi
fi

exec "$python_bin" "$repo_root/tools/stop_dev.py"
