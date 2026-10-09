from gui_agent.onboarding.models import ProjectConfig
from gui_agent.onboarding.session import (
    SessionStateError,
    capture_session_storage_for_project,
    filter_storage_state_for_project,
    playwright_storage_state,
    session_storage_init_script,
    validate_storage_state,
)


def _project() -> ProjectConfig:
    return ProjectConfig(
        id="project-test",
        name="Test project",
        baseUrl="https://ion.cesium.com",
        allowedHosts=["ion.cesium.com"],
    )


def test_parent_domain_cookie_is_valid_for_allowed_subdomain() -> None:
    metadata = validate_storage_state(
        _project(),
        {
            "cookies": [
                {
                    "name": "session",
                    "domain": ".cesium.com",
                }
            ],
            "origins": [],
        },
    )

    assert metadata.cookie_count == 1
    assert metadata.domains == ["cesium.com"]


def test_unrelated_cookie_domain_is_rejected() -> None:
    try:
        validate_storage_state(
            _project(),
            {
                "cookies": [
                    {
                        "name": "session",
                        "domain": ".example.test",
                    }
                ],
                "origins": [],
            },
        )
    except SessionStateError as exc:
        assert str(exc) == "Cookie 域名不在项目允许列表：example.test"
    else:
        raise AssertionError("unrelated cookie domains must remain blocked")


def test_recorded_state_drops_unrelated_third_party_data() -> None:
    state = filter_storage_state_for_project(
        _project(),
        {
            "cookies": [
                {"name": "ion-session", "domain": ".cesium.com"},
                {"name": "analytics", "domain": ".third-party.test"},
            ],
            "origins": [
                {"origin": "https://ion.cesium.com", "localStorage": []},
                {"origin": "https://third-party.test", "localStorage": []},
            ],
            "sessionStorage": [
                {"origin": "https://ion.cesium.com", "items": [{"name": "token", "value": "opaque"}]},
                {"origin": "https://third-party.test", "items": [{"name": "tracking", "value": "opaque"}]},
            ],
        },
    )

    assert [cookie["name"] for cookie in state["cookies"]] == ["ion-session"]
    assert [origin["origin"] for origin in state["origins"]] == [
        "https://ion.cesium.com"
    ]
    assert [origin["origin"] for origin in state["sessionStorage"]] == [
        "https://ion.cesium.com"
    ]


def test_session_storage_is_validated_and_removed_from_playwright_state() -> None:
    state = {
        "cookies": [],
        "origins": [],
        "sessionStorage": [
            {"origin": "https://ion.cesium.com", "items": [{"name": "token", "value": "opaque"}]},
        ],
    }

    metadata = validate_storage_state(_project(), state)

    assert metadata.session_storage_origin_count == 1
    assert metadata.session_storage_item_count == 1
    assert metadata.domains == ["ion.cesium.com"]
    assert playwright_storage_state(state) == {"cookies": [], "origins": []}


def test_session_storage_init_script_is_origin_scoped() -> None:
    state = {
        "cookies": [],
        "origins": [],
        "sessionStorage": [
            {"origin": "https://ion.cesium.com", "items": [{"name": "token", "value": "opaque"}]},
        ],
    }

    script = session_storage_init_script(state)

    assert script is not None
    assert "window.location.origin" in script
    assert "https://ion.cesium.com" in script
    assert "sessionStorage.setItem" in script


def test_capture_session_storage_uses_only_allowed_origins() -> None:
    class FakePage:
        def __init__(self, origin: str) -> None:
            self.origin = origin

        def evaluate(self, _script: str) -> dict:
            return {"origin": self.origin, "items": [{"name": "token", "value": "opaque"}]}

    captured = capture_session_storage_for_project(
        _project(),
        [FakePage("https://ion.cesium.com"), FakePage("https://third-party.test")],
    )

    assert captured == [
        {"origin": "https://ion.cesium.com", "items": [{"name": "token", "value": "opaque"}]},
    ]
