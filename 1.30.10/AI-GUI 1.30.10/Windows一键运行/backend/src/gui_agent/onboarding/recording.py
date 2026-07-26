"""Interactive login recording sessions backed by a controlled Playwright browser."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

from playwright.sync_api import sync_playwright

from ..security.policy import DomainPolicy, SecurityError, guard_playwright_route
from .models import ProjectConfig
from .session import validate_storage_state
from .store import ProjectStore


@dataclass
class RecordingSession:
    id: str
    project_id: str
    account_id: str = "default"
    status: str = "starting"
    ready: Event = field(default_factory=Event)
    finalize: Event = field(default_factory=Event)
    cancel: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)
    result: dict[str, Any] | None = None
    error: str | None = None


class LoginRecordingManager:
    def __init__(self) -> None:
        self._sessions: dict[str, RecordingSession] = {}
        self._lock = Lock()

    def start(self, project: ProjectConfig, store: ProjectStore, timeout_seconds: int = 600, account_id: str = "default") -> RecordingSession:
        if not any(item.id == account_id for item in project.account_profiles):
            raise ValueError(f"账号槽位不存在：{account_id}")
        session = RecordingSession(id=f"recording-{uuid4().hex[:10]}", project_id=project.id, account_id=account_id)
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
        try:
            policy = DomainPolicy(
                project.base_url,
                project.allowed_hosts,
                allow_private_network=project.allow_private_network,
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                context = browser.new_context(viewport={"width": 1440, "height": 960})

                context.route("**/*", lambda route: guard_playwright_route(route, policy))
                page = context.new_page()
                policy.clear_rejection()
                try:
                    page.goto(project.base_url, wait_until="domcontentloaded")
                except Exception as exc:
                    rejection = policy.consume_rejection()
                    if rejection:
                        raise SecurityError(rejection) from exc
                    raise
                session.status = "recording"
                session.ready.set()
                winner = _wait_for_signal(session, timeout_seconds)
                if winner == "cancel":
                    session.status = "cancelled"
                    return
                if winner == "timeout":
                    raise RuntimeError("登录录制超过项目运行时限，未保存任何会话")
                state = context.storage_state()
                metadata = validate_storage_state(project, state, session.account_id)
                store.save_session(project, state, metadata, session.account_id)
                session.result = metadata.model_dump(mode="json", by_alias=True)
                session.status = "completed"
        except Exception as exc:
            session.error = str(exc)
            session.status = "error"
            session.ready.set()
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            session.done.set()


def _wait_for_signal(session: RecordingSession, timeout_seconds: int) -> str:
    import time
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if session.cancel.wait(0.2):
            return "cancel"
        if session.finalize.is_set():
            return "finalize"
    return "timeout"
