import json

import pytest
from pydantic import ValidationError

from gui_agent.domain.models import ActionType, Step
from gui_agent.domain.results import FailureCategory, Observation, PageSemanticSummary, Status, StepResult
from gui_agent.execution.side_effects import evaluate_side_effect
from gui_agent.planning.agent_planner import (
    AgentDecision,
    AgentScenario,
    _agent_prompt,
    _decision_state_contract_violation,
    _normalize_agent_payload,
)
from gui_agent.site_capabilities.gaealavic import (
    GAEALaViCCapabilityPack,
    SCENARIO_STAGE_ORDER,
)
from gui_agent.site_capabilities.naming import alpha_name, next_test_name, parse_test_name_index


def _scenario(goal: str):
    return type("Scenario", (), {"name": "E2E workflow", "goal": goal, "business_context": {}})()


def _history_step(
    index: int,
    action: str,
    description: str,
    after: Observation,
) -> StepResult:
    return StepResult(
        index=index,
        action=action,
        description=description,
        target_summary=description,
        status=Status.PASSED,
        started_at=after.captured_at,
        ended_at=after.captured_at,
        after=after,
    )


def test_gaealavic_pack_matches_private_target_and_exposes_business_contract() -> None:
    pack = GAEALaViCCapabilityPack()
    assert pack.matches("http://192.168.31.218:7991/#/mineModelList")
    assert not pack.matches("http://192.168.31.219:7991/#/mineModelList")

    observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        title="GAEALaViC",
        accessibility_summary="建模 想定 运行记录 强化学习",
        semantic_summary=PageSemanticSummary(
            route="/#/mineModelList",
            heading="建模",
            components=[{
                "kind": "cascader",
                "label": "装备类型",
                "visibleOptions": [{"text": "A(机载)"}, {"text": "S(水面舰艇)"}],
            }],
            wizard={"visible": True, "activeStep": "选择类型", "stepLabels": ["选择类型", "关键信息"]},
            canvas={},
        ),
    )
    context = pack.planner_context(observation, [], _scenario("完整端到端建模、想定、仿真和强化学习测试"))
    assert context["businessChain"] == ["model", "instance", "scenario", "run", "trainingConfig"]
    assert context["observedComponents"][0]["visibleOptions"][0]["text"] == "A(机载)"
    failure = context["knownFailurePatterns"][0]
    assert failure["id"] == "model-wizard-existing-simulation-select-v1"
    assert failure["advisoryOnly"] is True
    assert "visibleOptions" in failure["recovery"]
    assert "selectedText" in failure["verification"]
    assert "model_wizard_step_1" not in context["remainingStages"]
    assert "model_wizard_step_2" in context["remainingStages"]


def test_gaealavic_wizard_stage_uses_observed_dialog_not_fixed_route_page() -> None:
    pack = GAEALaViCCapabilityPack()
    observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(
            route="/#/mineModelList",
            dialogs=[{"name": "新增智能体", "text": "智能体名称 基本信息 参与方"}],
            wizard={"visible": True, "activeStep": "基本信息", "text": "智能体名称 参与方"},
        ),
    )
    assert pack.page_stage(observation) == "model_wizard_step_3"


def test_gaealavic_wizard_stage_uses_confirmation_dialog_when_wizard_fields_are_empty() -> None:
    observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(
            dialogs=[{
                "name": "新增智能体 选择类型 关键信息 基本信息 确认创建 请确认以下信息",
                "text": "请确认以下信息 智能体关键字: test_D 中文名称: test_D 取消 上一步 创建",
            }],
            controls=[
                {"role": "button", "name": "取消"},
                {"role": "button", "name": "上一步"},
                {"role": "button", "name": "创建"},
            ],
            wizard={"visible": True, "activeStep": "", "text": ""},
        ),
    )

    assert GAEALaViCCapabilityPack().page_stage(observation) == "model_wizard_step_4"


def test_ai_can_choose_a_dynamic_cascader_path() -> None:
    normalized = _normalize_agent_payload({
        "kind": "action",
        "action": {
            "action": "component",
            "component": {
                "kind": "cascader",
                "semanticTarget": "选择当前页面观察到的装备类型路径",
                "locators": [{"css": ".ant-cascader-picker"}],
                "values": ["A(机载)", "A(不可见光、热辐射设备)", "C(通信(发射和接收))"],
                "expectedText": "C(通信(发射和接收))",
            },
            "description": "根据当前页面候选项选择与测试目标匹配的装备类型路径",
            "effect_level": "session_only",
        },
        "reason": "当前向导要求 AI 选择级联路径",
        "progress_assessment": "progress",
    }, "http://192.168.31.218:7991")
    decision = AgentDecision.model_validate(normalized)
    assert decision.action is not None
    assert decision.action.action is ActionType.COMPONENT
    assert decision.action.component is not None
    assert decision.action.component.kind == "cascader"


def test_full_workflow_cannot_complete_while_lifecycle_stages_remain() -> None:
    pack = GAEALaViCCapabilityPack()
    observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        accessibility_summary="\u5efa\u6a21 \u60f3\u5b9a \u8fd0\u884c \u5f3a\u5316\u5b66\u4e60",
        semantic_summary=PageSemanticSummary(
            route="/#/mineModelList",
            wizard={"visible": True, "activeStep": "\u9009\u62e9\u7c7b\u578b", "text": "\u88c5\u5907\u667a\u80fd\u4f53"},
        ),
    )
    remaining = pack.remaining_stages(observation, [], _scenario("\u5b8c\u6574\u7aef\u5230\u7aef\u95ed\u73af\u6d4b\u8bd5"))
    assert "model_wizard_step_2" in remaining
    assert "model_editor_3d" in remaining
    assert "scenario_list" in remaining
    assert "scenario_created_verified" in remaining


def test_model_editor_requires_a_ready_non_empty_canvas() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("\u5efa\u6a21\u5e76\u8fdb\u5165\u4e09\u7ef4\u7f16\u8f91\u5668")
    blank = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1",
        semantic_summary=PageSemanticSummary(canvas={"count": 1, "nonEmptySurface": False}),
    )
    ready = Observation(
        url=blank.url,
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True, "surfaces": [{"width": 1200, "height": 800}]}
        ),
    )
    assert "model_editor_3d" in pack.remaining_stages(blank, [], scenario)
    assert "model_editor_3d" not in pack.remaining_stages(ready, [], scenario)
    assert {item.type.value for item in pack.terminal_assertions(ready, [], scenario)} == {"page_reached", "visible"}


