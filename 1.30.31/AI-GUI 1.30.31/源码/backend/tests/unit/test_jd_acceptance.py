import json
from pathlib import Path

from fastapi.testclient import TestClient

from gui_agent.api import server
from gui_agent.commerce import AcceptanceBatchStore


def _catalog(tmp_path, *, blocked=True):
    root = tmp_path / "scenarios"
    root.mkdir()
    for index in range(1, 66):
        (root / f"J{index:02d}.json").write_text(json.dumps({
            "id": f"J{index:02d}", "priority": "P0" if index <= 40 else "P1",
            "title": f"Scenario {index}", "bindingStatus": "blocked" if blocked else "bound",
            "blockedDependencies": ["authorized environment"] if blocked else [],
            **({} if blocked else {"executablePlan": {"name": f"J{index:02d}"}}),
        }), encoding="utf-8")
    return root


def test_fixed_65_by_5_batch_stays_blocked_and_unverified(tmp_path) -> None:
    store = AcceptanceBatchStore(tmp_path / "batches", _catalog(tmp_path))
    batch = store.start()

    assert batch["scenarioCount"] == 65
    assert batch["plannedAttempts"] == 325
    assert len(batch["attempts"]) == 325
    assert batch["summary"]["counts"]["blocked"] == 325
    assert batch["summary"]["verifiedAttempts"] == 0
    assert batch["verificationStatus"] == "unverified"
    assert batch["summary"]["passed"] is False
    assert batch["summary"]["thresholds"]["amountAccuracy"]["actual"] is False
    assert batch["summary"]["thresholds"]["cleanupCompleteness"]["actual"] is False


def test_real_jd_catalog_produces_325_blocked_attempts(tmp_path) -> None:
    catalog = Path(__file__).resolve().parents[2] / "benchmarks" / "jd" / "scenarios"
    batch = AcceptanceBatchStore(tmp_path / "batches", catalog).start()
    assert batch["plannedAttempts"] == 325
    assert batch["summary"]["counts"]["blocked"] == 325
    assert {item["scenarioId"] for item in batch["attempts"]} == {f"J{index:02d}" for index in range(1, 66)}


def test_batch_cancel_resume_retry_and_attempt_evidence(tmp_path) -> None:
    store = AcceptanceBatchStore(tmp_path / "batches", _catalog(tmp_path, blocked=False))
    batch = store.start()
    cancelled = store.cancel(batch["id"])
    assert cancelled["summary"]["counts"]["cancelled"] == 325
    resumed = store.resume(batch["id"])
    assert resumed["summary"]["counts"]["queued"] == 325
    recorded = store.record_attempt(batch["id"], "J01#1", {
        "status": "failed", "runId": "run-1", "evidenceCompleteness": 0.9,
        "stableReplay": False, "amountAccurate": True, "cleanupComplete": True,
        "zeroToleranceIncidents": {"duplicateOrder": 1},
    })
    assert recorded["summary"]["counts"]["failed"] == 1
    assert recorded["summary"]["thresholds"]["zeroToleranceIncidents"]["actual"] == 1
    retried = store.retry_failed(batch["id"])
    assert retried["summary"]["counts"]["failed"] == 0
    assert next(item for item in retried["attempts"] if item["id"] == "J01#1")["runId"] is None


def test_bound_catalog_without_executable_plan_remains_blocked(tmp_path) -> None:
    catalog = _catalog(tmp_path, blocked=False)
    for path in catalog.glob("J*.json"):
        payload = json.loads(path.read_text(encoding="utf-8")); payload.pop("executablePlan")
        path.write_text(json.dumps(payload), encoding="utf-8")
    store = AcceptanceBatchStore(tmp_path / "batches", catalog)
    batch = store.start()
    assert batch["summary"]["counts"]["blocked"] == 325
    assert "场景缺少可执行计划绑定" in batch["attempts"][0]["blockedDependencies"]


def test_acceptance_api_and_report_use_persistent_store(tmp_path, monkeypatch) -> None:
    store = AcceptanceBatchStore(tmp_path / "batches", _catalog(tmp_path))
    monkeypatch.setattr(server, "JD_ACCEPTANCE_STORE", store)
    client = TestClient(server.app)

    created = client.post("/api/acceptance/jd/batches")
    assert created.status_code == 200
    batch = created.json()
    assert client.get("/api/acceptance/jd/batches").json()[0]["id"] == batch["id"]
    report = client.get(f"/api/acceptance/jd/batches/{batch['id']}/report.html")
    assert report.status_code == 200
    assert "325" in report.text and "unverified" in report.text
    assert "Acceptance thresholds" in report.text
    assert all(name in report.text for name in (
        "p0Completion", "allScenarioPassRate", "stableReplayRate",
        "evidenceCompleteness", "amountAccuracy", "cleanupCompleteness",
        "zeroToleranceIncidents",
    ))
    assert "Attempt evidence" in report.text and "runId" in report.text
