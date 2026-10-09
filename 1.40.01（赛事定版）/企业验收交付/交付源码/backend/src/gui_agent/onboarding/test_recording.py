from gui_agent.onboarding.recording import (
    LOGIN_NAVIGATION_COMMIT_TIMEOUT_MS,
    LoginRecordingManager,
    RecordingSession,
    _open_login_page,
    _open_user_controlled_login_page,
    _safe_network_reason,
    _safe_request_target,
    _safe_event_url,
    _show_login_window,
    _validate_recording_completion,
    _wait_for_signal,
    launch_visible_login_browser,
)
from gui_agent.security.policy import temporary_login_navigation_hosts


def test_cesium_recording_uses_the_same_temporary_oauth_hosts_as_runtime_login() -> None:
    assert temporary_login_navigation_hosts("https://ion.cesium.com") == (
        "accounts.google.com",
        "accounts.youtube.com",
        "github.com",
    )
    assert temporary_login_navigation_hosts("https://example.test") == ()


def test_login_recording_prefers_the_launcher_managed_edge(monkeypatch) -> None:
    calls = []

    class FakeChromium:
        @staticmethod
        def connect_over_cdp(url: str):
            calls.append(url)
            return "shared-browser"

    class FakePlaywright:
        chromium = FakeChromium()

    monkeypatch.setenv("GUI_BROWSER_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setenv("GUI_BROWSER_NAME", "Microsoft Edge")

    browser, name, shared = launch_visible_login_browser(FakePlaywright())

    assert browser == "shared-browser"
    assert name == "Microsoft Edge（与 GUI 同一窗口）"
    assert shared is True
    assert calls == ["http://127.0.0.1:9222"]


def test_user_controlled_login_does_not_install_a_context_route(monkeypatch) -> None:
    calls = []

    class FakePage:
        url = "about:blank"

        @staticmethod
        def is_closed() -> bool:
            return False

        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            calls.append((url, wait_until, timeout))

    page = FakePage()

    class FakeContext:
        @staticmethod
        def new_page():
            return page

        @staticmethod
        def route(*_args, **_kwargs):
            raise AssertionError("shared user-controlled login must not install a route")

    class FakeProject:
        base_url = "https://ion.cesium.com"
        allowed_hosts = ["ion.cesium.com"]
        allow_private_network = False

    class FakePolicy:
        @staticmethod
        def check_url(url: str) -> None:
            calls.append(("checked", url))

        @staticmethod
        def clear_rejection() -> None:
            pass

    monkeypatch.setattr("gui_agent.onboarding.recording._show_login_window", lambda *_args: None)

    opened = _open_user_controlled_login_page(FakeContext(), FakeProject(), FakePolicy())

    assert opened is page
    assert calls == [
        ("checked", "https://ion.cesium.com"),
        ("https://ion.cesium.com", "commit", LOGIN_NAVIGATION_COMMIT_TIMEOUT_MS),
    ]


def test_wait_for_signal_pumps_playwright_until_finalize() -> None:
    session = RecordingSession(id="recording-test", project_id="project-test")

    class FakePage:
        calls = 0

        def wait_for_timeout(self, _timeout_ms: int) -> None:
            self.calls += 1
            if self.calls == 2:
                session.finalize.set()

        @staticmethod
        def is_closed() -> bool:
            return False

    page = FakePage()

    assert _wait_for_signal(session, 1, page) == "finalize"
    assert page.calls == 2


def test_wait_for_signal_honors_cancel_before_pumping_page() -> None:
    session = RecordingSession(id="recording-test", project_id="project-test")
    session.cancel.set()

    class UnexpectedPage:
        @staticmethod
        def wait_for_timeout(_timeout_ms: int) -> None:
            raise AssertionError("cancel should be handled before pumping Playwright")

    assert _wait_for_signal(session, 1, UnexpectedPage()) == "cancel"


def test_wait_for_signal_follows_popup_when_original_page_closes() -> None:
    session = RecordingSession(id="recording-test", project_id="project-test")

    class ClosedPage:
        @staticmethod
        def is_closed() -> bool:
            return True

    class PopupPage:
        calls = 0

        @staticmethod
        def is_closed() -> bool:
            return False

        def wait_for_timeout(self, _timeout_ms: int) -> None:
            self.calls += 1
            session.finalize.set()

    popup = PopupPage()

    class FakeContext:
        pages = [ClosedPage(), popup]

    assert _wait_for_signal(session, 1, ClosedPage(), FakeContext()) == "finalize"
    assert popup.calls == 1


def test_wait_for_signal_reloads_inside_recording_worker() -> None:
    session = RecordingSession(id="recording-test", project_id="project-test")
    session.reload_requested.set()

    class FakePage:
        url = "https://example.test/login"
        reload_calls = 0

        @staticmethod
        def is_closed() -> bool:
            return False

        def reload(self, *, wait_until: str, timeout: int) -> None:
            assert wait_until == "commit"
            assert timeout == LOGIN_NAVIGATION_COMMIT_TIMEOUT_MS
            self.reload_calls += 1

        def wait_for_timeout(self, _timeout_ms: int) -> None:
            session.finalize.set()

        @staticmethod
        def bring_to_front() -> None:
            pass

    page = FakePage()

    class FakeContext:
        pages = [page]

        @staticmethod
        def new_cdp_session(_page):
            raise RuntimeError("not a Chromium test double")

    assert _wait_for_signal(session, 1, page, FakeContext()) == "finalize"
    assert page.reload_calls == 1
    assert session.reload_count == 1
    assert session.reload_done.is_set()
    assert not session.reload_requested.is_set()


def test_complete_reports_the_original_recording_error() -> None:
    manager = LoginRecordingManager()
    session = RecordingSession(
        id="recording-test",
        project_id="project-test",
        status="error",
        error="Cookie 域名不在项目允许列表：auth.example.test",
    )
    manager._sessions[session.id] = session

    try:
        manager.complete(session.id)
    except RuntimeError as exc:
        assert str(exc) == session.error
    else:
        raise AssertionError("the original recording error must be returned")


def test_open_login_page_returns_after_main_document_commit() -> None:
    calls: dict[str, object] = {}

    class FakePage:
        @staticmethod
        def bring_to_front() -> None:
            calls["foregrounded"] = int(calls.get("foregrounded", 0)) + 1

        @staticmethod
        def on(_event: str, _callback) -> None:
            pass

        @staticmethod
        def goto(url: str, *, wait_until: str, timeout: int) -> None:
            calls["goto"] = (url, wait_until, timeout)

        @staticmethod
        def reload(*_args, **_kwargs) -> None:
            raise AssertionError("slow login pages must not be reloaded")

    page = FakePage()

    class FakeContext:
        class FakeSession:
            @staticmethod
            def send(method: str, _params: dict | None = None) -> dict:
                return {} if method == "Browser.getWindowForTarget" else {}

            @staticmethod
            def detach() -> None:
                pass

        @staticmethod
        def route(_pattern: str, _callback) -> None:
            pass

        @staticmethod
        def new_page() -> FakePage:
            return page

        @staticmethod
        def new_cdp_session(_page) -> FakeSession:
            return FakeContext.FakeSession()

    context = FakeContext()

    class FakeBrowser:
        @staticmethod
        def new_context(*, viewport: dict[str, int], locale: str) -> FakeContext:
            calls["viewport"] = viewport
            calls["locale"] = locale
            return context

    class FakeProject:
        base_url = "https://slow.example.test/login"

    class FakePolicy:
        @staticmethod
        def clear_rejection() -> None:
            calls["policyCleared"] = True

    opened_context, opened_page = _open_login_page(
        FakeBrowser(), FakeProject(), FakePolicy()
    )

    assert opened_context is context
    assert opened_page is page
    assert calls["goto"] == (
        FakeProject.base_url,
        "commit",
        LOGIN_NAVIGATION_COMMIT_TIMEOUT_MS,
    )
    assert calls["viewport"] == {"width": 1440, "height": 960}
    assert calls["locale"] == "en-US"
    assert calls["policyCleared"] is True
    assert calls["foregrounded"] == 2


def test_show_login_window_restores_and_positions_browser() -> None:
    calls: list[tuple[str, dict | None]] = []

    class FakeSession:
        @staticmethod
        def send(method: str, params: dict | None = None) -> dict:
            calls.append((method, params))
            return {"windowId": 42} if method == "Browser.getWindowForTarget" else {}

        @staticmethod
        def detach() -> None:
            calls.append(("detach", None))

    class FakePage:
        @staticmethod
        def bring_to_front() -> None:
            calls.append(("bring_to_front", None))

    class FakeContext:
        @staticmethod
        def new_cdp_session(_page) -> FakeSession:
            return FakeSession()

    _show_login_window(FakeContext(), FakePage())

    assert calls == [
        ("bring_to_front", None),
        ("Browser.getWindowForTarget", None),
        ("Browser.setWindowBounds", {"windowId": 42, "bounds": {"windowState": "normal"}}),
        (
            "Browser.setWindowBounds",
            {"windowId": 42, "bounds": {"left": 80, "top": 60, "width": 1440, "height": 960}},
        ),
        ("detach", None),
    ]


def test_safe_request_target_drops_path_query_and_credentials() -> None:
    target = _safe_request_target(
        "https://user:password@auth.example.test:8443/login?token=secret"
    )

    assert target == "auth.example.test:8443"
    assert "secret" not in target
    assert "password" not in target


def test_network_reason_does_not_echo_unknown_failure_details() -> None:
    reason = _safe_network_reason("custom failure at /login?token=secret")

    assert reason == "网络请求失败"
    assert "secret" not in reason


def test_recording_diagnostic_url_removes_query_fragment_and_credentials() -> None:
    value = _safe_event_url(
        "https://user:password@accounts.google.com/o/oauth2/auth?token=secret#code"
    )

    assert value == "https://accounts.google.com/o/oauth2/auth"
    assert "secret" not in value
    assert "password" not in value


def test_recording_completion_rejects_visible_login_form() -> None:
    class LoginPage:
        @staticmethod
        def evaluate(_script: str) -> dict:
            return {"startupLoading": False, "loginFormVisible": True}

    try:
        _validate_recording_completion(LoginPage())
    except RuntimeError as exc:
        assert "登录尚未完成" in str(exc)
    else:
        raise AssertionError("a visible login form must not be saved as an authenticated session")


def test_recording_completion_accepts_authenticated_page() -> None:
    class AuthenticatedPage:
        @staticmethod
        def evaluate(_script: str) -> dict:
            return {"startupLoading": False, "loginFormVisible": False}

    _validate_recording_completion(AuthenticatedPage())
