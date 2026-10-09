from gui_agent.domain.models import ActionType, Step
from gui_agent.domain.results import ModelCallRecord, Observation, PageSemanticSummary
from gui_agent.execution.verification import (
    build_action_contract,
    target_service_not_implemented_evidence,
    target_service_not_implemented_from_verification,
    verify_action_result,
)
from gui_agent.execution.agent_runner import (
    _auto_stable_replay_safe,
    _external_model_call_count,
    _stable_replay_artifacts_root,
)
from gui_agent.site_capabilities import GenericWebCapabilityPack


def _observation(url: str, signature: str, accessibility: str = "") -> Observation:
    return Observation(
        url=url,
        title="Page",
        accessibility_summary=accessibility,
        semantic_summary=PageSemanticSummary(
            page_key=f"{url}|Page",
            route=url,
            signature=signature,
        ),
    )


def test_navigation_contract_requires_target_route() -> None:
    before = _observation("https://app.example.test/", "before")
    after = _observation("https://app.example.test/settings", "after")
    step = Step(action="navigate", target="/settings")
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=1)

    verified = verify_action_result(contract, step, before, after)

    assert verified.status == "passed"
    assert "route=/settings" in verified.facts


def test_click_without_independent_change_is_inconclusive() -> None:
    before = _observation("https://app.example.test/items", "same")
    after = _observation("https://app.example.test/items", "same")
    step = Step(action="click", locator={"role": "button", "name": "Next"})
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=2)

    verified = verify_action_result(contract, step, before, after)

    assert verified.status == "inconclusive"
    assert not verified.proved_progress


def test_write_requires_independent_business_evidence() -> None:
    before = _observation("https://app.example.test/items", "before")
    after = _observation("https://app.example.test/items/42", "after")
    step = Step(
        action="click",
        locator={"role": "button", "name": "Create"},
        effect_level="reversible_write",
    )
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=3)

    unproved = verify_action_result(contract, step, before, after)
    proved = verify_action_result(
        contract,
        step,
        before,
        after,
        business_evidence={"businessObjectId": "42"},
    )

    assert unproved.status == "inconclusive"
    assert proved.status == "passed"


def test_save_with_visible_validation_error_fails_instead_of_using_dom_change() -> None:
    before = _observation("https://app.example.test/editor", "before")
    after = Observation(
        url="https://app.example.test/editor",
        title="Page",
        semantic_summary=PageSemanticSummary(
            page_key="/editor|Page",
            route="/editor",
            signature="after-validation",
            controls=[{
                "runtimeId": "ai_42",
                "role": "textbox",
                "name": "任务路径点关键字",
                "required": True,
                "invalid": True,
                "validationMessage": "此项为必填项",
            }],
        ),
    )
    step = Step(
        action="click",
        locator={"role": "button", "name": "保存"},
        description="保存任务路径设置",
    )
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=4)

    verified = verify_action_result(contract, step, before, after)

    assert contract.business_verification_required is True
    assert verified.status == "failed"
    assert any("任务路径点关键字" in fact for fact in verified.facts)


def test_save_without_business_proof_remains_inconclusive() -> None:
    before = _observation("https://app.example.test/editor", "before")
    after = _observation("https://app.example.test/editor", "before")
    step = Step(
        action="click",
        locator={"role": "button", "name": "保存"},
        description="保存动力学设置",
    )
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=5)

    verified = verify_action_result(contract, step, before, after)

    assert contract.business_verification_required is True
    assert verified.status == "inconclusive"


def test_failed_business_request_cannot_be_masked_by_a_visual_change() -> None:
    before = _observation("http://192.168.31.218:7991/#/situationPage?type=run", "before")
    after = _observation("http://192.168.31.218:7991/#/situationPage?type=run", "after")
    after = after.model_copy(update={
        "failed_requests": [
            "HTTP 500 POST http://192.168.31.218:7980/api/v1/lavic-core/startSimulation"
        ]
    })
    step = Step(
        action="click",
        locator={"role": "button", "name": "启动"},
        description="点击启动仿真",
        effect_level="reversible_write",
    )
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=9)

    verified = verify_action_result(
        contract,
        step,
        before,
        after,
        visual_changed=True,
    )

    assert verified.status == "failed"
    assert any("HTTP 500" in fact for fact in verified.facts)


def test_start_simulation_501_is_explicit_and_non_replayable() -> None:
    before = _observation(
        "http://192.168.31.218:7991/#/situationPage?type=run&simulationStatus=Unstart",
        "before",
    )
    after = _observation(
        "http://192.168.31.218:7991/#/situationPage?type=run&simulationStatus=Unstart",
        "after",
    ).model_copy(update={
        "failed_requests": [
            "HTTP 501 POST http://192.168.31.218:7980/api/v1/lavic-core/startSimulation [urlSha256=abc]",
        ],
    })
    step = Step(
        action=ActionType.VISUAL_CLICK,
        execution_mode="visual",
        stability_level="C",
        visual_target="启动",
        relative_position={"xRatio": 0.5, "yRatio": 0.1},
        description="点击启动仿真",
    )
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=11)

    verified = verify_action_result(contract, step, before, after, visual_changed=True)

    assert verified.status == "failed"
    assert "target_service_not_implemented" in verified.facts
    assert "click_dispatched" in verified.facts
    assert "target_simulation_status=Unstart" in verified.facts
    assert "automatic_replay_allowed=false" in verified.facts
    assert "HTTP 501" in verified.reason
    classified = target_service_not_implemented_from_verification(verified.model_dump(mode="json"))
    assert classified is not None
    assert classified["clickDispatched"] is True
    assert classified["targetSimulationStatus"] == "Unstart"
    assert classified["automaticReplayAllowed"] is False


