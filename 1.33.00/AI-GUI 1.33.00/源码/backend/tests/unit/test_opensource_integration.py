from __future__ import annotations

from gui_agent.domain.models import ActionType, Locator, Step
from gui_agent.domain.results import Observation
from gui_agent.opensource.integration import (
    adapt_decision,
    create_execution_summary,
    execution_observation_context,
    execution_profiles_payload,
    record_decision,
    record_step,
)
from gui_agent.planning.agent_planner import AgentDecision, AgentScenario, VisualRequest, _agent_prompt


def _observation() -> Observation:
    return Observation(
        url="https://example.test/dashboard",
        title="Dashboard",
        dom_summary=["button | role=button | text=Open details", "input | label=Search"],
        accessibility_summary='- button "Open details"\n- textbox "Search"',
    )


def test_product_catalog_exposes_runtime_profiles_for_all_selected_reference_projects() -> None:
    profiles = {item["id"]: item for item in execution_profiles_payload()}

    assert {"stagehand", "browser-use", "ui-tars", "playwright-cli", "openadapt"} <= profiles.keys()
    assert profiles["stagehand"]["status"] == "product_runtime_integrated"
    assert profiles["openadapt"]["actionPolicy"].startswith("checkpoint")


def test_stagehand_adapter_accepts_observed_locator_and_blocks_ungrounded_action() -> None:
    observation = _observation()
    grounded = AgentDecision(
        kind="action",
        action=Step(action=ActionType.CLICK, locator=Locator(role="button", name="Open details")),
        reason="当前观察到按钮",
        progress_assessment="progress",
    )

    accepted, accepted_evidence = adapt_decision("stagehand", grounded, observation, [], visual_enabled=False)

    assert accepted.kind == "action"
    assert accepted_evidence["status"] == "applied"
    assert accepted_evidence["candidateCount"] == 2
    assert accepted_evidence["executionBoundary"] == "product_guarded_runner"

    ungrounded_step = grounded.action.model_copy(update={"locator": None})
    ungrounded = grounded.model_copy(update={"action": ungrounded_step})
    blocked, blocked_evidence = adapt_decision("stagehand", ungrounded, observation, [], visual_enabled=False)

    assert blocked.kind == "blocked"
    assert blocked.action is None
    assert blocked_evidence["status"] == "blocked"

    unknown = grounded.model_copy(update={
        "action": grounded.action.model_copy(update={"locator": Locator(role="button", name="Delete account")})
    })
    unknown_blocked, unknown_evidence = adapt_decision("stagehand", unknown, observation, [], visual_enabled=False)

    assert unknown_blocked.kind == "blocked"
    assert unknown_evidence["status"] == "blocked"


def test_ui_tars_requires_authorized_visual_fallback_and_openadapt_records_checkpoints() -> None:
    observation = _observation()
    visual = AgentDecision(
        kind="visual",
        visual_request=VisualRequest(target="地图按钮", trigger_reason="DOM 没有足够事实"),
        reason="需要截图确认",
        progress_assessment="unknown",
    )

    blocked, evidence = adapt_decision("ui-tars", visual, observation, [], visual_enabled=False)

    assert blocked.kind == "blocked"
    assert evidence["status"] == "blocked"
    assert "截图" in blocked.reason

    summary = create_execution_summary("openadapt")
    record_decision(summary, {"status": "applied"})
    record_step(summary, checkpoint=True)

    assert execution_observation_context("openadapt", observation, [])["checkpoint"]["resumePolicy"] == "reobserve_before_resume_and_revalidate_writes"
    assert summary["decisionCount"] == 1
    assert summary["stepCount"] == 1
    assert summary["checkpointCount"] == 1


def test_selected_strategy_is_present_in_the_actual_agent_prompt_context() -> None:
    prompt = _agent_prompt(
        scenario=AgentScenario(name="浏览测试", goal="检查当前页面"),
        base_url="https://example.test/",
        observation=_observation(),
        history=[],
        call_index=1,
        visual_enabled=False,
        execution_provider="browser-use",
    )

    assert "Browser-use state adapter" in prompt
    assert "one_action_per_observation" in prompt
    assert '"nextActionBoundary"' in prompt
