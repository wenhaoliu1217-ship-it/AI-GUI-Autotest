from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from gui_agent.api.server import (
    AgentRunRequest,
    DraftRequest,
    ModelDataAuthorizationRequest,
    _auto_select_project_for_target,
    _canonical_project_target,
    _promote_shallow_project_for_target,
    _resolve_saved_project_url,
    _classify_default_cesium_agent_plan,
    _effective_project_business_context,
    _normalized_origin,
    _reusable_saved_session,
    _validate_model_data_authorization,
    execute_agent_run,
    generate_plan,
    health,
    _run_list_payload,
    _run_progress_payload,
    _frontend_cache_headers,
    _session_recording_response,
    app,
)
from gui_agent.benchmarks.cesium_ion.policy import validate_cesium_plan
from gui_agent.domain.models import EffectLevel, TestPlan as Plan


def test_health_reports_model_first_planner() -> None:
    payload = health()

    assert payload["status"] == "ok"
    assert payload["appVersion"] == "1.32.01"
    assert payload["frontendBundleVersion"] == "1.32.01"
    assert payload["versionSkew"] is False
    assert payload["versionCompatibility"] == "supported_by_api_contract"
    assert payload["apiContractVersion"] == "ai-gui-http-v1"
    assert payload["frontendApiContractVersion"] == "ai-gui-http-v1"
    assert payload["frontendManifestVerified"] is True
    assert payload["contractCompatible"] is True
    assert payload["planner"] == (
        "mandatory-multimodal-stepwise-agent + run-local-recovery-contract + success-only-experience + deterministic-hard-safety"
    )
    assert payload["decisionPolicy"] == "mandatory_multimodal"
    assert payload["adaptiveRouter"] == "run_local_recovery_contract_enabled; cross_action_cache_enabled"
    assert payload["crossActionCache"] == {
        "enabled": True,
        "policy": "state_validated_advisory_v1",
        "successOnly": True,
        "requiresFreshGrounding": True,
    }
    assert "runnerAvailable" in payload
    assert "dockerEngineReady" in payload
    assert "runtimePythonReady" in payload
    assert "playwrightBrowserReady" in payload
    assert payload["hostPlatform"]["system"] in {"windows", "linux", "macos", "unknown"}
    assert payload["runnerMode"] in {"container", "process", "thread"}


def test_frontend_cache_policy_does_not_cache_entry_html_or_api() -> None:
    assert _frontend_cache_headers("/") == {
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
    }
    assert _frontend_cache_headers("/api/health")["Cache-Control"] == "no-store, max-age=0"
    assert _frontend_cache_headers("/assets/index-hash.js")["Cache-Control"].endswith("immutable")
    assert _frontend_cache_headers("/favicon.ico") == {}


def test_login_recording_poll_and_reload_routes_are_registered() -> None:
    routes = {(method, route.path) for route in app.routes for method in getattr(route, "methods", set())}

    assert ("GET", "/api/projects/{project_id}/session-recordings/{recording_id}") in routes
    assert ("POST", "/api/projects/{project_id}/session-recordings/{recording_id}/reload") in routes


def test_login_recording_response_contains_live_diagnostics() -> None:
    payload = _session_recording_response(SimpleNamespace(
        id="recording-test",
        project_id="project-test",
        status="recording",
        browser_name="Microsoft Edge",
        last_url="https://example.test/login",
        reload_count=2,
    ))

    assert payload["projectId"] == "project-test"
    assert payload["diagnostics"]["lastUrl"] == "https://example.test/login"
    assert payload["diagnostics"]["reloadCount"] == 2


def test_finished_run_list_payload_is_small_and_prebuilt_ui_compatible() -> None:
    payload = _run_list_payload({
        "run_id": "run-finished",
        "plan_name": "Completed test",
        "status": "passed",
        "started_at": "2026-08-13T00:00:00+00:00",
        "ended_at": "2026-08-13T00:00:01+00:00",
        "steps": [{"index": 1, "before": {"accessibility_summary": "x" * 10000}}],
        "assertions": [{"status": "passed"}],
        "findings": [{"id": "finding-1", "facts": ["x" * 10000]}],
        "model_call_records": [{"reason": "x" * 10000}],
        "goal_status": "achieved",
        "completion_gate": {"goal_status": "achieved"},
    })

    assert payload["summary_only"] is True
    assert payload["detail_endpoint"] == "/api/runs/run-finished"
    assert payload["steps"] == []
    assert payload["assertions"] == []
    assert payload["findings"] == []
    assert payload["model_call_records"] == []
    assert "completion_gate" not in payload


