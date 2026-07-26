"""断言执行。

把领域层的 Assertion 转成对 Playwright 页面的实际检查，返回
(通过?, 实际值摘要)。不抛断言异常，由执行器统一记录 AssertionResult，
保证"断言失败"与"执行错误"在报告中可区分。

数据范围断言用 NOT_VISIBLE 和 COUNT_EQUALS 表达，例如
"员工看不到其他人的客户" -> NOT_VISIBLE，"只看到自己的 3 条" -> COUNT_EQUALS。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..domain.models import Assertion, AssertionType
from ..locating.strategies import resolve_locator

if TYPE_CHECKING:
    from playwright.sync_api import Page

# 断言级别的默认超时（毫秒）。比动作超时短，避免断言在错误页面上长时间等待。
ASSERTION_TIMEOUT_MS = 5000


class AssertionOutcome:
    """一次断言检查的结果。passed 为断言是否成立；actual 为脱敏前的实际值摘要。"""

    def __init__(self, passed: bool, actual: str, semantic_evidence: dict[str, Any] | None = None) -> None:
        self.passed = passed
        self.actual = actual
        self.semantic_evidence = semantic_evidence


def check_assertion(page: "Page", assertion: Assertion, *, bridge_adapter=None) -> AssertionOutcome:
    """执行单条断言，返回结果。定位/运行异常向上抛出，由执行器归类为 ERROR。"""
    t = assertion.type

    if t == AssertionType.PAGE_REACHED:
        url = page.url
        return AssertionOutcome(
            passed=(assertion.expected or "") in url, actual=f"url={url}"
        )

    if t == AssertionType.URL_CONTAINS:
        url = page.url
        return AssertionOutcome(
            passed=(assertion.expected or "") in url, actual=f"url={url}"
        )

    if t == AssertionType.VISIBLE:
        loc = resolve_locator(page, assertion.locator)  # type: ignore[arg-type]
        visible = loc.first.is_visible()
        return AssertionOutcome(passed=visible, actual=f"visible={visible}")

    if t == AssertionType.NOT_VISIBLE:
        loc = resolve_locator(page, assertion.locator)  # type: ignore[arg-type]
        count = loc.count()
        visible = count > 0 and loc.first.is_visible()
        return AssertionOutcome(passed=(not visible), actual=f"visible={visible}")

    if t == AssertionType.TEXT_CONTAINS:
        loc = resolve_locator(page, assertion.locator)  # type: ignore[arg-type]
        text = loc.first.inner_text(timeout=ASSERTION_TIMEOUT_MS)
        return AssertionOutcome(
            passed=(assertion.expected or "") in text, actual=f"text={text!r}"
        )

    if t == AssertionType.VALUE_EQUALS:
        loc = resolve_locator(page, assertion.locator)  # type: ignore[arg-type]
        value = loc.first.input_value(timeout=ASSERTION_TIMEOUT_MS)
        return AssertionOutcome(
            passed=(value == assertion.expected), actual=f"value={value!r}"
        )

    if t == AssertionType.COUNT_EQUALS:
        loc = resolve_locator(page, assertion.locator)  # type: ignore[arg-type]
        count = loc.count()
        return AssertionOutcome(
            passed=(count == assertion.count), actual=f"count={count}"
        )

    semantic_types = {
        AssertionType.CANVAS_LAYER_VISIBLE, AssertionType.CANVAS_CAMERA_EQUALS,
        AssertionType.CANVAS_ENTITY_COUNT, AssertionType.CANVAS_SELECTED_ENTITY,
        AssertionType.CANVAS_PATH_POINT_COUNT, AssertionType.CANVAS_POI_COUNT,
        AssertionType.CANVAS_FENCE_COUNT, AssertionType.CANVAS_DRAWING_COUNT,
        AssertionType.CANVAS_TILES_LOADED, AssertionType.CANVAS_WEBGL_NO_ERROR,
    }
    if t in semantic_types:
        if bridge_adapter is None:
            raise ValueError("Canvas 业务语义断言需要已授权的 App Bridge")
        captured = bridge_adapter.capture_state(page, phase=f"assertion:{t.value}")
        scene = captured["sceneState"]
        passed, actual, classification = _check_canvas_semantic(t, scene, assertion)
        return AssertionOutcome(passed, actual, {
            "type": t.value, "sceneState": scene, "classification": classification,
            "adapter": captured.get("adapter"), "expected": assertion.expected,
            "expectedCount": assertion.count,
        })

    raise ValueError(f"未知断言类型：{t}")


def _check_canvas_semantic(t: AssertionType, scene: dict[str, Any], assertion: Assertion) -> tuple[bool, str, str]:
    if t == AssertionType.CANVAS_LAYER_VISIBLE:
        expected = assertion.expected or ""
        layer = next((item for item in scene.get("layers", []) if str(item.get("id")) == expected or str(item.get("name")) == expected), None)
        passed = bool(layer and (layer.get("visible") is True or layer.get("show") is True))
        return passed, f"layer={json.dumps(layer, ensure_ascii=False)}", "test_data_permission_or_product"
    if t == AssertionType.CANVAS_CAMERA_EQUALS:
        try:
            expected_camera = json.loads(assertion.expected or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("canvas_camera_equals expected 必须是 JSON 对象") from exc
        actual = scene.get("camera") or {}
        passed = _subset_close(actual, expected_camera, assertion.tolerance)
        return passed, f"camera={json.dumps(actual, ensure_ascii=False, sort_keys=True)}", "environment_timing_or_product"
    if t == AssertionType.CANVAS_SELECTED_ENTITY:
        actual = scene.get("selectedEntityId") or scene.get("selectedTargetId")
        return actual == assertion.expected, f"selectedEntityId={actual!r}", "test_data_or_product"
    count_keys = {
        AssertionType.CANVAS_ENTITY_COUNT: "entityCount",
        AssertionType.CANVAS_PATH_POINT_COUNT: "pathPoints",
        AssertionType.CANVAS_POI_COUNT: "pois",
        AssertionType.CANVAS_FENCE_COUNT: "fences",
        AssertionType.CANVAS_DRAWING_COUNT: "drawings",
    }
    if t in count_keys:
        value = scene.get(count_keys[t], 0)
        actual = len(value) if isinstance(value, list) else int(value)
        return actual == assertion.count, f"{count_keys[t]}={actual}", "test_data_or_product"
    if t == AssertionType.CANVAS_TILES_LOADED:
        actual = scene.get("tilesLoaded")
        return actual is True, f"tilesLoaded={actual}", "timing_or_environment"
    error = scene.get("webglError")
    return error in {None, "", False}, f"webglError={error!r}", "environment_or_product"


def _subset_close(actual: Any, expected: Any, tolerance: float) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _subset_close(actual[key], value, tolerance) for key, value in expected.items())
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(actual) - float(expected)) <= tolerance
    return actual == expected
