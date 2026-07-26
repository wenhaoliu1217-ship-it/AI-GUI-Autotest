"""Machine-verifiable commerce release gates for evidence and side effects."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..domain.results import StepResult


_PII_PATTERNS = {
    "mainland_phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "mainland_id": re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    "bank_card": re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    "email": re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"),
}


def evaluate_release_gate(
    steps: list["StepResult"],
    *,
    pending_resources: list[dict],
    ledger_entries: list[dict],
    minimum_evidence_ratio: float = 0.98,
    planned_step_count: int | None = None,
    additional_payload: Any = None,
) -> dict:
    evidence_checks: list[dict] = []
    for step in steps:
        _check(evidence_checks, step.index, "before_observation", step.before is not None)
        _check(evidence_checks, step.index, "after_observation", step.after is not None)
        _check(evidence_checks, step.index, "before_screenshot", bool(step.before and step.before.screenshot))
        _check(evidence_checks, step.index, "after_screenshot", bool(step.screenshot))
        _check(evidence_checks, step.index, "recovery_decision", step.recovery_evidence is not None)
        if step.action in {"upload_file", "download"}:
            _check(evidence_checks, step.index, "file_sha256", bool(step.file_evidence and step.file_evidence.get("sha256")))
        if step.commerce_state_evidence is not None:
            _check(evidence_checks, step.index, "backend_state_consistency", bool(step.commerce_state_evidence.get("consistent")))
    executed_indexes = {step.index for step in steps}
    for index in range(1, (planned_step_count or len(steps)) + 1):
        if index not in executed_indexes:
            _check(evidence_checks, index, "step_executed", False)

    passed_evidence = sum(item["passed"] for item in evidence_checks)
    ratio = passed_evidence / len(evidence_checks) if evidence_checks else 0.0
    leaks = _privacy_findings({
        "steps": [item.model_dump(mode="json", exclude_none=True) for item in steps],
        "additional": additional_payload,
    })
    references = [
        (item.get("reference") or {}).get("sha256")
        for item in ledger_entries
        if (item.get("reference") or {}).get("sha256")
    ]
    duplicate_references = len(references) - len(set(references))
    unknown_side_effects = sum(
        bool(step.recovery_evidence)
        and step.recovery_evidence.get("sideEffect") is True
        and step.recovery_evidence.get("outcome") == "side_effect_outcome_unknown"
        for step in steps
    )
    checks = {
        "evidenceCompleteness": {
            "passed": ratio >= minimum_evidence_ratio,
            "ratio": round(ratio, 6),
            "minimum": minimum_evidence_ratio,
            "passedItems": passed_evidence,
            "totalItems": len(evidence_checks),
            "missing": [item for item in evidence_checks if not item["passed"]],
        },
        "privacyLeakage": {"passed": not leaks, "count": len(leaks), "findings": leaks},
        "zeroResidual": {"passed": not pending_resources, "count": len(pending_resources)},
        "duplicateSideEffects": {
            "passed": duplicate_references == 0 and unknown_side_effects == 0,
            "duplicateResourceReferences": duplicate_references,
            "unknownSideEffectOutcomes": unknown_side_effects,
        },
    }
    return {
        "passed": all(item["passed"] for item in checks.values()),
        "policy": "commerce_release_gate_v1",
        "checks": checks,
    }


def _check(items: list[dict], step_index: int, name: str, passed: bool) -> None:
    items.append({"stepIndex": step_index, "item": name, "passed": bool(passed)})


def _privacy_findings(payload: Any, path: str = "steps") -> list[dict]:
    findings: list[dict] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"sha256", "responsesha256", "probeurlsha256", "idempotencykeysha256"}:
                continue
            findings.extend(_privacy_findings(value, f"{path}.{key}"))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            findings.extend(_privacy_findings(value, f"{path}[{index}]"))
    elif isinstance(payload, str) and "<redacted" not in payload.lower():
        for kind, pattern in _PII_PATTERNS.items():
            for match in pattern.finditer(payload):
                findings.append({
                    "type": kind,
                    "location": path,
                    "valueSha256": hashlib.sha256(match.group(0).encode("utf-8")).hexdigest(),
                })
    return findings
