"""Persistent background run orchestration for the local single-user runner."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, replace
from datetime import datetime
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, sleep
from typing import Callable
from urllib.parse import urlparse
from uuid import uuid4

from ..domain.models import TestPlan
from ..domain.results import Status
from ..domain.results import RunResult
from ..artifacts.report import write_reports
from ..planning.experience_store import ExperienceJournal, SuccessExperienceStore
from .runner import RunnerConfig, run_plan
from .isolation import WindowsJob, isolated_worker
from .container_runtime import DockerRunHandle, RUNNER_TMPFS_TOTAL_MB


Runner = Callable[[TestPlan, RunnerConfig], tuple[object, Path]]
ACTIVE_STATUSES = {
    Status.QUEUED.value,
    Status.RUNNING.value,
    Status.PENDING_CONFIRMATION.value,
    Status.WAITING_FOR_CLARIFICATION.value,
}
DEFAULT_MIN_ARTIFACT_FREE_MB = 2_048
_TARGET_CONNECTIVITY_MARKERS = (
    "net::ERR_CONNECTION_REFUSED",
    "net::ERR_CONNECTION_TIMED_OUT",
    "net::ERR_NAME_NOT_RESOLVED",
    "net::ERR_ADDRESS_UNREACHABLE",
    "net::ERR_INTERNET_DISCONNECTED",
)


def _supervisor_deadline(
    started: float,
    config: RunnerConfig,
    payload: dict | None = None,
) -> float:
    """Mirror child budget and allow an active render wait to finish."""
    payload = payload or {}
    limit = float(config.max_duration_seconds or 600)
    paused = max(
        0.0,
        float(payload.get("excludedWaitSeconds") or 0.0),
        float(payload.get("renderingPauseSeconds") or 0.0),
    )
    active = bool(payload.get("renderingWaitActive"))
    grace = float(config.rendering_wait_grace_seconds) if active else 0.0
    return started + limit + paused + grace


def _is_target_connectivity_failure(message: str) -> bool:
    return any(marker in str(message or "") for marker in _TARGET_CONNECTIVITY_MARKERS)


class ActiveRunConflict(RuntimeError):
    """Raised when the same project/environment already has an active run."""

    def __init__(self, activity_key: str, run_id: str) -> None:
        self.activity_key = activity_key
        self.run_id = run_id
        super().__init__(f"活动测试已存在：{run_id}")


@dataclass
class IsolatedJob:
    cancel_event: object
    supervisor: Thread
    process: object
    connection: Connection
    windows_job: WindowsJob
    cancel_requested_at: float | None = None


@dataclass
class ContainerJob:
    handle: DockerRunHandle
    supervisor: Thread
    cancel_requested_at: float | None = None
    cancel_acknowledged_at: float | None = None
    cancellation_acknowledged: bool = False


class RunOrchestrator:
    _state_lock = Lock()

    def __init__(
        self,
        runner: Runner = run_plan,
        *,
        isolated: bool | None = None,
        runner_mode: str | None = None,
    ) -> None:
        self._runner = runner
        self._isolated = runner is run_plan if isolated is None else isolated
        default_mode = "process" if self._isolated else "thread"
        self._runner_mode = runner_mode or os.getenv("GUI_RUNNER_MODE", default_mode)
        if self._runner_mode not in {"thread", "process", "container"}:
            raise ValueError(f"不支持的 Runner 模式：{self._runner_mode}")
        if self._runner_mode == "container" and runner is not run_plan:
            raise ValueError("容器模式不接受注入的本地 runner")
        self._jobs: dict[str, tuple[Event, Thread] | IsolatedJob | ContainerJob] = {}
        self._confirmations: dict[str, dict] = {}
        self._clarifications: dict[str, dict] = {}
        self._login_requests: dict[str, dict] = {}
        self._active_keys: dict[str, str] = {}
        self._lock = Lock()

    def reconcile(self, artifacts_root: Path) -> list[str]:
        """Finalize runs left active by a service/container restart.

        The in-memory job registry is empty after a process restart.  Any
        persisted active state is therefore an interrupted run, not a live
        run.  Reconcile it eagerly so the UI never hides it as perpetually
        running and the last checkpoint remains queryable.
        """
        root = Path(artifacts_root)
        reconciled: list[str] = []
        if not root.is_dir():
            return reconciled
        for run_dir in root.iterdir():
            if not run_dir.is_dir():
                continue
            run_id = run_dir.name
            with self._lock:
                if run_id in self._jobs:
                    continue
            state_path = run_dir / "run-state.json"
            if not state_path.is_file():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if state.get("status") not in ACTIVE_STATUSES:
                continue
            self._write_recovery_capsule(
                run_id,
                artifacts_root,
                "runner_restart_reconciled",
                forced=False,
            )
            self._mark_interrupted(
                run_id,
                artifacts_root,
                state,
                reason="runner_restart_reconciled",
            )
            reconciled.append(run_id)
        return reconciled

    def start(self, plan: TestPlan, config: RunnerConfig) -> dict:
        _ensure_artifact_capacity(config.artifacts_root)
        started = datetime.now().astimezone()
        run_id = f"{started:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        activity_key = self._activity_key(plan, config)
        self._claim_activity(activity_key, run_id, config.artifacts_root)
        initial = {
            "run_id": run_id,
            "plan_name": plan.name,
            "role": plan.role,
            "base_url_summary": plan.base_url,
            "status": Status.QUEUED.value,
            "started_at": started.isoformat(),
            "ended_at": started.isoformat(),
            "steps": [],
            "assertions": [],
            "failed_step_index": None,
            "reproduction_steps": [],
            "cause_hints": [],
            "findings": [],
            "replay_mode": config.replay_mode,
            "onboarding_level": config.onboarding_level,
            "stability_level": "A",
            "completion_reason": "queued",
            "project_id": config.project_id,
            "environment_id": config.environment_id,
            "activity_key": activity_key,
            "environment_updated_at": config.environment_updated_at,
            "artifact_retention_days": config.artifact_retention_days,
            "scenario_id": config.scenario_id,
            "scenario_updated_at": config.scenario_updated_at,
            "scenario_goal": config.scenario_goal or plan.name,
            "goal_status": "in_progress",
            "goal_summary": "运行已排队",
            "model_calls": 0,
            "estimated_cost": None,
            "pending_confirmation": None,
            "confirmation_history": [],
            "pending_clarification": None,
            "clarification_history": [],
            "runner_isolation": {
                "mode": {
                    "container": "docker_container",
                    "process": "spawn_process",
                    "thread": "in_process_thread",
                }[self._runner_mode],
                "memory_limit_mb": config.isolation_memory_limit_mb if self._runner_mode != "thread" else None,
                "network_policy": "playwright_request_guard",
            },
        }
        try:
            self._write_state(config.artifacts_root, run_id, initial)
            if self._runner_mode == "container":
                return self._start_container(run_id, plan, config, initial)
            if self._runner_mode == "process":
                return self._start_isolated(run_id, plan, config, initial)
            cancel_event = Event()
            thread = Thread(
                target=self._execute,
                args=(run_id, plan, config, cancel_event, activity_key),
                name=f"gui-run-{run_id}",
                daemon=True,
            )
            with self._lock:
                self._jobs[run_id] = (cancel_event, thread)
            thread.start()
            return initial
        except Exception:
            self._release_activity(activity_key, run_id)
            raise

    def run_blocking(self, plan: TestPlan, config: RunnerConfig) -> dict:
        state = self.start(plan, config)
        run_id = state["run_id"]
        started = monotonic()
        deadline = (
            _supervisor_deadline(started, config, state)
            + config.isolation_cancel_grace_seconds
            + 10
        )
        while monotonic() < deadline:
            sleep(0.02)
            state = self.read(run_id, config.artifacts_root) or state
            deadline = (
                _supervisor_deadline(started, config, state)
                + config.isolation_cancel_grace_seconds
                + 10
            )
            with self._lock:
                job_active = run_id in self._jobs
            # A Runner may publish a terminal-looking progress snapshot before
            # its completion gate, evidence manifest, and final classification
            # are persisted. The supervising job is the authoritative signal
            # that no more finalization writes remain.
            if not job_active and state.get("status") not in ACTIVE_STATUSES:
                break
        return state

    def cancel(self, run_id: str, artifacts_root: Path) -> dict:
        with self._lock:
            job = self._jobs.get(run_id)
        if job is None:
            state = self.read(run_id, artifacts_root)
            if state is None:
                raise KeyError(run_id)
            if state.get("status") not in ACTIVE_STATUSES:
                raise RuntimeError("运行已经结束，不能取消")
            return self._mark_interrupted(run_id, artifacts_root, state)
        state = self.read(run_id, artifacts_root) or {}
        requested_at = monotonic()
        state["cancellation_requested"] = True
        state["cancellation_requested_at"] = datetime.now().astimezone().isoformat()
        state["completion_reason"] = "cancellation_requested"
        self._write_state(artifacts_root, run_id, state)
        if isinstance(job, ContainerJob):
            job.handle.send({"type": "cancel"})
            job.cancel_requested_at = requested_at
        elif isinstance(job, IsolatedJob):
            job.cancel_event.set()
            job.cancel_requested_at = monotonic()
        else:
            job[0].set()
        with self._lock:
            confirmation = self._confirmations.get(run_id)
            if confirmation is not None:
                confirmation["decision"] = "rejected"
                confirmation["actor"] = "cancel_request"
                if "connection" in confirmation:
                    confirmation["connection"].send({
                        "type": "confirmation_decision",
                        "id": confirmation["id"],
                        "decision": "rejected",
                        "actor": "cancel_request",
                    })
                else:
                    confirmation["event"].set()
            clarification = self._clarifications.get(run_id)
            if clarification is not None:
                clarification["answer"] = None
                clarification["actor"] = "cancel_request"
                if "connection" in clarification:
                    clarification["connection"].send({
                        "type": "clarification_answer",
                        "id": clarification["id"],
                        "answer": None,
                        "actor": "cancel_request",
                    })
                else:
                    clarification["event"].set()
        return state

    def login_control(self, run_id: str, clarification_id: str, command: dict) -> dict:
        request_id = uuid4().hex
        entry = {"event": Event(), "result": None, "run_id": run_id}
        with self._lock:
            pending = self._clarifications.get(run_id)
            if (not pending or pending["id"] != clarification_id
                    or not pending.get("question", "").startswith("【需要手动登录】")
                    or pending.get("answer") is not None or "connection" not in pending):
                raise RuntimeError("登录接管已结束或当前运行不支持交互登录")
            self._login_requests[request_id] = entry
            try:
                pending["connection"].send({"type": "login_control", "id": clarification_id,
                    "request_id": request_id, "command": command})
            except Exception:
                self._login_requests.pop(request_id, None)
                raise RuntimeError("登录接管连接已关闭") from None
        try:
            if not entry["event"].wait(7):
                raise RuntimeError("登录窗口响应超时，请重试")
            return entry["result"]
        finally:
            with self._lock:
                self._login_requests.pop(request_id, None)

    def _receive_login_control(self, run_id: str, message: dict) -> None:
        with self._lock:
            entry = self._login_requests.get(message.get("request_id"))
            if entry is not None and entry["run_id"] == run_id:
                entry["result"] = message["payload"]
                entry["event"].set()

    def answer_clarification(
        self,
        run_id: str,
        artifacts_root: Path,
        clarification_id: str,
        answer: str,
        actor: str,
    ) -> dict:
        normalized = answer.strip()
        if not normalized:
            raise ValueError("澄清回答不能为空")
        with self._lock:
            pending = self._clarifications.get(run_id)
            if pending is None:
                raise RuntimeError("运行当前没有待回答的澄清问题")
            if pending["id"] != clarification_id:
                raise RuntimeError("澄清编号与当前问题不匹配")
            if pending.get("answer") is not None:
                raise RuntimeError("该澄清问题已经回答")
            pending["answer"] = normalized
            pending["actor"] = actor or "local_user"
            if "connection" in pending:
                pending["connection"].send({
                    "type": "clarification_answer",
                    "id": clarification_id,
                    "answer": normalized,
                    "actor": pending["actor"],
                })
            else:
                pending["event"].set()
        deadline = monotonic() + 2
        state = self.read(run_id, artifacts_root) or {}
        while state.get("status") == Status.WAITING_FOR_CLARIFICATION.value and monotonic() < deadline:
            sleep(0.02)
            state = self.read(run_id, artifacts_root) or state
        return state

    def confirm(
        self,
        run_id: str,
        artifacts_root: Path,
        confirmation_id: str,
        decision: str,
        actor: str,
    ) -> dict:
        if decision not in {"approved", "rejected"}:
            raise ValueError("确认决定必须为 approved 或 rejected")
        with self._lock:
            pending = self._confirmations.get(run_id)
            if pending is None:
                raise RuntimeError("运行当前没有待确认动作")
            if pending["id"] != confirmation_id:
                raise RuntimeError("确认编号与当前待确认动作不匹配")
            if pending.get("decision") is not None:
                raise RuntimeError("该确认已经处理，不能重复使用")
            pending["decision"] = decision
            pending["actor"] = actor or "local_user"
            if "connection" in pending:
                pending["connection"].send({
                    "type": "confirmation_decision",
                    "id": confirmation_id,
                    "decision": decision,
                    "actor": pending["actor"],
                })
            else:
                pending["event"].set()
        deadline = monotonic() + 2
        state = self.read(run_id, artifacts_root) or {}
        while state.get("status") == Status.PENDING_CONFIRMATION.value and monotonic() < deadline:
            sleep(0.02)
            state = self.read(run_id, artifacts_root) or state
        return state

    def read(self, run_id: str, artifacts_root: Path) -> dict | None:
        run_dir = Path(artifacts_root) / run_id
        final_path = run_dir / "run.json"
        state_path = run_dir / "run-state.json"
        candidates = [path for path in (final_path, state_path) if path.is_file()]
        target = max(candidates, key=lambda path: path.stat().st_mtime) if candidates else state_path
        if not target.is_file():
            return None
        with RunOrchestrator._state_lock:
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                if target == state_path or not state_path.is_file():
                    raise
                data = json.loads(state_path.read_text(encoding="utf-8"))
                target = state_path
        with self._lock:
            current_job = self._jobs.get(run_id)
        if (
            isinstance(current_job, (IsolatedJob, ContainerJob))
            and target == final_path
            and not data.get("runner_isolation")
            and state_path.is_file()
        ):
            with RunOrchestrator._state_lock:
                data = json.loads(state_path.read_text(encoding="utf-8"))
            target = state_path
        if (
            isinstance(current_job, (IsolatedJob, ContainerJob))
            and data.get("status") not in ACTIVE_STATUSES
            and not data.get("runner_isolation")
        ):
            data["status"] = Status.RUNNING.value
            data["completion_reason"] = "isolated_runner_finalizing"
        if target == state_path and data.get("status") in ACTIVE_STATUSES:
            with self._lock:
                active = run_id in self._jobs
            if not active:
                data = self._mark_interrupted(run_id, artifacts_root, data)
        return data

    def list(self, artifacts_root: Path) -> list[dict]:
        root = Path(artifacts_root)
        items: list[tuple[float, dict]] = []
        for run_dir in root.glob("*"):
            if not run_dir.is_dir():
                continue
            candidates = [
                path for path in (run_dir / "run.json", run_dir / "run-state.json")
                if path.is_file()
            ]
            if not candidates:
                continue
            target = max(candidates, key=lambda path: path.stat().st_mtime)
            try:
                data = self.read(run_dir.name, root)
                if data is not None:
                    items.append((target.stat().st_mtime, data))
            except (OSError, json.JSONDecodeError):
                continue
        return [data for _, data in sorted(items, key=lambda item: item[0], reverse=True)]

    def _execute(
        self,
        run_id: str,
        plan: TestPlan,
        config: RunnerConfig,
        cancel_event: Event,
        activity_key: str,
    ) -> None:
        try:
            effective = replace(
                config,
                run_id=run_id,
                cancel_event=cancel_event,
                progress_callback=lambda payload: self._write_state(config.artifacts_root, run_id, payload),
                confirmation_callback=lambda step, index, rule: self._wait_for_confirmation(
                    run_id, config.artifacts_root, cancel_event, config.confirmation_history,
                    step, index, rule,
                ),
                clarification_callback=lambda question, round_number: self._wait_for_clarification(
                    run_id, config.artifacts_root, cancel_event, config.clarification_history,
                    question, round_number,
                ),
            )
            result, _ = self._runner(plan, effective)
            payload = result.model_dump(mode="json")
            self._promote_success_experience(config, payload)
            self._persist_final_success_experience(config.artifacts_root, run_id, payload)
            self._write_state(config.artifacts_root, run_id, payload)
        except Exception as exc:
            state = self.read(run_id, config.artifacts_root) or {}
            state.update({
                "status": Status.SYSTEM_ERROR.value,
                "ended_at": datetime.now().astimezone().isoformat(),
                "completion_reason": "runner_exception",
                "system_error": str(exc),
            })
            self._write_state(config.artifacts_root, run_id, state)
            self._persist_failure_artifacts(config.artifacts_root, run_id, state)
        finally:
            with self._lock:
                self._jobs.pop(run_id, None)
                self._confirmations.pop(run_id, None)
                self._clarifications.pop(run_id, None)
            self._release_activity(activity_key, run_id)

    def _start_isolated(
        self, run_id: str, plan: TestPlan, config: RunnerConfig, initial: dict
    ) -> dict:
        context = get_context("spawn")
        cancel_event = context.Event()
        start_gate = context.Event()
        parent_connection, child_connection = context.Pipe(duplex=True)
        effective = replace(config, run_id=run_id)
        process = context.Process(
            target=isolated_worker,
            args=(
                plan.model_dump(mode="json"), effective, cancel_event,
                child_connection, start_gate, self._runner,
            ),
            name=f"gui-runner-{run_id}",
            daemon=False,
        )
        process.start()
        child_connection.close()
        windows_job = WindowsJob.assign(process.pid, config.isolation_memory_limit_mb)
        start_gate.set()
        supervisor = Thread(
            target=self._supervise_isolated,
            args=(
                run_id, config, process, cancel_event, parent_connection, windows_job,
                initial["activity_key"],
            ),
            name=f"gui-runner-supervisor-{run_id}",
            daemon=True,
        )
        job = IsolatedJob(
            cancel_event=cancel_event,
            supervisor=supervisor,
            process=process,
            connection=parent_connection,
            windows_job=windows_job,
        )
        with self._lock:
            self._jobs[run_id] = job
        initial["runner_isolation"].update({
            "process_id": process.pid,
            "windows_job_assigned": windows_job.assigned,
            "working_directory": str((Path(config.artifacts_root).resolve() / run_id)),
            "temp_directory": str((Path(config.artifacts_root).resolve() / run_id / "_runner_tmp")),
            "forced_termination": False,
        })
        self._write_state(config.artifacts_root, run_id, initial)
        supervisor.start()
        return initial

    def _start_container(
        self, run_id: str, plan: TestPlan, config: RunnerConfig, initial: dict
    ) -> dict:
        effective = replace(config, run_id=run_id)
        try:
            handle = DockerRunHandle(plan, effective, run_id)
        except Exception as exc:
            initial.update({
                "status": Status.SYSTEM_ERROR.value,
                "ended_at": datetime.now().astimezone().isoformat(),
                "completion_reason": "container_runner_start_failed",
                "system_error": str(exc),
            })
            self._write_state(config.artifacts_root, run_id, initial)
            self._persist_failure_artifacts(config.artifacts_root, run_id, initial)
            self._release_activity(initial["activity_key"], run_id)
            return initial
        supervisor = Thread(
            target=self._supervise_container,
            args=(run_id, config, handle, initial["activity_key"]),
            name=f"gui-container-supervisor-{run_id}",
            daemon=True,
        )
        job = ContainerJob(handle=handle, supervisor=supervisor)
        with self._lock:
            self._jobs[run_id] = job
        initial["runner_isolation"].update(self._container_isolation_state(handle, config, False))
        self._write_state(config.artifacts_root, run_id, initial)
        supervisor.start()
        return initial

    def _supervise_container(
        self,
        run_id: str,
        config: RunnerConfig,
        handle: DockerRunHandle,
        activity_key: str,
    ) -> None:
        started = monotonic()
        deadline = _supervisor_deadline(started, config)
        forced_reason: str | None = None
        final_received = False
        human_wait_started = None
        try:
            while True:
                message = handle.poll_message(0.05)
                if message is not None:
                    kind = message.get("type")
                    if kind == "progress":
                        payload = message["payload"]
                        deadline = _supervisor_deadline(started, config, payload)
                        self._write_state(config.artifacts_root, run_id, payload)
                    elif kind == "cancel_acknowledged":
                        acknowledged_at = monotonic()
                        with self._lock:
                            current = self._jobs.get(run_id)
                            if isinstance(current, ContainerJob):
                                current.cancel_acknowledged_at = acknowledged_at
                                current.cancellation_acknowledged = True
                        state = self.read(run_id, config.artifacts_root) or {}
                        state.update({
                            "cancellation_acknowledged": True,
                            "cancellation_acknowledged_at": datetime.now().astimezone().isoformat(),
                            "runner_lifecycle": "cancelling",
                        })
                        self._write_state(config.artifacts_root, run_id, state)
                    elif kind == "confirmation_requested":
                        self._register_isolated_confirmation(
                            run_id, config.artifacts_root, handle, message["payload"]
                        )
                    elif kind == "confirmation_resolved":
                        self._resolve_isolated_confirmation(
                            run_id, config.artifacts_root, message["payload"]
                        )
                    elif kind == "clarification_requested":
                        human_wait_started = monotonic()
                        self._register_isolated_clarification(
                            run_id, config.artifacts_root, handle, message["payload"]
                        )
                    elif kind == "clarification_resolved":
                        if human_wait_started is not None:
                            deadline += monotonic() - human_wait_started
                            human_wait_started = None
                        self._resolve_isolated_clarification(
                            run_id, config.artifacts_root, message["payload"]
                        )
                    elif kind == "login_control_result":
                        self._receive_login_control(run_id, message)
                    elif kind == "result":
                        if forced_reason:
                            continue
                        payload = message["payload"]
                        isolation = self._container_isolation_state(handle, config, False)
                        payload["runner_isolation"] = isolation
                        self._promote_success_experience(config, payload)
                        self._persist_final_isolation(config.artifacts_root, run_id, isolation)
                        self._persist_final_success_experience(config.artifacts_root, run_id, payload)
                        self._write_state(config.artifacts_root, run_id, payload)
                        final_received = True
                        break
                    elif kind in {"error", "protocol_error"}:
                        self._write_container_failure(
                            run_id, config, handle, "container_runner_exception",
                            f'{message.get("errorType", "ContainerError")}: {message.get("error", "")}',
                            False,
                        )
                        final_received = True
                        break
                if human_wait_started is None and monotonic() >= deadline and forced_reason is None:
                    forced_reason = "runner_resource_limit_exceeded"
                    try:
                        handle.send({"type": "cancel"})
                    except Exception:
                        pass
                with self._lock:
                    current = self._jobs.get(run_id)
                    cancelled_at = current.cancel_requested_at if isinstance(current, ContainerJob) else None
                if forced_reason and monotonic() >= deadline + config.isolation_cancel_grace_seconds:
                    break
                if cancelled_at is not None and monotonic() >= cancelled_at + config.isolation_cancel_grace_seconds:
                    forced_reason = "cancelled_forcibly"
                    break
                if not handle.is_alive():
                    break
            if forced_reason:
                self._write_recovery_capsule(
                    run_id,
                    config.artifacts_root,
                    forced_reason,
                    forced=True,
                )
                if handle.is_alive():
                    handle.graceful_stop(timeout=5.0)
                if handle.is_alive():
                    handle.terminate()
                handle.wait(3)
                self._write_container_failure(
                    run_id, config, handle, forced_reason,
                    "容器 Runner 超出资源时限，已强制终止容器"
                    if forced_reason == "runner_resource_limit_exceeded"
                    else "容器 Runner 未在取消宽限期内退出，已强制终止容器",
                    True,
                    cancelled=forced_reason == "cancelled_forcibly",
                )
                final_received = True
            elif final_received:
                handle.wait(3)
            elif not handle.is_alive():
                error = handle.error_summary() or "容器 Runner 未返回终态"
                self._write_container_failure(
                    run_id, config, handle, "container_runner_interrupted", error, False
                )
        finally:
            if handle.is_alive():
                handle.graceful_stop(timeout=3.0)
                if handle.is_alive():
                    self._write_recovery_capsule(
                        run_id,
                        config.artifacts_root,
                        "runner_finalization_forced",
                        forced=True,
                    )
                    handle.terminate()
                handle.wait(3)
            with self._lock:
                self._jobs.pop(run_id, None)
                self._confirmations.pop(run_id, None)
            self._release_activity(activity_key, run_id)

    @staticmethod
    def _container_isolation_state(
        handle: DockerRunHandle, config: RunnerConfig, forced: bool
    ) -> dict:
        return {
            "mode": "docker_container",
            "container_name": handle.container_name,
            "image": handle.image,
            "root_filesystem_read_only": True,
            "artifact_mount": str(handle.run_dir),
            "tmpfs_mb": RUNNER_TMPFS_TOTAL_MB,
            "memory_limit_mb": config.isolation_memory_limit_mb,
            "cpu_limit": float(os.getenv("GUI_RUNNER_CPUS", "2")),
            "pids_limit": int(os.getenv("GUI_RUNNER_PIDS", "256")),
            "capabilities_dropped": "ALL_AFTER_FIREWALL_INIT",
            "no_new_privileges": True,
            "container_network_mode": handle.network_mode,
            "container_private_network_allowed": handle.private_network_allowed,
            "network_policy": (
                "explicit_private_network_exception+playwright_request_guard"
                if handle.private_network_allowed
                else "container_egress_firewall+playwright_request_guard"
            ),
            "forced_termination": forced,
        }

    def _write_container_failure(
        self,
        run_id: str,
        config: RunnerConfig,
        handle: DockerRunHandle,
        reason: str,
        message: str,
        forced: bool,
        *,
        cancelled: bool = False,
    ) -> None:
        state = self.read(run_id, config.artifacts_root) or {}
        limit_exceeded = reason == "runner_resource_limit_exceeded"
        target_unreachable = (
            reason == "container_runner_exception"
            and _is_target_connectivity_failure(message)
        )
        state.update({
            "status": (
                Status.CANCELLED.value
                if cancelled else Status.INCOMPLETE.value
                if limit_exceeded or target_unreachable else Status.SYSTEM_ERROR.value
            ),
            "ended_at": datetime.now().astimezone().isoformat(),
            "completion_reason": (
                "target_application_unreachable" if target_unreachable else reason
            ),
            "system_error": message,
            "pending_confirmation": None,
            "pending_clarification": None,
            "runner_isolation": self._container_isolation_state(handle, config, forced),
            "recoverable": bool(state.get("steps")) or forced,
            "recovery_capsule": f"recovery-capsule.json" if forced else state.get("recovery_capsule"),
        })
        if limit_exceeded:
            runtime_limit = int(config.max_duration_seconds or 600)
            state.update({
                "goal_status": "incomplete",
                "goal_summary": (
                    f"运行已经开始并执行了动作，但超过当前项目的 {runtime_limit} 秒总时限；"
                    "系统已安全停止，未将超时误报为测试通过。"
                ),
                "result_classification": "agent_incomplete",
                "runtime_limit_seconds": runtime_limit,
            })
        elif target_unreachable:
            state.update({
                "goal_status": "incomplete",
                "goal_summary": (
                    "目标网站在首个页面导航阶段拒绝连接或无法访问；"
                    "Agent 尚未执行页面业务动作，请恢复目标服务或网络后重试。"
                ),
                "result_classification": "target_environment_unavailable",
                "target_connectivity_error": message,
            })
        self._write_state(config.artifacts_root, run_id, state)
        self._persist_failure_artifacts(config.artifacts_root, run_id, state)

    @staticmethod
    def _write_recovery_capsule(
        run_id: str,
        artifacts_root: Path,
        reason: str,
        *,
        forced: bool,
    ) -> None:
        run_dir = Path(artifacts_root) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        state: dict = {}
        state_path = run_dir / "run-state.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
        events: list[str] = []
        events_path = run_dir / "events.jsonl"
        if events_path.is_file():
            try:
                events = events_path.read_text(encoding="utf-8").splitlines()[-40:]
            except OSError:
                events = []
        capsule = {
            "schemaVersion": 1,
            "kind": "runner_recovery_capsule",
            "runId": run_id,
            "createdAt": datetime.now().astimezone().isoformat(),
            "reason": reason,
            "forcedTermination": forced,
            "lastState": state,
            "lastEvents": events,
            "artifacts": {
                "state": "run-state.json",
                "events": "events.jsonl",
                "screenshots": "screenshots/",
                "checkpoints": "checkpoints/",
            },
            "resumeFromStep": (
                max(
                    (int(item.get("index")) for item in state.get("steps", []) if isinstance(item, dict) and item.get("index") is not None),
                    default=0,
                )
                + 1
            ),
            "advisoryOnly": True,
        }
        target = run_dir / "recovery-capsule.json"
        temporary = target.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(capsule, stream, ensure_ascii=False, indent=2, default=str)
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except OSError:
                pass
        temporary.replace(target)

    def _supervise_isolated(
        self,
        run_id: str,
        config: RunnerConfig,
        process,
        cancel_event,
        connection: Connection,
        windows_job: WindowsJob,
        activity_key: str,
    ) -> None:
        started = monotonic()
        deadline = _supervisor_deadline(started, config)
        final_received = False
        human_wait_started = None
        forced_reason: str | None = None
        try:
            while True:
                if connection.poll(0.05):
                    try:
                        message = connection.recv()
                    except EOFError:
                        break
                    kind = message.get("type")
                    if kind == "progress":
                        payload = message["payload"]
                        deadline = _supervisor_deadline(started, config, payload)
                        self._write_state(config.artifacts_root, run_id, payload)
                    elif kind == "confirmation_requested":
                        self._register_isolated_confirmation(
                            run_id, config.artifacts_root, connection, message["payload"]
                        )
                    elif kind == "confirmation_resolved":
                        self._resolve_isolated_confirmation(
                            run_id, config.artifacts_root, message["payload"]
                        )
                    elif kind == "clarification_requested":
                        human_wait_started = monotonic()
                        self._register_isolated_clarification(
                            run_id, config.artifacts_root, connection, message["payload"]
                        )
                    elif kind == "clarification_resolved":
                        if human_wait_started is not None:
                            deadline += monotonic() - human_wait_started
                            human_wait_started = None
                        self._resolve_isolated_clarification(
                            run_id, config.artifacts_root, message["payload"]
                        )
                    elif kind == "login_control_result":
                        self._receive_login_control(run_id, message)
                    elif kind == "result":
                        if forced_reason:
                            continue
                        payload = message["payload"]
                        isolation = self._isolation_state(
                            config, run_id, process.pid, windows_job.assigned, False
                        )
                        payload["runner_isolation"] = isolation
                        self._promote_success_experience(config, payload)
                        self._persist_final_isolation(config.artifacts_root, run_id, isolation)
                        self._persist_final_success_experience(config.artifacts_root, run_id, payload)
                        self._write_state(config.artifacts_root, run_id, payload)
                        final_received = True
                        break
                    elif kind == "error":
                        self._write_isolated_failure(
                            run_id, config, "runner_exception",
                            f'{message.get("errorType", "RunnerError")}: {message.get("error", "")}',
                            process.pid, windows_job.assigned, False,
                        )
                        final_received = True
                        break
                if human_wait_started is None and monotonic() >= deadline:
                    forced_reason = "runner_resource_limit_exceeded"
                    cancel_event.set()
                with self._lock:
                    current = self._jobs.get(run_id)
                    cancelled_at = current.cancel_requested_at if isinstance(current, IsolatedJob) else None
                if forced_reason and monotonic() >= deadline + config.isolation_cancel_grace_seconds:
                    break
                if cancelled_at is not None and monotonic() >= cancelled_at + config.isolation_cancel_grace_seconds:
                    forced_reason = "cancelled_forcibly"
                    break
                if not process.is_alive():
                    break
            if forced_reason:
                windows_job.terminate()
                if process.is_alive():
                    process.terminate()
                process.join(2)
                self._write_isolated_failure(
                    run_id, config, forced_reason,
                    "隔离 Runner 超出资源时限，已强制终止进程树"
                    if forced_reason == "runner_resource_limit_exceeded"
                    else "隔离 Runner 未在取消宽限期内退出，已强制终止进程树",
                    process.pid, windows_job.assigned, True,
                    cancelled=forced_reason == "cancelled_forcibly",
                )
                final_received = True
            elif final_received:
                process.join(2)
            elif not process.is_alive():
                self._write_isolated_failure(
                    run_id, config, "runner_process_interrupted",
                    f"隔离 Runner 异常退出（exit code {process.exitcode}）",
                    process.pid, windows_job.assigned, False,
                )
        finally:
            if process.is_alive():
                windows_job.terminate()
                process.terminate()
                process.join(2)
            connection.close()
            windows_job.close()
            with self._lock:
                self._jobs.pop(run_id, None)
                self._confirmations.pop(run_id, None)
                self._clarifications.pop(run_id, None)
            self._release_activity(activity_key, run_id)

    def _register_isolated_confirmation(
        self, run_id: str, artifacts_root: Path, connection, payload: dict
    ) -> None:
        entry = {**payload, "connection": connection, "decision": None, "actor": None}
        with self._lock:
            self._confirmations[run_id] = entry
        state = self.read(run_id, artifacts_root) or {}
        state.update({
            "status": Status.PENDING_CONFIRMATION.value,
            "completion_reason": "dangerous_action_pending_confirmation",
            "pending_confirmation": payload,
            "goal_summary": f'步骤 {payload["step_index"]} 危险动作等待人工确认',
        })
        self._write_state(artifacts_root, run_id, state)

    def _resolve_isolated_confirmation(
        self, run_id: str, artifacts_root: Path, payload: dict
    ) -> None:
        state = self.read(run_id, artifacts_root) or {}
        history = list(state.get("confirmation_history", []))
        history.append(payload)
        state.update({
            "status": Status.RUNNING.value,
            "completion_reason": (
                "dangerous_action_approved"
                if payload["decision"] == "approved"
                else "dangerous_action_rejected_replanning"
            ),
            "pending_confirmation": None,
            "pending_clarification": None,
            "confirmation_history": history,
        })
        self._write_state(artifacts_root, run_id, state)
        with self._lock:
            self._confirmations.pop(run_id, None)

    def _register_isolated_clarification(
        self, run_id: str, artifacts_root: Path, connection, payload: dict
    ) -> None:
        entry = {**payload, "connection": connection, "answer": None, "actor": None}
        with self._lock:
            self._clarifications[run_id] = entry
        state = self.read(run_id, artifacts_root) or {}
        state.update({
            "status": Status.WAITING_FOR_CLARIFICATION.value,
            "completion_reason": "waiting_for_clarification",
            "pending_clarification": payload,
            "goal_summary": f'第 {payload["round"]} 轮等待用户澄清',
        })
        self._write_state(artifacts_root, run_id, state)

    def _resolve_isolated_clarification(
        self, run_id: str, artifacts_root: Path, payload: dict
    ) -> None:
        state = self.read(run_id, artifacts_root) or {}
        history = list(state.get("clarification_history", []))
        if payload.get("answer"):
            history.append(payload)
        state.update({
            "status": Status.RUNNING.value if payload.get("answer") else Status.CANCELLED.value,
            "completion_reason": "clarification_resolved" if payload.get("answer") else "clarification_cancelled",
            "pending_clarification": None,
            "clarification_history": history,
        })
        self._write_state(artifacts_root, run_id, state)
        with self._lock:
            self._clarifications.pop(run_id, None)

    @staticmethod
    def _isolation_state(
        config: RunnerConfig, run_id: str, process_id: int, job_assigned: bool,
        forced: bool,
    ) -> dict:
        run_dir = Path(config.artifacts_root).resolve() / run_id
        return {
            "mode": "spawn_process",
            "process_id": process_id,
            "windows_job_assigned": job_assigned,
            "memory_limit_mb": config.isolation_memory_limit_mb,
            "working_directory": str(run_dir),
            "temp_directory": str(run_dir / "_runner_tmp"),
            "network_policy": "playwright_request_guard",
            "forced_termination": forced,
        }

    def _write_isolated_failure(
        self,
        run_id: str,
        config: RunnerConfig,
        reason: str,
        message: str,
        process_id: int,
        job_assigned: bool,
        forced: bool,
        *,
        cancelled: bool = False,
    ) -> None:
        state = self.read(run_id, config.artifacts_root) or {}
        limit_exceeded = reason == "runner_resource_limit_exceeded"
        state.update({
            "status": (
                Status.CANCELLED.value
                if cancelled else Status.INCOMPLETE.value
                if limit_exceeded else Status.SYSTEM_ERROR.value
            ),
            "ended_at": datetime.now().astimezone().isoformat(),
            "completion_reason": reason,
            "system_error": message,
            "pending_confirmation": None,
            "runner_isolation": self._isolation_state(
                config, run_id, process_id, job_assigned, forced
            ),
        })
        if limit_exceeded:
            runtime_limit = int(config.max_duration_seconds or 600)
            state.update({
                "goal_status": "incomplete",
                "goal_summary": (
                    f"运行已经开始并执行了动作，但超过当前项目的 {runtime_limit} 秒总时限；"
                    "系统已安全停止，未将超时误报为测试通过。"
                ),
                "result_classification": "agent_incomplete",
                "runtime_limit_seconds": runtime_limit,
            })
        self._write_state(config.artifacts_root, run_id, state)
        self._persist_failure_artifacts(config.artifacts_root, run_id, state)

    @staticmethod
    def _persist_failure_artifacts(
        artifacts_root: Path, run_id: str, state: dict
    ) -> None:
        """Always leave a complete diagnostic bundle after supervisor failure."""
        run_dir = Path(artifacts_root) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(state)
        payload.setdefault(
            "result_classification",
            payload.get("completion_reason") or "runner_exception",
        )
        payload.setdefault("goal_status", "incomplete")
        payload.setdefault("goal_summary", "执行器异常终止，完整错误已写入本轮产物。")
        payload.setdefault("model_calls", 0)
        payload.setdefault("estimated_cost", None)

        target = run_dir / "run.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(target)

        gate = payload.get("completion_gate")
        if not isinstance(gate, dict):
            gate = {
                "schemaVersion": 1,
                "run_status": payload.get("status", Status.SYSTEM_ERROR.value),
                "goal_status": payload.get("goal_status", "incomplete"),
                "passed": False,
                "reasons": [
                    payload.get("completion_reason") or "runner_exception",
                    payload.get("system_error") or "执行器异常终止",
                ],
                "source": "orchestrator_failure_fallback",
            }
        (run_dir / "completion-gate.json").write_text(
            json.dumps(gate, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        finalization = {
            "schemaVersion": 1,
            "runId": run_id,
            "status": payload.get("status"),
            "completionReason": payload.get("completion_reason"),
            "runJson": "run.json",
            "events": "events.jsonl" if (run_dir / "events.jsonl").is_file() else None,
            "reports": ["report.md", "report.html"],
            "completionGate": "completion-gate.json",
            "trace": "trace.zip" if (run_dir / "trace.zip").is_file() else None,
            "traceUnavailableReason": (
                None
                if (run_dir / "trace.zip").is_file()
                else "Runner ended before Playwright trace finalization"
            ),
        }
        try:
            write_reports(RunResult.model_validate(payload), run_dir)
        except Exception as exc:
            summary = (
                f"# 测试运行异常：{payload.get('plan_name', run_id)}\n\n"
                f"- 运行 ID：`{run_id}`\n"
                f"- 状态：`{payload.get('status', 'system_error')}`\n"
                f"- 完成原因：`{payload.get('completion_reason', 'runner_exception')}`\n"
                f"- 系统错误：`{payload.get('system_error', '')}`\n"
                f"- 报告生成错误：`{type(exc).__name__}: {exc}`\n"
            )
            (run_dir / "report.md").write_text(summary, encoding="utf-8")
            escaped = summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            (run_dir / "report.html").write_text(
                "<pre>" + escaped + "</pre>", encoding="utf-8"
            )
        # Written last: consumers may treat this file as the atomic signal
        # that the diagnostic bundle is complete and safe to read.
        (run_dir / "artifact-finalization.json").write_text(
            json.dumps(finalization, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _persist_final_isolation(artifacts_root: Path, run_id: str, isolation: dict) -> None:
        target = Path(artifacts_root) / run_id / "run.json"
        if not target.is_file():
            return
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload["runner_isolation"] = isolation
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            temporary.replace(target)
            write_reports(RunResult.model_validate(payload), target.parent)
        except Exception:
            return

    @staticmethod
    def _promote_success_experience(config: RunnerConfig, payload: dict) -> None:
        if config.success_experience_root is None:
            return
        record = SuccessExperienceStore(config.success_experience_root).promote(payload)
        payload["success_experience"] = {
            "promoted": record is not None,
            "policy": "success-only-multimodal-v1",
            "runId": record.get("runId") if record else None,
        }

    @staticmethod
    def _persist_final_success_experience(
        artifacts_root: Path, run_id: str, payload: dict
    ) -> None:
        target = Path(artifacts_root) / run_id / "run.json"
        if not target.is_file() or "success_experience" not in payload:
            return
        try:
            final_payload = json.loads(target.read_text(encoding="utf-8"))
            final_payload["success_experience"] = payload["success_experience"]
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(final_payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            temporary.replace(target)
            write_reports(RunResult.model_validate(final_payload), target.parent)
        except Exception:
            return

    def _wait_for_confirmation(
        self,
        run_id: str,
        artifacts_root: Path,
        cancel_event: Event,
        history: list[dict],
        step,
        index: int,
        rule: str,
    ) -> bool:
        requested_at = datetime.now().astimezone()
        confirmation_id = f"confirmation-{uuid4().hex[:12]}"
        target = step.description or (step.locator.describe() if step.locator else step.target) or step.action.value
        pending_payload = {
            "id": confirmation_id,
            "step_index": index,
            "action": step.action.value,
            "target": target,
            "rule": rule,
            "requested_at": requested_at.isoformat(),
        }
        entry = {**pending_payload, "event": Event(), "decision": None, "actor": None}
        with self._lock:
            self._confirmations[run_id] = entry
        state = self.read(run_id, artifacts_root) or {}
        state.update({
            "status": Status.PENDING_CONFIRMATION.value,
            "completion_reason": "dangerous_action_pending_confirmation",
            "pending_confirmation": pending_payload,
            "confirmation_history": list(history),
            "goal_summary": f"步骤 {index} 危险动作等待人工确认",
        })
        self._write_state(artifacts_root, run_id, state)
        while not entry["event"].wait(0.25):
            if cancel_event.is_set():
                entry["decision"] = "rejected"
                entry["actor"] = "cancel_request"
                entry["event"].set()
                break
        decision = entry.get("decision") or "rejected"
        decided_at = datetime.now().astimezone().isoformat()
        history.append({
            **pending_payload,
            "decision": decision,
            "actor": entry.get("actor") or "local_user",
            "decided_at": decided_at,
        })
        state = self.read(run_id, artifacts_root) or state
        state.update({
            "status": Status.RUNNING.value,
            "completion_reason": (
                "dangerous_action_approved"
                if decision == "approved"
                else "dangerous_action_rejected_replanning"
            ),
            "pending_confirmation": None,
            "confirmation_history": list(history),
        })
        self._write_state(artifacts_root, run_id, state)
        with self._lock:
            self._confirmations.pop(run_id, None)
        return decision == "approved"

    def _wait_for_clarification(
        self,
        run_id: str,
        artifacts_root: Path,
        cancel_event: Event,
        history: list[dict],
        question: str,
        round_number: int,
    ) -> str | None:
        requested_at = datetime.now().astimezone()
        clarification_id = f"clarification-{uuid4().hex[:12]}"
        pending_payload = {
            "id": clarification_id,
            "round": round_number,
            "question": question,
            "requested_at": requested_at.isoformat(),
        }
        entry = {**pending_payload, "event": Event(), "answer": None, "actor": None}
        with self._lock:
            self._clarifications[run_id] = entry
        state = self.read(run_id, artifacts_root) or {}
        state.update({
            "status": Status.WAITING_FOR_CLARIFICATION.value,
            "completion_reason": "waiting_for_clarification",
            "pending_clarification": pending_payload,
            "clarification_history": list(history),
            "goal_summary": f"第 {round_number} 轮等待用户澄清",
        })
        self._write_state(artifacts_root, run_id, state)
        while not entry["event"].wait(0.25):
            if cancel_event.is_set():
                entry["answer"] = None
                entry["actor"] = "cancel_request"
                entry["event"].set()
                break
        answer = entry.get("answer")
        resolved = {
            **pending_payload,
            "answer": answer,
            "actor": entry.get("actor") or "local_user",
            "answered_at": datetime.now().astimezone().isoformat(),
        }
        state = self.read(run_id, artifacts_root) or state
        state.update({
            "status": Status.RUNNING.value if answer else Status.CANCELLED.value,
            "completion_reason": "clarification_resolved" if answer else "clarification_cancelled",
            "pending_clarification": None,
            "clarification_history": [*history, resolved] if answer else list(history),
        })
        self._write_state(artifacts_root, run_id, state)
        with self._lock:
            self._clarifications.pop(run_id, None)
        return str(answer) if answer else None

    def _mark_interrupted(
        self,
        run_id: str,
        artifacts_root: Path,
        state: dict,
        *,
        reason: str = "runner_process_interrupted",
    ) -> dict:
        state.update({
            "status": Status.INCOMPLETE.value,
            "ended_at": datetime.now().astimezone().isoformat(),
            "completion_reason": reason,
            "system_error": "执行服务重启或后台运行线程异常退出；已保留最后检查点",
            "goal_status": "incomplete",
            "result_classification": "runner_interrupted",
            "recoverable": bool(state.get("steps")),
            "recovery_capsule": "recovery-capsule.json",
        })
        self._write_state(artifacts_root, run_id, state)
        self._persist_failure_artifacts(artifacts_root, run_id, state)
        return state

    @staticmethod
    def _write_state(artifacts_root: Path, run_id: str, payload: dict) -> None:
        with RunOrchestrator._state_lock:
            run_dir = Path(artifacts_root) / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            target = run_dir / "run-state.json"
            persisted: dict = {}
            if target.is_file():
                try:
                    persisted = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    persisted = {}
            merged = dict(payload)
            if persisted.get("activity_key") and not merged.get("activity_key"):
                merged["activity_key"] = persisted["activity_key"]
            if isinstance(persisted.get("runner_isolation"), dict):
                current_isolation = merged.get("runner_isolation")
                if not isinstance(current_isolation, dict):
                    merged["runner_isolation"] = persisted["runner_isolation"]
                else:
                    merged["runner_isolation"] = {
                        **persisted["runner_isolation"],
                        **current_isolation,
                    }
            # A progress snapshot can legitimately omit arrays while a child
            # process is finalizing. Never replace already persisted steps or
            # evidence with an empty payload from a later failure path.
            for field_name in (
                "steps",
                "assertions",
                "reproduction_steps",
                "cause_hints",
                "findings",
                "model_call_records",
                "confirmation_history",
                "clarification_history",
            ):
                incoming = merged.get(field_name)
                previous = persisted.get(field_name)
                if isinstance(previous, list) and previous and (not isinstance(incoming, list) or not incoming):
                    merged[field_name] = previous
            merged.setdefault("experience_journal", {
                "root": str(Path(artifacts_root).resolve().parent / "data" / "experience-journal"),
                "advisory_only": True,
            })
            merged["last_persisted_at"] = datetime.now().astimezone().isoformat()
            encoded = json.dumps(merged, ensure_ascii=False, indent=2, default=str)
            temporary = target.with_suffix(".json.tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
            temporary.replace(target)
            try:
                ExperienceJournal(
                    Path(artifacts_root).resolve().parent / "data" / "experience-journal"
                ).record(merged)
            except Exception:
                # A diagnostic journal must never take down the test itself.
                pass

    @staticmethod
    def _activity_key(plan: TestPlan, config: RunnerConfig) -> str:
        if config.project_id:
            environment = config.environment_id or "default"
            return f"project:{config.project_id}:environment:{environment}"
        parsed = urlparse(plan.base_url)
        return f"origin:{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    def _release_activity(self, activity_key: str, run_id: str) -> None:
        with self._lock:
            if self._active_keys.get(activity_key) == run_id:
                self._active_keys.pop(activity_key, None)

    def _claim_activity(self, activity_key: str, run_id: str, artifacts_root: Path) -> None:
        with self._lock:
            existing = self._active_keys.get(activity_key)
            if existing is None:
                self._active_keys[activity_key] = run_id
                return
            live = existing in self._jobs

        # A supervisor may have written a terminal state before it reached its
        # finally block. Heal only that exact stale condition; queued/running
        # state remains blocked even during the small job-registration window.
        persisted_status = self._persisted_status(artifacts_root, existing)
        if live or persisted_status is None or persisted_status in ACTIVE_STATUSES:
            raise ActiveRunConflict(activity_key, existing)

        with self._lock:
            if self._active_keys.get(activity_key) != existing or existing in self._jobs:
                current = self._active_keys.get(activity_key)
                if current:
                    raise ActiveRunConflict(activity_key, current)
            else:
                self._active_keys.pop(activity_key, None)
            self._active_keys[activity_key] = run_id

    @staticmethod
    def _persisted_status(artifacts_root: Path, run_id: str) -> str | None:
        run_dir = Path(artifacts_root) / run_id
        candidates = [
            path for path in (run_dir / "run.json", run_dir / "run-state.json")
            if path.is_file()
        ]
        if not candidates:
            return None
        target = max(candidates, key=lambda path: path.stat().st_mtime)
        with RunOrchestrator._state_lock:
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        status = payload.get("status")
        return str(status) if status is not None else None

    def _release_activity_from_state(self, artifacts_root: Path, run_id: str) -> None:
        state = self.read(run_id, artifacts_root) or {}
        activity_key = state.get("activity_key")
        if activity_key:
            self._release_activity(str(activity_key), run_id)


def _ensure_artifact_capacity(
    root: Path,
    *,
    minimum_free_mb: int | None = None,
) -> None:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    configured = minimum_free_mb
    if configured is None:
        try:
            configured = int(os.getenv("GUI_MIN_ARTIFACT_FREE_MB", str(DEFAULT_MIN_ARTIFACT_FREE_MB)))
        except ValueError:
            configured = DEFAULT_MIN_ARTIFACT_FREE_MB
    required_mb = max(256, configured)
    free_mb = shutil.disk_usage(root).free // (1024 * 1024)
    if free_mb < required_mb:
        raise RuntimeError(
            "测试证据磁盘空间不足："
            f"当前可用 {free_mb} MB，启动 Runner 至少需要 {required_mb} MB；"
            "请清理旧测试记录或调整 GUI_AGENT_ARTIFACTS 后重试"
        )