def test_editor_contract_exposes_sections_and_excludes_action_parameter_details() -> None:
    pack = GAEALaViCCapabilityPack()
    observation = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1",
        accessibility_summary="\u5b50\u7ea7\u5b9e\u4f53 \u4efb\u52a1\u8def\u5f84 \u52a8\u4f5c\u6307\u4ee4 \u8bc4\u4f30\u6307\u6807",
        semantic_summary=PageSemanticSummary(canvas={"count": 1, "nonEmptySurface": True}),
    )
    contract = pack.planner_context(observation, [], _scenario("\u5b8c\u6574\u5efa\u6a21\u6d4b\u8bd5"))["modelEditorContract"]
    assert "mission_path" in contract["observedSections"]
    assert "actions_commands.parameter_configuration" in contract["excludedDetailTests"]
    assert "_path" in contract["missionPathRule"]
    assert "Scenario" in contract["missionPathRule"]
    assert "do not click Cancel" in contract["dynamicsRule"]
    assert "same mission-path panel" in contract["missionPathRule"]
    assert "Save first" in contract["saveGate"]
    assert len(contract["sections"]) == 16


def test_complete_3d_modeling_requires_dynamics_then_mission_path_saves() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("对现有 test_H 执行完整3D建模，完成动力学和任务路径并保存")
    editor = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1",
        accessibility_summary="动力学 任务路径 保存",
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True}
        ),
    )

    context = pack.planner_context(editor, [], scenario)["modelEditorContract"]

    assert context["requiredSections"] == ["dynamics", "mission_path"]
    assert context["completedSections"] == []
    assert context["remainingSections"] == ["dynamics", "mission_path"]
    assert context["nextRequiredSection"] == "dynamics"
    assert "model_dynamics_configured" in pack.remaining_stages(editor, [], scenario)
    assert "model_mission_path_configured" in pack.remaining_stages(editor, [], scenario)


def test_editor_panel_open_and_fill_do_not_complete_section_before_save() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("对现有 test_H 执行完整3D建模")
    editor = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1",
        accessibility_summary="任务路径设置 任务路径点关键字 保存",
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True}
        ),
    )
    history = [
        _history_step(1, "visual_click", "点击任务路径右侧加号", editor),
        _history_step(2, "fill", "填写任务路径点关键字 test_H_path", editor),
    ]

    contract = pack.planner_context(editor, history, scenario)["modelEditorContract"]

    assert contract["completedSections"] == []
    assert contract["nextRequiredSection"] == "dynamics"


def test_each_editor_section_requires_its_own_successful_save() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("对现有 test_H 执行完整3D建模")
    editor = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1",
        accessibility_summary="动力学 任务路径",
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True}
        ),
    )
    history = [
        _history_step(1, "visual_click", "点击动力学右侧加号并打开动力学设置", editor),
        _history_step(2, "click", "点击动力学设置面板的保存按钮", editor),
        _history_step(3, "click", "重新打开动力学设置并验证保存值仍然存在", editor),
        _history_step(4, "visual_click", "点击任务路径右侧加号并打开任务路径设置", editor),
        _history_step(5, "fill", "填写任务路径点关键字 test_H_path", editor),
        _history_step(6, "click", "点击任务路径设置面板的保存按钮", editor),
        _history_step(7, "click", "重新打开任务路径并验证 test_H_path 已持久化", editor),
    ]

    context = pack.planner_context(editor, history, scenario)

    assert context["modelEditorContract"]["completedSections"] == [
        "dynamics",
        "mission_path",
    ]
    assert context["modelEditorContract"]["remainingSections"] == []
    assert "model_dynamics_configured" not in context["remainingStages"]
    assert "model_mission_path_configured" not in context["remainingStages"]


def test_pending_write_without_readback_does_not_complete_editor_section() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("对现有 test_H 完成动力学设置并保存")
    editor = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1",
        accessibility_summary="动力学 保存",
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True}
        ),
    )
    opened = _history_step(1, "visual_click", "点击动力学右侧加号", editor)
    pending = _history_step(2, "click", "点击动力学设置面板的保存按钮", editor)
    pending.status = Status.INCOMPLETE
    pending.progress_assessment = "pending_business_verification"

    context = pack.planner_context(editor, [opened, pending], scenario)["modelEditorContract"]

    assert context["completedSections"] == []
    assert context["remainingSections"] == ["dynamics"]


def test_mission_path_save_then_no_response_cancel_is_narrow_completion_fallback() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("进入现有 test_A 的3D建模编辑器完成任务路径设置并保存")
    editor = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1",
        accessibility_summary="任务路径设置 通用参数 路径点设置 保存 取消",
        semantic_summary=PageSemanticSummary(canvas={"count": 1, "nonEmptySurface": True}),
    )
    opened = _history_step(1, "visual_click", "点击任务路径右侧加号并打开任务路径设置", editor)
    filled = _history_step(2, "fill", "将任务路径点关键字填写为 test_A_path", editor)
    points = _history_step(3, "visual_click", "点击任务路径设置面板顶部的路径点设置标签", editor)
    save = _history_step(4, "visual_click", "点击任务路径设置面板顶部的保存按钮", editor).model_copy(
        update={"status": Status.ERROR, "progress_assessment": "no_progress"}
    )
    cancel = _history_step(5, "visual_click", "点击同一任务路径设置面板顶部的取消按钮", editor)

    context = pack.planner_context(editor, [opened, filled, points, save, cancel], scenario)

    assert context["modelEditorContract"]["completedSections"] == ["mission_path"]
    assert "model_mission_path_configured" not in context["remainingStages"]


def test_mission_path_cancel_without_save_or_evidence_does_not_complete() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("进入现有 test_A 的3D建模编辑器完成任务路径设置并保存")
    editor = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1",
        accessibility_summary="任务路径设置 路径点设置 保存 取消",
        semantic_summary=PageSemanticSummary(canvas={"count": 1, "nonEmptySurface": True}),
    )
    history = [
        _history_step(1, "visual_click", "打开任务路径设置面板", editor),
        _history_step(2, "visual_click", "点击任务路径设置面板顶部的取消按钮", editor),
    ]

    context = pack.planner_context(editor, history, scenario)

    assert context["modelEditorContract"]["completedSections"] == []
    assert "model_mission_path_configured" in context["remainingStages"]


def test_failed_mission_path_save_cannot_be_rescued_by_cancel() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("进入现有 test_A 的3D建模编辑器完成任务路径设置并保存")
    editor = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1",
        accessibility_summary="任务路径设置 路径点设置 保存 取消",
        semantic_summary=PageSemanticSummary(canvas={"count": 1, "nonEmptySurface": True}),
    )
    failed_save = _history_step(4, "visual_click", "点击任务路径设置面板顶部的保存按钮", editor).model_copy(
        update={
            "status": Status.ERROR,
            "failure_category": FailureCategory.BUSINESS_STATE,
            "after": editor.model_copy(update={
                "failed_requests": ["HTTP 500 POST http://192.168.31.218:7980/api/v1/lavic-core/savePath"]
            }),
        }
    )
    history = [
        _history_step(1, "visual_click", "打开任务路径设置面板", editor),
        _history_step(2, "fill", "将任务路径点关键字填写为 test_A_path", editor),
        _history_step(3, "visual_click", "点击任务路径设置面板顶部的路径点设置标签", editor),
        failed_save,
        _history_step(5, "visual_click", "点击同一任务路径设置面板顶部的取消按钮", editor),
    ]

    context = pack.planner_context(editor, history, scenario)

    assert context["modelEditorContract"]["completedSections"] == []
    assert "model_mission_path_configured" in context["remainingStages"]


