from datetime import datetime, timezone

from gui_agent.commerce import evaluate_release_gate
from gui_agent.domain.results import Observation, Status, StepResult


def _step(**updates) -> StepResult:
    now = datetime.now(timezone.utc)
    observation = Observation(
        url="https://example.com", title="Catalog", screenshot="screenshots/before.png"
    )
    values = {
        "index": 1, "action": "navigate", "target_summary": "catalog",
        "status": Status.PASSED, "started_at": now, "ended_at": now,
        "screenshot": "screenshots/after.png", "before": observation,
        "after": observation.model_copy(update={"screenshot": "screenshots/after.png"}),
        "recovery_evidence": {
            "sideEffect": False, "outcome": "known", "decision": "not_needed",
            "retried": False, "attempts": [{"attempt": 1, "outcome": "succeeded"}],
        },
    }
    values.update(updates)
    return StepResult(**values)


def test_release_gate_passes_complete_clean_run() -> None:
    gate = evaluate_release_gate([_step()], pending_resources=[], ledger_entries=[])

    assert gate["passed"] is True
    assert gate["checks"]["evidenceCompleteness"]["ratio"] == 1
    assert gate["checks"]["privacyLeakage"]["count"] == 0
    assert gate["checks"]["duplicateSideEffects"]["unknownSideEffectOutcomes"] == 0


def test_release_gate_blocks_missing_evidence_without_raw_pii_in_finding() -> None:
    gate = evaluate_release_gate(
        [_step(description="customer 13800138000", screenshot=None, after=None)],
        pending_resources=[], ledger_entries=[],
    )

    assert gate["passed"] is False
    assert gate["checks"]["evidenceCompleteness"]["ratio"] < 0.98
    finding = gate["checks"]["privacyLeakage"]["findings"][0]
    assert finding["type"] == "mainland_phone"
    assert len(finding["valueSha256"]) == 64
    assert "13800138000" not in str(gate)


def test_release_gate_blocks_residual_duplicate_and_unknown_side_effect() -> None:
    step = _step(recovery_evidence={
        "sideEffect": True, "outcome": "side_effect_outcome_unknown",
        "decision": "manual_reconciliation_required", "retried": False,
        "attempts": [{"attempt": 1, "outcome": "failed"}],
    })
    ledger = [{"reference": {"sha256": "a" * 64}}, {"reference": {"sha256": "a" * 64}}]
    gate = evaluate_release_gate(
        [step], pending_resources=[{"reference": {"sha256": "a" * 64}}],
        ledger_entries=ledger,
    )

    assert gate["passed"] is False
    assert gate["checks"]["zeroResidual"]["count"] == 1
    duplicate = gate["checks"]["duplicateSideEffects"]
    assert duplicate["duplicateResourceReferences"] == 1
    assert duplicate["unknownSideEffectOutcomes"] == 1


def test_release_gate_counts_unexecuted_plan_steps_and_assertion_pii() -> None:
    gate = evaluate_release_gate(
        [_step()], pending_resources=[], ledger_entries=[], planned_step_count=2,
        additional_payload={"assertion": "contact buyer@example.com"},
    )

    assert gate["passed"] is False
    assert {item["item"] for item in gate["checks"]["evidenceCompleteness"]["missing"]} == {"step_executed"}
    assert gate["checks"]["privacyLeakage"]["findings"][0]["type"] == "email"


def test_release_gate_ignores_non_luhn_technical_identifiers_but_detects_valid_card() -> None:
    technical = _step(description="request uuid 1784799605983206517")
    clean_gate = evaluate_release_gate([technical], pending_resources=[], ledger_entries=[])
    assert clean_gate["checks"]["privacyLeakage"]["count"] == 0

    leaked = _step(description="payment card 4111111111111111")
    blocked_gate = evaluate_release_gate([leaked], pending_resources=[], ledger_entries=[])
    assert blocked_gate["checks"]["privacyLeakage"]["findings"][0]["type"] == "bank_card"
