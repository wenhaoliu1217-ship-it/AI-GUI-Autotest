from __future__ import annotations

from types import SimpleNamespace

from gui_agent.domain.models import ActionType, Locator, Step
from gui_agent.planning.task_authorization import (
    derive_task_authorization,
    is_creation_step,
)


def _scenario(goal: str):
    return SimpleNamespace(
        goal=goal,
        preconditions="",
        expected_results=[],
        clarification_history=[],
    )


def test_creation_requires_current_run_explicit_permission() -> None:
    assert derive_task_authorization(_scenario("Explore the complete modeling workflow")).create_allowed is False
    assert derive_task_authorization(_scenario("允许最终创建一个测试模型")).create_allowed is True


def test_direct_chinese_create_instruction_is_authorized() -> None:
    for goal in (
        "创建独立测试想定",
        "根据当前页面自主探索，创建一个独立测试想定，名称为GUI测试。不得删除已有数据。",
    ):
        assert derive_task_authorization(_scenario(goal)).create_allowed is True
    for goal in ("检查创建一个测试想定所需字段", "创建一个想定，但不要提交", "不要创建独立测试想定"):
        assert derive_task_authorization(_scenario(goal)).create_allowed is False


def test_creation_clarification_is_current_run_authorization() -> None:
    scenario = _scenario("探索想定页面")
    assert derive_task_authorization(scenario).create_allowed is False
    scenario.clarification_history = [{"answer": "确认授权现在点击保存，创建名称为GUI测试的独立测试想定。不授权删除。"}]
    assert derive_task_authorization(scenario).create_allowed is True


def test_explicit_creation_denial_wins_over_workflow_wording() -> None:
    authorization = derive_task_authorization(
        _scenario("检查创建流程，但不要实际创建任何模型")
    )
    assert authorization.create_allowed is False


def test_no_final_click_or_submit_never_grants_creation() -> None:
    goals = (
        "验证创建一个模型时名称必填，但不点击最终创建",
        "测试创建一个模型的页面，但不要提交",
        "进入创建流程，在创建前停止",
    )

    assert all(not derive_task_authorization(_scenario(goal)).create_allowed for goal in goals)


def test_creation_requires_explicit_execution_language() -> None:
    assert derive_task_authorization(
        _scenario("实际创建一个测试模型并点击最终创建")
    ).create_allowed is True


def test_one_create_grant_is_not_cancelled_by_duplicate_creation_guard() -> None:
    goal = (
        "本次测试明确允许创建一个测试模型，并要求最后点击创建。"
        "不得重复创建第二个同名模型。"
    )

    authorization = derive_task_authorization(_scenario(goal))

    assert authorization.create_allowed is True
    assert authorization.source == "goal"
    assert derive_task_authorization(
        _scenario("检查创建一个测试模型所需字段")
    ).create_allowed is False
    assert derive_task_authorization(
        _scenario("请检查创建按钮和创建页面")
    ).create_allowed is False
    assert derive_task_authorization(
        _scenario("请创建一个测试模型")
    ).create_allowed is True


def test_required_submit_wording_is_not_misread_as_create_denial() -> None:
    goal = (
        "本次测试明确允许创建一个测试模型，并要求最后点击创建按钮完成真实创建。"
        "禁止只填写表单而不提交。"
        "不得重复创建第二个同名模型，也不得修改或删除已有模型。"
    )

    authorization = derive_task_authorization(_scenario(goal))

    assert authorization.create_allowed is True
    assert authorization.source == "goal"


def test_scenario_save_uses_the_same_explicit_write_authorization() -> None:
    assert derive_task_authorization(
        _scenario("实际保存想定并返回列表验证")
    ).create_allowed is True
    assert derive_task_authorization(
        _scenario("检查保存想定按钮，但不要提交保存想定")
    ).create_allowed is False


def test_final_create_button_is_guarded_without_model_metadata() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="确认创建"),
        description="Submit the final wizard step",
    )
    assert is_creation_step(step) is True


def test_opening_create_wizard_is_not_a_persistent_create() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="新增"),
        description="打开创建向导以继续探索表单",
    )
    assert is_creation_step(step) is False
