"""Shared Playwright startup diagnostics for every browser execution path."""

from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Iterator

from playwright.sync_api import Playwright, sync_playwright


class BrowserRuntimeUnavailable(RuntimeError):
    """The Playwright driver could not start before a browser was launched."""


SUPPORTED_BROWSERS = ("chromium", "edge", "firefox", "webkit")


def normalize_browser_name(value: str | None = None) -> str:
    name = (value or os.getenv("GUI_BROWSER", "chromium")).strip().lower()
    aliases = {"chrome": "chromium", "msedge": "edge", "webkitgtk": "webkit"}
    normalized = aliases.get(name, name)
    if normalized not in SUPPORTED_BROWSERS:
        raise BrowserRuntimeUnavailable(
            f"不支持的浏览器：{name}；可选值为 {', '.join(SUPPORTED_BROWSERS)}"
        )
    return normalized


def launch_browser(playwright: Playwright, *, browser_name: str | None = None, headless: bool = True, slow_mo_ms: int = 0):
    """Launch one of the Playwright browser types with a stable error boundary."""
    name = normalize_browser_name(browser_name)
    browser_type = playwright.chromium if name in {"chromium", "edge"} else getattr(playwright, name)
    kwargs = {"headless": headless, "slow_mo": slow_mo_ms}
    if name == "edge":
        kwargs["channel"] = "msedge"
    try:
        return browser_type.launch(**kwargs)
    except Exception as exc:
        raise BrowserRuntimeUnavailable(
            f"浏览器 {name} 启动失败；请检查对应 Playwright 浏览器和系统依赖"
        ) from exc


@contextmanager
def playwright_runtime() -> Iterator[Playwright]:
    manager = sync_playwright()
    try:
        playwright = manager.__enter__()
    except Exception as exc:
        detail = str(exc)
        known_startup_failure = (
            isinstance(exc, AttributeError) and "_playwright" in detail
        ) or "connection closed while reading from the driver" in detail.lower()
        if known_startup_failure:
            raise BrowserRuntimeUnavailable(
                "Playwright 浏览器驱动启动失败；请检查打包运行时、Node/浏览器文件读取权限和 PLAYWRIGHT_BROWSERS_PATH"
            ) from exc
        raise
    try:
        yield playwright
    finally:
        manager.__exit__(None, None, None)
