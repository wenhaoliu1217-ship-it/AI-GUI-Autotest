from gui_agent.onboarding.models import ProjectConfig
from gui_agent.onboarding.session import (
    filter_storage_state_for_project,
    playwright_storage_state,
    session_storage_init_script,
    validate_storage_state,
)


def _project() -> ProjectConfig:
    return ProjectConfig(
        id="project-session",
        name="Session test",
        baseUrl="https://example.com",
        allowedHosts=["example.com"],
    )


def test_session_storage_is_removed_from_playwright_state_and_validated() -> None:
    state = {
        "cookies": [],
        "origins": [],
        "sessionStorage": [
            {"origin": "https://example.com", "items": [{"name": "token", "value": "redacted"}]}
        ],
    }

    metadata = validate_storage_state(_project(), state)

    assert metadata.session_storage_origin_count == 1
    assert metadata.session_storage_item_count == 1
    assert playwright_storage_state(state) == {"cookies": [], "origins": []}


def test_session_storage_init_script_is_origin_scoped() -> None:
    script = session_storage_init_script(
        {
            "cookies": [],
            "origins": [],
            "sessionStorage": [
                {"origin": "https://example.com", "items": [{"name": "token", "value": "redacted"}]}
            ],
        }
    )

    assert script is not None
    assert "window.location.origin" in script
    assert "example.com" in script


def test_filter_storage_state_keeps_only_project_hosts() -> None:
    filtered = filter_storage_state_for_project(
        _project(),
        {
            "cookies": [
                {"name": "allowed", "value": "1", "domain": "example.com"},
                {"name": "other", "value": "2", "domain": "other.example"},
            ],
            "origins": [
                {"origin": "https://example.com", "localStorage": []},
                {"origin": "https://other.example", "localStorage": []},
            ],
            "sessionStorage": [
                {"origin": "https://example.com", "items": []},
                {"origin": "https://other.example", "items": []},
            ],
        },
    )

    assert [item["name"] for item in filtered["cookies"]] == ["allowed"]
    assert [item["origin"] for item in filtered["origins"]] == ["https://example.com"]
    assert [item["origin"] for item in filtered["sessionStorage"]] == ["https://example.com"]
