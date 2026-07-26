from datetime import datetime, timezone

from gui_agent.domain.results import AssertionResult, Observation, PageIssue, Status, StepResult
from gui_agent.execution.findings import build_findings


def test_visibility_finding_uses_description_instead_of_none_string() -> None:
    findings = build_findings([], [AssertionResult(
        index=1,
        type="visible",
        description="客户管理标题可见",
        detail="visible",
        status=Status.FAILED,
        expected_summary="None",
        actual_summary="visible=False",
    )], [])

    assert findings[0].expected_result == "客户管理标题可见"


def test_page_issue_and_request_failure_include_timeline_and_classification() -> None:
    now = datetime.now(timezone.utc)
    step = StepResult(
        index=1,
        action="click",
        description="提交",
        target_summary="button text=提交",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
        screenshot="screenshots/after.png",
        before=Observation(url="https://example.com/form", screenshot="screenshots/before.png", captured_at=now),
        after=Observation(
            url="https://example.com/form",
            screenshot="screenshots/after.png",
            captured_at=now,
            failed_requests=["HTTP 503 POST https://example.com/api/orders"],
            page_issues=[PageIssue(
                kind="element_obscured",
                severity="High",
                confidence="high",
                message="交互控件中心点被其他元素遮挡，可能无法点击",
                target="button | text=提交",
            )],
        ),
    )

    findings = build_findings([step], [], ["点击提交"])

    assert len(findings) == 2
    runtime = next(item for item in findings if item.category == "runtime")
    obscured = next(item for item in findings if item.category == "element_obscured")
    assert runtime.facts[0].startswith("服务端请求失败")
    assert [event.phase for event in runtime.evidence_timeline] == ["before_action", "after_action"]
    assert obscured.evidence == ["screenshots/before.png", "screenshots/after.png"]


def test_three_no_progress_steps_create_unresponsive_finding() -> None:
    now = datetime.now(timezone.utc)
    steps = [StepResult(
        index=index,
        action="click",
        target_summary=f"目标 {index}",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
        progress_assessment="no_progress",
        after=Observation(url="https://example.com", screenshot=f"screenshots/{index}.png", captured_at=now),
    ) for index in range(1, 4)]

    findings = build_findings(steps, [], ["步骤 1", "步骤 2", "步骤 3"])

    assert len(findings) == 1
    assert findings[0].category == "unresponsive"
    assert len(findings[0].evidence_timeline) == 3


def _canvas_step(evidence: dict, *, status: Status = Status.ERROR, error: str | None = None) -> StepResult:
    now = datetime.now(timezone.utc)
    return StepResult(
        index=2,
        action="bridge_click",
        description="选择实体 Alpha",
        target_summary="entity.alpha",
        status=status,
        started_at=now,
        ended_at=now,
        error_message=error,
        execution_mode="app_bridge",
        canvas_evidence={
            "mode": "app_bridge",
            "action": "bridge_click",
            "semanticTarget": "entity.alpha",
            "beforeScreenshot": "screenshots/before.png",
            "afterScreenshot": "screenshots/after.png",
            **evidence,
        },
        before=Observation(url="https://example.com", screenshot="screenshots/before.png", captured_at=now),
        after=Observation(url="https://example.com", screenshot="screenshots/after.png", captured_at=now),
    )


def test_bridge_contract_error_replaces_generic_step_failure() -> None:
    error = "Bridge 缺少最小契约方法：waitForSceneReady"
    step = _canvas_step({"collectionStatus": "failed", "failurePhase": "stability", "error": error}, error=error)

    findings = build_findings([step], [], ["打开页面", "选择实体 Alpha"])

    assert [item.category for item in findings] == ["bridge_contract_error"]
    assert any("waitForSceneReady" in fact for fact in findings[0].facts)
    assert findings[0].confidence == "high"


def test_canvas_selection_mismatch_is_deterministic_finding() -> None:
    step = _canvas_step({
        "collectionStatus": "complete",
        "selectedTargetBefore": None,
        "selectedTargetAfter": "entity.beta",
        "semanticStateVerified": True,
    }, status=Status.PASSED)

    findings = build_findings([step], [], ["打开页面", "选择实体 Alpha"])

    assert [item.category for item in findings] == ["canvas_selection_mismatch"]
    assert 'selectedTargetAfter="entity.beta"' in findings[0].facts


def test_canvas_scene_loading_and_tiles_not_loaded_create_scene_finding() -> None:
    step = _canvas_step({
        "collectionStatus": "complete",
        "sceneAfter": {"loading": True, "tilesLoaded": False},
        "selectedTargetAfter": "entity.alpha",
        "semanticStateVerified": True,
    }, status=Status.PASSED)

    findings = build_findings([step], [], ["打开页面", "选择实体 Alpha"])

    assert [item.category for item in findings] == ["canvas_scene_not_ready"]
    assert "sceneAfter.loading=true" in findings[0].facts
    assert "sceneAfter.tilesLoaded=false" in findings[0].facts


def test_canvas_semantic_state_unchanged_creates_specialized_finding() -> None:
    step = _canvas_step({
        "collectionStatus": "complete",
        "selectedTargetAfter": "entity.alpha",
        "semanticStateVerified": False,
    }, status=Status.PASSED)

    findings = build_findings([step], [], ["打开页面", "选择实体 Alpha"])

    assert [item.category for item in findings] == ["canvas_state_unchanged"]
    assert "semanticStateVerified=false" in findings[0].facts


def test_valid_bridge_evidence_does_not_create_finding() -> None:
    step = _canvas_step({
        "collectionStatus": "complete",
        "sceneBefore": {"loading": False, "tilesLoaded": True},
        "sceneAfter": {"loading": False, "tilesLoaded": True},
        "visibleTargets": [{"id": "entity.alpha"}],
        "selectedTargetBefore": None,
        "selectedTargetAfter": "entity.alpha",
        "semanticStateVerified": True,
    }, status=Status.PASSED)

    assert build_findings([step], [], ["打开页面", "选择实体 Alpha"]) == []


def test_canvas_resource_failure_is_reported_from_direct_scene_state() -> None:
    step = _canvas_step({
        "collectionStatus": "complete",
        "sceneAfter": {"loading": False, "tilesLoaded": True, "resourceFailures": ["terrain/0/0/0"]},
        "selectedTargetAfter": "entity.alpha",
        "semanticStateVerified": True,
    }, status=Status.PASSED)

    findings = build_findings([step], [], ["打开页面", "选择实体 Alpha"])

    assert [item.category for item in findings] == ["canvas_resource_failure"]
    assert any("resourceFailures" in fact for fact in findings[0].facts)


def test_canvas_target_visibility_and_interactivity_have_distinct_categories() -> None:
    visibility_error = "Bridge 可见目标中不存在：entity.alpha"
    invisible = _canvas_step({
        "collectionStatus": "failed",
        "visibleTargets": [{"id": "entity.beta"}],
        "error": visibility_error,
    }, error=visibility_error)
    position_error = "Bridge 未返回有效目标位置"
    not_interactive = _canvas_step({
        "collectionStatus": "failed",
        "visibleTargets": [{"id": "entity.alpha"}],
        "error": position_error,
    }, error=position_error)

    invisible_findings = build_findings([invisible], [], ["打开页面", "选择实体 Alpha"])
    interaction_findings = build_findings([not_interactive], [], ["打开页面", "选择实体 Alpha"])

    assert [item.category for item in invisible_findings] == ["canvas_target_not_visible"]
    assert [item.category for item in interaction_findings] == ["canvas_target_not_interactive"]
