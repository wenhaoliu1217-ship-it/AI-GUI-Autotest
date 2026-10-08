from types import SimpleNamespace

from gui_agent.domain.models import ActionType, Step
from gui_agent.domain.results import Observation, PageSemanticSummary
from gui_agent.execution.agent_runner import _stable_replay_required
from gui_agent.site_capabilities import GenericWebCapabilityPack


def _scenario(*expected_results: str):
    return SimpleNamespace(expected_results=list(expected_results))


def test_generic_context_exposes_current_page_state_without_form_values() -> None:
    observation = Observation(
        url="https://example.test/orders/42",
        title="Orders",
        semantic_summary=PageSemanticSummary(
            page_key="/orders/42|Orders",
            route="/orders/42",
            heading="Order details",
            dialogs=[{"role": "dialog", "name": "Confirm"}],
            controls=[{"role": "button", "name": "Save", "valueState": "non_empty"}],
            components=[{"kind": "searchable_select"}],
            forms=[{"name": "order", "controls": 3, "submitButtons": 1}],
            state_signals=["modal_visible"],
            blocking_errors=[],
            canvas={"count": 1, "nonEmptySurface": True, "loading": False},
        ),
    )

    context = GenericWebCapabilityPack().planner_context(observation, [], _scenario())
    state = context["currentPageState"]

    assert context["sitePack"] == "generic-web"
    assert context["sitePackVersion"] == "2"
    assert state["route"] == "/orders/42"
    assert state["heading"] == "Order details"
    assert state["controlCount"] == 1
    assert state["componentCount"] == 1
    assert state["stateSignals"] == ["modal_visible"]
    assert state["canvas"] == {"count": 1, "nonEmptySurface": True, "loading": False}
    assert "value" not in str(state)


def test_generic_terminal_assertions_use_only_explicit_expectations() -> None:
    pack = GenericWebCapabilityPack()
    observation = Observation(url="https://example.test/orders/42")

    assertions = pack.terminal_assertions(
        observation,
        [],
        _scenario("页面显示“Order saved”", "URL 包含 /orders/42", "页面显示“Order saved”"),
    )

    assert [(item.type.value, item.expected) for item in assertions] == [
        ("text_contains", "Order saved"),
        ("url_contains", "/orders/42"),
    ]
    assert pack.terminal_assertions(observation, [], _scenario()) == []


def test_generic_navigation_transition_is_path_based() -> None:
    before = Observation(url="https://example.test/orders")
    step = Step(action=ActionType.NAVIGATE, target="/orders/42")

    assert GenericWebCapabilityPack().expected_transition(step, before) == {
        "urlPathPrefix": "/orders/42"
    }


def test_completion_proof_does_not_require_unavailable_generic_replay() -> None:
    pack = GenericWebCapabilityPack()

    assert _stable_replay_required(pack, []) is False

