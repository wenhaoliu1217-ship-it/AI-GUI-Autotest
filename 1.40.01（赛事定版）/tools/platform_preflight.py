"""Report whether this host can run the real isolated Runner.

This is intentionally a diagnostic command. It does not start Docker Desktop,
change the active project, or claim that an untested OS is certified.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "backend" / "src"
sys.path.insert(0, str(SOURCE_ROOT))


def _module_ready(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def collect_preflight(*, require_docker: bool = False) -> dict[str, object]:
    from gui_agent.execution.container_runtime import (
        DEFAULT_RUNNER_IMAGE,
        docker_engine_ready,
        docker_image_available,
        resolve_docker_cli,
    )
    from gui_agent.platform_support import (
        detect_host_platform,
        playwright_browser_ready,
        resolve_playwright_browser_root,
    )
    from gui_agent.playwright_runtime import BrowserRuntimeUnavailable, normalize_browser_name

    host = detect_host_platform()
    browser_root = resolve_playwright_browser_root(ROOT / "runtime" / "ms-playwright")
    requested_browser = os.getenv("GUI_BROWSER", "chromium")
    try:
        browser_name = normalize_browser_name(requested_browser)
        browser_name_error = None
    except BrowserRuntimeUnavailable as exc:
        browser_name = requested_browser.strip().lower() or "chromium"
        browser_name_error = str(exc)
    docker_cli = resolve_docker_cli()
    docker_ready = docker_engine_ready(docker_cli, timeout=3.0) if docker_cli else False
    image_ready = (
        docker_image_available(DEFAULT_RUNNER_IMAGE, docker_cli, timeout=3.0)
        if docker_ready
        else False
    )
    python_ready = all(_module_ready(name) for name in ("fastapi", "playwright", "uvicorn"))
    browser_installed = playwright_browser_ready(browser_root, browser_name)
    browser_ready = browser_installed and (os.getenv("GUI_RUNNER_MODE", "container") != "container" or browser_name == "chromium")
    checks = {
        "pythonRuntime": python_ready,
        "playwrightBrowser": browser_ready,
        "dockerCli": bool(docker_cli),
        "dockerEngine": docker_ready,
        "runnerImage": image_ready,
    }
    runner_ready = python_ready and browser_ready and docker_ready and image_ready
    required_ready = runner_ready if require_docker else python_ready and browser_ready
    return {
        "status": "ready" if required_ready else "not_ready",
        "hostPlatform": host.to_dict(),
        "runnerMode": os.getenv("GUI_RUNNER_MODE", "container"),
        "runnerImage": DEFAULT_RUNNER_IMAGE,
        "browserName": browser_name,
        "browserInstalled": browser_installed,
        "browserReason": browser_name_error or ("runner_image_only_certifies_chromium" if not browser_ready and browser_name != "chromium" else "ready" if browser_installed else "missing"),
        "dockerCliPath": docker_cli,
        "playwrightBrowsersPath": str(browser_root) if browser_root else None,
        "checks": checks,
        "runnerAvailable": runner_ready,
        "requireDocker": require_docker,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check AI-GUI Autotest platform prerequisites")
    parser.add_argument("--require-docker", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        payload = collect_preflight(require_docker=args.require_docker)
    except Exception as exc:
        payload = {"status": "error", "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if payload.get("status") == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
