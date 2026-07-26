"""Deterministic page and iframe selection for multi-surface commerce flows."""

from __future__ import annotations

from time import monotonic, sleep

from playwright.sync_api import Error as PlaywrightError

from ..domain.models import BrowserTarget


def resolve_browser_surface(
    context, current_page, target: BrowserTarget, policy, *, enforce_url_condition: bool = True
):
    deadline = monotonic() + target.wait_timeout_ms / 1000
    page = None
    while monotonic() <= deadline:
        pages = [candidate for candidate in context.pages if not candidate.is_closed()]
        candidates = _page_candidates(pages, current_page, target.page)
        if target.url_contains and enforce_url_condition:
            candidates = [candidate for candidate in candidates if target.url_contains in candidate.url]
        if candidates:
            page = candidates[-1]
            break
        sleep(0.05)
    if page is None:
        suffix = (
            f" 且 URL 包含 {target.url_contains!r}"
            if target.url_contains and enforce_url_condition else ""
        )
        raise PlaywrightError(f"等待浏览器页面 {target.page}{suffix} 超时")

    if page.url != "about:blank":
        policy.check_url(page.url)
    page.set_default_timeout(target.wait_timeout_ms)
    page.set_default_navigation_timeout(target.wait_timeout_ms)

    root = page
    frame_evidence = None
    if target.frame_css:
        frames = page.locator(target.frame_css)
        count = frames.count()
        if count != 1:
            raise PlaywrightError(f"iframe 选择器匹配到 {count} 个元素，必须唯一")
        frames.first.wait_for(state="visible", timeout=target.wait_timeout_ms)
        root = page.frame_locator(target.frame_css)
        frame_evidence = {"selector": target.frame_css, "matched": 1}

    return page, root, {
        "pageSelection": target.page,
        "pageCount": len([candidate for candidate in context.pages if not candidate.is_closed()]),
        "url": page.url,
        "urlCondition": target.url_contains,
        "frame": frame_evidence,
    }


def _page_candidates(pages, current_page, selection: str):
    if selection == "current":
        return [current_page] if current_page in pages else []
    if selection == "newest":
        return pages
    opener = current_page.opener() if current_page and not current_page.is_closed() else None
    return [opener] if opener in pages else []