def test_start_simulation_501_classifier_does_not_treat_reload_as_click() -> None:
    observation = _observation(
        "http://192.168.31.218:7991/#/situationPage?type=run&simulationStatus=Unstart",
        "same",
    ).model_copy(update={
        "failed_requests": [
            "HTTP 501 POST http://192.168.31.218:7980/api/v1/lavic-core/startSimulation",
        ],
    })

    assert target_service_not_implemented_evidence(
        Step(action=ActionType.RELOAD), observation
    ) is None


def test_missing_font_404_does_not_invalidate_business_action() -> None:
    before = _observation("https://app.example.test/editor", "before")
    after = _observation("https://app.example.test/editor", "before")
    after = after.model_copy(update={
        "failed_requests": [
            "HTTP 404 GET https://app.example.test/assets/font-files/Inter-Regular.woff2 [urlSha256=abc]"
        ]
    })
    step = Step(action="click", locator={"role": "button", "name": "打开"})
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=10)

    verified = verify_action_result(contract, step, before, after)

    assert verified.status == "inconclusive"


def test_fill_uses_independent_control_state_evidence() -> None:
    before = _observation("https://app.example.test/items", "same")
    after = _observation("https://app.example.test/items", "same")
    step = Step(action="fill", locator={"role": "searchbox", "name": "Search"}, value="Google")
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=6)

    verified = verify_action_result(
        contract,
        step,
        before,
        after,
        execution_detail={"controlState": {"kind": "value", "verified": True}},
    )

    assert verified.status == "passed"
    assert "control_state_verified:value" in verified.facts


def test_control_state_mismatch_fails_the_action_contract() -> None:
    before = _observation("https://app.example.test/items", "same")
    after = _observation("https://app.example.test/items", "same")
    step = Step(action="clear", locator={"role": "searchbox", "name": "Search"})
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=7)

    verified = verify_action_result(
        contract,
        step,
        before,
        after,
        execution_detail={"controlState": {"kind": "value", "verified": False}},
    )

    assert verified.status == "failed"


def test_press_accepts_observed_accessibility_state_change() -> None:
    before = _observation(
        "https://app.example.test/items?search=Google",
        "same",
        'searchbox "Search": Google',
    )
    after = _observation(
        "https://app.example.test/items?search=Google",
        "same",
        'searchbox "Search": Google\n6 assets total',
    )
    step = Step(action="press", locator={"role": "searchbox", "name": "Search"}, value="Enter")
    contract = build_action_contract(step, before, GenericWebCapabilityPack(), index=8)

    verified = verify_action_result(contract, step, before, after)

    assert verified.status == "passed"
    assert "accessibility_state_changed" in verified.facts


def test_automatic_stable_replay_accepts_only_explicit_locator_reads() -> None:
    read_steps = [
        Step(action="navigate", target="/", effect_level="read_only"),
        Step(
            action="click",
            locator={"role": "link", "name": "Usage"},
            effect_level="read_only",
        ),
    ]
    write_step = Step(
        action="fill",
        locator={"role": "textbox", "name": "Name"},
        value="E2E_value",
        effect_level="session_only",
    )
    search_steps = [
        Step(
            action="fill",
            locator={"role": "searchbox", "name": "Search"},
            value="Google",
            state_machine_id="cesium.assets.search.fill",
            effect_kind="browse_search_filter_sort",
            effect_level="read_only",
        ),
        Step(
            action="press",
            locator={"role": "searchbox", "name": "Search"},
            value="Enter",
            state_machine_id="cesium.assets.search.apply",
            effect_kind="browse_search_filter_sort",
            effect_level="read_only",
        ),
        Step(
            action="clear",
            locator={"role": "searchbox", "name": "Search"},
            state_machine_id="cesium.assets.search.clear",
            effect_kind="browse_search_filter_sort",
            effect_level="read_only",
        ),
    ]

    assert _auto_stable_replay_safe(read_steps)
    assert _auto_stable_replay_safe([*read_steps, *search_steps])
    assert not _auto_stable_replay_safe([write_step])


def test_local_capability_decisions_do_not_consume_model_call_quota() -> None:
    records = [
        ModelCallRecord(
            index=1,
            model="site-capability-controller",
            protocol="local",
            elapsed_ms=0,
            decision="action",
            reason="known stage",
        ),
        ModelCallRecord(
            index=2,
            model="gpt-test",
            protocol="responses",
            elapsed_ms=10,
            decision="action",
            reason="unknown page",
        ),
    ]

    assert _external_model_call_count(records) == 1


def test_stable_replay_artifacts_stay_inside_current_run_mount(tmp_path) -> None:
    run_dir = tmp_path / "run-123"

    replay_root = _stable_replay_artifacts_root(run_dir)

    assert replay_root == run_dir / "stable-replay"
    assert replay_root.is_relative_to(run_dir)