def test_dynamics_save_then_cancel_is_the_site_completion_sequence() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("进入现有 test_A 的3D建模编辑器完成动力学设置并保存")
    editor = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=test_A",
        accessibility_summary="动力学 动力学配置 保存 取消",
        semantic_summary=PageSemanticSummary(canvas={"count": 1, "nonEmptySurface": True}),
    )
    history = [
        _history_step(1, "click", "点击动力学右侧蓝色加号", editor),
        _history_step(2, "click", "点击动力学配置面板右上角保存", editor),
        _history_step(3, "wait_for", "等待动力学保存处理完成", editor),
        _history_step(4, "click", "点击动力学配置面板右上角取消关闭面板", editor),
    ]
    assert "dynamics" in pack._completed_editor_section_ids(history)


def test_test_name_sequence_uses_excel_column_order_and_preserves_gaps() -> None:
    assert alpha_name(1) == "A"
    assert alpha_name(26) == "Z"
    assert alpha_name(27) == "AA"
    assert alpha_name(52) == "AZ"
    assert alpha_name(53) == "BA"
    assert parse_test_name_index("test_AA") == 27
    assert parse_test_name_index("E2E_test_AA") is None
    assert next_test_name([f"test_{alpha_name(index)}" for index in range(1, 28)]) == "test_AB"
    assert next_test_name(["test_A", "test_C"]) == "test_B"


def test_resource_name_observation_drives_the_next_exact_test_name() -> None:
    pack = GAEALaViCCapabilityPack()
    cases = [
        ([], "test_A"),
        (["test_A"], "test_B"),
        ([f"test_{alpha_name(index)}" for index in range(1, 27)], "test_AA"),
        ([f"test_{alpha_name(index)}" for index in range(1, 53)], "test_BA"),
    ]
    for existing, expected in cases:
        observation = Observation(
            url="http://192.168.31.218:7991/#/mineModelList",
            semantic_summary=PageSemanticSummary(resource_names=existing),
        )
        contract = pack.planner_context(
            observation,
            [],
            _scenario("按 test_A、test_B 到 test_Z，再到 test_AA 的顺序选择第一个未占用名称"),
        )["resourceNameContract"]
        assert contract["mode"] == "test_sequence"
        assert contract["observedTestSequenceNames"] == existing
        assert contract["nextTestSequenceName"] == expected
        assert contract["visibleName"] == expected


def test_search_input_is_not_mistaken_for_an_occupied_resource_name() -> None:
    observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        dom_summary=["input | placeholder=搜索我的模型... | value=test_C"],
        accessibility_summary='textbox "搜索我的模型..." value="test_C"',
        semantic_summary=PageSemanticSummary(resource_names=["test_A", "test_B"]),
    )

    contract = GAEALaViCCapabilityPack().planner_context(
        observation,
        [],
        _scenario("按 test_A 到 test_Z、再到 test_AA 的顺序选择第一个未占用名称"),
    )["resourceNameContract"]

    assert contract["observedTestSequenceNames"] == ["test_A", "test_B"]
    assert contract["visibleName"] == "test_C"


def test_resource_names_accumulate_across_the_run_history() -> None:
    first_page = Observation(
        url="http://192.168.31.218:7991/#/mineModelList?page=1",
        semantic_summary=PageSemanticSummary(resource_names=["test_A", "test_B"]),
    )
    second_page = Observation(
        url="http://192.168.31.218:7991/#/mineModelList?page=2",
        semantic_summary=PageSemanticSummary(resource_names=["test_C", "test_D"]),
    )

    contract = GAEALaViCCapabilityPack().planner_context(
        second_page,
        [_history_step(1, "click", "scan page 1", first_page)],
        _scenario("按 test_A 到 test_Z、再到 test_AA 的顺序选择第一个未占用名称"),
    )["resourceNameContract"]

    assert contract["observedTestSequenceNames"] == ["test_A", "test_B", "test_C", "test_D"]
    assert contract["visibleName"] == "test_E"


def test_empty_exact_search_locks_name_and_requires_create_wizard() -> None:
    observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(resource_names=["test_A", "test_B"]),
    )
    history = [
        _history_step(
            1,
            "fill",
            "在创建前对候选名称 test_C 执行精确搜索，确认未占用",
            observation,
        )
    ]

    contract = GAEALaViCCapabilityPack().planner_context(
        observation,
        history,
        _scenario("按 test_A 到 test_Z、再到 test_AA 的顺序选择第一个未占用名称"),
    )["resourceNameContract"]

    assert contract["visibleName"] == "test_C"
    assert contract["allocationState"] == {
        "lockedName": "test_C",
        "observedOccupiedNames": ["test_A", "test_B"],
        "conflictCheckStatus": "available",
        "exactSearchAttempts": 1,
        "searchAllowed": False,
        "nextRequiredBusinessAction": "open_create_wizard",
    }


def test_sequence_name_stays_locked_when_confirmation_summary_mentions_pending_name() -> None:
    list_observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(resource_names=["test_A", "test_B", "test_C"]),
    )
    history = [
        _history_step(
            1,
            "fill",
            "Exact search for available name test_D in the model searchbox",
            list_observation,
        )
    ]
    confirmation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(
            resource_names=["test_A", "test_B", "test_C", "test_D"],
            wizard={"visible": True, "activeStep": "确认创建", "text": "test_D"},
        ),
    )

    contract = GAEALaViCCapabilityPack().planner_context(
        confirmation,
        history,
        _scenario("按 test_A、test_B 顺序选择第一个未占用名称并创建"),
    )["resourceNameContract"]

    assert contract["visibleName"] == "test_D"
    assert contract["nextTestSequenceName"] == "test_D"
    assert contract["allocationState"]["lockedName"] == "test_D"
    assert contract["allocationState"]["conflictCheckStatus"] == "available"
    assert contract["allocationState"]["observedOccupiedNames"] == ["test_A", "test_B", "test_C"]


