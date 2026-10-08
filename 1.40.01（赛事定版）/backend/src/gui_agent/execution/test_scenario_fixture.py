from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

from gui_agent.artifacts.manager import ArtifactManager
from gui_agent.domain.results import Status, StepResult
from gui_agent.execution.observation import ObservationCollector
from gui_agent.security.redaction import Redactor
from gui_agent.site_capabilities.gaealavic import GAEALaViCCapabilityPack


FIXTURE = Path(__file__).parents[1] / "demo" / "site" / "scenario.html"


def test_scenario_fixture_closes_portal_canvas_path_and_list_proof(tmp_path) -> None:
    artifacts = ArtifactManager(tmp_path, "scenario-fixture", Redactor())
    pack = GAEALaViCCapabilityPack()
    scenario = type(
        "Scenario",
        (),
        {
            "name": "scenario fixture",
            "goal": "实际创建完整想定并配置路径",
            "test_data": {"scenarioName": "scenario_test_A"},
            "business_context": {},
        },
    )()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 800})
        page.goto(FIXTURE.as_uri())
        collector = ObservationCollector(page, artifacts, Redactor())

        page.get_by_role("button", name="Create scenario").click()
        page.get_by_label("Scenario name").fill("scenario_test_A")
        page.get_by_role("combobox", name="Select model").click()
        opened = collector.capture(_shot(page, artifacts, "portal-open"))
        assert opened.semantic_summary is not None
        assert any(
            option["text"] == "Existing model Alpha"
            for component in opened.semantic_summary.components
            for option in component.get("visibleOptions", [])
        )
        page.get_by_role("option", name="Existing model Alpha").click()
        page.get_by_role("button", name="Configure instance").click()
        page.get_by_label("Instance name").fill("scenario_instance_A")
        page.get_by_role("button", name="Open 3D editor").click()

        before = collector.capture(_shot(page, artifacts, "canvas-before"))
        assert pack.page_stage(before) == "scenario_path_configuration"
        before_signature = before.semantic_summary.canvas["signature"]
        page.get_by_role("button", name="Add waypoint").click()
        page.get_by_role("button", name="Add waypoint").click()
        after = collector.capture(_shot(page, artifacts, "canvas-after"))
        assert after.semantic_summary.canvas["signature"] != before_signature
        assert "Waypoint count: 2" in after.accessibility_summary

        page.get_by_role("button", name="Review and create").click()
        confirmation = collector.capture(_shot(page, artifacts, "confirmation"))
        assert pack.page_stage(confirmation) == "scenario_confirmation"
        started = confirmation.captured_at
        page.get_by_role("button", name="Create scenario").click()
        final = collector.capture(_shot(page, artifacts, "scenario-list"))
        history = [
            StepResult(
                index=8,
                action="click",
                description="点击确认创建想定 scenario_test_A",
                target_summary="role=button name=Create scenario",
                status=Status.INCOMPLETE,
                started_at=started,
                ended_at=final.captured_at,
                after=final,
                progress_assessment="pending_business_verification",
            )
        ]

        assert pack.page_stage(final) == "scenario_list"
        assert "scenario_test_A" in final.semantic_summary.resource_names
        assert "scenario_created_verified" not in pack.remaining_stages(
            final, history, scenario
        )
        browser.close()


def _shot(page, artifacts: ArtifactManager, name: str) -> str:
    target, relative = artifacts.screenshot_path(name)
    page.screenshot(path=str(target))
    return relative