def test_active_run_list_payload_keeps_live_steps() -> None:
    payload = _run_list_payload({
        "run_id": "run-active",
        "plan_name": "Live test",
        "status": "running",
        "started_at": "2026-08-13T00:00:00+00:00",
        "ended_at": "2026-08-13T00:00:00+00:00",
        "steps": [{"index": 1, "status": "passed"}],
        "assertions": [],
        "findings": [],
        "model_call_records": [],
    })

    assert payload.get("summary_only") is None
    assert payload["steps"][0]["index"] == 1


def test_active_run_progress_payload_only_returns_records_after_cursors() -> None:
    payload = _run_progress_payload({
        "run_id": "run-active",
        "plan_name": "Live test",
        "status": "running",
        "started_at": "2026-09-05T00:00:00+00:00",
        "ended_at": "2026-09-05T00:00:02+00:00",
        "steps": [
            {"index": 1, "status": "passed", "before": {"accessibility_summary": "x" * 10000}},
            {"index": 2, "status": "passed", "after": {"accessibility_summary": "y" * 10000}},
        ],
        "assertions": [],
        "reproduction_steps": [],
        "cause_hints": [],
        "findings": [],
        "model_call_records": [
            {"index": 1, "reason": "first"},
            {"index": 2, "reason": "second"},
        ],
        "confirmation_history": [{"id": "a"}, {"id": "b"}],
    }, after_step=1, after_model_call=1, after_confirmation=1)

    assert payload["delta"] is True
    assert [item["index"] for item in payload["steps"]] == [2]
    assert [item["index"] for item in payload["model_call_records"]] == [2]
    assert payload["confirmation_history"] == [{"id": "b"}]
    assert payload["next_step_index"] == 2
    assert payload["next_model_call_index"] == 2
    assert payload["next_confirmation_offset"] == 2


def test_active_run_progress_payload_does_not_repeat_large_step_evidence() -> None:
    payload = _run_progress_payload({
        "run_id": "run-active",
        "plan_name": "Live test",
        "status": "running",
        "started_at": "2026-09-05T00:00:00+00:00",
        "ended_at": "2026-09-05T00:00:02+00:00",
        "steps": [{"index": 1, "before": {"accessibility_summary": "x" * 100000}}],
        "assertions": [],
        "model_call_records": [],
    }, after_step=1)

    assert payload["steps"] == []
    assert len(str(payload)) < 5000


def test_default_cesium_agent_plan_is_classified_as_read_only() -> None:
    plan = Plan.model_validate(
        {
            "name": "Cesium read-only exploration",
            "base_url": "https://ion.cesium.com",
            "steps": [
                {
                    "action": "navigate",
                    "target": "/",
                    "description": "Agent bootstrap navigation",
                }
            ],
            "assertions": [],
        }
    )

    _classify_default_cesium_agent_plan(plan, generated_from_target=True)

    assert plan.steps[0].effect_kind == "browse_search_filter_sort"
    assert plan.steps[0].effect_level is EffectLevel.READ_ONLY
    validate_cesium_plan(plan, plan.base_url, [])


def test_generated_cesium_navigation_plan_is_ready_for_policy_review() -> None:
    payload = generate_plan(
        DraftRequest(
            name="Cesium login handoff regression",
            targetUrl="https://ion.cesium.com/stories/example",
            flow="导航到目标 URL",
            expectation="确认看到“Stories”",
        )
    )

    plan = Plan.model_validate(payload["plan"])
    assert payload["warnings"] == []
    assert plan.steps[0].effect_kind == "browse_search_filter_sort"
    assert plan.steps[0].effect_level is EffectLevel.READ_ONLY
    validate_cesium_plan(plan, plan.base_url, [])


def test_reusable_saved_session_accepts_a_valid_existing_login() -> None:
    project = SimpleNamespace(id="project-test", allowed_hosts=["example.test"])
    store = SimpleNamespace(
        load_session=lambda _project_id: {
            "cookies": [{
                "name": "session",
                "value": "opaque",
                "domain": "example.test",
                "path": "/",
                "expires": 4102444800,
            }],
            "origins": [],
        },
        get_session_metadata=lambda _project_id: SimpleNamespace(
            imported_at="2026-07-31T00:00:00+00:00"
        ),
    )

    metadata = _reusable_saved_session(project, store)

    assert metadata is not None
    assert metadata.expiry_status == "active"
    assert metadata.imported_at == "2026-07-31T00:00:00+00:00"


