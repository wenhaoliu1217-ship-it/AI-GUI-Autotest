"""Persistent, cancellable 30x5 acceptance batch scheduling."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from time import sleep
from typing import Callable
from uuid import uuid4

from .benchmark import _attempt_from_result, _markdown, _summarize
from .binding import CompiledScenario
from .models import AcceptanceAttempt, BenchmarkScenario


BatchExecutor = Callable[[CompiledScenario, int], dict]
ACTIVE_BATCH_STATUSES = {"queued", "running", "cancelling"}


class AcceptanceBatchManager:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._io_lock = Lock()
        self._states: dict[str, dict] = {}
        self._cancel: dict[str, Event] = {}
        self._compiled: dict[str, list[CompiledScenario]] = {}
        self._executors: dict[str, BatchExecutor] = {}

    def start(self, compiled: list[CompiledScenario], executor: BatchExecutor, *, dry_run: bool) -> dict:
        if [item.scenario.id for item in compiled] != [f"S{index:02d}" for index in range(1, 31)]:
            raise ValueError("验收批次必须按顺序完整包含 S01-S30")
        batch_id = f"acceptance-{datetime.now().astimezone():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        attempts = [
            {"scenarioId": item.scenario.id, "repeat": repeat, "status": "pending", "runId": None, "completionReason": None}
            for item in compiled for repeat in range(1, 6)
        ]
        payload = {
            "schemaVersion": "1", "batchId": batch_id, "status": "queued", "dryRun": dry_run,
            "createdAt": _now(), "updatedAt": _now(), "plannedRuns": 150, "completedRuns": 0,
            "currentScenarioId": None, "currentRepeat": None, "cancelRequested": False,
            "attempts": attempts, "summaryAvailable": False,
        }
        self._write(batch_id, payload)
        self._launch(batch_id, compiled, executor)
        return payload

    def read(self, batch_id: str) -> dict | None:
        with self._io_lock:
            if batch_id in self._states:
                return json.loads(json.dumps(self._states[batch_id]))
        target = self._batch_file(batch_id)
        if not target.is_file():
            return None
        for attempt in range(20):
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
                with self._io_lock:
                    self._states[batch_id] = payload
                return json.loads(json.dumps(payload))
            except PermissionError:
                if attempt == 19:
                    raise
                sleep(0.01)

    def list(self) -> list[dict]:
        payloads = [self.read(path.parent.name) for path in self.root.glob("acceptance-*/batch.json")]
        return sorted((item for item in payloads if item), key=lambda item: item["createdAt"], reverse=True)

    def cancel(self, batch_id: str) -> dict:
        with self._lock:
            payload = self._require(batch_id)
            if payload["status"] not in ACTIVE_BATCH_STATUSES:
                raise RuntimeError("验收批次已结束，不能取消")
            payload["status"] = "cancelling"
            payload["cancelRequested"] = True
            payload["updatedAt"] = _now()
            self._write(batch_id, payload)
            self._cancel.setdefault(batch_id, Event()).set()
            return payload

    def resume(self, batch_id: str, executor: BatchExecutor | None = None) -> dict:
        with self._lock:
            payload = self._require(batch_id)
            if payload["status"] not in {"cancelled", "failed"}:
                raise RuntimeError("只有已取消或失败的批次可以恢复")
            compiled = self._compiled.get(batch_id)
            effective_executor = executor or self._executors.get(batch_id)
            if compiled is None or effective_executor is None:
                raise RuntimeError("服务重启后恢复需要重新提交并校验场景绑定")
            payload["status"] = "queued"
            payload["cancelRequested"] = False
            payload["updatedAt"] = _now()
            self._write(batch_id, payload)
        self._launch(batch_id, compiled, effective_executor)
        return self._require(batch_id)

    def retry_failed(self, batch_id: str, executor: BatchExecutor | None = None) -> dict:
        with self._lock:
            payload = self._require(batch_id)
            if payload["status"] in ACTIVE_BATCH_STATUSES:
                raise RuntimeError("运行中的批次不能重试")
            failed = [item for item in payload["attempts"] if item["status"] not in {"passed", "dry_run_ready"}]
            if not failed:
                raise RuntimeError("没有可重试的失败尝试")
            compiled = self._compiled.get(batch_id)
            effective_executor = executor or self._executors.get(batch_id)
            if compiled is None or effective_executor is None:
                raise RuntimeError("服务重启后重试需要重新提交并校验场景绑定")
            for item in failed:
                item.update(status="pending", runId=None, completionReason=None)
            payload["status"] = "queued"
            payload["completedRuns"] = sum(item["status"] in {"passed", "dry_run_ready"} for item in payload["attempts"])
            payload["cancelRequested"] = False
            payload["updatedAt"] = _now()
            self._write(batch_id, payload)
        self._launch(batch_id, compiled, effective_executor)
        return self._require(batch_id)

    def _launch(self, batch_id: str, compiled: list[CompiledScenario], executor: BatchExecutor) -> None:
        cancel = Event()
        self._cancel[batch_id] = cancel
        self._compiled[batch_id] = compiled
        self._executors[batch_id] = executor
        Thread(target=self._run, args=(batch_id, compiled, executor, cancel), daemon=True).start()

    def _run(self, batch_id: str, compiled: list[CompiledScenario], executor: BatchExecutor, cancel: Event) -> None:
        by_id = {item.scenario.id: item for item in compiled}
        try:
            self._update(batch_id, status="running")
            for index in range(150):
                payload = self._require(batch_id)
                attempt = payload["attempts"][index]
                if attempt["status"] != "pending":
                    continue
                if cancel.is_set():
                    self._write_reports(batch_id)
                    self._update(batch_id, status="cancelled", currentScenarioId=None, currentRepeat=None)
                    return
                scenario_id, repeat = attempt["scenarioId"], int(attempt["repeat"])
                self._update(batch_id, currentScenarioId=scenario_id, currentRepeat=repeat)
                payload = self._require(batch_id)
                attempt = payload["attempts"][index]
                attempt["status"] = "running"
                payload["updatedAt"] = _now()
                self._write(batch_id, payload)
                result = executor(by_id[scenario_id], repeat)
                payload = self._require(batch_id)
                attempt = payload["attempts"][index]
                attempt.update(
                    status=result.get("status", "system_error"), runId=result.get("run_id"),
                    completionReason=result.get("completion_reason", "missing_completion_reason"), result=result,
                )
                payload["completedRuns"] = sum(item["status"] not in {"pending", "running"} for item in payload["attempts"])
                payload["updatedAt"] = _now()
                self._write(batch_id, payload)
            self._write_reports(batch_id)
            self._update(batch_id, status="completed", currentScenarioId=None, currentRepeat=None)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            try:
                self._write_reports(batch_id)
            finally:
                self._update(batch_id, status="failed", error=error, currentScenarioId=None, currentRepeat=None)

    def _write_reports(self, batch_id: str) -> None:
        payload = self._require(batch_id)
        scenarios = {item.scenario.id: item.scenario for item in self._compiled.get(batch_id, [])}
        attempts: list[AcceptanceAttempt] = []
        for item in payload["attempts"]:
            result = item.get("result")
            if not result or item["scenarioId"] not in scenarios:
                attempts.append(AcceptanceAttempt(
                    scenarioId=item["scenarioId"], repeat=item["repeat"], status=item["status"],
                    goalStatus="incomplete", runId=item.get("runId") or f"{batch_id}-{item['scenarioId']}-R{item['repeat']}",
                    completionReason=item.get("completionReason") or item["status"],
                    blockedDependencies=[] if item["status"] == "dry_run_ready" else ["attempt_not_executed"],
                ))
            else:
                attempts.append(_attempt_from_result(scenarios[item["scenarioId"]], item["repeat"], result))
        summary = _summarize(batch_id, attempts, None)
        if payload["dryRun"]:
            summary["releaseStatus"] = "unverified"
            summary["verificationStatus"] = "dry_run_only"
        directory = self._dir(batch_id)
        (directory / "acceptance-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "acceptance-report.md").write_text(_markdown(summary), encoding="utf-8")
        payload["summaryAvailable"] = True
        payload["updatedAt"] = _now()
        self._write(batch_id, payload)

    def _update(self, batch_id: str, **changes) -> None:
        with self._lock:
            payload = self._require(batch_id)
            payload.update(changes, updatedAt=_now())
            self._write(batch_id, payload)

    def _require(self, batch_id: str) -> dict:
        payload = self.read(batch_id)
        if payload is None:
            raise KeyError(batch_id)
        return payload

    def _dir(self, batch_id: str) -> Path:
        if not batch_id.startswith("acceptance-") or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in batch_id):
            raise ValueError("验收批次编号非法")
        target = (self.root / batch_id).resolve()
        if self.root not in target.parents:
            raise ValueError("验收批次编号非法")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _batch_file(self, batch_id: str) -> Path:
        return self._dir(batch_id) / "batch.json"

    def _write(self, batch_id: str, payload: dict) -> None:
        target = self._batch_file(batch_id)
        temporary = target.with_suffix(".json.tmp")
        snapshot = json.loads(json.dumps(payload))
        with self._io_lock:
            self._states[batch_id] = snapshot
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            for attempt in range(20):
                try:
                    temporary.replace(target)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    sleep(0.01)


def dry_run_executor(compiled: CompiledScenario, repeat: int) -> dict:
    return {
        "run_id": f"dry-run-{compiled.scenario.id}-R{repeat}", "status": "dry_run_ready",
        "goal_status": "incomplete", "completion_reason": "dry_run_binding_validated",
        "replay_mode": "stable", "steps": [], "evidence_manifest": {}, "cleanup_report": {"objects": []},
    }


def _now() -> str:
    return datetime.now().astimezone().isoformat()
