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


def test_duplicate_expectation_is_not_reported_as_unparseable() -> None:
    result = plan_from_draft(
        name="公开测试站登录验收",
        target_url="https://the-internet.herokuapp.com",
        flow=(
            "打开“/login”；在“Username”输入“tomsmith”；"
            "在“Password”输入“SuperSecretPassword!”；点击“Login”；"
            "确认看到“Secure Area”；截图"
        ),
        expectation="确认看到“Secure Area”",
    )

    assert result.warnings == []
    assert len(result.plan.assertions) == 1
    assert result.plan.assertions[0].locator.text == "Secure Area"
    password_step = result.plan.steps[3]
    assert password_step.value is None
    assert password_step.value_from_secret == "TEST_PASSWORD"
    assert "SuperSecretPassword" not in password_step.description


def test_negative_constraints_never_become_fill_or_click_actions() -> None:
    result = plan_from_draft(
        name="京东公开站只读检查",
        target_url="https://www.jd.com/",
        flow="截图；不登录，不填写，不点击任何会产生账户或交易副作用的控件；确认看到“京东”",
        expectation="确认看到“京东”",
    )

    assert [step.action.value for step in result.plan.steps] == ["navigate", "screenshot"]
    assert len(result.plan.assertions) == 1
    assert result.warnings == []
