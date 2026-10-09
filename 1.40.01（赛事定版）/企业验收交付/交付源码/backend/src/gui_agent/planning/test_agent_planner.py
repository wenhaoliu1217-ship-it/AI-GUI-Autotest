import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from gui_agent.domain.models import ActionType
from gui_agent.domain.results import (
    FailureCategory,
    Observation,
    PageSemanticSummary,
    Status,
    StepResult,
)
from gui_agent.execution.agent_runner import _approval_rule, _show_human_takeover_window
from gui_agent.execution.runner import _cause_hint
from gui_agent.planning.agent_planner import (
    AIAgentPlanner,
    AGENT_PROMPT_MAX_CHARS,
    AgentDecision,
    AgentScenario,
    _agent_prompt,
    _bounded_observation_for_prompt,
    _compact_prompt_schema,
    _decision_state_contract_violation,
    _normalize_empty_search_focus,
    _normalize_agent_payload,
    _repeatable_form_contract,
)
from gui_agent.planning.ai_provider import AISettings, _strict_schema


class _ModelFirstSitePack:
    def __init__(
        self,
        remaining: tuple[str, ...] = (),
        context: dict | None = None,
    ) -> None:
        self.remaining = remaining
        self.context = context

    def planner_context(self, _observation, _history, _scenario) -> dict:
        return self.context or {
            "remainingStages": list(self.remaining),
            "adapterMode": "advisory",
        }

    @staticmethod
    def effective_business_context(context: dict) -> dict:
        return context

    def remaining_stages(self, _observation, _history, _scenario) -> list[str]:
        return list(self.remaining)

    @staticmethod
    def required_followup_action(*_args):
        raise AssertionError("site adapters must not choose a runtime action")

    @staticmethod
    def next_required_action(*_args):
        raise AssertionError("site adapters must not override a model decision")


def _model_first_planner(decision, pack: _ModelFirstSitePack):
    calls = []
    decisions = decision if isinstance(decision, list) else [decision]

    def request(**kwargs):
        response = decisions[min(len(calls), len(decisions) - 1)]
        calls.append(kwargs)
        return SimpleNamespace(
            value=response,
            elapsed_ms=7,
            input_tokens=100,
            output_tokens=20,
            attempt_count=1,
            repair_count=0,
        )

    planner = object.__new__(AIAgentPlanner)
    planner.settings = AISettings(
        protocol="responses",
        base_url="https://api.example.test/v1",
        model="model-first-test",
        api_key=SecretStr("test-key"),
    )
    planner.scenario = AgentScenario(name="model-first", goal="Inspect the current page")
    planner.base_url = "https://app.example.test"
    planner.visual_enabled = True
    planner.multimodal_required = True
    planner.successful_experiences = []
    planner.site_pack = pack
    planner.gateway = SimpleNamespace(request=request)
    return planner, calls


def _multimodal_decide(planner, observation: Observation, tmp_path):
    screenshot = tmp_path / "current.png"
    screenshot.write_bytes(b"current-page-image-evidence")
    observation.screenshot = "screenshots/current.png"
    return planner.decide(observation, [], 1, screenshot_path=screenshot)


def test_external_model_is_primary_even_when_an_adapter_has_stages(tmp_path) -> None:
    model_decision = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "button", "name": "Continue"},
            "description": "Continue from the latest observed page",
            "effect_level": "read_only",
        },
        "reason": "The current page exposes a valid Continue action",
        "progress_assessment": "progress",
    })
    planner, calls = _model_first_planner(
        model_decision,
        _ModelFirstSitePack(("wizard_step_2",)),
    )

    result = _multimodal_decide(
        planner, Observation(url="https://app.example.test/wizard"), tmp_path
    )

    assert len(calls) == 1
    assert result.model == "model-first-test"
    assert result.protocol == "responses"
    assert result.multimodal is True
    assert calls[0]["prompt"][0]["content"][1]["type"] == "input_image"
    assert result.decision is model_decision


