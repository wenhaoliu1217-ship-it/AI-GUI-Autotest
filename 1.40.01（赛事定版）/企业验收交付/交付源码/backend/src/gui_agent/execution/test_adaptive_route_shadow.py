from __future__ import annotations

from gui_agent.domain.results import Observation, PageSemanticSummary
from gui_agent.execution.agent_runner import (
    _emit_adaptive_route_shadow,
    _emit_decision_route_gate,
)


class Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict]] = []

    def event(self, name: str, **payload) -> None:
        self.items.append((name, payload))


def test_shadow_route_is_auditable_and_does_not_claim_production_control() -> None:
    events = Events()
    observation = Observation(
        url="https://example.test/app",
        semantic_summary=PageSemanticSummary(
            page_key="app|home",
            route="/app",
            signature="sig-1",
        ),
    )

    _emit_adaptive_route_shadow(events, before=None, after=observation)

    assert len(events.items) == 1
    name, payload = events.items[0]
    assert name == "adaptive_route_shadow"
    assert payload["production_policy"] == "mandatory_multimodal"
    assert payload["proposed_route"] == "call_multimodal_agent"
    assert payload["change_kind"] == "initial"


def test_predecision_gate_is_enforced_without_claiming_action_cache() -> None:
    events = Events()
    observation = Observation(
        url="https://example.test/app",
        semantic_summary=PageSemanticSummary(
            page_key="app|home",
            route="/app",
            signature="sig-1",
        ),
    )

    _emit_decision_route_gate(events, before=None, after=observation)

    name, payload = events.items[0]
    assert name == "decision_route_gate"
    assert payload["safety_gate_enforced"] is True
    assert payload["route"] == "call_multimodal_agent"
    assert payload["active_contract"] is False
    assert payload["contract_execution_enabled"] is False
    assert payload["adaptive_execution_enabled"] is False
    assert payload["model_call_required"] is True
