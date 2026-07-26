from contextlib import contextmanager

from gui_agent.execution import runner


class _Artifacts:
    screenshot_mask_selectors = ()

    def __init__(self, root):
        self.root = root
        self.events = []

    def screenshot_path(self, name):
        path = self.root / f"{name}.png"
        return path, f"screenshots/{name}.png"

    def event(self, kind, **payload):
        self.events.append((kind, payload))


@contextmanager
def _privacy_masks(_page, _selectors):
    yield {"masked_count": 0, "invalid_selectors": [], "selector_count": 5}


def test_capture_screenshot_uses_bounded_playwright_capture(tmp_path, monkeypatch):
    class Page:
        def screenshot(self, **kwargs):
            assert kwargs["timeout"] == 5_000
            assert kwargs["full_page"] is False
            tmp_path.joinpath("before.png").write_bytes(b"png")

    artifacts = _Artifacts(tmp_path)
    monkeypatch.setattr(runner, "screenshot_privacy_masks", _privacy_masks)

    assert runner._capture_screenshot(Page(), artifacts, "before") == "screenshots/before.png"
    assert artifacts.events[0][0] == "screenshot_privacy_applied"


def test_capture_screenshot_timeout_is_non_fatal(tmp_path, monkeypatch):
    class Page:
        def screenshot(self, **_kwargs):
            raise RuntimeError("screenshot timed out")

    artifacts = _Artifacts(tmp_path)
    monkeypatch.setattr(runner, "screenshot_privacy_masks", _privacy_masks)

    assert runner._capture_screenshot(Page(), artifacts, "before") is None
    assert artifacts.events == [
        (
            "screenshot_capture_skipped",
            {"screenshot": "screenshots/before.png", "timeout_ms": 5_000},
        )
    ]
