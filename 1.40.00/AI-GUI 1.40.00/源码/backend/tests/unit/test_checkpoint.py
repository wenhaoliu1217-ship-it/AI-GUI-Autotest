from datetime import datetime, timezone

from gui_agent.domain.models import ActionType, Locator, Step
from gui_agent.domain.results import Observation, PageHealth, Status, StepResult
from gui_agent.execution.checkpoint import build_checkpoint, verify_checkpoint_page


def _observation(*, title: str = "测试页") -> Observation:
    return Observation(
        url="https://example.test/orders",
        title=title,
        dom_summary=["orders-table"],
        accessibility_summary="订单列表",
        page_health=PageHealth(
            ready_state="complete",
            visible_text_length=20,
            visible_element_count=3,
            interactive_count=3,
        ),
    )


def _step_result(index: int, action: str, observation: Observation, **kwargs) -> StepResult:
    now = datetime.now(timezone.utc)
    return StepResult(
        index=index,
        action=action,
        target_summary=action,
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
        before=observation,
        after=observation,
        **kwargs,
    )


def test_checkpoint_marks_only_verified_read_only_steps_as_safe_to_skip():
    observation = _observation()
    steps = [
        _step_result(1, ActionType.NAVIGATE.value, observation),
        _step_result(
            2,
            ActionType.FILL.value,
            observation,
            side_effect_evidence={"field": "order_id", "value_present": True},
        ),
    ]
    executed_steps = [
        Step(action=ActionType.NAVIGATE, target="/orders"),
        Step(action=ActionType.FILL, locator=Locator(label="订单号"), value="E2E-001"),
    ]

    checkpoint = build_checkpoint(
        run_id="run-2",
        resume_from_run_id="run-1",
        status=Status.SYSTEM_ERROR,
        observation=observation,
        steps=steps,
        executed_steps=executed_steps,
        current_goal="查询订单",
    )

    assert [item["index"] for item in checkpoint["safeReadOnlySteps"]] == [1]
    assert checkpoint["lastSafeStepIndex"] == 1
    assert [item["index"] for item in checkpoint["pendingWriteRevalidations"]] == [2]
    assert checkpoint["recoveryPolicy"]["skipOnlyVerifiedReadOnly"] is True
    assert checkpoint["recoveryPolicy"]["revalidateWritesBeforeContinue"] is True


def test_checkpoint_page_verification_fails_closed_on_page_change():
    observation = _observation()
    checkpoint = build_checkpoint(
        run_id="run-1",
        status=Status.SYSTEM_ERROR,
        observation=observation,
        steps=[],
        executed_steps=[],
        current_goal="查询订单",
    )

    assert verify_checkpoint_page(checkpoint, observation)["verified"] is True
    mismatch = verify_checkpoint_page(checkpoint, _observation(title="登录页"))
    assert mismatch["verified"] is False
    assert mismatch["sameUrl"] is True
    assert mismatch["fingerprintMatch"] is False
