from playwright.sync_api import sync_playwright

from gui_agent.execution.observation import ObservationCollector
from gui_agent.execution.stability import prepare_action
from gui_agent.domain.models import BrowserTarget, Locator, Step
from gui_agent.security.redaction import Redactor


def test_browser_diagnostics_detect_reviewable_layout_and_blank_page_signals() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 800, "height": 600})
        collector = ObservationCollector(page, None, Redactor())  # type: ignore[arg-type]

        page.set_content("""
          <style>
            #label { width: 45px; height: 18px; overflow: hidden; white-space: nowrap; }
            #cover { position: fixed; left: 0; top: 40px; width: 120px; height: 40px; z-index: 2; }
            #submit { position: fixed; left: 0; top: 40px; width: 120px; height: 40px; }
          </style>
          <div id="label" data-testid="truncated-label">这是一段确定会被容器明显截断的文字</div>
          <button id="submit" aria-label="提交订单">提交</button>
          <div id="cover">遮挡层</div>
        """)
        observation = collector.capture(None)
        kinds = {issue.kind for issue in observation.page_issues}
        assert "text_truncated" in kinds
        assert "element_obscured" in kinds

        page.set_content("""
          <nav>
            <ul><li class="nav-item active"><a href="/stories">Stories</a></li></ul>
            <a href="/assets" aria-current="false">Assets</a>
          </nav>
        """)
        navigation = collector.capture(None)
        assert any(
            "href=/stories" in item and "ancestor-state=active" in item
            for item in navigation.dom_summary
        )
        assert any(
            "href=/assets" in item and "aria-current=false" in item
            for item in navigation.dom_summary
        )

        page.set_content("""
          <main id="app"></main>
          <script>
            setTimeout(() => {
              const nav = document.createElement('nav');
              nav.textContent = 'Stories';
              document.querySelector('#app').appendChild(nav);
            }, 100);
          </script>
        """)
        prepared = prepare_action(
            page,
            Step(
                action="wait_for",
                locator=Locator(role="navigation"),
                browserTarget=BrowserTarget(waitTimeoutMs=2_000),
            ),
            bridge_adapter=None,
            timeout_ms=250,
        )
        assert prepared.evidence["passed"] is True
        assert prepared.evidence["mode"] == "locator_wait"

        page.goto("data:text/html,<html><body></body></html>")
        blank = collector.capture(None)
        assert any(issue.kind == "blank_page" for issue in blank.page_issues)
        browser.close()
