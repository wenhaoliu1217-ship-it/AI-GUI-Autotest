from datetime import datetime, timezone
from types import SimpleNamespace

from PIL import Image

from gui_agent.domain.results import Status, StepResult
from gui_agent.execution.findings import _build_canvas_findings
from gui_agent.execution.stability import (
    attach_rendering_evidence,
    attach_visual_delta_evidence,
    strict_3d_evidence_passed,
)


def _observation(canvas: dict) -> SimpleNamespace:
    return SimpleNamespace(semantic_summary=SimpleNamespace(canvas=canvas))


def _canvas_step(rendering: dict) -> StepResult:
    now = datetime.now(timezone.utc)
    return StepResult(
        index=1,
        action="visual_click",
        target_summary="3D canvas",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
        execution_mode="visual",
        canvas_evidence={
            "mode": "visual",
            "action": "visual_click",
            "collectionStatus": "complete",
            "renderingEvidence": rendering,
            "renderingVerified": bool(rendering.get("nonEmptyPixels")),
        },
    )


def test_attach_rendering_evidence_marks_non_empty_webgl_as_verified() -> None:
    evidence = attach_rendering_evidence(
        {"mode": "visual"},
        _observation({
            "count": 1,
            "surfaces": [{"webgl": True, "webglVersion": "webgl2"}],
            "nonEmptySurface": True,
            "webglSurfaceCount": 1,
            "nonEmptyPixels": True,
            "contextLost": False,
            "renderingEvidence": "webgl_non_empty_pixels",
        }),
    )

    assert evidence is not None
    assert evidence["renderingVerified"] is True
    assert evidence["renderingEvidence"]["webglSurfaceCount"] == 1
    assert evidence["renderingEvidence"]["renderingEvidence"] == "webgl_non_empty_pixels"


def test_canvas_finding_reports_empty_webgl_pixels() -> None:
    step = _canvas_step({
        "count": 1,
        "webglSurfaceCount": 1,
        "nonEmptyPixels": False,
        "contextLost": False,
        "renderingEvidence": "surface_dimensions_only",
    })

    findings = _build_canvas_findings(step, ["进入 3D 页面"])

    assert [finding.category for finding in findings] == [
        "canvas_main_surface_unbound", "canvas_rendering_unverified"
    ]


def test_canvas_finding_reports_lost_webgl_context() -> None:
    step = _canvas_step({
        "count": 1,
        "webglSurfaceCount": 1,
        "nonEmptyPixels": False,
        "contextLost": True,
        "renderingEvidence": "none",
    })

    findings = _build_canvas_findings(step, ["进入 3D 页面"])

    assert [finding.category for finding in findings] == [
        "canvas_main_surface_unbound", "canvas_context_lost"
    ]


def test_visual_delta_is_measured_only_inside_bound_canvas(tmp_path) -> None:
    Image.new("RGB", (100, 100), "black").save(tmp_path / "before.png")
    after = Image.new("RGB", (100, 100), "black")
    for x in range(20, 80):
        for y in range(20, 80):
            after.putpixel((x, y), (255, 255, 255))
    after.save(tmp_path / "after.png")
    evidence = attach_visual_delta_evidence(
        {
            "beforeScreenshot": "before.png",
            "afterScreenshot": "after.png",
            "renderingEvidence": {
                "targetSelector": ".cesium-widget canvas",
                "targetBounds": {"x": 10, "y": 10, "width": 80, "height": 80},
                "targetViewport": {"width": 100, "height": 100},
            },
        },
        SimpleNamespace(run_dir=tmp_path),
    )

    assert evidence is not None
    assert evidence["visualDelta"]["available"] is True
    assert evidence["visualDelta"]["passed"] is True
    assert evidence["visualDelta"]["region"] == {"left": 10, "top": 10, "right": 90, "bottom": 90}


def test_strict_3d_evidence_accepts_enum_step_modes() -> None:
    step = _canvas_step({
        "targetSelector": ".cesium-widget canvas",
        "targetFrameUrl": "https://ion.cesium.com/stories/example",
        "webglSurfaceCount": 1,
        "contextLost": False,
    })
    step.canvas_evidence["visualDelta"] = {"passed": True}

    assert strict_3d_evidence_passed([step]) is True


def test_strict_3d_evidence_rejects_missing_delta() -> None:
    step = _canvas_step({
        "targetSelector": ".cesium-widget canvas",
        "targetFrameUrl": "https://ion.cesium.com/stories/example",
        "webglSurfaceCount": 1,
        "contextLost": False,
    })

    assert strict_3d_evidence_passed([step]) is False
