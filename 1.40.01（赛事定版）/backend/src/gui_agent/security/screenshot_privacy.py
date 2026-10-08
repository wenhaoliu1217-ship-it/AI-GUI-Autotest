"""Pixel-level privacy masks applied only while browser evidence is captured."""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


MASK_INSTALL_TIMEOUT_MS = 500
MAX_SCREENSHOT_MASK_FRAMES = 10


DEFAULT_SCREENSHOT_MASK_SELECTORS = (
    'input[type="password"]',
    'input[autocomplete="current-password"]',
    'input[autocomplete="new-password"]',
    '[data-sensitive="true"]',
    '[data-private="true"]',
    'textarea[name*="token" i]',
    'textarea[id*="token" i]',
    '[class*="token" i] textarea',
    '[class*="secret" i] textarea',
)

_INSTALL_MASKS = """
(root, { selectors, token }) => {
  const document = root.ownerDocument;
  const view = document.defaultView;
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
      view.setTimeout(() => mask.remove(), 5000);
      maskedCount += 1;
    }
  }
  return { maskedCount, invalidSelectors };
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
    masked_count = 0
    invalid_selectors: set[str] = set()
    frames = list(page.frames)
    timed_out_frame_count = 0
    failed_frame_count = 0
    for frame in frames[:MAX_SCREENSHOT_MASK_FRAMES]:
        try:
            result = frame.locator("html").evaluate(
                _INSTALL_MASKS,
                {"selectors": normalized, "token": token},
                timeout=MASK_INSTALL_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError:
            timed_out_frame_count += 1
            continue
        except Exception:
            failed_frame_count += 1
            continue
        if isinstance(result, dict):
            masked_count += int(result.get("maskedCount", 0))
            invalid_selectors.update(str(item) for item in result.get("invalidSelectors", []))
    yield {
        "masked_count": masked_count,
        "invalid_selectors": sorted(invalid_selectors),
        "selector_count": len(normalized),
        "timed_out_frame_count": timed_out_frame_count,
        "failed_frame_count": failed_frame_count,
        "skipped_frame_count": max(0, len(frames) - MAX_SCREENSHOT_MASK_FRAMES),
    }
