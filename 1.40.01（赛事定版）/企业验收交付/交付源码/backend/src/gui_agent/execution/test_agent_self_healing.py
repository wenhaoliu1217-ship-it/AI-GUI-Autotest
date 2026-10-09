from __future__ import annotations

import pytest

from gui_agent.domain.models import ActionType, ExecutionMode, Locator, RelativePosition, StabilityLevel, Step
from gui_agent.domain.results import FailureCategory
from gui_agent.execution.agent_runner import (
    _check_agent_step,
    _ground_cesium_start_request_to_locator,
    _refresh_visual_step,
    _terminal_target_service_failure,
    _should_replan_after_locator_failure,
)
from gui_agent.execution.stability import prepare_action
from gui_agent.planning.visual_adapter import VisualSuggestion
from gui_agent.planning.agent_planner import VisualRequest
from gui_agent.security.policy import SecurityError


class _FakeButton:
    def __init__(self, visible: bool = True, enabled: bool = True) -> None:
        self._visible = visible
        self._enabled = enabled

    def is_visible(self) -> bool:
        return self._visible

    def is_enabled(self) -> bool:
        return self._enabled


class _FakeButtons:
    def __init__(self, buttons: list[_FakeButton]) -> None:
        self._buttons = buttons

    def count(self) -> int:
        return len(self._buttons)

    def nth(self, index: int) -> _FakeButton:
        return self._buttons[index]


class _FakePage:
    def __init__(self, url: str, buttons: list[_FakeButton]) -> None:
        self.url = url
        self._buttons = _FakeButtons(buttons)

    def get_by_role(self, role: str, **kwargs):
        assert role == "button"
        assert kwargs == {"name": "启动", "exact": True}
        return self._buttons


def test_pointer_interception_without_side_effect_returns_to_agent() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator=Locator(role="combobox"),
        description="Open the currently observed selector",
    )

    assert _should_replan_after_locator_failure(
        step,
        FailureCategory.TIMEOUT,
        {"checked": True, "passed": True},
        "span intercepts pointer events",
        None,
    ) is True


@pytest.mark.parametrize(
    "action,value",
    [
        (ActionType.FILL, "value"),
        (ActionType.SELECT, "option"),
        (ActionType.CLEAR, None),
        (ActionType.CHECK, None),
        (ActionType.UNCHECK, None),
        (ActionType.HOVER, None),
        (ActionType.PRESS, "Enter"),
        (ActionType.UPLOAD_FILE, None),
        (ActionType.DOWNLOAD, None),
    ],
)
def test_every_pre_action_locator_failure_returns_to_agent(action, value) -> None:
    kwargs = {"action": action, "locator": Locator(role="textbox")}
    if value is not None:
        kwargs["value"] = value
    if action == ActionType.UPLOAD_FILE:
        kwargs["file_asset_ref"] = "asset:" + "a" * 64
    step = Step(**kwargs)

    assert _should_replan_after_locator_failure(
        step,
        FailureCategory.LOCATOR,
        {"checked": True, "passed": False},
        "target did not match",
        None,
        "pre_action",
    ) is True


def test_hard_security_rejection_never_replans() -> None:
    step = Step(action=ActionType.CLICK, locator=Locator(role="button", name="Pay"))

    assert _should_replan_after_locator_failure(
        step,
        FailureCategory.SECURITY,
        {"checked": True, "passed": False},
        "forbidden action",
        None,
        "pre_action",
    ) is False


def test_possible_side_effect_is_never_blindly_replanned() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="Create"),
        description="Final create",
        action_category="create",
        object_type="model",
        business_object_name="E2E_TEST_MODEL",
    )

    assert _should_replan_after_locator_failure(
        step,
        FailureCategory.TIMEOUT,
        {"checked": True, "passed": True},
        "span intercepts pointer events",
        {"decision": "conditional"},
    ) is False


def test_execution_guard_rejects_unrequested_creation() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="Create"),
        description="Confirm create",
    )

    with pytest.raises(SecurityError, match="not authorized"):
        _check_agent_step(step, (), create_authorized=False)

    _check_agent_step(step, (), create_authorized=True)


def test_execution_guard_reports_direct_visual_action_truthfully() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="Unlabeled visual target"),
        execution_mode=ExecutionMode.VISUAL,
    )

    with pytest.raises(SecurityError, match="未经过受控视觉适配器"):
        _check_agent_step(step, (), visual_authorized=False)

    _check_agent_step(step, (), visual_authorized=True)


def test_visual_reground_updates_pydantic_step_without_dataclass_replace() -> None:
    """The live visual retry must reach stability through Pydantic's copy API."""

    original = Step(
        action=ActionType.VISUAL_CLICK,
        execution_mode=ExecutionMode.VISUAL,
        stability_level=StabilityLevel.B,
        visual_target="动力学右侧加号",
        relative_position=RelativePosition(xRatio=0.10, yRatio=0.20),
    )
    refreshed = _refresh_visual_step(
        original,
        VisualSuggestion(
            target="动力学右侧加号",
            action="click",
            x_ratio=0.84,
            y_ratio=0.41,
            expected_change="动力学配置面板出现",
            confidence=0.95,
            rationale="当前截图中目标清晰可见",
        ),
    )
    page = type(
        "ViewportPage",
        (),
        {"viewport_size": {"width": 1440, "height": 900}},
    )()
    prepared = prepare_action(
        page,
        refreshed,
        bridge_adapter=None,
        timeout_ms=1_000,
    )

    assert original.relative_position == RelativePosition(xRatio=0.10, yRatio=0.20)
    assert refreshed.relative_position == RelativePosition(xRatio=0.84, yRatio=0.41)
    assert refreshed.visual_expected_change == "动力学配置面板出现"
    assert prepared.evidence["mode"] == "visual_viewport"
    assert prepared.evidence["passed"] is True


