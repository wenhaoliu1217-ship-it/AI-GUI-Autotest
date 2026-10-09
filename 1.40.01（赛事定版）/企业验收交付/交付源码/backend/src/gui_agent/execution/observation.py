"""Bounded, redacted browser observations captured around every action."""

from __future__ import annotations

from collections import deque
from fnmatch import fnmatch
import hashlib
import json
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from ..artifacts import ArtifactManager
from ..domain.results import Observation, PageHealth, PageIssue, PageSemanticSummary
from ..security.redaction import Redactor, summarize_request_url


OBSERVATION_EVALUATE_TIMEOUT_MS = 2_000
TELEMETRY_ABORT_HOSTS = ("google-analytics.com",)
MAX_SEMANTIC_COMPONENTS = 30
MAX_VISIBLE_COMPONENT_OPTIONS = 120
MAX_CESIUM_CANVAS_TARGETS = 8
_FATAL_RENDER_MARKERS = (
    "an error occurred while rendering. rendering has stopped.",
    "application error: a client-side exception has occurred",
    "a client-side exception has occurred",
)


def blocking_page_error(observation: Observation) -> str | None:
    """Return a high-confidence visible application failure, if present."""

    semantic = observation.semantic_summary
    if semantic is not None and semantic.blocking_errors:
        return semantic.blocking_errors[0][:1_000]
    visible_facts = "\n".join(
        [observation.accessibility_summary, *observation.dom_summary]
    ).lower()
    if any(marker in visible_facts for marker in _FATAL_RENDER_MARKERS):
        for line in observation.accessibility_summary.splitlines():
            cleaned = line.strip().lstrip("- ").strip('"')
            for name in (
                "TypeError:", "ReferenceError:", "RangeError:", "SyntaxError:"
            ):
                position = cleaned.find(name)
                if position >= 0:
                    return cleaned[position:].strip('"')[:1_000]
        return "The target application reported that rendering stopped"
    return None


def _is_ignorable_telemetry_abort(url: str, failure_text: str) -> bool:
    if "ERR_ABORTED" not in failure_text.upper():
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in TELEMETRY_ABORT_HOSTS)


