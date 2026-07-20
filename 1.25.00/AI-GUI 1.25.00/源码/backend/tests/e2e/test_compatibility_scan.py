import pytest

from gui_agent.demo.server import DemoServer, find_available_port
from gui_agent.onboarding.models import ProjectConfig
from gui_agent.onboarding.scanner import scan_project


@pytest.mark.e2e
def test_read_only_scan_profiles_login_and_spa_without_submitting() -> None:
    with DemoServer(port=find_available_port()) as demo:
        project = ProjectConfig(
            id="project-scan", name="历史 CRM", baseUrl=demo.url,
            allowedHosts=["127.0.0.1"], forbiddenActions=["删除数据"], onboardingLevel="L0",
        )
        report = scan_project(project, headless=True, timeout_ms=10_000)

    assert report.scanned_pages
    assert report.scanned_pages[0]["pageType"] == "登录页"
    assert report.recommended_onboarding_level == "L1"
    assert any("登录" in item for item in report.authentication_signals)
    assert report.candidate_locators["labels"] >= 2
    assert report.stable_areas
    assert report.suggested_scenarios
    assert not any(token in " ".join(report.failed_requests).lower() for token in ("delete", "publish", "checkout"))
