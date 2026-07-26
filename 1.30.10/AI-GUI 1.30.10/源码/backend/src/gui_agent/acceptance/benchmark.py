from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

from .models import AcceptanceAttempt, BenchmarkScenario

ScenarioExecutor = Callable[[BenchmarkScenario, int], dict]


def load_scenarios(root: str | Path) -> list[BenchmarkScenario]:
    scenarios = [
        BenchmarkScenario.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(Path(root).glob("S*.json"))
    ]
    expected = [f"S{index:02d}" for index in range(1, 31)]
    actual = [item.id for item in scenarios]
    if actual != expected:
        raise ValueError(f"基准场景必须完整覆盖 S01-S30，当前为 {actual}")
    return scenarios


class AcceptanceRunner:
    def __init__(self, scenarios: Iterable[BenchmarkScenario], repeats: int = 5) -> None:
        self.scenarios = list(scenarios)
        self.repeats = repeats
        if [item.id for item in self.scenarios] != [f"S{index:02d}" for index in range(1, 31)]:
            raise ValueError("验收调度器只接受有序且完整的 S01-S30")
        if repeats != 5:
            raise ValueError("1.30.10 发布验收固定要求每场景重复 5 次")

    def run(
        self,
        executor: ScenarioExecutor,
        output_dir: str | Path,
        *,
        l4_result: dict | None = None,
    ) -> dict:
        batch_id = f"acceptance-{datetime.now().astimezone():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        attempts: list[AcceptanceAttempt] = []
        for scenario in self.scenarios:
            for repeat in range(1, self.repeats + 1):
                if scenario.binding_status == "blocked":
                    attempts.append(AcceptanceAttempt(
                        scenarioId=scenario.id,
                        repeat=repeat,
                        status="blocked",
                        goalStatus="incomplete",
                        runId=f"{batch_id}-{scenario.id}-R{repeat}",
                        completionReason="enterprise_dependency_blocked",
                        blockedDependencies=scenario.blocked_dependencies,
                    ))
                    continue
                attempts.append(_attempt_from_result(scenario, repeat, executor(scenario, repeat)))
        summary = _summarize(batch_id, attempts, l4_result)
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "acceptance-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (target / "acceptance-report.md").write_text(_markdown(summary), encoding="utf-8")
        return summary