def _bounded_semantic_components(value: Any) -> list[dict[str, Any]]:
    """Deduplicate component triggers and keep Portal options on one owner."""
    if not isinstance(value, list):
        return []
    deduplicated: list[dict[str, Any]] = []
    runtime_ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        runtime_id = str(item.get("runtimeId") or "")
        if runtime_id and runtime_id in runtime_ids:
            continue
        if runtime_id:
            runtime_ids.add(runtime_id)
        deduplicated.append(item)
        if len(deduplicated) >= 80:
            break

    owner_item: dict[str, Any] | None = None
    owner_score = -1
    for item in deduplicated:
        groups = item.get("visibleOptionGroups")
        options = item.get("visibleOptions")
        group_count = len(groups) if isinstance(groups, list) else 0
        option_count = len(options) if isinstance(options, list) else 0
        score = (10_000 if item.get("expanded") else 0) + group_count * 100 + option_count
        if group_count and score > owner_score:
            owner_item = item
            owner_score = score

    components = deduplicated[:MAX_SEMANTIC_COMPONENTS]
    if owner_item is not None and owner_item not in components:
        components = components[: MAX_SEMANTIC_COMPONENTS - 1] + [owner_item]
    option_owner = components.index(owner_item) if owner_item in components else None

    remaining_options = MAX_VISIBLE_COMPONENT_OPTIONS
    for index, item in enumerate(components):
        options = item.get("visibleOptions")
        groups = item.get("visibleOptionGroups")
        if option_owner is not None and index != option_owner:
            # Portal overlays are document-global. They must not be copied onto
            # every closed selector on the page.
            item["visibleOptions"] = []
            item["visibleOptionGroups"] = []
            if item.get("kind") != "native_select":
                item["optionCount"] = 0
            continue
        bounded_options = options[:remaining_options] if isinstance(options, list) else []
        remaining_options -= len(bounded_options)
        item["visibleOptions"] = bounded_options
        bounded_groups: list[dict[str, Any]] = []
        if isinstance(groups, list):
            for raw_group in groups[:12]:
                if not isinstance(raw_group, dict):
                    continue
                group = dict(raw_group)
                group_options = group.get("options")
                group["options"] = group_options[:40] if isinstance(group_options, list) else []
                if group["options"]:
                    bounded_groups.append(group)
        item["visibleOptionGroups"] = bounded_groups
    return components


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

    def capture(
        self,
        screenshot: str | None,
        *,
        detailed: bool = True,
        skip_canvas_readback: bool = False,
    ) -> Observation:
        url = self._safe_value(lambda: self.page.url, "about:blank")
        title = self._safe_value(self.page.title, "") if detailed else ""
        dom_summary = self._dom_summary() if detailed else []
        accessibility_summary = self._accessibility_summary() if detailed else ""
        semantic_summary = (
            self._semantic_summary(skip_canvas_readback=skip_canvas_readback)
            if detailed else None
        )
        if isinstance(semantic_summary, dict):
            canvas = semantic_summary.get("canvas")
            if isinstance(canvas, dict):
                visual_signature = self._canvas_signature(canvas_facts=canvas)
                if visual_signature:
                    canvas["signature"] = visual_signature
        console_errors, self._console_cursor = self._since(self._console, self._console_cursor)
        page_errors, self._page_error_cursor = self._since(self._page_errors, self._page_error_cursor)
        failed_requests, self._request_cursor = self._since(self._failed_requests, self._request_cursor)
        diagnostics = self._page_diagnostics() if detailed else {}
        observation = Observation(
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
            semantic_summary=(
                PageSemanticSummary(**self._redact_semantic(semantic_summary))
                if isinstance(semantic_summary, dict) else None
            ),
        )
        if observation.semantic_summary is not None:
            try:
                self.artifacts.event(
                    "page_semantic_scan",
                    page_key=observation.semantic_summary.page_key,
                    route=observation.semantic_summary.route,
                    signature=observation.semantic_summary.signature,
                    control_count=len(observation.semantic_summary.controls),
                    dialog_count=len(observation.semantic_summary.dialogs),
                    state_signals=observation.semantic_summary.state_signals,
                )
            except Exception:
                pass
        return observation

    def _canvas_signature(self, *, canvas_facts: dict[str, Any] | None = None) -> str:
        try:
            page_url = self._safe_value(lambda: getattr(self.page, "url", ""), "")
            page_host = (urlparse(page_url).hostname or "").lower()
            if page_host == "ion.cesium.com" and canvas_facts is not None:
                # The semantic scan already performed a bounded WebGL readback.
                # Reuse that value instead of touching the live Canvas again;
                # Cesium can block even a locator evaluate during camera inertia.
                payload = json.dumps(
                    canvas_facts, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                return hashlib.sha256(payload).hexdigest()[:16]
            canvases = self.page.locator("canvas:visible")
            if canvases.count() <= 0:
                return ""
            canvas = canvases.first
            payload = canvas.screenshot(
                animations="disabled",
                timeout=OBSERVATION_EVALUATE_TIMEOUT_MS,
            )
            return hashlib.sha256(payload).hexdigest()[:16]
        except (OSError, PlaywrightError):
            return ""

    def _cesium_canvas_facts(self, *, skip_canvas_readback: bool) -> dict[str, Any] | None:
        """Inspect the actual Cesium widget canvas, including closed shadow roots."""
        try:
            page_url = self._safe_value(lambda: getattr(self.page, "url", ""), "")
            if (urlparse(page_url).hostname or "").lower() != "ion.cesium.com":
                return None
            candidates = self.page.locator(".cesium-widget canvas")
            count = min(candidates.count(), MAX_CESIUM_CANVAS_TARGETS)
            if count <= 0:
                return None
            surfaces: list[dict[str, Any]] = []
            for index in range(count):
                candidate = candidates.nth(index)
                fact = candidate.evaluate(
                    """(element, options) => {
                      const rect = element.getBoundingClientRect();
                      const style = getComputedStyle(element);
                      const visible = rect.width > 1 && rect.height > 1 &&
                        style.display !== 'none' && style.visibility !== 'hidden' &&
                        Number(style.opacity || '1') > 0.01;
                      let webgl = false;
                      let webglVersion = '';
                      let vendor = '';
                      let renderer = '';
                      let contextLost = false;
                      let nonEmptyPixels = false;
                      let pixelSignature = 0;
                      const mixPixels = values => {
                        for (const value of values) {
                          pixelSignature = (Math.imul(pixelSignature, 31) + value) >>> 0;
                        }
                      };
                      try {
                        const gl2 = element.getContext('webgl2');
                        const gl = gl2 || element.getContext('webgl');
                        webgl = Boolean(gl);
                        webglVersion = gl2 ? 'webgl2' : gl ? 'webgl' : '';
                        if (gl) {
                          contextLost = Boolean(gl.isContextLost?.());
                          const debug = gl.getExtension('WEBGL_debug_renderer_info');
                          if (debug) {
                            vendor = String(gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) || '').slice(0, 120);
                            renderer = String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) || '').slice(0, 160);
                          }
                          if (!options.skipWebglReadback && !contextLost) {
                            const width = Math.min(8, Math.max(1, gl.drawingBufferWidth || 1));
                            const height = Math.min(8, Math.max(1, gl.drawingBufferHeight || 1));
                            const pixels = new Uint8Array(width * height * 4);
                            gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
                            mixPixels(pixels);
                            nonEmptyPixels = Array.from(pixels).some(value => value > 0);
                          }
                        }
                      } catch (_) {}
                      return {
                        visible,
                        width: Math.round(rect.width), height: Math.round(rect.height),
                        nativeWidth: Number(element.width || 0), nativeHeight: Number(element.height || 0),
                        webgl, webglVersion, vendor, renderer, contextLost, nonEmptyPixels,
                        pixelSignature: pixelSignature.toString(16),
                        bounds: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
                      };
                    }""",
                    {"skipWebglReadback": skip_canvas_readback},
                    timeout=OBSERVATION_EVALUATE_TIMEOUT_MS,
                )
                if isinstance(fact, dict) and fact.get("visible"):
                    surfaces.append(fact)
            if not surfaces:
                return None
            surfaces.sort(
                key=lambda item: (
                    bool(item.get("webgl")),
                    int(item.get("width", 0)) * int(item.get("height", 0)),
                    bool(item.get("nonEmptyPixels")),
                ),
                reverse=True,
            )
            target = surfaces[0]
            viewport = getattr(self.page, "viewport_size", None) or {}
            return {
                "count": len(surfaces),
                "surfaces": [
                    {key: value for key, value in item.items() if key != "visible"}
                    for item in surfaces
                ],
                "nonEmptySurface": any(
                    int(item.get("width", 0)) > 20 and int(item.get("height", 0)) > 20
                    for item in surfaces
                ),
                "webglSurfaceCount": sum(bool(item.get("webgl")) for item in surfaces),
                "nonEmptyPixels": any(bool(item.get("nonEmptyPixels")) for item in surfaces),
                "contextLost": any(bool(item.get("contextLost")) for item in surfaces),
                "pixelSignature": "|".join(str(item.get("pixelSignature") or "") for item in surfaces),
                "renderingEvidence": (
                    "webgl_non_empty_pixels"
                    if any(item.get("webgl") and item.get("nonEmptyPixels") for item in surfaces)
                    else "surface_dimensions_only"
                ),
                "targetSelector": ".cesium-widget canvas",
                "targetFrameUrl": page_url,
                "targetBounds": target.get("bounds"),
                "targetViewport": {
                    "width": int(viewport.get("width") or 0),
                    "height": int(viewport.get("height") or 0),
                },
            }
        except (OSError, PlaywrightError, TypeError, ValueError):
            return None

    def _semantic_summary(self, *, skip_canvas_readback: bool = False) -> dict[str, Any] | None:
        """Collect page meaning and value-state signals without raw input values."""
        try:
            expression = """() => {
                  const normalize = value => String(value || '').replace(/\\s+/g, ' ').trim();
                  // Playwright pierces open Shadow DOM, but document-level
                  // querySelectorAll does not. Walk open roots so the planner
                  // receives the same controls that execution can reach.
                  const roots = [];
                  const collectRoot = root => {
                    if (!root || roots.length >= 48 || roots.includes(root)) return;
                    roots.push(root);
                    for (const host of Array.from(root.querySelectorAll('*'))) {
                      if (host.shadowRoot) collectRoot(host.shadowRoot);
                      if (roots.length >= 48) break;
                    }
                  };
                  collectRoot(document);
                  const queryAll = selector => roots.flatMap(root => Array.from(root.querySelectorAll(selector)));
                  const visible = el => {
                    if (!(el instanceof Element) || el.hidden || el.closest('template')) return false;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 1 || rect.height <= 1) return false;
                    let current = el;
                    for (let depth = 0; current instanceof Element && depth < 32; depth += 1) {
                      const style = getComputedStyle(current);
                      const opacity = Number.parseFloat(style.opacity || '1');
                      if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) ||
                          (!Number.isNaN(opacity) && opacity <= 0.01) ||
                          current.getAttribute('aria-hidden') === 'true' || current.hasAttribute('inert')) return false;
                      const root = current.getRootNode();
                      current = current.parentElement || (root instanceof ShadowRoot ? root.host : null);
                    }
                    return true;
                  };
                  const roleOf = el => el.getAttribute('role') || (
                    el.matches('button,input[type="button"],input[type="submit"]') ? 'button' :
                    el.matches('a[href]') ? 'link' : el.matches('select') ? 'combobox' :
                    el.matches('textarea,input:not([type]),input[type="text"],input[type="search"],input[type="password"],[contenteditable="true"]') ? 'textbox' :
                    el.matches('input[type="checkbox"]') ? 'checkbox' : el.matches('input[type="radio"]') ? 'radio' : ''
                  );
                  const nameOf = el => normalize(
                    el.getAttribute('aria-label') || el.getAttribute('title') ||
                    (el.labels && el.labels[0] && el.labels[0].innerText) ||
                    (el.matches('input,textarea,select') ? el.getAttribute('placeholder') : '') ||
                    el.innerText || el.textContent
                  ).slice(0, 120);
                  const describedTextOf = el => {
                    const ids = normalize(el.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
                    const described = ids.map(id => document.getElementById(id)?.innerText || '').join(' ');
                    const localHelp = el.closest('.ant-form-item,[class*="form-item" i],[class*="field" i]')
                      ?.querySelector('.ant-form-item-explain,[role="alert"],[class*="error" i],[class*="help" i]')?.innerText || '';
                    return normalize(described || localHelp || el.getAttribute('title') || '').slice(0, 240);
                  };
                  const requiredOf = el => Boolean(el.required) || el.getAttribute('aria-required') === 'true';
                  window.__AI_GUI_RUNTIME_ID_SEQUENCE__ = Number(window.__AI_GUI_RUNTIME_ID_SEQUENCE__ || 0);
                  const runtimeIdOf = el => {
                    let value = el.getAttribute('data-ai-gui-runtime-id');
                    if (!value) {
                      window.__AI_GUI_RUNTIME_ID_SEQUENCE__ += 1;
                      value = `ai_${window.__AI_GUI_RUNTIME_ID_SEQUENCE__}`;
                      el.setAttribute('data-ai-gui-runtime-id', value);
                    }
                    return value;
                  };
                  const valueStateOf = el => {
                    if (!el.matches('input,textarea,select,[contenteditable="true"]')) return '';
                    if (el.matches('input[type="password"],input[type="hidden"]')) return 'redacted';
                    if (el.matches('input[type="checkbox"],input[type="radio"]')) {
                      return Boolean(el.checked) ? 'non_empty' : 'empty';
                    }
                    const current = 'value' in el ? Reflect.get(el, 'value') : el.textContent;
                    return normalize(current) ? 'non_empty' : 'empty';
                  };
                  const invalidOf = el => el.getAttribute('aria-invalid') === 'true' ||
                    (typeof el.checkValidity === 'function' && !el.checkValidity());
                  const headings = queryAll('h1,h2,h3,h4,[role="heading"]')
                    .filter(visible).map(nameOf).filter(Boolean).slice(0, 30);
                  const regions = queryAll('header,nav,main,aside,section,form,footer,[role="main"],[role="navigation"],[role="region"]')
                    .filter(visible).map(el => ({ tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '', name: nameOf(el) })).slice(0, 40);
                  const dialogs = queryAll('[role="dialog"],.ant-modal,.ant-modal-wrap,[class*="modal" i],[class*="dialog" i]')
                    .filter(visible).map(el => ({ role: el.getAttribute('role') || '', name: nameOf(el), text: normalize(el.innerText || '').slice(0, 240) })).slice(0, 12);
                  const controls = queryAll('button,a[href],input,select,textarea,[contenteditable="true"],[role]')
                    .filter(visible).map(el => ({
                      runtimeId: runtimeIdOf(el),
                      role: roleOf(el), name: nameOf(el),
                      testId: el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-qa') || '',
                      href: el.matches('a[href]') ? el.getAttribute('href') || '' : '',
                      disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
                      required: requiredOf(el),
                      valueState: valueStateOf(el),
                      invalid: invalidOf(el),
                      validationMessage: normalize(Reflect.get(el, 'validationMessage') || '').slice(0, 200),
                      disabledReason: describedTextOf(el),
                      selected: el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-current') === 'page' ||
                        /active|selected|current/i.test(String(el.className || '')),
                      checked: 'checked' in el ? Boolean(el.checked) : el.getAttribute('aria-checked') === 'true'
                    })).filter(item => item.role || item.name).slice(0, 160);
                  const forms = queryAll('form').filter(visible).map(form => ({
                    name: nameOf(form),
                    controls: form.querySelectorAll('input,select,textarea,[contenteditable="true"]').length,
                    submitButtons: form.querySelectorAll('button[type="submit"],input[type="submit"]').length
                  })).slice(0, 20);
                  const uniqueOptions = items => {
                    const seen = new Set();
                    return items.filter(item => {
                      const key = `${item.text}\u0000${item.selected}\u0000${item.disabled}`;
                      if (seen.has(key)) return false;
                      seen.add(key);
                      return true;
                    });
                  };
                  const optionOf = el => ({
                    runtimeId: runtimeIdOf(el),
                    text: normalize(el.innerText || el.textContent || '').slice(0, 160),
                    selected: el.getAttribute('aria-selected') === 'true' ||
                      el.classList.contains('ant-cascader-menu-item-active') ||
                      el.classList.contains('ant-select-item-option-selected'),
                    disabled: el.getAttribute('aria-disabled') === 'true' ||
                      el.classList.contains('ant-select-item-option-disabled')
                  });
                  const visibleOptions = uniqueOptions(queryAll(
                     '[role="option"],.ant-cascader-menu-item,.ant-select-item-option,[class*="option" i]'
                  ).filter(visible).map(optionOf).filter(item => item.text)).slice(0, 120);
                  const visibleOptionGroups = queryAll(
                     '.ant-cascader-menu,[role="listbox"],.ant-select-dropdown,[class*="option-list" i]'
                  ).filter(visible).map((panel, groupIndex) => ({
                    groupIndex,
                    options: uniqueOptions(Array.from(panel.querySelectorAll(
                       '[role="option"],.ant-cascader-menu-item,.ant-select-item-option,[class*="option" i]'
                    )).filter(visible).map(optionOf).filter(item => item.text)).slice(0, 40)
                  })).filter(group => group.options.length).slice(0, 12);
                  const controlLabel = el => {
                    const labelled = el.getAttribute('aria-label') || el.getAttribute('placeholder');
                    if (labelled) return normalize(labelled).slice(0, 160);
                    const label = el.labels && el.labels[0];
                    if (label) return normalize(label.innerText || label.textContent).slice(0, 160);
                    const parent = el.closest('form,.ant-form-item,[class*="form-item" i],[class*="field" i]');
                    return normalize(parent?.innerText || '').split(/\\n/).map(item => item.trim()).filter(Boolean)[0]?.slice(0, 160) || '';
                  };
                  const componentSelectors = [
                    'select', '[role="combobox"]', '.ant-select', '.ant-cascader-picker',
                    '.ant-select-selector', '[data-testid][aria-haspopup="listbox"]'
                  ].join(',');
                  const dialogCandidates = queryAll(
                    '[role="dialog"],.ant-modal,.ant-modal-wrap,.modal-box,[class*="modal-box" i],[class*="modal-content" i],[class*="dialog-content" i],[class*="modal" i],[class*="dialog" i]'
                  ).filter(visible);
                  const dialogScore = el => (
                    el.querySelectorAll(componentSelectors).length * 1000 +
                    el.querySelectorAll('button,input,select,textarea,[role="button"],[role="combobox"]').length * 10 +
                    Math.min(normalize(el.innerText || '').length, 1000)
                  );
                  const activeDialog = dialogCandidates.sort((left, right) => dialogScore(right) - dialogScore(left))[0];
                  const componentScope = activeDialog || document;
                  const componentFacts = [];
                  const seenRuntimeIds = new Set();
                  const componentCandidates = activeDialog
                    ? Array.from(activeDialog.querySelectorAll(componentSelectors))
                    : queryAll(componentSelectors);
                  for (const candidate of componentCandidates.filter(visible)) {
                    const root = candidate.closest('.ant-select,.ant-cascader-picker') || candidate;
                    const trigger = root.matches('select,[role="combobox"],.ant-select-selector') ? root :
                      (root.querySelector('.ant-select-selector,[role="combobox"],select') || root);
                    const runtimeId = runtimeIdOf(trigger);
                    if (seenRuntimeIds.has(runtimeId)) continue;
                    seenRuntimeIds.add(runtimeId);
                    const rootClass = String(root.className || '');
                    const kind = trigger.matches('select') ? 'native_select' :
                      (/cascader/i.test(rootClass) ? 'cascader' : 'searchable_select');
                    const nativeOptions = trigger.matches('select') ? uniqueOptions(
                      Array.from(trigger.options || []).map(option => ({
                        runtimeId: runtimeIdOf(option), text: normalize(option.label || option.textContent || '').slice(0, 160),
                        selected: Boolean(option.selected), disabled: Boolean(option.disabled)
                      })).filter(item => item.text)
                    ).slice(0, 40) : [];
                    const selectedNode = root.querySelector('.ant-select-selection-item,.ant-cascader-picker-label');
                    const selectedText = trigger.matches('select') ?
                      normalize(trigger.selectedOptions?.[0]?.label || '') :
                      normalize(selectedNode?.textContent || trigger.innerText || trigger.textContent || '').slice(0, 200);
                    const rect = trigger.getBoundingClientRect();
                    componentFacts.push({
                      kind, label: controlLabel(trigger), runtimeId,
                      role: trigger.getAttribute('role') || '',
                      placeholder: normalize(trigger.getAttribute('placeholder') || '').slice(0, 160),
                      expanded: trigger.getAttribute('aria-expanded') === 'true' || root.getAttribute('aria-expanded') === 'true' ||
                        Boolean(root.querySelector('[aria-expanded="true"]')),
                      required: root.hasAttribute('required') || Boolean(root.querySelector('[required]')) || /\\*/.test(normalize(root.parentElement?.innerText || '').slice(0, 180)),
                      selectedText, optionCount: nativeOptions.length,
                      visibleOptions: nativeOptions, visibleOptionGroups: [],
                      testId: root.getAttribute('data-testid') || root.getAttribute('data-test') || root.getAttribute('data-qa') || '',
                      _rect: { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom }
                    });
                    if (componentFacts.length >= 30) break;
                  }
                  let optionOwnerIndex = componentFacts.findIndex(item => item.expanded);
                  if (optionOwnerIndex < 0 && visibleOptionGroups.length && componentFacts.length) {
                    const panel = queryAll(
                      '.ant-cascader-menu,[role="listbox"],.ant-select-dropdown'
                    ).filter(visible)[0];
                    const panelRect = panel?.getBoundingClientRect();
                    if (panelRect) {
                      let bestScore = Number.POSITIVE_INFINITY;
                      componentFacts.forEach((item, index) => {
                        const rect = item._rect;
                        const horizontalGap = Math.max(0, rect.left - panelRect.right, panelRect.left - rect.right);
                        const verticalGap = Math.abs(panelRect.top - rect.bottom);
                        const score = horizontalGap + verticalGap;
                        if (score < bestScore) { bestScore = score; optionOwnerIndex = index; }
                      });
                    }
                  }
                  if (optionOwnerIndex >= 0) {
                    componentFacts[optionOwnerIndex].visibleOptions = visibleOptions.slice(0, 120);
                    componentFacts[optionOwnerIndex].visibleOptionGroups = visibleOptionGroups;
                    componentFacts[optionOwnerIndex].optionCount = visibleOptions.length;
                    componentFacts[optionOwnerIndex].expanded = true;
                  }
                  const components = componentFacts.map((item, index) => {
                    const { _rect, ...fact } = item;
                    return { index, ...fact };
                  });
                  const wizardNodes = queryAll(
                    '[aria-current="step"],[role="listitem"],[class*="step" i],[class*="steps" i]'
                  ).filter(visible).map((el, index) => ({
                    index, text: normalize(el.innerText || el.textContent || '').slice(0, 160),
                    current: el.getAttribute('aria-current') === 'step' ||
                      /active|current|process/i.test(String(el.className || ''))
                  })).filter(item => item.text).slice(0, 30);
                  const wizardText = normalize(
                    activeDialog?.innerText || dialogCandidates.map(el => el.innerText || '').join(' ')
                  );
                  const wizard = {
                    visible: dialogs.length > 0,
                    steps: wizardNodes,
                    activeStep: wizardNodes.find(item => item.current)?.text || '',
                    text: wizardText.slice(0, 1200),
                    stepLabels: ['选择类型', '关键', '基本信息', '确认创建'].filter(label => wizardText.includes(label)),
                    blockingControls: controls.filter(item => item.required && (item.valueState === 'empty' || item.invalid))
                      .map(item => ({ role: item.role, name: item.name, valueState: item.valueState, invalid: item.invalid,
                        validationMessage: item.validationMessage })).slice(0, 30),
                    disabledActions: controls.filter(item => item.role === 'button' && item.disabled)
                      .map(item => ({ name: item.name, disabledReason: item.disabledReason })).slice(0, 30)
                  };
                  const canvasNodes = queryAll('canvas').filter(visible).map((el, index) => {
                    const rect = el.getBoundingClientRect();
                    let webgl = false;
                    let webglVersion = '';
                    let vendor = '';
                    let renderer = '';
                    let contextLost = false;
                    let nonEmptyPixels = false;
                    let pixelSignature = 0;
                    const mixPixels = values => {
                      for (const value of values) pixelSignature = (Math.imul(pixelSignature, 31) + value) >>> 0;
                    };
                    try {
                      const gl2 = el.getContext('webgl2');
                      const gl = gl2 || el.getContext('webgl');
                      webgl = Boolean(gl);
                      webglVersion = gl2 ? 'webgl2' : gl ? 'webgl' : '';
                      if (gl) {
                        contextLost = Boolean(gl.isContextLost?.());
                        const debug = gl.getExtension('WEBGL_debug_renderer_info');
                        if (debug) {
                          vendor = String(gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) || '').slice(0, 120);
                          renderer = String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) || '').slice(0, 160);
                        }
                        if (!skipWebglReadback) {
                          const sampleWidth = Math.min(8, Math.max(1, gl.drawingBufferWidth || 1));
                          const sampleHeight = Math.min(8, Math.max(1, gl.drawingBufferHeight || 1));
                          const pixels = new Uint8Array(sampleWidth * sampleHeight * 4);
                          gl.readPixels(0, 0, sampleWidth, sampleHeight, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
                          mixPixels(pixels);
                          nonEmptyPixels = Array.from(pixels).some(value => value > 0);
                        }
                      }
                    } catch (_) {}
                    if (!webgl) {
                      try {
                        const ctx = el.getContext('2d');
                        if (ctx) {
                          const pixels = ctx.getImageData(0, 0, Math.min(8, el.width || 1), Math.min(8, el.height || 1)).data;
                          mixPixels(pixels);
                          nonEmptyPixels = Array.from(pixels).some(value => value > 0);
                        }
                      } catch (_) {}
                    }
                    return { index, width: Math.round(rect.width), height: Math.round(rect.height), webgl,
                      webglVersion, vendor, renderer, contextLost, nonEmptyPixels,
                      pixelSignature: pixelSignature.toString(16) };
                  });
                  const canvas = {
                    count: canvasNodes.length,
                    surfaces: canvasNodes,
                    nonEmptySurface: canvasNodes.some(item => item.width > 20 && item.height > 20),
                    webglSurfaceCount: canvasNodes.filter(item => item.webgl).length,
                    nonEmptyPixels: canvasNodes.some(item => item.nonEmptyPixels),
                    contextLost: canvasNodes.some(item => item.contextLost),
                    pixelSignature: canvasNodes.map(item => item.pixelSignature || '').join('|'),
                    renderingEvidence: canvasNodes.some(item => item.webgl && item.nonEmptyPixels)
                      ? 'webgl_non_empty_pixels'
                      : canvasNodes.some(item => item.nonEmptyPixels)
                        ? 'canvas_non_empty_pixels'
                        : canvasNodes.some(item => item.width > 20 && item.height > 20)
                          ? 'surface_dimensions_only' : 'none',
                    semanticBridge: typeof window.__WEB_AI_TEST__ === 'object'
                  };
                  const extractTestNames = value => Array.from(
                    String(value || '').matchAll(/(?:^|[^A-Za-z0-9_])((?:scenario_)?test_[A-Z]+)(?=$|[^A-Za-z0-9_])/g),
                    match => match[1]
                  );
                  const resourceListRoute = /(?:scenarioTable|mineScenarioList|minePlanList|mineModelList)/i.test(
                    location.hash || location.pathname
                  );
                  const generalResourceNames = resourceListRoute ? queryAll(
                    '.ant-card-meta-title,.ant-card-head-title,[class*="card-title" i],'
                    + '[class*="resource-name" i],[data-resource-name]'
                  ).filter(visible).map(el => normalize(
                    el.getAttribute('data-resource-name') || el.innerText || el.textContent || ''
                  )).filter(name => name && name.length <= 120 && !/^(?:修改|删除|详情|预览|复制|编辑|保存)$/i.test(name)) : [];
                  const resourceNames = Array.from(new Set([
                    ...Array.from(document.querySelectorAll('option')).flatMap(option =>
                      extractTestNames(option.textContent || option.label || '')
                    ),
                    ...extractTestNames(document.body?.innerText || ''),
                    ...generalResourceNames
                  ])).sort().slice(0, 2000);
                  const editorSpecs = [
                    ['child_entities', '\u5b50\u7ea7\u5b9e\u4f53'],
                    ['formal_model', '\u5f62\u5f0f\u5316\u6a21\u578b'],
                    ['dynamics', '\u52a8\u529b\u5b66'],
                    ['sensing_range', '\u611f\u77e5\u8303\u56f4'],
                    ['communication_stack', '\u901a\u4fe1\u534f\u8bae\u6808'],
                    ['mission_path', '\u4efb\u52a1\u8def\u5f84'],
                    ['poi', '\u5174\u8da3\u70b9'],
                    ['time_space_annotation', '\u65f6\u7a7a\u6807\u6ce8'],
                    ['constraint_fence', '\u7ea6\u675f\u56f4\u680f'],
                    ['parameters', '\u53c2\u6570\u5b9a\u4e49'],
                    ['actions_commands', '\u52a8\u4f5c\u6307\u4ee4'],
                    ['ooda', '\u611f\u77e5\u884c\u4e3a'],
                    ['behavior_tree', '\u884c\u4e3a\u6811'],
                    ['doctrine', '\u6761\u4ee4'],
                    ['cognition', '\u8ba4\u77e5\u6a21\u578b'],
                    ['evaluation_metrics', '\u8bc4\u4f30\u6307\u6807']
                  ];
                  const visibleText = normalize(roots.map(root => {
                    // Document.textContent is null by specification; use the
                    // rendered body text for the top-level document and keep
                    // textContent for shadow roots.
                    if (root === document) return document.body?.innerText || document.body?.textContent || '';
                    return root instanceof Element ? (root.innerText || root.textContent || '') : (root.textContent || '');
                  }).join(' '));
                  canvas.modelEditor = {
                    visible: /agentEditPage/i.test(location.href) || editorSpecs.some(([, label]) => visibleText.includes(label)),
                    sections: editorSpecs.filter(([, label]) => visibleText.includes(label)).map(([id, label]) => ({ id, label })),
                    missionPathSignals: queryAll('button,[role="button"],[title],[aria-label]')
                      .filter(visible).map(el => nameOf(el)).filter(name =>
                        /\u8def\u5f84|\u8f68\u8ff9|\u822a\u70b9|path|route|waypoint/i.test(name)
                      ).slice(0, 30),
                    excludedDetailTests: ['actions_commands.parameter_configuration']
                  };
                  const stateSignals = [];
                  const bodyText = visibleText;
                  const fatalMarkers = [
                    'An error occurred while rendering. Rendering has stopped.',
                    'Application error: a client-side exception has occurred',
                    'A client-side exception has occurred'
                  ];
                  const blockingErrors = [];
                  if (fatalMarkers.some(marker => bodyText.toLowerCase().includes(marker.toLowerCase()))) {
                    const detail = bodyText.match(/(?:TypeError|ReferenceError|RangeError|SyntaxError):.{0,500}/i)?.[0] || '';
                    blockingErrors.push((detail || fatalMarkers.find(marker =>
                      bodyText.toLowerCase().includes(marker.toLowerCase())) || 'Application rendering stopped').slice(0, 600));
                    stateSignals.push('fatal_render_error');
                  }
                  const loadingSelectors =
                    '[aria-busy="true"],.loading,.loading-message,.page-loading-placeholder,.spinner,[class*="skeleton" i]';
                  if (queryAll(loadingSelectors).some(visible)) stateSignals.push('loading');
                  if (dialogs.length) stateSignals.push('modal_visible');
                  if (queryAll('[role="alert"],[role="status"],[class*="toast" i]').some(visible)) stateSignals.push('notification_visible');
                  if (queryAll('[disabled], [aria-disabled="true"]').some(visible)) stateSignals.push('disabled_controls');
                  if (queryAll('canvas,webgl').some(visible)) stateSignals.push('canvas_or_webgl');
                  if (components.some(item => item.expanded)) stateSignals.push('component_expanded');
                  if (wizard.visible) stateSignals.push('wizard_visible');
                  return {
                    route: `${location.pathname}${location.hash || ''}`,
                    heading: headings[0] || document.title || '', headings, regions, dialogs, controls, forms,
                    components, wizard, canvas, resourceNames, blockingErrors,
                    stateSignals
                  };
                }"""
            expression = expression.replace(
                "const canvasNodes =",
                f"const skipWebglReadback = {'true' if skip_canvas_readback else 'false'};\n                  const canvasNodes =",
                1,
            )
            result = self._bounded_document_evaluate(expression)
            if not isinstance(result, dict):
                return None
            cesium_canvas = self._cesium_canvas_facts(
                skip_canvas_readback=skip_canvas_readback
            )
            if cesium_canvas is not None:
                # The locator can pierce a closed shadow root where the
                # document-level semantic scan cannot. Prefer the bound target
                # so auxiliary logo/scale canvases are never treated as 3D proof.
                result["canvas"] = cesium_canvas
            result["components"] = _bounded_semantic_components(result.get("components", []))
            identity = {
                "route": result.get("route", ""),
                "heading": result.get("heading", ""),
                "headings": result.get("headings", [])[:10],
                "dialogs": result.get("dialogs", [])[:4],
                "controls": [
                    {key: item.get(key) for key in (
                        "role", "name", "testId", "href", "disabled", "required",
                        "valueState", "invalid", "selected", "checked"
                    )}
                    for item in result.get("controls", [])[:80]
                    if isinstance(item, dict)
                ],
                "components": result.get("components", [])[:MAX_SEMANTIC_COMPONENTS],
                "wizard": result.get("wizard", {}),
                "canvas": result.get("canvas", {}),
                "resource_names": result.get("resourceNames", [])[:2000],
                "blocking_errors": result.get("blockingErrors", [])[:5],
            }
            result["page_key"] = f"{result.get('route', '')}|{result.get('heading', '')}"[:500]
            result["resource_names"] = result.pop("resourceNames", [])[:2000]
            result["blocking_errors"] = result.pop("blockingErrors", [])[:5]
            result["state_signals"] = result.pop("stateSignals", [])
            result["signature"] = hashlib.sha256(
                json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()[:16]
            return result
        except Exception:
            return None

    def _redact_semantic(self, value: dict[str, Any]) -> dict[str, Any]:
        def scrub(item):
            if isinstance(item, dict):
                return {key: scrub(child) for key, child in item.items()}
            if isinstance(item, list):
                return [scrub(child) for child in item]
            return self.redactor.scrub(str(item)) if isinstance(item, str) else item
        return scrub(value)

    def _page_diagnostics(self) -> dict:
        try:
            result = self._bounded_document_evaluate(
                """() => {
                  const issues = [];
                  const viewport = { width: window.innerWidth, height: window.innerHeight };
                  const visible = (el) => {
                    if (!(el instanceof Element) || el.hidden || el.closest('template')) return false;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 1 || rect.height <= 1 || rect.bottom <= 0 || rect.right <= 0 ||
                        rect.top >= viewport.height || rect.left >= viewport.width) return false;
                    let current = el;
                    for (let depth = 0; current instanceof Element && depth < 32; depth += 1) {
                      const style = getComputedStyle(current);
                      const opacity = Number.parseFloat(style.opacity || '1');
                      if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) ||
                          (!Number.isNaN(opacity) && opacity <= 0.01) ||
                          current.getAttribute('aria-hidden') === 'true' || current.hasAttribute('inert')) return false;
                      const root = current.getRootNode();
                      current = current.parentElement || (root instanceof ShadowRoot ? root.host : null);
                    }
                    return true;
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
                    const nativeDisabled = 'disabled' in el && Boolean(el.disabled);
                    const ariaDisabled = el.getAttribute('aria-disabled') === 'true';
                    const disabledAncestor = el.closest('fieldset[disabled],[aria-disabled="true"]');
                    if (nativeDisabled || ariaDisabled || disabledAncestor) continue;
                    const rect = el.getBoundingClientRect();
                    const x = Math.max(0, Math.min(viewport.width - 1, rect.left + rect.width / 2));
                    const y = Math.max(0, Math.min(viewport.height - 1, rect.top + rect.height / 2));
                    const shallowTop = document.elementFromPoint(x, y);
                    let top = shallowTop;
                    for (let depth = 0; top?.shadowRoot && depth < 12; depth += 1) {
                      const nested = top.shadowRoot.elementFromPoint(x, y);
                      if (!nested || nested === top) break;
                      top = nested;
                    }
                    let shadowHostProxy = false;
                    if (shallowTop && top !== el) {
                      let root = el.getRootNode();
                      for (let depth = 0; root instanceof ShadowRoot && depth < 12; depth += 1) {
                        if (root.host === shallowTop) {
                          shadowHostProxy = true;
                          break;
                        }
                        root = root.host.getRootNode();
                      }
                    }
                    const transientStatusLayer = top?.closest('[role="status"],[aria-busy="true"]');
                    if (top && !transientStatusLayer && !shadowHostProxy &&
                        top !== el && !el.contains(top) && !top.contains(el)) {
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
                  const visibleText = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                  const fatalRenderMarkers = [
                    'An error occurred while rendering. Rendering has stopped.',
                    'Application error: a client-side exception has occurred',
                    'A client-side exception has occurred'
                  ];
                  if (fatalRenderMarkers.some(marker => visibleText.toLowerCase().includes(marker.toLowerCase()))) {
                    const detail = visibleText.match(/(?:TypeError|ReferenceError|RangeError|SyntaxError):.{0,500}/i)?.[0] || '';
                    issues.push({
                      kind: 'fatal_render_error', severity: 'High', confidence: 'high',
                      message: 'The target application reported a fatal client-side rendering error',
                      target: 'document', details: { error: detail.slice(0, 600) }
                    });
                  }
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
            result = self._bounded_document_evaluate(
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

    def _bounded_document_evaluate(self, expression: str):
        return self.page.locator("html").evaluate(
            expression,
            timeout=OBSERVATION_EVALUATE_TIMEOUT_MS,
        )

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
            if request.resource_type == "media" and "ERR_ABORTED" in failure_text:
                return
            if _is_ignorable_telemetry_abort(request.url, failure_text):
                return
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
