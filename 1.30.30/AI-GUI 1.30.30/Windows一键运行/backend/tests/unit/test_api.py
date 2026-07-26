import json
from fastapi.testclient import TestClient
from pathlib import Path

from gui_agent.api import server
from gui_agent.onboarding.models import CompatibilityReport
from gui_agent.onboarding.store import ProjectStore

app = server.app


client = TestClient(app)


def test_health_declares_real_engine() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "real"
    assert response.json()["engine"] == "playwright-chromium"
    assert response.json()["aiConfigStorage"] == "request-memory-only"
    assert response.json()["appVersion"] == "1.30.30"


def test_commerce_policy_endpoint_blocks_production_order_submission() -> None:
    response = client.post(
        "/api/commerce/policy/evaluate",
        json={
            "action": "submit_order",
            "environment": "production_readonly",
            "runId": "readonly-run",
        },
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is False
    assert "正式消费者站禁止" in response.json()["reason"]


def test_jd_benchmark_api_keeps_all_scenarios_unverified() -> None:
    manifest = client.get("/api/benchmarks/jd/manifest")
    scenarios = client.get("/api/benchmarks/jd/scenarios")

    assert manifest.status_code == 200
    assert manifest.json()["plannedAttempts"] == 325
    assert scenarios.status_code == 200
    assert len(scenarios.json()) == 65
    assert all(item["verificationStatus"] == "unverified" for item in scenarios.json())


def test_commerce_profile_round_trip_and_production_run_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    monkeypatch.setattr(server.DomainPolicy, "check_url", lambda self, url: None)
    project = client.post(
        "/api/projects",
        json={
            "name": "JD production readonly",
            "baseUrl": "https://example.com",
            "commerceProfile": {
                "enabled": True,
                "environment": "production_readonly",
                "accountRef": "JD_BUYER_ACCOUNT",
                "piiMaskSelectors": ["[data-testid='mobile']"],
            },
        },
    )
    assert project.status_code == 200, project.text
    saved = project.json()
    assert saved["commerceProfile"]["environment"] == "production_readonly"

    blocked = client.post(
        "/api/runs",
        json={
            "plan": {
                "name": "forbidden order",
                "base_url": "https://example.com",
                "steps": [{
                    "action": "click",
                    "locator": {"role": "button", "name": "提交订单"},
                    "description": "提交订单",
                }],
                "assertions": [],
            },
            "projectId": saved["id"],
            "asyncExecution": True,
        },
    )
    assert blocked.status_code == 422
    assert "命中禁止动作：提交订单" in blocked.json()["detail"]


def test_cesium_bridge_reference_adapter_is_downloadable() -> None:
    response = client.get("/api/bridge/cesium-reference")

    assert response.status_code == 200
    assert "installCesiumTestBridge" in response.text
    assert "getSceneState" in response.text and "waitForSceneReady" in response.text


def test_environment_rejects_unsafe_bridge_global_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post("/api/projects", json={"name": "Bridge project", "baseUrl": "https://example.com"}).json()

    response = client.post(
        f"/api/projects/{project['id']}/environments",
        json={"name": "QA", "appBridge": {"enabled": True, "globalName": "window.alert(1)", "adapter": "generic"}},
    )

    assert response.status_code == 422
    assert "安全 JavaScript 标识符" in response.text


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


def test_adaptive_replay_requires_fresh_request_scoped_ai_settings() -> None:
    response = client.post("/api/runs/not-used/replay", json={"mode": "adaptive", "headless": True})

    assert response.status_code == 422
    assert response.json()["detail"] == "自适应回放必须显式提供本次视觉模型设置"


def test_project_config_is_persisted_and_normalizes_allowed_hosts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    response = client.post(
        "/api/projects",
        json={
            "name": "企业测试站",
            "baseUrl": "https://example.com/app/",
            "allowedHosts": ["https://assets.example.net/path"],
            "forbiddenActions": ["支付", "删除数据"],
            "allowPrivateNetwork": False,
            "businessContext": {
                "description": "客户运营后台 ",
                "terminology": {" 客户池 ": " 未分配客户 "},
                "objectTypes": ["客户", "客户"],
                "stateModels": {"客户": ["待分配", "跟进中", "跟进中"]},
                "exampleGoals": ["把客户分配给销售"],
                "operatingBoundaries": ["只操作 QA 租户"],
                "allowedActions": [" 查询客户 ", "查询客户", "分配客户"],
                "bridgeCapabilities": ["等待场景就绪", "读取选中对象"],
                "bridgeSemanticTargets": {" agent.primary ": " 主仿真 Agent "},
            },
            "onboardingLevel": "L0",
            "limits": {"maxSteps": 30, "timeoutSeconds": 300, "maxModelCalls": 10},
        },
    )
    assert response.status_code == 200
    project = response.json()
    assert project["baseUrl"] == "https://example.com/app"
    assert project["allowedHosts"] == ["example.com", "assets.example.net"]
    assert project["allowPrivateNetwork"] is False
    assert project["businessContext"]["description"] == "客户运营后台"
    assert project["businessContext"]["terminology"] == {"客户池": "未分配客户"}
    assert project["businessContext"]["objectTypes"] == ["客户"]
    assert project["businessContext"]["stateModels"] == {"客户": ["待分配", "跟进中"]}
    assert project["businessContext"]["allowedActions"] == ["查询客户", "分配客户"]
    assert project["businessContext"]["bridgeCapabilities"] == ["等待场景就绪", "读取选中对象"]
    assert project["businessContext"]["bridgeSemanticTargets"] == {"agent.primary": "主仿真 Agent"}
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
    environment_update = client.put(
        f"/api/projects/{project_id}/environments/{environment.json()['id']}",
        json={
            "name": "预发布 V2", "variables": {"TENANT": "qa", "TEST_BASE_URL": "https://example.com"},
            "secretRefs": {"PASSWORD": "QA_PASSWORD"}, "ignoreRules": ["**/analytics/**"],
            "viewport": {"width": 1280, "height": 800}, "deviceScaleFactor": 1.25,
            "appBridge": {"enabled": True, "adapter": "cesium"}, "artifactRetentionDays": 14,
            "screenshotMaskSelectors": [".customer-name", "[data-private=true]"],
        },
    )
    assert environment_update.status_code == 200, environment_update.text
    assert environment_update.json()["viewport"] == {"width": 1280, "height": 800}
    assert environment_update.json()["screenshotMaskSelectors"] == [
        ".customer-name", "[data-private=true]"
    ]

    scenario = client.post(
        f"/api/projects/{project_id}/scenarios",
        json={
            "name": "搜索商品",
            "preconditions": ["已登录测试环境"],
            "goal": "搜索商品 A 并确认价格",
            "testData": {"keyword": "A"},
            "expectedResults": ["显示商品 A"],
            "forbiddenActions": ["支付"],
        },
    )
    assert scenario.status_code == 200, scenario.text
    assert client.get(f"/api/projects/{project_id}/scenarios").json()[0]["goal"].startswith("搜索")
    scenario_update = client.put(
        f"/api/projects/{project_id}/scenarios/{scenario.json()['id']}",
        json={
            "name": "搜索商品",
            "preconditions": ["已登录测试环境"],
            "goal": "搜索商品 A 并确认价格与库存",
            "testData": {"keyword": "A"},
            "expectedResults": ["显示商品 A", "价格与库存可见"],
            "forbiddenActions": ["支付"],
        },
    )
    assert scenario_update.status_code == 200, scenario_update.text
    assert scenario_update.json()["goal"].endswith("库存")

    updated = client.put(f"/api/projects/{project_id}", json={
        "onboardingLevel": "L3", "name": "企业闭环 V2", "allowPrivateNetwork": True,
        "businessContext": {"terminology": {"任务池": "待处理任务集合"}},
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["onboardingLevel"] == "L3"
    assert updated.json()["allowPrivateNetwork"] is True
    assert updated.json()["businessContext"]["terminology"] == {"任务池": "待处理任务集合"}
    audit = client.get(f"/api/projects/{project_id}/audit").json()
    assert [item["action"] for item in audit] == ["create", "create", "update", "create", "update", "update"]
    assert audit[-2]["objectType"] == "scenario"
    assert set(audit[-2]["changedFields"]) == {"goal", "expectedResults"}
    assert audit[2]["objectType"] == "environment"
    assert set(audit[2]["changedFields"]) == {"name", "variables", "viewport", "deviceScaleFactor", "artifactRetentionDays", "screenshotMaskSelectors"}
    assert "allowPrivateNetwork" in audit[-1]["changedFields"]
    assert "businessContext" in audit[-1]["changedFields"]
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
    sensitive = client.post(
        f"/api/projects/{project['id']}/environments",
        json={"name": "测试", "variables": {"API_TOKEN": "plain-secret"}},
    )
    assert sensitive.status_code == 422
    assert "secretRefs" in sensitive.json()["detail"]


def test_scenario_requires_complete_context_and_secret_references(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post("/api/projects", json={"name": "场景校验", "baseUrl": "https://example.com"}).json()
    endpoint = f"/api/projects/{project['id']}/scenarios"

    missing = client.post(endpoint, json={"name": "不完整", "goal": "登录", "expectedResults": []})
    assert missing.status_code == 422
    assert "preconditions" in str(missing.json()) or "前置条件" in str(missing.json())

    secret = client.post(endpoint, json={
        "name": "登录", "preconditions": ["测试账号可用"], "goal": "登录后台",
        "testData": {"password": "plain-secret"}, "expectedResults": ["进入控制台"],
    })
    assert secret.status_code == 422
    assert "<secret:ENV_NAME>" in str(secret.json())

    accepted = client.post(endpoint, json={
        "name": "登录", "preconditions": ["测试账号可用"], "goal": "登录后台",
        "testData": {"password": "<secret:TEST_PASSWORD>"}, "expectedResults": ["进入控制台"],
    })
    assert accepted.status_code == 200, accepted.text


def test_fixed_run_uses_persisted_scenario_and_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post(
        "/api/projects",
        json={"name": "运行场景", "baseUrl": "https://example.com", "forbiddenActions": ["删除数据"]},
    ).json()
    environment = client.post(
        f"/api/projects/{project['id']}/environments",
        json={
            "name": "QA", "variables": {"TEST_BASE_URL": "https://example.com", "TENANT": "qa"},
            "secretRefs": {"PASSWORD": "QA_PASSWORD"}, "ignoreRules": ["**/analytics/**"],
            "viewport": {"width": 1280, "height": 800}, "deviceScaleFactor": 1.25,
            "appBridge": {"enabled": True, "globalName": "CUSTOM_TEST_BRIDGE", "adapter": "cesium"},
            "artifactRetentionDays": 14,
        },
    ).json()
    scenario = client.post(
        f"/api/projects/{project['id']}/scenarios",
        json={
            "name": "商品检索", "preconditions": ["已登录"], "goal": "查找商品 A",
            "testData": {"keyword": "A"}, "expectedResults": ["商品 A 可见"], "forbiddenActions": ["支付"],
        },
    ).json()

    class FakeOrchestrator:
        def start(self, saved_plan, config):
            assert config.scenario_goal == "查找商品 A"
            assert config.forbidden_actions == ("删除数据", "支付")
            assert config.scenario_id == scenario["id"]
            assert config.scenario_updated_at == scenario["updatedAt"]
            assert config.project_id == project["id"]
            assert config.environment_id == environment["id"]
            assert config.environment_updated_at == environment["updatedAt"]
            assert dict(config.environment_variables)["TEST_BASE_URL"] == "https://example.com"
            assert dict(config.secret_refs) == {"PASSWORD": "QA_PASSWORD"}
            assert config.ignore_rules == ("**/analytics/**",)
            assert config.viewport == (1280, 800) and config.device_scale_factor == 1.25
            assert config.app_bridge_enabled is True
            assert config.app_bridge_global_name == "CUSTOM_TEST_BRIDGE"
            assert config.app_bridge_adapter == "cesium"
            assert config.artifact_retention_days == 14
            now = "2026-07-20T00:00:00+08:00"
            return {
                "run_id": "scenario-run", "plan_name": saved_plan.name, "scenario_id": config.scenario_id,
                "scenario_updated_at": config.scenario_updated_at, "scenario_goal": config.scenario_goal,
                "project_id": config.project_id, "environment_id": config.environment_id,
                "environment_updated_at": config.environment_updated_at, "artifact_retention_days": config.artifact_retention_days,
                "role": None, "base_url_summary": saved_plan.base_url, "status": "queued",
                "started_at": now, "ended_at": now, "steps": [], "assertions": [],
                "reproduction_steps": [], "cause_hints": [], "findings": [], "completion_reason": "queued",
            }

    monkeypatch.setattr(server, "RUN_ORCHESTRATOR", FakeOrchestrator())
    response = client.post("/api/runs", json={
        "plan": {"name": "商品检索", "base_url": "${TEST_BASE_URL}", "steps": [{"action": "navigate", "target": "/"}], "assertions": []},
        "projectId": project["id"], "environmentId": environment["id"], "scenarioId": scenario["id"], "asyncExecution": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["scenario_id"] == scenario["id"]
    assert response.json()["environment_id"] == environment["id"]
    assert response.json()["scenario_goal"] == "查找商品 A"


def test_environment_reports_missing_runtime_secret_without_exposing_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    monkeypatch.delenv("FR01_MISSING_PASSWORD", raising=False)
    project = client.post("/api/projects", json={"name": "密钥检查", "baseUrl": "https://example.com"}).json()
    environment = client.post(
        f"/api/projects/{project['id']}/environments",
        json={"name": "QA", "secretRefs": {"LOGIN_PASSWORD": "FR01_MISSING_PASSWORD"}},
    ).json()
    response = client.post("/api/runs", json={
        "plan": {
            "name": "登录", "base_url": "https://example.com",
            "steps": [{"action": "fill", "locator": {"label": "密码"}, "value_from_secret": "LOGIN_PASSWORD"}],
            "assertions": [],
        },
        "projectId": project["id"], "environmentId": environment["id"], "asyncExecution": True,
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "测试环境缺少运行时密钥：LOGIN_PASSWORD -> FR01_MISSING_PASSWORD"


def test_plan_generation_resolves_target_from_selected_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post(
        "/api/projects", json={"name": "环境规划", "baseUrl": "https://example.com"}
    ).json()
    environment = client.post(
        f"/api/projects/{project['id']}/environments",
        json={"name": "QA", "variables": {"TEST_BASE_URL": "https://example.com"}},
    ).json()

    response = client.post(
        "/api/plans/generate",
        json={
            "name": "环境地址规划",
            "targetUrl": "${TEST_BASE_URL}",
            "flow": "确认看到“Example Domain”",
            "preconditions": "测试环境已启动",
            "expectation": "确认看到“Example Domain”",
            "projectId": project["id"],
            "environmentId": environment["id"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["plan"]["base_url"] == "https://example.com"


def test_compatibility_scan_creates_an_editable_sample_scenario(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post(
        "/api/projects", json={"name": "历史应用", "baseUrl": "https://example.com", "forbiddenActions": ["支付"]}
    ).json()

    def fake_scan(saved_project, **_kwargs):
        return CompatibilityReport(
            projectId=saved_project.id, onboardingLevel="L0", recommendedOnboardingLevel="L2",
            requestedUrl=saved_project.base_url, finalUrl=saved_project.base_url, title="历史应用",
            status="attention", pageSummary={"buttons": 2}, candidateLocators={"namedControls": 2},
            capabilities=["标准 DOM"], thirdPartyHosts=[], consoleErrors=[], failedRequests=[],
            blockedAreas=[], recommendations=["补充 aria-label"],
            suggestedScenarios=["验证主要导航入口“客户管理”可见且可访问"],
            scannedPages=[{"url": saved_project.base_url, "title": "历史应用", "pageType": "导航/工作台"}],
            stableAreas=["2 个具名控件可稳定定位"], adaptiveAreas=["1 个控件需要可访问名称"],
            recommendedConfig={"allowedHosts": saved_project.allowed_hosts, "ignoreRules": [], "viewport": {"width": 1440, "height": 960}, "limits": saved_project.limits.model_dump(mode="json", by_alias=True)},
        )

    monkeypatch.setattr(server, "scan_project", fake_scan)
    response = client.post(f"/api/projects/{project['id']}/scan", json={"headless": True, "timeoutMs": 30000})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["sampleScenarioCreated"] is True
    assert payload["sampleScenarioId"].startswith("scenario-")
    scenarios = client.get(f"/api/projects/{project['id']}/scenarios").json()
    assert len(scenarios) == 1
    assert scenarios[0]["goal"] == "确认看到“历史应用”"
    assert scenarios[0]["expectedResults"] == ["确认看到“历史应用”"]
    assert "支付" in scenarios[0]["forbiddenActions"]
    audit = client.get(f"/api/projects/{project['id']}/audit").json()
    assert [item["action"] for item in audit[-2:]] == ["create", "scan"]


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
    payload = response.json()
    assert payload["deleted"] == ["run-one", "run-two"]
    assert payload["count"] == 2
    assert payload["auditId"].startswith("deletion-")
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


def test_cleanup_endpoint_protects_active_runs_and_exposes_deletion_audit(tmp_path: Path, monkeypatch) -> None:
    artifacts = (tmp_path / "artifacts").resolve()
    monkeypatch.setattr(server, "ARTIFACTS_ROOT", artifacts)
    ended_at = "2026-01-01T00:00:00+00:00"
    for run_id, status in (("expired", "passed"), ("active", "pending_confirmation")):
        run_dir = artifacts / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "run_id": run_id,
            "status": status,
            "ended_at": ended_at,
            "artifact_retention_days": 1,
        }), encoding="utf-8")

    response = client.post("/api/runs/cleanup", json={"actor": "api-test"})

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == ["expired"]
    assert response.json()["skippedActive"] == ["active"]
    assert not (artifacts / "expired").exists()
    assert (artifacts / "active" / "run.json").is_file()

    audit = client.get("/api/runs/deletion-audit")
    assert audit.status_code == 200
    assert audit.headers["content-disposition"] == 'attachment; filename="run-deletion-audit.json"'
    records = audit.json()
    assert records[0]["actor"] == "api-test"
    assert records[0]["action"] == "automatic_retention_cleanup"


def test_agent_run_uses_project_limits_without_echoing_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    project = client.post(
        "/api/projects",
        json={
            "name": "Agent 项目",
            "baseUrl": "https://example.com",
            "forbiddenActions": ["发布内容"],
            "businessContext": {
                "terminology": {"发布单": "待发布的内容对象"},
                "operatingBoundaries": ["信息不足时先澄清"],
            },
            "limits": {"maxSteps": 7, "timeoutSeconds": 90, "maxModelCalls": 4},
        },
    ).json()

    class FakeOrchestrator:
        def start(self, saved_plan, config):
            assert config.max_steps == 7
            assert config.max_model_calls == 4
            assert config.max_duration_seconds == 90
            assert config.forbidden_actions == ("发布内容", "支付")
            assert config.agent_planner.settings.api_key.get_secret_value() == "private-agent-key"
            assert config.agent_planner.scenario.business_context["terminology"] == {"发布单": "待发布的内容对象"}
            assert config.agent_planner.scenario.business_context["operatingBoundaries"] == ["信息不足时先澄清"]
            now = "2026-07-20T00:00:00+08:00"
            return {
                "run_id": "agent-run-test", "plan_name": saved_plan.name, "role": None,
                "base_url_summary": saved_plan.base_url, "status": "queued",
                "started_at": now, "ended_at": now, "steps": [], "assertions": [],
                "reproduction_steps": [], "cause_hints": [], "findings": [],
                "completion_reason": "queued", "model_calls": 0,
            }

    monkeypatch.setattr(server, "RUN_ORCHESTRATOR", FakeOrchestrator())
    response = client.post(
        "/api/agent-runs",
        json={
            "plan": {
                "name": "逐步探索", "base_url": "https://example.com",
                "steps": [{"action": "navigate", "target": "/"}], "assertions": [],
            },
            "scenario": {
                "name": "逐步探索", "goal": "打开首页", "expectedResults": ["首页可见"],
                "forbiddenActions": ["支付"],
            },
            "settings": {
                "protocol": "responses", "baseUrl": "https://api.openai.com/v1",
                "model": "test-model", "apiKey": "private-agent-key",
            },
            "projectId": project["id"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    assert "private-agent-key" not in response.text


def _write_reviewable_run(root: Path, run_id: str = "review-run") -> Path:
    run_dir = root / run_id
    run_dir.mkdir()
    plan = {
        "name": "审核回归路径",
        "base_url": "https://example.com",
        "steps": [
            {"action": "navigate", "target": "/", "description": "打开首页"},
            {"action": "click", "locator": {"role": "button", "name": "登录"}, "description": "点击登录"},
        ],
        "assertions": [{"type": "url_contains", "expected": "/", "description": "保持在站点内"}],
    }
    run = {
        "run_id": run_id,
        "plan_name": plan["name"],
        "base_url_summary": plan["base_url"],
        "status": "passed",
        "started_at": "2026-07-20T00:00:00+00:00",
        "ended_at": "2026-07-20T00:00:01+00:00",
        "steps": [], "assertions": [], "reproduction_steps": [], "cause_hints": [],
        "findings": [{
            "id": "finding-1", "title": "原问题", "category": "expectation_failed",
            "severity": "Medium", "confidence": "medium", "actual_result": "实际",
            "expected_result": "原预期", "facts": ["事实"], "inference": "推断",
            "evidence": [], "reproduction_steps": [], "review_status": "pending_review",
            "review_history": [],
        }],
    }
    (run_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (run_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    (run_dir / "report.html").write_text("<html><body>execution evidence</body></html>", encoding="utf-8")
    return run_dir


def test_path_review_preserves_original_and_recompiles_selected_steps(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "ARTIFACTS_ROOT", tmp_path)
    run_dir = _write_reviewable_run(tmp_path)
    state = client.get("/api/runs/review-run/review").json()
    state["steps"][0]["step"]["description"] = "打开审核后的首页"
    state["steps"][1]["retained"] = False

    response = client.patch("/api/runs/review-run/review", json={"steps": state["steps"]})

    assert response.status_code == 200, response.text
    reviewed = json.loads((run_dir / "reviewed-plan.json").read_text(encoding="utf-8"))
    original = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    assert len(reviewed["steps"]) == 1
    assert reviewed["steps"][0]["description"] == "打开审核后的首页"
    assert len(original["steps"]) == 2 and original["steps"][1]["description"] == "点击登录"
    generated = (run_dir / "generated-test.spec.ts").read_text(encoding="utf-8")
    assert "打开审核后的首页" in generated
    assert "点击登录" not in generated
    history = response.json()["history"]
    assert {item["action"] for item in history[0]["changes"]} == {"edited", "removed"}
    assert history[0]["changes"][0].get("before") or history[0]["changes"][1].get("before")
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["generated_test"]["source"] == generated
    assert run["path_review_history"] == history


def test_review_records_finding_values_and_rejects_plaintext_sensitive_step(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "ARTIFACTS_ROOT", tmp_path)
    _write_reviewable_run(tmp_path)
    finding = client.patch(
        "/api/runs/review-run/findings/finding-1",
        json={"status": "confirmed", "title": "登录结果错误", "severity": "High", "expectedResult": "进入控制台"},
    )
    assert finding.status_code == 200, finding.text
    payload = finding.json()
    assert payload["title"] == "登录结果错误" and payload["severity"] == "High"
    changes = payload["review_history"][0]["changes"]
    assert changes["title"] == {"before": "原问题", "after": "登录结果错误"}
    assert changes["review_status"] == {"before": "pending_review", "after": "confirmed"}

    state = client.get("/api/runs/review-run/review").json()
    state["steps"][1]["step"] = {
        "action": "fill", "locator": {"label": "密码"}, "value": "plain-secret", "description": "输入密码",
    }
    rejected = client.patch("/api/runs/review-run/review", json={"steps": state["steps"]})
    assert rejected.status_code == 422
    assert "value_from_secret" in rejected.json()["detail"]
    assert not (tmp_path / "review-run" / "reviewed-plan.json").exists()


def test_generated_source_edit_is_versioned_downloadable_and_secret_checked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "ARTIFACTS_ROOT", tmp_path)
    run_dir = _write_reviewable_run(tmp_path)
    review = client.get("/api/runs/review-run/review").json()
    compiled = client.patch("/api/runs/review-run/review", json={"steps": review["steps"]})
    assert compiled.status_code == 200, compiled.text
    original_source = (run_dir / "generated-test.spec.ts").read_text(encoding="utf-8")
    edited_source = original_source.replace("打开首页", "打开已审核首页")

    saved = client.patch("/api/runs/review-run/generated-test", json={"source": edited_source})

    assert saved.status_code == 200, saved.text
    assert saved.json()["source_revision"] == 2
    history = saved.json()["source_review_history"]
    assert history[-1]["action"] == "manual_source_edit"
    assert history[-1]["beforeSha256"] != history[-1]["afterSha256"]
    assert (run_dir / "generated-test.spec.ts").read_text(encoding="utf-8") == edited_source
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["generated_test"]["source"] == edited_source
    download = client.get("/api/runs/review-run/generated-test")
    assert download.status_code == 200 and download.content.decode("utf-8") == edited_source

    unsafe = edited_source.replace(
        "test(\"审核回归路径\"",
        "const password = \"plain-secret\";\ntest(\"审核回归路径\"",
    )
    rejected = client.patch("/api/runs/review-run/generated-test", json={"source": unsafe})
    assert rejected.status_code == 422
    assert "process.env" in rejected.json()["detail"]
    assert (run_dir / "generated-test.spec.ts").read_text(encoding="utf-8") == edited_source


def test_run_payload_enriches_goal_review_state_and_downloads_current_reports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "ARTIFACTS_ROOT", tmp_path)
    _write_reviewable_run(tmp_path)

    response = client.get("/api/runs/review-run")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scenario_goal"] == "审核回归路径"
    assert payload["goal_status"] == "achieved"
    assert payload["review_summary"] == {
        "disposition": "pending_confirmation", "pending": 1, "confirmed": 0, "rejected": 0, "total": 1,
    }
    assert payload["duration_ms"] == 1000
    json_report = client.get("/api/runs/review-run/report.json")
    html_report = client.get("/api/runs/review-run/report.html")
    downloaded_payload = json.loads(json_report.content)
    assert json_report.status_code == 200 and downloaded_payload["run_id"] == "review-run"
    assert downloaded_payload["scenario_goal"] == "审核回归路径"
    assert downloaded_payload["goal_status"] == "achieved"
    assert downloaded_payload["review_summary"] == payload["review_summary"]
    assert downloaded_payload["duration_ms"] == 1000
    assert html_report.status_code == 200 and b"execution evidence" in html_report.content
    assert "review-run-report.json" in json_report.headers["content-disposition"]
    assert "review-run-evidence.html" in html_report.headers["content-disposition"]