def test_reusable_saved_session_rejects_an_expired_login() -> None:
    project = SimpleNamespace(id="project-test", allowed_hosts=["example.test"])
    store = SimpleNamespace(
        load_session=lambda _project_id: {
            "cookies": [{
                "name": "session",
                "value": "opaque",
                "domain": "example.test",
                "path": "/",
                "expires": 1,
            }],
            "origins": [],
        },
        get_session_metadata=lambda _project_id: SimpleNamespace(
            imported_at="2020-01-01T00:00:00+00:00"
        ),
    )

    assert _reusable_saved_session(project, store) is None


def test_project_target_uses_configured_http_authority_for_same_host() -> None:
    project = SimpleNamespace(base_url="http://192.168.31.218:7991/#/login")

    target = _canonical_project_target(
        "https://192.168.31.218:7991/#/scenario/list", project
    )

    assert target == "http://192.168.31.218:7991/#/scenario/list"


def test_project_target_does_not_rewrite_a_different_host() -> None:
    project = SimpleNamespace(base_url="http://192.168.31.218:7991/#/login")

    target = _canonical_project_target("https://open.weixin.qq.com/login", project)

    assert target == "https://open.weixin.qq.com/login"


def test_project_target_does_not_rewrite_a_different_port() -> None:
    project = SimpleNamespace(base_url="http://192.168.31.218:7991/#/login")

    target = _canonical_project_target(
        "http://192.168.31.218:8080/#/scenario/list", project
    )

    assert target == "http://192.168.31.218:8080/#/scenario/list"


def test_project_target_can_correct_scheme_when_both_use_default_authority() -> None:
    project = SimpleNamespace(base_url="http://example.test/#/login")

    target = _canonical_project_target("https://example.test/#/home", project)

    assert target == "http://example.test/#/home"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://Example.Test:80/path", "http://example.test"),
        ("https://Example.Test:443/path", "https://example.test"),
        ("http://192.168.31.218:7991/#/login", "http://192.168.31.218:7991"),
    ],
)
def test_normalized_origin_canonicalizes_default_ports(url: str, expected: str) -> None:
    assert _normalized_origin(url) == expected


def test_model_authorization_accepts_the_exact_target_origin() -> None:
    authorization = ModelDataAuthorizationRequest(
        authorizedOrigin="http://192.168.31.218:7991",
        allowDom=True,
        allowScreenshots=True,
    )

    result = _validate_model_data_authorization(
        "http://192.168.31.218:7991/#/mineModelList", authorization
    )

    assert result == "http://192.168.31.218:7991"


def test_model_authorization_rejects_same_host_with_different_scheme() -> None:
    authorization = ModelDataAuthorizationRequest(
        authorizedOrigin="https://192.168.31.218:7991",
        allowDom=True,
        allowScreenshots=True,
    )

    with pytest.raises(HTTPException, match="origin 与实际目标不一致") as exc:
        _validate_model_data_authorization(
            "http://192.168.31.218:7991/#/mineModelList", authorization
        )

    assert exc.value.status_code == 422


def test_model_authorization_keeps_legacy_host_requests_compatible() -> None:
    authorization = ModelDataAuthorizationRequest(
        siteHost="example.test",
        allowDom=True,
        allowScreenshots=True,
    )

    assert _validate_model_data_authorization(
        "https://example.test/path", authorization
    ) == "https://example.test"


def test_agent_request_names_the_current_real_decision_policy() -> None:
    payload = AgentRunRequest.model_validate({
        "targetUrl": "https://example.test",
        "scenario": {"name": "inspect", "goal": "inspect current page"},
        "settings": {
            "protocol": "chat_completions",
            "baseUrl": "https://model.example.test/v1",
            "model": "test-model",
            "apiKey": "test-key",
        },
        "modelDataAuthorization": {
            "authorizedOrigin": "https://example.test",
            "allowDom": True,
            "allowScreenshots": True,
        },
    })

    assert payload.decisionPolicy == "mandatory_multimodal"
    assert payload.enableVisualFallback is None


