from datetime import datetime

import pytest

from gui_agent.domain.models import Step, EffectLevel
from gui_agent.domain.results import Observation, PageSemanticSummary, StepResult
from gui_agent.decision.recovery_contract import action_fingerprint
from gui_agent.planning.agent_planner import AgentDecision, AgentScenario, AIAgentPlanner
from gui_agent.planning.form_fast_path import FormFastPath, PlannedFill


def fixture(name="新想定", rid=1):
    ids = [f"ai_{rid+i}" for i in range(4)]
    observation = Observation(url="http://example.test/create", screenshot="current.png",
        semantic_summary=PageSemanticSummary(page_key="create|form", route="/create",
            dialogs=[{"identity": "Create"}], controls=[
                {"runtimeId": key, "role": "textbox", "name": f"字段{i}",
                 "disabled": False, "valueState": "empty"} for i, key in enumerate(ids)]))
    action = Step(action="fill", locator={"runtime_id": ids[0]}, value=name, effect_level="session_only")
    fills = [PlannedFill(runtime_id=key, value=f"{name}-{i}") for i, key in enumerate(ids[1:])]
    scenario = AgentScenario(name="form", goal=f"创建{name}")
    return observation, action, fills, scenario


def passed(action, after, index=1):
    now = datetime.now().astimezone()
    return StepResult(index=index, action="fill", target_summary=action.locator.describe(), status="passed", started_at=now, ended_at=now,
        after=after, progress_assessment="progress", action_fingerprint=action_fingerprint(action),
        verification_evidence={"status": "passed", "facts": ["control_state_verified:value"]})


@pytest.mark.parametrize("name,rid", [("新想定XYZ", 10), ("客户资料临时测试", 100), ("Cesium标题设置", 900)])
def test_one_model_plan_supplies_three_verified_fills(name, rid):
    observation, action, fills, scenario = fixture(name, rid)
    path = FormFastPath()
    path.seed(action, fills, observation, [], scenario)
    history = []
    for i in range(3):
        observation.semantic_summary.controls[i]["valueState"] = "non_empty"
        history.append(passed(action, observation, i+1))
        action = path.take(observation, history, scenario)
        assert action and action.value == f"{name}-{i}"
        assert action.effect_level.value == "session_only"
    assert path.take(observation, history, scenario) is None


@pytest.mark.parametrize("mutation", ["route", "dialog", "disabled", "error", "invalid", "missing", "goal", "failed", "unverified", "unrelated", "stale", "fingerprint"])
def test_changed_or_unverified_state_discards_contract(mutation):
    observation, action, fills, scenario = fixture()
    path = FormFastPath()
    path.seed(action, fills, observation, [], scenario)
    history = [passed(action, observation)]
    if mutation == "route": observation.url += "/other"
    if mutation == "dialog": observation.semantic_summary.dialogs.append({"identity": "Confirm"})
    if mutation == "disabled": observation.semantic_summary.controls[1]["disabled"] = True
    if mutation == "error": observation.page_errors.append("404")
    if mutation == "invalid": observation.semantic_summary.controls[0]["invalid"] = True
    if mutation == "missing": observation.screenshot = None
    if mutation == "goal": scenario.goal = "仅查看，不要创建"
    if mutation == "failed": history[0].status = "error"
    if mutation == "unverified": history[0].verification_evidence = {}
    if mutation == "unrelated": observation.semantic_summary.controls[2]["valueState"] = "non_empty"
    if mutation == "stale": path.deadline = 0
    if mutation == "fingerprint": history[0].action_fingerprint = "different"
    assert path.take(observation, history, scenario) is None
    assert not path.pending


@pytest.mark.parametrize("kind", ["write", "secret", "duplicate", "unknown", "password", "no_form", "canvas", "scope"])
def test_unsafe_or_ambiguous_plans_cannot_seed(kind):
    observation, action, fills, scenario = fixture()
    if kind == "write": action.effect_level = EffectLevel.REVERSIBLE_WRITE
    if kind == "secret": action.value_from_secret = "PASSWORD"
    if kind == "duplicate": fills[0].runtime_id = action.locator.runtime_id
    if kind == "unknown": fills[0].runtime_id = "ai_888"
    if kind == "password": observation.semantic_summary.controls[1]["name"] = "密码"
    if kind == "no_form": observation.semantic_summary.dialogs = []
    if kind == "canvas": observation.semantic_summary.canvas = {"count": 1}
    if kind == "scope": action.locator.scope = {"kind": "dialog"}
    path = FormFastPath()
    path.seed(action, fills, observation, [], scenario)
    assert not path.pending


def test_followup_schema_cannot_express_delete_or_click():
    with pytest.raises(ValueError):
        PlannedFill.model_validate({"runtime_id": "ai_2", "value": "x", "action": "delete"})
    with pytest.raises(ValueError):
        AgentDecision(kind="action", action={"action": "click", "locator": {"runtime_id": "ai_1"}},
            reason="save", form_followups=[{"runtime_id": "ai_2", "value": "x"}])


def test_planner_hits_contract_without_gateway_or_site_adapter():
    observation, action, fills, scenario = fixture()
    planner = object.__new__(AIAgentPlanner)
    planner.scenario = scenario
    planner.form_fast_path = FormFastPath()
    planner.form_fast_path.seed(action, fills, observation, [], scenario)
    result = planner.decide_locally(observation, [passed(action, observation)], 2)
    assert result.protocol == "local" and result.attempt_count == 0
    assert result.normalization_events[0]["model_call_saved"] == 1


def test_real_planner_seeds_followups_from_gateway_then_reduces_calls(tmp_path):
    from gui_agent.planning.test_agent_planner import _model_first_planner, _ModelFirstSitePack
    observation, action, fills, scenario = fixture("不同名称，不用固定脚本", 71)
    decision = AgentDecision(kind="action", action=action, form_followups=fills, reason="fill form")
    planner, calls = _model_first_planner(decision, _ModelFirstSitePack())
    planner.scenario = scenario
    screenshot = tmp_path / "current.png"
    screenshot.write_bytes(b"test screenshot")
    result = planner.decide(observation, [], 1, screenshot_path=screenshot)
    assert result.multimodal and len(calls) == 1
    history = []
    for i in range(3):
        history.append(passed(result.decision.action, observation, i + 1))
        result = planner.decide_locally(observation, history, i + 2)
        assert result.protocol == "local"
    assert len(calls) == 1


def test_kill_switch_discards_pending_contract(monkeypatch):
    from types import SimpleNamespace
    observation, action, fills, scenario = fixture()
    planner = object.__new__(AIAgentPlanner)
    planner.scenario = scenario
    planner.form_fast_path = FormFastPath()
    planner.form_fast_path.seed(action, fills, observation, [], scenario)
    planner.site_pack = SimpleNamespace(required_followup_action=lambda *a: None, next_required_action=lambda *a: None)
    monkeypatch.setenv("GUI_AGENT_FORM_FAST_PATH", "0")
    assert planner.decide_locally(observation, [passed(action, observation)], 2) is None
    assert not planner.form_fast_path.pending