def test_local_decision_uses_no_gateway_or_multimodal_payload() -> None:
    action = {
        "action": "fill",
        "locator": {"role": "textbox", "name": "搜索想定名称"},
        "value": "任意新名称-09",
        "description": "按本次任务参数精确搜索 任意新名称-09",
        "effect_level": "session_only",
    }

    class _LocalPack(_ModelFirstSitePack):
        site_id = "intranet-test"

        @staticmethod
        def required_followup_action(*_args):
            return None

        @staticmethod
        def next_required_action(*_args):
            from gui_agent.domain.models import Step
            return Step.model_validate(action)

    planner, calls = _model_first_planner(
        AgentDecision(kind="blocked", reason="must not be called"),
        _LocalPack(),
    )

    result = planner.decide_locally(Observation(url="https://app.example.test"), [], 1)

    assert result is not None
    assert calls == []
    assert result.protocol == "local"
    assert result.multimodal is False
    assert result.input_tokens == 0
    assert result.output_tokens == 0


def test_adapter_can_reject_premature_completion_without_choosing_an_action(tmp_path) -> None:
    planner, calls = _model_first_planner(
        AgentDecision(
            kind="complete",
            reason="The requested checks appear complete",
            progress_assessment="progress",
        ),
        _ModelFirstSitePack(("final_business_assertion",)),
    )

    result = _multimodal_decide(
        planner, Observation(url="https://app.example.test/result"), tmp_path
    )

    assert len(calls) == 1
    assert result.model == "model-first-test"
    assert result.decision.kind == "blocked"
    assert "final_business_assertion" in result.decision.reason


def test_model_completion_is_preserved_when_independent_validation_passes(tmp_path) -> None:
    model_decision = AgentDecision(
        kind="complete",
        reason="The requested checks and postconditions are complete",
        progress_assessment="progress",
    )
    planner, calls = _model_first_planner(model_decision, _ModelFirstSitePack())

    result = _multimodal_decide(
        planner, Observation(url="https://app.example.test/result"), tmp_path
    )

    assert len(calls) == 1
    assert result.decision is model_decision


def test_available_locked_name_rejects_repeat_search_and_replans_to_create(tmp_path) -> None:
    repeat_search = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "fill",
            "locator": {"role": "textbox", "name": "搜索我的模型..."},
            "value": "test_C",
            "description": "再次精确搜索 test_C",
            "effect_level": "read_only",
        },
        "reason": "Repeat the exact conflict search",
        "progress_assessment": "progress",
    })
    open_create = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "button", "name": "新增"},
            "description": "Open the create wizard after the zero-result conflict check",
            "effect_level": "session_only",
        },
        "reason": "The locked name is available, so creation is the next action",
        "progress_assessment": "progress",
    })
    pack = _ModelFirstSitePack(context={
        "resourceNameContract": {
            "visibleName": "test_C",
            "allocationState": {
                "lockedName": "test_C",
                "conflictCheckStatus": "available",
                "searchAllowed": False,
                "nextRequiredBusinessAction": "open_create_wizard",
            },
        },
    })
    planner, calls = _model_first_planner([repeat_search, open_create], pack)

    result = _multimodal_decide(
        planner, Observation(url="https://app.example.test/models"), tmp_path
    )

    assert len(calls) == 2
    repair_text = " ".join(
        item.get("text", "")
        for message in calls[1]["prompt"]
        for item in message.get("content", [])
        if isinstance(item, dict)
    )
    assert "DECISION_REJECTED_BY_STATE_VALIDATOR" in repair_text
    assert result.decision is open_create
    assert result.attempt_count == 2
    assert result.repair_count == 1


def test_authorized_final_confirmation_rejects_cancel_without_rollback() -> None:
    decision = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "button", "name": "取消"},
            "description": "取消当前确认对话框",
            "effect_level": "session_only",
        },
        "reason": "Leave the confirmation step",
        "progress_assessment": "progress",
    })

    violation = _decision_state_contract_violation(decision, {
        "taskAuthorization": {"createAllowed": True},
        "pageStage": "model_wizard_step_4",
        "recoveryContract": {"must_rollback_or_clarify": False},
    })

    assert violation is not None
    assert "Do not cancel" in violation


