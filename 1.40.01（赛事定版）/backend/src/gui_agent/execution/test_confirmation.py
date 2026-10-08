from gui_agent.domain.models import ActionType, Locator, Step
from gui_agent.execution.confirmation import confirmation_match


def test_chinese_negative_action_list_does_not_request_delete_confirmation() -> None:
    step = Step(
        action=ActionType.SELECT,
        locator=Locator(role="combobox", name="创建人"),
        value="全部人员",
        description="只读筛选；不新建、编辑、保存、删除或启动训练。",
    )

    assert confirmation_match(step) is None


def test_positive_delete_intent_still_requests_confirmation() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="删除"),
        description="删除当前配置",
    )

    assert confirmation_match(step) == "删除"
