from __future__ import annotations

import pytest
from pydantic import ValidationError

from gui_agent.domain.models import ActionType, Locator, Step
from gui_agent.execution.async_state import AsyncStateError, WebSocketEvidenceCollector, wait_for_state
from gui_agent.execution.lifecycle import cleanup_business_objects, reverse_cleanup_order
from gui_agent.execution.side_effects import confirmation_rule, evaluate_side_effect
from gui_agent.execution.compiler import compile_test
from gui_agent.onboarding.models import ProjectConfig, ScenarioConfig
from gui_agent.security.policy import SecurityError


class Redactor:
    def scrub(self, value: str) -> str:
        return value.replace("token-secret", "[REDACTED]")


class FakeLocator:
    def __init__(self, states: list[str]) -> None:
        self.states = iter(states)

    def inner_text(self, timeout: int) -> str:
        return next(self.states)


def state_step() -> Step:
    return Step(
        action=ActionType.WAIT_FOR_STATE,
        locator=Locator(test_id="state"),
        state_machine_id="job",
        business_object_id="E2E_job_1",
    )


def machine() -> dict:
    return {
        "id": "job", "states": ["queued", "running", "done", "failed"],
        "terminalStates": ["done"], "failureStates": ["failed"],
        "transitions": {"queued": ["running", "failed"], "running": ["done", "failed"]},
        "pollingIntervalMs": 100, "timeoutMs": 2000,
    }


def test_wait_for_state_records_valid_timeline(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.execution.async_state.resolve_action_locator", lambda *_: FakeLocator(["queued", "running", "done"]))
    evidence = wait_for_state(object(), state_step(), machine())
    assert evidence["classification"] == "success_terminal"
    assert [item["state"] for item in evidence["timeline"]] == ["queued", "running", "done"]


def test_wait_for_state_classifies_invalid_transition(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.execution.async_state.resolve_action_locator", lambda *_: FakeLocator(["queued", "done"]))
    with pytest.raises(AsyncStateError) as raised:
        wait_for_state(object(), state_step(), machine())
    assert raised.value.async_evidence["classification"] == "invalid_transition"


def test_websocket_evidence_keeps_only_business_fields() -> None:
    collector = WebSocketEvidenceCollector(Redactor())
    collector._frame("received", "ws://example.test/socket", '{"token":"token-secret","businessId":"B-1","state":"done","detail":"private"}')
    event = collector.timeline[0]
    assert event["businessFields"] == {"businessId": "B-1", "state": "done"}
    assert "token-secret" not in str(event)


def test_project_validates_state_machine_and_policy() -> None:
    project = ProjectConfig(
        id="p1", name="P", baseUrl="https://example.com",
        asyncStateMachines=[machine() | {"name": "Job"}],
        sideEffectPolicies=[{
            "id": "delete", "actionCategory": "delete", "objectType": "job",
            "decision": "confirm", "rollbackRule": "verify absent",
        }],
    )
    assert project.async_state_machines[0].terminal_states == ["done"]
    with pytest.raises(ValidationError):
        ProjectConfig(id="p2", name="P", baseUrl="https://example.com", asyncStateMachines=[machine() | {"name": "Job", "terminalStates": ["missing"]}])


def test_scenario_rejects_dependency_cycle() -> None:
    def cleanup(name: str, object_type: str) -> dict:
        return {"action": "click", "locator": {"text": "delete"}, "action_category": "delete", "object_type": object_type, "business_object_name": name, "cleanup_required": True}
    objects = [
        {"key": "a", "objectType": "job", "name": "E2E_a", "dependencies": ["b"], "cleanupStep": cleanup("E2E_a", "job")},
        {"key": "b", "objectType": "job", "name": "E2E_b", "dependencies": ["a"], "cleanupStep": cleanup("E2E_b", "job")},
    ]
    with pytest.raises(ValidationError, match="依赖存在环"):
        ScenarioConfig(id="s", projectId="p", name="S", preconditions=["无"], goal="g", expectedResults=["ok"], businessObjects=objects)


def test_cleanup_runs_reverse_dependency_order_and_collects_failures() -> None:
    objects = (
        {"key": "parent", "objectType": "job", "name": "E2E_parent", "dependencies": [], "cleanupStep": {}},
        {"key": "child", "objectType": "task", "name": "E2E_child", "dependencies": ["parent"], "cleanupStep": {}, "manualFallback": "remove child"},
    )
    assert [item["key"] for item in reverse_cleanup_order(objects)] == ["child", "parent"]
    report = cleanup_business_objects(objects, lambda item: {"key": item["key"]}, lambda item: item["key"] != "child")
    assert report["status"] == "failed"
    assert report["objects"][1]["status"] == "cleaned"
    assert report["manualActions"] == ["remove child"]


def test_side_effect_policy_requires_prefix_and_confirmation() -> None:
    step = Step(
        action="click", locator={"text": "delete"}, action_category="delete",
        object_type="job", business_object_name="E2E_job_1", cleanup_required=True,
    )
    evidence = evaluate_side_effect(step, ({
        "id": "delete", "actionCategory": "delete", "objectType": "job",
        "namePattern": "^E2E_", "decision": "confirm", "rollbackRule": "verify absent",
    },), environment_id=None, role="tester")
    assert confirmation_rule(evidence) == "side-effect:delete"
    invalid = step.model_copy(update={"business_object_name": "prod-job"})
    with pytest.raises(SecurityError, match="E2E_"):
        evaluate_side_effect(invalid, (), environment_id=None, role="tester")


def test_compiler_generates_state_machine_polling_without_fixed_sleep() -> None:
    from gui_agent.domain.models import TestPlan
    source, _ = compile_test(TestPlan(name="state", base_url="https://example.com", steps=[state_step()]))
    assert "ASYNC_STATE_MACHINE_JOB" in source
    assert "Invalid async transition" in source
    assert "pollingIntervalMs" in source
