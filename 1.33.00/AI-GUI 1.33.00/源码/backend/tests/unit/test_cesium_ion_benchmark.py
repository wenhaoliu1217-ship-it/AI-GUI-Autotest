from pathlib import Path
import hashlib
import json

import pytest

from gui_agent.benchmarks.cesium_ion import acceptance_payload, scenario_catalog, site_map_payload
from gui_agent.benchmarks.cesium_ion.ledger import LedgerError, ResourceLedger
from gui_agent.benchmarks.cesium_ion.policy import CesiumPolicyError, cesium_confirmation_rule, policy_payload, validate_cesium_plan
from gui_agent.benchmarks.cesium_ion.site_acceptance import evaluate_site_observation
from gui_agent.benchmarks.cesium_ion.test_data import REQUIRED_DATA, CesiumTestDataManifestError, readiness_payload, validate_manifest_payload
from gui_agent.domain.models import ActionType, EffectLevel, Locator, Step, TestPlan as DomainTestPlan
from gui_agent.domain.results import Observation, PageHealth
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
    assert confirmation_match(
        destructive.steps[0], specialized_rules=(cesium_confirmation_rule,)
    ) == "cesium:delete_resource"


def _write_valid_cesium_manifest(root: Path) -> dict:
    artifacts = []
    for artifact_id, filename, purpose in REQUIRED_DATA:
        content = f"fixture-{artifact_id}".encode("utf-8")
        (root / filename).write_bytes(content)
        item = {
            "id": artifact_id,
            "file": filename,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        if artifact_id.startswith("D"):
            item["spatialMetadata"] = {"coordinateSystem": "EPSG:4326", "bbox": [0, 0, 1, 1]}
        artifacts.append(item)
    manifest = {"version": "1.33.00", "target": "https://ion.cesium.com", "artifacts": artifacts}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_cesium_test_data_manifest_is_measured_and_never_accepts_path_traversal(tmp_path: Path) -> None:
    manifest = _write_valid_cesium_manifest(tmp_path)
    normalized = validate_manifest_payload(manifest)
    assert len(normalized["artifacts"]) == len(REQUIRED_DATA)
    ready = readiness_payload(tmp_path)
    assert ready["manifestStatus"] == "ready"
    assert ready["summary"] == {"required": 22, "ready": 22, "missing": 0, "mismatch": 0}

    unsafe = json.loads(json.dumps(manifest))
    unsafe["artifacts"][0]["file"] = "../outside.glb"
    with pytest.raises(CesiumTestDataManifestError, match="safe package filename"):
        validate_manifest_payload(unsafe)

    (tmp_path / "cesium-e2e-model.glb").write_bytes(b"changed")
    mismatch = readiness_payload(tmp_path)
    assert mismatch["manifestStatus"] == "blocked"
    assert mismatch["summary"]["mismatch"] == 1


def test_cesium_site_acceptance_requires_visible_session_and_map_facts() -> None:
    login_wall = evaluate_site_observation(
        Observation(
            url="https://ion.cesium.com/assets",
            title="Cesium ion",
            dom_summary=["input | type=password"],
            accessibility_summary='button "Sign in"',
            page_health=PageHealth(ready_state="complete", visible_text_length=80, interactive_count=2),
        )
    )
    assert login_wall["status"] == "blocked"
    assert login_wall["session"]["verified"] is False
    assert any("登录墙" in item for item in login_wall["blockers"])

    authenticated_map = evaluate_site_observation(
        Observation(
            url="https://ion.cesium.com/stories/editor/?id=e2e-story",
            title="Cesium ion Story",
            dom_summary=[
                "shadow=ion-app > ion-map-viewer | div | title=Area",
                "shadow=ion-app > ion-map-viewer | canvas | webgl",
            ],
            accessibility_summary='button "Sign out"\nbutton "Area"',
            page_health=PageHealth(ready_state="complete", visible_text_length=240, interactive_count=8, visual_surface_count=1),
        ),
        require_map_surface=True,
        required_map_controls=("area",),
    )
    assert authenticated_map["status"] == "verified"
    assert authenticated_map["identity"]["verified"] is True
    assert authenticated_map["session"]["verified"] is True
    assert authenticated_map["map"]["verified"] is True
