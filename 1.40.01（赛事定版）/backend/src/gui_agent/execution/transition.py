"""Bounded stabilization barrier for SPA route transitions."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..domain.models import ActionType, Step
from ..domain.results import Observation
from .verification import ActionContract, ActionVerificationError
from .time_budget import RunTimeBudget


_TRANSITION_ACTIONS = {
    ActionType.NAVIGATE,
    ActionType.CLICK,
    ActionType.PRESS,
    ActionType.SELECT,
    ActionType.VISUAL_CLICK,
    ActionType.BRIDGE_CLICK,
}


def stabilize_after_action(
    page,
    step: Step,
    contract: ActionContract,
    before: Observation,
    *,
    timeout_ms: int,
    run_budget: RunTimeBudget | None = None,
) -> dict[str, Any]:
    """Wait until a route contract exposes matching page content.

    URL mutation alone is not sufficient for an SPA transition. The new route
    must expose its expected title or heading before the next observation is
    sent to the planner.
    """
    if step.action not in _TRANSITION_ACTIONS:
        return {"checked": False, "reason": "action_does_not_transition"}
    if not contract.expected_route_prefix:
        # Capability packs do not know every SPA route.  When the browser URL
        # nevertheless changed, wait for transient loaders to settle before
        # capturing the next Agent observation.  This is deliberately
        # best-effort: a permanently broken page must remain visible in the
        # evidence rather than being converted into a false pass.
        if page.url != before.url:
            try:
                with (
                    run_budget.rendering_wait("generic_route_settlement")
                    if run_budget else nullcontext()
                ):
                    page.wait_for_timeout(350)
                    page.wait_for_function(
                        r"""() => {
                      const visible = (element) => {
                        const style = getComputedStyle(element);
                        const rect = element.getBoundingClientRect();
                        return style.display !== 'none' && style.visibility !== 'hidden' &&
                          Number(style.opacity || 1) > 0.01 && rect.width > 0 && rect.height > 0;
                      };
                      const loading = Array.from(document.querySelectorAll(
                        '[aria-busy="true"], [role="progressbar"], .page-loading-placeholder, '
                        + '.loading, .loading-message, .spinner, [class*="skeleton" i], '
                        + '[class*="spinner" i], [class*="loading" i], svg[class*="spin" i]'
                      )).some(visible);
                      return document.readyState !== 'loading' && document.body && !loading;
                    }""",
                        timeout=max(500, timeout_ms),
                    )
                return {
                    "checked": True,
                    "reason": "generic_route_settlement",
                    "routeChanged": True,
                    "settled": True,
                    "actualUrl": page.url,
                }
            except PlaywrightTimeoutError:
                return {
                    "checked": True,
                    "reason": "generic_route_settlement_timeout",
                    "routeChanged": True,
                    "settled": False,
                    "actualUrl": page.url,
                }
        return {"checked": False, "reason": "no_route_contract"}

    expected = {
        "routePrefix": contract.expected_route_prefix,
        "heading": contract.expected_heading or "",
    }
    try:
        with (
            run_budget.rendering_wait("route_contract_settlement")
            if run_budget else nullcontext()
        ):
            page.wait_for_function(
                r"""expected => {
              const path = location.pathname || '/';
              if (!path.startsWith(expected.routePrefix)) return false;
              if (document.readyState === 'loading') return false;
              if (!expected.heading) return true;
              const wanted = expected.heading.toLowerCase();
              const title = String(document.title || '').toLowerCase();
              if (title.includes(wanted)) return true;
              const headings = Array.from(document.querySelectorAll('h1,h2,h3,[role="heading"]'))
                .filter(el => {
                  const style = getComputedStyle(el);
                  const rect = el.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 1 && rect.height > 1;
                })
                .map(el => String(el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase());
              return headings.some(value => value.includes(wanted));
            }""",
                arg=expected,
                timeout=max(500, timeout_ms),
            )
    except PlaywrightTimeoutError as exc:
        raise ActionVerificationError(
            "SPA transition did not expose the expected route content: "
            f"route={contract.expected_route_prefix}, heading={contract.expected_heading or 'unspecified'}"
        ) from exc
    return {
        "checked": True,
        "routePrefix": contract.expected_route_prefix,
        "expectedHeading": contract.expected_heading,
        "actualUrl": page.url,
        "actualTitle": page.title(),
        "passed": True,
    }
