"""Dependency-aware L4 workflow execution and reverse cleanup."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4


StageExecutor = Callable[[dict], dict]
CleanupExecutor = Callable[[dict], dict]


class L4WorkflowError(ValueError):
    pass


class L4Orchestrator:
    def run(
        self,
        workflow: dict,
        output_dir: str | Path,
        *,
        stage_executors: dict[str, StageExecutor] | None = None,
        cleanup_executors: dict[str, CleanupExecutor] | None = None,
        dry_run: bool = False,
    ) -> dict:
        stages = self._validate(workflow)
        run_id = f"l4-{datetime.now().astimezone():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        timeline: list[dict] = []
        outputs: dict[str, dict] = {}
        completed: list[str] = []
        manual_actions: list[dict] = []
        cleanup_results: list[dict] = []
        failed_stage: str | None = None

        if dry_run:
            for stage in stages:
                stage_outputs = {name: f"dry-run://{stage['id']}/{name}" for name in stage["requiredOutputs"]}
                outputs[stage["id"]] = stage_outputs
                timeline.append(self._event(stage["id"], "dry_run_ready", "依赖与输出契约已验证，未访问目标站"))
            result = self._result(
                run_id, "unverified", "incomplete", True, timeline, outputs, [], [], None,
                verification_status="dry_run_only",
            )
            return self._persist(output_dir, result)

        executors = stage_executors or {}
        cleanup = cleanup_executors or {}
        for stage in stages:
            stage_id = stage["id"]
            missing_dependencies = [item for item in stage.get("dependsOn", []) if item not in completed]
            if missing_dependencies:
                failed_stage = stage_id
                timeline.append(self._event(stage_id, "blocked", f"前置阶段未完成：{', '.join(missing_dependencies)}"))
                break
            executor = executors.get(stage_id)
            if executor is None:
                failed_stage = stage_id
                timeline.append(self._event(stage_id, "blocked", "缺少阶段执行绑定"))
                break
            timeline.append(self._event(stage_id, "running", "阶段开始"))
            context = {"l4RunId": run_id, "stageId": stage_id, "dependencyOutputs": {key: outputs[key] for key in stage.get("dependsOn", [])}, "allOutputs": outputs}
            try:
                response = executor(context)
            except Exception as exc:
                response = {"status": "system_error", "error": f"{type(exc).__name__}: {exc}", "outputs": {}}
            stage_outputs = response.get("outputs") if isinstance(response.get("outputs"), dict) else {}
            missing_outputs = [
                name for name in stage["requiredOutputs"]
                if stage_outputs.get(name) is None or stage_outputs.get(name) == "" or stage_outputs.get(name) == []
            ]
            if response.get("status") != "passed" or missing_outputs:
                failed_stage = stage_id
                reason = response.get("error") or response.get("completionReason") or "阶段未通过"
                if missing_outputs:
                    reason = f"缺少必需输出：{', '.join(missing_outputs)}"
                timeline.append(self._event(stage_id, "failed", reason))
                break
            outputs[stage_id] = stage_outputs
            completed.append(stage_id)
            timeline.append(self._event(stage_id, "passed", "阶段完成", outputs=stage_outputs))

        cleanup_success = True
        if failed_stage:
            for stage_id in reversed(completed):
                cleanup_executor = cleanup.get(stage_id)
                if cleanup_executor is None:
                    cleanup_success = False
                    action = {"stageId": stage_id, "reason": "缺少自动清理绑定", "outputs": outputs.get(stage_id, {})}
                    manual_actions.append(action)
                    timeline.append(self._event(stage_id, "manual_cleanup_required", action["reason"]))
                    continue
                try:
                    cleanup_result = cleanup_executor(outputs.get(stage_id, {}))
                except Exception as exc:
                    cleanup_result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                cleanup_results.append({"stageId": stage_id, **cleanup_result})
                if cleanup_result.get("status") not in {"cleared", "passed", "deleted"}:
                    cleanup_success = False
                    manual_actions.append({"stageId": stage_id, "reason": cleanup_result.get("error", "自动清理失败"), "outputs": outputs.get(stage_id, {})})
                timeline.append(self._event(stage_id, f"cleanup_{cleanup_result.get('status', 'failed')}", cleanup_result.get("error", "逆序清理")))

        goal_status = "achieved" if not failed_stage and len(completed) == len(stages) else "incomplete"
        status = "passed" if goal_status == "achieved" else "failed"
        result = self._result(run_id, status, goal_status, cleanup_success, timeline, outputs, cleanup_results, manual_actions, failed_stage, verification_status="executed")
        return self._persist(output_dir, result)

    @staticmethod
    def _validate(workflow: dict) -> list[dict]:
        stages = workflow.get("stages")
        if not isinstance(stages, list) or not stages:
            raise L4WorkflowError("L4 工作流缺少 stages")
        ids = [str(item.get("id", "")) for item in stages]
        if not all(ids) or len(ids) != len(set(ids)):
            raise L4WorkflowError("L4 阶段 ID 必须唯一且非空")
        seen: set[str] = set()
        for stage in stages:
            dependencies = stage.get("dependsOn", [])
            unknown = [item for item in dependencies if item not in seen]
            if unknown:
                raise L4WorkflowError(f"阶段 {stage['id']} 存在未定义或逆序依赖：{unknown[0]}")
            required = stage.get("requiredOutputs")
            if not isinstance(required, list) or not required:
                raise L4WorkflowError(f"阶段 {stage['id']} 缺少 requiredOutputs")
            seen.add(stage["id"])
        return stages

    @staticmethod
    def _event(stage_id: str, status: str, detail: str, **extra) -> dict:
        return {"at": datetime.now().astimezone().isoformat(), "stageId": stage_id, "status": status, "detail": detail, **extra}

    @staticmethod
    def _result(run_id, status, goal_status, cleanup_success, timeline, outputs, cleanup_results, manual_actions, failed_stage, *, verification_status):
        return {
            "schemaVersion": "1", "runId": run_id, "status": status, "goalStatus": goal_status,
            "verificationStatus": verification_status, "cleanupSuccess": cleanup_success,
            "failedStage": failed_stage, "outputs": outputs, "stateTimeline": timeline,
            "cleanupResults": cleanup_results, "manualCleanupActions": manual_actions,
            "generatedAt": datetime.now().astimezone().isoformat(),
        }

    @staticmethod
    def _persist(output_dir: str | Path, result: dict) -> dict:
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "l4-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [
            "# GAEALaViC L4 闭环报告", "", f"- 运行：`{result['runId']}`",
            f"- 状态：**{result['status']}**", f"- 验证：{result['verificationStatus']}",
            f"- 清理：{'成功' if result['cleanupSuccess'] else '需要人工处置'}", "", "## 状态时间线", "",
        ]
        lines.extend(f"- {item['at']} · {item['stageId']} · {item['status']} · {item['detail']}" for item in result["stateTimeline"])
        lines.extend(["", "## 人工清理", ""])
        lines.extend(f"- {item['stageId']}: {item['reason']}" for item in result["manualCleanupActions"])
        (target / "l4-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return result