def test_authorized_final_confirmation_allows_create() -> None:
    decision = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "button", "name": "创建"},
            "description": "提交已锁定的测试模型",
            "effect_level": "high_risk_write",
        },
        "reason": "The user authorized one creation and the wizard is ready",
        "progress_assessment": "progress",
    })

    violation = _decision_state_contract_violation(decision, {
        "taskAuthorization": {"createAllowed": True},
        "pageStage": "model_wizard_step_4",
        "recoveryContract": {"must_rollback_or_clarify": False},
    })

    assert violation is None


def _repeatable_form_observation(selected_text: str | None = None) -> Observation:
    components = [
        {
            "runtimeId": "ai_120",
            "kind": "native_select",
            "selectedText": "蓝方",
        }
    ]
    if selected_text is not None:
        components.append(
            {
                "runtimeId": "ai_143",
                "kind": "cascader",
                "selectedText": selected_text,
            }
        )
    return Observation(
        url="http://192.168.31.218:7991/#/agentCreatePage",
        semantic_summary=PageSemanticSummary(components=components),
    )


def _repeatable_add_result(before: Observation, after: Observation) -> StepResult:
    now = datetime.now().astimezone()
    return StepResult(
        index=13,
        action="click",
        description="在当前创建向导点击蓝色加号新增装备类型一行",
        target_summary="runtime_id=ai_137, role=button[name=+]",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
        before=before,
        after=after,
    )


def test_repeatable_form_requires_new_row_to_be_filled_before_add_or_next() -> None:
    before = _repeatable_form_observation()
    pending = _repeatable_form_observation("请选择")

    assert _repeatable_form_contract(before, [])["active"] is False

    history = [_repeatable_add_result(before, pending)]
    contract = _repeatable_form_contract(pending, history)
    assert contract["active"] is True
    assert contract["addRuntimeId"] == "ai_137"
    assert contract["pendingRuntimeIds"] == ["ai_143"]

    repeat_add = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"runtime_id": "ai_137"},
            "description": "再次新增装备类型",
            "effect_level": "session_only",
        },
        "reason": "Add another row",
    })
    advance = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "button", "name": "下一步"},
            "description": "进入下一步",
            "effect_level": "session_only",
        },
        "reason": "Advance",
    })
    fill_pending = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"runtime_id": "ai_143"},
            "description": "展开新增的装备类型选择器",
            "effect_level": "session_only",
        },
        "reason": "Fill the pending row",
    })
    context = {"repeatableFormContract": contract}

    assert "do not add another row" in (
        _decision_state_contract_violation(repeat_add, context) or ""
    )
    assert "before advancing" in (
        _decision_state_contract_violation(advance, context) or ""
    )
    assert _decision_state_contract_violation(fill_pending, context) is None

    completed = _repeatable_form_observation("飞机 / 固定翼")
    assert _repeatable_form_contract(completed, history)["active"] is False


def test_wizard_next_revealing_a_required_selector_is_not_an_add_row() -> None:
    before = _repeatable_form_observation()
    pending = _repeatable_form_observation("请选择")
    now = datetime.now().astimezone()
    transition = StepResult(
        index=6,
        action="click",
        description="进入关键信息步骤",
        target_summary="runtime_id=ai_128, role=button[name=下一步]",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
        before=before,
        after=pending,
        planner_reason="点击下一步后重新观察新向导步骤",
    )

    assert _repeatable_form_contract(pending, [transition]) == {"active": False}


def test_primary_planner_rejects_missing_current_screenshot() -> None:
    planner, _calls = _model_first_planner(
        AgentDecision(kind="blocked", reason="unused"), _ModelFirstSitePack()
    )

    with pytest.raises(Exception, match="missing the current-page screenshot"):
        planner.decide(Observation(url="https://app.example.test"), [], 1)


