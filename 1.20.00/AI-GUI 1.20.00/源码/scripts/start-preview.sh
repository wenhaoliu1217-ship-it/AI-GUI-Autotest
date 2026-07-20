#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-4173}"
HOST="${HOST:-0.0.0.0}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
PID_FILE="$RUNTIME_DIR/preview.pid"
LOG_FILE="$RUNTIME_DIR/preview.log"

mkdir -p "$RUNTIME_DIR"

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE")"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
      echo "Preview already running on http://localhost:$PORT/ (pid $old_pid)"
      exit 0
    fi
  fi
fi

cd "$ROOT_DIR"
npm run build

if command -v setsid >/dev/null 2>&1; then
  setsid "$ROOT_DIR/node_modules/.bin/vite" preview --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 < /dev/null &
else
  nohup "$ROOT_DIR/node_modules/.bin/vite" preview --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 < /dev/null &
fi
new_pid="$!"
echo "$new_pid" > "$PID_FILE"

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    echo "Preview running on http://localhost:$PORT/ (pid $new_pid)"
    echo "Log: $LOG_FILE"
    exit 0
  fi
  sleep 1
done

echo "Preview did not become ready on http://localhost:$PORT/." >&2
echo "Log: $LOG_FILE" >&2
exit 1
