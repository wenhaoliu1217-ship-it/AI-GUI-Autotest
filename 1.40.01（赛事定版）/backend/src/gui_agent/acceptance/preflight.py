"""Control-plane delivery checks kept separate from business completion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .benchmark import load_scenarios
from .l4 import L4Orchestrator


class PreflightCheck(BaseModel):
    id: str
    status: Literal["ready", "blocked", "error"]
    summary: str
    details: dict = Field(default_factory=dict)


class ControlPlanePreflight(BaseModel):
    model_config = {"populate_by_name": True}

    schema_version: str = Field(default="1", alias="schemaVersion")
    status: Literal["ready", "blocked", "error"]
    control_plane_ready: bool = Field(alias="controlPlaneReady")
    write_operations_allowed: bool = Field(alias="writeOperationsAllowed")
    checks: list[PreflightCheck]
    blockers: list[str] = Field(default_factory=list)


def evaluate_control_plane_preflight(gae_root: str | Path, cesium_manifest: dict) -> ControlPlanePreflight:
    root = Path(gae_root)
    checks: list[PreflightCheck] = []
    blockers: list[str] = []

    try:
        scenarios = load_scenarios(root / "scenarios")
        ready_count = sum(item.binding_status == "ready" for item in scenarios)
        blocked_count = len(scenarios) - ready_count
        checks.append(PreflightCheck(
            id="gaealavic_scenarios",
            status="ready",
            summary="S01-S30 场景合同完整且顺序连续",
            details={"scenarioCount": len(scenarios), "readyCount": ready_count, "blockedCount": blocked_count},
        ))
        blockers.extend(dependency for item in scenarios for dependency in item.blocked_dependencies)
    except Exception as exc:
        checks.append(PreflightCheck(id="gaealavic_scenarios", status="error", summary=str(exc)))
        blockers.append("gaealavic_scenario_catalog_invalid")

    try:
        workflow = json.loads((root / "l4-workflow.json").read_text(encoding="utf-8"))
        stages = L4Orchestrator._validate(workflow)
        expected = ["MODEL", "INSTANCE", "SCENARIO", "RUN_TRAINING", "CLOSE"]
        actual = [stage["id"] for stage in stages]
        if actual != expected:
            raise ValueError(f"L4 阶段必须为 {expected}，当前为 {actual}")
        checks.append(PreflightCheck(
            id="gaealavic_l4_workflow",
            status="ready",
            summary="L4 阶段、依赖、必需输出和清理合同可读",
            details={"stageCount": len(stages), "stageIds": actual},
        ))
    except Exception as exc:
        checks.append(PreflightCheck(id="gaealavic_l4_workflow", status="error", summary=str(exc)))
        blockers.append("gaealavic_l4_workflow_invalid")

    manifest_status = str(cesium_manifest.get("manifestStatus") or "blocked")
    manifest_ready = manifest_status == "ready"
    checks.append(PreflightCheck(
        id="cesium_test_data_manifest",
        status="ready" if manifest_ready else "blocked",
        summary=("Cesium 固定数据 manifest 已就绪" if manifest_ready else str(cesium_manifest.get("reason") or "Cesium 固定数据 manifest 未就绪")),
        details={"manifestStatus": manifest_status, "version": cesium_manifest.get("version")},
    ))
    if not manifest_ready:
        blockers.append("cesium_authoritative_test_data_manifest")

    control_plane_ready = all(item.status == "ready" for item in checks[:2])
    unique_blockers = sorted(set(blockers))
    write_operations_allowed = control_plane_ready and manifest_ready and not unique_blockers
    status: Literal["ready", "blocked", "error"] = (
        "error" if any(item.status == "error" for item in checks[:2]) else
        "ready" if write_operations_allowed else
        "blocked"
    )
    return ControlPlanePreflight(
        status=status,
        control_plane_ready=control_plane_ready,
        write_operations_allowed=write_operations_allowed,
        checks=checks,
        blockers=unique_blockers,
    )


def assert_control_plane_delivery(gae_root: str | Path) -> None:
    report = evaluate_control_plane_preflight(gae_root, {"manifestStatus": "blocked"})
    errors = [item.summary for item in report.checks[:2] if item.status != "ready"]
    if errors:
        raise RuntimeError("控制面交付包不完整：" + "；".join(errors))