def test_cesium_read_only_action_drops_inapplicable_action_category() -> None:
    raw = {
        "kind": "action",
        "action": {
            "action": "navigate",
            "target": "/",
            "description": "Open Cesium ion",
            "effect_kind": "browse_search_filter_sort",
            "effect_level": "read_only",
            "action_category": "navigation",
        },
        "reason": "Open the requested site",
        "progress_assessment": "progress",
    }

    normalized = _normalize_agent_payload(raw, "https://ion.cesium.com")
    decision = AgentDecision.model_validate(normalized)

    assert decision.action is not None
    assert decision.action.action is ActionType.NAVIGATE
    assert decision.action.action_category is None


def test_generic_read_only_action_drops_inapplicable_action_category() -> None:
    raw = {
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "menuitem", "name": "Analytics"},
            "description": "Inspect another authenticated module",
            "effect_kind": "browse_search_filter_sort",
            "effect_level": "read_only",
            "action_category": "navigation",
        },
        "reason": "Continue the read-only site inspection",
        "progress_assessment": "progress",
    }

    normalized = _normalize_agent_payload(raw, "https://app.example.test")
    decision = AgentDecision.model_validate(normalized)

    assert decision.action is not None
    assert decision.action.action is ActionType.CLICK
    assert decision.action.action_category is None


def test_visual_metadata_with_structured_fill_locator_is_demoted_to_locator() -> None:
    events: list[dict] = []
    normalized = _normalize_agent_payload(
        {
            "kind": "action",
            "action": {
                "action": "fill",
                "locator": {"role": "textbox", "name": "任务路径点关键字"},
                "value": "test_H_path",
                "execution_mode": "visual",
                "visual_target": "任务路径点关键字",
                "relative_position": {"xRatio": 0.52, "yRatio": 0.41},
                "computer_use_triggered": True,
                "computer_use_reason": "DOM snapshot was empty",
            },
            "reason": "Fill the focused path keyword field",
        },
        "http://192.168.31.218:7991",
        events,
    )

    decision = AgentDecision.model_validate(normalized)
    assert decision.action is not None
    assert decision.action.execution_mode.value == "locator"
    assert decision.action.locator is not None
    assert decision.action.value == "test_H_path"
    assert "visual_target" not in normalized["action"]
    assert "relative_position" not in normalized["action"]
    assert events and events[0]["type"] == "visual_metadata_demoted_to_locator"


def test_empty_gaealavic_search_focus_is_promoted_to_fill_for_current_target() -> None:
    events: list[dict] = []
    decision = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"runtime_id": "ai_19", "role": "textbox", "name": "搜索我的模型..."},
            "description": "聚焦搜索框",
        },
        "reason": "填写当前目标",
    })
    observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(controls=[{
            "runtimeId": "ai_19",
            "role": "textbox",
            "name": "搜索我的模型...",
            "valueState": "empty",
        }]),
    )

    normalized = _normalize_empty_search_focus(
        decision,
        observation,
        {"sitePack": "gaealavic", "resourceNameContract": {"visibleName": "test_A"}},
        events,
    )

    assert normalized.action is not None
    assert normalized.action.action is ActionType.FILL
    assert normalized.action.value == "test_A"
    assert events and events[0]["type"] == "empty_search_focus_promoted_to_fill"


def test_empty_search_focus_without_runtime_id_matches_current_semantic_control() -> None:
    events: list[dict] = []
    decision = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "textbox", "placeholder": "搜索我的模型..."},
            "description": "聚焦当前搜索框",
        },
        "reason": "查找当前锁定目标",
    })
    observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        semantic_summary=PageSemanticSummary(controls=[
            {"runtimeId": "ai_2", "role": "button", "name": "新增", "valueState": ""},
            {
                "runtimeId": "ai_19",
                "role": "textbox",
                "name": "搜索我的模型...",
                "placeholder": "搜索我的模型...",
                "valueState": "empty",
            },
        ]),
    )

    normalized = _normalize_empty_search_focus(
        decision,
        observation,
        {"sitePack": "gaealavic", "resourceNameContract": {"visibleName": "test_A"}},
        events,
    )

    assert normalized.action is not None
    assert normalized.action.action is ActionType.FILL
    assert normalized.action.value == "test_A"


