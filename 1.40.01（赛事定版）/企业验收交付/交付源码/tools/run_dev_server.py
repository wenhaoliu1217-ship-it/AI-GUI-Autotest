from __future__ import annotations

import os
import pathlib
import json
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOT = (REPO_ROOT / "backend" / "src").resolve()
sys.path.insert(0, str(SOURCE_ROOT))


def _configure_project_defaults() -> None:
    """Keep every platform anchored to this checkout, not the caller's cwd."""
    local_root = REPO_ROOT / ".local"
    local_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("GUI_STATIC_DIR", str(REPO_ROOT / "frontend-dist"))
    os.environ.setdefault("GUI_AGENT_ARTIFACTS", str(local_root / "artifacts"))
    os.environ.setdefault("GUI_AGENT_DATA", str(local_root / "data"))
    os.environ.setdefault("GUI_API_HOST", "127.0.0.1")
    os.environ.setdefault("GUI_RUNNER_MODE", "container")
    bundled_browsers = REPO_ROOT / "runtime" / "ms-playwright"
    if bundled_browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled_browsers))
    os.chdir(REPO_ROOT)


def _check_runtime(frontend_manifest: pathlib.Path | None = None) -> int:
    import fastapi  # noqa: F401
    import gui_agent
    import playwright  # noqa: F401
    import uvicorn  # noqa: F401
    from gui_agent.version import API_CONTRACT_VERSION

    module_path = pathlib.Path(gui_agent.__file__).resolve()
    if not module_path.is_relative_to(SOURCE_ROOT):
        return 2
    if frontend_manifest is not None:
        try:
            manifest = json.loads(frontend_manifest.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return 3
        if manifest.get("apiContractVersion") != API_CONTRACT_VERSION:
            return 4
    return 0


def main() -> int:
    _configure_project_defaults()
    if "--check" in sys.argv[1:]:
        manifest = None
        if "--frontend-manifest" in sys.argv[1:]:
            index = sys.argv.index("--frontend-manifest")
            if index + 1 >= len(sys.argv):
                return 3
            manifest = pathlib.Path(sys.argv[index + 1]).resolve()
        return _check_runtime(manifest)

    import uvicorn

    host = os.getenv("GUI_API_HOST", "127.0.0.1")
    port = int(os.getenv("GUI_API_PORT", "8080"))
    uvicorn.run("gui_agent.api.server:app", host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
