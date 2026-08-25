from __future__ import annotations

from pathlib import Path

from gui_agent.opensource.catalog import PROJECTS, _project_payload, catalog_payload


def test_catalog_contains_all_archived_reference_projects() -> None:
    ids = {project.project_id for project in PROJECTS}
    assert ids == {
        "playwright-mcp",
        "playwright-cli",
        "stagehand",
        "browser-use",
        "openadapt",
        "browsergym",
        "agentlab",
        "webarena",
        "osworld",
        "ui-tars",
        "testzeus-hercules",
    }


def test_catalog_reports_reference_checkout_and_license(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "references"
    project = root / "playwright-mcp"
    project.mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "LICENSE").write_text("Apache License", encoding="utf-8")
    monkeypatch.setenv("GUI_AGENT_OPEN_SOURCE_ROOT", str(root))

    payload = catalog_payload()
    item = next(item for item in payload["projects"] if item["id"] == "playwright-mcp")
    missing = next(item for item in payload["projects"] if item["id"] == "stagehand")

    assert payload["referenceRoot"] == str(root.resolve())
    assert item["referenceAvailable"] is True
    assert item["gitCheckout"] is True
    assert item["licenseFile"] == "LICENSE"
    assert missing["referenceAvailable"] is False
    assert payload["summary"]["available"] == 1
    assert payload["summary"]["adapterReady"] == 9
    assert {item["id"] for item in payload["executionProfiles"]} >= {"stagehand", "browser-use", "ui-tars", "playwright-cli", "openadapt"}
    assert payload["projectCount"] == 11


def test_webarena_requires_a_ready_docker_daemon(monkeypatch) -> None:
    webarena = next(project for project in PROJECTS if project.project_id == "webarena")
    runtime = {
        "node": {"available": True},
        "python": {"available": True},
        "docker": {"available": True, "daemonReady": False, "status": "daemon_unavailable"},
    }

    item = _project_payload(webarena, None, runtime)

    assert item["runtimeReady"] is False
