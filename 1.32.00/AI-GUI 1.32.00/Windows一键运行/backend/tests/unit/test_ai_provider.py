import json

import pytest
from pydantic import SecretStr

from gui_agent.planning.ai_provider import AIProviderError, AISettings, plan_with_ai, probe_capabilities, test_connection as check_connection


def test_http_model_endpoint_allows_docker_host_alias_but_rejects_remote_host() -> None:
    local = AISettings(
        protocol="responses",
        base_url="http://host.docker.internal:11434/v1",
        model="local-model",
        api_key=SecretStr("request-key"),
    )
    assert local.validated() is local

    with pytest.raises(AIProviderError, match="必须使用 HTTPS"):
        AISettings(
            protocol="responses",
            base_url="http://models.example.com/v1",
            model="remote-model",
            api_key=SecretStr("request-key"),
        ).validated()


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeClient:
    response_payload: dict = {}
    last_headers: dict = {}
    last_json: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.last_headers = headers
        self.__class__.last_json = json
        return FakeResponse(self.__class__.response_payload)


class ProbeClient(FakeClient):
    responses: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.last_headers = headers
        self.__class__.last_json = json
        return FakeResponse(self.__class__.responses.pop(0))


def settings() -> AISettings:
    return AISettings(
        protocol="responses",
        base_url="https://api.openai.com/v1",
        model="test-model",
        api_key=SecretStr("secret-test-key"),
    )


def test_connection_uses_bearer_key_without_returning_it(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", FakeClient)
    FakeClient.response_payload = {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]
    }
    result = check_connection(settings())
    assert result["connected"] is True
    assert result["model"] == "test-model"
    assert "secret-test-key" not in json.dumps(result)
    assert FakeClient.last_headers["Authorization"] == "Bearer secret-test-key"


def test_capability_probe_requires_schema_and_multi_turn_without_claiming_vision(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", ProbeClient)
    ProbeClient.responses = [
        {"output_text": "OK"},
        {"output_text": '{"echo":"schema-ok"}'},
        {"output_text": "GUI_MULTI_TURN_7319"},
        {"output_text": "RED"},
    ]
    result = probe_capabilities(settings())
    assert result["verifiedModelId"] == "test-model"
    assert result["capabilities"] == {
        "schema": "passed",
        "multiTurn": "passed",
        "vision": "passed",
    }
    assert "secret-test-key" not in json.dumps(result)


def test_ai_plan_is_schema_validated_and_user_target_is_preserved(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", FakeClient)
    model_plan = {
        "name": "模型擅自改名",
        "base_url": "https://wrong.example",
        "role": None,
        "preconditions": [],
        "steps": [
            {"action": "navigate", "target": "/", "locator": None, "value": None, "value_from_secret": None, "description": "打开网站"},
            {"action": "click", "target": None, "locator": {"role": "button", "name": "登录", "label": None, "test_id": None, "css": None, "text": None}, "value": None, "value_from_secret": None, "description": "点击登录"},
        ],
        "assertions": [
            {"type": "visible", "locator": {"role": None, "name": None, "label": None, "test_id": None, "css": None, "text": "控制台"}, "expected": None, "count": None, "description": "控制台可见"}
        ],
    }
    FakeClient.response_payload = {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(model_plan, ensure_ascii=False)}]}]
    }
    result = plan_with_ai(
        settings=settings(),
        name="登录验收",
        target_url="https://example.com",
        flow="点击登录并确认控制台可见",
        role="测试员",
        preconditions="测试环境已启动",
        expectation="控制台可见",
        business_context={
            "terminology": {"客户池": "未分配客户"},
            "objectTypes": ["客户", "销售人员"],
            "operatingBoundaries": ["只操作 QA 租户"],
            "allowedActions": ["查询客户"],
            "bridgeCapabilities": ["读取选中对象"],
            "bridgeSemanticTargets": {"customer.primary": "主客户对象"},
        },
    )
    assert result.plan.name == "登录验收"
    assert result.plan.base_url == "https://example.com"
    assert result.plan.assertions[0].locator.text == "控制台"
    assert FakeClient.last_json["text"]["format"]["strict"] is True
    assert "客户池" in FakeClient.last_json["input"]
    assert "只操作 QA 租户" in FakeClient.last_json["input"]
    assert "项目业务上下文属于用户审核的可信配置" in FakeClient.last_json["input"]
    assert "查询客户" in FakeClient.last_json["input"]
    assert "customer.primary" in FakeClient.last_json["input"]
    assert "只能生成其中明确允许" in FakeClient.last_json["input"]
