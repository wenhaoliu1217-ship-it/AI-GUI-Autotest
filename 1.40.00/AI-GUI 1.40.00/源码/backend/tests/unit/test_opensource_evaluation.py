from gui_agent.opensource.evaluation import evaluation_fixture_catalog_payload, run_evaluation_fixture
from fastapi.testclient import TestClient

from gui_agent.api import server


def test_product_owned_evaluation_fixtures_are_available_without_upstream_runtime() -> None:
    fixtures = evaluation_fixture_catalog_payload()

    assert {item["provider"] for item in fixtures} == {"browsergym", "agentlab", "webarena", "browser-use", "ui-tars", "playwright-cli"}
    assert all(item["available"] for item in fixtures)
    assert all(item["upstreamRuntimeRequired"] is False for item in fixtures)
    assert all(item["actionPolicy"] == "contract_validation_only" for item in fixtures)


def test_browsergym_fixture_runs_only_the_product_normalizer() -> None:
    result = run_evaluation_fixture("browsergym")

    assert result["adapter"] == "browsergym"
    assert result["status"] == "episode_ready"
    assert result["summary"]["success"] is True
    assert result["fixture"] == {
        "id": "browsergym-contract-fixture",
        "source": "product_owned_deterministic_fixture",
        "upstreamRuntimeStarted": False,
        "actionPolicy": "contract_validation_only",
    }


def test_agentlab_fixture_runs_only_the_product_normalizer() -> None:
    result = run_evaluation_fixture("agentlab")

    assert result["adapter"] == "agentlab"
    assert result["status"] == "experiment_ready"
    assert result["runStatus"] == "done"
    assert result["trajectorySummary"]["success"] is None
    assert result["fixture"]["upstreamRuntimeStarted"] is False


def test_webarena_fixture_runs_only_the_product_normalizer() -> None:
    result = run_evaluation_fixture("webarena")

    assert result["adapter"] == "webarena"
    assert result["status"] == "trajectory_ready"
    assert result["summary"]["success"] is True
    assert result["fixture"]["upstreamRuntimeStarted"] is False


def test_browser_use_fixture_runs_only_the_product_normalizer() -> None:
    result = run_evaluation_fixture("browser-use")

    assert result["adapter"] == "browser-use"
    assert result["status"] == "agent_state_ready"
    assert result["summary"] == {"stepCount": 2, "done": True, "success": True}
    assert result["actionPolicy"] == "agent_state_preview_only_no_execution"
    assert result["fixture"]["upstreamRuntimeStarted"] is False


def test_ui_tars_fixture_runs_only_the_product_normalizer() -> None:
    result = run_evaluation_fixture("ui-tars")

    assert result["adapter"] == "ui-tars"
    assert result["status"] == "candidate_actions_ready"
    assert result["summary"]["actionCount"] == 2
    assert result["actionPolicy"] == "visual_candidate_only_no_execution"
    assert result["fixture"]["upstreamRuntimeStarted"] is False


def test_playwright_cli_fixture_runs_only_the_product_normalizer() -> None:
    result = run_evaluation_fixture("playwright-cli")

    assert result["adapter"] == "playwright-cli"
    assert result["status"] == "trace_ready"
    assert result["summary"] == {
        "commandCount": 5,
        "navigationCount": 1,
        "writeLikeCount": 1,
        "unsafeCount": 1,
    }
    assert result["fixture"]["upstreamRuntimeStarted"] is False


def test_stagehand_and_openadapt_are_available_through_the_gui_read_only_normalize_route() -> None:
    client = TestClient(server.app)

    stagehand = client.post(
        "/api/opensource/evaluation/normalize",
        json={
            "provider": "stagehand",
            "payload": {
                "candidates": [{"action": "click", "description": "Open details", "locator": {"role": "button"}}],
            },
        },
    )
    assert stagehand.status_code == 200
    assert stagehand.json()["adapter"] == "stagehand"
    assert stagehand.json()["evidence"]["actionPolicy"] == "candidate_only_no_execution"

    openadapt = client.post(
        "/api/opensource/evaluation/normalize",
        json={
            "provider": "openadapt",
            "payload": {
                "checkpoint": {"id": "checkpoint-1", "status": "paused", "stepIndex": 3},
                "evidence": {"verified": True, "eventCount": 4},
            },
        },
    )
    assert openadapt.status_code == 200
    assert openadapt.json()["adapter"] == "openadapt"
    assert openadapt.json()["actionPolicy"] == "checkpoint_only_no_execution"

    browser_use = client.post(
        "/api/opensource/evaluation/normalize",
        json={
            "provider": "browser-use",
            "payload": {
                "goal": "Inspect customers",
                "history": [{"step": 0, "action": {"type": "observe"}, "done": True}],
            },
        },
    )
    assert browser_use.status_code == 200
    assert browser_use.json()["adapter"] == "browser-use"
    assert browser_use.json()["summary"]["stepCount"] == 1

    ui_tars = client.post(
        "/api/opensource/evaluation/normalize",
        json={
            "provider": "ui-tars",
            "payload": {
                "coordinate_scale": 1000,
                "response": "Action: click(point='<point>100 200</point>')",
            },
        },
    )
    assert ui_tars.status_code == 200
    assert ui_tars.json()["adapter"] == "ui-tars"
    assert ui_tars.json()["actions"][0]["coordinates"]["start"] == [0.1, 0.2, 0.1, 0.2]

    playwright_cli = client.post(
        "/api/opensource/evaluation/normalize",
        json={
            "provider": "playwright-cli",
            "payload": {"commands": [{"command": "browser_navigate https://example.test/"}]},
        },
    )
    assert playwright_cli.status_code == 200
    assert playwright_cli.json()["adapter"] == "playwright-cli"
    assert playwright_cli.json()["summary"]["navigationCount"] == 1


def test_upstream_runtime_is_not_ready_without_explicit_isolated_python(monkeypatch) -> None:
    monkeypatch.delenv("GUI_AGENT_OPENSOURCE_RUNTIME_PYTHON", raising=False)

    response = TestClient(server.app).get("/api/opensource/evaluation/runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["executionBoundary"] == "isolated_child_process"
    assert payload["summary"]["runtimeReady"] == 0
    assert all(item["status"] == "not_configured" for item in payload["providers"])
