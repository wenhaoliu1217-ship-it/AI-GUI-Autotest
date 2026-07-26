"""Persistent background run orchestration for the local single-user runner."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, sleep
from typing import Callable
from uuid import uuid4

from ..domain.models import TestPlan
from ..domain.results import Status
from ..domain.results import RunResult
from ..artifacts.report import write_reports
from .runner import RunnerConfig, run_plan
from .isolation import WindowsJob, isolated_worker
from .container_runtime import DockerRunHandle


Runner = Callable[[TestPlan, RunnerConfig], tuple[object, Path]]
ACTIVE_STATUSES = {
    Status.QUEUED.value,
    Status.RUNNING.value,
    Status.PENDING_CONFIRMATION.value,
    Status.WAITING_FOR_CLARIFICATION.value,
}


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
        self._lock = Lock()

    def start(self, plan: TestPlan, config: RunnerConfig) -> dict:
        started = datetime.now().astimezone()
        run_id = f"{started:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
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
        self._write_state(config.artifacts_root, run_id, initial)
        if self._runner_mode == "container":
            return self._start_container(run_id, plan, config, initial)
        if self._runner_mode == "process":
            return self._start_isolated(run_id, plan, config, initial)
        cancel_event = Event()
        thread = Thread(
            target=self._execute,
            args=(run_id, plan, config, cancel_event),
            name=f"gui-run-{run_id}",
            daemon=True,
        )
        with self._lock:
            self._jobs[run_id] = (cancel_event, thread)
        thread.start()
        return initial

    def run_blocking(self, plan: TestPlan, config: RunnerConfig) -> dict:
        state = self.start(plan, config)
        run_id = state["run_id"]
        deadline = monotonic() + (config.max_duration_seconds or 600) + config.isolation_cancel_grace_seconds + 10
        while state.get("status") in ACTIVE_STATUSES and monotonic() < deadline:
            sleep(0.02)
            state = self.read(run_id, config.artifacts_root) or state
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
        state["cancellation_requested"] = True
        state["completion_reason"] = "cancellation_requested"
        self._write_state(artifacts_root, run_id, state)
        if isinstance(job, ContainerJob):
            job.handle.send({"type": "cancel"})
            job.cancel_requested_at = monotonic()
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
        while state.get("status") in ACTIVE_STATUSES and monotonic() < deadline:
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

    def _execute(self, run_id: str, plan: TestPlan, config: RunnerConfig, cancel_event: Event) -> None:
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
            self._write_state(config.artifacts_root, run_id, result.model_dump(mode="json"))
        except Exception as exc:
            state = self.read(run_id, config.artifacts_root) or {}
            state.update({
                "status": Status.SYSTEM_ERROR.value,
                "ended_at": datetime.now().astimezone().isoformat(),
                "completion_reason": "runner_exception",
                "system_error": str(exc),
            })
            self._write_state(config.artifacts_root, run_id, state)
        finally:
            with self._lock:
                self._jobs.pop(run_id, None)
                self._confirmations.pop(run_id, None)
                self._clarifications.pop(run_id, None)
                self._clarifications.pop(run_id, None)

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
            args=(run_id, config, process, cancel_event, parent_connection, windows_job),
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
            return initial
        supervisor = Thread(
            target=self._supervise_container,
            args=(run_id, config, handle),
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
        self, run_id: str, config: RunnerConfig, handle: DockerRunHandle
    ) -> None:
        deadline = monotonic() + (config.max_duration_seconds or 600)
        forced_reason: str | None = None
        final_received = False
        try:
            while True:
                message = handle.poll_message(0.05)
                if message is not None:
                    kind = message.get("type")
                    if kind == "progress":
                        self._write_state(config.artifacts_root, run_id, message["payload"])
                    elif kind == "confirmation_requested":
                        self._register_isolated_confirmation(
                            run_id, config.artifacts_root, handle, message["payload"]
                        )
                    elif kind == "confirmation_resolved":
                        self._resolve_isolated_confirmation(
                            run_id, config.artifacts_root, message["payload"]
                        )
                    elif kind == "clarification_requested":
                        self._register_isolated_clarification(
                            run_id, config.artifacts_root, handle, message["payload"]
                        )
                    elif kind == "clarification_resolved":
                        self._resolve_isolated_clarification(
                            run_id, config.artifacts_root, message["payload"]
                        )
                    elif kind == "result":
                        if forced_reason:
                            continue
                        payload = message["payload"]
                        isolation = self._container_isolation_state(handle, config, False)
                        payload["runner_isolation"] = isolation
                        self._persist_final_isolation(config.artifacts_root, run_id, isolation)
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
                if monotonic() >= deadline and forced_reason is None:
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
                handle.terminate()
                handle.wait(2)
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
                handle.terminate()
                handle.wait(2)
            with self._lock:
                self._jobs.pop(run_id, None)
                self._confirmations.pop(run_id, None)

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
            "tmpfs_mb": 324,
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
        state.update({
            "status": Status.CANCELLED.value if cancelled else Status.SYSTEM_ERROR.value,
            "ended_at": datetime.now().astimezone().isoformat(),
            "completion_reason": reason,
            "system_error": message,
            "pending_confirmation": None,
            "pending_clarification": None,
            "runner_isolation": self._container_isolation_state(handle, config, forced),
        })
        self._write_state(config.artifacts_root, run_id, state)

    def _supervise_isolated(
        self,
        run_id: str,
        config: RunnerConfig,
        process,
        cancel_event,
        connection: Connection,
        windows_job: WindowsJob,
    ) -> None:
        started = monotonic()
        deadline = started + (config.max_duration_seconds or 600)
        final_received = False
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
                        self._write_state(config.artifacts_root, run_id, message["payload"])
                    elif kind == "confirmation_requested":
                        self._register_isolated_confirmation(
                            run_id, config.artifacts_root, connection, message["payload"]
                        )
                    elif kind == "confirmation_resolved":
                        self._resolve_isolated_confirmation(
                            run_id, config.artifacts_root, message["payload"]
                        )
                    elif kind == "clarification_requested":
                        self._register_isolated_clarification(
                            run_id, config.artifacts_root, connection, message["payload"]
                        )
                    elif kind == "clarification_resolved":
                        self._resolve_isolated_clarification(
                            run_id, config.artifacts_root, message["payload"]
                        )
                    elif kind == "result":
                        if forced_reason:
                            continue
                        payload = message["payload"]
                        isolation = self._isolation_state(
                            config, run_id, process.pid, windows_job.assigned, False
                        )
                        payload["runner_isolation"] = isolation
                        self._persist_final_isolation(config.artifacts_root, run_id, isolation)
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
                if monotonic() >= deadline:
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
            "status": Status.RUNNING.value if payload["decision"] == "approved" else Status.CANCELLED.value,
            "completion_reason": "dangerous_action_approved" if payload["decision"] == "approved" else "dangerous_action_rejected",
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
        state.update({
            "status": Status.CANCELLED.value if cancelled else Status.SYSTEM_ERROR.value,
            "ended_at": datetime.now().astimezone().isoformat(),
            "completion_reason": reason,
            "system_error": message,
            "pending_confirmation": None,
            "runner_isolation": self._isolation_state(
                config, run_id, process_id, job_assigned, forced
            ),
        })
        self._write_state(config.artifacts_root, run_id, state)

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
            "status": Status.RUNNING.value if decision == "approved" else Status.CANCELLED.value,
            "completion_reason": "dangerous_action_approved" if decision == "approved" else "dangerous_action_rejected",
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

    def _mark_interrupted(self, run_id: str, artifacts_root: Path, state: dict) -> dict:
        state.update({
            "status": Status.SYSTEM_ERROR.value,
            "ended_at": datetime.now().astimezone().isoformat(),
            "completion_reason": "runner_process_interrupted",
            "system_error": "执行服务重启或后台运行线程异常退出",
        })
        self._write_state(artifacts_root, run_id, state)
        return state

    @staticmethod
    def _write_state(artifacts_root: Path, run_id: str, payload: dict) -> None:
        with RunOrchestrator._state_lock:
            run_dir = Path(artifacts_root) / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            target = run_dir / "run-state.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            temporary.replace(target)