def test_explicit_visual_click_is_not_demoted_even_with_canvas_metadata() -> None:
    events: list[dict] = []
    normalized = _normalize_agent_payload(
        {
            "kind": "action",
            "action": {
                "action": "visual_click",
                "execution_mode": "visual",
                "visual_target": "3D canvas marker",
                "relative_position": {"xRatio": 0.5, "yRatio": 0.5},
            },
            "reason": "Click the visible 3D marker",
        },
        "http://192.168.31.218:7991",
        events,
    )

    assert normalized["kind"] == "visual"
    assert "action" not in normalized
    assert normalized["visual_request"]["target"] == "3D canvas marker"
    assert events and events[0]["type"] == "direct_visual_action_routed_through_adapter"


def test_locatorless_visual_action_stays_visual() -> None:
    events: list[dict] = []
    normalized = _normalize_agent_payload(
        {
            "kind": "action",
            "action": {
                "action": "click",
                "execution_mode": "visual",
                "visual_target": "unlabeled control",
                "relative_position": {"xRatio": 0.2, "yRatio": 0.3},
            },
            "reason": "Use the visible unlabeled control",
        },
        "https://app.example.test",
        events,
    )

    assert normalized["action"]["execution_mode"] == "visual"
    assert events == []


def test_screenshot_payload_drops_form_fields_and_records_normalization_event() -> None:
    events: list[dict] = []
    raw = {
        "kind": "action",
        "action": {
            "action": "screenshot",
            "locator": {"role": "button", "name": "启动"},
            "value": "unexpected-filler",
            "value_from_secret": "E2E_SCREENSHOT_TOKEN",
            "description": "Capture the current page as evidence",
        },
        "reason": "Record the current state",
        "progress_assessment": "progress",
    }

    normalized = _normalize_agent_payload(
        raw,
        "http://192.168.31.218:7991",
        events,
    )

    decision = AgentDecision.model_validate(normalized)
    assert decision.action is not None
    assert decision.action.action is ActionType.SCREENSHOT
    assert all(field not in normalized["action"] for field in (
        "locator",
        "value",
        "value_from_secret",
    ))
    assert events == [{
        "type": "screenshot_payload_fields_dropped",
        "action": "screenshot",
        "removedFields": ["locator", "value", "value_from_secret"],
        "reason": "screenshot actions do not accept locator or value fields",
    }]


def test_valid_screenshot_payload_does_not_emit_normalization_event() -> None:
    events: list[dict] = []
    normalized = _normalize_agent_payload(
        {
            "kind": "action",
            "action": {
                "action": "screenshot",
                "description": "Capture the current page as evidence",
            },
            "reason": "Record the current state",
        },
        "https://app.example.test",
        events,
    )

    decision = AgentDecision.model_validate(normalized)
    assert decision.action is not None
    assert decision.action.action is ActionType.SCREENSHOT
    assert events == []


def test_generic_write_action_keeps_lifecycle_metadata_validation() -> None:
    raw = {
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "button", "name": "Create workspace"},
            "description": "Create a workspace",
            "effect_kind": "create_workspace",
            "effect_level": "reversible_write",
            "action_category": "create",
        },
        "reason": "Start a write flow",
        "progress_assessment": "progress",
    }

    normalized = _normalize_agent_payload(raw, "https://app.example.test")

    try:
        AgentDecision.model_validate(normalized)
    except ValueError as exc:
        assert "object_type" in str(exc)
        assert "business_object_name" in str(exc)
    else:
        raise AssertionError("write lifecycle metadata must remain subject to schema validation")


def test_natural_language_navigation_target_falls_back_to_site_root() -> None:
    raw = {
        "kind": "action",
        "action": {
            "action": "navigate",
            "target": "Return to the site root and inspect the default page",
            "effect_kind": "browse_search_filter_sort",
            "effect_level": "read_only",
        },
        "reason": "Inspect the default page",
        "progress_assessment": "progress",
    }

    normalized = _normalize_agent_payload(raw, "https://app.example.test")
    decision = AgentDecision.model_validate(normalized)

    assert decision.action is not None
    assert decision.action.target == "/"
    assert decision.action.description == "Return to the site root and inspect the default page"


