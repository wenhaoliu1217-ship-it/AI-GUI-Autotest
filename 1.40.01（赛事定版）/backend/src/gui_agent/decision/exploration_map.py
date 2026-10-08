"""Bounded run-local map of observed pages and transitions."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..domain.results import Observation, Status, StepResult
from .recovery_contract import observation_state_key


def build_exploration_map(
    observation: Observation,
    history: list[StepResult],
    *,
    max_nodes: int = 30,
    max_transitions: int = 60,
) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    transitions: list[dict[str, Any]] = []
    failures: Counter[str] = Counter()

    for result in history:
        before_key = _add_node(nodes, result.before)
        after_key = _add_node(nodes, result.after)
        if result.status == Status.ERROR or result.progress_assessment == "no_progress":
            failures[after_key or before_key] += 1
        if before_key or after_key:
            transitions.append({
                "step": result.index,
                "from": before_key,
                "to": after_key,
                "action": result.action,
                "status": result.status.value,
                "progress": result.progress_assessment,
            })

    current_key = _add_node(nodes, observation)
    ordered = list(nodes.values())[-max_nodes:]
    retained = {item["stateKey"] for item in ordered}
    bounded_transitions = [
        item for item in transitions
        if (not item["from"] or item["from"] in retained)
        and (not item["to"] or item["to"] in retained)
    ][-max_transitions:]
    return {
        "schemaVersion": 1,
        "scope": "current_run_only",
        "currentObservationOverrides": True,
        "currentStateKey": current_key,
        "visitedStateCount": len(nodes),
        "nodes": [
            {**item, "failureCount": failures[item["stateKey"]]}
            for item in ordered
        ],
        "transitions": bounded_transitions,
        "rules": [
            "This map records observed transitions, not a fixed workflow script.",
            "The latest screenshot and semantic observation override every prior node.",
            "A transition with no progress must not be treated as a successful route.",
        ],
    }


def _add_node(nodes: dict[str, dict[str, Any]], observation: Observation | None) -> str:
    if observation is None:
        return ""
    key = observation_state_key(observation)
    if not key:
        return ""
    summary = observation.semantic_summary
    nodes[key] = {
        "stateKey": key,
        "url": observation.url,
        "title": observation.title,
        "route": summary.route if summary is not None else "",
        "heading": summary.heading if summary is not None else "",
        "dialogs": [
            str(item.get("identity") or item.get("name") or "")
            for item in (summary.dialogs if summary is not None else [])
            if str(item.get("identity") or item.get("name") or "")
        ][:8],
    }
    return key
