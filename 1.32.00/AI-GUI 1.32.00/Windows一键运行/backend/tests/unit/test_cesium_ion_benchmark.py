from pathlib import Path

import pytest

from gui_agent.benchmarks.cesium_ion import acceptance_payload, scenario_catalog, site_map_payload
from gui_agent.benchmarks.cesium_ion.ledger import LedgerError, ResourceLedger
from gui_agent.benchmarks.cesium_ion.policy import CesiumPolicyError, policy_payload, validate_cesium_plan
from gui_agent.domain.models import ActionType, EffectLevel, Locator, Step, TestPlan as DomainTestPlan
from gui_agent.execution.confirmation import confirmation_match


def test_catalog_contains_c01_through_c60_without_false_passes() -> None:
    cases = scenario_catalog()

    assert [item["id"] for item in cases] == [f"C{index:02d}" for index in range(1, 61)]
    assert all(item["execution"]["repetitionsCompleted"] == 0 for item in cases)
    assert not any(item["execution"]["status"] == "passed" for item in cases)
    summary = acceptance_payload()["summary"]
    assert summary["passed"] == 0
    assert summary["byStatus"] == {"blocked": 41, "observed_read_only": 14, "unverified": 5}


def test_site_map_and_policy_preserve_observed_safety_boundaries() -> None:
    site_map = site_map_payload()
    policy = policy_payload()

    assert len(site_map["pages"]) == 14
    assert site_map["safety"]["existingAssetsAreE2EOwned"] is False
    assert site_map["safety"]["defaultTokenMutable"] is False
    assert policy["sideEffects"]["billing_change"]["level"] == "forbidden"
    assert policy["sideEffects"]["regenerate_default_token"]["confirmation"] is True


def test_resource_ledger_requires_e2e_ownership_and_proves_cleanup(tmp_path: Path) -> None:
    ledger = ResourceLedger(tmp_path / "resource-ledger.json")
    payload = {
        "runId": "run-123",
        "caseId": "C10",
        "resourceType": "asset",
        "resourceId": "123456",
        "name": "E2E-20260722-run-123-C10",
    }

    with pytest.raises(LedgerError, match="E2E-"):
        ledger.register({**payload, "name": "existing-user-asset"})

    entry = ledger.register(payload)
    assert ledger.summary() == {"total": 1, "pendingCleanup": 1, "zeroResidualProven": False}
    cleaned = ledger.record_cleanup(entry["ledgerId"], "completed", ["GET /v1/assets/123456 returned 404"])

    assert cleaned["cleanupStatus"] == "completed"
    assert ledger.summary() == {"total": 1, "pendingCleanup": 0, "zeroResidualProven": True}


def test_cesium_policy_requires_structured_effects_and_ledger_owned_deletes(tmp_path: Path) -> None:
    unclassified = DomainTestPlan(
        name="C05", base_url="https://ion.cesium.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/assets")], assertions=[],
    )
    with pytest.raises(CesiumPolicyError, match="effect_kind/effect_level"):
        validate_cesium_plan(unclassified, unclassified.base_url, [])

    read_only = unclassified.model_copy(update={
        "steps": [Step(
            action=ActionType.NAVIGATE, target="/assets",
            effect_kind="browse_search_filter_sort", effect_level=EffectLevel.READ_ONLY,
        )]
    })
    validate_cesium_plan(read_only, read_only.base_url, [])

    forbidden = read_only.model_copy(update={
        "steps": [Step(
            action=ActionType.CLICK, locator=Locator(role="button", name="Upgrade"),
            effect_kind="billing_change", effect_level=EffectLevel.FORBIDDEN,
        )]
    })
    with pytest.raises(CesiumPolicyError, match="禁止操作"):
        validate_cesium_plan(forbidden, forbidden.base_url, [])

    ledger = ResourceLedger(tmp_path / "ledger.json")
    entry = ledger.register({
        "runId": "run-policy", "caseId": "C33", "resourceType": "asset",
        "resourceId": "asset-123", "name": "E2E-20260722-run-policy-C33",
    })
    destructive = read_only.model_copy(update={
        "steps": [Step(
            action=ActionType.CLICK, locator=Locator(role="button", name="Delete"),
            effect_kind="delete_resource", effect_level=EffectLevel.HIGH_RISK_WRITE,
            target_id="asset-123", resource_name=entry["name"], cleanup_action="verify API/UI absence",
        )]
    })
    with pytest.raises(CesiumPolicyError, match="不属于待清理"):
        validate_cesium_plan(destructive, destructive.base_url, [])
    validate_cesium_plan(destructive, destructive.base_url, ledger.list())
    assert confirmation_match(destructive.steps[0]) == "cesium:delete_resource"

