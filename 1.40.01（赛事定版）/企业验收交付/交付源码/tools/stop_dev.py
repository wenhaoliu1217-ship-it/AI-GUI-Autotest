"""Stop only the development server recorded by this source tree."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / ".local" / "server.pid"


def _command_line(pid: int) -> str:
    proc_file = Path("/proc") / str(pid) / "cmdline"
    try:
        if proc_file.is_file():
            return proc_file.read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def main() -> int:
    if not PID_FILE.is_file():
        print("No development server PID record found.")
        return 0
    try:
        record = json.loads(PID_FILE.read_text(encoding="utf-8"))
        pid = int(record["pid"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("The development server PID record is invalid; no process was stopped.", file=sys.stderr)
        return 2

    command = _command_line(pid)
    if "run_dev_server.py" not in command or str(ROOT) not in command:
        print("The recorded PID does not match this development service; no process was stopped.", file=sys.stderr)
        return 2

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        return 0
    except OSError as exc:
        print(f"Unable to stop development server: {exc}", file=sys.stderr)
        return 2

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            PID_FILE.unlink(missing_ok=True)
            return 0
        except OSError:
            break
        time.sleep(0.1)
    print("The development server did not stop within 5 seconds.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
