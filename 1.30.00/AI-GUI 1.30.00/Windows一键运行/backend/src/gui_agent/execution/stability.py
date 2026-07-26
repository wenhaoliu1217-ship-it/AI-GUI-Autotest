"""Explicit pre-action stability checks for locator, visual, and Bridge actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..domain.models import ActionType, ExecutionMode, Step
from ..locating.strategies import resolve_locator
from .bridge_adapter import CanvasAppBridgeAdapter, PreparedBridgeAction


LOCATOR_ACTIONS = {
    ActionType.CLICK,
    ActionType.FILL,
    ActionType.SELECT,
    ActionType.WAIT_FOR,
    ActionType.CLEAR,
    ActionType.CHECK,
    ActionType.UNCHECK,
    ActionType.HOVER,
}
VISUAL_ACTIONS = {
    ActionType.VISUAL_CLICK,
    ActionType.VISUAL_HOVER,
    ActionType.VISUAL_SCROLL,
    ActionType.VISUAL_DRAG,
}


@dataclass(frozen=True)
class PreparedAction:
    evidence: dict[str, Any]
    bridge_action: PreparedBridgeAction | None = None
    canvas_evidence: dict[str, Any] | None = None


def prepare_action(
    page,
    step: Step,
    *,
    bridge_adapter: CanvasAppBridgeAdapter | None,
    timeout_ms: int,
) -> PreparedAction:
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

    if step.action in LOCATOR_ACTIONS or (step.action in VISUAL_ACTIONS and step.locator is not None):
        assert step.locator is not None
        locator = resolve_locator(page, step.locator).first
        locator.wait_for(state="visible", timeout=timeout_ms)
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
              const top = document.elementFromPoint(centerX, centerY);
              const unoccluded = !options.requireUnoccluded || !!top && (top === element || element.contains(top));
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
                boxBefore: before,
                boxAfter: after,
                occludingElement: unoccluded || !top ? null : `${top.tagName.toLowerCase()}${top.id ? '#' + top.id : ''}`,
              };
            }""",
            {"requireEnabled": require_enabled, "requireUnoccluded": require_unoccluded},
        )
        failures = [name for name in ("visible", "enabled", "stable", "unoccluded") if not evidence.get(name)]
        if failures:
            raise PlaywrightError(f"动作前稳定性检查失败：{', '.join(failures)}")
        evidence["passed"] = True
        canvas_evidence = None
        if step.action in VISUAL_ACTIONS:
            canvas_evidence = {
                "mode": "visual",
                "visualTarget": step.visual_target,
                "bridgeAvailable": bridge_adapter is not None,
                "bridgeBefore": bridge_adapter.capture_state(page, phase="before") if bridge_adapter else None,
            }
        return PreparedAction(evidence=evidence, canvas_evidence=canvas_evidence)

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
