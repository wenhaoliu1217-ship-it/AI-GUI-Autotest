from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
import time
from contextlib import contextmanager

import gui_agent.execution.runner as runner_module
from gui_agent.execution.runner import (
    SCREENSHOT_TIMEOUT_MS,
    _capture_cdp_screenshot,
    _capture_screenshot,
)


def test_screenshot_retries_on_owner_thread_without_animation_fast_forward(monkeypatch, tmp_path) -> None:
    from threading import get_ident
    owner = get_ident()
    calls = []
    events = []

    @contextmanager
    def masks(*args):
        yield {"masked_count": 1}

    monkeypatch.setattr(runner_module, "screenshot_privacy_masks", masks)

    class Page:
        def screenshot(self, **options):
            assert get_ident() == owner
            calls.append(options)
            if len(calls) == 1:
                raise TimeoutError("animation settlement timed out")
            Path(options["path"]).write_bytes(b"png")

    class Artifacts:
        screenshot_mask_selectors = ()

        def screenshot_path(self, name):
            return tmp_path / f"{name}.png", f"screenshots/{name}.png"

        def event(self, kind, **payload):
            events.append((kind, payload))

    assert _capture_screenshot(Page(), Artifacts(), "retry") == "screenshots/retry.png"
    assert [call["animations"] for call in calls] == ["disabled", "allow"]
    assert all(call["timeout"] <= 5000 for call in calls)
    assert any(kind == "screenshot_retry_captured" for kind, _ in events)
    assert not any(kind == "screenshot_cdp_fallback" for kind, _ in events)


def test_screenshot_capture_uses_the_bounded_playwright_api() -> None:
    calls: list[dict] = []
    events: list[tuple[str, dict]] = []

    class FakeLocator:
        @staticmethod
        def evaluate(_expression: str, _argument: object, *, timeout: int) -> dict:
            return {"maskedCount": 0, "invalidSelectors": []}

    class FakeFrame:
        @staticmethod
        def locator(selector: str) -> FakeLocator:
            assert selector == "html"
            return FakeLocator()

    class FakePage:
        frames = [FakeFrame()]

        @staticmethod
        def screenshot(**options) -> None:
            calls.append(options)
            Path(options["path"]).write_bytes(b"png")

    with TemporaryDirectory() as directory:
        root = Path(directory)

        class FakeArtifacts:
            screenshot_mask_selectors: tuple[str, ...] = ()

            @staticmethod
            def screenshot_path(name: str) -> tuple[Path, str]:
                return root / f"{name}.png", f"screenshots/{name}.png"

            @staticmethod
            def event(kind: str, **payload) -> None:
                events.append((kind, payload))

        relative = _capture_screenshot(FakePage(), FakeArtifacts(), "bounded")

    assert relative == "screenshots/bounded.png"
    assert len(calls) == 1
    assert calls[0]["timeout"] == SCREENSHOT_TIMEOUT_MS
    assert calls[0]["full_page"] is False
    assert calls[0]["animations"] == "disabled"
    assert events[0][0] == "screenshot_privacy_applied"


