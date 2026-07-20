"""Bounded, redacted browser observations captured around every action."""

from __future__ import annotations

from collections import deque
from fnmatch import fnmatch
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..artifacts import ArtifactManager
from ..domain.results import Observation
from ..security.redaction import Redactor


class ObservationCollector:
    """Collect browser facts without persisting full DOM, form values, or headers."""

    def __init__(self, page, artifacts: ArtifactManager, redactor: Redactor, ignore_rules: tuple[str, ...] = ()) -> None:
        self.page = page
        self.artifacts = artifacts
        self.redactor = redactor
        self.ignore_rules = ignore_rules
        self._console: deque[str] = deque(maxlen=100)
        self._page_errors: deque[str] = deque(maxlen=100)
        self._failed_requests: deque[str] = deque(maxlen=100)
        self._console_cursor = 0
        self._page_error_cursor = 0
        self._request_cursor = 0
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)

    def capture(self, screenshot: str | None) -> Observation:
        url = self._safe_value(lambda: self.page.url, "about:blank")
        title = self._safe_value(self.page.title, "")
        dom_summary = self._dom_summary()
        accessibility_summary = self._accessibility_summary()
        console_errors, self._console_cursor = self._since(self._console, self._console_cursor)
        page_errors, self._page_error_cursor = self._since(self._page_errors, self._page_error_cursor)
        failed_requests, self._request_cursor = self._since(self._failed_requests, self._request_cursor)
        return Observation(
            url=self.redactor.scrub(url),
            title=self.redactor.scrub(title),
            screenshot=screenshot,
            dom_summary=[self.redactor.scrub(item) for item in dom_summary],
            accessibility_summary=self.redactor.scrub(accessibility_summary),
            console_errors=[self.redactor.scrub(item) for item in console_errors],
            page_errors=[self.redactor.scrub(item) for item in page_errors],
            failed_requests=[self.redactor.scrub(item) for item in failed_requests],
        )

    def _dom_summary(self) -> list[str]:
        try:
            result = self.page.evaluate(
                """() => {
                  const selectors = 'a,button,input,select,textarea,[role],[aria-label],[data-testid],h1,h2,h3';
                  return Array.from(document.querySelectorAll(selectors)).slice(0, 80).map((node) => {
                    const el = node;
                    const tag = el.tagName.toLowerCase();
                    const role = el.getAttribute('role');
                    const label = el.getAttribute('aria-label');
                    const testId = el.getAttribute('data-testid');
                    const type = el.getAttribute('type');
                    const text = ['input', 'textarea', 'select'].includes(tag)
                      ? '' : (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
                    const state = [];
                    if (el.disabled) state.push('disabled');
                    if (el.checked) state.push('checked');
                    return [tag, role && `role=${role}`, label && `label=${label}`,
                      testId && `testid=${testId}`, type && `type=${type}`,
                      text && `text=${text}`, ...state].filter(Boolean).join(' | ');
                  }).filter(Boolean);
                }"""
            )
            return [str(item)[:500] for item in result] if isinstance(result, list) else []
        except Exception:
            return []

    def _accessibility_summary(self) -> str:
        try:
            snapshot = self.page.locator("body").aria_snapshot(timeout=2_000)
            return str(snapshot)[:12_000]
        except Exception:
            return ""

    def _on_console(self, message: Any) -> None:
        try:
            if message.type == "error":
                self._console.append(str(message.text)[:1_000])
        except Exception:
            pass

    def _on_page_error(self, error: Any) -> None:
        self._page_errors.append(str(error)[:1_000])

    def _on_request_failed(self, request: Any) -> None:
        try:
            if self._ignored(request.url):
                return
            failure = request.failure
            failure_text = failure if isinstance(failure, str) else str(failure or "request failed")
            self._failed_requests.append(f"{request.method} {request.url} - {failure_text}"[:1_500])
        except Exception:
            pass

    def _on_response(self, response: Any) -> None:
        try:
            if self._ignored(response.url):
                return
            if response.status >= 400:
                self._failed_requests.append(
                    f"HTTP {response.status} {response.request.method} {response.url}"[:1_500]
                )
        except Exception:
            pass

    def _ignored(self, url: str) -> bool:
        return any(fnmatch(url, pattern) for pattern in self.ignore_rules)

    @staticmethod
    def _since(items: deque[str], cursor: int) -> tuple[list[str], int]:
        values = list(items)
        # A bounded deque may have evicted old entries. In that case return its current contents.
        start = cursor if cursor <= len(values) else 0
        return values[start:], len(values)

    @staticmethod
    def _safe_value(reader, fallback: str) -> str:
        try:
            return str(reader()) if callable(reader) else str(reader)
        except PlaywrightError:
            return fallback