def test_next_step_that_mentions_future_creation_is_not_a_completed_create() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("按 test_A、test_B 顺序选择第一个未占用名称并创建")
    searched = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(resource_names=["test_A", "test_B", "test_C"]),
    )
    confirmation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(
            dialogs=[{"text": "请确认以下信息 test_D 取消 上一步 创建"}],
            controls=[{"role": "button", "name": "创建"}],
            resource_names=["test_D"],
            wizard={"visible": True, "activeStep": "", "text": ""},
        ),
    )
    history = [
        _history_step(1, "fill", "Exact search for available name test_D in the model searchbox", searched),
        _history_step(2, "click", "click @ role=button[name=下一步] value=test_D", confirmation).model_copy(
            update={"planner_reason": "进入确认创建前的下一步，不提交或创建持久化模型"}
        ),
    ]

    contract = pack.planner_context(confirmation, history, scenario)["resourceNameContract"]

    assert pack.page_stage(confirmation) == "model_wizard_step_4"
    assert contract["visibleName"] == "test_D"
    assert contract["allocationState"]["conflictCheckStatus"] == "available"
    assert contract["allocationState"]["nextRequiredBusinessAction"] == "submit_locked_resource"


def test_exact_user_name_is_used_without_forcing_the_test_sequence() -> None:
    pack = GAEALaViCCapabilityPack()
    contract = pack.planner_context(
        Observation(url="http://192.168.31.218:7991/#/mineModelList"),
        [],
        _scenario("创建装备智能体，智能体名称为“低空巡检模型_甲”"),
    )["resourceNameContract"]

    assert contract["mode"] == "exact_user_name"
    assert contract["visibleName"] == "低空巡检模型_甲"
    assert contract["keyword"].startswith("agent_")
    assert contract["keyword"].isascii()
    assert contract["keyword"].replace("_", "").isalpha()
    assert contract["nextTestSequenceName"] is None
    assert contract["internalLedgerName"] == pack.internal_ledger_name("低空巡检模型_甲")


def test_current_goal_target_overrides_stale_model_name_test_data() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = AgentScenario(
        name="3D model edit",
        goal=(
            "只查找一次已经存在的 test_A，点击 test_A 对应的修改进入 3D 编辑器，"
            "完成 test_A_path 并保存；禁止创建新模型。"
        ),
        test_data={"modelName": "test_I", "pathName": "test_I_path"},
    )

    contract = pack.planner_context(
        Observation(url="http://192.168.31.218:7991/#/mineModelList"),
        [],
        scenario,
    )["resourceNameContract"]

    assert contract["mode"] == "exact_user_name"
    assert contract["visibleName"] == "test_A"
    assert contract["allocationState"]["lockedName"] == "test_A"


def test_existing_target_prompt_never_allocates_the_next_test_name() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = AgentScenario(
        name="3D model edit",
        goal=(
            "在建模列表中只查找一次已经存在的 test_A，点击 test_A 对应的修改进入3D编辑器。"
            "禁止创建新模型。将任务路径关键字填写为 test_A_path。"
            "依次检查通用参数、路径点设置、路径生成和自然语言描述。"
        ),
        test_data={"modelName": "test_I", "pathName": "test_I_path"},
    )
    model_list = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(
            resource_names=[f"test_{letter}" for letter in "ABCDEFGH"]
        ),
    )

    contract = pack.planner_context(model_list, [], scenario)["resourceNameContract"]

    assert pack._requests_test_sequence(scenario.goal) is False
    assert contract["mode"] == "exact_user_name"
    assert contract["operation"] == "open_existing_resource"
    assert contract["visibleName"] == "test_A"
    assert contract["allocationState"] == {
        "lockedName": "test_A",
        "observedOccupiedNames": [f"test_{letter}" for letter in "ABCDEFGH"],
        "conflictCheckStatus": "not_started",
        "exactSearchAttempts": 0,
        "searchAllowed": True,
        "nextRequiredBusinessAction": "exact_search_existing_name_once",
    }


def test_existing_target_stays_locked_after_search_and_open() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = AgentScenario(
        name="3D model edit",
        goal="只查找一次已经存在的 test_A，点击修改进入3D编辑器。",
        test_data={"modelName": "test_I"},
    )
    search_result = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(resource_names=["test_A"]),
    )
    searched = _history_step(1, "fill", "在搜索框精确查找 test_A", search_result)
    after_search = pack.planner_context(
        search_result, [searched], scenario
    )["resourceNameContract"]

    assert after_search["visibleName"] == "test_A"
    assert after_search["allocationState"]["conflictCheckStatus"] == "target_visible"
    assert after_search["allocationState"]["nextRequiredBusinessAction"] == "open_existing_resource"

    editor = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1",
        semantic_summary=PageSemanticSummary(),
    )
    opened = _history_step(2, "click", "点击 test_A 对应的修改进入3D编辑器", editor)
    after_open = pack.planner_context(
        editor, [searched, opened], scenario
    )["resourceNameContract"]

    assert after_open["visibleName"] == "test_A"
    assert after_open["allocationState"]["conflictCheckStatus"] == "target_open"
    assert after_open["allocationState"]["searchAllowed"] is False


def test_test_data_name_is_used_when_current_goal_has_no_explicit_target() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = AgentScenario(
        name="3D model edit",
        goal="打开指定的现有模型并检查任务路径。",
        test_data={"modelName": "test_I"},
    )

    contract = pack.planner_context(
        Observation(url="http://192.168.31.218:7991/#/mineModelList"),
        [],
        scenario,
    )["resourceNameContract"]

    assert contract["visibleName"] == "test_I"


def test_missing_user_name_does_not_silently_force_test_a() -> None:
    contract = GAEALaViCCapabilityPack().planner_context(
        Observation(url="http://192.168.31.218:7991/#/mineModelList"),
        [],
        _scenario("创建一个装备智能体"),
    )["resourceNameContract"]

    assert contract["mode"] == "user_defined"
    assert contract["visibleName"] is None
    assert contract["internalLedgerName"] is None
    assert contract["keyword"] is None


def test_explicit_user_keyword_is_separate_from_the_visible_name() -> None:
    scenario = AgentScenario(
        name="modeling",
        goal="创建装备智能体",
        test_data={"resourceName": "用户模型 08", "agentKeyword": "custom_agent"},
    )
    contract = GAEALaViCCapabilityPack().planner_context(
        Observation(url="http://192.168.31.218:7991/#/mineModelList"), [], scenario,
    )["resourceNameContract"]

    assert contract["visibleName"] == "用户模型 08"
    assert contract["keyword"] == "custom_agent"


def test_visible_test_name_is_translated_only_at_gaealavic_policy_boundary() -> None:
    pack = GAEALaViCCapabilityPack()
    action = {
        "action": "click",
        "locator": {"role": "button", "name": "\u521b\u5efa"},
        "description": "create model",
        "effect_level": "reversible_write",
        "action_category": "create",
        "object_type": "model",
        "resource_name": "test_AA",
    }
    pack.normalize_action_payload(action)
    assert action["resource_name"] == "test_AA"
    assert action["business_object_name"] == pack.internal_ledger_name("test_AA")

    action["cleanup_required"] = True
    step = Step.model_validate(action)
    evidence = evaluate_side_effect(
        step,
        pack.default_side_effect_policies(),
        environment_id=None,
        role="tester",
    )
    assert evidence is not None
    assert evidence["resourceName"] == "test_AA"
    assert evidence["objectName"] == pack.internal_ledger_name("test_AA")


