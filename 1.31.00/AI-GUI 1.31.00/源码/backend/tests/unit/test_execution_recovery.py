import pytest
from playwright.sync_api import Error as PlaywrightError

from gui_agent.domain.models import Step
from gui_agent.execution.recovery import (
    HttpExecutionError,
    SideEffectOutcomeUnknown,
    execute_with_recovery,
)


@pytest.mark.parametrize("error", [
    HttpExecutionError(429, "https://example.test"),
    HttpExecutionError(503, "https://example.test"),
    PlaywrightError("net::ERR_INTERNET_DISCONNECTED"),
])
def test_read_action_retries_bounded_recoverable_failures(error: Exception) -> None:
    calls = 0
    waits: list[int] = []

    def operation() -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return {"ok": True}

    result, evidence = execute_with_recovery(
        Step(action="navigate", target="/catalog"), operation, wait=waits.append
    )

    assert result == {"ok": True}
    assert calls == 2
    assert waits == [250]
    assert evidence["retried"] is True
    assert evidence["attempts"][0]["failureClass"] in {
        "http_429", "http_5xx", "network_disconnected"
    }


def _write_step(**updates) -> Step:
    metadata = {
        "action": "submit_order",
        "targetKind": "orderId",
        "targetRef": "resource:E2E_ORDER_RECOVERY",
        "beforeState": "draft",
        "idempotencyKeyRef": "secret:E2E_ORDER_IDEMPOTENCY",
        "e2eOwned": True,
        "stateProbe": {
            "domain": "order", "url": "/state/${RUN_ID}", "jsonPath": "state",
            "expectedState": "pending_payment",
        },
        **updates,
    }
    return Step(
        action="click", locator={"text": "提交订单"}, description="提交订单",
        commerce=metadata,
    )


def test_applied_write_is_not_replayed_after_unknown_response() -> None:
    calls = 0

    def operation() -> dict:
        nonlocal calls
        calls += 1
        raise PlaywrightError("net::ERR_CONNECTION_RESET")

    result, evidence = execute_with_recovery(
        _write_step(), operation, wait=lambda _: None,
        probe=lambda: {"verified": True, "state": "pending_payment"},
    )

    assert result == {}
    assert calls == 1
    assert evidence["decision"] == "original_action_confirmed_applied"
    assert evidence["retried"] is False
    assert evidence["noReplayReason"] == "backend_confirmed_applied"
    assert len(evidence["idempotencyKeySha256"]) == 64


def test_write_replays_once_only_when_probe_confirms_before_state() -> None:
    calls = 0

    def operation() -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HttpExecutionError(503, "https://example.test/submit")
        return {"submitted": True}

    result, evidence = execute_with_recovery(
        _write_step(), operation, wait=lambda _: None,
        probe=lambda: {"verified": True, "state": "draft"},
    )

    assert result == {"submitted": True}
    assert calls == 2
    assert evidence["retried"] is True


@pytest.mark.parametrize("updates,reason", [
    ({"idempotencyKeyRef": None}, "missing_idempotency_key"),
    ({"stateProbe": None}, "missing_backend_state_probe"),
])
def test_unknown_write_without_proof_requires_manual_reconciliation(updates, reason) -> None:
    with pytest.raises(SideEffectOutcomeUnknown) as caught:
        execute_with_recovery(
            _write_step(**updates),
            lambda: (_ for _ in ()).throw(PlaywrightError("net::ERR_CONNECTION_RESET")),
            wait=lambda _: None,
        )

    assert caught.value.evidence["outcome"] == "side_effect_outcome_unknown"
    assert caught.value.evidence["decision"] == "manual_reconciliation_required"
    assert caught.value.evidence["noReplayReason"] == reason
    assert caught.value.evidence["retried"] is False


def test_read_session_crash_rebuilds_before_retry() -> None:
    calls = 0
    rebuilt = 0

    def operation() -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PlaywrightError("Target closed: page crashed")
        return {"ok": True}

    def recover() -> None:
        nonlocal rebuilt
        rebuilt += 1

    _, evidence = execute_with_recovery(
        Step(action="reload"), operation, wait=lambda _: None, recover_session=recover
    )

    assert rebuilt == 1
    assert evidence["attempts"][0]["sessionRebuilt"] is True
