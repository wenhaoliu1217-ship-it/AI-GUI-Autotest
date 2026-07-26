from playwright.sync_api import Error as PlaywrightError

from gui_agent.onboarding.recording import launch_visible_login_browser


class FakeChromium:
    def __init__(self, edge_available: bool) -> None:
        self.edge_available = edge_available
        self.calls: list[dict] = []

    def launch(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("channel") == "msedge" and not self.edge_available:
            raise PlaywrightError("Edge is unavailable")
        return object()


class FakePlaywright:
    def __init__(self, edge_available: bool) -> None:
        self.chromium = FakeChromium(edge_available)


def test_login_browser_prefers_microsoft_edge() -> None:
    playwright = FakePlaywright(edge_available=True)

    _, name = launch_visible_login_browser(playwright)

    assert name == "Microsoft Edge"
    assert playwright.chromium.calls == [{"channel": "msedge", "headless": False}]


def test_login_browser_falls_back_to_packaged_browser() -> None:
    playwright = FakePlaywright(edge_available=False)

    _, name = launch_visible_login_browser(playwright)

    assert name == "内置测试浏览器"
    assert playwright.chromium.calls == [
        {"channel": "msedge", "headless": False},
        {"headless": False},
    ]
