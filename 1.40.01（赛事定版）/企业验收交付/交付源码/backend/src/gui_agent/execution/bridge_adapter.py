"""Runtime adapter for the optional Canvas App Bridge contract."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..security.redaction import Redactor
from ..security.policy import SecurityError


REQUIRED_METHODS = (
    "getSceneState",
    "listVisibleTargets",
    "getTargetScreenPosition",
    "getSelectedTargetId",
    "waitForSceneReady",
)


@dataclass(frozen=True)
class PreparedBridgeAction:
    target_id: str
    position: dict[str, float]
    evidence: dict[str, Any]


class CanvasAppBridgeAdapter:
    def __init__(
        self,
        global_name: str,
        *,
        adapter_name: str = "generic",
        timeout_ms: int = 10_000,
        redactor: Redactor | None = None,
    ) -> None:
        self.global_name = global_name
        self.adapter_name = adapter_name
        self.timeout_ms = timeout_ms
        self.redactor = redactor or Redactor()

    def capture_state(self, page, *, phase: str) -> dict[str, Any]:
        raw = page.evaluate(
            """async ({ globalName, timeoutMs, requiredMethods }) => {
              const bridge = window[globalName];
              if (!bridge || typeof bridge !== 'object') throw new Error(`应用测试桥接 ${globalName} 不可用`);
              const missing = requiredMethods.filter((name) => typeof bridge[name] !== 'function');
              if (missing.length) throw new Error(`Bridge 缺少最小契约方法：${missing.join(', ')}`);
              if (String(bridge.version || '') !== '1') throw new Error('Bridge version 必须为 1');
              const bounded = (promise, label) => Promise.race([
                Promise.resolve(promise),
                new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} 超时`)), timeoutMs)),
              ]);
              await bounded(bridge.waitForSceneReady(), '等待场景就绪');
              const [sceneState, visibleTargets, selectedTargetId] = await Promise.all([
                bounded(bridge.getSceneState(), '读取场景状态'),
                bounded(bridge.listVisibleTargets(), '读取可见目标'),
                bounded(bridge.getSelectedTargetId(), '读取选中目标'),
              ]);
              return { version: String(bridge.version), capabilities: requiredMethods, sceneState, visibleTargets, selectedTargetId };
            }""",
            {
                "globalName": self.global_name,
                "timeoutMs": self.timeout_ms,
                "requiredMethods": list(REQUIRED_METHODS),
            },
        )
        if not isinstance(raw, dict) or not isinstance(raw.get("sceneState"), dict):
            raise PlaywrightError("Bridge getSceneState 必须返回对象")
        if not isinstance(raw.get("visibleTargets"), list):
            raise PlaywrightError("Bridge listVisibleTargets 必须返回数组")
        return self._sanitize({
            "phase": phase,
            "adapter": self.adapter_name,
            "globalName": self.global_name,
            "version": raw.get("version"),
            "capabilities": raw.get("capabilities"),
            "sceneReady": True,
            "sceneState": raw.get("sceneState"),
            "visibleTargets": raw.get("visibleTargets"),
            "selectedTargetId": raw.get("selectedTargetId"),
        })

    def prepare_click(self, page, target_id: str) -> PreparedBridgeAction:
        raw = page.evaluate(
            """async ({ globalName, targetId, timeoutMs, requiredMethods }) => {
              const bridge = window[globalName];
              if (!bridge || typeof bridge !== 'object') throw new Error(`应用测试桥接 ${globalName} 不可用`);
              const missing = requiredMethods.filter((name) => typeof bridge[name] !== 'function');
              if (missing.length) throw new Error(`Bridge 缺少最小契约方法：${missing.join(', ')}`);
              if (String(bridge.version || '') !== '1') throw new Error('Bridge version 必须为 1');
              const bounded = (promise, label) => Promise.race([
                Promise.resolve(promise),
                new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} 超时`)), timeoutMs)),
              ]);
              await bounded(bridge.waitForSceneReady(), '等待场景就绪');
              const [sceneState, visibleTargets, selectedTargetId, position] = await Promise.all([
                bounded(bridge.getSceneState(), '读取场景状态'),
                bounded(bridge.listVisibleTargets(), '读取可见目标'),
                bounded(bridge.getSelectedTargetId(), '读取选中目标'),
                bounded(bridge.getTargetScreenPosition(targetId), '读取目标屏幕位置'),
              ]);
              return {
                version: String(bridge.version),
                capabilities: requiredMethods,
                sceneState,
                visibleTargets,
                selectedTargetId,
                position,
              };
            }""",
            {
                "globalName": self.global_name,
                "targetId": target_id,
                "timeoutMs": self.timeout_ms,
                "requiredMethods": list(REQUIRED_METHODS),
            },
        )
        if not isinstance(raw, dict):
            raise PlaywrightError("Bridge 返回的准备结果无效")
        scene = raw.get("sceneState")
        targets = raw.get("visibleTargets")
        position = raw.get("position")
        if not isinstance(scene, dict):
            raise PlaywrightError("Bridge getSceneState 必须返回对象")
        if not isinstance(targets, list):
            raise PlaywrightError("Bridge listVisibleTargets 必须返回数组")
        target_ids = {
            str(item.get("id") or item.get("targetId"))
            for item in targets
            if isinstance(item, dict) and (item.get("id") or item.get("targetId"))
        }
        if target_id not in target_ids:
            raise PlaywrightError(f"Bridge 可见目标中不存在：{target_id}")
        if not isinstance(position, dict):
            raise PlaywrightError("Bridge getTargetScreenPosition 必须返回坐标对象")
        x, y = position.get("x"), position.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or not math.isfinite(x) or not math.isfinite(y):
            raise PlaywrightError("Bridge 未返回有效目标位置")
        evidence = self._sanitize({
            "adapter": self.adapter_name,
            "globalName": self.global_name,
            "version": raw.get("version"),
            "capabilities": raw.get("capabilities"),
            "sceneBefore": scene,
            "visibleTargets": targets,
            "selectedTargetBefore": raw.get("selectedTargetId"),
            "targetPosition": {"x": float(x), "y": float(y)},
            "sceneReadyBefore": True,
        })
        return PreparedBridgeAction(
            target_id=target_id,
            position={"x": float(x), "y": float(y)},
            evidence=evidence,
        )

    def complete_click(self, page, prepared: PreparedBridgeAction) -> dict[str, Any]:
        raw = page.evaluate(
            """async ({ globalName, timeoutMs }) => {
              const bridge = window[globalName];
              const bounded = (promise, label) => Promise.race([
                Promise.resolve(promise),
                new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} 超时`)), timeoutMs)),
              ]);
              await bounded(bridge.waitForSceneReady(), '动作后等待场景就绪');
              const [sceneState, selectedTargetId] = await Promise.all([
                bounded(bridge.getSceneState(), '动作后读取场景状态'),
                bounded(bridge.getSelectedTargetId(), '动作后读取选中目标'),
              ]);
              return { sceneState, selectedTargetId };
            }""",
            {"globalName": self.global_name, "timeoutMs": self.timeout_ms},
        )
        if not isinstance(raw, dict) or not isinstance(raw.get("sceneState"), dict):
            raise PlaywrightError("Bridge 动作后场景状态无效")
        selected = raw.get("selectedTargetId")
        if selected != prepared.target_id:
            raise SecurityError(
                f"Bridge 动作后选中目标不匹配：期望 {prepared.target_id}，实际 {selected or '未选中'}"
            )
        return self._sanitize({
            **prepared.evidence,
            "sceneAfter": raw["sceneState"],
            "selectedTargetAfter": selected,
            "sceneReadyAfter": True,
            "semanticStateVerified": True,
        })

    def _sanitize(self, value: Any) -> Any:
        return _bounded_json(value, self.redactor)


class CesiumBridgeAdapter(CanvasAppBridgeAdapter):
    """Bridge protocol adapter with Cesium-specific scene evidence validation."""

    def prepare_click(self, page, target_id: str) -> PreparedBridgeAction:
        prepared = super().prepare_click(page, target_id)
        scene = prepared.evidence.get("sceneBefore")
        self._validate_scene(scene)
        return prepared

    def capture_state(self, page, *, phase: str) -> dict[str, Any]:
        evidence = super().capture_state(page, phase=phase)
        self._validate_scene(evidence.get("sceneState"))
        return evidence

    @staticmethod
    def _validate_scene(scene: Any) -> None:
        if not isinstance(scene, dict) or not any(key in scene for key in ("camera", "layers", "loading", "tilesLoaded")):
            raise PlaywrightError("Cesium Bridge 场景状态缺少 camera/layers/loading/tilesLoaded 证据")


class GAEALaViCCesiumBridgeAdapter(CesiumBridgeAdapter):
    """校验仿真业务页面暴露的标准化三维语义，不直接读取 Cesium 私有对象。"""

    REQUIRED_SCENE_KEYS = (
        "camera", "layers", "entityCount", "selectedEntityId", "pathPoints",
        "pois", "fences", "drawings", "tilesLoaded", "webglError",
    )

    @classmethod
    def _validate_scene(cls, scene: Any) -> None:
        super()._validate_scene(scene)
        missing = [key for key in cls.REQUIRED_SCENE_KEYS if key not in scene]
        if missing:
            raise PlaywrightError(f"仿真业务三维适配器缺少语义状态：{', '.join(missing)}")
        if not isinstance(scene.get("layers"), list):
            raise PlaywrightError("仿真业务三维适配器的 layers 必须为数组")
        for key in ("pathPoints", "pois", "fences", "drawings"):
            if not isinstance(scene.get(key), list):
                raise PlaywrightError(f"仿真业务三维适配器的 {key} 必须为数组")


def create_bridge_adapter(
    *, enabled: bool, global_name: str, adapter_name: str, timeout_ms: int, redactor: Redactor
) -> CanvasAppBridgeAdapter | None:
    if not enabled:
        return None
    adapter_type = (
        GAEALaViCCesiumBridgeAdapter if adapter_name == "gaealavic_cesium"
        else CesiumBridgeAdapter if adapter_name == "cesium"
        else CanvasAppBridgeAdapter
    )
    return adapter_type(
        global_name,
        adapter_name=adapter_name,
        timeout_ms=timeout_ms,
        redactor=redactor,
    )


def _bounded_json(value: Any, redactor: Redactor, *, depth: int = 0) -> Any:
    if depth >= 6:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redactor.scrub(value[:1000])
    if isinstance(value, list):
        return [_bounded_json(item, redactor, depth=depth + 1) for item in value[:200]]
    if isinstance(value, dict):
        return {
            redactor.scrub(str(key)[:200]): _bounded_json(item, redactor, depth=depth + 1)
            for key, item in list(value.items())[:200]
        }
    return redactor.scrub(str(value)[:1000])
