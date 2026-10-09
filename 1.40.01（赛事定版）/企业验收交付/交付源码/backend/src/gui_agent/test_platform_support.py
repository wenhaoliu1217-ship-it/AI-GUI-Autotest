from pathlib import Path

from gui_agent.platform_support import (
    detect_host_platform,
    playwright_chromium_ready,
    resolve_playwright_browser_root,
    subprocess_creationflags,
)


def test_host_platform_payload_is_stable_and_serializable() -> None:
    payload = detect_host_platform().to_dict()

    assert payload["system"] in {"windows", "linux", "macos", "unknown"}
    assert payload["machine"]
    assert payload["python"]
    assert isinstance(payload["isWindows"], bool)
    assert isinstance(payload["isUnix"], bool)


def test_empty_playwright_override_does_not_hide_project_runtime(tmp_path, monkeypatch) -> None:
    browser_root = tmp_path / "ms-playwright"
    relative = {
        "windows": Path("chrome-win/chrome.exe"),
        "linux": Path("chrome-linux/chrome"),
        "macos": Path("chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
    }.get(detect_host_platform().system)
    if relative is None:
        return
    executable = browser_root / "chromium-1" / relative
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"browser")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    resolved = resolve_playwright_browser_root(browser_root)

    assert resolved == browser_root.resolve()
    assert playwright_chromium_ready(resolved) is True


def test_subprocess_creation_flags_are_an_integer() -> None:
    assert isinstance(subprocess_creationflags(), int)
