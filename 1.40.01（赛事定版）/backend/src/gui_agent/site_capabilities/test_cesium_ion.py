from types import SimpleNamespace

from pydantic import SecretStr

from gui_agent.domain.models import ActionType, Locator, Step
from gui_agent.domain.results import Observation, PageSemanticSummary, Status, StepResult
from gui_agent.execution.confirmation import confirmation_match
from gui_agent.planning.agent_planner import AIAgentPlanner, AgentDecision, AgentScenario
from gui_agent.planning.ai_provider import AISettings
from gui_agent.site_capabilities import CesiumIonCapabilityPack, resolve_site_capability_pack


def _observation(
    route: str,
    *,
    loading: bool = False,
    accessibility: str = "",
) -> Observation:
    usage_facts = "Usage Data Streaming Imagery" if route.startswith("/usage") else ""
    return Observation(
        url=f"https://ion.cesium.com{route}",
        title="Cesium ion",
        accessibility_summary="\n".join(item for item in (usage_facts, accessibility) if item),
        semantic_summary=PageSemanticSummary(
            page_key=f"{route}|Cesium ion",
            route=route,
            controls=[{"role": "button", "name": "admin", "testId": "account-button-in-header"}],
            state_signals=["loading"] if loading else [],
            signature=route,
        ),
    )


def _result(index: int, route: str) -> StepResult:
    observation = _observation(route)
    return StepResult(
        index=index,
        action="click",
        target_summary=route,
        status=Status.PASSED,
        started_at=observation.captured_at,
        ended_at=observation.captured_at,
        after=observation,
    )


def test_registry_selects_cesium_pack() -> None:
    assert resolve_site_capability_pack("https://ion.cesium.com/assets").site_id == "cesium-ion"
    assert resolve_site_capability_pack("https://example.test").site_id == "generic-web"


def test_known_private_route_remains_classified_when_bounded_controls_omit_account() -> None:
    pack = CesiumIonCapabilityPack()
    observation = _observation("/assetdepot/354307")
    assert observation.semantic_summary is not None
    observation.semantic_summary.controls = []

    assert pack.page_stage(observation) == "asset_depot"


def test_login_page_is_not_mistaken_for_a_private_stage() -> None:
    pack = CesiumIonCapabilityPack()
    observation = _observation("/signin")
    observation.title = "Sign in | Cesium ion"
    observation.accessibility_summary = 'button "Sign in"'
    assert observation.semantic_summary is not None
    observation.semantic_summary.controls = []

    assert pack.page_stage(observation) == "unauthenticated"


def test_planner_calls_model_before_cesium_adapter_scheduler(tmp_path) -> None:
    calls = []

    class Gateway:
        @staticmethod
        def request(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                value=AgentDecision.model_validate({
                    "kind": "action",
                    "action": {
                        "action": "click",
                        "locator": {"role": "link", "name": "Usage"},
                        "description": "Open Usage from the latest observed navigation",
                        "effect_level": "read_only",
                    },
                    "reason": "The model selected the next observed Cesium section",
                    "progress_assessment": "progress",
                }),
                elapsed_ms=5,
                input_tokens=50,
                output_tokens=10,
                attempt_count=1,
                repair_count=0,
            )

    planner = object.__new__(AIAgentPlanner)
    planner.settings = AISettings(
        protocol="responses",
        base_url="https://api.example.test/v1",
        model="cesium-model-first",
        api_key=SecretStr("test-key"),
    )
    planner.scenario = AgentScenario(name="check", goal="check ion.cesium.com")
    planner.base_url = "https://ion.cesium.com"
    planner.visual_enabled = True
    planner.multimodal_required = True
    planner.successful_experiences = []
    planner.site_pack = CesiumIonCapabilityPack()
    planner.gateway = Gateway()

    screenshot = tmp_path / "cesium-current.png"
    screenshot.write_bytes(b"cesium-current-image")
    observation = _observation("/")
    observation.screenshot = "screenshots/cesium-current.png"
    result = planner.decide(
        observation, [_result(1, "/")], 1, screenshot_path=screenshot
    )

    assert len(calls) == 1
    assert result.protocol == "responses"
    assert result.model == "cesium-model-first"
    assert result.multimodal is True
    assert calls[0]["prompt"][0]["content"][1]["type"] == "input_image"
    assert result.decision.kind == "action"
    assert result.decision.action is not None
    assert result.decision.action.locator is not None
    assert result.decision.action.locator.name == "Usage"