def test_internal_ledger_alias_is_never_typed_into_the_gaealavic_page() -> None:
    pack = GAEALaViCCapabilityPack()
    alias = pack.internal_ledger_name("用户指定模型_7")
    normalized = _normalize_agent_payload({
        "kind": "action",
        "action": {
            "action": "fill",
            "locator": {"role": "textbox", "name": "搜索我的模型..."},
            "value": alias,
            "description": "exact model conflict check",
            "effect_level": "session_only",
            "resource_name": "用户指定模型_7",
            "business_object_name": alias,
        },
        "reason": "provider copied the internal ledger alias into a page value",
        "progress_assessment": "progress",
    }, "http://192.168.31.218:7991")

    decision = AgentDecision.model_validate(normalized)
    assert decision.action is not None
    assert decision.action.value == "用户指定模型_7"
    assert decision.action.resource_name == "用户指定模型_7"
    assert decision.action.business_object_name == alias


def test_legacy_project_context_is_reconciled_before_gaealavic_planning() -> None:
    effective = GAEALaViCCapabilityPack().effective_business_context({
        "description": "Use only isolated E2E_ resources.",
        "exampleGoals": ["Create an E2E_ lifecycle"],
        "operatingBoundaries": [
            "Only create objects whose names begin with E2E_",
            "Never delete unknown resources or resources not owned by the E2E ledger",
        ],
        "facts": [{
            "id": "gaealavic.e2e_boundary",
            "statement": "Writable test resources must use the E2E_ prefix.",
            "source": "legacy",
        }],
    })
    serialized = json.dumps(effective, ensure_ascii=False)

    assert "Only create objects whose names begin with E2E_" not in serialized
    assert "Use only isolated E2E_ resources" not in serialized
    assert effective["resourceNaming"]["mode"] == "user_requirement"
    assert effective["resourceNaming"]["internalLedgerPattern"] == r"^E2E_GAEALAVIC_[A-F0-9]{24}$"
    assert "create_e2e_resource" not in serialized
    assert "must never be typed into or searched" in serialized
    assert "follow the current user's explicit requirement" in serialized
    assert "test_A, test_B ... sequence is used only" in serialized

    assert GAEALaViCCapabilityPack().effective_business_context(effective) == effective


def test_agent_prompt_uses_reconciled_gaealavic_context() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = AgentScenario(
        name="modeling",
        goal="按 test_A、test_B 到 test_Z，再到 test_AA 的顺序创建第一个未占用模型",
        business_context={
            "description": "Use only isolated E2E_ resources.",
            "operatingBoundaries": ["Only create objects whose names begin with E2E_"],
        },
    )
    prompt = _agent_prompt(
        scenario=scenario,
        base_url="http://192.168.31.218:7991",
        observation=Observation(
            url="http://192.168.31.218:7991/#/mineModelList",
            semantic_summary=PageSemanticSummary(resource_names=["test_A", "test_B"]),
        ),
        history=[],
        call_index=1,
        schema={"type": "object"},
        visual_enabled=False,
        site_pack=pack,
    )

    assert "Only create objects whose names begin with E2E_" in prompt
    assert '"advisoryOnly": true' in prompt
    assert '"visibleName": "test_C"' in prompt
    assert f'"internalLedgerName": "{pack.internal_ledger_name("test_C")}"' in prompt
    assert "Never put resourceNameContract.internalLedgerName" in prompt
    assert "A clarification asking the user to rename" in prompt


def test_sidebar_labels_do_not_override_the_current_route() -> None:
    pack = GAEALaViCCapabilityPack()
    observation = Observation(
        url="http://192.168.31.218:7991/#/mineScenarioList",
        accessibility_summary="\u5efa\u6a21 \u60f3\u5b9a \u8fd0\u884c \u5f3a\u5316\u5b66\u4e60",
        semantic_summary=PageSemanticSummary(heading="\u60f3\u5b9a"),
    )
    assert pack.page_stage(observation) == "scenario_list"


def test_complete_scenario_goal_does_not_require_model_creation_workflow() -> None:
    pack = GAEALaViCCapabilityPack()

    required = pack.required_stage_ids(
        _scenario("完整想定流程：选择已有模型和实例，配置三维路径并创建想定")
    )

    assert required == ["authenticated", *SCENARIO_STAGE_ORDER]
    assert "model_wizard_step_1" not in required
    assert "model_created_verified" not in required


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        (
            Observation(
                url="http://192.168.31.218:7991/#/mineScenarioList",
                semantic_summary=PageSemanticSummary(heading="想定"),
            ),
            "scenario_list",
        ),
        (
            Observation(
                url="http://192.168.31.218:7991/#/scenarioCreate",
                semantic_summary=PageSemanticSummary(
                    wizard={"visible": True, "text": "想定名称 基本信息"}
                ),
            ),
            "scenario_create_form",
        ),
        (
            Observation(
                url="http://192.168.31.218:7991/#/scenarioCreate",
                semantic_summary=PageSemanticSummary(
                    components=[{"kind": "searchable_select", "label": "选择模型"}],
                    wizard={"visible": True, "text": "选择模型 仿真模型"},
                ),
            ),
            "scenario_model_selection",
        ),
        (
            Observation(
                url="http://192.168.31.218:7991/#/scenarioEdit",
                semantic_summary=PageSemanticSummary(
                    canvas={"count": 1, "nonEmptySurface": True}
                ),
            ),
            "scenario_3d_editor",
        ),
        (
            Observation(
                url="http://192.168.31.218:7991/#/scenarioEdit",
                semantic_summary=PageSemanticSummary(
                    canvas={"count": 1, "nonEmptySurface": True},
                    controls=[{"role": "button", "name": "添加航点"}],
                ),
            ),
            "scenario_path_configuration",
        ),
    ],
)
def test_scenario_stage_is_derived_from_current_observation(
    observation: Observation, expected: str
) -> None:
    assert GAEALaViCCapabilityPack().page_stage(observation) == expected


def test_existing_situation_run_route_is_classified_as_run() -> None:
    pack = GAEALaViCCapabilityPack()
    running = Observation(
        url=(
            "http://192.168.31.218:7991/#/situationPage?name=test_D&"
            "type=run&simulationStatus=Running"
        ),
        accessibility_summary="运行模式 暂停 停止 x50",
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True},
            controls=[
                {"role": "button", "name": "暂停"},
                {"role": "button", "name": "停止"},
            ],
        ),
    )
    assert pack.page_stage(running) == "run"


