from playwright.sync_api import Error as PlaywrightError

from gui_agent.onboarding.scanner import (
    _apply_login_route_signal,
    _goto_with_retries,
    _wait_for_page_readiness,
)


def test_scan_recognizes_login_route_before_form_controls_finish_loading() -> None:
    facts = {
        "title": "Sign In | Cesium ion",
        "finalUrl": "https://ion.cesium.com/signin/",
        "auth": {"passwordInputs": 0, "loginDetected": False},
    }

    normalized = _apply_login_route_signal(facts)

    assert normalized["auth"]["loginDetected"] is True


def test_scan_retries_transient_connection_closure() -> None:
    calls: list[tuple[str, object]] = []

    class Page:
        attempts = 0

        def goto(self, url: str, *, wait_until: str):
            self.attempts += 1
            calls.append(("goto", (url, wait_until)))
            if self.attempts < 3:
                raise PlaywrightError("Page.goto: net::ERR_CONNECTION_CLOSED")
            return "response"

        @staticmethod
        def wait_for_timeout(timeout: int) -> None:
            calls.append(("wait", timeout))

    assert _goto_with_retries(Page(), "https://example.test/") == "response"
    assert calls == [
        ("goto", ("https://example.test/", "commit")),
        ("wait", 1_000),
        ("goto", ("https://example.test/", "commit")),
        ("wait", 2_000),
        ("goto", ("https://example.test/", "commit")),
    ]


def test_scan_waits_for_a_real_page_surface() -> None:
    calls = {}

    class Page:
        @staticmethod
        def wait_for_function(script: str, *, timeout: int) -> None:
            calls["script"] = script
            calls["timeout"] = timeout

    _wait_for_page_readiness(Page(), 30_000)

    assert calls["timeout"] == 30_000
    assert "page-loading-placeholder" in calls["script"]


def test_scan_readiness_timeout_is_reported_by_scan_facts_instead() -> None:
    class SlowPage:
        @staticmethod
        def wait_for_function(_script: str, *, timeout: int) -> None:
            raise PlaywrightError(f"timeout after {timeout}")

    _wait_for_page_readiness(SlowPage(), 5_000)
