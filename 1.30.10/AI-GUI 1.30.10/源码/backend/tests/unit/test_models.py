import pytest
from pydantic import ValidationError

from gui_agent.domain.models import ActionType, ExecutionMode, Locator, RelativePosition, StabilityLevel, Step


def test_locator_requires_strategy() -> None:
    with pytest.raises(ValidationError):
        Locator()


def test_locator_supports_placeholder_stable_attribute_and_business_scope() -> None:
    locator = Locator(
        role="button",
        name="删除",
        scope={
            "kind": "row",
            "locator": {"attribute": {"name": "data-object-id", "value": "agent-42"}},
            "identity": "E2E_Agent_42",
        },
    )

    assert locator.scope and locator.scope.kind == "row"
    assert "data-object-id=agent-42" in locator.describe()
    assert Locator(placeholder="请输入租户").placeholder == "请输入租户"


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


def test_file_transfer_steps_require_registered_file_and_e2e_object() -> None:
    upload = Step.model_validate({
        "action": "upload", "locator": {"label": "Agent JSON"},
        "file_id": "file-0123456789ab", "business_object_name": "E2E_Agent",
        "expected_file_validity": "valid",
    })
    download = Step.model_validate({
        "action": "download", "locator": {"role": "link", "name": "Export"},
        "business_object_name": "E2E_Run_1",
        "download_validation": {"extension": ".json", "format": "json", "requiredJsonKeys": ["runId"]},
    })

    assert upload.action == ActionType.UPLOAD
    assert download.download_validation.required_json_keys == ["runId"]
    with pytest.raises(ValidationError, match="E2E_ 前缀"):
        Step.model_validate({
            "action": "upload", "locator": {"label": "Agent JSON"},
            "file_id": "file-0123456789ab", "business_object_name": "production-agent",
        })
    with pytest.raises(ValidationError, match="residual_object_locator"):
        Step.model_validate({
            "action": "upload", "locator": {"label": "Agent JSON"},
            "file_id": "file-0123456789ab", "business_object_name": "E2E_Bad",
            "expected_file_validity": "invalid",
        })
