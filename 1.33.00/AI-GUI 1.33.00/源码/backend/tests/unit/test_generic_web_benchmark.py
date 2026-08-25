from gui_agent.benchmarks.generic_web import validation_payload


def test_generic_web_baseline_has_two_non_engine_specific_sites() -> None:
    payload = validation_payload()

    assert payload["suite"] == "generic-web"
    assert payload["summary"] == {
        "siteCount": 2,
        "taskCount": 4,
        "verified": 0,
        "unverified": 4,
    }
    assert {site["path"] for site in payload["sites"]} == {"/", "/shadow.html"}
    site_serialized = str(payload["sites"])
    assert "Cesium" not in site_serialized
    assert "GAEALaViC" not in site_serialized
    assert all(task["status"] == "unverified" for site in payload["sites"] for task in site["tasks"])
