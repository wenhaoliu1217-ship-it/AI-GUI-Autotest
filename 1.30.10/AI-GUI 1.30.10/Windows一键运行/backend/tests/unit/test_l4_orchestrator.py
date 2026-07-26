import json
from pathlib import Path

from gui_agent.acceptance import L4Orchestrator


WORKFLOW = Path(__file__).resolve().parents[2] / "benchmarks" / "gaealavic" / "l4-workflow.json"


def workflow() -> dict:
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


def test_l4_dry_run_validates_all_dependencies_without_claiming_execution(tmp_path: Path) -> None:
    result = L4Orchestrator().run(workflow(), tmp_path, dry_run=True)

    assert result["status"] == "unverified"
    assert result["goalStatus"] == "incomplete"
    assert result["verificationStatus"] == "dry_run_only"
    assert len(result["stateTimeline"]) == len(workflow()["stages"])
    assert all(item["status"] == "dry_run_ready" for item in result["stateTimeline"])
    assert (tmp_path / "l4-result.json").is_file()
    assert (tmp_path / "l4-report.md").is_file()


def test_l4_passes_outputs_to_dependents_and_records_success(tmp_path: Path) -> None:
    seen_contexts = {}
    executors = {}
    for stage in workflow()["stages"]:
        def execute(context, current=stage):
            seen_contexts[current["id"]] = context
            return {"status": "passed", "outputs": {name: f"{current['id']}-{name}" for name in current["requiredOutputs"]}}
        executors[stage["id"]] = execute

    result = L4Orchestrator().run(workflow(), tmp_path, stage_executors=executors)

    assert result["status"] == "passed"
    assert result["goalStatus"] == "achieved"
    assert result["outputs"]["start"]["simulationRunId"] == "start-simulationRunId"
    assert seen_contexts["wait"]["dependencyOutputs"]["start"]["simulationRunId"] == "start-simulationRunId"


def test_l4_failure_cleans_completed_stages_in_reverse_and_emits_manual_actions(tmp_path: Path) -> None:
    small = {
        "stages": [
            {"id": "model", "requiredOutputs": ["businessId"]},
            {"id": "scenario", "dependsOn": ["model"], "requiredOutputs": ["businessId"]},
            {"id": "start", "dependsOn": ["scenario"], "requiredOutputs": ["runId"]},
        ]
    }
    cleanup_order = []
    result = L4Orchestrator().run(
        small,
        tmp_path,
        stage_executors={
            "model": lambda _context: {"status": "passed", "outputs": {"businessId": "model-1"}},
            "scenario": lambda _context: {"status": "passed", "outputs": {"businessId": "scenario-1"}},
            "start": lambda _context: {"status": "failed", "outputs": {}, "error": "start failed"},
        },
        cleanup_executors={
            "scenario": lambda _outputs: cleanup_order.append("scenario") or {"status": "deleted"},
        },
    )

    assert cleanup_order == ["scenario"]
    assert result["failedStage"] == "start"
    assert result["cleanupSuccess"] is False
    assert result["manualCleanupActions"][0]["stageId"] == "model"
