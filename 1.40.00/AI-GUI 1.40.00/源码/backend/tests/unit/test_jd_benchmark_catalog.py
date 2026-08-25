import json
from pathlib import Path


def test_jd_catalog_contains_65_blocked_scenarios_and_325_attempts():
    root = Path(__file__).resolve().parents[2] / "benchmarks" / "jd"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    files = sorted((root / "scenarios").glob("J*.json"))
    scenarios = [json.loads(path.read_text(encoding="utf-8")) for path in files]

    assert manifest["scenarioCount"] == 65
    assert manifest["repeatCount"] == 5
    assert manifest["plannedAttempts"] == 325
    assert [item["id"] for item in scenarios] == [f"J{index:02d}" for index in range(1, 66)]
    assert all(item["bindingStatus"] == "blocked" for item in scenarios)
    assert all(item["verificationStatus"] == "unverified" for item in scenarios)


def test_formal_site_never_allows_write_baseline():
    root = Path(__file__).resolve().parents[2] / "benchmarks" / "jd" / "scenarios"
    scenarios = [json.loads(path.read_text(encoding="utf-8")) for path in root.glob("J*.json")]
    writes = [
        item
        for item in scenarios
        if item["riskLevel"] != "read" and item["id"] != "J13"
    ]

    assert writes
    assert all(item["formalSitePolicy"] == "forbidden" for item in writes)
    assert all(item["allowedEnvironments"] == ["isolated_transaction"] for item in writes)
