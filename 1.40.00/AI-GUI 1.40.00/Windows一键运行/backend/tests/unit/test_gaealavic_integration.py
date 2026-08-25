from pathlib import Path
from time import sleep

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Error as PlaywrightError

from gui_agent.api import server
from gui_agent.acceptance import AcceptanceBatchManager
from gui_agent.execution.bridge_adapter import GAEALaViCCesiumBridgeAdapter


client = TestClient(server.app)


def test_gae_catalog_keeps_all_30_blocked_scenarios_in_150_run_denominator() -> None:
    response = client.get("/api/acceptance/gaealavic/scenarios")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenarioCount"] == 30
    assert payload["repeatCount"] == 5
    assert payload["plannedRuns"] == 150
    assert payload["readyCount"] == 0
    assert payload["blockedCount"] == 30
    assert [item["id"] for item in payload["scenarios"]] == [f"S{index:02d}" for index in range(1, 31)]
    assert all(item["executablePlan"] is None for item in payload["scenarios"])


def test_gae_l4_contract_remains_blocked_and_has_full_cleanup_tail() -> None:
    response = client.get("/api/acceptance/gaealavic/l4-workflow")

    assert response.status_code == 200
    payload = response.json()
    assert payload["bindingStatus"] == "blocked"
    assert payload["stages"][0]["id"] == "login"
    assert [item["id"] for item in payload["stages"]][-3:] == ["download", "cleanup", "evidence"]
    assert payload["blockedDependencies"]


def test_gae_dry_run_schedules_all_150_attempts_without_claiming_real_execution(tmp_path: Path, monkeypatch) -> None:
    manager = AcceptanceBatchManager(tmp_path / "batches")
    monkeypatch.setattr(server, "GAE_ACCEPTANCE_BATCHES", manager)

    response = client.post("/api/acceptance/gaealavic/batches", json={"dryRun": True})

    assert response.status_code == 200
    batch_id = response.json()["batchId"]
    for _ in range(200):
        payload = client.get(f"/api/acceptance/gaealavic/batches/{batch_id}").json()
        if payload["status"] == "completed":
            break
        sleep(0.01)
    assert payload["plannedRuns"] == 150
    assert payload["completedRuns"] == 150
    assert payload["dryRun"] is True
    assert {item["status"] for item in payload["attempts"]} == {"dry_run_ready"}
    summary = client.get(f"/api/acceptance/gaealavic/batches/{batch_id}/summary.json").json()
    assert summary["verificationStatus"] == "dry_run_only"
    assert summary["releaseStatus"] == "unverified"


def test_gae_reference_adapter_is_downloadable() -> None:
    response = client.get("/api/bridge/gaealavic-cesium-adapter")

    assert response.status_code == 200
    assert "__WEB_AI_TEST__" in response.text


def test_gae_bridge_requires_business_semantics() -> None:
    with pytest.raises(PlaywrightError, match="缺少语义状态"):
        GAEALaViCCesiumBridgeAdapter._validate_scene({"camera": {}, "layers": [], "tilesLoaded": True})

    GAEALaViCCesiumBridgeAdapter._validate_scene({
        "camera": {}, "layers": [], "entityCount": 0, "selectedEntityId": None,
        "pathPoints": [], "pois": [], "fences": [], "drawings": [],
        "tilesLoaded": True, "webglError": None,
    })


def test_gae_assets_are_packaged_with_source() -> None:
    root = Path(server.__file__).resolve().parents[3] / "benchmarks" / "gaealavic"
    assert len(list((root / "scenarios").glob("S*.json"))) == 30
    assert (root / "l4-workflow.json").is_file()
