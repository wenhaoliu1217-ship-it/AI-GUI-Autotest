"""Run-local recovery constraints derived from current evidence.

The contract is deliberately advisory for business intent and mandatory for
replay safety: a failed action on an unchanged page cannot be proposed again
with the same grounding, and an uncertain persistent write cannot be replayed.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

from ..domain.models import ActionType, Step
from ..domain.results import Observation, Status, StepResult


class RecoveryContract(BaseModel):
    active: bool = False
    page_state_key: str = ""
    attempt: int = Field(default=0, ge=0)
    failure_categories: list[str] = Field(default_factory=list)
    prohibited_action_fingerprints: list[str] = Field(default_factory=list)
    required_strategy_change: list[str] = Field(default_factory=list)
    safe_strategies: list[str] = Field(default_factory=list)
    replay_policy: str = "fresh_agent_decision_required"
    rollback_policy: str = "not_required"
    persistent_outcome_unknown: bool = False
    must_rollback_or_clarify: bool = False


def action_fingerprint(step: Step) -> str:
    """Return a value-safe identity for repeated-action rejection."""
    target = step.locator.describe() if step.locator is not None else (step.target or "")
    component = step.component.kind if step.component is not None else ""
    raw = "|".join((step.action.value, step.execution_mode.value, target, component))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def observation_state_key(observation: Observation | None) -> str:
    if observation is None:
        return ""
    summary = observation.semantic_summary
    if summary is None:
        return f"{observation.url}|{observation.title}"
    return "|".join((summary.page_key, summary.route, summary.signature))


def derive_recovery_contract(
    observation: Observation,
    history: list[StepResult],
) -> RecoveryContract:
    current_key = observation_state_key(observation)
    failures: list[StepResult] = []
    for result in reversed(history[-12:]):
        if result.status not in {Status.ERROR, Status.INCOMPLETE} and result.progress_assessment != "no_progress":
            if failures:
                break
            continue
        result_key = observation_state_key(result.after)
        if result_key and current_key and result_key != current_key:
            if failures:
                break
            continue
        failures.append(result)

    if not failures:
        return RecoveryContract(page_state_key=current_key)

    categories = list(dict.fromkeys(
        result.failure_category.value if result.failure_category is not None else "unknown"
        for result in failures
    ))
    fingerprints = list(dict.fromkeys(
        result.action_fingerprint
        for result in failures
        if result.action_fingerprint
    ))
    uncertain_write = any(
        result.progress_assessment == "pending_business_verification"
        or bool((result.recovery_evidence or {}).get("outcome") == "side_effect_outcome_unknown")
        for result in failures
    )
    pre_action_only = all(result.failure_phase == "pre_action" for result in failures)
    safe_strategies = _safe_strategies(categories, pre_action_only, uncertain_write)
    return RecoveryContract(
        active=True,
        page_state_key=current_key,
        attempt=len(failures),
        failure_categories=categories,
        prohibited_action_fingerprints=fingerprints,
        required_strategy_change=[
            "use a newly observed runtime id or a different semantic locator",
            "change the interaction level, for example open the control before selecting an option",
            "verify overlay, loading, validation, or route state before another action",
        ],
        safe_strategies=safe_strategies,
        replay_policy=(
            "verify_business_state_only_no_replay"
            if uncertain_write else "same_action_and_grounding_forbidden_on_unchanged_state"
        ),
        rollback_policy=(
            "agent_may_use_browser_back_or_wizard_previous_after_current_page_verification"
            if pre_action_only and not uncertain_write else "do_not_rollback_until_business_outcome_is_known"
        ),
        persistent_outcome_unknown=uncertain_write,
        must_rollback_or_clarify=len(failures) >= 2,
    )


def build_recovery_checkpoint(
    *,
    index: int,
    step: Step,
    before: Observation | None,
    after: Observation | None,
    status: Status,
    failure_phase: str | None = None,
    verification_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    persistent = is_persistent_action(step)
    independently_verified = bool(
        verification_evidence
        and verification_evidence.get("status") == "passed"
    )
    return {
        "schemaVersion": 1,
        "stepIndex": index,
        "action": step.action.value,
        "actionFingerprint": action_fingerprint(step),
        "status": status.value,
        "failurePhase": failure_phase,
        "beforeStateKey": observation_state_key(before),
        "afterStateKey": observation_state_key(after),
        "beforeUrl": before.url if before is not None else None,
        "afterUrl": after.url if after is not None else None,
        "persistentAction": persistent,
        "independentlyVerified": independently_verified,
        "automaticReplayAllowed": not persistent and failure_phase != "verification",
        "rollback": {
            "allowed": not persistent,
            "mechanism": "fresh_agent_decision_browser_back_or_wizard_previous" if not persistent else None,
            "reason": (
                "The action is non-persistent and may be rolled back after current-page verification"
                if not persistent else "Persistent outcomes require independent verification before any replay or rollback"
            ),
        },
    }


def is_persistent_action(step: Step) -> bool:
    if _looks_like_creation(step) or _looks_like_persistent_write(step) or step.action_category is not None:
        return True
    if step.effect_level is not None and step.effect_level.value not in {
        "read_only", "session_only", "isolated_local_write",
    }:
        return True
    return step.action in {ActionType.UPLOAD, ActionType.UPLOAD_FILE}


def _looks_like_persistent_write(step: Step) -> bool:
    """Fail safe when a model omits effect metadata from an obvious write control."""

    if step.action not in {ActionType.CLICK, ActionType.PRESS, ActionType.COMPONENT}:
        return False
    values = [str(step.description or "").lower()]
    if step.locator is not None:
        values.extend(str(value or "").lower() for value in (
            step.locator.name,
            step.locator.text,
            step.locator.label,
        ))
    text = " ".join(values)
    return any(token in text for token in (
        "保存", "提交", "确认更新", "确认修改", "save", "submit", "confirm update",
    ))


def _looks_like_creation(step: Step) -> bool:
    if str(step.action_category or "").strip().lower() == "create":
        return True
    if "create" in str(step.effect_kind or "").strip().lower():
        return True
    if step.action not in {ActionType.CLICK, ActionType.PRESS, ActionType.COMPONENT}:
        return False
    values = [str(step.description or "").lower()]
    if step.locator is not None:
        values.extend(str(value or "").lower() for value in (
            step.locator.name,
            step.locator.text,
            step.locator.label,
        ))
    text = " ".join(values)
    return any(token in text for token in (
        "confirm create", "create now", "submit create", "create scenario",
        "save scenario", "final create", "实际创建", "最终创建", "创建想定", "保存想定",
    ))


def _safe_strategies(
    categories: list[str],
    pre_action_only: bool,
    uncertain_write: bool,
) -> list[str]:
    if uncertain_write:
        return [
            "inspect the current page and backend-visible business state",
            "locate the created or saved object by exact current-run name",
            "ask for manual reconciliation when the outcome remains ambiguous",
        ]
    strategies = [
        "re-observe the current screenshot, DOM, accessibility tree, dialogs, and components",
        "ground a different visible target from the current observation",
    ]
    if any(category in {"locator", "timeout"} for category in categories):
        strategies.extend([
            "open a closed selector or overlay, then observe its newly visible options",
            "use current runtime-id grounding or multimodal disambiguation",
        ])
    if pre_action_only:
        strategies.append("return to the previous safe wizard/page state and choose another path")
    return strategies
