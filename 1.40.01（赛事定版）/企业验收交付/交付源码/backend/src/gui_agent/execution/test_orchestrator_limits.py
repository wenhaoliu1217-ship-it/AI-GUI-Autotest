from threading import Event
import time

import pytest

from gui_agent.domain.models import ActionType, Step, TestPlan as Plan
from gui_agent.execution.orchestrator import ActiveRunConflict, RunOrchestrator, _supervisor_deadline
from gui_agent.execution.runner import RunnerConfig


def test_supervisor_deadline_tracks_rendering_pause_without_charging_it() -> None:
    config = RunnerConfig(max_duration_seconds=30, rendering_wait_grace_seconds=600)
    started = 100.0

    active = _supervisor_deadline(
        started, config,
        {"renderingWaitActive": True, "renderingPauseSeconds": 0},
    )
    finished = _supervisor_deadline(
        started, config,
        {
            "renderingWaitActive": False,
            "excludedWaitSeconds": 90,
            "renderingPauseSeconds": 80,
        },
    )

    assert active == 730.0
    assert finished == 220.0


def test_default_container_cancel_grace_covers_blocking_model_requests() -> None:
    assert RunnerConfig().isolation_cancel_grace_seconds >= 90.0


def test_blocking_run_extends_deadline_for_completed_excluded_waits(
    tmp_path, monkeypatch
) -> None:
    plan = Plan(
        name="rendering pause",
        base_url="https://ion.cesium.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/")],
    )
    config = RunnerConfig(
        artifacts_root=tmp_path,
        max_duration_seconds=1,
        rendering_wait_grace_seconds=0,
        isolation_cancel_grace_seconds=0,
    )
    orchestrator = RunOrchestrator(runner=lambda *_args: None, isolated=False)
    run_id = "dynamic-blocking-deadline"
    states = iter([
        {
            "run_id": run_id,
            "status": "running",
            "excludedWaitSeconds": 20,
            "renderingPauseSeconds": 20,
        },
        {"run_id": run_id, "status": "passed"},
    ])

    def start(_plan, _config):
        orchestrator._jobs[run_id] = (Event(), Event())
        return {"run_id": run_id, "status": "queued"}

    def read(_run_id, _artifacts_root):
        state = next(states)
        if state["status"] == "passed":
            orchestrator._jobs.pop(run_id, None)
        return state

    clock = iter([0.0, 1.0, 12.0])
    monkeypatch.setattr("gui_agent.execution.orchestrator.monotonic", lambda: next(clock))
    monkeypatch.setattr("gui_agent.execution.orchestrator.sleep", lambda _seconds: None)
    monkeypatch.setattr(orchestrator, "start", start)
    monkeypatch.setattr(orchestrator, "read", read)

    result = orchestrator.run_blocking(plan, config)

    assert result["status"] == "passed"


class FakeContainerHandle:
    container_name = "ai-gui-test"
    image = "ai-gui-runner:test"
    network_mode = "bridge"
    private_network_allowed = False

    def __init__(self, run_dir) -> None:
        self.run_dir = run_dir


def test_resource_limit_is_reported_as_started_but_incomplete(tmp_path) -> None:
    run_id = "run-limit-test"
    config = RunnerConfig(artifacts_root=tmp_path)
    orchestrator = RunOrchestrator(runner=lambda *_args: None, isolated=False)
    orchestrator._write_state(
        tmp_path,
        run_id,
        {
            "run_id": run_id,
            "status": "running",
            "goal_status": "in_progress",
            "steps": [{"index": 1, "status": "passed"}],
        },
    )

    orchestrator._write_container_failure(
        run_id,
        config,
        FakeContainerHandle(tmp_path / run_id),
        "runner_resource_limit_exceeded",
        "Runner exceeded its bounded runtime",
        True,
    )

    result = orchestrator.read(run_id, tmp_path)
    assert result is not None
    assert result["status"] == "incomplete"
    assert result["goal_status"] == "incomplete"
    assert result["result_classification"] == "agent_incomplete"
    assert "已经开始并执行了动作" in result["goal_summary"]
    assert result["runtime_limit_seconds"] == 600
    assert result["runner_isolation"]["forced_termination"] is True


