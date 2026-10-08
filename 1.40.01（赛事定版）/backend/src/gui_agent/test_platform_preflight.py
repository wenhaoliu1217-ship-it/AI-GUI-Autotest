import importlib.util
from pathlib import Path


def _load_preflight_module():
    path = Path(__file__).resolve().parents[3] / "tools" / "platform_preflight.py"
    spec = importlib.util.spec_from_file_location("gui_agent_platform_preflight", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_returns_structured_prerequisite_report() -> None:
    payload = _load_preflight_module().collect_preflight(require_docker=False)

    assert payload["status"] in {"ready", "not_ready"}
    assert payload["hostPlatform"]["system"] in {"windows", "linux", "macos", "unknown"}
    assert set(payload["checks"]) == {
        "pythonRuntime",
        "playwrightBrowser",
        "dockerCli",
        "dockerEngine",
        "runnerImage",
    }
    assert isinstance(payload["runnerAvailable"], bool)
