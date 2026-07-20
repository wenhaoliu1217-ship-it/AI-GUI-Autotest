from fastapi.testclient import TestClient
from pathlib import Path

from gui_agent.api import server
from gui_agent.onboarding.store import ProjectStore

app = server.app


client = TestClient(app)


def test_health_declares_real_engine() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "real"
    assert response.json()["engine"] == "playwright-chromium"
    assert response.json()["aiConfigStorage"] == "request-memory-only"


def test_planner_returns_422_for_unexecutable_text() -> None:
    response = client.post(
        "/api/plans/generate",
        json={
            "name": "模糊流程",
            "targetUrl": "https://example.com",
            "flow": "随便帮我测试一下",
            "role": "tester",
        },
    )
    assert response.status_code == 422
    assert "没有识别出" in response.json()["detail"]


def test_ai_endpoint_rejects_empty_key_without_echoing_it() -> None:
    response = client.post(
        "/api/ai/test",
        json={
            "settings": {
                "protocol": "responses",
                "baseUrl": "https://api.openai.com/v1",
                "model": "test-model",
                "apiKey": "",
            }
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "请填写新的 API Key"


def test_project_config_is_persisted_and_normalizes_allowed_hosts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    response = client.post(
        "/api/projects",
        json={
            "name": "企业测试站",
            "baseUrl": "https://example.com/app/",
            "allowedHosts": ["https://assets.example.net/path"],
            "forbiddenActions": ["支付", "删除数据"],
            "onboardingLevel": "L0",
            "limits": {"maxSteps": 30, "timeoutSeconds": 300, "maxModelCalls": 10},
        },
    )
    assert response.status_code == 200
    project = response.json()
    assert project["baseUrl"] == "https://example.com/app"
    assert project["allowedHosts"] == ["example.com", "assets.example.net"]
    assert (tmp_path / "projects" / project["id"] / "project.json").is_file()
    assert client.get("/api/projects").json()[0]["name"] == "企业测试站"


def test_project_rejects_non_http_base_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    response = client.post("/api/projects", json={"name": "非法站点", "baseUrl": "file:///etc/passwd"})
    assert response.status_code == 422
    assert "http/https" in str(response.json())


def test_project_session_is_domain_checked_and_dpapi_encrypted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post(
        "/api/projects",
        json={"name": "L1 项目", "baseUrl": "https://example.com", "onboardingLevel": "L1"},
    ).json()
    state = {
        "cookies": [{
            "name": "session", "value": "top-secret-session", "domain": "example.com", "path": "/",
            "expires": 2_000_000_000, "httpOnly": True, "secure": True, "sameSite": "Lax",
        }],
        "origins": [],
    }
    response = client.post(f"/api/projects/{project['id']}/session", json={"storageState": state})
    assert response.status_code == 200
    assert response.json()["cookieCount"] == 1
    assert response.json()["encryption"] == "Windows DPAPI / CurrentUser"
    encrypted = (tmp_path / "projects" / project["id"] / "storage-state.dpapi").read_bytes()
    assert b"top-secret-session" not in encrypted
    metadata = client.get(f"/api/projects/{project['id']}/session").json()
    assert metadata["domains"] == ["example.com"]
    assert "value" not in str(metadata)


def test_project_session_rejects_cookie_outside_allowed_domains(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post("/api/projects", json={"name": "安全项目", "baseUrl": "https://example.com"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/session",
        json={"storageState": {"cookies": [{"name": "x", "value": "y", "domain": "evil.example", "path": "/"}], "origins": []}},
    )
    assert response.status_code == 422
    assert "不在项目允许列表" in response.json()["detail"]


def test_environment_scenario_project_update_and_audit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post("/api/projects", json={"name": "企业闭环", "baseUrl": "https://example.com"}).json()
    project_id = project["id"]

    environment = client.post(
        f"/api/projects/{project_id}/environments",
        json={
            "name": "预发布",
            "variables": {"TENANT": "qa"},
            "secretRefs": {"PASSWORD": "QA_PASSWORD"},
            "ignoreRules": ["**/analytics/**"],
            "appBridge": {"enabled": True, "adapter": "cesium"},
        },
    )
    assert environment.status_code == 200, environment.text
    assert environment.json()["secretRefs"] == {"PASSWORD": "QA_PASSWORD"}
    assert "QA_PASSWORD" in (tmp_path / "projects" / project_id / "environments" / f"{environment.json()['id']}.json").read_text(encoding="utf-8")

    scenario = client.post(
        f"/api/projects/{project_id}/scenarios",
        json={
            "name": "搜索商品",
            "goal": "搜索商品 A 并确认价格",
            "testData": {"keyword": "A"},
            "expectedResults": ["显示商品 A"],
            "forbiddenActions": ["支付"],
        },
    )
    assert scenario.status_code == 200, scenario.text
    assert client.get(f"/api/projects/{project_id}/scenarios").json()[0]["goal"].startswith("搜索")

    updated = client.put(f"/api/projects/{project_id}", json={"onboardingLevel": "L3", "name": "企业闭环 V2"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["onboardingLevel"] == "L3"
    audit = client.get(f"/api/projects/{project_id}/audit").json()
    assert [item["action"] for item in audit] == ["create", "create", "create", "update"]
    assert all("QA_PASSWORD" not in str(item) for item in audit)


def test_environment_rejects_invalid_variable_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post("/api/projects", json={"name": "安全配置", "baseUrl": "https://example.com"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/environments",
        json={"name": "测试", "variables": {"INVALID-NAME": "value"}},
    )
    assert response.status_code == 422
    assert "环境变量名非法" in response.json()["detail"]


def test_interactive_login_recording_uses_existing_secure_session_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post("/api/projects", json={"name": "登录录制", "baseUrl": "https://example.com", "onboardingLevel": "L1"}).json()

    class FakeRecording:
        id = "recording-test"
        project_id = project["id"]
        status = "recording"
        result = None

    class FakeManager:
        def start(self, saved_project, store, timeout):
            assert saved_project.id == project["id"] and timeout == 300
            return FakeRecording()

        def complete(self, recording_id):
            assert recording_id == "recording-test"
            recording = FakeRecording()
            recording.status = "completed"
            recording.result = {"projectId": project["id"], "encryption": "Windows DPAPI / CurrentUser"}
            return recording

    monkeypatch.setattr(server, "LOGIN_RECORDINGS", FakeManager())
    started = client.post(f"/api/projects/{project['id']}/session-recordings", json={"timeoutSeconds": 300})
    assert started.status_code == 200 and started.json()["status"] == "recording"
    completed = client.post(f"/api/projects/{project['id']}/session-recordings/recording-test/complete")
    assert completed.status_code == 200
    assert completed.json()["session"]["encryption"] == "Windows DPAPI / CurrentUser"


def test_bulk_delete_runs_removes_only_validated_artifact_directories(tmp_path: Path, monkeypatch) -> None:
    artifacts = (tmp_path / "artifacts").resolve()
    monkeypatch.setattr(server, "ARTIFACTS_ROOT", artifacts)
    for run_id in ("run-one", "run-two", "run-keep"):
        run_dir = artifacts / run_id
        (run_dir / "screenshots").mkdir(parents=True)
        (run_dir / "run.json").write_text('{"run_id":"' + run_id + '"}', encoding="utf-8")
        (run_dir / "screenshots" / "evidence.png").write_bytes(b"evidence")

    response = client.post("/api/runs/delete", json={"runIds": ["run-one", "run-two", "run-one"]})

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": ["run-one", "run-two"], "count": 2}
    assert not (artifacts / "run-one").exists()
    assert not (artifacts / "run-two").exists()
    assert (artifacts / "run-keep" / "run.json").is_file()


def test_bulk_delete_validates_entire_batch_before_removing_any_run(tmp_path: Path, monkeypatch) -> None:
    artifacts = (tmp_path / "artifacts").resolve()
    monkeypatch.setattr(server, "ARTIFACTS_ROOT", artifacts)
    run_dir = artifacts / "run-valid"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text('{"run_id":"run-valid"}', encoding="utf-8")

    response = client.post("/api/runs/delete", json={"runIds": ["run-valid", "../outside"]})

    assert response.status_code == 400
    assert (run_dir / "run.json").is_file()