def test_target_connection_refused_is_not_reported_as_runner_crash(tmp_path) -> None:
    run_id = "target-unreachable"
    config = RunnerConfig(artifacts_root=tmp_path)
    orchestrator = RunOrchestrator(runner=lambda *_args: None, isolated=False)
    orchestrator._write_state(
        tmp_path,
        run_id,
        {
            "run_id": run_id,
            "status": "running",
            "goal_status": "in_progress",
            "steps": [],
        },
    )

    orchestrator._write_container_failure(
        run_id,
        config,
        FakeContainerHandle(tmp_path / run_id),
        "container_runner_exception",
        "Error: Page.goto: net::ERR_CONNECTION_REFUSED at http://192.168.31.218:7991/",
        False,
    )

    result = orchestrator.read(run_id, tmp_path)
    assert result is not None
    assert result["status"] == "incomplete"
    assert result["goal_status"] == "incomplete"
    assert result["completion_reason"] == "target_application_unreachable"
    assert result["result_classification"] == "target_environment_unavailable"
    assert "Agent 尚未执行页面业务动作" in result["goal_summary"]


def test_same_origin_cannot_start_two_active_runs(tmp_path) -> None:
    gate = Event()

    def blocked_runner(*_args):
        gate.wait(2)
        raise RuntimeError("test runner stopped")

    plan = Plan(
        name="origin conflict",
        base_url="https://ion.cesium.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/")],
    )
    config = RunnerConfig(artifacts_root=tmp_path, max_duration_seconds=2)
    orchestrator = RunOrchestrator(runner=blocked_runner, isolated=False)
    first = orchestrator.start(plan, config)

    with pytest.raises(ActiveRunConflict) as error:
        orchestrator.start(plan, config)
    assert error.value.run_id == first["run_id"]

    gate.set()
    state = orchestrator.read(first["run_id"], tmp_path)
    for _ in range(100):
        if state and state.get("status") not in {"queued", "running"}:
            break
        gate.set()
        time.sleep(0.01)
        state = orchestrator.read(first["run_id"], tmp_path)

    # The activity key is released after the failed runner exits.
    second = orchestrator.start(plan, config)
    assert second["run_id"] != first["run_id"]
    gate.set()


def test_runner_exception_always_persists_diagnostic_bundle(tmp_path) -> None:
    def broken_runner(*_args):
        raise RuntimeError("browser target closed")

    plan = Plan(
        name="diagnostic persistence",
        base_url="https://ion.cesium.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/")],
    )
    orchestrator = RunOrchestrator(runner=broken_runner, isolated=False)
    started = orchestrator.start(plan, RunnerConfig(artifacts_root=tmp_path))
    run_dir = tmp_path / started["run_id"]

    for _ in range(100):
        if (run_dir / "artifact-finalization.json").is_file():
            break
        time.sleep(0.01)

    assert (run_dir / "run.json").is_file()
    assert (run_dir / "report.md").is_file()
    assert (run_dir / "report.html").is_file()
    assert (run_dir / "completion-gate.json").is_file()
    assert (run_dir / "artifact-finalization.json").is_file()
    assert "browser target closed" in (run_dir / "run.json").read_text(encoding="utf-8")


def test_final_payload_without_activity_key_releases_the_claim(tmp_path) -> None:
    class FinalResult:
        def __init__(self, run_id: str) -> None:
            self.run_id = run_id

        def model_dump(self, **_kwargs) -> dict:
            return {
                "run_id": self.run_id,
                "status": "passed",
                "completion_reason": "completed",
            }

    def terminal_runner(_plan, config):
        return FinalResult(config.run_id), tmp_path / str(config.run_id)

    plan = Plan(
        name="metadata preservation",
        base_url="https://ion.cesium.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/")],
    )
    config = RunnerConfig(artifacts_root=tmp_path)
    orchestrator = RunOrchestrator(runner=terminal_runner, isolated=False)
    first = orchestrator.start(plan, config)

    for _ in range(100):
        state = orchestrator.read(first["run_id"], tmp_path)
        if state and state.get("status") == "passed":
            break
        time.sleep(0.01)

    assert state is not None
    assert state["activity_key"] == first["activity_key"]
    second = orchestrator.start(plan, config)
    assert second["run_id"] != first["run_id"]


