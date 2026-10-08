from types import SimpleNamespace

from gui_agent.domain.models import ActionType
from gui_agent.domain.results import Observation, PageSemanticSummary, Status, StepResult
from gui_agent.site_capabilities.gaealavic import GAEALaViCCapabilityPack
from gui_agent.site_capabilities.intranet_intent import compile_intranet_intent
from gui_agent.site_capabilities.parameterized_form import next_parameterized_form_action


def _scenario(goal: str, test_data: dict):
    return SimpleNamespace(
        name="runtime form",
        goal=goal,
        preconditions="",
        expected_results=[],
        clarification_history=[],
        test_data=test_data,
    )


def _observation(controls):
    return Observation(
        url="http://192.168.31.218:7991/#/scenarioEdit",
        semantic_summary=PageSemanticSummary(controls=controls),
    )


def _passed(step, observation):
    return StepResult(
        index=1,
        action=step.action.value,
        description=step.description,
        target_summary=step.description or "",
        status=Status.PASSED,
        started_at=observation.captured_at,
        ended_at=observation.captured_at,
        after=observation,
    )


def _next(observation, history, scenario):
    return next_parameterized_form_action(
        observation,
        history,
        scenario,
        compile_intranet_intent(scenario),
        stage="scenario_create_form",
        internal_name=GAEALaViCCapabilityPack.internal_ledger_name,
    )


def test_arbitrary_scenario_name_and_description_are_runtime_parameters() -> None:
    scenario = _scenario(
        "实际创建想定，想定名称为 蓝军临时想定-09，并保存想定",
        {"scenarioName": "蓝军临时想定-09", "description": "离线参数化验证"},
    )
    observation = _observation([
        {"runtimeId": "ai_20", "role": "textbox", "name": "想定名称", "valueState": "empty"},
        {"runtimeId": "ai_21", "role": "textbox", "name": "描述", "valueState": "empty"},
    ])

    action = _next(observation, [], scenario)

    assert action is not None
    assert action.action == ActionType.FILL
    assert action.value == "蓝军临时想定-09"
    assert action.locator.runtime_id == "ai_20"


def test_common_input_prompt_prefix_still_maps_to_runtime_parameter() -> None:
    scenario = _scenario(
        "实际创建想定，想定名称为 蓝军临时想定-09",
        {"scenarioName": "蓝军临时想定-09"},
    )
    observation = _observation([{
        "runtimeId": "ai_20",
        "role": "textbox",
        "name": "请输入想定名称",
        "valueState": "empty",
    }])

    action = _next(observation, [], scenario)

    assert action is not None
    assert action.value == "蓝军临时想定-09"


def test_secret_search_and_complex_controls_are_not_filled_locally() -> None:
    scenario = _scenario("实际创建想定，想定名称为 test_D", {
        "password": "must-not-leak", "scenarioName": "test_D", "model": "A型",
    })
    observation = _observation([
        {"runtimeId": "ai_1", "role": "textbox", "name": "密码", "valueState": "empty"},
        {"runtimeId": "ai_2", "role": "textbox", "name": "搜索想定名称", "valueState": "empty"},
        {"runtimeId": "ai_3", "role": "combobox", "name": "仿真模型", "valueState": "empty"},
    ])

    assert _next(observation, [], scenario) is None


def test_save_requires_a_successful_current_run_fill_and_no_empty_required_field() -> None:
    scenario = _scenario(
        "找到想定 test_D，修改描述并保存",
        {"description": "新的说明"},
    )
    empty = _observation([
        {"runtimeId": "ai_8", "role": "textbox", "name": "描述", "valueState": "empty", "required": True},
        {"runtimeId": "ai_9", "role": "button", "name": "保存", "disabled": False},
    ])
    fill = _next(empty, [], scenario)
    assert fill is not None and fill.action == ActionType.FILL

    still_empty = _next(empty, [_passed(fill, empty)], scenario)
    assert still_empty is None

    ready = _observation([
        {"runtimeId": "ai_8", "role": "textbox", "name": "描述", "valueState": "non_empty", "required": True},
        {"runtimeId": "ai_9", "role": "button", "name": "保存", "disabled": False},
    ])
    save = _next(ready, [_passed(fill, ready)], scenario)
    assert save is not None
    assert save.action == ActionType.CLICK
    assert save.action_category == "update"
    assert save.business_object_name.startswith("E2E_GAEALAVIC_")
    assert save.resource_name == "test_D"


def test_create_submission_requires_explicit_current_run_authorization() -> None:
    scenario = _scenario("查看创建想定页面，想定名称为 test_D", {"scenarioName": "test_D"})
    ready = _observation([
        {"runtimeId": "ai_9", "role": "button", "name": "确认创建", "disabled": False},
    ])
    fake_fill = SimpleNamespace(
        status=Status.PASSED,
        action="fill",
        description="按本次任务参数填写字段「想定名称」",
        target_summary="",
    )

    assert _next(ready, [fake_fill], scenario) is None


def test_submit_does_not_reuse_a_form_fill_from_another_page() -> None:
    scenario = _scenario(
        "实际创建想定，想定名称为 test_D",
        {"scenarioName": "test_D"},
    )
    other_page = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage",
        semantic_summary=PageSemanticSummary(),
    )
    old_fill = SimpleNamespace(
        status=Status.PASSED,
        action="fill",
        description="按本次任务参数填写字段「模型名称」",
        target_summary="",
        after=other_page,
    )
    ready = _observation([{
        "runtimeId": "ai_9", "role": "button", "name": "确认创建", "disabled": False,
    }])

    assert _next(ready, [old_fill], scenario) is None


def test_delete_intent_disables_all_parameterized_form_actions() -> None:
    scenario = _scenario("找到 test_D 并删除想定", {"description": "不应填写"})
    observation = _observation([
        {"runtimeId": "ai_8", "role": "textbox", "name": "描述", "valueState": "empty"},
    ])

    assert _next(observation, [], scenario) is None
