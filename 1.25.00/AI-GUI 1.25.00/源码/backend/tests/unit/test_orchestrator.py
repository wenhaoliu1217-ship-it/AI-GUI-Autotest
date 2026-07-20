import time
from datetime import datetime
from pathlib import Path
from threading import Event

from gui_agent.domain.models import ActionType, Step, TestPlan as ExecutionPlan
from gui_agent.domain.results import RunResult, Status
from gui_agent.execution.orchestrator import RunOrchestrator
from gui_agent.execution.runner import RunnerConfig


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        name="可取消运行",
        base_url="https://example.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/")],
    )


def test_orchestrator_persists_progress_and_cancels_active_run(tmp_path: Path) -> None:
    running_seen = Event()

    def fake_runner(plan: ExecutionPlan, config: RunnerConfig):
        started = datetime.now().astimezone()
        assert config.cancel_event is not None
        assert config.progress_callback is not None
        config.progress_callback({
            "run_id": config.run_id,
            "plan_name": plan.name,
            "role": None,
            "base_url_summary": plan.base_url,
            "status": "running",
            "started_at": started.isoformat(),
            "ended_at": started.isoformat(),
            "steps": [],
            "assertions": [],
            "reproduction_steps": [],
            "cause_hints": [],
            "findings": [],
            "completion_reason": "running",
        })
        running_seen.set()
        assert config.cancel_event.wait(2)
        result = RunResult(
            run_id=config.run_id or "missing",
            plan_name=plan.name,
            base_url_summary=plan.base_url,
            status=Status.CANCELLED,
            started_at=started,
            ended_at=datetime.now().astimezone(),
            completion_reason="cancelled_by_user",
        )
        return result, tmp_path / result.run_id

    orchestrator = RunOrchestrator(runner=fake_runner)
    queued = orchestrator.start(_plan(), RunnerConfig(artifacts_root=tmp_path))
    run_id = queued["run_id"]

    assert running_seen.wait(1)
    assert orchestrator.read(run_id, tmp_path)["status"] == "running"
    requested = orchestrator.cancel(run_id, tmp_path)
    assert requested["cancellation_requested"] is True

    deadline = time.time() + 2
    while orchestrator.read(run_id, tmp_path)["status"] != "cancelled" and time.time() < deadline:
        time.sleep(0.01)
    final = orchestrator.read(run_id, tmp_path)
    assert final["status"] == "cancelled"
    assert final["completion_reason"] == "cancelled_by_user"
    assert (tmp_path / run_id / "run-state.json").is_file()


def test_orchestrator_marks_abandoned_active_state_as_system_error(tmp_path: Path) -> None:
    orchestrator = RunOrchestrator()
    run_id = "run-abandoned"
    RunOrchestrator._write_state(tmp_path, run_id, {
        "run_id": run_id,
        "status": "running",
        "started_at": datetime.now().astimezone().isoformat(),
    })

    recovered = orchestrator.read(run_id, tmp_path)

    assert recovered["status"] == "system_error"
    assert recovered["completion_reason"] == "runner_process_interrupted"
