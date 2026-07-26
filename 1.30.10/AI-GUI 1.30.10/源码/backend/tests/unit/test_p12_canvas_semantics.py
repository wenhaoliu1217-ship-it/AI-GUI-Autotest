from __future__ import annotations

import pytest
from pydantic import ValidationError

from gui_agent.assertions.checks import check_assertion
from gui_agent.domain.models import Assertion, AssertionType, Step
from gui_agent.domain.results import AssertionResult, Status
from gui_agent.execution.bridge_adapter import GAEALaViCCesiumBridgeAdapter
from gui_agent.execution.findings import build_findings


SCENE = {
    "camera": {"longitude": 116.3, "latitude": 39.9, "height": 1000, "heading": 0, "pitch": -1, "roll": 0},
    "layers": [{"id": "base", "name": "Base", "visible": True}],
    "entityCount": 2, "selectedEntityId": "entity-1", "pathPoints": [{}, {}, {}],
    "pois": [{}], "fences": [{}, {}], "drawings": [{"type": "polygon"}],
    "tilesLoaded": True, "loading": False, "webglError": None,
}


class Bridge:
    adapter_name = "gaealavic_cesium"
    def capture_state(self, _page, *, phase: str):
        return {"adapter": self.adapter_name, "phase": phase, "sceneState": SCENE}


def test_canvas_gesture_models_enforce_relative_geometry_and_stability() -> None:
    polygon = Step.model_validate({
        "action": "visual_draw_polygon", "execution_mode": "visual", "stability_level": "C",
        "visual_target": "围栏", "canvas_region_locator": {"css": "canvas"},
        "visual_points": [{"xRatio": .1, "yRatio": .1}, {"xRatio": .9, "yRatio": .1}, {"xRatio": .5, "yRatio": .9}],
    })
    assert len(polygon.visual_points) == 3
    with pytest.raises(ValidationError, match="至少需要 3"):
        Step.model_validate({
            "action": "visual_draw_polygon", "execution_mode": "visual", "stability_level": "C",
            "visual_target": "围栏", "canvas_region_locator": {"css": "canvas"},
            "visual_points": [{"xRatio": .1, "yRatio": .1}, {"xRatio": .9, "yRatio": .1}],
        })
    with pytest.raises(ValidationError, match="只能为 B 或 C"):
        Step.model_validate({
            "action": "visual_zoom", "execution_mode": "visual", "stability_level": "A",
            "visual_target": "地图", "canvas_region_locator": {"css": "canvas"},
            "relative_position": {"xRatio": .5, "yRatio": .5},
        })


@pytest.mark.parametrize(("assertion", "actual"), [
    (Assertion(type=AssertionType.CANVAS_LAYER_VISIBLE, expected="base"), "layer="),
    (Assertion(type=AssertionType.CANVAS_CAMERA_EQUALS, expected='{"longitude":116.30005}', tolerance=.001), "camera="),
    (Assertion(type=AssertionType.CANVAS_ENTITY_COUNT, count=2), "entityCount=2"),
    (Assertion(type=AssertionType.CANVAS_SELECTED_ENTITY, expected="entity-1"), "selectedEntityId="),
    (Assertion(type=AssertionType.CANVAS_PATH_POINT_COUNT, count=3), "pathPoints=3"),
    (Assertion(type=AssertionType.CANVAS_POI_COUNT, count=1), "pois=1"),
    (Assertion(type=AssertionType.CANVAS_FENCE_COUNT, count=2), "fences=2"),
    (Assertion(type=AssertionType.CANVAS_DRAWING_COUNT, count=1), "drawings=1"),
    (Assertion(type=AssertionType.CANVAS_TILES_LOADED), "tilesLoaded=True"),
    (Assertion(type=AssertionType.CANVAS_WEBGL_NO_ERROR), "webglError=None"),
])
def test_canvas_semantic_assertions_use_normalized_bridge_state(assertion: Assertion, actual: str) -> None:
    outcome = check_assertion(object(), assertion, bridge_adapter=Bridge())
    assert outcome.passed is True
    assert actual in outcome.actual
    assert outcome.semantic_evidence["adapter"] == "gaealavic_cesium"


def test_gaealavic_adapter_rejects_incomplete_semantic_state() -> None:
    GAEALaViCCesiumBridgeAdapter._validate_scene(SCENE)
    with pytest.raises(Exception, match="缺少语义状态"):
        GAEALaViCCesiumBridgeAdapter._validate_scene({"camera": {}, "layers": [], "tilesLoaded": True})


def test_semantic_assertion_failure_creates_specific_finding() -> None:
    assertion = AssertionResult(
        index=1, type="canvas_webgl_no_error", status=Status.FAILED,
        detail="canvas_webgl_no_error", expected_summary="无 WebGL 错误",
        actual_summary="webglError='context lost'",
        semantic_evidence={"type": "canvas_webgl_no_error", "classification": "environment_or_product", "sceneState": {**SCENE, "webglError": "context lost"}},
    )
    findings = build_findings([], [assertion], [])
    assert [item.category for item in findings] == ["canvas_webgl_error"]
    assert "environment_or_product" in findings[0].inference
