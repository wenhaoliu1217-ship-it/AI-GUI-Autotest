import json
from pathlib import Path

from gui_agent.acceptance import AcceptanceRunner, load_scenarios


CATALOG = Path(__file__).resolve().parents[2] / "benchmarks" / "gaealavic" / "scenarios"


def test_catalog_has_complete_executable_contracts_and_explicit_blockers() -> None:
    scenarios = load_scenarios(CATALOG)

    assert [item.id for item in scenarios] == [f"S{index:02d}" for index in range(1, 31)]
    assert all(item.environment_ref and item.account_role for item in scenarios)
    assert all(item.prerequisites and item.steps and item.assertions for item in scenarios)
    assert all(item.dangerous_policy and item.cleanup and item.evidence_requirements for item in scenarios)
    assert all(item.binding_status == "blocked" and item.blocked_dependencies for item in scenarios)


def test_blocked_scenarios_still_create_150_attempt_denominator(tmp_path) -> None:
    scenarios = load_scenarios(CATALOG)
    calls = []

    summary = AcceptanceRunner(scenarios).run(lambda *_args: calls.append(_args), tmp_path)

    assert calls == []
    assert len(summary["attempts"]) == 150
    assert summary["metrics"]["plannedRuns"] == 150
    assert summary["metrics"]["scenarioCompletionRate"] == 0
    assert summary["releaseStatus"] == "blocked"
    assert not summary["gates"]["allScenariosExecutable"]
    assert not summary["gates"]["l4"]
    assert (tmp_path / "acceptance-summary.json").is_file()
    assert (tmp_path / "acceptance-report.md").is_file()


def test_ready_runs_aggregate_weighted_metrics_and_traceability(tmp_path) -> None:
    scenarios = [
        item.model_copy(update={"binding_status": "ready", "blocked_dependencies": [], "executable_plan": {"name": item.name}})
        for item in load_scenarios(CATALOG)
    ]

    def execute(scenario, repeat):
        return {
            "run_id": f"run-{scenario.id}-{repeat}",
            "status": "passed",
            "goal_status": "achieved",
            "completion_reason": "plan_completed",
            "replay_mode": "stable",
            "evidence_manifest": {"presentCount": 19, "applicableCount": 20},
            "evidence_manifest_path": "evidence/evidence-manifest.json",
            "cleanup_report": {"objects": [{"status": "cleared", "verified": True}]},
            "steps": [{
                "index": 1,
                "status": "passed",
                "execution_mode": "locator",
                "side_effect_evidence": {"businessObjectId": f"E2E_{scenario.id}_{repeat}"},
            }],
        }

    summary = AcceptanceRunner(scenarios).run(
        execute,
        tmp_path,
        l4_result={"run_id": "l4-1", "goal_status": "achieved", "cleanup_success": True},
    )

    assert summary["metrics"]["scenarioCompletionRate"] == 1
    assert summary["metrics"]["stableReplaySuccessRate"] == 1
    assert summary["metrics"]["evidenceCompleteness"] == 0.95
    assert summary["metrics"]["cleanupSuccessRate"] == 1
    assert summary["metrics"]["l4Success"] is True
    assert summary["releaseStatus"] == "releasable"
    assert summary["attempts"][0]["runId"] == "run-S01-1"
    assert summary["attempts"][0]["businessIds"] == ["E2E_S01_1"]
    persisted = json.loads((tmp_path / "acceptance-summary.json").read_text(encoding="utf-8"))
    assert persisted["attempts"][0]["evidenceManifestPath"] == "evidence/evidence-manifest.json"