def test_wait_without_locator_is_grounded_to_generic_loading_surface() -> None:
    normalized = _normalize_agent_payload(
        {
            "kind": "action",
            "action": {
                "action": "wait_for",
                "value": "visible",
            },
            "reason": "The 3D editor is still loading; observe again after it settles",
        },
        "http://192.168.31.218:7991",
    )

    decision = AgentDecision.model_validate(normalized)
    assert decision.action is not None
    assert decision.action.action is ActionType.WAIT_FOR
    assert decision.action.value == "hidden"
    assert decision.action.locator is not None
    assert ".page-loading-placeholder" in (decision.action.locator.css or "")
    assert "重新观察" in (decision.action.description or "")


def test_wait_with_grounded_locator_is_not_rewritten() -> None:
    normalized = _normalize_agent_payload(
        {
            "kind": "action",
            "action": {
                "action": "wait_for",
                "locator": {"role": "heading", "name": "3D 建模"},
                "value": "visible",
            },
            "reason": "Wait for the editor heading",
        },
        "https://app.example.test",
    )

    decision = AgentDecision.model_validate(normalized)
    assert decision.action is not None
    assert decision.action.locator is not None
    assert decision.action.locator.role == "heading"
    assert decision.action.value == "visible"


def test_valid_navigation_targets_are_preserved() -> None:
    for target in ("/", "./settings", "../login", "#/projects", "?page=2", "assets/list", "/中文路径", "https://docs.example.test/start"):
        normalized = _normalize_agent_payload(
            {
                "kind": "action",
                "action": {
                    "action": "navigate",
                    "target": target,
                    "effect_level": "read_only",
                },
                "reason": "Open a valid route",
            },
            "https://app.example.test",
        )
        assert normalized["action"]["target"] == target


def test_empty_defaulted_gesture_fields_use_bounded_defaults() -> None:
    raw = {
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "menuitem", "name": "帮助"},
            "zoom_delta": None,
            "scroll_delta_y": "  ",
            "expected_residual_count": "",
        },
        "reason": "Open the help module",
        "progress_assessment": "progress",
    }

    normalized = _normalize_agent_payload(raw, "https://app.example.test")
    decision = AgentDecision.model_validate(normalized)

    assert normalized["action"].get("zoom_delta") is None
    assert normalized["action"].get("scroll_delta_y") is None
    assert normalized["action"].get("expected_residual_count") is None
    assert decision.action is not None
    assert decision.action.zoom_delta == -600
    assert decision.action.scroll_delta_y == 600
    assert decision.action.expected_residual_count == 0


def test_invalid_defaulted_gesture_fields_remain_rejected() -> None:
    raw = {
        "kind": "action",
        "action": {
            "action": "scroll",
            "zoom_delta": "not-a-number",
            "scroll_delta_y": 9000,
        },
        "reason": "Continue reviewing the page",
        "progress_assessment": "progress",
    }

    normalized = _normalize_agent_payload(raw, "https://app.example.test")

    try:
        AgentDecision.model_validate(normalized)
    except ValidationError as exc:
        locations = {tuple(error["loc"]) for error in exc.errors()}
        assert ("action", "zoom_delta") in locations
        assert ("action", "scroll_delta_y") in locations
    else:
        raise AssertionError("non-empty invalid gesture values must be rejected")


