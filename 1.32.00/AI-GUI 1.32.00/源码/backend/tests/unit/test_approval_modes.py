from gui_agent.domain.models import ActionType, Step
from gui_agent.execution.agent_runner import _approval_rule


def test_ask_mode_confirms_every_form_write() -> None:
    step = Step(action=ActionType.FILL, locator={"label": "姓名"}, value="E2E_测试用户")
    assert _approval_rule(step, "ask", None) == "approval-mode:write-action"


def test_delegate_and_full_keep_existing_safety_confirmation() -> None:
    step = Step(action=ActionType.CLICK, locator={"role": "button", "name": "提交订单"})
    assert _approval_rule(step, "delegate", "提交订单") == "提交订单"
    assert _approval_rule(step, "full", "提交订单") == "提交订单"


def test_delegate_does_not_pause_for_unclassified_low_risk_form_write() -> None:
    step = Step(action=ActionType.SELECT, locator={"label": "颜色"}, value="蓝色")
    assert _approval_rule(step, "delegate", None) is None


def test_cesium_reversible_click_follows_beginner_approval_mode() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator={"role": "button", "name": "Create story"},
        effect_kind="create_story",
        effect_level="reversible_write",
        cleanup_action="delete the ledger-owned E2E story",
    )
    assert _approval_rule(step, "ask", None) == "approval-mode:site-write:create_story"
    assert _approval_rule(step, "delegate", None) is None
