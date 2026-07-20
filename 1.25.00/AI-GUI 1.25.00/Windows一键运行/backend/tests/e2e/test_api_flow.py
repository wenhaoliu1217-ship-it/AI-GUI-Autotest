from pathlib import Path
import time

from fastapi.testclient import TestClient

import gui_agent.api.server as api_server
from gui_agent.demo.server import DemoServer, find_available_port
from gui_agent.domain.models import ActionType, Locator, Step, TestPlan as ExecutionPlan
from gui_agent.planning.demo_planner import plan_from_text


def test_http_api_executes_browser_and_serves_step_screenshot(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(api_server, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    with DemoServer(port=find_available_port()) as demo:
        monkeypatch.setenv("TEST_BASE_URL", demo.url)
        monkeypatch.setenv("ADMIN_USERNAME", "admin")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
        response = TestClient(api_server.app).post(
            "/api/runs",
            json={"plan": plan_from_text("管理员登录后新建客户并分配给员工").model_dump(mode="json")},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "passed"
    assert payload["steps"]
    assert all(step["screenshot"] for step in payload["steps"])
    assert all(step["before"]["screenshot"] for step in payload["steps"])
    assert all(step["after"]["url"] for step in payload["steps"])
    screenshot_url = f"{payload['artifact_base_url']}/{payload['steps'][-1]['screenshot']}"
    screenshot = TestClient(api_server.app).get(screenshot_url)
    assert screenshot.status_code == 200
    assert screenshot.headers["content-type"] == "image/png"
    assert screenshot.content.startswith(b"\x89PNG")


def test_http_api_reports_real_locator_failure_with_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(api_server, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    with DemoServer(port=find_available_port()) as demo:
        plan = ExecutionPlan(
            name="真实失败检查",
            base_url=demo.url,
            steps=[
                Step(action=ActionType.NAVIGATE, target="/", description="打开目标网站"),
                Step(
                    action=ActionType.CLICK,
                    locator=Locator(role="button", name="页面上不存在的按钮"),
                    description="点击不存在按钮",
                ),
            ],
        )
        response = TestClient(api_server.app).post(
            "/api/runs",
            json={"plan": plan.model_dump(mode="json"), "timeoutMs": 1000},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["failed_step_index"] == 2
    assert payload["steps"][-1]["status"] == "error"
    assert payload["steps"][-1]["screenshot"]
    assert payload["steps"][-1]["after"]["screenshot"]
    assert payload["cause_hints"]


def test_http_api_cancels_background_browser_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api_server, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    with DemoServer(port=find_available_port()) as demo:
        plan = ExecutionPlan(
            name="真实取消检查",
            base_url=demo.url,
            steps=[
                Step(action=ActionType.NAVIGATE, target="/", description="打开目标网站"),
                Step(action=ActionType.NAVIGATE, target="/", description="再次打开目标网站"),
            ],
        )
        client = TestClient(api_server.app)
        started = client.post(
            "/api/runs",
            json={"plan": plan.model_dump(mode="json"), "asyncExecution": True},
        )
        assert started.status_code == 200, started.text
        run_id = started.json()["run_id"]
        assert started.json()["status"] in {"queued", "running"}

        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["cancellation_requested"] is True

        deadline = time.time() + 10
        payload = client.get(f"/api/runs/{run_id}").json()
        while payload["status"] in {"queued", "running"} and time.time() < deadline:
            time.sleep(0.05)
            payload = client.get(f"/api/runs/{run_id}").json()

    assert payload["status"] == "cancelled"
    assert payload["completion_reason"] == "cancelled_by_user"
    assert (tmp_path / "artifacts" / run_id / "run.json").is_file()
