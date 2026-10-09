"""Current-page hit-test grounding for semantic click targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GroundedClickTarget:
    target: Any | None
    evidence: dict[str, Any]


@dataclass(frozen=True)
class GroundedLocatorResolution:
    locator: Any | None
    evidence: dict[str, Any]


_GROUND_CLICK_SCRIPT = """element => {
  const deepElementFromPoint = (x, y) => {
    let current = document.elementFromPoint(x, y);
    for (let depth = 0; current?.shadowRoot && depth < 12; depth += 1) {
      const nested = current.shadowRoot.elementFromPoint(x, y);
      if (!nested || nested === current) break;
      current = nested;
    }
    return current;
  };
  const describe = node => node instanceof Element ? {
    tag: node.tagName.toLowerCase(),
    role: node.getAttribute('role') || '',
    id: node.id || '',
    classes: typeof node.className === 'string' ? node.className.slice(0, 240) : '',
  } : null;
  const rect = element.getBoundingClientRect();
  const x = rect.x + rect.width / 2;
  const y = rect.y + rect.height / 2;
  const hit = deepElementFromPoint(x, y);
  if (!(hit instanceof Element)) {
    return {target: null, evidence: {
      accepted: false, mode: 'no_hit_target', semanticTarget: describe(element)
    }};
  }
  if (hit === element || element.contains(hit)) {
    return {target: element, evidence: {
      accepted: true, mode: 'semantic_target', semanticTarget: describe(element),
      hitTarget: describe(hit)
    }};
  }

  let common = element.parentElement;
  let commonDepth = 1;
  while (common && !common.contains(hit) && commonDepth < 7) {
    common = common.parentElement;
    commonDepth += 1;
  }
  const hitStyle = getComputedStyle(hit);
  const commonIsLocal = common && common !== document.body && common !== document.documentElement && commonDepth <= 5;
  const sameDialog = element.closest('[role="dialog"],dialog') === hit.closest('[role="dialog"],dialog');
  const hitRect = hit.getBoundingClientRect();
  const hitIsUsable = hitStyle.pointerEvents !== 'none' && hitRect.width > 0 && hitRect.height > 0;
  if (commonIsLocal && sameDialog && hitIsUsable) {
    return {target: hit, evidence: {
      accepted: true, mode: 'current_hit_target_proxy', semanticTarget: describe(element),
      hitTarget: describe(hit), commonAncestor: describe(common), commonDepth
    }};
  }
  return {target: null, evidence: {
    accepted: false, mode: 'unrelated_occluder', semanticTarget: describe(element),
    hitTarget: describe(hit), commonAncestor: describe(common), commonDepth
  }};
}"""


def ground_click_target(locator) -> GroundedClickTarget:
    """Resolve the element that receives pointer events for this exact page state.

    This does not use site names, component libraries, prior coordinates, or
    forced clicks. A proxy is accepted only when the browser hit test proves it
    is local to the semantic target in the current document.
    """

    if not hasattr(locator, "evaluate_handle"):
        return GroundedClickTarget(
            None,
            {"accepted": True, "mode": "grounding_api_unavailable"},
        )
    bundle = locator.evaluate_handle(_GROUND_CLICK_SCRIPT)
    target_property = bundle.get_property("target")
    evidence_property = bundle.get_property("evidence")
    target = target_property.as_element()
    evidence = evidence_property.json_value()
    if not isinstance(evidence, dict):
        evidence = {"accepted": False, "mode": "invalid_grounding_evidence"}
    return GroundedClickTarget(target, evidence)


def narrow_to_unique_visible_click_target(locator) -> GroundedLocatorResolution:
    """Narrow duplicate semantic matches using only current visible geometry."""

    count = locator.count()
    if count == 1:
        return GroundedLocatorResolution(
            locator,
            {"mode": "unique_semantic_match", "candidateCount": 1},
        )
    candidates = []
    for index in range(count):
        candidate = locator.nth(index)
        try:
            box = candidate.bounding_box()
            if (
                candidate.is_visible()
                and candidate.is_enabled()
                and box is not None
                and box.get("width", 0) > 1
                and box.get("height", 0) > 1
            ):
                candidates.append((index, candidate))
        except Exception:
            continue
    if len(candidates) == 1:
        index, candidate = candidates[0]
        return GroundedLocatorResolution(
            candidate,
            {
                "mode": "unique_visible_geometry",
                "candidateCount": count,
                "selectedIndex": index,
            },
        )
    return GroundedLocatorResolution(
        None,
        {
            "mode": "ambiguous_current_page_candidates",
            "candidateCount": count,
            "visibleCandidateCount": len(candidates),
        },
    )
