"""Interactive login recording sessions backed by a controlled Playwright browser."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from ..security.policy import DomainPolicy, SecurityError
from .models import ProjectConfig
from .session import (
    SESSION_STORAGE_STATE_KEY,
    capture_session_storage_for_project,
    filter_storage_state_for_project,
    validate_storage_state,
)
from .store import ProjectStore


@dataclass
class RecordingSession:
    id: str
    project_id: str
    status: str = "starting"
    ready: Event = field(default_factory=Event)
    finalize: Event = field(default_factory=Event)
    cancel: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)
    result: dict[str, Any] | None = None
    error: str | None = None
    browser_name: str | None = None


LOGIN_NAVIGATION_COMMIT_TIMEOUT_MS = 15_000
LOGIN_EVENT_PUMP_MS = 200
_GOOGLE_OAUTH_COMPAT_ARGS = ["--disable-blink-features=AutomationControlled"]


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
        browser_cleanup: Callable[[], None] = lambda: None
        try:
            policy = DomainPolicy(
                project.base_url,
                project.allowed_hosts,
                allow_private_network=project.allow_private_network,
            )
            policy.check_url(project.base_url)
            with sync_playwright() as playwright:
                browser, session.browser_name, shared_browser, browser_cleanup = launch_visible_login_browser(playwright)
                if shared_browser:
                    contexts = browser.contexts
                    if not contexts:
                        raise RuntimeError("共享 Edge 没有可用的浏览器窗口，请重新双击启动 GUI")
                    context = contexts[0]
                else:
                    context = browser.new_context(viewport={"width": 1440, "height": 960})

                existing_page = None
                if shared_browser:
                    target_host = (urlparse(project.base_url).hostname or "").lower()
                    existing_pages = [
                        candidate
                        for candidate in context.pages
                        if (urlparse(candidate.url).hostname or "").lower() == target_host
                    ]
                    existing_page = existing_pages[-1] if existing_pages else None
                page = _open_user_controlled_login_page(
                    context, project, policy, existing_page=existing_page
                )
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
            if page is not None:
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
            browser_cleanup()
            session.done.set()


def _open_user_controlled_login_page(
    context, project: ProjectConfig, policy=None, existing_page=None
):
    """Validate the initial target, then leave login networking under user control."""
    if policy is None:
        policy = DomainPolicy(
            project.base_url,
            project.allowed_hosts,
            allow_private_network=project.allow_private_network,
        )
        policy.check_url(project.base_url)
        page = context.new_page()
        page.goto(project.base_url, wait_until="domcontentloaded")
        return page
    if existing_page is not None and not existing_page.is_closed():
        # Process mode already has the user's authenticated same-site page.
        # Reuse it instead of opening a fresh page that may start an unrelated
        # third-party SSO flow and make the user log in twice.
        _show_login_window(context, existing_page)
        return existing_page
    page = context.new_page()
    navigation_failure: dict[str, str | None] = {"reason": None}

    def remember_navigation_failure(request) -> None:
        try:
            if request.is_navigation_request():
                navigation_failure["reason"] = request.failure
        except Exception:
            navigation_failure["reason"] = None

    page.on("requestfailed", remember_navigation_failure)
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
    return page


def _show_login_window(context, page) -> None:
    session = None
    try:
        page.bring_to_front()
        session = context.new_cdp_session(page)
        window = session.send("Browser.getWindowForTarget")
        window_id = window.get("windowId")
        if window_id is None:
            return
        session.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "normal"}})
        session.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"left": 80, "top": 60, "width": 1440, "height": 960}},
        )
    except Exception:
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


def _safe_request_target(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "未知主机"
    return f"{host}:{parsed.port}" if parsed.port is not None else host


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


def _wait_for_signal(session: RecordingSession, timeout_seconds: int, page, context=None) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if session.cancel.is_set():
            return "cancel"
        if session.finalize.is_set():
            return "finalize"
        page = _latest_open_page(page, context)
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        try:
            page.wait_for_timeout(min(LOGIN_EVENT_PUMP_MS, remaining_ms))
        except PlaywrightError:
            if session.cancel.is_set():
                return "cancel"
            replacement = _latest_open_page(page, context)
            if replacement is page:
                time.sleep(min(LOGIN_EVENT_PUMP_MS / 1000, remaining_ms / 1000))
            page = replacement
    return "timeout"


def _project_storage_state(project: ProjectConfig, state: dict[str, Any]) -> dict[str, Any]:
    """Keep only the current project's session data from the shared GUI browser."""
    allowed = {host.strip().rstrip(".").lower() for host in project.allowed_hosts}
    cookies = []
    for cookie in state.get("cookies", []):
        domain = str(cookie.get("domain", "")).lstrip(".").lower()
        if domain in allowed or any(host.endswith(f".{domain}") for host in allowed):
            cookies.append(cookie)
    origins = []
    for origin in state.get("origins", []):
        host = (urlparse(str(origin.get("origin", ""))).hostname or "").lower()
        if host in allowed:
            origins.append(origin)
    return {"cookies": cookies, "origins": origins}


