"""Strict scene-level completion gate.

Step success is intentionally kept separate from business completion.  This
module is the only place that can turn a run into ``goal_status=achieved``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..domain.results import AssertionResult, Status


class CompletionGateResult(BaseModel):
    run_status: Literal["passed", "failed", "incomplete", "blocked"]
    goal_status: Literal["achieved", "incomplete", "not_achieved", "blocked"]
    required_stage_count: int = Field(ge=0)
    completed_stage_count: int = Field(ge=0)
    terminal_assertions_passed: int = Field(ge=0)
    terminal_assertions_required: int = Field(ge=0)
    evidence_ratio: float = Field(ge=0, le=1)
    zero_residual_proven: bool
    stable_replay_required: bool
    stable_replay_passed: bool
    strict_3d_required: bool = False
    strict_3d_passed: bool = True
    completion_proof_mode: Literal["stable_replay", "terminal_state"]
    reasons: list[str] = Field(default_factory=list)


def evaluate_completion_gate(
    *,
    status: Status | str,
    completion_reason: str,
    assertions: list[AssertionResult],
    required_stage_count: int,
    completed_stage_count: int,
    evidence_manifest: dict[str, Any] | None,
    cleanup_report: dict[str, Any] | None,
    commerce_summary: dict[str, Any] | None,
    cleanup_required: bool,
    replay_mode: str,
    stable_replay_required: bool = True,
    safety_violations: int = 0,
    stable_replay_result: bool | None = None,
    strict_3d_required: bool = False,
    strict_3d_passed: bool = True,
    scoped_task: bool = False,
) -> CompletionGateResult:
    """Evaluate all release conditions without inferring business facts."""
    raw_status = status.value if isinstance(status, Status) else str(status)
    terminal_required = len(assertions)
    terminal_passed = sum(item.status == Status.PASSED for item in assertions)
    evidence_ratio = float((evidence_manifest or {}).get("completeness", 0.0) or 0.0)
    zero_residual = _zero_residual(
        cleanup_report, commerce_summary, required=cleanup_required
    )
    stable_replay = (
        stable_replay_result
        if stable_replay_result is not None
        else raw_status == Status.PASSED.value and replay_mode == "stable"
    )
    reasons: list[str] = []

    if not scoped_task and (required_stage_count <= 0 or completed_stage_count < required_stage_count):
        reasons.append("required_stages_incomplete")
    if terminal_required == 0 and not scoped_task:
        reasons.append("terminal_assertions_missing")
    elif terminal_passed != terminal_required:
        reasons.append("terminal_assertions_failed_or_unknown")
    if evidence_ratio < 0.98:
        reasons.append("evidence_below_0_98")
    if not zero_residual:
        reasons.append("zero_residual_unproven")
    if stable_replay_required and not stable_replay:
        reasons.append("stable_replay_required")
    if strict_3d_required and not strict_3d_passed:
        reasons.append("strict_3d_evidence_required")
    if safety_violations:
        reasons.append("safety_violations_present")

    blocked = (not scoped_task) and (raw_status == "blocked" or "blocked" in completion_reason.lower())
    assertion_failure = any(item.status == Status.FAILED for item in assertions)
    all_conditions = not reasons
    if blocked:
        goal_status = "blocked"
    elif assertion_failure or raw_status in {Status.FAILED.value, Status.ISSUES_FOUND.value}:
        goal_status = "not_achieved"
    elif all_conditions and (raw_status == Status.PASSED.value or (scoped_task and completed_stage_count > 0)):
        goal_status = "achieved"
    else:
        goal_status = "incomplete"

    if blocked:
        run_status = "blocked"
    elif goal_status == "not_achieved":
        run_status = "failed"
    elif goal_status == "achieved":
        run_status = "passed"
    else:
        run_status = "incomplete"
    return CompletionGateResult(
        run_status=run_status,
        goal_status=goal_status,
        required_stage_count=max(0, required_stage_count),
        completed_stage_count=max(0, completed_stage_count),
        terminal_assertions_passed=terminal_passed,
        terminal_assertions_required=terminal_required,
        evidence_ratio=round(max(0.0, min(1.0, evidence_ratio)), 4),
        zero_residual_proven=zero_residual,
        stable_replay_required=stable_replay_required,
        stable_replay_passed=stable_replay,
        strict_3d_required=strict_3d_required,
        strict_3d_passed=strict_3d_passed,
        completion_proof_mode=(
            "stable_replay" if stable_replay_required else "terminal_state"
        ),
        reasons=reasons,
    )


def _zero_residual(
    cleanup_report: dict[str, Any] | None,
    commerce_summary: dict[str, Any] | None,
    *,
    required: bool,
) -> bool:
    if not required:
        return True
    if cleanup_report is None and commerce_summary is None:
        return False
    if cleanup_report is not None:
        if cleanup_report.get("status") != "passed":
            return False
        if cleanup_report.get("manualActions"):
            return False
        for item in cleanup_report.get("objects", []):
            if item.get("status") not in {"cleared", "deleted", "passed"} or item.get("verified") is not True:
                return False
    if commerce_summary is not None:
        if commerce_summary.get("zeroResidual") is not True:
            return False
        release_gate = commerce_summary.get("releaseGate")
        if release_gate is not None and release_gate.get("passed") is not True:
            return False
    return True
