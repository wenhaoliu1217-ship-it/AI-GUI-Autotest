from pathlib import Path
import time

from fastapi.testclient import TestClient

import gui_agent.api.server as api_server
from gui_agent.demo.server import DemoServer, find_available_port
from gui_agent.domain.models import ActionType, Locator, Step, TestPlan as ExecutionPlan
from gui_agent.planning.demo_planner import plan_from_text
from gui_agent.onboarding.store import ProjectStore
from gui_agent.execution.orchestrator import RunOrchestrator


def _local_project(client: TestClient, base_url: str, tmp_path: Path, monkeypatch) -> dict:
    monkeypatch.setattr(api_server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    response = client.post("/api/projects", json={
        "name": "受控本地 E2E",
        "baseUrl": base_url,
        "allowedHosts": ["127.0.0.1"],
        "allowPrivateNetwork": True,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_http_api_executes_browser_and_serves_step_screenshot(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(api_server, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    with DemoServer(port=find_available_port()) as demo:
        monkeypatch.setenv("TEST_BASE_URL", demo.url)
        monkeypatch.setenv("ADMIN_USERNAME", "admin")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
        client = TestClient(api_server.app)
        project = _local_project(client, demo.url, tmp_path, monkeypatch)
        response = client.post(
            "/api/runs",
            json={
                "plan": plan_from_text("管理员登录后新建客户并分配给员工").model_dump(mode="json"),
                "projectId": project["id"],
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "passed"
    assert payload["runner_isolation"]["mode"] == "spawn_process"
    assert payload["runner_isolation"]["windows_job_assigned"] is True
    assert payload["runner_isolation"]["forced_termination"] is False
    assert payload["steps"]
    assert all(step["screenshot"] for step in payload["steps"])
    assert all(step["before"]["screenshot"] for step in payload["steps"])
    assert all(step["after"]["url"] for step in payload["steps"])
    screenshot_url = f"{payload['artifact_base_url']}/{payload['steps'][-1]['screenshot']}"
    screenshot = TestClient(api_server.app).get(screenshot_url)
    assert screenshot.status_code == 200
    assert screenshot.headers["content-type"] == "image/png"
    assert screenshot.content.startswith(b"\x89PNG")
    html_report = TestClient(api_server.app).get(f"/api/runs/{payload['run_id']}/report.html")
    assert html_report.status_code == 200
    assert "Runner 隔离：spawn_process" in html_report.content.decode("utf-8")


def test_http_api_reports_real_locator_failure_with_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(api_server, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    with DemoServer(port=find_available_port()) as demo:
        client = TestClient(api_server.app)
        project = _local_project(client, demo.url, tmp_path, monkeypatch)
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
        response = client.post(
            "/api/runs",
            json={"plan": plan.model_dump(mode="json"), "timeoutMs": 1000, "projectId": project["id"]},
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
        project = _local_project(client, demo.url, tmp_path, monkeypatch)
        started = client.post(
            "/api/runs",
            json={"plan": plan.model_dump(mode="json"), "asyncExecution": True, "projectId": project["id"]},
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


def test_http_api_confirms_or_rejects_dangerous_action_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api_server, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(api_server, "RUN_ORCHESTRATOR", RunOrchestrator())
    with DemoServer(port=find_available_port()) as demo:
        client = TestClient(api_server.app)
        project = _local_project(client, demo.url, tmp_path, monkeypatch)
        plan = ExecutionPlan(
            name="危险动作 API 确认",
            base_url=demo.url,
            steps=[
                Step(action=ActionType.NAVIGATE, target="/", description="打开登录页"),
                Step(
                    action=ActionType.CLICK,
                    locator=Locator(role="button", name="登录"),
                    description="发布内容前点击登录",
                ),
            ],
        )

        def start_and_wait() -> tuple[str, dict]:
            started = client.post("/api/runs", json={
                "plan": plan.model_dump(mode="json"), "projectId": project["id"], "asyncExecution": True,
            })
            assert started.status_code == 200, started.text
            run_id = started.json()["run_id"]
            deadline = time.time() + 10
            state = client.get(f"/api/runs/{run_id}").json()
            while state["status"] != "pending_confirmation" and time.time() < deadline:
                time.sleep(0.05)
                state = client.get(f"/api/runs/{run_id}").json()
            assert state["status"] == "pending_confirmation", state
            return run_id, state

        approved_id, approved_state = start_and_wait()
        confirmation_id = approved_state["pending_confirmation"]["id"]
        wrong = client.post(f"/api/runs/{approved_id}/confirmation", json={
            "confirmationId": "wrong-id", "decision": "approved", "actor": "e2e",
        })
        assert wrong.status_code == 409
        approved = client.post(f"/api/runs/{approved_id}/confirmation", json={
            "confirmationId": confirmation_id, "decision": "approved", "actor": "e2e",
        })
        assert approved.status_code == 200, approved.text
        deadline = time.time() + 10
        approved_final = client.get(f"/api/runs/{approved_id}").json()
        while approved_final["status"] in {"running", "pending_confirmation"} and time.time() < deadline:
            time.sleep(0.05)
            approved_final = client.get(f"/api/runs/{approved_id}").json()
        assert approved_final["status"] == "passed"
        assert approved_final["confirmation_history"][0]["decision"] == "approved"
        reused = client.post(f"/api/runs/{approved_id}/confirmation", json={
            "confirmationId": confirmation_id, "decision": "approved", "actor": "e2e",
        })
        assert reused.status_code == 409

        rejected_id, rejected_state = start_and_wait()
        rejected = client.post(f"/api/runs/{rejected_id}/confirmation", json={
            "confirmationId": rejected_state["pending_confirmation"]["id"],
            "decision": "rejected", "actor": "e2e",
        })
        assert rejected.status_code == 200, rejected.text
        deadline = time.time() + 10
        rejected_final = client.get(f"/api/runs/{rejected_id}").json()
        while rejected_final["status"] in {"queued", "running", "pending_confirmation"} and time.time() < deadline:
            time.sleep(0.05)
            rejected_final = client.get(f"/api/runs/{rejected_id}").json()
        assert rejected_final["status"] == "cancelled"
        assert rejected_final["completion_reason"] == "dangerous_action_rejected"
        assert rejected_final["steps"][-1]["status"] == "skipped"
