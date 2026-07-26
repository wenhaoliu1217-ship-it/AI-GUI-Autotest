import json
import os
import time
import pytest
from datetime import datetime
from pathlib import Path
from threading import Event

from gui_agent.domain.models import ActionType, Locator, Step, TestPlan as ExecutionPlan
from gui_agent.domain.results import RunResult, Status
from gui_agent.execution.orchestrator import RunOrchestrator
from gui_agent.execution.runner import RunnerConfig


def _isolated_probe_runner(plan: ExecutionPlan, config: RunnerConfig):
    started = datetime.now().astimezone()
    Path("runner-process.txt").write_text(str(os.getpid()), encoding="utf-8")
    Path(os.environ["TMP"]).joinpath("temp-probe.txt").write_text("isolated", encoding="utf-8")
    Path("environment-probe.json").write_text(json.dumps({
        "allowed_secret": os.environ.get("QA_PASSWORD"),
        "unrelated_secret_present": "HOST_PRIVATE_TOKEN" in os.environ,
    }), encoding="utf-8")
    result = RunResult(
        run_id=config.run_id or "missing",
        plan_name=plan.name,
        base_url_summary=plan.base_url,
        status=Status.PASSED,
        started_at=started,
        ended_at=datetime.now().astimezone(),
        completion_reason="plan_completed",
    )
    return result, Path.cwd()


def _stubborn_isolated_runner(plan: ExecutionPlan, config: RunnerConfig):
    time.sleep(30)
    return _isolated_probe_runner(plan, config)


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


def test_orchestrator_falls_back_while_container_final_report_is_incomplete(
    tmp_path: Path,
) -> None:
    run_id = "run-container-write-race"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "run.json").write_text("", encoding="utf-8")
    RunOrchestrator._write_state(tmp_path, run_id, {
        "run_id": run_id,
        "status": "running",
        "started_at": datetime.now().astimezone().isoformat(),
    })

    recovered = RunOrchestrator().read(run_id, tmp_path)

    assert recovered["run_id"] == run_id
    assert recovered["status"] == "system_error"
    assert recovered["completion_reason"] == "runner_process_interrupted"


def test_default_orchestrator_runs_in_spawned_process_with_bounded_directories(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("QA_PASSWORD", "allowed-runtime-secret")
    monkeypatch.setenv("HOST_PRIVATE_TOKEN", "must-not-reach-runner")
    orchestrator = RunOrchestrator(runner=_isolated_probe_runner, isolated=True)

    final = orchestrator.run_blocking(
        _plan(), RunnerConfig(
            artifacts_root=tmp_path,
            max_duration_seconds=10,
            secret_refs=(("LOGIN_PASSWORD", "QA_PASSWORD"),),
        )
    )

    run_dir = tmp_path / final["run_id"]
    child_pid = int((run_dir / "runner-process.txt").read_text(encoding="utf-8"))
    assert final["status"] == "passed"
    assert child_pid != os.getpid()
    assert final["runner_isolation"]["mode"] == "spawn_process"
    assert final["runner_isolation"]["process_id"] == child_pid
    assert final["runner_isolation"]["working_directory"] == str(run_dir.resolve())
    assert final["runner_isolation"]["temp_directory"] == str((run_dir / "_runner_tmp").resolve())
    assert (run_dir / "_runner_tmp" / "temp-probe.txt").read_text(encoding="utf-8") == "isolated"
    environment_probe = json.loads((run_dir / "environment-probe.json").read_text(encoding="utf-8"))
    assert environment_probe == {
        "allowed_secret": "allowed-runtime-secret",
        "unrelated_secret_present": False,
    }
    if os.name == "nt":
        assert final["runner_isolation"]["windows_job_assigned"] is True


def test_isolated_orchestrator_force_terminates_after_cancel_grace(tmp_path: Path) -> None:
    orchestrator = RunOrchestrator(runner=_stubborn_isolated_runner, isolated=True)
    queued = orchestrator.start(
        _plan(),
        RunnerConfig(
            artifacts_root=tmp_path,
            max_duration_seconds=30,
            isolation_cancel_grace_seconds=0.2,
        ),
    )
    orchestrator.cancel(queued["run_id"], tmp_path)

    deadline = time.time() + 5
    final = orchestrator.read(queued["run_id"], tmp_path)
    while final["status"] in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.02)
        final = orchestrator.read(queued["run_id"], tmp_path)

    assert final["status"] == "cancelled"
    assert final["completion_reason"] == "cancelled_forcibly"
    assert final["runner_isolation"]["forced_termination"] is True


def test_isolated_orchestrator_enforces_parent_wall_clock_limit(tmp_path: Path) -> None:
    orchestrator = RunOrchestrator(runner=_stubborn_isolated_runner, isolated=True)

    final = orchestrator.run_blocking(
        _plan(),
        RunnerConfig(
            artifacts_root=tmp_path,
            max_duration_seconds=1,
            isolation_cancel_grace_seconds=0.1,
        ),
    )

    assert final["status"] == "system_error"
    assert final["completion_reason"] == "runner_resource_limit_exceeded"
    assert final["runner_isolation"]["forced_termination"] is True


@pytest.mark.parametrize(("decision", "expected_status"), [("approved", "passed"), ("rejected", "cancelled")])
def test_orchestrator_confirmation_is_bound_and_single_use(
    tmp_path: Path, decision: str, expected_status: str,
) -> None:
    dangerous = Step(action=ActionType.CLICK, locator=Locator(role="button", name="删除客户"), description="删除客户")

    def fake_runner(plan: ExecutionPlan, config: RunnerConfig):
        started = datetime.now().astimezone()
        assert config.confirmation_callback is not None
        approved = config.confirmation_callback(dangerous, 1, "删除")
        result = RunResult(
            run_id=config.run_id or "missing",
            plan_name=plan.name,
            base_url_summary=plan.base_url,
            status=Status.PASSED if approved else Status.CANCELLED,
            started_at=started,
            ended_at=datetime.now().astimezone(),
            completion_reason="plan_completed" if approved else "dangerous_action_rejected",
            confirmation_history=list(config.confirmation_history),
        )
        return result, tmp_path / result.run_id

    orchestrator = RunOrchestrator(runner=fake_runner)
    queued = orchestrator.start(_plan(), RunnerConfig(artifacts_root=tmp_path))
    run_id = queued["run_id"]
    deadline = time.time() + 2
    state = orchestrator.read(run_id, tmp_path)
    while state["status"] != "pending_confirmation" and time.time() < deadline:
        time.sleep(0.01)
        state = orchestrator.read(run_id, tmp_path)
    confirmation_id = state["pending_confirmation"]["id"]

    orchestrator.confirm(run_id, tmp_path, confirmation_id, decision, "tester")
    deadline = time.time() + 2
    final = orchestrator.read(run_id, tmp_path)
    while final["status"] in {"running", "pending_confirmation"} and time.time() < deadline:
        time.sleep(0.01)
        final = orchestrator.read(run_id, tmp_path)

    assert final["status"] == expected_status
    assert final["confirmation_history"][0]["decision"] == decision
    assert final["confirmation_history"][0]["actor"] == "tester"
    with pytest.raises(RuntimeError, match="没有待确认"):
        orchestrator.confirm(run_id, tmp_path, confirmation_id, decision, "tester")
