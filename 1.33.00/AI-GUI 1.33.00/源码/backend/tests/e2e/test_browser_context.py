import pytest
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from gui_agent.domain.models import BrowserTarget
from gui_agent.execution.browser_context import resolve_browser_surface


class _Policy:
    def check_url(self, _url: str) -> None:
        return None


class _FakePage:
    url = "chrome-error://chromewebdata/"

    def is_closed(self) -> bool:
        return False

    def set_default_timeout(self, _timeout: int) -> None:
        return None

    def set_default_navigation_timeout(self, _timeout: int) -> None:
        return None


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.pages = [page]


class _RecordingPolicy:
    def __init__(self) -> None:
        self.checked: list[str] = []

    def check_url(self, url: str) -> None:
        self.checked.append(url)


def test_navigation_can_select_internal_error_page_before_recovery() -> None:
    page = _FakePage()
    policy = _RecordingPolicy()

    selected, _, evidence = resolve_browser_surface(
        _FakeContext(page),
        page,
        BrowserTarget(),
        policy,
        enforce_url_condition=False,
        validate_current_url=False,
    )

    assert selected is page
    assert evidence["url"] == "chrome-error://chromewebdata/"
    assert policy.checked == []


def test_non_navigation_still_validates_selected_page_url() -> None:
    page = _FakePage()
    policy = _RecordingPolicy()

    resolve_browser_surface(_FakeContext(page), page, BrowserTarget(), policy)

    assert policy.checked == ["chrome-error://chromewebdata/"]


@pytest.mark.e2e
def test_selects_newest_popup_and_unique_iframe_surface() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_content('<button id="open" onclick="window.open(\'about:blank\')">Open</button>')
        with page.expect_popup() as popup_info:
            page.locator("#open").click()
        popup = popup_info.value
        popup.set_content('<iframe id="payment" srcdoc="<button id=confirm>Confirm</button>"></iframe>')

        selected, surface, evidence = resolve_browser_surface(
            context, page, BrowserTarget(page="newest", frameCss="#payment"), _Policy()
        )
        assert selected == popup
        assert surface.locator("#confirm").count() == 1
        assert evidence["pageCount"] == 2
        assert evidence["frame"] == {"selector": "#payment", "matched": 1}

        opener, _, _ = resolve_browser_surface(
            context, popup, BrowserTarget(page="opener"), _Policy()
        )
        assert opener == page
        browser.close()


@pytest.mark.e2e
def test_rejects_ambiguous_iframe_scope() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_content('<iframe class="payment"></iframe><iframe class="payment"></iframe>')
        with pytest.raises(PlaywrightError, match="必须唯一"):
            resolve_browser_surface(
                context, page, BrowserTarget(frameCss=".payment"), _Policy()
            )
        browser.close()
