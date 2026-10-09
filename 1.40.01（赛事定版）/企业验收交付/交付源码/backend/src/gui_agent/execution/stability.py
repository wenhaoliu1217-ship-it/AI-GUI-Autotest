"""Explicit pre-action stability checks for locator, visual, and Bridge actions."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..domain.models import ActionType, ExecutionMode, Step
from ..locating.strategies import LocatorError, resolve_step_locator
from .bridge_adapter import CanvasAppBridgeAdapter, PreparedBridgeAction
from .grounding import ground_click_target, narrow_to_unique_visible_click_target


LOCATOR_ACTIONS = {
    ActionType.CLICK,
    ActionType.FILL,
    ActionType.SELECT,
    ActionType.WAIT_FOR,
    ActionType.CLEAR,
    ActionType.CHECK,
    ActionType.UNCHECK,
    ActionType.HOVER,
    ActionType.UPLOAD_FILE,
    ActionType.DOWNLOAD,
    ActionType.PRESS,
}
VISUAL_ACTIONS = {
    ActionType.VISUAL_CLICK,
    ActionType.VISUAL_HOVER,
    ActionType.VISUAL_SCROLL,
    ActionType.VISUAL_DRAG,
    ActionType.VISUAL_ZOOM,
    ActionType.VISUAL_DRAW_POLYGON,
    ActionType.VISUAL_DRAW_RECTANGLE,
}


@dataclass(frozen=True)
class PreparedAction:
    evidence: dict[str, Any]
    bridge_action: PreparedBridgeAction | None = None
    canvas_evidence: dict[str, Any] | None = None
    click_target: Any | None = None


def prepare_action(
    page,
    step: Step,
    *,
    bridge_adapter: CanvasAppBridgeAdapter | None,
    timeout_ms: int,
    locator_root=None,
) -> PreparedAction:
    if step.action == ActionType.WAIT_FOR:
        # Presence and uniqueness are the result of a wait, not a precondition.
        # Hidden/detached waits must also accept an already absent target.
        return PreparedAction(
            evidence={"checked": False, "passed": True, "mode": "deferred_wait"}
        )

    if step.action == ActionType.BRIDGE_CLICK:
        if bridge_adapter is None:
            raise PlaywrightError("当前环境未启用 App Bridge，拒绝执行 app_bridge 动作")
        assert step.bridge_target_id is not None
        prepared = bridge_adapter.prepare_click(page, step.bridge_target_id)
        return PreparedAction(
            evidence={
                "checked": True,
                "passed": True,
                "mode": "app_bridge",
                "sceneReady": True,
                "targetId": step.bridge_target_id,
                "adapter": bridge_adapter.adapter_name,
            },
            bridge_action=prepared,
        )

    visual_region_locator = (
        step.canvas_region_locator
        if step.action in {
            ActionType.VISUAL_ZOOM,
            ActionType.VISUAL_DRAW_POLYGON,
            ActionType.VISUAL_DRAW_RECTANGLE,
        }
        else step.locator
    )
    if step.action in LOCATOR_ACTIONS or (step.action in VISUAL_ACTIONS and visual_region_locator is not None):
        assert visual_region_locator is not None
        resolved_step = (
            step.model_copy(update={"locator": visual_region_locator})
            if step.action in VISUAL_ACTIONS and step.locator is None
            else step
        )
        locator = resolve_step_locator(locator_root or page, resolved_step, scroll_page=page)
        candidate_resolution = None
        if step.action == ActionType.CLICK:
            candidate_resolution = narrow_to_unique_visible_click_target(locator)
            if candidate_resolution.locator is not None:
                locator = candidate_resolution.locator
        count = (
            locator.count()
            if candidate_resolution is None or candidate_resolution.locator is not None
            else 0
        )
        if count != 1:
            raise LocatorError(f"动作目标必须唯一，实际匹配 {count} 个：{visual_region_locator.describe()}")
        visibility_wait_fallback = None
        # Native file inputs are intentionally hidden behind a visible label
        # or button. Playwright can set files on an attached hidden input, so
        # requiring pointer visibility here blocks every standard upload form.
        allow_hidden_file_input = step.action == ActionType.UPLOAD_FILE
        try:
            locator.wait_for(
                state="attached" if allow_hidden_file_input else "visible",
                timeout=timeout_ms,
            )
        except PlaywrightError:
            # Some continuously re-rendered SPA forms replace a control while
            # Playwright polls. Its log can report one unique visible element
            # and still time out. Non-pointer form operations may continue only
            # when the fresh locator is visible in the current document; their
            # execution path independently verifies the resulting value/state.
            if (
                allow_hidden_file_input
                or
                step.action not in {
                    ActionType.FILL,
                    ActionType.CLEAR,
                    ActionType.SELECT,
                    ActionType.CHECK,
                    ActionType.UNCHECK,
                    ActionType.PRESS,
                }
                or not locator.is_visible(timeout=min(timeout_ms, 1_000))
            ):
                raise
            visibility_wait_fallback = "immediate_current_document_check"
        grounded_click = (
            ground_click_target(locator)
            if step.action == ActionType.CLICK
            else None
        )
        if grounded_click is not None and not grounded_click.evidence.get("accepted"):
            raise PlaywrightError(
                "Current-page hit testing rejected an unrelated pointer-event occluder"
            )
        require_enabled = step.action not in {ActionType.WAIT_FOR, ActionType.HOVER, ActionType.VISUAL_HOVER}
        require_unoccluded = step.action not in {ActionType.WAIT_FOR}
        evidence = locator.evaluate(
            """async (element, options) => {
              const sample = () => {
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return {
                  x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                  visible: rect.width > 0 && rect.height > 0 && style.display !== 'none' &&
                    style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.01,
                };
              };
              const before = sample();
              await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              const after = sample();
              const centerX = after.x + after.width / 2;
              const centerY = after.y + after.height / 2;
              const shallowTop = document.elementFromPoint(centerX, centerY);
              let top = shallowTop;
              let shadowPierced = false;
              for (let depth = 0; top?.shadowRoot && depth < 12; depth += 1) {
                const nested = top.shadowRoot.elementFromPoint(centerX, centerY);
                if (!nested || nested === top) break;
                top = nested;
                shadowPierced = true;
              }
              let shadowHostProxy = false;
              if (shallowTop && top !== element) {
                let root = element.getRootNode();
                for (let depth = 0; root instanceof ShadowRoot && depth < 12; depth += 1) {
                  if (root.host === shallowTop) {
                    shadowHostProxy = true;
                    break;
                  }
                  root = root.host.getRootNode();
                }
              }
              let compositeProxy = false;
              if (top && element.getAttribute('role') === 'combobox') {
                let ancestor = element.parentElement;
                for (let depth = 0; ancestor && depth < 6; depth += 1, ancestor = ancestor.parentElement) {
                  const classes = typeof ancestor.className === 'string' ? ancestor.className.toLowerCase() : '';
                  if ((classes.includes('select') || ancestor.getAttribute('role') === 'combobox') &&
                      ancestor.contains(top)) {
                    compositeProxy = true;
                    break;
                  }
                }
              }
              const unoccluded = !options.requireUnoccluded || !!top &&
                (top === element || element.contains(top) || shadowHostProxy || compositeProxy);
              const nativeDisabled = 'disabled' in element && Boolean(element.disabled);
              const ariaDisabled = element.getAttribute('aria-disabled') === 'true';
              const enabled = !options.requireEnabled || (!nativeDisabled && !ariaDisabled);
              const stable = Math.abs(before.x - after.x) <= 1 && Math.abs(before.y - after.y) <= 1 &&
                Math.abs(before.width - after.width) <= 1 && Math.abs(before.height - after.height) <= 1;
              return {
                checked: true,
                mode: 'locator',
                visible: before.visible && after.visible,
                enabled,
                stable,
                unoccluded,
                shadowPierced,
                shadowHostProxy,
                compositeProxy,
                boxBefore: before,
                boxAfter: after,
                occludingElement: unoccluded || !top ? null : `${top.tagName.toLowerCase()}${top.id ? '#' + top.id : ''}`,
              };
            }""",
            {"requireEnabled": require_enabled, "requireUnoccluded": require_unoccluded},
        )
        if (
            grounded_click is not None
            and grounded_click.evidence.get("mode") == "current_hit_target_proxy"
        ):
            evidence["unoccluded"] = True
            evidence["hitTargetProxy"] = True
        required_evidence = ("enabled", "stable") if allow_hidden_file_input else ("visible", "enabled", "stable", "unoccluded")
        failures = [name for name in required_evidence if not evidence.get(name)]
        if failures:
            raise PlaywrightError(f"动作前稳定性检查失败：{', '.join(failures)}")
        evidence["passed"] = True
        if visibility_wait_fallback:
            evidence["visibilityWaitFallback"] = visibility_wait_fallback
        if grounded_click is not None:
            evidence["grounding"] = grounded_click.evidence
        if candidate_resolution is not None:
            evidence["candidateResolution"] = candidate_resolution.evidence
        canvas_evidence = None
        if step.action in VISUAL_ACTIONS:
            canvas_evidence = {
                "mode": "visual",
                "visualTarget": step.visual_target,
                "bridgeAvailable": bridge_adapter is not None,
                "bridgeBefore": bridge_adapter.capture_state(page, phase="before") if bridge_adapter else None,
            }
        return PreparedAction(
            evidence=evidence,
            canvas_evidence=canvas_evidence,
            click_target=grounded_click.target if grounded_click is not None else None,
        )

    if step.action in VISUAL_ACTIONS:
        viewport = page.viewport_size
        if not viewport or viewport["width"] <= 0 or viewport["height"] <= 0:
            raise PlaywrightError("视觉动作前无法确认有效 viewport")
        return PreparedAction(
            evidence={
                "checked": True,
                "passed": True,
                "mode": "visual_viewport",
                "visible": True,
                "stable": True,
                "viewport": viewport,
            },
            canvas_evidence={
                "mode": "visual",
                "visualTarget": step.visual_target,
                "bridgeAvailable": bridge_adapter is not None,
                "bridgeBefore": bridge_adapter.capture_state(page, phase="before") if bridge_adapter else None,
            },
        )

    return PreparedAction(evidence={"checked": False, "passed": True, "mode": "not_applicable"})


def finalize_canvas_evidence(
    page,
    step: Step,
    *,
    prepared: PreparedAction,
    bridge_adapter: CanvasAppBridgeAdapter | None,
    execution_detail: dict[str, Any],
    before_screenshot: str | None,
    after_screenshot: str | None,
) -> dict[str, Any] | None:
    if step.execution_mode not in {ExecutionMode.VISUAL, ExecutionMode.APP_BRIDGE}:
        return None
    evidence: dict[str, Any] = {
        "mode": step.execution_mode.value,
        "action": step.action.value,
        "semanticTarget": step.visual_target or step.bridge_target_id,
        "coordinateSource": execution_detail.get("coordinateSource"),
        "beforeScreenshot": before_screenshot,
        "afterScreenshot": after_screenshot,
        "traceArtifact": "trace.zip",
        "collectionStatus": "complete",
    }
    if step.execution_mode == ExecutionMode.APP_BRIDGE:
        bridge_result = execution_detail.get("appBridgeResult") or {}
        evidence.update({
            "bridgeAvailable": True,
            "bridgeVersion": bridge_result.get("version"),
            "bridgeCapabilities": bridge_result.get("capabilities"),
            "sceneBefore": bridge_result.get("sceneBefore"),
            "sceneAfter": bridge_result.get("sceneAfter"),
            "visibleTargets": bridge_result.get("visibleTargets"),
            "selectedTargetBefore": bridge_result.get("selectedTargetBefore"),
            "selectedTargetAfter": bridge_result.get("selectedTargetAfter"),
            "semanticStateVerified": bridge_result.get("semanticStateVerified"),
        })
        return evidence

    bridge_before = (prepared.canvas_evidence or {}).get("bridgeBefore")
    bridge_after = bridge_adapter.capture_state(page, phase="after") if bridge_adapter else None
    evidence.update({
        "bridgeAvailable": bridge_adapter is not None,
        "bridgeBefore": bridge_before,
        "bridgeAfter": bridge_after,
        "sceneStateChanged": (
            bridge_before.get("sceneState") != bridge_after.get("sceneState")
            if isinstance(bridge_before, dict) and isinstance(bridge_after, dict)
            else None
        ),
        "selectedTargetChanged": (
            bridge_before.get("selectedTargetId") != bridge_after.get("selectedTargetId")
            if isinstance(bridge_before, dict) and isinstance(bridge_after, dict)
            else None
        ),
    })
    return evidence


def attach_rendering_evidence(
    canvas_evidence: dict[str, Any] | None,
    observation: Any,
) -> dict[str, Any] | None:
    """Attach bounded WebGL/Canvas facts to the persisted visual evidence."""
    if canvas_evidence is None:
        return None
    semantic = getattr(observation, "semantic_summary", None)
    canvas = getattr(semantic, "canvas", None) if semantic is not None else None
    if not isinstance(canvas, dict):
        return canvas_evidence
    rendering = {
        key: canvas.get(key)
        for key in (
            "count", "surfaces", "nonEmptySurface", "webglSurfaceCount",
            "nonEmptyPixels", "contextLost", "renderingEvidence", "targetSelector",
            "targetFrameUrl", "targetBounds", "targetViewport",
        )
        if key in canvas
    }
    canvas_evidence["renderingEvidence"] = rendering
    canvas_evidence["renderingVerified"] = bool(
        rendering.get("nonEmptyPixels")
        and not rendering.get("contextLost")
        and (rendering.get("webglSurfaceCount", 0) or rendering.get("count", 0))
    )
    return canvas_evidence


def attach_visual_delta_evidence(
    canvas_evidence: dict[str, Any] | None,
    artifacts: Any,
) -> dict[str, Any] | None:
    """Compare only the bound Canvas region in the persisted before/after shots."""
    if canvas_evidence is None:
        return None
    rendering = canvas_evidence.get("renderingEvidence")
    if not isinstance(rendering, dict) or not rendering.get("targetSelector"):
        return canvas_evidence
    before_name = canvas_evidence.get("beforeScreenshot")
    after_name = canvas_evidence.get("afterScreenshot")
    bounds = rendering.get("targetBounds")
    viewport = rendering.get("targetViewport")
    if not before_name or not after_name or not isinstance(bounds, dict):
        canvas_evidence["visualDelta"] = {
            "available": False,
            "passed": False,
            "reason": "before_after_screenshot_or_target_bounds_missing",
        }
        return canvas_evidence
    try:
        before_path = Path(artifacts.run_dir) / str(before_name)
        after_path = Path(artifacts.run_dir) / str(after_name)
        before_width, before_height, before_pixels = _read_png_rgb(before_path)
        after_width, after_height, after_pixels = _read_png_rgb(after_path)
        viewport_width = float((viewport or {}).get("width") or before_width)
        viewport_height = float((viewport or {}).get("height") or before_height)
        x = float(bounds.get("x", 0))
        y = float(bounds.get("y", 0))
        width = float(bounds.get("width", 0))
        height = float(bounds.get("height", 0))
        left = max(0, int(x * before_width / max(viewport_width, 1.0)))
        top = max(0, int(y * before_height / max(viewport_height, 1.0)))
        right = min(before_width, int((x + width) * before_width / max(viewport_width, 1.0)))
        bottom = min(before_height, int((y + height) * before_height / max(viewport_height, 1.0)))
        if right <= left or bottom <= top:
            raise ValueError("target_bounds_outside_screenshot")
        after_left = max(0, int(x * after_width / max(viewport_width, 1.0)))
        after_top = max(0, int(y * after_height / max(viewport_height, 1.0)))
        after_right = min(after_width, int((x + width) * after_width / max(viewport_width, 1.0)))
        after_bottom = min(after_height, int((y + height) * after_height / max(viewport_height, 1.0)))
        if after_right <= after_left or after_bottom <= after_top:
            raise ValueError("target_bounds_outside_after_screenshot")

        region_width = right - left
        region_height = bottom - top
        after_region_width = after_right - after_left
        after_region_height = after_bottom - after_top
        pixel_count = region_width * region_height
        sample_step = max(1, int((pixel_count / 1_000_000) ** 0.5))
        total_delta = 0
        changed_pixels = 0
        sampled_pixels = 0
        for row in range(0, region_height, sample_step):
            before_row = (top + row) * before_width
            after_row = after_top + min(after_region_height - 1, int(row * after_region_height / region_height))
            after_row_offset = after_row * after_width
            for column in range(0, region_width, sample_step):
                before_column = left + column
                after_column = after_left + min(after_region_width - 1, int(column * after_region_width / region_width))
                before_offset = (before_row + before_column) * 3
                after_offset = (after_row_offset + after_column) * 3
                delta = (
                    abs(before_pixels[before_offset] - after_pixels[after_offset])
                    + abs(before_pixels[before_offset + 1] - after_pixels[after_offset + 1])
                    + abs(before_pixels[before_offset + 2] - after_pixels[after_offset + 2])
                )
                total_delta += delta
                changed_pixels += delta > 12
                sampled_pixels += 1
        mean_delta = total_delta / max(1, sampled_pixels * 3 * 255)
        changed_ratio = changed_pixels / max(1, sampled_pixels)
        canvas_evidence["visualDelta"] = {
            "available": True,
            "passed": bool(mean_delta >= 0.01 and changed_ratio >= 0.01),
            "metric": "bound_canvas_mean_absolute_rgb_delta",
            "meanDelta": round(mean_delta, 6),
            "changedPixelRatio": round(changed_ratio, 6),
            "sampledPixels": sampled_pixels,
            "thresholds": {"meanDelta": 0.01, "changedPixelRatio": 0.01},
            "region": {"left": left, "top": top, "right": right, "bottom": bottom},
        }
        # Cesium readback may be intentionally skipped while the viewer is
        # animating. A changed screenshot region plus a live WebGL context
        # is still sufficient to verify that the bound surface rendered.
        if (
            canvas_evidence["visualDelta"]["passed"]
            and int(rendering.get("webglSurfaceCount") or 0) >= 1
            and not rendering.get("contextLost")
        ):
            canvas_evidence["renderingVerified"] = True
    except Exception as exc:
        canvas_evidence["visualDelta"] = {
            "available": False,
            "passed": False,
            "reason": type(exc).__name__,
        }
    return canvas_evidence


def _read_png_rgb(path: Path) -> tuple[int, int, bytes]:
    """Decode the 8-bit non-interlaced PNGs emitted by Playwright screenshots."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("screenshot_is_not_png")
    width = height = bit_depth = color_type = interlace = None
    palette: bytes | None = None
    transparency: bytes | None = None
    compressed = bytearray()
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
        elif kind == b"PLTE":
            palette = bytes(payload)
        elif kind == b"tRNS":
            transparency = bytes(payload)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
    if width is None or height is None or bit_depth != 8 or interlace != 0:
        raise ValueError("unsupported_png_format")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError("unsupported_png_color_type")
    raw = zlib.decompress(bytes(compressed))
    stride = width * channels
    expected = height * (stride + 1)
    if len(raw) < expected:
        raise ValueError("truncated_png_data")
    rows: list[bytearray] = []
    cursor = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        current = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        for index in range(stride):
            left = current[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                current[index] = (current[index] + left) & 255
            elif filter_type == 2:
                current[index] = (current[index] + above) & 255
            elif filter_type == 3:
                current[index] = (current[index] + ((left + above) // 2)) & 255
            elif filter_type == 4:
                estimate = left + above - upper_left
                distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
                current[index] = (current[index] + (left if distances[0] <= distances[1] and distances[0] <= distances[2] else above if distances[1] <= distances[2] else upper_left)) & 255
            elif filter_type != 0:
                raise ValueError("unsupported_png_filter")
        rows.append(current)
        previous = current
    rgb = bytearray(width * height * 3)
    output = 0
    for row in rows:
        for pixel in range(width):
            source = pixel * channels
            if color_type == 0:
                value = row[source]
                rgb[output:output + 3] = bytes((value, value, value))
            elif color_type == 2:
                rgb[output:output + 3] = row[source:source + 3]
            elif color_type == 3:
                palette_index = row[source] * 3
                if palette is None or palette_index + 3 > len(palette):
                    raise ValueError("png_palette_missing")
                rgb[output:output + 3] = palette[palette_index:palette_index + 3]
            elif color_type == 4:
                value = row[source]
                rgb[output:output + 3] = bytes((value, value, value))
            else:
                rgb[output:output + 3] = row[source:source + 3]
            output += 3
    return int(width), int(height), bytes(rgb)


def strict_3d_evidence_passed(steps: list[Any]) -> bool:
    """Return true only when every visual 3D step has bound WebGL and delta proof."""
    def value_of(item: Any) -> str:
        value = getattr(item, "value", item)
        return str(value)

    visual_steps = [
        step for step in steps
        if value_of(getattr(step, "execution_mode", "")) == "visual"
        and value_of(getattr(step, "action", "")) in {
            "visual_click", "visual_hover", "visual_scroll", "visual_drag"
        }
    ]
    if not visual_steps:
        return True
    for step in visual_steps:
        evidence = getattr(step, "canvas_evidence", None)
        rendering = evidence.get("renderingEvidence") if isinstance(evidence, dict) else None
        delta = evidence.get("visualDelta") if isinstance(evidence, dict) else None
        if not isinstance(rendering, dict) or not isinstance(delta, dict):
            return False
        if not rendering.get("targetSelector") or not rendering.get("targetFrameUrl"):
            return False
        if int(rendering.get("webglSurfaceCount") or 0) < 1:
            return False
        if rendering.get("contextLost") or not delta.get("passed"):
            return False
    return True
