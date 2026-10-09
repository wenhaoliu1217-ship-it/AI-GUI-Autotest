from __future__ import annotations

from gui_agent.decision import (
    ChangeKind,
    DecisionRoute,
    ObservationDiff,
    diff_observations,
    route_decision,
)
from gui_agent.domain.results import FailureCategory, Observation, PageSemanticSummary


def observation(
    *,
    url: str = "https://example.test/app",
    route: str = "/app",
    page_key: str = "app|home",
    signature: str = "sig-1",
    dialogs: list[dict[str, str]] | None = None,
    wizard: dict | None = None,
    controls: list[dict] | None = None,
    canvas: dict | None = None,
    console_errors: list[str] | None = None,
) -> Observation:
    return Observation(
        url=url,
        console_errors=console_errors or [],
        semantic_summary=PageSemanticSummary(
            route=route,
            page_key=page_key,
            signature=signature,
            dialogs=dialogs or [],
            wizard=wizard or {},
            controls=controls or [],
            canvas=canvas or {},
        ),
    )


def test_initial_observation_requires_agent() -> None:
    diff = diff_observations(None, observation())
    result = route_decision(diff)

    assert diff.primary is ChangeKind.INITIAL
    assert result.route is DecisionRoute.CALL_MULTIMODAL_AGENT
    assert result.visual_required is True


def test_control_state_change_can_continue_a_matching_contract() -> None:
    before = observation(controls=[{"runtimeId": "ai_1", "valueState": "empty"}])
    after = observation(
        signature="sig-2",
        controls=[{"runtimeId": "ai_1", "valueState": "non_empty"}],
    )
    diff = diff_observations(before, after)
    result = route_decision(
        diff,
        has_active_contract=True,
        contract_matches_page=True,
    )

    assert diff.primary is ChangeKind.CONTROL_STATE
    assert result.route is DecisionRoute.EXECUTE_CONTRACT


def test_new_portal_overlay_invalidates_contract_and_calls_agent() -> None:
    before = observation()
    after = observation(
        signature="sig-2",
        dialogs=[{"identity": "existing simulation model options"}],
    )
    diff = diff_observations(before, after)
    result = route_decision(
        diff,
        has_active_contract=True,
        contract_matches_page=True,
    )

    assert diff.has(ChangeKind.OVERLAY_OPENED)
    assert result.route is DecisionRoute.CALL_MULTIMODAL_AGENT
    assert result.contract_invalidated is True


def test_second_no_progress_attempt_requires_visual_recovery() -> None:
    diff = ObservationDiff(primary=ChangeKind.NONE, changes=[ChangeKind.NONE])
    result = route_decision(diff, no_progress_count=2)

    assert result.route is DecisionRoute.RECOVER
    assert result.visual_required is True


def test_security_failure_blocks_without_guessing() -> None:
    diff = ObservationDiff(primary=ChangeKind.NONE, changes=[ChangeKind.NONE])
    result = route_decision(diff, last_failure=FailureCategory.SECURITY)

    assert result.route is DecisionRoute.BLOCK


def test_canvas_change_requires_multimodal_agent() -> None:
    before = observation(canvas={"count": 1, "signature": "scene-a"})
    after = observation(canvas={"count": 1, "signature": "scene-b"})
    diff = diff_observations(before, after)
    result = route_decision(diff)

    assert diff.primary is ChangeKind.VISUAL_SURFACE
    assert result.route is DecisionRoute.CALL_MULTIMODAL_AGENT
    assert result.visual_required is True


def test_pending_postcondition_can_use_deterministic_verification() -> None:
    diff = ObservationDiff(
        primary=ChangeKind.CONTROL_STATE,
        changes=[ChangeKind.CONTROL_STATE],
    )
    result = route_decision(diff, contract_postcondition_pending=True)

    assert result.route is DecisionRoute.VERIFY_ONLY
