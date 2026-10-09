"""Evidence-backed Cesium ion coverage and release-gate aggregation.

This module only reads persisted ``run.json`` files.  A run without an explicit
scenario id is reported as unassigned and never credited to a catalog case.
That keeps the coverage report useful for acceptance planning without turning
an unrelated or partial run into a false business result.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .catalog import SCENARIOS, scenario_catalog
from .policy import is_cesium_target


REQUIRED_REPETITIONS = 5
EVIDENCE_THRESHOLD = 0.98

MODULES = (
    ("authentication_navigation", "认证与导航", tuple(f"C{i:02d}" for i in range(1, 10))),
    ("asset_ingestion", "资产导入与异步处理", tuple(f"C{i:02d}" for i in range(10, 25))),
    ("viewer_3d", "Viewer 与 3D 交互", tuple(f"C{i:02d}" for i in range(25, 30))),
    ("asset_lifecycle", "资产生命周期与 Clips", tuple(f"C{i:02d}" for i in range(30, 40))),
    ("tokens_security", "Token 与安全", tuple(f"C{i:02d}" for i in range(40, 44))),
    ("usage", "用量与配额", tuple(f"C{i:02d}" for i in range(44, 46))),
    ("stories", "Stories", tuple(f"C{i:02d}" for i in range(46, 49))),
    ("observability_compatibility", "兼容性与可观测性", ("C49", "C50")),
    ("account_team_billing", "账户、团队与计费", tuple(f"C{i:02d}" for i in range(51, 57))),
    ("resilience_cleanup", "恢复与闭环清理", tuple(f"C{i:02d}" for i in range(57, 61))),
)

_CASE_TO_MODULE = {
    case_id: module_id
    for module_id, _label, case_ids in MODULES
    for case_id in case_ids
}
_P0 = {case_id for case_id, priority, _title, _expected in SCENARIOS if priority == "P0"}


def _read_cesium_runs(artifacts_root: Path) -> list[dict[str, Any]]:
    if not artifacts_root.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    for run_path in sorted(artifacts_root.glob("*/run.json")):
        try:
            payload = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        target = str(payload.get("base_url_summary") or payload.get("baseUrlSummary") or "")
        if is_cesium_target(target):
            payload = dict(payload)
            payload["_path"] = run_path.as_posix()
            runs.append(payload)
    return runs


def _gate_value(payload: dict[str, Any], snake: str, camel: str, default: Any = None) -> Any:
    gate = payload.get("completion_gate") or payload.get("completionGate") or {}
    if not isinstance(gate, dict):
        gate = {}
    return gate.get(snake, gate.get(camel, default))


def _run_facts(payload: dict[str, Any]) -> dict[str, Any]:
    stable = bool(
        (payload.get("stable_replay") or payload.get("stableReplay") or {}).get("passed")
        if isinstance(payload.get("stable_replay") or payload.get("stableReplay") or {}, dict)
        else False
    )
    stable = bool(_gate_value(payload, "stable_replay_passed", "stableReplayPassed", stable))
    strict_required = bool(_gate_value(payload, "strict_3d_required", "strict3dRequired", False))
    strict_passed = bool(_gate_value(payload, "strict_3d_passed", "strict3dPassed", False)) if strict_required else False
    evidence = payload.get("evidence_manifest") or payload.get("evidenceManifest") or {}
    evidence_ratio = float(evidence.get("completeness") or 0.0) if isinstance(evidence, dict) else 0.0
    cleanup = payload.get("cleanup_report") or payload.get("cleanupReport")
    cleanup_applicable = isinstance(cleanup, dict)
    cleanup_passed = not cleanup_applicable or (
        cleanup.get("status") == "passed" and not cleanup.get("manualActions")
    )
    status = str(payload.get("status") or "")
    goal_status = str(payload.get("goal_status") or payload.get("goalStatus") or "")
    return {
        "runId": str(payload.get("run_id") or payload.get("runId") or ""),
        "scenarioId": str(payload.get("scenario_id") or payload.get("scenarioId") or "").strip() or None,
        "status": status,
        "goalStatus": goal_status,
        "achieved": status == "passed" and goal_status == "achieved",
        "stableReplayPassed": stable,
        "strict3dRequired": strict_required,
        "strict3dPassed": strict_passed,
        "evidenceCompleteness": round(max(0.0, min(1.0, evidence_ratio)), 4),
        "evidencePassed": evidence_ratio >= EVIDENCE_THRESHOLD,
        "cleanupApplicable": cleanup_applicable,
        "cleanupPassed": cleanup_passed,
        "path": payload.get("_path"),
    }


def _case_stats(case: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
    achieved = [item for item in facts if item["achieved"]]
    stable = [item for item in facts if item["stableReplayPassed"]]
    strict = [item for item in facts if item["strict3dPassed"]]
    evidence = [item for item in facts if item["evidencePassed"]]
    status = case["execution"]["status"]
    if achieved and len(stable) >= REQUIRED_REPETITIONS:
        status = "passed"
    elif achieved:
        status = "completed_below_repetition_gate"
    elif facts:
        status = "executed_not_achieved"
    return {
        "id": case["id"],
        "priority": case["priority"],
        "title": case["title"],
        "module": _CASE_TO_MODULE.get(case["id"], "unmapped"),
        "status": status,
        "executedRuns": len(facts),
        "achievedRuns": len(achieved),
        "stableReplayRuns": len(stable),
        "strictWebglRuns": len(strict),
        "evidenceCompleteRuns": len(evidence),
        "repetitionsRequired": REQUIRED_REPETITIONS,
        "repetitionsCompleted": min(len(stable), REQUIRED_REPETITIONS),
        "runIds": [item["runId"] for item in facts if item["runId"]],
    }


def cesium_coverage_payload(artifacts_root: Path) -> dict[str, Any]:
    catalog = scenario_catalog()
    facts = [_run_facts(item) for item in _read_cesium_runs(artifacts_root)]
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unassigned: list[dict[str, Any]] = []
    for item in facts:
        scenario_id = item["scenarioId"]
        if scenario_id in _CASE_TO_MODULE:
            by_case[scenario_id].append(item)
        else:
            unassigned.append(item)

    cases = [_case_stats(case, by_case.get(case["id"], [])) for case in catalog]
    case_by_id = {case["id"]: case for case in cases}
    modules: list[dict[str, Any]] = []
    for module_id, label, case_ids in MODULES:
        module_cases = [case_by_id[item] for item in case_ids]
        completed = sum(case["achievedRuns"] > 0 for case in module_cases)
        p0_cases = [case for case in module_cases if case["id"] in _P0]
        p0_completed = sum(case["achievedRuns"] > 0 for case in p0_cases)
        modules.append({
            "id": module_id,
            "label": label,
            "caseIds": list(case_ids),
            "caseCount": len(module_cases),
            "completedCaseCount": completed,
            "completionPercent": round(completed / len(module_cases), 4) if module_cases else 1.0,
            "p0CaseCount": len(p0_cases),
            "completedP0CaseCount": p0_completed,
            "p0CompletionPercent": round(p0_completed / len(p0_cases), 4) if p0_cases else 1.0,
            "stableReplayRuns": sum(case["stableReplayRuns"] for case in module_cases),
            "strictWebglRuns": sum(case["strictWebglRuns"] for case in module_cases),
        })

    p0_total = len(_P0)
    p0_completed = sum(case["id"] in _P0 and case["achievedRuns"] > 0 for case in cases)
    completed_cases = sum(case["achievedRuns"] > 0 for case in cases)
    cesium_run_count = len(facts)
    stable_count = sum(item["stableReplayPassed"] for item in facts)
    strict_required_count = sum(item["strict3dRequired"] for item in facts)
    strict_passed_count = sum(item["strict3dPassed"] for item in facts)
    evidence_count = sum(item["evidencePassed"] for item in facts)
    cleanup_runs = [item for item in facts if item["cleanupApplicable"]]
    cleanup_count = sum(item["cleanupPassed"] for item in cleanup_runs)
    evidence_ratio = evidence_count / cesium_run_count if cesium_run_count else 0.0
    business_gate = {
        "passed": p0_completed == p0_total and completed_cases / len(cases) >= 0.95,
        "p0CompletionPercent": round(p0_completed / p0_total, 4) if p0_total else 0.0,
        "allCompletionPercent": round(completed_cases / len(cases), 4) if cases else 0.0,
        "requiredP0CompletionPercent": 1.0,
        "requiredAllCompletionPercent": 0.95,
    }
    stable_gate = {
        "passed": stable_count >= REQUIRED_REPETITIONS,
        "actual": stable_count,
        "required": REQUIRED_REPETITIONS,
    }
    strict_gate = {
        "passed": strict_required_count >= REQUIRED_REPETITIONS and strict_passed_count >= REQUIRED_REPETITIONS,
        "actual": strict_passed_count,
        "required": REQUIRED_REPETITIONS,
        "eligibleRuns": strict_required_count,
    }
    evidence_gate = {
        "passed": cesium_run_count > 0 and evidence_ratio >= EVIDENCE_THRESHOLD,
        "actualCompleteRunPercent": round(evidence_ratio, 4),
        "requiredCompleteRunPercent": EVIDENCE_THRESHOLD,
        "completeRuns": evidence_count,
        "runCount": cesium_run_count,
    }
    cleanup_gate = {
        "passed": all(item["cleanupPassed"] for item in cleanup_runs),
        "actual": cleanup_count,
        "required": len(cleanup_runs),
        "applicableRuns": len(cleanup_runs),
    }
    gates = {
        "businessCoverage": business_gate,
        "stableReplay": stable_gate,
        "strictWebgl": strict_gate,
        "evidence": evidence_gate,
        "cleanup": cleanup_gate,
    }
    return {
        "schemaVersion": "1",
        "suite": "cesium-ion",
        "target": "https://ion.cesium.com",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "truthPolicy": "只有带明确 scenarioId 的真实 run.json 才计入场景；未执行、阻塞、证据不足或未达五轮回放不得计为通过",
        "thresholds": {
            "requiredRepetitions": REQUIRED_REPETITIONS,
            "p0CompletionPercent": 1.0,
            "allCompletionPercent": 0.95,
            "evidencePercent": EVIDENCE_THRESHOLD,
        },
        "runs": {
            "cesiumRunCount": cesium_run_count,
            "assignedRunCount": len(facts) - len(unassigned),
            "unassignedRunCount": len(unassigned),
            "achievedRunCount": sum(item["achieved"] for item in facts),
            "stableReplayPassedCount": stable_count,
            "strictWebglPassedCount": strict_passed_count,
            "evidenceCompleteCount": evidence_count,
            "cleanupApplicableCount": len(cleanup_runs),
        },
        "modules": modules,
        "cases": cases,
        "unassignedRuns": [
            {key: item[key] for key in ("runId", "status", "goalStatus", "strict3dRequired", "strict3dPassed", "path")}
            for item in unassigned
        ],
        "gates": gates,
        "releaseReady": all(item["passed"] for item in gates.values()),
    }

