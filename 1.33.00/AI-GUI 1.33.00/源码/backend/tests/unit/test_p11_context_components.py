from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gui_agent.api import server
from gui_agent.domain.models import ComponentAction, Step
from gui_agent.onboarding.models import BusinessContext, ComponentAdapter, ProjectConfig
from gui_agent.onboarding.store import ProjectStore
from gui_agent.execution.component_policy import validate_component_step
from gui_agent.security.policy import SecurityError


def test_confirmed_context_requires_source_and_blocked_adapter_requires_reason() -> None:
    with pytest.raises(ValidationError, match="必须填写来源"):
        BusinessContext(facts=[{"id": "fact.one", "category": "object", "statement": "对象定义", "status": "confirmed"}])
    with pytest.raises(ValidationError, match="缺失原因"):
        ComponentAdapter(
            id="run.tab", module="run", page="运行", status="blocked",
            action={"kind": "tab", "semanticTarget": "运行页签", "locators": [{"role": "tab", "name": "运行"}]},
        )


@pytest.mark.parametrize("payload", [
    {"kind": "cascade_select", "semanticTarget": "级联", "locators": [{"label": "省"}], "values": []},
    {"kind": "searchable_select", "semanticTarget": "搜索", "locators": [{"text": "开"}], "values": ["A"]},
    {"kind": "date_time_range", "semanticTarget": "日期", "locators": [{"label": "开始"}], "values": ["1"]},
    {"kind": "upload_dialog", "semanticTarget": "上传", "locators": [{"text": "开"}, {"label": "文件"}]},
])
def test_component_contract_rejects_incomplete_semantics(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ComponentAction.model_validate(payload)


def test_project_api_persists_p03_and_p11_packages(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(server, "PROJECT_STORE", ProjectStore(tmp_path / "projects"))
    client = TestClient(server.app)
    payload = {
        "name": "GAE", "baseUrl": "https://example.com",
        "businessContext": {
            "facts": [{"id": "fact.agent", "category": "object", "statement": "装备智能体对象", "source": "企业文档 v1", "status": "confirmed"}],
            "objectRelations": [{"sourceObject": "装备智能体", "relation": "产生", "targetObject": "实例", "source": "待确认", "status": "blocked"}],
            "missingFacts": ["运行状态协议"], "sourceRevision": "v1",
        },
        "asyncStateMachines": [{"id": "job", "name": "任务", "states": ["done"], "terminalStates": ["done"]}],
        "sideEffectPolicies": [{"id": "delete", "actionCategory": "delete", "objectType": "job", "decision": "confirm"}],
        "componentAdapters": [{
            "id": "run.tab", "module": "run", "page": "运行", "status": "blocked", "blockedReason": "待授权页面",
            "action": {"kind": "tab", "semanticTarget": "运行页签", "locators": [{"role": "tab", "name": "运行"}]},
        }],
    }
    created = client.post("/api/projects", json=payload)
    assert created.status_code == 200, created.text
    project = created.json()
    assert project["asyncStateMachines"][0]["id"] == "job"
    assert project["sideEffectPolicies"][0]["id"] == "delete"
    assert project["componentAdapters"][0]["module"] == "run"
    status = client.get(f"/api/projects/{project['id']}/business-context-status").json()
    assert status["status"] == "blocked"
    assert status["confirmedCount"] == 1
    assert "运行状态协议" in status["blockedItems"]
    assert any("装备智能体 产生 实例" in item for item in status["blockedItems"])

    scenario = client.post(f"/api/projects/{project['id']}/scenarios", json={
        "name": "生命周期", "preconditions": ["无"], "goal": "测试", "expectedResults": ["完成"],
        "businessObjects": [{
            "key": "job", "objectType": "job", "name": "E2E_job", "dependencies": [],
            "cleanupStep": {"action": "click", "locator": {"text": "remove"}, "action_category": "delete", "object_type": "job", "business_object_name": "E2E_job", "cleanup_required": True},
        }],
    })
    assert scenario.status_code == 200, scenario.text
    assert scenario.json()["businessObjects"][0]["key"] == "job"


def test_component_step_and_project_adapter_are_target_independent() -> None:
    step = Step.model_validate({
        "action": "component", "component_adapter_id": "agent.search",
        "component": {"kind": "searchable_select", "semanticTarget": "装备智能体", "locators": [{"role": "combobox", "name": "智能体"}, {"placeholder": "搜索"}, {"role": "option", "name": "E2E_Agent"}], "values": ["E2E_Agent"]},
    })
    assert step.component.kind == "searchable_select"
    serialized = json.dumps(step.model_dump(mode="json"), ensure_ascii=False)
    assert "Cesium" not in serialized
    with pytest.raises(SecurityError, match="blocked"):
        validate_component_step(step, ({"id": "agent.search", "status": "blocked", "action": step.component.model_dump(mode="json", by_alias=True, exclude_none=True)},), require_adapter=True)
    configured = {"id": "agent.search", "status": "configured", "action": step.component.model_dump(mode="json", by_alias=True, exclude_none=True)}
    validate_component_step(step, (configured,), require_adapter=True)
