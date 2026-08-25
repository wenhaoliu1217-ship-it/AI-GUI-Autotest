"""JSON-lines entry point for a single browser run inside a locked-down container."""

from __future__ import annotations

import json
import os
import queue
import sys
from dataclasses import fields, replace
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import uuid4

from pydantic import SecretStr

from ..domain.models import TestPlan
from ..planning.agent_planner import AIAgentPlanner, AgentScenario
from ..planning.ai_provider import AISettings
from ..planning.visual_adapter import OpenAIVisualAdapter
from ..planning.replay_planner import AdaptiveReplayPlanner
from .runner import RunnerConfig, run_plan


_OUTPUT_LOCK = Lock()


def _send(message: dict) -> None:
    with _OUTPUT_LOCK:
        sys.stdout.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


def _settings(payload: dict) -> AISettings:
    return AISettings(
        protocol=payload["protocol"],
        base_url=payload["base_url"],
        model=payload["model"],
        api_key=SecretStr(payload["api_key"]),
        input_cost_per_million=payload.get("input_cost_per_million"),
        output_cost_per_million=payload.get("output_cost_per_million"),
    )


def _config(spec: dict, cancel_event: Event, decisions: queue.Queue) -> RunnerConfig:
    payload = dict(spec["config"])
    payload["artifacts_root"] = Path("/work/artifacts")
    tuple_fields = {
        "allowed_hosts", "forbidden_actions", "environment_variables", "secret_refs",
        "ignore_rules", "screenshot_mask_selectors", "viewport",
        "file_assets",
        "async_state_machines", "side_effect_policies", "component_adapters", "business_objects",
        "test_files", "cesium_owned_resources",
    }
    for name in tuple_fields:
        if name in payload:
            payload[name] = tuple(
                tuple(item) if isinstance(item, list) else item for item in payload[name]
            )
    allowed = {item.name for item in fields(RunnerConfig)}
    payload = {key: value for key, value in payload.items() if key in allowed}
    planner_payload = spec.get("agent_planner")
    if planner_payload:
        if planner_payload.get("kind") == "adaptive_replay":
            payload["agent_planner"] = AdaptiveReplayPlanner(TestPlan.model_validate(planner_payload["plan"]))
        else:
            payload["agent_planner"] = AIAgentPlanner(
                _settings(planner_payload["settings"]),
                AgentScenario.model_validate(planner_payload["scenario"]),
                planner_payload["base_url"],
                visual_enabled=bool(planner_payload.get("visual_enabled")),
            )
    visual_payload = spec.get("visual_adapter")
    if visual_payload:
        payload["visual_adapter"] = OpenAIVisualAdapter(
            _settings(visual_payload["settings"]),
            minimum_confidence=float(visual_payload.get("minimum_confidence", 0.7)),
        )
    config = RunnerConfig(**payload)

    def progress(value: dict) -> None:
        _send({"type": "progress", "payload": value})

    def confirm(step, index: int, rule: str) -> bool:
        confirmation_id = f"confirmation-{uuid4().hex[:12]}"
        requested = {
            "id": confirmation_id,
            "step_index": index,
            "action": step.action.value,
            "target": step.description
            or (step.locator.describe() if step.locator else step.target)
            or step.action.value,
            "rule": rule,
        }
        _send({"type": "confirmation_requested", "payload": requested})
        decision = "rejected"
        actor = "cancel_request"
        while not cancel_event.is_set():
            try:
                message = decisions.get(timeout=0.25)
            except queue.Empty:
                continue
            if message.get("id") != confirmation_id:
                continue
            decision = str(message.get("decision", "rejected"))
            actor = str(message.get("actor", "local_user"))
            break
        resolved = {**requested, "decision": decision, "actor": actor}
        config.confirmation_history.append(resolved)
        _send({"type": "confirmation_resolved", "payload": resolved})
        return decision == "approved"

    def clarify(question: str, round_number: int) -> str | None:
        clarification_id = f"clarification-{uuid4().hex[:12]}"
        requested = {
            "id": clarification_id,
            "round": round_number,
            "question": question,
        }
        _send({"type": "clarification_requested", "payload": requested})
        answer = None
        actor = "cancel_request"
        while not cancel_event.is_set():
            try:
                message = decisions.get(timeout=0.25)
            except queue.Empty:
                continue
            if message.get("type") != "clarification_answer" or message.get("id") != clarification_id:
                continue
            answer = message.get("answer")
            actor = str(message.get("actor", "local_user"))
            break
        _send({
            "type": "clarification_resolved",
            "payload": {**requested, "answer": answer, "actor": actor},
        })
        return str(answer) if answer else None

    return replace(
        config,
        cancel_event=cancel_event,
        progress_callback=progress,
        confirmation_callback=confirm,
        clarification_callback=clarify,
    )


def main() -> None:
    first = sys.stdin.readline()
    if not first:
        raise SystemExit("missing container run specification")
    spec = json.loads(first)
    for name, value in spec.get("environment", {}).items():
        os.environ[str(name)] = str(value)
    cancel_event = Event()
    decisions: queue.Queue = queue.Queue()

    def receive() -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("type") == "cancel":
                cancel_event.set()
            elif message.get("type") in {"confirmation_decision", "clarification_answer"}:
                decisions.put(message)

    Thread(target=receive, name="container-control", daemon=True).start()
    try:
        plan = TestPlan.model_validate(spec["plan"])
        config = _config(spec, cancel_event, decisions)
        result, _ = run_plan(plan, config)
        _send({"type": "result", "payload": result.model_dump(mode="json")})
    except BaseException as exc:
        _send({"type": "error", "error": str(exc), "errorType": type(exc).__name__})


if __name__ == "__main__":
    main()
