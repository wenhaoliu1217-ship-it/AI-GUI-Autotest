from datetime import datetime, timezone
from threading import Lock

import pytest

from gui_agent.commerce import (
    CallbackObservation,
    CommerceAssuranceError,
    InventoryAttempt,
    evaluate_callback_idempotency,
    run_two_session_inventory_barrier,
)


def _time():
    return datetime.now(timezone.utc).isoformat()


def test_two_session_barrier_has_one_winner_and_no_oversell() -> None:
    stock = {"value": 1}
    lock = Lock()

    def action(session, digest):
        def run():
            started = _time()
            with lock:
                outcome = "reserved" if stock["value"] else "rejected"
                if outcome == "reserved":
                    stock["value"] -= 1
            return InventoryAttempt(
                session=session, outcome=outcome, requestSha256=digest,
                startedAt=started, endedAt=_time(),
            )
        return run

    evidence, result = run_two_session_inventory_barrier(
        action("A", "a" * 64), action("B", "b" * 64),
        stock_before=1, stock_after=lambda: stock["value"],
    )
    assert result["noOversell"] is True
    assert result["winnerCount"] == 1
    assert {item.outcome for item in evidence.attempts} == {"reserved", "rejected"}


def test_payment_callback_duplicate_is_ignored_exactly_once() -> None:
    observations = [
        CallbackObservation(
            kind="payment", idempotencyKeySha256="a" * 64, payloadSha256="b" * 64,
            result="applied", observedAt=_time(),
        ),
        CallbackObservation(
            kind="payment", idempotencyKeySha256="a" * 64, payloadSha256="b" * 64,
            result="duplicate_ignored", observedAt=_time(),
        ),
    ]
    result = evaluate_callback_idempotency(observations)
    assert result["duplicateSideEffects"] == 0

    with pytest.raises(CommerceAssuranceError, match="重复记账"):
        evaluate_callback_idempotency([
            observations[0], observations[1].model_copy(update={"result": "applied"})
        ])
