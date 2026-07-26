import pytest

from gui_agent.demo.server import DemoServer, find_available_port
from gui_agent.onboarding.models import ProjectConfig
from gui_agent.onboarding.scanner import scan_project


@pytest.mark.e2e
def test_read_only_scan_profiles_login_and_spa_without_submitting() -> None:
    with DemoServer(port=find_available_port()) as demo:
        project = ProjectConfig(
            id="project-scan", name="历史 CRM", baseUrl=demo.url,
            allowedHosts=["127.0.0.1"], forbiddenActions=["删除数据"],
            allowPrivateNetwork=True, onboardingLevel="L0",
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


@pytest.mark.e2e
def test_scan_recurses_open_shadow_roots_tracks_slots_and_excludes_hidden_templates() -> None:
    with DemoServer(port=find_available_port()) as demo:
        project = ProjectConfig(
            id="project-shadow-scan", name="Shadow fixture", baseUrl=f"{demo.url}/shadow.html",
            allowedHosts=["127.0.0.1"], allowPrivateNetwork=True, onboardingLevel="L0",
        )
        report = scan_project(project, headless=True, timeout_ms=10_000)

    page = report.scanned_pages[0]
    controls = page["controls"]
    create_story = next(item for item in controls if item["name"] == "Create story")
    slotted = next(item for item in controls if item["name"] == "插槽操作")
    hidden = next(item for item in controls if item["name"] == "隐藏操作")

    assert report.page_summary["shadowRoots"] == 2
    assert report.page_summary["shadowControls"] >= 4
    assert report.page_summary["slots"] == 2
    assert report.page_summary["hiddenControls"] == 1
    assert create_story["visible"] is True
    assert create_story["shadowHosts"] == ["#ion-shell", '[data-test="panel"]']
    assert create_story["locator"]["testId"] == "create-story"
    assert slotted["slot"] == "actions"
    assert slotted["shadowHosts"] == ["#ion-shell"]
    assert hidden["visible"] is False
    assert all(item["name"] != "模板按钮" for item in controls)
    assert page["sideEffects"] == [{
        "control": "Create story", "role": "button",
        "shadowHosts": ["#ion-shell", '[data-test="panel"]'],
        "disposition": "not-triggered-by-read-only-scan",
    }]
    assert "super-sensitive-value" not in report.model_dump_json()
