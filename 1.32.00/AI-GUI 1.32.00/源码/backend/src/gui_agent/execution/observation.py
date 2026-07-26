"""Bounded, redacted browser observations captured around every action."""

from __future__ import annotations

from collections import deque
from fnmatch import fnmatch
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..artifacts import ArtifactManager
from ..domain.results import Observation, PageHealth, PageIssue
from ..security.redaction import Redactor, summarize_request_url


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
        diagnostics = self._page_diagnostics()
        return Observation(
            url=self.redactor.scrub(url),
            title=self.redactor.scrub(title),
            screenshot=screenshot,
            dom_summary=[self.redactor.scrub(item) for item in dom_summary],
            accessibility_summary=self.redactor.scrub(accessibility_summary),
            console_errors=[self.redactor.scrub(item) for item in console_errors],
            page_errors=[self.redactor.scrub(item) for item in page_errors],
            failed_requests=[self.redactor.scrub(item) for item in failed_requests],
            page_issues=[
                PageIssue(
                    kind=str(item.get("kind", "ui")),
                    severity=str(item.get("severity", "Medium")),
                    confidence=str(item.get("confidence", "medium")),
                    message=self.redactor.scrub(str(item.get("message", "页面异常信号"))),
                    target=self.redactor.scrub(str(item.get("target", ""))),
                    details=item.get("details", {}) if isinstance(item.get("details"), dict) else {},
                )
                for item in diagnostics.get("issues", [])[:30]
                if isinstance(item, dict)
            ],
            page_health=PageHealth(**diagnostics["health"]) if diagnostics.get("health") else None,
        )

    def _page_diagnostics(self) -> dict:
        try:
            result = self.page.evaluate(
                """() => {
                  const issues = [];
                  const viewport = { width: window.innerWidth, height: window.innerHeight };
                  const visible = (el) => {
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                      Number(style.opacity || 1) > 0 && rect.width > 1 && rect.height > 1 &&
                      rect.bottom > 0 && rect.right > 0 && rect.top < viewport.height && rect.left < viewport.width;
                  };
                  const targetName = (el) => {
                    const tag = el.tagName.toLowerCase();
                    const role = el.getAttribute('role');
                    const label = el.getAttribute('aria-label');
                    const testId = el.getAttribute('data-testid');
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 100);
                    return [tag, role && `role=${role}`, label && `label=${label}`,
                      testId && `testid=${testId}`, text && `text=${text}`].filter(Boolean).join(' | ');
                  };
                  const all = Array.from(document.body?.querySelectorAll('*') || []);
                  const visibleElements = all.filter(visible);
                  const interactives = Array.from(document.querySelectorAll(
                    'a[href],button,input:not([type="hidden"]),select,textarea,[role="button"],[role="link"],[tabindex]'
                  )).slice(0, 120);

                  for (const el of interactives) {
                    if (!visible(el)) continue;
                    const rect = el.getBoundingClientRect();
                    const x = Math.max(0, Math.min(viewport.width - 1, rect.left + rect.width / 2));
                    const y = Math.max(0, Math.min(viewport.height - 1, rect.top + rect.height / 2));
                    const top = document.elementFromPoint(x, y);
                    if (top && top !== el && !el.contains(top) && !top.contains(el)) {
                      issues.push({
                        kind: 'element_obscured', severity: 'High', confidence: 'high',
                        message: '交互控件中心点被其他元素遮挡，可能无法点击', target: targetName(el),
                        details: { coveringElement: targetName(top), x: Math.round(x), y: Math.round(y) }
                      });
                    }
                    const style = getComputedStyle(el);
                    if (style.pointerEvents === 'none') {
                      issues.push({
                        kind: 'control_inoperable', severity: 'Medium', confidence: 'high',
                        message: '可见交互控件禁用了指针事件', target: targetName(el),
                        details: { pointerEvents: 'none' }
                      });
                    }
                  }

                  const textNodes = Array.from(document.querySelectorAll(
                    'button,a,label,p,li,td,th,h1,h2,h3,h4,[role="button"],[data-testid]'
                  )).slice(0, 180);
                  for (const el of textNodes) {
                    if (!visible(el)) continue;
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (text.length < 4) continue;
                    const style = getComputedStyle(el);
                    const clips = ['hidden', 'clip'].includes(style.overflow) ||
                      ['hidden', 'clip'].includes(style.overflowX) || ['hidden', 'clip'].includes(style.overflowY) ||
                      style.textOverflow === 'ellipsis';
                    if (clips && (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2)) {
                      issues.push({
                        kind: 'text_truncated', severity: 'Medium', confidence: 'high',
                        message: '文本内容超出可见区域并被裁剪', target: targetName(el),
                        details: { clientWidth: el.clientWidth, scrollWidth: el.scrollWidth,
                          clientHeight: el.clientHeight, scrollHeight: el.scrollHeight }
                      });
                    }
                  }

                  const visibleTextLength = (document.body?.innerText || '').replace(/\\s+/g, '').length;
                  const visualSurfaces = Array.from(document.querySelectorAll('canvas,svg,img,video')).filter((el) => {
                    const rect = el.getBoundingClientRect();
                    return visible(el) && rect.width * rect.height >= 400;
                  });
                  const health = {
                    ready_state: document.readyState,
                    visible_text_length: visibleTextLength,
                    visible_element_count: visibleElements.length,
                    interactive_count: interactives.filter(visible).length,
                    visual_surface_count: visualSurfaces.length
                  };
                  if (location.href !== 'about:blank' && document.readyState !== 'loading' &&
                      visibleTextLength < 2 && visibleElements.length === 0 && visualSurfaces.length === 0) {
                    issues.push({
                      kind: 'blank_page', severity: 'High', confidence: 'high',
                      message: '页面加载完成但没有可见文本、元素或图形内容', target: 'document', details: health
                    });
                  }
                  return { health, issues };
                }"""
            )
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}

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
                    const href = el.getAttribute('href');
                    const text = ['input', 'textarea', 'select'].includes(tag)
                      ? '' : (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
                    const state = [];
                    if (el.disabled) state.push('disabled');
                    if (el.checked) state.push('checked');
                    if (el.selected) state.push('selected');
                    for (const name of ['aria-current', 'aria-selected', 'aria-pressed', 'aria-expanded', 'aria-checked']) {
                      const value = el.getAttribute(name);
                      if (value !== null) state.push(`${name}=${value}`);
                    }
                    const semanticClasses = (value) => Array.from(value || []).filter((name) =>
                      /(^|[-_])(active|current|selected|checked|open)([-_]|$)/i.test(name)
                    ).slice(0, 6);
                    const ownClasses = semanticClasses(el.classList);
                    if (ownClasses.length) state.push(`state-class=${ownClasses.join(',')}`);
                    let ancestor = el.parentElement;
                    for (let depth = 0; ancestor && depth < 2; depth += 1, ancestor = ancestor.parentElement) {
                      const ancestorClasses = semanticClasses(ancestor.classList);
                      const ancestorCurrent = ancestor.getAttribute('aria-current');
                      const ancestorSelected = ancestor.getAttribute('aria-selected');
                      if (ancestorClasses.length || ancestorCurrent !== null || ancestorSelected !== null) {
                        state.push(`ancestor-state=${[
                          ...ancestorClasses,
                          ancestorCurrent !== null ? `aria-current=${ancestorCurrent}` : '',
                          ancestorSelected !== null ? `aria-selected=${ancestorSelected}` : '',
                        ].filter(Boolean).join(',')}`);
                        break;
                      }
                    }
                    return [tag, role && `role=${role}`, label && `label=${label}`,
                      testId && `testid=${testId}`, type && `type=${type}`,
                      href && `href=${href.slice(0, 240)}`, text && `text=${text}`,
                      ...state].filter(Boolean).join(' | ');
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
            self._failed_requests.append(
                f"{request.method} {summarize_request_url(request.url)} - {failure_text[:300]}"
            )
        except Exception:
            pass

    def _on_response(self, response: Any) -> None:
        try:
            if self._ignored(response.url):
                return
            if response.status >= 400:
                self._failed_requests.append(
                    f"HTTP {response.status} {response.request.method} {summarize_request_url(response.url)}"
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