def test_cesium_write_action_keeps_incomplete_lifecycle_metadata_rejected() -> None:
    raw = {
        "kind": "action",
        "action": {
            "action": "click",
            "locator": {"role": "button", "name": "Add data"},
            "description": "Start an upload",
            "effect_kind": "upload_or_cloud_import",
            "effect_level": "reversible_write",
            "cleanup_action": "delete ledger-owned asset/task",
            "action_category": "create",
        },
        "reason": "Start a write flow",
        "progress_assessment": "progress",
    }

    normalized = _normalize_agent_payload(raw, "https://ion.cesium.com")

    try:
        AgentDecision.model_validate(normalized)
    except ValueError as exc:
        assert "object_type" in str(exc)
        assert "business_object_name" in str(exc)
    else:
        raise AssertionError("write lifecycle metadata must remain subject to schema validation")


def test_model_failure_hint_keeps_the_safe_validation_reason() -> None:
    hint = _cause_hint(
        FailureCategory.MODEL,
        1,
        "Agent 单步决策未通过安全 Schema 校验：action 缺少 locator",
    )

    assert "AI 决策格式未通过安全校验" in hint.message
    assert "缺少 locator" in hint.message


def test_cesium_human_takeover_waits_for_authenticated_account_control() -> None:
    raw = {
        "kind": "action",
        "action": {
            "action": "human_takeover",
            "description": "User signs in",
            "takeover_reason": "other",
            "effect_kind": "browse_search_filter_sort",
            "effect_level": "read_only",
        },
        "reason": "Credentials must remain with the user",
    }

    decision = AgentDecision.model_validate(
        _normalize_agent_payload(raw, "https://ion.cesium.com")
    )

    assert decision.action is not None
    assert decision.action.browser_target.wait_timeout_ms == 600_000
    assert decision.action.takeover_resume_locator is not None
    assert decision.action.takeover_resume_locator.test_id == "account-button-in-header"
    assert decision.action.effect_kind == "browse_search_filter_sort"
    assert decision.action.effect_level.value == "read_only"
    assert _approval_rule(decision.action, "ask", None) is None


def test_cesium_human_takeover_accepts_camel_case_gateway_aliases() -> None:
    raw = {
        "kind": "action",
        "action": {
            "action": "human_takeover",
            "description": "User signs in",
            "takeoverReason": "other",
            "browserTarget": {"urlContains": "/scenarioCaseDetails"},
        },
        "reason": "Credentials must remain with the user",
    }
    decision = AgentDecision.model_validate(
        _normalize_agent_payload(raw, "https://ion.cesium.com/stories/editor")
    )
    assert decision.action is not None
    assert decision.action.takeover_reason == "other"
    assert decision.action.browser_target.url_contains == "/scenarioCaseDetails"


def test_human_takeover_window_is_restored_and_foregrounded() -> None:
    calls: list[tuple[str, dict | None]] = []

    class Session:
        @staticmethod
        def send(method: str, params: dict | None = None) -> dict:
            calls.append((method, params))
            return {"windowId": 7} if method == "Browser.getWindowForTarget" else {}

        @staticmethod
        def detach() -> None:
            calls.append(("detach", None))

    class Context:
        @staticmethod
        def new_cdp_session(_page) -> Session:
            return Session()

    class Page:
        @staticmethod
        def bring_to_front() -> None:
            calls.append(("bring_to_front", None))

    _show_human_takeover_window(Context(), Page())

    assert calls[0] == ("bring_to_front", None)
    assert calls[1] == ("Browser.getWindowForTarget", None)
    assert calls[-1] == ("detach", None)


def test_chat_prompt_schema_is_compact_but_keeps_validation_shape() -> None:
    schema = _strict_schema(AgentDecision.model_json_schema())
    compact = _compact_prompt_schema(schema)

    assert len(str(compact)) < len(str(schema)) * 0.7
    assert "$defs" in compact
    assert "properties" in compact
    assert "description" not in str(compact)


