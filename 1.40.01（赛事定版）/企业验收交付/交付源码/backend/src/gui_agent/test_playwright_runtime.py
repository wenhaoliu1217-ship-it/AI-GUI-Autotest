import pytest

from gui_agent import playwright_runtime as runtime


def test_driver_startup_attribute_error_is_actionable(monkeypatch) -> None:
    class BrokenManager:
        def __enter__(self):
            raise AttributeError("'PlaywrightContextManager' object has no attribute '_playwright'")

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(runtime, "sync_playwright", lambda: BrokenManager())

    with pytest.raises(runtime.BrowserRuntimeUnavailable) as error:
        with runtime.playwright_runtime():
            raise AssertionError("unreachable")
    assert "PLAYWRIGHT_BROWSERS_PATH" in str(error.value)


def test_browser_aliases_are_normalized() -> None:
    assert runtime.normalize_browser_name("chrome") == "chromium"
    assert runtime.normalize_browser_name("msedge") == "edge"


def test_unsupported_browser_is_rejected() -> None:
    with pytest.raises(runtime.BrowserRuntimeUnavailable, match="不支持的浏览器"):
        runtime.normalize_browser_name("opera")


def test_edge_uses_chromium_channel() -> None:
    class FakeBrowserType:
        def __init__(self):
            self.kwargs = None

        def launch(self, **kwargs):
            self.kwargs = kwargs
            return "browser"

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeBrowserType()
            self.firefox = FakeBrowserType()
            self.webkit = FakeBrowserType()

    playwright = FakePlaywright()
    assert runtime.launch_browser(playwright, browser_name="edge") == "browser"
    assert playwright.chromium.kwargs == {"headless": True, "slow_mo": 0, "channel": "msedge"}