def test_agent_run_keeps_project_context_separate_from_capability_reference() -> None:
    project = SimpleNamespace(
        business_context=SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "description": "GAEALaViC test project",
                "operatingBoundaries": [],
            }
        ),
        commerce_profile=SimpleNamespace(
            model_dump=lambda **_kwargs: {"enabled": False}
        ),
    )

    context = _effective_project_business_context(
        project,
        "http://192.168.31.218:7991/#/mineModelList",
    )

    assert context["description"] == "GAEALaViC test project"
    assert "resourceNaming" not in context
    assert context["commerceProfile"] == {"enabled": False}


def test_execute_agent_run_defines_target_url_before_queueing(monkeypatch) -> None:
    queued = {}

    def start(plan, config):
        queued["base_url"] = plan.base_url
        queued["authorized_origin"] = config.model_data_authorization["authorizedOrigin"]
        return {"run_id": "run-target-url-regression", "status": "queued"}

    monkeypatch.setattr(
        "gui_agent.api.server.RUN_ORCHESTRATOR",
        SimpleNamespace(start=start),
    )
    payload = AgentRunRequest.model_validate({
        "targetUrl": "http://192.168.31.218:7991/#/mineModelList",
        "scenario": {
            "name": "GAEALaViC modeling regression",
            "goal": "Inspect the modeling workflow",
        },
        "settings": {
            "protocol": "chat_completions",
            "baseUrl": "https://model.example.test/v1",
            "model": "test-model",
            "apiKey": "test-api-key",
        },
        "modelDataAuthorization": {
            "authorizedOrigin": "http://192.168.31.218:7991",
            "allowDom": True,
            "allowScreenshots": True,
        },
    })

    result = execute_agent_run(payload)

    assert result["run_id"] == "run-target-url-regression"
    assert queued == {
        "base_url": "http://192.168.31.218:7991/#/mineModelList",
        "authorized_origin": "http://192.168.31.218:7991",
    }


def test_quick_start_auto_selects_exact_saved_private_project(monkeypatch) -> None:
    allowed_project = SimpleNamespace(
        id="project-private-allowed",
        base_url="http://192.168.31.218:7991/#/login",
        allow_private_network=True,
    )
    stale_project = SimpleNamespace(
        id="project-stale-https",
        base_url="https://192.168.31.218:7991",
        allow_private_network=False,
    )
    monkeypatch.setattr(
        "gui_agent.api.server.PROJECT_STORE",
        SimpleNamespace(list=lambda: [stale_project, allowed_project]),
    )

    selected = _auto_select_project_for_target(
        "http://192.168.31.218:7991/#/mineModelList"
    )

    assert selected is allowed_project


def test_saved_private_project_bypasses_only_the_public_redirect_probe(monkeypatch) -> None:
    project = SimpleNamespace(
        id="project-private-allowed",
        base_url="http://192.168.31.218:7991/#/login",
        allowed_hosts=["192.168.31.218"],
        allow_private_network=True,
    )
    monkeypatch.setattr(
        "gui_agent.api.server.PROJECT_STORE",
        SimpleNamespace(list=lambda: [project]),
    )

    resolved = _resolve_saved_project_url(
        "http://192.168.31.218:7991/#/mineModelList"
    )

    assert resolved == {
        "url": "http://192.168.31.218:7991/#/mineModelList",
        "changed": False,
        "redirectChain": ["http://192.168.31.218:7991/#/mineModelList"],
        "authorizationProjectId": "project-private-allowed",
        "resolutionMode": "saved_project_exact_origin",
    }


def test_saved_private_resolution_fails_closed_without_private_authorization(monkeypatch) -> None:
    project = SimpleNamespace(
        id="project-private-denied",
        base_url="http://192.168.31.218:7991",
        allowed_hosts=["192.168.31.218"],
        allow_private_network=False,
    )
    monkeypatch.setattr(
        "gui_agent.api.server.PROJECT_STORE",
        SimpleNamespace(list=lambda: [project]),
    )

    assert _resolve_saved_project_url("http://192.168.31.218:7991") is None