def test_screenshot_capture_bounds_a_stuck_cdp_fallback(monkeypatch) -> None:
    """A wedged CDP compositor must not hold the Runner past its budget."""
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(runner_module, "_CDP_SCREENSHOT_LOCK", Lock())
    worker_started = Event()
    release = Event()
    finished = Event()

    class FakeLocator:
        @staticmethod
        def evaluate(_expression: str, _argument: object, *, timeout: int) -> dict:
            return {"maskedCount": 0, "invalidSelectors": []}

    class FakeFrame:
        @staticmethod
        def locator(_selector: str) -> FakeLocator:
            return FakeLocator()

    class StuckClient:
        def send(self, *_args, **_kwargs) -> dict:
            # Simulate Chromium not answering until the host-side timeout has
            # elapsed, then release it so the test can cleanly unlock the
            # process-local guard.
            worker_started.set()
            assert release.wait(timeout=2.0)
            return {"data": ""}

        def detach(self) -> None:
            finished.set()

    class FakeContext:
        @staticmethod
        def new_cdp_session(_page) -> StuckClient:
            return StuckClient()

    class FakePage:
        frames = [FakeFrame()]
        context = FakeContext()

        @staticmethod
        def screenshot(**_options) -> None:
            raise RuntimeError("Playwright screenshot unavailable")

    with TemporaryDirectory() as directory:
        root = Path(directory)

        class FakeArtifacts:
            screenshot_mask_selectors: tuple[str, ...] = ()

            @staticmethod
            def screenshot_path(name: str) -> tuple[Path, str]:
                return root / f"{name}.png", f"screenshots/{name}.png"

            @staticmethod
            def event(kind: str, **payload) -> None:
                events.append((kind, payload))

        monkeypatch.setattr(runner_module, "CDP_SCREENSHOT_TIMEOUT_MS", 100)
        started_at = time.perf_counter()
        relative = _capture_screenshot(FakePage(), FakeArtifacts(), "stuck-cdp")
        elapsed = time.perf_counter() - started_at

    assert relative is None
    assert elapsed < 1.0
    assert worker_started.is_set()
    fallback = next(payload for kind, payload in events if kind == "screenshot_cdp_fallback")
    assert fallback["status"] == "skipped"
    assert fallback["reason"] == "timeout"
    assert any(kind == "screenshot_capture_failed" for kind, _ in events)
    release.set()
    assert finished.wait(timeout=1.0)


def test_screenshot_cdp_fallback_returns_busy_while_previous_call_is_pending(monkeypatch) -> None:
    """A second fallback must not create another potentially stuck CDP worker."""
    monkeypatch.setattr(runner_module, "_CDP_SCREENSHOT_LOCK", Lock())
    monkeypatch.setattr(runner_module, "CDP_SCREENSHOT_TIMEOUT_MS", 2_000)
    started = Event()
    release = Event()
    cdp_sessions = 0
    events: list[tuple[str, dict]] = []

    class PendingClient:
        def send(self, *_args, **_kwargs) -> dict:
            started.set()
            assert release.wait(timeout=2.0)
            return {"data": ""}

        @staticmethod
        def detach() -> None:
            return None

    class FakeContext:
        def new_cdp_session(self, _page) -> PendingClient:
            nonlocal cdp_sessions
            cdp_sessions += 1
            return PendingClient()

    class FakeLocator:
        @staticmethod
        def evaluate(_expression: str, _argument: object, *, timeout: int) -> dict:
            return {"maskedCount": 0, "invalidSelectors": []}

    class FakeFrame:
        @staticmethod
        def locator(_selector: str) -> FakeLocator:
            return FakeLocator()

    class FakePage:
        context = FakeContext()
        frames = [FakeFrame()]

        @staticmethod
        def screenshot(**_options) -> None:
            raise RuntimeError("Playwright screenshot unavailable")

    with TemporaryDirectory() as directory:
        root = Path(directory)

        class FakeArtifacts:
            screenshot_mask_selectors: tuple[str, ...] = ()

            @staticmethod
            def screenshot_path(name: str) -> tuple[Path, str]:
                return root / f"{name}.png", f"screenshots/{name}.png"

            @staticmethod
            def event(kind: str, **payload) -> None:
                events.append((kind, payload))

        first_result: dict[str, tuple[bool, str]] = {}
        first_path = root / "first.png"
        first_thread = Thread(
            target=lambda: first_result.setdefault(
                "value", _capture_cdp_screenshot(FakePage(), first_path)
            ),
            daemon=True,
        )
        first_thread.start()
        assert started.wait(timeout=1.0)

        started_at = time.perf_counter()
        second = _capture_screenshot(FakePage(), FakeArtifacts(), "busy")
        elapsed = time.perf_counter() - started_at

        # Let the first worker finish so the test does not leave a live daemon
        # holding the guard after teardown.
        release.set()
        first_thread.join(timeout=1.0)

    assert second is None
    assert elapsed < 0.5
    assert cdp_sessions == 1
    fallback = next(payload for kind, payload in events if kind == "screenshot_cdp_fallback")
    assert fallback == {
        "screenshot": "screenshots/busy.png",
        "status": "skipped",
        "reason": "busy",
    }
    assert first_result["value"] == (False, "empty_response")