def launch_visible_login_browser(playwright):
    """Use a normal Edge process for user authentication whenever possible."""
    # Reuse the visible GUI browser whenever its CDP endpoint is available.
    # This keeps the user's authenticated profile and makes login recording
    # happen in the same window the user can see.
    if os.environ.get("GUI_BROWSER_CDP_URL", "").strip():
        try:
            browser, name, shared = _launch_playwright_login_browser(playwright)
            return browser, name, shared, lambda: None
        except (OSError, PlaywrightError, RuntimeError):
            pass
    try:
        return _launch_standard_edge_browser(playwright)
    except (OSError, PlaywrightError, RuntimeError):
        browser, name, shared = _launch_playwright_login_browser(playwright)
        return browser, name, shared, lambda: None


def _launch_standard_edge_browser(playwright):
    executable = _find_edge_executable()
    if executable is None:
        raise RuntimeError("Microsoft Edge is unavailable")
    profile_dir = Path(tempfile.mkdtemp(prefix="ai-gui-login-edge-"))
    port = _reserve_local_port()
    process = subprocess.Popen(
        [
            str(executable),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-sandbox",
            "--disable-gpu",
            *_GOOGLE_OAUTH_COMPAT_ARGS,
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    browser = None
    try:
        cdp_url = f"http://127.0.0.1:{port}"
        _wait_for_cdp(cdp_url, process)
        browser = playwright.chromium.connect_over_cdp(cdp_url)
    except Exception:
        _terminate_process(process)
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise

    def cleanup() -> None:
        try:
            browser.close()
        except Exception:
            pass
        _terminate_process(process)
        shutil.rmtree(profile_dir, ignore_errors=True)

    return browser, "Microsoft Edge", True, cleanup


def _find_edge_executable() -> Path | None:
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")]
    for root in roots:
        if not root:
            continue
        candidate = Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if candidate.is_file():
            return candidate
    located = shutil.which("msedge")
    return Path(located) if located else None


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_cdp(cdp_url: str, process, timeout_seconds: float = 12.0) -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Microsoft Edge closed before login recording started")
        try:
            with opener.open(f"{cdp_url}/json/version", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Microsoft Edge did not expose the login connection in time")


def _terminate_process(process) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=2)
        except Exception:
            pass


def _launch_playwright_login_browser(playwright):
    """Use the Edge window that already displays the GUI whenever the launcher provides it."""
    cdp_url = os.environ.get("GUI_BROWSER_CDP_URL", "").strip()
    if cdp_url:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
        except PlaywrightError as exc:
            raise RuntimeError("无法连接显示 GUI 的 Edge 窗口，请关闭后重新双击启动 AI 测试") from exc
        name = os.environ.get("GUI_BROWSER_NAME", "Microsoft Edge").strip() or "Microsoft Edge"
        return browser, f"{name}（与 GUI 同一窗口）", True
    try:
        return (
            playwright.chromium.launch(
                channel="msedge",
                headless=False,
                args=_GOOGLE_OAUTH_COMPAT_ARGS,
                ignore_default_args=["--enable-automation"],
            ),
            "Microsoft Edge",
            False,
        )
    except PlaywrightError:
        return (
            playwright.chromium.launch(
                headless=False,
                args=_GOOGLE_OAUTH_COMPAT_ARGS,
                ignore_default_args=["--enable-automation"],
            ),
            "内置测试浏览器",
            False,
        )
