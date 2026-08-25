import pytest
from playwright.sync_api import sync_playwright

from gui_agent.benchmarks.generic_web import run_regression, validation_payload
from gui_agent.demo.server import DemoServer, find_available_port


@pytest.mark.e2e
def test_generic_web_baseline_pages_are_real_observable_surfaces() -> None:
    payload = validation_payload()
    with DemoServer(port=find_available_port()) as demo:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            for site in payload["sites"]:
                page.goto(f"{demo.url}{site['path']}", wait_until="domcontentloaded")
                assert page.locator("body").inner_text().strip()
                if site["id"] == "generic-crm":
                    assert page.get_by_label("用户名").count() == 1
                    assert page.get_by_role("button", name="登录").count() == 1
                else:
                    assert page.get_by_label("搜索资产").count() == 1
                    assert page.get_by_role("button", name="Create story").count() == 1
            browser.close()


@pytest.mark.e2e
def test_generic_web_regression_executes_all_tasks_and_persists_evidence(tmp_path) -> None:
    payload = run_regression(tmp_path / "artifacts")

    assert payload["summary"] == {
        "siteCount": 2,
        "taskCount": 4,
        "verified": 4,
        "unverified": 0,
    }
    assert payload["lastRun"]["status"] == "passed"
    assert {item["taskId"] for item in payload["evidence"]} == {"G01", "G02", "G03", "G04"}
    assert all(item["status"] == "passed" for item in payload["evidence"])
    evidence_path = tmp_path / "artifacts" / payload["lastRun"]["evidencePath"]
    assert evidence_path.exists()
    latest = tmp_path / "artifacts" / "generic-web" / "latest.json"
    assert latest.exists()
    assert validation_payload(tmp_path / "artifacts")["summary"]["verified"] == 4
