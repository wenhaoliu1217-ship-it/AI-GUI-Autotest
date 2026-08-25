"""Bounded async-state polling and redacted WebSocket evidence."""

from __future__ import annotations

import json
from datetime import datetime
from time import monotonic, sleep
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..domain.models import Step
from ..locating.strategies import resolve_action_locator


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class AsyncStateError(RuntimeError):
    def __init__(self, message: str, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.async_evidence = evidence


class WebSocketEvidenceCollector:
    def __init__(self, redactor, *, payload_limit: int = 512) -> None:
        self.redactor = redactor
        self.payload_limit = payload_limit
        self.timeline: list[dict[str, Any]] = []
        self._connections: dict[str, int] = {}

    def attach(self, page) -> None:
        page.on("websocket", self._on_socket)

    def _on_socket(self, websocket) -> None:
        url = self.redactor.scrub(websocket.url)
        reconnect = self._connections.get(url, 0)
        self._connections[url] = reconnect + 1
        self.timeline.append({"timestamp": _now(), "kind": "open", "url": url, "reconnectCount": reconnect})
        websocket.on("framesent", lambda payload: self._frame("sent", url, payload))
        websocket.on("framereceived", lambda payload: self._frame("received", url, payload))
        websocket.on("close", lambda: self.timeline.append({"timestamp": _now(), "kind": "close", "url": url}))
        websocket.on("socketerror", lambda error: self.timeline.append({
            "timestamp": _now(), "kind": "socket_error", "url": url,
            "error": self.redactor.scrub(str(error))[:self.payload_limit],
        }))

    def _frame(self, direction: str, url: str, payload: Any) -> None:
        raw = payload if isinstance(payload, str) else "<binary>"
        summary: dict[str, Any] = {}
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                allowed = {"id", "businessId", "business_id", "objectId", "object_id", "state", "status", "event", "type"}
                summary = {key: self.redactor.scrub(str(item))[:200] for key, item in value.items() if key in allowed}
        except (json.JSONDecodeError, TypeError):
            pass
        self.timeline.append({
            "timestamp": _now(), "kind": "frame", "direction": direction, "url": url,
            "payloadBytes": len(raw.encode("utf-8", errors="replace")), "businessFields": summary,
        })


def wait_for_state(page, step: Step, machine: dict[str, Any]) -> dict[str, Any]:
    locator = resolve_action_locator(page, step.locator)  # type: ignore[arg-type]
    states = set(machine.get("states", []))
    terminal = set(machine.get("terminalStates", machine.get("terminal_states", [])))
    failures = set(machine.get("failureStates", machine.get("failure_states", [])))
    transitions = machine.get("transitions", {})
    interval_ms = int(machine.get("pollingIntervalMs", machine.get("polling_interval_ms", 1000)))
    timeout_ms = int(machine.get("timeoutMs", machine.get("timeout_ms", 120_000)))
    started = monotonic()
    previous: str | None = None
    timeline: list[dict[str, Any]] = []
    while (monotonic() - started) * 1000 <= timeout_ms:
        state = locator.inner_text(timeout=min(interval_ms, 5000)).strip()
        event = {"timestamp": _now(), "state": state}
        if state not in states:
            event["classification"] = "unknown_state"
            timeline.append(event)
            raise AsyncStateError(
                f"异步对象 {step.business_object_id} 返回未声明状态：{state}",
                {"stateMachineId": step.state_machine_id, "businessObjectId": step.business_object_id, "classification": "unknown_state", "timeline": timeline},
            )
        if previous is not None and state != previous and state not in set(transitions.get(previous, [])):
            event["classification"] = "invalid_transition"
            timeline.append(event)
            raise AsyncStateError(
                f"异步状态非法迁移：{previous} -> {state}",
                {"stateMachineId": step.state_machine_id, "businessObjectId": step.business_object_id, "classification": "invalid_transition", "timeline": timeline},
            )
        event["classification"] = "observed"
        timeline.append(event)
        if state in failures:
            event["classification"] = "failure_terminal"
            raise AsyncStateError(
                f"异步对象 {step.business_object_id} 进入失败状态：{state}",
                {"stateMachineId": step.state_machine_id, "businessObjectId": step.business_object_id, "finalState": state, "classification": "failure_terminal", "timeline": timeline},
            )
        if state in terminal:
            event["classification"] = "success_terminal"
            return {
                "stateMachineId": step.state_machine_id, "businessObjectId": step.business_object_id,
                "finalState": state, "classification": "success_terminal", "timeline": timeline,
                "elapsedMs": int((monotonic() - started) * 1000),
            }
        previous = state
        sleep(interval_ms / 1000)
    raise AsyncStateError(
        f"异步对象 {step.business_object_id} 在 {timeout_ms}ms 内未到达终态",
        {"stateMachineId": step.state_machine_id, "businessObjectId": step.business_object_id, "classification": "timeout", "timeline": timeline, "elapsedMs": int((monotonic() - started) * 1000)},
    )