def _attempt_from_result(scenario: BenchmarkScenario, repeat: int, result: dict) -> AcceptanceAttempt:
    manifest = result.get("evidence_manifest") or {}
    cleanup = result.get("cleanup_report") or {}
    cleanup_objects = cleanup.get("objects", [])
    steps = result.get("steps", [])
    business_ids = sorted({
        str(evidence[key])
        for step in steps
        for evidence in (step.get("file_evidence"), step.get("async_evidence"), step.get("side_effect_evidence"))
        if isinstance(evidence, dict)
        for key in ("businessObjectId", "generatedBusinessId", "businessId")
        if evidence.get(key)
    })
    return AcceptanceAttempt(
        scenarioId=scenario.id,
        repeat=repeat,
        status=result.get("status", "system_error"),
        goalStatus=result.get("goal_status", "incomplete"),
        runId=result.get("run_id", f"missing-{scenario.id}-R{repeat}"),
        completionReason=result.get("completion_reason", "missing_completion_reason"),
        stableCandidate=result.get("replay_mode") == "stable",
        stableSuccess=result.get("replay_mode") == "stable" and result.get("status") == "passed",
        adaptiveInterventions=sum(step.get("execution_mode") == "adaptive" for step in steps),
        visualInterventions=sum(step.get("execution_mode") == "visual" for step in steps),
        evidencePresent=int(manifest.get("presentCount", 0)),
        evidenceRequired=int(manifest.get("applicableCount", 0)),
        cleanupCleared=sum(item.get("status") in {"cleared", "deleted"} and item.get("verified") for item in cleanup_objects),
        cleanupRequired=len(cleanup_objects),
        highRiskMisoperations=int(result.get("high_risk_misoperations", 0)),
        plaintextSensitiveLeaks=int(result.get("plaintext_sensitive_leaks", 0)),
        nakedCoordinateStableCases=int(result.get("naked_coordinate_stable_cases", 0)),
        unauthorizedDomainAccesses=int(result.get("unauthorized_domain_accesses", 0)),
        evidenceManifestPath=result.get("evidence_manifest_path"),
        businessIds=business_ids,
        failedStepIds=[f"step-{step['index']}" for step in steps if step.get("status") != "passed"],
    )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _summarize(batch_id: str, attempts: list[AcceptanceAttempt], l4_result: dict | None) -> dict:
    total = len(attempts)
    achieved = sum(item.goal_status == "achieved" for item in attempts)
    stable_total = sum(item.stable_candidate for item in attempts)
    stable_passed = sum(item.stable_success for item in attempts)
    evidence_required = sum(item.evidence_required for item in attempts)
    evidence_present = sum(item.evidence_present for item in attempts)
    cleanup_required = sum(item.cleanup_required for item in attempts)
    cleanup_cleared = sum(item.cleanup_cleared for item in attempts)
    zero_tolerance = {
        "highRiskMisoperations": sum(item.high_risk_misoperations for item in attempts),
        "plaintextSensitiveLeaks": sum(item.plaintext_sensitive_leaks for item in attempts),
        "nakedCoordinateStableCases": sum(item.naked_coordinate_stable_cases for item in attempts),
        "unauthorizedDomainAccesses": sum(item.unauthorized_domain_accesses for item in attempts),
    }
    l4_success = bool(l4_result and l4_result.get("goal_status") == "achieved" and l4_result.get("cleanup_success") is True)
    metrics = {
        "plannedRuns": 150,
        "recordedAttempts": total,
        "scenarioCompletionRate": _rate(achieved, 150),
        "stableReplaySuccessRate": _rate(stable_passed, stable_total),
        "stableReplayAttempts": stable_total,
        "evidenceCompleteness": _rate(evidence_present, evidence_required),
        "cleanupSuccessRate": _rate(cleanup_cleared, cleanup_required),
        "cleanupRequiredObjects": cleanup_required,
        "adaptiveInterventions": sum(item.adaptive_interventions for item in attempts),
        "visualInterventions": sum(item.visual_interventions for item in attempts),
        "l4Success": l4_success,
        **zero_tolerance,
    }
    gates = {
        "allScenariosExecutable": all(not item.blocked_dependencies for item in attempts),
        "runCount": total == 150,
        "scenarioCompletion": metrics["scenarioCompletionRate"] >= 0.80,
        "stableReplay": stable_total > 0 and metrics["stableReplaySuccessRate"] >= 0.95,
        "evidence": evidence_required > 0 and metrics["evidenceCompleteness"] >= 0.95,
        "cleanup": cleanup_required > 0 and metrics["cleanupSuccessRate"] == 1.0,
        "l4": l4_success,
        "zeroTolerance": all(value == 0 for value in zero_tolerance.values()),
    }
    blocked = sorted({dependency for item in attempts for dependency in item.blocked_dependencies})
    releasable = all(gates.values()) and not blocked
    return {
        "schemaVersion": "1",
        "batchId": batch_id,
        "generatedAt": datetime.now().astimezone().isoformat(),
        "releaseStatus": "releasable" if releasable else "blocked",
        "metrics": metrics,
        "gates": gates,
        "blockedDependencies": blocked,
        "failureDistribution": dict(Counter(item.completion_reason for item in attempts if item.goal_status != "achieved")),
        "attempts": [item.model_dump(mode="json", by_alias=True) for item in attempts],
        "l4Result": l4_result,
    }


def _markdown(summary: dict) -> str:
    metrics = summary["metrics"]
    lines = [
        "# GAEALaViC 1.30.10 验收报告",
        "",
        f"- 批次：`{summary['batchId']}`",
        f"- 发布状态：**{summary['releaseStatus']}**",
        f"- 记录尝试：{metrics['recordedAttempts']} / 150",
        f"- 场景完成率：{metrics['scenarioCompletionRate'] * 100:.2f}%",
        f"- 稳定回放成功率：{metrics['stableReplaySuccessRate'] * 100:.2f}%",
        f"- 证据完整率：{metrics['evidenceCompleteness'] * 100:.2f}%",
        f"- 清理成功率：{metrics['cleanupSuccessRate'] * 100:.2f}%",
        f"- L4 完整成功：{'是' if metrics['l4Success'] else '否'}",
        "",
        "## 发布门槛",
        "",
    ]
    lines.extend(f"- [{'x' if passed else ' '}] {name}" for name, passed in summary["gates"].items())
    lines.extend(["", "## 阻塞依赖", ""])
    lines.extend(f"- {item}" for item in summary["blockedDependencies"])
    lines.extend(["", "## 失败分布", ""])
    lines.extend(f"- {reason}: {count}" for reason, count in summary["failureDistribution"].items())
    return "\n".join(lines) + "\n"
