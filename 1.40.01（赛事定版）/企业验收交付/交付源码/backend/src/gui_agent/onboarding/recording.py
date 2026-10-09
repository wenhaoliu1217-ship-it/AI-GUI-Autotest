"""Interactive login recording sessions backed by a controlled Playwright browser."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock, Thread
import time
import os
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from ..playwright_runtime import playwright_runtime

from ..security.policy import (
    DomainPolicy,
    SecurityError,
    guard_playwright_route,
    temporary_login_navigation_hosts,
)
from .models import ProjectConfig
from .session import (
    SESSION_STORAGE_STATE_KEY,
    capture_session_storage_for_project,
    filter_storage_state_for_project,
    validate_storage_state,
)
from .store import ProjectStore


LOGIN_NAVIGATION_COMMIT_TIMEOUT_MS = 15_000
LOGIN_EVENT_PUMP_MS = 200


@dataclass
class RecordingSession:
    id: str
    project_id: str
    status: str = "starting"
    ready: Event = field(default_factory=Event)
    finalize: Event = field(default_factory=Event)
    cancel: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)
    reload_requested: Event = field(default_factory=Event)
    reload_done: Event = field(default_factory=Event)
    result: dict[str, Any] | None = None
    error: str | None = None
    reload_error: str | None = None
    last_url: str | None = None
    reload_count: int = 0
    browser_name: str | None = None
    failed_requests: list[dict[str, Any]] = field(default_factory=list)
    http_errors: list[dict[str, Any]] = field(default_factory=list)
    recent_responses: list[dict[str, Any]] = field(default_factory=list)
    pending_requests: dict[int, dict[str, Any]] = field(default_factory=dict)
    console_errors: int = 0
    page_errors: int = 0


class LoginRecordingManager:
    def __init__(self) -> None:
        self._sessions: dict[str, RecordingSession] = {}
        self._lock = Lock()

    def start(self, project: ProjectConfig, store: ProjectStore, timeout_seconds: int = 600) -> RecordingSession:
        session = RecordingSession(id=f"recording-{uuid4().hex[:10]}", project_id=project.id)
        with self._lock:
            if any(item.project_id == project.id and item.status in {"starting", "recording", "saving"} for item in self._sessions.values()):
                raise ValueError("该项目已有进行中的登录录制")
            self._sessions[session.id] = session
        Thread(target=self._worker, args=(session, project, store, timeout_seconds), daemon=True).start()
        if not session.ready.wait(20):
            session.cancel.set()
            raise RuntimeError("交互登录浏览器启动超时")
        if session.error:
            raise RuntimeError(session.error)
        return session

    def complete(self, recording_id: str) -> RecordingSession:
        session = self.get(recording_id)
        if session.error:
            raise RuntimeError(session.error)
        if session.status != "recording":
            raise ValueError(f"录制当前状态为 {session.status}，不能完成")
        session.status = "saving"
        session.finalize.set()
        if not session.done.wait(30):
            raise RuntimeError("保存登录态超时")
        if session.error:
            raise RuntimeError(session.error)
        return session

    def stop(self, recording_id: str) -> RecordingSession:
        session = self.get(recording_id)
        session.cancel.set()
        session.done.wait(10)
        return session

    def reload(self, recording_id: str) -> RecordingSession:
        session = self.get(recording_id)
        if session.error:
            raise RuntimeError(session.error)
        if session.status != "recording":
            raise ValueError(f"录制当前状态为 {session.status}，不能重新加载")
        session.reload_error = None
        session.reload_done.clear()
        session.reload_requested.set()
        if not session.reload_done.wait(20):
            raise RuntimeError("重新加载登录页面超时")
        if session.reload_error:
            raise RuntimeError(session.reload_error)
        return session

    def get(self, recording_id: str) -> RecordingSession:
        with self._lock:
            session = self._sessions.get(recording_id)
        if session is None:
            raise ValueError("登录录制不存在")
        return session

    @staticmethod
    def _worker(session: RecordingSession, project: ProjectConfig, store: ProjectStore, timeout_seconds: int) -> None:
        browser = None
        context = None
        page = None
        shared_browser = False
        try:
            policy = DomainPolicy(
                project.base_url,
                project.allowed_hosts,
                allow_private_network=project.allow_private_network,
            )
            temporary_hosts = temporary_login_navigation_hosts(project.base_url)
            with policy.allow_temporary_navigation_hosts(temporary_hosts), playwright_runtime() as playwright:
                browser, session.browser_name, shared_browser = launch_visible_login_browser(playwright)
                if shared_browser:
                    contexts = browser.contexts
                    if not contexts:
                        raise RuntimeError("共享 Edge 没有可用的浏览器窗口，请重新启动测试助手")
                    context = contexts[0]
                    target_host = (urlparse(project.base_url).hostname or "").lower()
                    existing_pages = [
                        candidate for candidate in context.pages
                        if (urlparse(candidate.url).hostname or "").lower() == target_host
                    ]
                    page = _open_user_controlled_login_page(
                        context,
                        project,
                        policy,
                        existing_page=existing_pages[-1] if existing_pages else None,
                        recording=session,
                    )
                else:
                    context, page = _open_login_page(browser, project, policy, session)
                session.last_url = getattr(page, "url", session.last_url)
                session.status = "recording"
                session.ready.set()
                winner = _wait_for_signal(session, timeout_seconds, page, context)
                if winner == "cancel":
                    session.status = "cancelled"
                    return
                if winner == "timeout":
                    raise RuntimeError("登录录制超过项目运行时限，未保存任何会话")
                rejection = policy.consume_rejection()
                if rejection:
                    raise SecurityError(rejection)
                page = _latest_open_page(page, context)
                _validate_recording_completion(page)
                state = context.storage_state()
                state[SESSION_STORAGE_STATE_KEY] = capture_session_storage_for_project(project, context.pages)
                state = filter_storage_state_for_project(project, state)
                metadata = validate_storage_state(project, state)
                store.save_session(project, state, metadata)
                session.result = metadata.model_dump(mode="json", by_alias=True)
                session.status = "completed"
        except Exception as exc:
            session.error = str(exc)
            session.status = "error"
            session.ready.set()
        finally:
            if page is not None and shared_browser:
                try:
                    page.close()
                except Exception:
                    pass
            if context is not None and not shared_browser:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None and not shared_browser:
                try:
                    browser.close()
                except Exception:
                    pass
            session.done.set()


def _open_login_page(
    browser,
    project: ProjectConfig,
    policy: DomainPolicy,
    recording: RecordingSession | None = None,
):
    context = browser.new_context(viewport={"width": 1440, "height": 960}, locale="en-US")
    context.route("**/*", lambda route: guard_playwright_route(route, policy))
    page = context.new_page()
    navigation_failure: dict[str, str | None] = {"reason": None}

    def remember_navigation_failure(request) -> None:
        try:
            if request.is_navigation_request():
                navigation_failure["reason"] = request.failure
        except Exception:
            navigation_failure["reason"] = None

    page.on("requestfailed", remember_navigation_failure)
    if recording is not None:
        _attach_recording_diagnostics(page, recording)
    policy.clear_rejection()
    _show_login_window(context, page)
    try:
        page.goto(
            project.base_url,
            wait_until="commit",
            timeout=LOGIN_NAVIGATION_COMMIT_TIMEOUT_MS,
        )
    except Exception as exc:
        rejection = policy.consume_rejection()
        if rejection:
            raise SecurityError(rejection) from exc
        target = _safe_request_target(project.base_url)
        reason = _safe_network_reason(navigation_failure["reason"] or str(exc))
        raise RuntimeError(f"无法打开登录页面 {target}：{reason}") from exc
    _show_login_window(context, page)
    return context, page


def _open_user_controlled_login_page(
    context,
    project: ProjectConfig,
    policy: DomainPolicy | None = None,
    *,
    existing_page=None,
    recording: RecordingSession | None = None,
):
    """Open login in the normal shared Edge without installing automation routing."""
    policy = policy or DomainPolicy(
        project.base_url,
        project.allowed_hosts,
        allow_private_network=project.allow_private_network,
    )
    policy.check_url(project.base_url)
    if existing_page is not None and not existing_page.is_closed():
        if recording is not None:
            _attach_recording_diagnostics(existing_page, recording)
        _show_login_window(context, existing_page)
        return existing_page

    page = context.new_page()
    if recording is not None:
        _attach_recording_diagnostics(page, recording)
    policy.clear_rejection()
    _show_login_window(context, page)
    try:
        page.goto(
            project.base_url,
            wait_until="commit",
            timeout=LOGIN_NAVIGATION_COMMIT_TIMEOUT_MS,
        )
    except Exception as exc:
        target = _safe_request_target(project.base_url)
        reason = _safe_network_reason(str(exc))
        raise RuntimeError(f"无法打开登录页面 {target}：{reason}") from exc
    _show_login_window(context, page)
    return page


def _attach_recording_diagnostics(page, recording: RecordingSession) -> None:
    """Capture redacted login diagnostics without request bodies or credentials."""
    def request_event(request) -> dict[str, Any]:
        return {
            "method": str(getattr(request, "method", "GET")),
            "url": _safe_event_url(str(getattr(request, "url", ""))),
            "resourceType": str(getattr(request, "resource_type", "")) or None,
        }

    def on_request(request) -> None:
        recording.pending_requests[id(request)] = request_event(request)

    def on_request_finished(request) -> None:
        recording.pending_requests.pop(id(request), None)

    def on_request_failed(request) -> None:
        event = recording.pending_requests.pop(id(request), None) or request_event(request)
        event["failure"] = _safe_network_reason(str(getattr(request, "failure", "")))
        recording.failed_requests.append(event)
        del recording.failed_requests[:-20]

    def on_response(response) -> None:
        request = getattr(response, "request", None)
        if request is not None:
            recording.pending_requests.pop(id(request), None)
            event = request_event(request)
        else:
            event = {"method": "GET", "url": _safe_event_url(str(getattr(response, "url", "")))}
        event["status"] = int(getattr(response, "status", 0) or 0)
        recording.recent_responses.append(event)
        del recording.recent_responses[:-20]
        if event["status"] >= 400:
            recording.http_errors.append(event.copy())
            del recording.http_errors[:-20]

    def on_console(message) -> None:
        if str(getattr(message, "type", "")).lower() == "error":
            recording.console_errors += 1

    def on_page_error(_error) -> None:
        recording.page_errors += 1

    page.on("request", on_request)
    page.on("requestfinished", on_request_finished)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)
    page.on("console", on_console)
    page.on("pageerror", on_page_error)


def _safe_event_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "非 HTTP 页面"
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}{parsed.path or '/'}"


def _show_login_window(context, page) -> None:
    session = None
    try:
        page.bring_to_front()
        session = context.new_cdp_session(page)
        window = session.send("Browser.getWindowForTarget")
        window_id = window.get("windowId")
        if window_id is None:
            return
        session.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "normal"}},
        )
        session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {"left": 80, "top": 60, "width": 1440, "height": 960},
            },
        )
    except Exception:
        # Bringing the page forward is best effort on non-Chromium fallbacks.
        return
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass


def _latest_open_page(page, context=None):
    if context is not None:
        try:
            for candidate in reversed(context.pages):
                if not candidate.is_closed():
                    return candidate
        except PlaywrightError:
            pass
    return page


def _wait_for_signal(
    session: RecordingSession,
    timeout_seconds: int,
    page,
    context=None,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if session.cancel.is_set():
            return "cancel"
        if session.finalize.is_set():
            return "finalize"
        page = _latest_open_page(page, context)
        if session.reload_requested.is_set():
            try:
                page.reload(wait_until="commit", timeout=LOGIN_NAVIGATION_COMMIT_TIMEOUT_MS)
                _show_login_window(context, page)
                session.reload_count += 1
                session.last_url = page.url
            except Exception as exc:
                session.reload_error = _safe_network_reason(str(exc))
            finally:
                session.reload_requested.clear()
                session.reload_done.set()
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        try:
            page.wait_for_timeout(min(LOGIN_EVENT_PUMP_MS, remaining_ms))
            session.last_url = getattr(page, "url", session.last_url)
        except PlaywrightError:
            if session.cancel.is_set():
                return "cancel"
            replacement = _latest_open_page(page, context)
            if replacement is page:
                time.sleep(min(LOGIN_EVENT_PUMP_MS / 1000, remaining_ms / 1000))
            page = replacement
    return "timeout"


def _safe_request_target(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "未知主机"
    if parsed.port is not None:
        return f"{host}:{parsed.port}"
    return host


def _safe_network_reason(failure: str | None) -> str:
    value = (failure or "").lower()
    if "timed_out" in value or "timeout" in value:
        return "网络请求超时"
    if "name_not_resolved" in value:
        return "域名解析失败"
    if "cert_" in value or "certificate" in value:
        return "网站证书校验失败"
    if any(marker in value for marker in ("connection_refused", "connection_reset", "connection_closed")):
        return "无法连接网站"
    return "网络请求失败"


def _validate_recording_completion(page) -> None:
    state = page.evaluate(
        """() => {
            const visible = (element) => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                    rect.width > 0 && rect.height > 0;
            };
            const loginSelectors = [
                'input[type="password"]',
                'form[action*="login" i]',
                'form[action*="signin" i]',
                '[data-testid*="login" i]',
                '[data-testid*="signin" i]'
            ];
            const loginFormVisible = loginSelectors.some((selector) =>
                Array.from(document.querySelectorAll(selector)).some(visible)
            );
            const bodyText = document.body?.innerText?.trim() || '';
            const startupLoading = document.readyState === 'loading' || !document.body ||
                (bodyText.length < 20 && Boolean(document.querySelector(
                    '[aria-busy="true"], [role="progressbar"], .loading, .spinner'
                )));
            return { startupLoading, loginFormVisible };
        }"""
    )
    if state.get("startupLoading"):
        raise RuntimeError("登录页面仍在加载，请等待页面稳定后再点击“我已登录”")
    if state.get("loginFormVisible"):
        raise RuntimeError("登录尚未完成，请在弹出的窗口中完成登录后再点击“我已登录”")


def launch_visible_login_browser(playwright):
    """Prefer the normal Edge process created by the GUI launcher (1.32.00 path)."""
    cdp_url = os.getenv("GUI_BROWSER_CDP_URL", "").strip()
    if cdp_url:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
        except PlaywrightError as exc:
            raise RuntimeError("无法连接显示 GUI 的 Edge 窗口，请重新启动测试助手") from exc
        name = os.getenv("GUI_BROWSER_NAME", "Microsoft Edge").strip() or "Microsoft Edge"
        return browser, f"{name}（与 GUI 同一窗口）", True

    compatibility_args = ["--disable-blink-features=AutomationControlled"]
    try:
        browser = playwright.chromium.launch(
            channel="msedge",
            headless=False,
            args=compatibility_args,
            ignore_default_args=["--enable-automation"],
        )
        return browser, "Microsoft Edge", False
    except PlaywrightError:
        browser = playwright.chromium.launch(
            headless=False,
            args=compatibility_args,
            ignore_default_args=["--enable-automation"],
        )
        return browser, "内置测试浏览器", False