def test_existing_scenario_contract_forbids_model_jump_and_creation_stages() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario(
        "只修改已有 test_D 想定，配置实例和 test_D_path，进入运行模式，启动仿真后停止"
    )
    required = pack.required_stage_ids(scenario)
    assert "scenario_create_form" not in required
    assert "scenario_confirmation" not in required
    assert "scenario_path_saved" in required
    assert "run" in required
    contract = pack.planner_context(
        Observation(
            url="http://192.168.31.218:7991/#/situationPage?name=test_D&type=edit&simulationStatus=Unstart",
            semantic_summary=PageSemanticSummary(canvas={"count": 1, "nonEmptySurface": True}),
        ),
        [],
        scenario,
    )["scenarioContract"]
    assert contract["operation"] == "update_existing_scenario"
    assert "跳转到仿真模型" in contract["forbiddenActions"]
    assert "agentEditPage" in " ".join(contract["forbiddenActions"])


def test_existing_scenario_video_run_requires_ordered_terminal_evidence() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario(
        "只修改已有 test_D 想定，配置 test_D_path，进入运行模式，启动仿真，切换 x50，确认实体移动后停止仿真"
    )
    edit = Observation(
        url="http://192.168.31.218:7991/#/situationPage?name=test_D&type=edit&simulationStatus=Unstart",
        accessibility_summary="编辑模式 实例配置 任务路径 启动",
        semantic_summary=PageSemanticSummary(canvas={"count": 1, "nonEmptySurface": True}),
    )
    running_a = Observation(
        url="http://192.168.31.218:7991/#/situationPage?name=test_D&type=run&simulationStatus=Running",
        accessibility_summary="运行模式 暂停 停止 x1 实体位置 1",
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True},
            signature="running-a",
        ),
    )
    running_b = running_a.model_copy(
        update={
            "accessibility_summary": "运行模式 暂停 停止 x50 实体位置 2",
            "semantic_summary": PageSemanticSummary(
                canvas={"count": 1, "nonEmptySurface": True},
                signature="running-b",
            ),
        }
    )
    stopped = Observation(
        url="http://192.168.31.218:7991/#/situationPage?name=test_D&type=run&simulationStatus=Unstart",
        accessibility_summary="运行模式 启动",
        semantic_summary=PageSemanticSummary(canvas={"count": 1, "nonEmptySurface": True}),
    )
    history = [
        _history_step(1, "click", "切换到运行模式", edit),
        _history_step(2, "click", "点击启动仿真", running_a),
        _history_step(3, "wait_for", "确认仿真正在运行", running_a),
        _history_step(4, "select", "将速度切换为 x50", running_b),
        _history_step(5, "wait_for", "确认实体位置发生变化", running_b),
        _history_step(6, "click", "点击停止仿真", running_b),
        _history_step(7, "wait_for", "确认停止完成并恢复启动按钮", stopped),
    ]
    evidence = pack.planner_context(stopped, history, scenario)["runContract"]["evidence"]
    assert all(evidence.values())
    assert "run" not in pack.remaining_stages(stopped, history, scenario)
    assert pack.terminal_assertions(stopped, history, scenario)


def test_mvp_run_completes_when_run_mode_start_control_is_visible() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("只修改已有 test_D 想定，进入运行模式并看到启动按钮")
    run_ready = Observation(
        url=(
            "http://192.168.31.218:7991/#/situationPage?name=test_D&"
            "type=run&simulationStatus=Unstart"
        ),
        accessibility_summary="运行模式 启动",
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True},
            controls=[{"role": "button", "name": "启动"}],
        ),
    )

    contract = pack.planner_context(run_ready, [], scenario)["runContract"]

    assert contract["requiredEvidenceOrder"] == ["runModeReady"]
    assert contract["evidence"]["runModeReady"] is True
    assert contract["nextRequiredEvidence"] == "complete"
    assert "run" not in pack.remaining_stages(run_ready, [], scenario)
    assertions = pack.terminal_assertions(run_ready, [], scenario)
    assert {item.type.value for item in assertions} == {"url_contains", "text_contains"}


def test_mvp_run_does_not_complete_from_edit_mode_start_control() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("只修改已有 test_D 想定，进入运行模式并看到启动按钮")
    edit = Observation(
        url=(
            "http://192.168.31.218:7991/#/situationPage?name=test_D&"
            "type=edit&simulationStatus=Unstart"
        ),
        accessibility_summary="编辑模式 启动",
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True},
            controls=[{"role": "button", "name": "启动"}],
        ),
    )

    evidence = pack._run_workflow_evidence(edit, [])
    assert evidence["runModeReady"] is False


def test_explicit_real_run_goal_requires_ordered_runtime_evidence() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario(
        "只修改已有 test_D 想定，进入运行模式，启动仿真，切换 x50，确认实体移动后停止仿真"
    )
    assert pack._run_required_evidence(scenario) == [
        "runModeReady",
        "startClicked",
        "runningObserved",
        "x50Selected",
        "movementObserved",
        "stopClicked",
        "stoppedObserved",
    ]
    run_ready = Observation(
        url=(
            "http://192.168.31.218:7991/#/situationPage?name=test_D&"
            "type=run&simulationStatus=Unstart"
        ),
        accessibility_summary="运行模式 启动",
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True},
            controls=[{"role": "button", "name": "启动"}],
        ),
    )

    assert "run" in pack.remaining_stages(run_ready, [], scenario)
    assert pack.planner_context(run_ready, [], scenario)["runContract"]["nextRequiredEvidence"] == "startClicked"


def test_scenario_creation_requires_final_write_and_list_presence() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = AgentScenario(
        name="scenario workflow",
        goal="实际创建想定并返回列表验证",
        test_data={"scenarioName": "scenario_test_A"},
    )
    editor = Observation(
        url="http://192.168.31.218:7991/#/scenarioEdit",
        semantic_summary=PageSemanticSummary(
            canvas={"count": 1, "nonEmptySurface": True}
        ),
    )
    create_step = _history_step(
        8, "click", "点击确认创建想定 scenario_test_A", editor
    ).model_copy(
        update={
            "status": Status.INCOMPLETE,
            "progress_assessment": "pending_business_verification",
        }
    )
    absent = Observation(
        url="http://192.168.31.218:7991/#/mineScenarioList",
        semantic_summary=PageSemanticSummary(resource_names=[]),
    )
    present = Observation(
        url="http://192.168.31.218:7991/#/mineScenarioList",
        semantic_summary=PageSemanticSummary(resource_names=["scenario_test_A"]),
    )

    assert "scenario_created_verified" in pack.remaining_stages(
        absent, [create_step], scenario
    )
    assert "scenario_created_verified" not in pack.remaining_stages(
        present, [create_step], scenario
    )
    assertions = pack.terminal_assertions(present, [create_step], scenario)
    assert len(assertions) == 1
    assert assertions[0].expected == "scenario_test_A"


