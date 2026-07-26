import json
from pathlib import Path
from time import monotonic, sleep

from gui_agent.acceptance import AcceptanceBatchManager, CompiledScenario, dry_run_executor, load_scenarios
from gui_agent.domain.models import Step, TestPlan as PlanModel


CATALOG = Path(__file__).resolve().parents[2] / "benchmarks" / "gaealavic" / "scenarios"


def compiled_catalog() -> list[CompiledScenario]:
    return [
        CompiledScenario(
            scenario=item,
            plan=PlanModel(name=item.name, base_url="https://example.com", steps=[Step(action="screenshot")]),
            account_id="tester",
            file_ids=(),
        )
        for item in load_scenarios(CATALOG)
    ]


def wait_for(manager: AcceptanceBatchManager, batch_id: str, statuses: set[str]) -> dict:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        payload = manager.read(batch_id)
        if payload and payload["status"] in statuses:
            return payload
        sleep(0.01)
    raise AssertionError(f"batch did not reach {statuses}")


def test_dry_run_persists_fixed_150_attempts_and_unverified_report(tmp_path: Path) -> None:
    manager = AcceptanceBatchManager(tmp_path)
    started = manager.start(compiled_catalog(), dry_run_executor, dry_run=True)
    completed = wait_for(manager, started["batchId"], {"completed"})

    assert completed["plannedRuns"] == 150
    assert completed["completedRuns"] == 150
    assert all(item["status"] == "dry_run_ready" for item in completed["attempts"])
    summary = json.loads((tmp_path / started["batchId"] / "acceptance-summary.json").read_text(encoding="utf-8"))
    assert summary["releaseStatus"] == "unverified"
    assert summary["verificationStatus"] == "dry_run_only"


def test_cancel_resume_and_retry_failed_are_persisted(tmp_path: Path) -> None:
    manager = AcceptanceBatchManager(tmp_path)
    calls = 0

    def executor(compiled, repeat):
        nonlocal calls
        calls += 1
        sleep(0.005)
        return {
            "run_id": f"run-{compiled.scenario.id}-{repeat}",
            "status": "system_error" if calls == 2 else "passed",
            "goal_status": "incomplete" if calls == 2 else "achieved",
            "completion_reason": "injected" if calls == 2 else "plan_completed",
            "steps": [], "evidence_manifest": {}, "cleanup_report": {"objects": []},
        }

    started = manager.start(compiled_catalog(), executor, dry_run=False)
    while manager.read(started["batchId"])["completedRuns"] < 3:
        sleep(0.005)
    manager.cancel(started["batchId"])
    cancelled = wait_for(manager, started["batchId"], {"cancelled"})
    assert cancelled["completedRuns"] < 150

    manager.resume(started["batchId"])
    completed = wait_for(manager, started["batchId"], {"completed"})
    assert completed["completedRuns"] == 150
    assert any(item["status"] == "system_error" for item in completed["attempts"])

    manager.retry_failed(started["batchId"])
    retried = wait_for(manager, started["batchId"], {"completed"})
    assert all(item["status"] == "passed" for item in retried["attempts"])