def test_cesium_smoke_pack_drives_the_next_unvisited_stage() -> None:
    pack = CesiumIonCapabilityPack()
    scenario = SimpleNamespace(name="check", goal="check ion.cesium.com")
    current = _observation("/assets")

    remaining = pack.remaining_stages(current, [], scenario)
    action = pack.next_required_action(current, [], scenario)

    assert remaining[0] == "assets_search_submitted"
    assert action is not None
    assert action.locator is not None
    assert action.locator.name == "Search"
    assert action.action.value == "fill"
    assert action.effect_level.value == "read_only"


def test_generated_broad_scope_is_still_a_smoke_workflow() -> None:
    pack = CesiumIonCapabilityPack()
    scenario = SimpleNamespace(
        name="check ion.cesium.com",
        goal="Check My Assets, Asset Depot, Clips, Access Tokens, and Usage",
    )

    assert pack.remaining_stages(_observation("/assets"), [], scenario)[0] == "assets_search_submitted"


def test_write_scope_is_never_silently_reduced_to_read_only_smoke() -> None:
    pack = CesiumIonCapabilityPack()
    scenario = SimpleNamespace(
        name="complete Cesium modeling test",
        goal="Create a model, upload it, launch the simulation, and clean up the owned resource",
    )

    assert pack.required_stage_ids(scenario) == []
    assert pack.next_required_action(_observation("/assets"), [], scenario) is None


def test_assets_search_contract_submits_verifies_and_restores() -> None:
    pack = CesiumIonCapabilityPack()
    scenario = SimpleNamespace(name="check", goal="check ion.cesium.com")
    default_facts = 'searchbox "Search"\nGoogle Maps 2D\nCesium OSM Buildings\n11 assets total'
    filled_facts = 'searchbox "Search": Google\nGoogle Maps 2D\nCesium OSM Buildings\n11 assets total'
    filtered_facts = 'searchbox "Search": Google\nGoogle Maps 2D\n6 assets total'
    cleared_facts = 'searchbox "Search"\nGoogle Maps 2D\n6 assets total'

    initial = _observation("/assets", accessibility=default_facts)
    fill = pack.required_followup_action(initial, [], scenario)
    assert fill is not None and fill.action.value == "fill" and fill.value == "Google"
    assert fill.state_machine_id == "cesium.assets.search.fill"
    assert "CESIUM_CHECK" not in (fill.description or "")

    filled = _observation("/assets", accessibility=filled_facts)
    submit = pack.required_followup_action(filled, [_result(1, "/assets")], scenario)
    assert submit is not None and submit.action.value == "press" and submit.value == "Enter"
    assert submit.state_machine_id == "cesium.assets.search.apply"
    assert "submit" not in (submit.description or "").lower()
    assert confirmation_match(submit) is None

    filtered = _observation("/assets?search=Google", accessibility=filtered_facts)
    filtered_result = StepResult(
        index=2,
        action="press",
        target_summary="submit search",
        status=Status.PASSED,
        started_at=filtered.captured_at,
        ended_at=filtered.captured_at,
        after=filtered,
    )
    clear = pack.required_followup_action(filtered, [filtered_result], scenario)
    assert clear is not None and clear.action.value == "clear"
    assert clear.state_machine_id == "cesium.assets.search.clear"

    cleared = _observation("/assets?search=Google", accessibility=cleared_facts)
    restore = pack.required_followup_action(cleared, [filtered_result], scenario)
    assert restore is not None and restore.action.value == "press" and restore.value == "Enter"
    assert restore.state_machine_id == "cesium.assets.search.restore"

    restored = _observation("/assets", accessibility=default_facts)
    remaining = pack.remaining_stages(restored, [filtered_result], scenario)
    assert remaining[0] == "asset_depot"


