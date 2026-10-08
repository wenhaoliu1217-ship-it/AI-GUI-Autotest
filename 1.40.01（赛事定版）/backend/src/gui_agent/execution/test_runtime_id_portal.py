from __future__ import annotations

from playwright.sync_api import sync_playwright

from gui_agent.artifacts import ArtifactManager
from gui_agent.domain.models import Locator, LocatorScope
from gui_agent.execution.observation import ObservationCollector, blocking_page_error
from gui_agent.locating.strategies import resolve_action_locator
from gui_agent.security.redaction import Redactor


def test_current_runtime_id_clicks_portal_option_outside_dialog(tmp_path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <div role="dialog" aria-label="Create model">
              <label>Simulation model</label>
              <input role="combobox" aria-label="Simulation model" aria-expanded="true">
            </div>
            <div class="ant-select-dropdown" role="listbox">
              <div role="option" onclick="this.dataset.selected='yes'">Visible model A</div>
              <div role="option" onclick="this.dataset.selected='yes'">Visible model B</div>
            </div>
            """
        )
        artifacts = ArtifactManager(tmp_path, "runtime-id-probe", Redactor())
        observation = ObservationCollector(page, artifacts, Redactor()).capture(None)
        assert observation.semantic_summary is not None
        component = observation.semantic_summary.components[0]
        options = component["visibleOptions"]
        selected = next(item for item in options if item["text"] == "Visible model B")
        runtime_id = selected["runtimeId"]

        locator = Locator(
            runtime_id=runtime_id,
            scope=LocatorScope(
                kind="dialog",
                identity="Create model",
                locator=Locator(role="dialog", name="Create model"),
            ),
        )
        resolve_action_locator(page, locator).click()

        assert page.locator(f'[data-ai-gui-runtime-id="{runtime_id}"]').get_attribute(
            "data-selected"
        ) == "yes"
        browser.close()


def test_visible_rendering_error_is_structured_and_blocks_writes(tmp_path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <main><canvas width="640" height="480"></canvas></main>
            <section role="alert">
              <p>An error occurred while rendering. Rendering has stopped.</p>
              <p>TypeError: Cannot read properties of undefined (reading 'length')</p>
              <button>OK</button>
            </section>
            """
        )
        artifacts = ArtifactManager(tmp_path, "fatal-render-probe", Redactor())
        observation = ObservationCollector(page, artifacts, Redactor()).capture(None)

        assert observation.semantic_summary is not None
        assert "fatal_render_error" in observation.semantic_summary.state_signals
        assert observation.semantic_summary.blocking_errors
        assert blocking_page_error(observation).startswith("TypeError:")
        assert any(
            issue.kind == "fatal_render_error" for issue in observation.page_issues
        )
        browser.close()
