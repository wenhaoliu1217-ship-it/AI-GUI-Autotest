#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_bin=${GUI_AGENT_PYTHON:-}
detached=0
skip_browser=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --detached) detached=1 ;;
    --skip-browser) skip_browser=1 ;;
    --port)
      shift
      [ "$#" -gt 0 ] || { echo "--port requires a value" >&2; exit 2; }
      GUI_API_PORT=$1
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
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

export GUI_API_PORT=${GUI_API_PORT:-8080}
export GUI_SKIP_BROWSER=${GUI_SKIP_BROWSER:-$skip_browser}

mkdir -p "$repo_root/.local"
if [ "$detached" -eq 1 ]; then
  stdout_log="$repo_root/.local/server-stdout.log"
  stderr_log="$repo_root/.local/server-stderr.log"
  nohup "$python_bin" "$repo_root/tools/run_dev_server.py" >"$stdout_log" 2>"$stderr_log" < /dev/null &
  server_pid=$!
  printf '{"pid":%s,"port":%s,"repoRoot":"%s","command":"python tools/run_dev_server.py"}\n' \
    "$server_pid" "$GUI_API_PORT" "$repo_root" > "$repo_root/.local/server.pid"
  echo "Started AI-GUI Autotest on http://127.0.0.1:$GUI_API_PORT/ (PID $server_pid)"
  exit 0
fi

exec "$python_bin" "$repo_root/tools/run_dev_server.py"