def test_quick_start_ambiguous_saved_projects_fail_closed(monkeypatch) -> None:
    duplicate_a = SimpleNamespace(
        id="project-a",
        base_url="http://192.168.31.218:7991/#/login",
        allow_private_network=True,
    )
    duplicate_b = SimpleNamespace(
        id="project-b",
        base_url="http://192.168.31.218:7991/#/mineModelList",
        allow_private_network=True,
    )
    monkeypatch.setattr(
        "gui_agent.api.server.PROJECT_STORE",
        SimpleNamespace(list=lambda: [duplicate_a, duplicate_b]),
    )

    assert _auto_select_project_for_target("http://192.168.31.218:7991") is None


def test_quick_start_prefers_unique_richer_exact_origin_project(monkeypatch) -> None:
    empty_l0 = SimpleNamespace(
        id="project-empty-l0",
        base_url="http://192.168.31.218:7991",
        allow_private_network=True,
        onboarding_level="L0",
        business_context=SimpleNamespace(
            model_dump=lambda **_kwargs: {"description": "", "facts": []}
        ),
        async_state_machines=[],
        side_effect_policies=[],
        component_adapters=[],
    )
    rich_l2 = SimpleNamespace(
        id="project-rich-l2",
        base_url="http://192.168.31.218:7991/#/login",
        allow_private_network=True,
        onboarding_level="L2",
        business_context=SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "description": "GAEALaViC lifecycle contract",
                "facts": [{"id": "objects"}],
                "objectTypes": ["model", "scenario"],
            }
        ),
        async_state_machines=[object()],
        side_effect_policies=[object()],
        component_adapters=[],
    )
    monkeypatch.setattr(
        "gui_agent.api.server.PROJECT_STORE",
        SimpleNamespace(list=lambda: [empty_l0, rich_l2]),
    )

    selected = _auto_select_project_for_target(
        "http://192.168.31.218:7991/#/mineModelList"
    )

    assert selected is rich_l2


def test_explicit_stale_l0_project_is_promoted_to_unique_richer_origin(monkeypatch) -> None:
    empty_l0 = SimpleNamespace(
        id="project-empty-l0",
        base_url="http://192.168.31.218:7991",
        allow_private_network=True,
        onboarding_level="L0",
        business_context=SimpleNamespace(
            model_dump=lambda **_kwargs: {"description": "", "facts": []}
        ),
        async_state_machines=[],
        side_effect_policies=[],
        component_adapters=[],
    )
    rich_l2 = SimpleNamespace(
        id="project-rich-l2",
        base_url="http://192.168.31.218:7991/#/login",
        allow_private_network=True,
        onboarding_level="L2",
        business_context=SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "description": "GAEALaViC lifecycle contract",
                "facts": [{"id": "objects"}],
            }
        ),
        async_state_machines=[object()],
        side_effect_policies=[object()],
        component_adapters=[],
    )
    monkeypatch.setattr(
        "gui_agent.api.server.PROJECT_STORE",
        SimpleNamespace(list=lambda: [empty_l0, rich_l2]),
    )

    promoted = _promote_shallow_project_for_target(
        empty_l0, "http://192.168.31.218:7991/#/mineModelList"
    )

    assert promoted is rich_l2


