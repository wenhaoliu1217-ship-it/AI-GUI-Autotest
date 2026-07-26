import pytest
from pydantic import ValidationError

from gui_agent.domain.models import ActionType, ExecutionMode, Locator, RelativePosition, StabilityLevel, Step, WaitCondition


def test_locator_requires_strategy() -> None:
    with pytest.raises(ValidationError):
        Locator()


def test_fill_requires_value() -> None:
    with pytest.raises(ValidationError):
        Step(action=ActionType.FILL, locator=Locator(label="用户名"))


def test_secret_and_plain_value_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        Step(
            action=ActionType.FILL,
            locator=Locator(label="用户名"),
            value="admin",
            value_from_secret="ADMIN_USERNAME",
        )


def test_visual_click_uses_relative_canvas_position() -> None:
    step = Step(
        action=ActionType.VISUAL_CLICK,
        execution_mode=ExecutionMode.VISUAL,
        stability_level=StabilityLevel.C,
        locator=Locator(css="canvas"),
        visual_target="地图上的目标 A",
        relative_position=RelativePosition(xRatio=0.4, yRatio=0.6),
        computer_use_triggered=True,
        computer_use_reason="DOM 无法表达 Canvas 目标",
    )
    assert step.relative_position.x_ratio == 0.4
    assert "x" not in step.model_dump(exclude_none=True)


def test_visual_click_rejects_absolute_or_out_of_bounds_coordinates() -> None:
    with pytest.raises(ValidationError):
        Step(
            action=ActionType.VISUAL_CLICK,
            execution_mode=ExecutionMode.VISUAL,
            stability_level=StabilityLevel.C,
            locator=Locator(css="canvas"),
            visual_target="目标",
            relative_position={"xRatio": 120, "yRatio": 10},
        )


def test_file_and_business_wait_actions_require_bounded_inputs() -> None:
    upload = Step(
        action=ActionType.UPLOAD_FILES,
        locator=Locator(css="input[type=file]", shadow_hosts=["ion-upload-form"]),
        file_ids=["D01/cesium-e2e-model.glb"],
    )
    wait = Step(
        action=ActionType.WAIT_UNTIL,
        wait_condition=WaitCondition(
            source="attribute",
            locator=Locator(test_id="tiling-status"),
            attribute="data-status",
            expected="COMPLETE",
        ),
    )

    assert upload.locator.shadow_hosts == ["ion-upload-form"]
    assert wait.wait_condition.timeout_ms == 120_000
    with pytest.raises(ValidationError):
        Step(action=ActionType.UPLOAD_FILES, locator=Locator(css="input[type=file]"))
    with pytest.raises(ValidationError):
        WaitCondition(source="attribute", expected="COMPLETE", locator=Locator(css="#status"))