def test_arbitrary_chinese_scenario_name_is_verified_from_structured_list_names() -> None:
    pack = GAEALaViCCapabilityPack()
    resource_name = "九月红蓝对抗想定-临时09"
    scenario = AgentScenario(
        name="arbitrary scenario workflow",
        goal=f"实际创建想定，想定名称为 {resource_name}，并返回列表验证",
        test_data={"scenarioName": resource_name},
    )
    editor = Observation(url="http://192.168.31.218:7991/#/scenarioEdit")
    create_step = _history_step(
        8, "click", f"点击确认创建想定 {resource_name}", editor
    ).model_copy(
        update={
            "status": Status.INCOMPLETE,
            "progress_assessment": "pending_business_verification",
        }
    )
    present = Observation(
        url="http://192.168.31.218:7991/#/mineScenarioList",
        semantic_summary=PageSemanticSummary(resource_names=[resource_name]),
    )

    assert "scenario_created_verified" not in pack.remaining_stages(
        present, [create_step], scenario
    )
    assertions = pack.terminal_assertions(present, [create_step], scenario)
    assert len(assertions) == 1
    assert assertions[0].expected == resource_name


def test_complete_modeling_goal_does_not_require_unrequested_modules() -> None:
    pack = GAEALaViCCapabilityPack()
    required = pack.required_stage_ids(_scenario("\u5b8c\u6574\u5efa\u6a21\u521b\u5efa\u6d4b\u8bd5"))
    assert "model_created_verified" in required
    assert "model_editor_3d" not in required
    assert "scenario" not in required
    assert "run" not in required


def test_existing_model_3d_edit_goal_excludes_denied_lifecycle_modules() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario(
        "\u4f7f\u7528\u5f53\u524d\u5df2\u767b\u5f55\u72b6\u6001\uff0c\u5bf9\u5df2\u521b\u5efa\u7684 test_H \u7ee7\u7eed\u6267\u884c3D\u5efa\u6a21\u6d4b\u8bd5\u3002\n"
        "\u7981\u6b62\u521b\u5efa\u65b0\u7684\u667a\u80fd\u4f53\uff0c\u7981\u6b62\u542f\u52a8\u4eff\u771f\u6216\u5f3a\u5316\u5b66\u4e60\u3002\n"
        "\u8fdb\u5165\u5efa\u6a21\u5217\u8868\uff0c\u6253\u5f00 test_H \u76843D\u7f16\u8f91\u9875\uff0c\u9a8c\u8bc13D\u573a\u666f\u5e76\u4fdd\u5b58\u4efb\u52a1\u8def\u5f84\u3002"
    )

    required = pack.required_stage_ids(scenario)

    assert required == [
        "authenticated",
        "model_list",
        "model_editor_3d",
        "model_dynamics_configured",
        "model_mission_path_configured",
    ]
    assert "model_created_verified" not in required
    assert "scenario_list" not in required
    assert "run" not in required
    assert "reinforcement_learning" not in required


def test_parameterized_scenario_fast_path_searches_then_opens_exact_card() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("找到 蓝军临时想定-09，进入想定")
    list_page = Observation(
        url="http://192.168.31.218:7991/#/scenarioTable/",
        accessibility_summary="想定列表 蓝军临时想定-09 修改 删除",
        semantic_summary=PageSemanticSummary(
            controls=[{
                "runtimeId": "ai_12",
                "role": "textbox",
                "name": "搜索想定名称",
                "valueState": "empty",
                "disabled": False,
            }],
        ),
    )

    search = pack.next_required_action(list_page, [], scenario)
    assert search is not None
    assert search.action == ActionType.FILL
    assert search.value == "蓝军临时想定-09"
    assert search.locator.runtime_id == "ai_12"

    searched = _history_step(1, "fill", search.description or "", list_page)
    open_card = pack.next_required_action(list_page, [searched], scenario)
    assert open_card is not None
    assert open_card.action == ActionType.CLICK
    assert open_card.locator.scope.identity == "蓝军临时想定-09"
    assert open_card.locator.name == "修改"


def test_parameterized_form_is_used_by_capability_pack_for_arbitrary_name() -> None:
    pack = GAEALaViCCapabilityPack()
    resource_name = "九月红蓝对抗想定-临时09"
    scenario = AgentScenario(
        name="parameterized scenario form",
        goal=f"实际创建想定，想定名称为 {resource_name}",
        test_data={"scenarioName": resource_name},
    )
    form = Observation(
        url="http://192.168.31.218:7991/#/scenarioEdit",
        semantic_summary=PageSemanticSummary(
            wizard={"visible": True, "text": "基本信息 想定名称"},
            controls=[{
                "runtimeId": "ai_55",
                "role": "textbox",
                "name": "想定名称",
                "valueState": "empty",
                "required": True,
                "disabled": False,
            }],
        ),
    )

    action = pack.next_required_action(form, [], scenario)

    assert action is not None
    assert action.action == ActionType.FILL
    assert action.value == resource_name
    assert action.locator.runtime_id == "ai_55"


def test_parameterized_fast_path_refuses_delete_task() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("找到 test_D 并删除该想定")
    observation = Observation(
        url="http://192.168.31.218:7991/#/scenarioTable/",
        accessibility_summary="test_D 修改 删除",
        semantic_summary=PageSemanticSummary(resource_names=["test_D"]),
    )

    assert pack.next_required_action(observation, [], scenario) is None


def test_parameterized_run_start_requires_exact_bound_target() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("找到 蓝军临时想定-09，进入想定并启动仿真")
    run_page = Observation(
        url=(
            "http://192.168.31.218:7991/#/situationPage?"
            "type=run&simulationStatus=Unstart"
        ),
        accessibility_summary="启动",
        semantic_summary=PageSemanticSummary(controls=[{
            "runtimeId": "ai_41",
            "role": "button",
            "name": "启动",
            "disabled": False,
        }]),
    )

    assert pack.next_required_action(run_page, [], scenario) is None

    opened = _history_step(
        2,
        "click",
        "在名称为 蓝军临时想定-09 的唯一卡片内打开修改入口",
        run_page,
    )
    start = pack.next_required_action(run_page, [opened], scenario)
    assert start is not None
    assert start.action == ActionType.CLICK
    assert start.locator.runtime_id == "ai_41"
    assert start.action_category == "start_simulation"


