"""Objective run metrics for the stepwise Agent."""

from __future__ import annotations

from typing import Any

from ..domain.results import ModelCallRecord, Status, StepResult


def evaluate_agent_run(
    *,
    status: Status,
    goal_status: str,
    steps: list[StepResult],
    model_calls: list[ModelCallRecord],
    evidence_manifest: dict[str, Any] | None,
    clarification_count: int = 0,
    visited_state_count: int = 0,
) -> dict[str, Any]:
    failed = [item for item in steps if item.status == Status.ERROR]
    no_progress = [item for item in steps if item.progress_assessment == "no_progress"]
    recovery_attempts = [item for item in steps if item.recovery_attempt is not None]
    recovered = [
        item for item in recovery_attempts
        if item.status == Status.PASSED and item.progress_assessment == "progress"
    ]
    external_calls = [item for item in model_calls if item.protocol != "local"]
    multimodal_calls = [item for item in external_calls if item.multimodal]
    completeness = float((evidence_manifest or {}).get("completeness") or 0.0)
    return {
        "schemaVersion": 1,
        "taskSuccess": status == Status.PASSED and goal_status == "achieved",
        "executedStepCount": len(steps),
        "failedStepCount": len(failed),
        "noProgressStepCount": len(no_progress),
        "recoveryAttemptCount": len(recovery_attempts),
        "recoverySuccessCount": len(recovered),
        "recoverySuccessRate": round(len(recovered) / len(recovery_attempts), 4) if recovery_attempts else None,
        "modelCallCount": len(external_calls),
        "multimodalDecisionCount": len(multimodal_calls),
        "multimodalCoverage": round(len(multimodal_calls) / len(external_calls), 4) if external_calls else None,
        "evidenceCompleteness": completeness,
        "visitedStateCount": visited_state_count,
        "manualClarificationCount": clarification_count,
        "manualClarificationRequired": clarification_count > 0,
        "acceptanceLevel": (
            "accepted" if status == Status.PASSED and goal_status == "achieved" and completeness >= 0.98
            else "not_accepted"
        ),
    }
