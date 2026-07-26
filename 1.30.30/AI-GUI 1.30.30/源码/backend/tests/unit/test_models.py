import pytest
from pydantic import ValidationError

from gui_agent.domain.models import ActionType, ExecutionMode, Locator, RelativePosition, StabilityLevel, Step


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


def test_commerce_scope_requires_structured_locator_action() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="Add to cart"),
        commerceScope={
            "kind": "product_card",
            "container": {"css": "[data-product-card]"},
            "anchor": {"text": "E2E Product A"},
            "excludedMarkers": [{"css": "[data-ad]"}],
            "maxScrollAttempts": 3,
        },
    )
    assert step.commerce_scope.kind == "product_card"
    assert step.commerce_scope.max_scroll_attempts == 3

    with pytest.raises(ValidationError, match="作用域"):
        Step(
            action=ActionType.SCREENSHOT,
            commerceScope={
                "kind": "product_card",
                "container": {"css": "article"},
                "anchor": {"text": "Product"},
            },
        )


def test_human_takeover_requires_d_level_and_resume_condition() -> None:
    with pytest.raises(ValidationError, match="D 级"):
        Step(
            action="human_takeover",
            takeoverReason="qr_login",
            browserTarget={"urlContains": "/account"},
        )
    step = Step(
        action="human_takeover",
        takeoverReason="qr_login",
        browserTarget={"urlContains": "/account"},
        stability_level="D",
        stability_reason="扫码登录必须人工接管",
    )
    assert step.browser_target.url_contains == "/account"