def test_quick_start_run_uses_auto_selected_private_project(monkeypatch) -> None:
    captured = {}
    project = SimpleNamespace(
        id="project-private-allowed",
        base_url="http://192.168.31.218:7991/#/login",
        allowed_hosts=["192.168.31.218"],
        allow_private_network=True,
        onboarding_level="L2",
        limits=SimpleNamespace(timeout_seconds=600, max_model_calls=20, max_steps=50),
        forbidden_actions=[],
        business_context=SimpleNamespace(model_dump=lambda **_kwargs: {}),
        commerce_profile=SimpleNamespace(
            enabled=False,
            model_dump=lambda **_kwargs: {"enabled": False},
        ),
    )
    monkeypatch.setattr(
        "gui_agent.api.server.PROJECT_STORE",
        SimpleNamespace(list=lambda: [project], load_session=lambda _project_id: None),
    )
    monkeypatch.setattr("gui_agent.api.server._environment_for_run", lambda *_args: None)
    monkeypatch.setattr("gui_agent.api.server._universal_runner_options", lambda *_args: {})
    monkeypatch.setattr("gui_agent.api.server._commerce_runner_options", lambda *_args: {})
    monkeypatch.setattr("gui_agent.api.server._file_asset_runner_options", lambda *_args: ())
    monkeypatch.setattr("gui_agent.api.server._enforce_cesium_policy", lambda *_args: None)
    monkeypatch.setattr("gui_agent.api.server._cesium_runner_policy", lambda *_args: (False, ()))

    def start(_plan, config):
        captured["project_id"] = config.project_id
        captured["allowed_hosts"] = config.allowed_hosts
        captured["allow_private_network"] = config.allow_private_network
        return {"run_id": "run-private-quick-start", "status": "queued"}

    monkeypatch.setattr(
        "gui_agent.api.server.RUN_ORCHESTRATOR",
        SimpleNamespace(start=start),
    )
    payload = AgentRunRequest.model_validate({
        "targetUrl": "http://192.168.31.218:7991/#/mineModelList",
        "scenario": {"name": "modeling", "goal": "Inspect the modeling workflow"},
        "settings": {
            "protocol": "chat_completions",
            "baseUrl": "https://model.example.test/v1",
            "model": "test-model",
            "apiKey": "test-key",
        },
        "modelDataAuthorization": {
            "authorizedOrigin": "http://192.168.31.218:7991",
            "allowDom": True,
            "allowScreenshots": True,
        },
    })

    result = execute_agent_run(payload)

    assert result["run_id"] == "run-private-quick-start"
    assert captured == {
        "project_id": project.id,
        "allowed_hosts": ("192.168.31.218",),
        "allow_private_network": True,
    }


def test_agent_run_upgrades_explicit_stale_l0_to_l2_budget(monkeypatch) -> None:
    captured = {}

    def project(project_id: str, level: str, timeout: int, description: str):
        return SimpleNamespace(
            id=project_id,
            name=project_id,
            base_url="http://192.168.31.218:7991/#/login",
            allowed_hosts=["192.168.31.218"],
            allow_private_network=True,
            onboarding_level=level,
            limits=SimpleNamespace(
                timeout_seconds=timeout, max_model_calls=50, max_steps=100
            ),
            forbidden_actions=[],
            business_context=SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "description": description,
                    "facts": ([{"id": "workflow"}] if description else []),
                }
            ),
            commerce_profile=SimpleNamespace(
                enabled=False,
                model_dump=lambda **_kwargs: {"enabled": False},
            ),
            async_state_machines=([object()] if level == "L2" else []),
            side_effect_policies=([object()] if level == "L2" else []),
            component_adapters=[],
        )

    stale_l0 = project("project-stale-l0", "L0", 600, "")
    rich_l2 = project("project-rich-l2", "L2", 3600, "GAEALaViC workflow")
    store = SimpleNamespace(
        get=lambda project_id: stale_l0 if project_id == stale_l0.id else rich_l2,
        list=lambda: [stale_l0, rich_l2],
        load_session=lambda _project_id: None,
    )
    monkeypatch.setattr("gui_agent.api.server.PROJECT_STORE", store)
    monkeypatch.setattr("gui_agent.api.server._environment_for_run", lambda *_args: None)
    monkeypatch.setattr("gui_agent.api.server._universal_runner_options", lambda *_args: {})
    monkeypatch.setattr("gui_agent.api.server._commerce_runner_options", lambda *_args: {})
    monkeypatch.setattr("gui_agent.api.server._file_asset_runner_options", lambda *_args: ())
    monkeypatch.setattr("gui_agent.api.server._enforce_cesium_policy", lambda *_args: None)
    monkeypatch.setattr("gui_agent.api.server._cesium_runner_policy", lambda *_args: (False, ()))

    def start(_plan, config):
        captured["project_id"] = config.project_id
        captured["onboarding_level"] = config.onboarding_level
        captured["max_duration_seconds"] = config.max_duration_seconds
        return {"run_id": "run-promoted-l2", "status": "queued"}

    monkeypatch.setattr(
        "gui_agent.api.server.RUN_ORCHESTRATOR", SimpleNamespace(start=start)
    )
    payload = AgentRunRequest.model_validate({
        "targetUrl": "http://192.168.31.218:7991/#/mineModelList",
        "projectId": stale_l0.id,
        "scenario": {"name": "modeling", "goal": "Inspect test_H"},
        "settings": {
            "protocol": "chat_completions",
            "baseUrl": "https://model.example.test/v1",
            "model": "test-model",
            "apiKey": "test-key",
        },
        "modelDataAuthorization": {
            "authorizedOrigin": "http://192.168.31.218:7991",
            "allowDom": True,
            "allowScreenshots": True,
        },
    })

    result = execute_agent_run(payload)

    assert result["run_id"] == "run-promoted-l2"
    assert captured == {
        "project_id": rich_l2.id,
        "onboarding_level": "L2",
        "max_duration_seconds": 3600,
    }


