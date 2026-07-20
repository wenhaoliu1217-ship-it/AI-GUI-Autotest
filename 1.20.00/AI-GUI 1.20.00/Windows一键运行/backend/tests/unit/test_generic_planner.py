import pytest

from gui_agent.planning.generic_planner import PlanningError, plan_from_draft


def test_generates_reviewable_actions_and_assertions() -> None:
    result = plan_from_draft(
        name="登录检查",
        target_url="http://127.0.0.1:8765",
        flow="在“用户名”输入“admin”；点击“登录”；截图",
        expectation="确认看到“客户管理”",
    )
    assert [step.action.value for step in result.plan.steps] == [
        "navigate", "fill", "click", "screenshot"
    ]
    assert result.plan.assertions[0].type.value == "visible"
    assert len(result.plan.assertions) == 1
    assert result.warnings == []


def test_rejects_ambiguous_flow_instead_of_fabricating_success() -> None:
    with pytest.raises(PlanningError, match="没有识别出"):
        plan_from_draft(
            name="模糊流程",
            target_url="https://example.com",
            flow="帮我把整个网站都测试一下",
        )
