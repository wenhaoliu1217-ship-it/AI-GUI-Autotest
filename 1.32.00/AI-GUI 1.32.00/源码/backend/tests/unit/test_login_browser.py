from playwright.sync_api import Error as PlaywrightError

import gui_agent.onboarding.recording as recording_module
from gui_agent.onboarding.models import ProjectConfig
from gui_agent.onboarding.recording import (
    _open_user_controlled_login_page,
    _project_storage_state,
    launch_visible_login_browser,
)


class FakeChromium:
    def __init__(self, edge_available: bool) -> None:
        self.edge_available = edge_available
        self.calls: list[dict] = []

    def launch(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("channel") == "msedge" and not self.edge_available:
            raise PlaywrightError("Edge is unavailable")
        return object()

    def connect_over_cdp(self, url: str):
        self.calls.append({"connect_over_cdp": url})
        return object()


class FakePlaywright:
    def __init__(self, edge_available: bool) -> None:
        self.chromium = FakeChromium(edge_available)


class FakePage:
    def __init__(self) -> None:
        self.goto_calls: list[tuple[str, str]] = []

    def goto(self, url: str, wait_until: str):
        self.goto_calls.append((url, wait_until))


class UserControlledContext:
    def __init__(self) -> None:
        self.page = FakePage()

    def new_page(self):
        return self.page

    def route(self, *_args, **_kwargs):
        raise AssertionError("The user-controlled login window must not install a route guard")


def test_login_browser_prefers_standard_microsoft_edge(monkeypatch) -> None:
    playwright = FakePlaywright(edge_available=True)
    cleanup = lambda: None
    monkeypatch.setattr(
        recording_module,
        "_launch_standard_edge_browser",
        lambda _playwright: ("browser", "Microsoft Edge", True, cleanup),
    )

    browser, name, shared, returned_cleanup = launch_visible_login_browser(playwright)

    assert browser == "browser"
    assert name == "Microsoft Edge"
    assert shared is True
    assert returned_cleanup is cleanup
    assert playwright.chromium.calls == []


def test_login_browser_falls_back_to_packaged_browser(monkeypatch) -> None:
    playwright = FakePlaywright(edge_available=False)
    monkeypatch.setattr(
        recording_module,
        "_launch_standard_edge_browser",
        lambda _playwright: (_ for _ in ()).throw(RuntimeError("standard Edge unavailable")),
    )

    _, name, shared, cleanup = launch_visible_login_browser(playwright)

    assert name == "内置测试浏览器"
    assert shared is False
    assert playwright.chromium.calls == [
        {"channel": "msedge", "headless": False},
        {"headless": False},
    ]
    cleanup()


def test_login_browser_uses_the_edge_window_that_displays_the_gui(monkeypatch) -> None:
    playwright = FakePlaywright(edge_available=True)
    monkeypatch.setenv("GUI_BROWSER_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setenv("GUI_BROWSER_NAME", "Microsoft Edge")

    _, name, shared, cleanup = launch_visible_login_browser(playwright)

    assert name == "Microsoft Edge（与 GUI 同一窗口）"
    assert shared is True
    assert playwright.chromium.calls == [{"connect_over_cdp": "http://127.0.0.1:9222"}]
    cleanup()


def test_shared_browser_only_exports_the_current_websites_login_state() -> None:
    project = ProjectConfig(
        id="project-ion",
        name="Cesium ion",
        baseUrl="https://ion.cesium.com",
        allowedHosts=["ion.cesium.com"],
    )
    state = {
        "cookies": [
            {"name": "ion", "value": "1", "domain": "ion.cesium.com"},
            {"name": "cesium-parent", "value": "2", "domain": ".cesium.com"},
            {"name": "gui", "value": "3", "domain": "127.0.0.1"},
        ],
        "origins": [
            {"origin": "https://ion.cesium.com", "localStorage": []},
            {"origin": "http://127.0.0.1:8080", "localStorage": []},
        ],
    }

    filtered = _project_storage_state(project, state)

    assert [cookie["name"] for cookie in filtered["cookies"]] == ["ion", "cesium-parent"]
    assert [origin["origin"] for origin in filtered["origins"]] == ["https://ion.cesium.com"]


def test_user_controlled_login_does_not_install_the_automation_route_guard() -> None:
    project = ProjectConfig(
        id="project-ion",
        name="Cesium ion",
        baseUrl="https://ion.cesium.com",
        allowedHosts=["ion.cesium.com", "api.cesium.com"],
    )
    context = UserControlledContext()

    page = _open_user_controlled_login_page(context, project)

    assert page is context.page
    assert page.goto_calls == [("https://ion.cesium.com", "domcontentloaded")]