def test_cesium_run_start_visual_request_is_grounded_to_unique_locator() -> None:
    page = _FakePage(
        "http://192.168.31.218:7991/#/situationPage?type=run&simulationStatus=Unstart",
        [_FakeButton()],
    )
    request = VisualRequest(
        target="顶部中间的蓝色启动按钮",
        trigger_reason="运行模式下启动仿真",
        preferred_action="click",
        expected_change="仿真状态变为 Running",
    )

    step, evidence = _ground_cesium_start_request_to_locator(page, request)

    assert step is not None
    assert step.action is ActionType.CLICK
    assert step.execution_mode is ExecutionMode.LOCATOR
    assert step.locator == Locator(role="button", name="启动", exact=True)
    assert evidence["eligible"] is True
    assert evidence["candidateCount"] == 1
    assert evidence["visibleCandidateCount"] == 1
    assert evidence["enabledCandidateCount"] == 1


def test_cesium_grounding_preserves_original_safety_metadata() -> None:
    page = _FakePage(
        "http://192.168.31.218:7991/#/situationPage?type=run&simulationStatus=Unstart",
        [_FakeButton()],
    )
    request = VisualRequest(
        target="顶部中间的蓝色启动按钮",
        trigger_reason="运行模式下启动仿真",
        preferred_action="click",
        expected_change="仿真状态变为 Running",
    )
    original = Step(
        action=ActionType.VISUAL_CLICK,
        execution_mode=ExecutionMode.VISUAL,
        stability_level=StabilityLevel.C,
        visual_target="启动",
        relative_position={"xRatio": 0.5, "yRatio": 0.1},
        description="视觉点击启动",
        action_category="start_simulation",
        object_type="scenario",
        business_object_name="E2E_test_D",
        effect_kind="start_simulation",
        effect_level="reversible_write",
    )

    grounded, _ = _ground_cesium_start_request_to_locator(
        page, request, base_step=original
    )

    assert grounded is not None
    assert grounded.execution_mode is ExecutionMode.LOCATOR
    assert grounded.locator == Locator(role="button", name="启动", exact=True)
    assert grounded.action_category == original.action_category
    assert grounded.object_type == original.object_type
    assert grounded.business_object_name == original.business_object_name
    assert grounded.effect_kind == original.effect_kind
    assert grounded.effect_level == original.effect_level
    assert grounded.computer_use_triggered is False
    assert grounded.computer_use_reason is None
    # The returned object must remain valid if it crosses the normal
    # serialization boundary used by the isolated Runner.
    assert Step.model_validate(grounded.model_dump(mode="json")).locator == grounded.locator


@pytest.mark.parametrize(
    "url,buttons,preferred_action,expected_reason",
    [
        (
            "http://192.168.31.218:7991/#/situationPage?type=edit&simulationStatus=Unstart",
            [_FakeButton()],
            "click",
            "run_mode_unstart_guard_failed",
        ),
        (
            "http://192.168.31.218:7991/#/situationPage?type=run&simulationStatus=Running",
            [_FakeButton()],
            "click",
            "run_mode_unstart_guard_failed",
        ),
        (
            "http://192.168.31.218:7991/#/situationPage?type=run&simulationStatus=Unstart",
            [_FakeButton(), _FakeButton()],
            "click",
            "start_button_not_unique_visible_enabled",
        ),
        (
            "http://192.168.31.218:7991/#/situationPage?type=run&simulationStatus=Unstart",
            [_FakeButton(enabled=False)],
            "click",
            "start_button_not_unique_visible_enabled",
        ),
        (
            "http://192.168.31.218:7991/#/situationPage?type=run&simulationStatus=Unstart",
            [_FakeButton()],
            "hover",
            "request_is_not_start_click",
        ),
    ],
)
def test_cesium_start_locator_fallback_fails_closed_for_unsafe_context(
    url: str,
    buttons: list[_FakeButton],
    preferred_action: str,
    expected_reason: str,
) -> None:
    page = _FakePage(url, buttons)
    request = VisualRequest(
        target="启动仿真",
        trigger_reason="运行模式下的视觉目标",
        preferred_action=preferred_action,
    )

    step, evidence = _ground_cesium_start_request_to_locator(page, request)

    assert step is None
    assert evidence["eligible"] is False
    assert evidence["reason"] == expected_reason


def test_target_service_501_failure_is_terminal_and_non_replayable() -> None:
    evidence = {
        "status": "failed",
        "facts": [
            "target_service_not_implemented",
            "click_dispatched",
            "target_simulation_status=Unstart",
            "automatic_replay_allowed=false",
        ],
    }

    terminal = _terminal_target_service_failure(evidence)

    assert terminal is not None
    assert terminal["automaticReplayAllowed"] is False
    assert terminal["targetSimulationStatus"] == "Unstart"


def test_non_target_failure_does_not_trigger_terminal_501_path() -> None:
    assert _terminal_target_service_failure({
        "status": "failed",
        "facts": ["runtime_failure:HTTP 500 POST /api/other"],
    }) is None


def test_501_classification_without_dispatch_proof_is_not_terminal() -> None:
    assert _terminal_target_service_failure({
        "status": "failed",
        "facts": [
            "target_service_not_implemented",
            "target_simulation_status=Unstart",
            "automatic_replay_allowed=false",
        ],
    }) is None
