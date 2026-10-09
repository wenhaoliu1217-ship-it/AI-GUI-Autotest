from types import SimpleNamespace

import gui_agent.onboarding.session as session
from gui_agent.onboarding.models import ProjectConfig


def _project() -> ProjectConfig:
    return ProjectConfig(
        id="platform-session-test",
        name="Platform session",
        baseUrl="https://example.com",
        allowedHosts=["example.com"],
    )


def test_windows_keeps_legacy_storage_filename(monkeypatch) -> None:
    monkeypatch.setattr(session.os, "name", "nt")

    assert session.storage_state_filename() == "storage-state.dpapi"
    assert session._encryption_description() == "Windows DPAPI / CurrentUser"


def test_unix_uses_secure_filename_and_reports_keyring_backend(monkeypatch) -> None:
    monkeypatch.setattr(session.os, "name", "posix")
    monkeypatch.setattr(session.os, "uname", lambda: SimpleNamespace(sysname="Linux"), raising=False)

    assert session.storage_state_filename() == "storage-state.secure"
    assert session._encryption_description() == "linux OS keyring + Fernet"


def test_unix_missing_optional_dependency_is_actionable(monkeypatch) -> None:
    monkeypatch.setattr(session.os, "name", "posix")
    monkeypatch.setitem(__import__("sys").modules, "keyring", None)

    try:
        session._keyring_backend()
    except session.SessionStateError as exc:
        assert "requirements-platform.txt" in str(exc)
    else:
        raise AssertionError("missing keyring dependency must be reported")