def test_adaptive_policy_is_not_claimed_before_router_is_deployed() -> None:
    payload = AgentRunRequest.model_validate({
        "targetUrl": "https://example.test",
        "decisionPolicy": "adaptive_multimodal",
        "scenario": {"name": "inspect", "goal": "inspect current page"},
        "settings": {
            "protocol": "chat_completions",
            "baseUrl": "https://model.example.test/v1",
            "model": "test-model",
            "apiKey": "test-key",
        },
        "modelDataAuthorization": {
            "authorizedOrigin": "https://example.test",
            "allowDom": True,
            "allowScreenshots": True,
        },
    })

    with pytest.raises(HTTPException, match="自适应多模态决策路由尚未部署") as exc:
        execute_agent_run(payload)

    assert exc.value.status_code == 422


def test_agent_run_current_goal_overrides_selected_saved_scenario(monkeypatch) -> None:
    captured = {}
    project = SimpleNamespace(
        id="project-current-run-authority",
        base_url="https://example.test",
        allowed_hosts=[],
        allow_private_network=False,
        onboarding_level="L0",
        limits=SimpleNamespace(timeout_seconds=60, max_model_calls=10, max_steps=20),
        forbidden_actions=["create"],
        business_context=SimpleNamespace(model_dump=lambda **_kwargs: {}),
        commerce_profile=SimpleNamespace(
            enabled=False,
            model_dump=lambda **_kwargs: {"enabled": False},
        ),
    )
    saved = SimpleNamespace(
        id="scenario-old",
        name="old scenario",
        goal="inspect only and do not create",
        preconditions=[],
        test_data={"legacy": True},
        expected_results=[],
        forbidden_actions=["create"],
        commerce_steps=[],
        updated_at="old",
    )

    monkeypatch.setattr(
        "gui_agent.api.server.PROJECT_STORE",
        SimpleNamespace(
            get=lambda _project_id: project,
            load_session=lambda _project_id: None,
        ),
    )
    monkeypatch.setattr("gui_agent.api.server._scenario_for_run", lambda *_args: saved)
    monkeypatch.setattr("gui_agent.api.server._validate_scenario_commerce", lambda *_args: None)
    monkeypatch.setattr("gui_agent.api.server._environment_for_run", lambda *_args: None)
    monkeypatch.setattr("gui_agent.api.server._universal_runner_options", lambda *_args: {})
    monkeypatch.setattr("gui_agent.api.server._commerce_runner_options", lambda *_args: {})
    monkeypatch.setattr("gui_agent.api.server._file_asset_runner_options", lambda *_args: ())
    monkeypatch.setattr("gui_agent.api.server._enforce_cesium_policy", lambda *_args: None)
    monkeypatch.setattr("gui_agent.api.server._cesium_runner_policy", lambda *_args: (False, ()))
    monkeypatch.setattr(
        "gui_agent.api.server.DomainPolicy.check_url",
        lambda *_args, **_kwargs: None,
    )

    def start(_plan, config):
        captured["goal"] = config.agent_planner.scenario.goal
        captured["forbidden"] = config.forbidden_actions
        return {"run_id": "run-current-authority", "status": "queued"}

    monkeypatch.setattr(
        "gui_agent.api.server.RUN_ORCHESTRATOR",
        SimpleNamespace(start=start),
    )
    payload = AgentRunRequest.model_validate({
        "targetUrl": "https://example.test",
        "projectId": project.id,
        "scenarioId": saved.id,
        "scenario": {
            "name": "current run",
            "goal": "Please create a test model and verify it",
        },
        "settings": {
            "protocol": "chat_completions",
            "baseUrl": "https://model.example.test/v1",
            "model": "test-model",
            "apiKey": "test-key",
        },
        "modelDataAuthorization": {
            "siteHost": "example.test",
            "allowDom": True,
            "allowScreenshots": True,
        },
    })

    execute_agent_run(payload)

    assert captured["goal"] == "Please create a test model and verify it"
    assert "create" not in captured["forbidden"]