def test_blocking_run_waits_past_terminal_progress_until_job_finalizes(tmp_path) -> None:
    class FinalResult:
        def __init__(self, run_id: str) -> None:
            self.run_id = run_id

        def model_dump(self, **_kwargs) -> dict:
            return {
                "run_id": self.run_id,
                "status": "passed",
                "goal_status": "achieved",
                "goal_summary": "CompletionGate 已通过",
                "completion_reason": "plan_completed",
                "completion_gate": {"reasons": []},
                "result_classification": "fixed_passed",
            }

    orchestrator = None

    def terminal_runner(_plan, config):
        assert orchestrator is not None
        orchestrator._write_state(
            tmp_path,
            str(config.run_id),
            {
                "run_id": str(config.run_id),
                "status": "passed",
                "goal_status": "in_progress",
                "completion_reason": "plan_completed",
            },
        )
        time.sleep(0.1)
        return FinalResult(str(config.run_id)), tmp_path / str(config.run_id)

    plan = Plan(
        name="terminal progress race",
        base_url="https://ion.cesium.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/")],
    )
    config = RunnerConfig(artifacts_root=tmp_path, max_duration_seconds=2)
    orchestrator = RunOrchestrator(runner=terminal_runner, isolated=False)

    result = orchestrator.run_blocking(plan, config)

    assert result["status"] == "passed"
    assert result["goal_status"] == "achieved"
    assert result["completion_gate"] == {"reasons": []}
    assert result["result_classification"] == "fixed_passed"


def test_terminal_persisted_state_heals_a_stale_activity_claim(tmp_path) -> None:
    gate = Event()

    def blocked_runner(*_args):
        gate.wait(2)
        raise RuntimeError("test runner stopped")

    plan = Plan(
        name="stale claim",
        base_url="https://ion.cesium.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/")],
    )
    config = RunnerConfig(
        artifacts_root=tmp_path,
        project_id="project-1",
        environment_id="default",
    )
    orchestrator = RunOrchestrator(runner=blocked_runner, isolated=False)
    activity_key = orchestrator._activity_key(plan, config)
    stale_run_id = "stale-terminal-run"
    orchestrator._active_keys[activity_key] = stale_run_id
    orchestrator._write_state(
        tmp_path,
        stale_run_id,
        {"run_id": stale_run_id, "status": "system_error"},
    )

    started = orchestrator.start(plan, config)

    assert started["run_id"] != stale_run_id
    assert orchestrator._active_keys[activity_key] == started["run_id"]
    gate.set()


def test_live_job_remains_blocked_even_if_persisted_state_is_terminal(tmp_path) -> None:
    plan = Plan(
        name="live claim",
        base_url="https://ion.cesium.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/")],
    )
    config = RunnerConfig(artifacts_root=tmp_path, project_id="project-1")
    orchestrator = RunOrchestrator(runner=lambda *_args: None, isolated=False)
    activity_key = orchestrator._activity_key(plan, config)
    live_run_id = "live-run"
    orchestrator._active_keys[activity_key] = live_run_id
    orchestrator._jobs[live_run_id] = (Event(), Event())
    orchestrator._write_state(
        tmp_path,
        live_run_id,
        {"run_id": live_run_id, "status": "system_error"},
    )

    with pytest.raises(ActiveRunConflict) as error:
        orchestrator.start(plan, config)

    assert error.value.run_id == live_run_id


def test_restart_reconciliation_preserves_checkpoint_and_writes_capsule(tmp_path) -> None:
    orchestrator = RunOrchestrator(runner=lambda *_args: None, isolated=False)
    run_id = "orphaned-run"
    orchestrator._write_state(
        tmp_path,
        run_id,
        {
            "run_id": run_id,
            "base_url_summary": "https://example.test",
            "status": "running",
            "goal_status": "in_progress",
            "steps": [{"index": 3, "action": "click", "status": "passed"}],
        },
    )
    (tmp_path / run_id / "events.jsonl").write_text(
        '{"type":"step_passed","index":3}\n', encoding="utf-8"
    )

    reconciled = orchestrator.reconcile(tmp_path)

    assert reconciled == [run_id]
    state = orchestrator.read(run_id, tmp_path)
    assert state is not None
    assert state["status"] == "incomplete"
    assert state["completion_reason"] == "runner_restart_reconciled"
    assert state["recoverable"] is True
    assert state["steps"][0]["index"] == 3
    capsule = tmp_path / run_id / "recovery-capsule.json"
    assert capsule.is_file()


def test_late_empty_snapshot_does_not_erase_completed_steps(tmp_path) -> None:
    orchestrator = RunOrchestrator(runner=lambda *_args: None, isolated=False)
    run_id = "preserve-steps"
    orchestrator._write_state(
        tmp_path,
        run_id,
        {
            "run_id": run_id,
            "status": "running",
            "steps": [{"index": 1, "action": "navigate", "status": "passed"}],
        },
    )
    orchestrator._write_state(
        tmp_path,
        run_id,
        {"run_id": run_id, "status": "incomplete", "steps": [], "assertions": []},
    )

    state = orchestrator.read(run_id, tmp_path)
    assert state is not None
    assert state["steps"][0]["index"] == 1
    journal = tmp_path.parent / "data" / "experience-journal" / "runs" / f"{run_id}.json"
    assert journal.is_file()
