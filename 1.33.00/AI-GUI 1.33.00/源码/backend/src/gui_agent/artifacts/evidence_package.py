"""Build a run-linked, machine-readable standard evidence package."""

from __future__ import annotations

from typing import Any

from ..domain.results import RunResult


def build_evidence_package(artifacts, result: RunResult) -> tuple[dict[str, Any], str]:
    prefix = "evidence"
    snapshots: dict[str, tuple[str, Any]] = {
        "run_metadata": (f"{prefix}/run-metadata.json", {
            "runId": result.run_id, "planName": result.plan_name, "status": result.status.value,
            "startedAt": result.started_at.isoformat(), "endedAt": result.ended_at.isoformat(),
            "projectId": result.project_id, "environmentId": result.environment_id,
            "accountId": result.account_id, "accountRole": result.account_role,
            "scenarioId": result.scenario_id, "goalStatus": result.goal_status,
        }),
        "project_environment": (f"{prefix}/project-environment.json", {
            "project": result.project_snapshot, "environment": result.environment_snapshot,
            "businessContext": result.business_context_snapshot,
        }),
        "app_map": (f"{prefix}/app-map.json", result.app_map_snapshot),
        "action_timeline": (f"{prefix}/action-timeline.json", [item.model_dump(mode="json") for item in result.steps]),
        "browser_observations": (f"{prefix}/browser-observations.json", [
            {"stepId": f"step-{item.index}", "before": item.before.model_dump(mode="json") if item.before else None, "after": item.after.model_dump(mode="json") if item.after else None}
            for item in result.steps
        ]),
        "websocket": (f"{prefix}/websocket-timeline.json", result.websocket_timeline),
        "model_calls": (f"{prefix}/model-calls.json", [item.model_dump(mode="json") for item in result.model_call_records]),
        "findings": (f"{prefix}/findings.json", [item.model_dump(mode="json") for item in result.findings]),
        "confirmations": (f"{prefix}/confirmations.json", result.confirmation_history),
        "cleanup": (f"{prefix}/cleanup-report.json", result.cleanup_report),
    }
    written: dict[str, str] = {}
    for key, (path, payload) in snapshots.items():
        if payload is not None:
            written[key] = artifacts.write_json(path, {"runId": result.run_id, "data": payload})

    def item(
        key: str, label: str, *, applicable: bool = True, present: bool | None = None,
        reason: str = "", paths: list[str] | None = None, step_ids: list[str] | None = None,
        business_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        resolved_present = bool(paths) if present is None else present
        status = "not_applicable" if not applicable else "present" if resolved_present else "missing"
        return {
            "id": key, "label": label, "status": status, "reason": reason,
            "artifacts": paths or [], "runId": result.run_id,
            "stepIds": step_ids or [], "businessIds": business_ids or [],
        }

    step_ids = [f"step-{item.index}" for item in result.steps]
    business_ids = _business_ids(result)
    screenshot_paths = [path for step in result.steps for path in [step.screenshot] if path]
    file_steps = [step for step in result.steps if step.file_evidence]
    canvas_steps = [step for step in result.steps if step.canvas_evidence]
    items = [
        item("run_metadata", "运行元数据", paths=[written["run_metadata"]]),
        item("plan", "受约束执行计划", present=(artifacts.run_dir / "plan.json").is_file(), paths=["plan.json"], step_ids=step_ids),
        item("project_environment", "脱敏项目／环境与业务上下文", applicable=bool(result.project_id), present="project_environment" in written, reason="未关联项目" if not result.project_id else "", paths=[written["project_environment"]] if "project_environment" in written else []),
        item("app_map", "兼容性扫描与页面地图", applicable=bool(result.project_id), present="app_map" in written and result.app_map_snapshot is not None, reason="未关联项目" if not result.project_id else "项目尚无 app-map" if result.app_map_snapshot is None else "", paths=[written["app_map"]] if "app_map" in written else []),
        item("actions", "动作时间线", paths=[written["action_timeline"]], step_ids=step_ids, business_ids=business_ids),
        item("observations", "DOM／ARIA／console／network 观察", paths=[written["browser_observations"]], step_ids=step_ids),
        item("screenshots", "步骤截图", applicable=bool(result.steps), present=len(screenshot_paths) == len(result.steps), reason="运行无步骤" if not result.steps else "部分步骤截图缺失" if len(screenshot_paths) != len(result.steps) else "", paths=screenshot_paths, step_ids=step_ids),
        item("trace", "Playwright Trace", present=artifacts.trace_path.is_file(), paths=["trace.zip"] if artifacts.trace_path.is_file() else []),
        item("websocket", "WebSocket 时间线", applicable=bool(result.websocket_timeline), paths=[written["websocket"]] if result.websocket_timeline else [], reason="运行未建立 WebSocket" if not result.websocket_timeline else ""),
        item("model_calls", "模型调用记录", applicable=bool(result.model_call_records), paths=[written["model_calls"]] if result.model_call_records else [], reason="固定计划未调用模型" if not result.model_call_records else ""),
        item("file_transfer", "上传／下载证据", applicable=bool(file_steps), present=all(step.file_evidence for step in file_steps), reason="计划无上传／下载" if not file_steps else "", step_ids=[f"step-{step.index}" for step in file_steps], business_ids=business_ids),
        item("canvas", "Canvas／Bridge 语义证据", applicable=bool(canvas_steps or any(a.semantic_evidence for a in result.assertions)), present=all(step.canvas_evidence for step in canvas_steps), reason="计划无 Canvas／Bridge 行为" if not canvas_steps and not any(a.semantic_evidence for a in result.assertions) else "", step_ids=[f"step-{step.index}" for step in canvas_steps]),
        item("findings", "Finding 与证据时间线", paths=[written["findings"]]),
        item("generated_test", "生成的 Playwright 测试", present=bool(result.generated_test and result.generated_test.source_path), paths=[result.generated_test.source_path] if result.generated_test else []),
        item("confirmations", "危险动作确认历史", applicable=bool(result.confirmation_history), paths=[written["confirmations"]] if result.confirmation_history else [], reason="运行无确认动作" if not result.confirmation_history else ""),
        item("cleanup", "业务对象清理报告", applicable=result.cleanup_report is not None, paths=[written["cleanup"]] if result.cleanup_report is not None else [], reason="场景未声明业务对象清理" if result.cleanup_report is None else "", business_ids=business_ids),
        item("review_history", "Finding／路径审核历史", applicable=False, reason="运行结束后人工审核阶段生成"),
        item("acceptance_summary", "批次验收摘要", applicable=False, reason="仅批量验收任务适用"),
    ]
    applicable = [entry for entry in items if entry["status"] != "not_applicable"]
    present_count = sum(entry["status"] == "present" for entry in applicable)
    completeness = round(present_count / len(applicable), 4) if applicable else 1.0
    manifest = {
        "schemaVersion": "1", "runId": result.run_id, "items": items,
        "applicableCount": len(applicable), "presentCount": present_count,
        "missingCount": len(applicable) - present_count, "completeness": completeness,
    }
    path = artifacts.write_json(f"{prefix}/evidence-manifest.json", manifest)
    return manifest, path


def _business_ids(result: RunResult) -> list[str]:
    values: list[str] = []
    for step in result.steps:
        for evidence in (step.file_evidence, step.async_evidence, step.side_effect_evidence):
            if not isinstance(evidence, dict):
                continue
            for key in ("businessObjectId", "generatedBusinessId", "businessId"):
                if evidence.get(key):
                    values.append(str(evidence[key]))
    if result.cleanup_report:
        values.extend(str(item.get("businessId")) for item in result.cleanup_report.get("objects", []) if item.get("businessId"))
    return list(dict.fromkeys(values))
