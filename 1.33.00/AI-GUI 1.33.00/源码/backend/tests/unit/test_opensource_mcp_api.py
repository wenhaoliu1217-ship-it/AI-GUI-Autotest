from __future__ import annotations

from fastapi.testclient import TestClient

from gui_agent.api import server


def test_playwright_mcp_observe_route_applies_domain_policy_and_returns_evidence(monkeypatch) -> None:
    observed = {
        "adapter": "playwright-mcp",
        "runtimeEvidence": {"toolCount": 23, "unsafeTools": ["browser_evaluate"]},
        "observation": {
            "url": "https://example.test/dashboard",
            "title": "Fixture",
            "dom_summary": [],
            "accessibility_summary": "- heading \"Fixture\"",
            "console_errors": [],
            "page_errors": [],
            "failed_requests": [],
        },
        "toolResult": {"adapter": "playwright-mcp", "ok": True},
    }
    monkeypatch.setattr(server, "observe_playwright_mcp_url", lambda _url, _hosts: observed)

    response = TestClient(server.app).post(
        "/api/opensource/playwright-mcp/observe",
        json={
            "url": "https://example.test/",
            "allowedHosts": ["example.test"],
            "allowPrivateNetwork": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["runtimeEvidence"]["toolCount"] == 23


def test_playwright_mcp_observe_route_rejects_an_unauthorized_observed_redirect(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "observe_playwright_mcp_url",
        lambda _url, _hosts: {"observation": {"url": "https://not-allowed.test/"}},
    )

    response = TestClient(server.app).post(
        "/api/opensource/playwright-mcp/observe",
        json={
            "url": "https://example.test/",
            "allowedHosts": ["example.test"],
            "allowPrivateNetwork": False,
        },
    )

    assert response.status_code == 422


def test_evaluation_normalize_route_exposes_read_only_browsergym_contract() -> None:
    response = TestClient(server.app).post(
        "/api/opensource/evaluation/normalize",
        json={
            "provider": "browsergym",
            "payload": {
                "taskId": "fixture.search",
                "steps": [{"step": 0, "action": "observe", "reward": 1, "terminated": True, "truncated": False}],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["adapter"] == "browsergym"
    assert response.json()["actionPolicy"] == "trajectory_evaluation_only"


def test_evaluation_normalize_route_exposes_read_only_agentlab_contract() -> None:
    response = TestClient(server.app).post(
        "/api/opensource/evaluation/normalize",
        json={
            "provider": "agentlab",
            "payload": {
                "exp_args": {"env_args": {"task_name": "fixture.task"}},
                "summary_info": {"terminated": True},
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["adapter"] == "agentlab"
    assert response.json()["actionPolicy"] == "experiment_metadata_only"


def test_evaluation_fixture_routes_are_explicitly_product_owned() -> None:
    client = TestClient(server.app)
    catalog_response = client.get("/api/opensource/evaluation/fixtures")
    run_response = client.post(
        "/api/opensource/evaluation/fixture",
        json={"provider": "browsergym"},
    )

    assert catalog_response.status_code == 200
    assert catalog_response.json()[0]["source"] == "product_owned_deterministic_fixture"
    assert run_response.status_code == 200
    assert run_response.json()["fixture"]["upstreamRuntimeStarted"] is False
