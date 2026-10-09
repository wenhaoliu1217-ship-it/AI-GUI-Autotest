from datetime import datetime

from gui_agent.decision.exploration_map import build_exploration_map
from gui_agent.decision.recovery_contract import (
    action_fingerprint,
    build_recovery_checkpoint,
    derive_recovery_contract,
    is_persistent_action,
)
from gui_agent.domain.models import ActionType, EffectLevel, Locator, Step
from gui_agent.domain.results import FailureCategory, Observation, PageSemanticSummary, Status, StepResult
from gui_agent.execution.agent_evaluation import evaluate_agent_run
from gui_agent.planning.agent_planner import AgentDecision, _recovery_contract_violation


def _observation(signature: str = "sig-1", route: str = "/wizard") -> Observation:
    return Observation(
        url=f"https://example.test{route}",
        title="Wizard",
        semantic_summary=PageSemanticSummary(
            page_key=f"{route}|Wizard",
            route=route,
            heading="Wizard",
            signature=signature,
        ),
    )


def _result(step: Step, *, status: Status = Status.ERROR, phase: str = "pre_action") -> StepResult:
    now = datetime.now().astimezone()
    return StepResult(
        index=1,
        action=step.action.value,
        target_summary=step.locator.describe() if step.locator else step.target or "",
        status=status,
        started_at=now,
        ended_at=now,
        failure_category=FailureCategory.LOCATOR,
        failure_phase=phase,
        before=_observation(),
        after=_observation(),
        progress_assessment="no_progress",
        action_fingerprint=action_fingerprint(step),
    )


def test_same_failed_grounding_is_forbidden_on_unchanged_page() -> None:
    step = Step(action=ActionType.CLICK, locator=Locator(role="combobox", name="Model"))
    contract = derive_recovery_contract(_observation(), [_result(step)])

    assert contract.active is True
    assert contract.attempt == 1
    assert contract.prohibited_action_fingerprints == [action_fingerprint(step)]
    violation = _recovery_contract_violation(
        AgentDecision(kind="action", action=step, reason="Retry"),
        contract.model_dump(mode="json"),
    )
    assert violation is not None
    assert "already failed" in violation


def test_changed_page_does_not_carry_stale_failed_grounding() -> None:
    step = Step(action=ActionType.CLICK, locator=Locator(role="button", name="Next"))
    contract = derive_recovery_contract(_observation("sig-2"), [_result(step)])

    assert contract.active is False
    assert contract.prohibited_action_fingerprints == []


def test_persistent_write_checkpoint_disables_automatic_replay() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="Create scenario"),
        action_category="create",
        effect_level=EffectLevel.REVERSIBLE_WRITE,
        object_type="scenario",
        business_object_name="E2E_scenario_test_A",
    )
    checkpoint = build_recovery_checkpoint(
        index=3,
        step=step,
        before=_observation(),
        after=_observation("sig-2", "/scenarios"),
        status=Status.ERROR,
        failure_phase="verification",
    )

    assert checkpoint["persistentAction"] is True
    assert checkpoint["automaticReplayAllowed"] is False
    assert checkpoint["rollback"]["allowed"] is False


def test_save_button_is_persistent_even_when_model_omits_effect_metadata() -> None:
    step = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="保存"),
        description="点击动力学面板的保存按钮",
    )

    assert is_persistent_action(step) is True


def test_unknown_persistent_outcome_cannot_bypass_no_replay_with_new_locator() -> None:
    original = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="Create scenario"),
        action_category="create",
        effect_level=EffectLevel.REVERSIBLE_WRITE,
        object_type="scenario",
        business_object_name="E2E_scenario_test_A",
    )
    failed = _result(original, phase="verification")
    failed.recovery_evidence = {"outcome": "side_effect_outcome_unknown"}
    contract = derive_recovery_contract(_observation(), [failed])
    alternate = original.model_copy(update={
        "locator": Locator(text="Create scenario"),
    })

    violation = _recovery_contract_violation(
        AgentDecision(kind="action", action=alternate, reason="Try another locator"),
        contract.model_dump(mode="json"),
    )

    assert contract.persistent_outcome_unknown is True
    assert violation is not None
    assert "persistent write outcome is unknown" in violation.lower()


def test_exploration_map_keeps_observed_transition_and_failure() -> None:
    step = Step(action=ActionType.CLICK, locator=Locator(role="button", name="Next"))
    result = _result(step)
    result.after = _observation("sig-2", "/wizard/2")
    exploration = build_exploration_map(result.after, [result])

    assert exploration["scope"] == "current_run_only"
    assert exploration["visitedStateCount"] == 2
    assert exploration["transitions"][0]["progress"] == "no_progress"
    current = next(item for item in exploration["nodes"] if item["stateKey"] == exploration["currentStateKey"])
    assert current["failureCount"] == 1


def test_agent_evaluation_does_not_accept_incomplete_run() -> None:
    metrics = evaluate_agent_run(
        status=Status.INCOMPLETE,
        goal_status="incomplete",
        steps=[],
        model_calls=[],
        evidence_manifest={"completeness": 1.0},
        visited_state_count=2,
    )

    assert metrics["taskSuccess"] is False
    assert metrics["acceptanceLevel"] == "not_accepted"
    assert metrics["visitedStateCount"] == 2
