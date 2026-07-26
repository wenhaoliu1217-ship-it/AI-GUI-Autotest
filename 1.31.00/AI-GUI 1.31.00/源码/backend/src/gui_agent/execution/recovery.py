"""Bounded execution recovery that never guesses about commerce side effects."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, TypeVar

from playwright.sync_api import Error as PlaywrightError

from ..commerce.models import CommerceAction
from ..domain.models import ActionType, Step


T = TypeVar("T")

_READ_ACTIONS = {
    CommerceAction.BROWSE, CommerceAction.SEARCH, CommerceAction.FILTER,
    CommerceAction.SORT, CommerceAction.PAGINATE, CommerceAction.VIEW_PRODUCT,
    CommerceAction.VIEW_ACCOUNT_STRUCTURE, CommerceAction.VIEW_HELP,
    CommerceAction.CHANGE_REGION, CommerceAction.DOWNLOAD_INVOICE,
}
_READONLY_BROWSER_ACTIONS = {
    ActionType.NAVIGATE, ActionType.WAIT_FOR, ActionType.SCREENSHOT,
    ActionType.HOVER, ActionType.SCROLL, ActionType.BACK, ActionType.RELOAD,
    ActionType.DOWNLOAD,
}


@dataclass(frozen=True)
class HttpExecutionError(PlaywrightError):
    status: int
    url: str

    def __str__(self) -> str:
        return f"页面返回 HTTP {self.status}：{self.url}"


class RecoveryError(RuntimeError):
    def __init__(self, message: str, evidence: dict):
        super().__init__(message)
        self.evidence = evidence


class SideEffectOutcomeUnknown(RecoveryError):
    pass


def execute_with_recovery(
    step: Step,
    execute: Callable[[], T],
    *,
    wait: Callable[[int], None],
    probe: Callable[[], dict] | None = None,
    recover_session: Callable[[], None] | None = None,
    max_read_attempts: int = 3,
) -> tuple[T, dict]:
    """Execute with bounded retries and proof-first side-effect handling."""
    attempts: list[dict] = []
    replayed_side_effect = False
    while True:
        attempt = len(attempts) + 1
        try:
            value = execute()
            attempts.append({"attempt": attempt, "outcome": "succeeded"})
            return value, _evidence(
                step, attempts, "succeeded_after_retry" if attempt > 1 else "not_needed",
                retried=attempt > 1,
            )
        except Exception as exc:
            failure = classify_failure(exc)
            attempts.append({
                "attempt": attempt,
                "outcome": "failed",
                "failureClass": failure,
                "httpStatus": exc.status if isinstance(exc, HttpExecutionError) else None,
            })
            if failure == "non_recoverable":
                raise

            if _is_read_only(step):
                if attempt >= max_read_attempts:
                    evidence = _evidence(step, attempts, "read_retry_exhausted", retried=attempt > 1)
                    raise RecoveryError("只读动作安全重试已耗尽", evidence) from exc
                backoff_ms = 250 * (2 ** (attempt - 1))
                attempts[-1].update({"retried": True, "backoffMs": backoff_ms})
                if failure == "session_crashed":
                    if recover_session is None:
                        evidence = _evidence(step, attempts, "session_recovery_unavailable", retried=False)
                        raise RecoveryError("浏览器会话崩溃且无法重建", evidence) from exc
                    recover_session()
                    attempts[-1]["sessionRebuilt"] = True
                wait(backoff_ms)
                continue

            if step.commerce is None:
                raise

            metadata = step.commerce
            idem_hash = _idempotency_hash(metadata.idempotency_key_ref)
            if not idem_hash or probe is None:
                reason = "missing_idempotency_key" if not idem_hash else "missing_backend_state_probe"
                evidence = _evidence(
                    step, attempts, "manual_reconciliation_required", retried=False,
                    outcome="side_effect_outcome_unknown", no_replay_reason=reason,
                )
                raise SideEffectOutcomeUnknown(
                    "副作用动作结果不明，禁止自动重放；需要人工核对后台状态", evidence
                ) from exc

            try:
                state = probe()
            except Exception as probe_exc:
                evidence = _evidence(
                    step, attempts, "manual_reconciliation_required", retried=False,
                    outcome="side_effect_outcome_unknown", no_replay_reason="backend_probe_failed",
                    probe={"verified": False, "errorClass": type(probe_exc).__name__},
                )
                raise SideEffectOutcomeUnknown(
                    "副作用动作结果不明且后台状态探针失败，禁止自动重放", evidence
                ) from exc

            if state.get("state") == metadata.state_probe.expected_state:
                evidence = _evidence(
                    step, attempts, "original_action_confirmed_applied", retried=False,
                    outcome="recovered_without_replay", probe=state,
                    no_replay_reason="backend_confirmed_applied",
                )
                return {}, evidence  # type: ignore[return-value]

            if (
                not replayed_side_effect
                and metadata.before_state
                and state.get("state") == metadata.before_state
            ):
                backoff_ms = 250
                attempts[-1].update({"retried": True, "backoffMs": backoff_ms, "probe": state})
                replayed_side_effect = True
                if failure == "session_crashed":
                    if recover_session is None:
                        evidence = _evidence(
                            step, attempts, "manual_reconciliation_required", retried=False,
                            outcome="side_effect_outcome_unknown", no_replay_reason="session_recovery_unavailable",
                            probe=state,
                        )
                        raise SideEffectOutcomeUnknown("页面会话无法重建，禁止重放副作用动作", evidence) from exc
                    recover_session()
                    attempts[-1]["sessionRebuilt"] = True
                wait(backoff_ms)
                continue

            reason = "side_effect_retry_failed" if replayed_side_effect else "backend_state_ambiguous"
            evidence = _evidence(
                step, attempts, "manual_reconciliation_required", retried=replayed_side_effect,
                outcome="side_effect_outcome_unknown", no_replay_reason=reason, probe=state,
            )
            raise SideEffectOutcomeUnknown(
                "副作用动作结果不明，后台状态不能证明可安全重放；需要人工核对", evidence
            ) from exc


def classify_failure(exc: Exception) -> str:
    if isinstance(exc, HttpExecutionError):
        if exc.status == 429:
            return "http_429"
        if 500 <= exc.status <= 599:
            return "http_5xx"
        return "non_recoverable"
    if isinstance(exc, PlaywrightError):
        message = str(exc).lower()
        if any(token in message for token in (
            "err_internet_disconnected", "err_network_changed", "err_connection_reset",
            "err_connection_closed", "err_empty_response", "err_name_not_resolved", "networkerror",
        )):
            return "network_disconnected"
        if any(token in message for token in ("target closed", "page crashed", "browser has been closed")):
            return "session_crashed"
    return "non_recoverable"


def _is_read_only(step: Step) -> bool:
    if step.commerce is not None:
        return step.commerce.action in _READ_ACTIONS
    return step.action in _READONLY_BROWSER_ACTIONS


def _idempotency_hash(reference: str | None) -> str | None:
    if not reference:
        return None
    return hashlib.sha256(reference.encode("utf-8")).hexdigest()


def _evidence(
    step: Step,
    attempts: list[dict],
    decision: str,
    *,
    retried: bool,
    outcome: str = "known",
    no_replay_reason: str | None = None,
    probe: dict | None = None,
) -> dict:
    return {
        "policy": "bounded_read_retry_proof_first_side_effect_recovery",
        "sideEffect": step.commerce is not None and step.commerce.action not in _READ_ACTIONS,
        "outcome": outcome,
        "decision": decision,
        "retried": retried,
        "attempts": attempts,
        "idempotencyKeySha256": _idempotency_hash(
            step.commerce.idempotency_key_ref if step.commerce else None
        ),
        "backendProbe": probe,
        "noReplayReason": no_replay_reason,
    }
