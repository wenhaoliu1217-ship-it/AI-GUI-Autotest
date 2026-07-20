"""Persistent background run orchestration for the local single-user runner."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Callable
from uuid import uuid4

from ..domain.models import TestPlan
from ..domain.results import Status
from .runner import RunnerConfig, run_plan


Runner = Callable[[TestPlan, RunnerConfig], tuple[object, Path]]
ACTIVE_STATUSES = {Status.QUEUED.value, Status.RUNNING.value}


class RunOrchestrator:
    _state_lock = Lock()

    def __init__(self, runner: Runner = run_plan) -> None:
        self._runner = runner
        self._jobs: dict[str, tuple[Event, Thread]] = {}
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
        }
        self._write_state(config.artifacts_root, run_id, initial)
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
        job[0].set()
        return state

    def read(self, run_id: str, artifacts_root: Path) -> dict | None:
        run_dir = Path(artifacts_root) / run_id
        final_path = run_dir / "run.json"
        state_path = run_dir / "run-state.json"
        target = final_path if final_path.is_file() else state_path
        if not target.is_file():
            return None
        with RunOrchestrator._state_lock:
            data = json.loads(target.read_text(encoding="utf-8"))
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
            target = run_dir / "run.json"
            if not target.is_file():
                target = run_dir / "run-state.json"
            if not target.is_file():
                continue
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