def test_parameterized_run_stop_is_not_emitted_before_required_movement() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("进入想定 test_D，启动仿真，切换 x50，观察实体移动后停止仿真")
    running = Observation(
        url=(
            "http://192.168.31.218:7991/#/situationPage?"
            "type=run&simulationStatus=Running"
        ),
        accessibility_summary="停止 暂停 x50",
        semantic_summary=PageSemanticSummary(controls=[{
            "runtimeId": "ai_52",
            "role": "button",
            "name": "停止",
            "disabled": False,
        }]),
    )
    opened = _history_step(
        1, "click", "在名称为 test_D 的唯一卡片内打开修改入口", running
    )

    assert pack.next_required_action(running, [opened], scenario) is None


def test_created_model_requires_final_create_then_list_presence() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("\u521b\u5efa\u667a\u80fd\u4f53\uff0c\u540d\u79f0\u4e3a test_D\uff0c\u8fd4\u56de\u5217\u8868\u9a8c\u8bc1")
    list_observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(resource_names=["test_D"]),
    )
    create_step = _history_step(
        8,
        "click",
        "\u70b9\u51fb\u6700\u7ec8\u521b\u5efa test_D",
        Observation(url="http://192.168.31.218:7991/#/agentEditPage?agentKey=1"),
    )

    assert "model_created_verified" not in pack.remaining_stages(
        list_observation, [create_step], scenario
    )
    assertions = pack.terminal_assertions(list_observation, [create_step], scenario)
    assert len(assertions) == 1
    assert assertions[0].type.value == "text_contains"
    assert assertions[0].expected == "test_D"


def test_pending_create_is_resolved_only_by_independent_list_presence() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("实际创建智能体 test_E，返回列表验证")
    editor = Observation(
        url="http://192.168.31.218:7991/#/agentEditPage?agentKey=2"
    )
    pending = _history_step(8, "click", "点击最终创建 test_E", editor).model_copy(
        update={
            "status": Status.INCOMPLETE,
            "progress_assessment": "pending_business_verification",
        }
    )
    absent = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(resource_names=[]),
    )
    present = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(resource_names=["test_E"]),
    )

    assert "model_created_verified" in pack.remaining_stages(absent, [pending], scenario)
    assert "model_created_verified" not in pack.remaining_stages(present, [pending], scenario)


def test_search_value_without_create_does_not_prove_resource_creation() -> None:
    pack = GAEALaViCCapabilityPack()
    scenario = _scenario("\u521b\u5efa\u667a\u80fd\u4f53\uff0c\u540d\u79f0\u4e3a test_D")
    observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(resource_names=["test_D"]),
    )
    search = _history_step(1, "fill", "\u641c\u7d22 test_D", observation)

    assert "model_created_verified" in pack.remaining_stages(
        observation, [search], scenario
    )
    assert pack.terminal_assertions(observation, [search], scenario) == []


def test_test_model_writes_have_a_narrow_reversible_default_policy() -> None:
    policies = GAEALaViCCapabilityPack().default_side_effect_policies()
    create = next(item for item in policies if item["actionCategory"] == "create")
    update = next(item for item in policies if item["actionCategory"] == "update")
    assert create["objectType"] == "model"
    assert create["namePattern"] == r"^E2E_GAEALAVIC_[A-F0-9]{24}$"
    assert create["decision"] == "conditional"
    assert create["rollbackRule"]
    assert update["decision"] == "allow"
    assert "Never delete" in update["rollbackRule"]

    step = Step.model_validate({
        "action": "click",
        "locator": {"role": "button", "name": "Save"},
        "effect_level": "reversible_write",
        "action_category": "update",
        "object_type": "model",
        "resource_name": "test_H",
        "business_object_name": GAEALaViCCapabilityPack().internal_ledger_name("test_H"),
        "cleanup_required": False,
    })
    evidence = evaluate_side_effect(
        step, policies, environment_id=None, role="tester"
    )
    assert evidence is not None
    assert evidence["decision"] == "allow"


def test_simulation_model_combobox_click_targets_visible_ant_selector() -> None:
    normalized = _normalize_agent_payload({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {
                "role": "combobox",
                "label": "选择已有仿真模型",
            },
            "description": "展开当前仿真模型候选项并重新观察",
            "effect_level": "session_only",
        },
        "reason": "Open the dynamic selector before choosing an observed compatible option",
        "progress_assessment": "progress",
    }, "http://192.168.31.218:7991")

    assert normalized["action"]["locator"]["role"] == "combobox"
    assert "css" not in normalized["action"]["locator"]


def test_incomplete_simulation_model_component_opens_selector_before_validation() -> None:
    normalized = _normalize_agent_payload({
        "kind": "action",
        "action": {
            "action": "component",
            "description": "展开选择已有仿真模型并重新观察候选项",
            "component": {
                "kind": "searchable_select",
                "semanticTarget": "选择已有仿真模型",
                "locators": [{"role": "combobox"}],
                "values": [],
            },
            "effect_level": "session_only",
        },
        "reason": "Open the closed dynamic selector before choosing an option",
        "progress_assessment": "progress",
    }, "http://192.168.31.218:7991")

    decision = AgentDecision.model_validate(normalized)

    assert decision.action is not None
    assert decision.action.action is ActionType.CLICK
    assert decision.action.locator.role == "combobox"
    assert decision.action.component is None


def test_observed_simulation_component_value_targets_visible_ant_option() -> None:
    option = "空战蓝方战机（超视距空战想定）_副本（1）"
    normalized = _normalize_agent_payload({
        "kind": "action",
        "action": {
            "action": "component",
            "description": "从已展开的选择已有仿真模型候选项中选择兼容模型",
            "component": {
                "kind": "searchable_select",
                "semanticTarget": "选择已有仿真模型",
                "locators": [
                    {"css": "dialog[open] .ant-select-selector"},
                    {"role": "combobox"},
                    {"role": "option", "name": option},
                ],
                "values": [option],
            },
            "effect_level": "session_only",
        },
        "reason": "Choose one option observed in the current open dropdown",
        "progress_assessment": "progress",
    }, "http://192.168.31.218:7991")

    decision = AgentDecision.model_validate(normalized)

    assert decision.action is not None
    assert decision.action.action is ActionType.COMPONENT
    assert decision.action.component is not None
    assert decision.action.component.values == [option]
    assert "one observable browser transition" in (
        _decision_state_contract_violation(
            decision,
            {"taskAuthorization": {"createAllowed": True}},
        )
        or ""
    )


def test_simulation_role_option_targets_unique_visual_ant_option() -> None:
    option = "物流无人机A（低空想定选址）"
    normalized = _normalize_agent_payload({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "option", "name": option},
            "description": "选择已有仿真模型的当前可见兼容选项",
            "effect_level": "session_only",
        },
        "reason": "Choose one option observed in the current open dropdown",
        "progress_assessment": "progress",
    }, "http://192.168.31.218:7991")

    decision = AgentDecision.model_validate(normalized)

    assert decision.action is not None
    assert decision.action.locator is not None
    assert decision.action.locator.role == "option"
    assert decision.action.locator.name == option
    assert decision.action.locator.css is None