def test_large_observation_prompt_is_bounded_and_keeps_expanded_cascader_leaf() -> None:
    options = [
        {"runtimeId": f"option-{index}", "text": f"Option {index}", "selected": False, "disabled": False}
        for index in range(120)
    ]
    options[-1]["text"] = "C(communication)"
    components = [
        {
            "runtimeId": f"trigger-{index}",
            "kind": "cascader",
            "expanded": index == 39,
            "visibleOptions": list(options),
            "visibleOptionGroups": [{"groupIndex": 2, "options": list(options)}],
            "optionCount": len(options),
        }
        for index in range(40)
    ]
    observation = Observation(
        url="http://192.168.31.218:7991/#/mineModelList",
        dom_summary=["control " + ("x" * 500) for _ in range(200)],
        accessibility_summary="tree " * 100_000,
        semantic_summary=PageSemanticSummary(
            route="/#/mineModelList",
            controls=[{"runtimeId": f"control-{index}", "role": "button", "name": "x" * 500} for index in range(160)],
            components=components,
        ),
    )

    facts = _bounded_observation_for_prompt(observation)
    prompt = _agent_prompt(
        scenario=AgentScenario(name="create", goal="Create the authorized test model"),
        base_url="http://192.168.31.218:7991",
        observation=observation,
        history=[],
        call_index=17,
        schema=_compact_prompt_schema(_strict_schema(AgentDecision.model_json_schema())),
        visual_enabled=True,
        site_pack=_ModelFirstSitePack(context={"observedComponents": components}),
        site_context={"observedComponents": components},
    )

    assert len(json.dumps(facts, ensure_ascii=False)) <= 55_000
    assert len(prompt) <= AGENT_PROMPT_MAX_CHARS
    assert "C(communication)" in prompt
    assert prompt.count("C(communication)") <= 2


def test_cesium_spinner_glyph_wait_uses_loading_placeholder() -> None:
    normalized = _normalize_agent_payload(
        {
            "kind": "action",
            "action": {
                "action": "wait_for",
                "locator": {"text": "\uf110"},
                "value": "hidden",
                "effect_kind": "browse_search_filter_sort",
                "effect_level": "read_only",
            },
            "reason": "Wait for loading to finish",
        },
        "https://ion.cesium.com",
    )

    assert normalized["action"]["locator"] == {"css": ".page-loading-placeholder"}


def test_read_only_search_fill_does_not_require_write_approval() -> None:
    step = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "fill",
            "locator": {"role": "searchbox", "name": "Search for..."},
            "value": "Google",
            "effect_kind": "browse_search_filter_sort",
            "effect_level": "read_only",
        },
        "reason": "Filter the current table without changing stored data",
    }).action

    assert step is not None
    assert _approval_rule(step, "ask", None) is None


def test_secret_or_write_fill_still_requires_approval() -> None:
    secret_step = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "fill",
            "locator": {"role": "textbox", "name": "Token"},
            "value_from_secret": "E2E_TOKEN",
            "effect_kind": "browse_search_filter_sort",
            "effect_level": "session_only",
        },
        "reason": "Use a secret in a field",
    }).action
    write_step = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "fill",
            "locator": {"role": "textbox", "name": "Name"},
            "value": "E2E_resource",
            "effect_kind": "create_story",
            "effect_level": "reversible_write",
        },
        "reason": "Prepare a persistent write",
    }).action

    assert secret_step is not None
    assert write_step is not None
    assert _approval_rule(secret_step, "ask", None) == "approval-mode:write-action"
    assert _approval_rule(write_step, "ask", None) == "approval-mode:site-write:create_story"


def test_session_only_form_staging_does_not_pause_the_wizard() -> None:
    fill_step = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "fill",
            "locator": {"label": "Agent name"},
            "value": "test_A",
            "effect_level": "session_only",
        },
        "reason": "Populate a local wizard field before the final Create action",
    }).action
    cascader_step = AgentDecision.model_validate({
        "kind": "action",
        "action": {
            "action": "component",
            "component": {
                "kind": "cascader",
                "semanticTarget": "equipment type",
                "locators": [{"css": ".ant-cascader-picker"}],
                "values": ["A", "B", "C"],
                "expectedText": "C",
            },
            "effect_level": "session_only",
        },
        "reason": "Choose a currently visible equipment path",
    }).action

    assert fill_step is not None
    assert cascader_step is not None
    assert _approval_rule(fill_step, "ask", None) is None
    assert _approval_rule(cascader_step, "ask", None) is None
