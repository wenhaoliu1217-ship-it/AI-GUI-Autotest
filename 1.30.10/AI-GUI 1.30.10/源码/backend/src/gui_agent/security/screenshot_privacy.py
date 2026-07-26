"""Pixel-level privacy masks applied only while browser evidence is captured."""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4


DEFAULT_SCREENSHOT_MASK_SELECTORS = (
    'input[type="password"]',
    'input[autocomplete="current-password"]',
    'input[autocomplete="new-password"]',
    '[data-sensitive="true"]',
    '[data-private="true"]',
)

_INSTALL_MASKS = """
({ selectors, token }) => {
  let maskedCount = 0;
  const invalidSelectors = [];
  for (const selector of selectors) {
    let elements;
    try {
      elements = document.querySelectorAll(selector);
    } catch (_) {
      invalidSelectors.push(selector);
      continue;
    }
    for (const element of elements) {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      const mask = document.createElement('div');
      mask.setAttribute('data-gui-agent-privacy-mask', token);
      mask.setAttribute('aria-hidden', 'true');
      mask.style.cssText = [
        'position:fixed',
        `left:${rect.left}px`,
        `top:${rect.top}px`,
        `width:${rect.width}px`,
        `height:${rect.height}px`,
        'margin:0',
        'padding:0',
        'border:0',
        'border-radius:0',
        'background:#111',
        'box-shadow:none',
        'filter:none',
        'opacity:1',
        'pointer-events:none',
        'z-index:2147483647'
      ].join(';');
      (document.documentElement || document.body).appendChild(mask);
      maskedCount += 1;
    }
  }
  return { maskedCount, invalidSelectors };
}
"""

_REMOVE_MASKS = """
(token) => {
  for (const mask of document.querySelectorAll('[data-gui-agent-privacy-mask]')) {
    if (mask.getAttribute('data-gui-agent-privacy-mask') === token) mask.remove();
  }
}
"""


def normalize_mask_selectors(selectors: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    values = (*DEFAULT_SCREENSHOT_MASK_SELECTORS, *selectors)
    return tuple(dict.fromkeys(item.strip() for item in values if item.strip()))[:100]


@contextmanager
def screenshot_privacy_masks(page, selectors: tuple[str, ...] | list[str]):
    """Cover matching elements in every attached frame, then restore the page."""
    token = f"privacy-{uuid4().hex}"
    normalized = normalize_mask_selectors(selectors)
    installed_frames = []
    masked_count = 0
    invalid_selectors: set[str] = set()
    for frame in list(page.frames):
        try:
            result = frame.evaluate(
                _INSTALL_MASKS,
                {"selectors": normalized, "token": token},
            )
        except Exception:
            continue
        installed_frames.append(frame)
        if isinstance(result, dict):
            masked_count += int(result.get("maskedCount", 0))
            invalid_selectors.update(str(item) for item in result.get("invalidSelectors", []))
    try:
        yield {
            "masked_count": masked_count,
            "invalid_selectors": sorted(invalid_selectors),
            "selector_count": len(normalized),
        }
    finally:
        for frame in installed_frames:
            try:
                frame.evaluate(_REMOVE_MASKS, token)
            except Exception:
                pass