def test_confirmation_ignores_explicit_negative_intent() -> None:
    assert confirmation_match(Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="Run check"),
        description="Do not delete any existing resource",
    )) is None
    assert confirmation_match(Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="Delete"),
        description="Delete the ledger-owned E2E resource",
    )) == "delete"


def test_cesium_smoke_pack_binds_terminal_assertions_after_all_stages() -> None:
    pack = CesiumIonCapabilityPack()
    scenario = SimpleNamespace(name="check", goal="check ion.cesium.com")
    routes = [
        "/", "/assets", "/assets?search=Google", "/assets", "/assetdepot", "/clips", "/tokens", "/usage",
        "/account", "/account/billing", "/account/license", "/account/labels",
        "/account/applications", "/account/developer",
    ]
    history = []
    for index, route in enumerate(routes, start=1):
        if route == "/assets?search=Google":
            observation = _observation(route, accessibility='searchbox "Search": Google\nGoogle Maps 2D\n6 assets total')
            history.append(StepResult(
                index=index,
                action="press",
                target_summary=route,
                status=Status.PASSED,
                started_at=observation.captured_at,
                ended_at=observation.captured_at,
                after=observation,
            ))
        elif route == "/assets" and any(item.after and "search=Google" in item.after.url for item in history):
            observation = _observation(route, accessibility='searchbox "Search"\nCesium OSM Buildings\n11 assets total')
            history.append(StepResult(
                index=index,
                action="press",
                target_summary=route,
                status=Status.PASSED,
                started_at=observation.captured_at,
                ended_at=observation.captured_at,
                after=observation,
            ))
        else:
            history.append(_result(index, route))
    current = _observation("/account/teams")

    assertions = pack.terminal_assertions(current, history, scenario)

    assert not pack.remaining_stages(current, history, scenario)
    assert [item.type.value for item in assertions] == ["url_contains", "visible"]
    assert assertions[0].expected == "/account/teams"


def test_usage_stage_requires_all_loading_indicators_to_finish() -> None:
    pack = CesiumIonCapabilityPack()
    scenario = SimpleNamespace(name="check", goal="check ion.cesium.com")
    current = _observation("/usage", loading=True)

    assert "usage" in pack.remaining_stages(current, [], scenario)
    followup = pack.required_followup_action(current, [], scenario)

    assert followup is not None
    assert followup.action.value == "wait_for"
    assert followup.locator is not None
    assert followup.locator.css == ".loading-message"
    assert followup.state_machine_id == "site_terminal_loading"


def test_intrinsically_read_only_recovery_action_gets_cesium_policy_metadata() -> None:
    payload = {
        "action": "wait_for",
        "locator": {"css": ".fa-spinner"},
        "value": "hidden",
    }

    CesiumIonCapabilityPack().normalize_action_payload(payload)

    assert payload["effect_kind"] == "browse_search_filter_sort"
    assert payload["effect_level"] == "read_only"


def test_cesium_policy_metadata_is_not_inferred_for_clicks() -> None:
    payload = {"action": "click", "locator": {"role": "button", "name": "Share"}}

    CesiumIonCapabilityPack().normalize_action_payload(payload)

    assert "effect_kind" not in payload
    assert "effect_level" not in payload


def test_story_editor_navigation_click_is_classified_read_only() -> None:
    payload = {
        "action": "click",
        "locator": {"role": "link", "name": "Edit story"},
    }

    CesiumIonCapabilityPack().normalize_action_payload(payload)

    assert payload["effect_kind"] == "browse_search_filter_sort"
    assert payload["effect_level"] == "read_only"


def test_story_annotation_entry_binds_reversible_write_without_auto_delete() -> None:
    payload = {
        "action": "click",
        "locator": {"role": "button", "name": "Add polyline"},
    }

    CesiumIonCapabilityPack().normalize_action_payload(payload)

    assert payload["effect_kind"] == "story_annotation_measurement"
    assert payload["effect_level"] == "reversible_write"
    assert "明确批准删除" in payload["cleanup_action"]
