import base64
import json
import struct
import zlib

import httpx
import pytest
from pydantic import SecretStr

from gui_agent.planning.ai_provider import AIProviderError, AISettings, _post, _VISION_PROBE_IMAGE, plan_with_ai, probe_capabilities, test_connection as check_connection


def test_embedded_vision_probe_is_a_valid_nontrivial_png() -> None:
    raw = base64.b64decode(_VISION_PROBE_IMAGE.partition(",")[2], validate=True)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", raw[16:24])
    assert width >= 8 and height >= 8
    offset = 8
    while offset < len(raw):
        length = struct.unpack(">I", raw[offset:offset + 4])[0]
        kind = raw[offset + 4:offset + 8]
        data = raw[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", raw[offset + 8 + length:offset + 12 + length])[0]
        assert zlib.crc32(kind + data) & 0xFFFFFFFF == expected_crc
        offset += 12 + length
    assert offset == len(raw)


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
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class FakeClient:
    response_payload: dict = {}
    last_headers: dict = {}
    last_json: dict = {}
    last_url: str = ""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.last_url = url
        self.__class__.last_headers = headers
        self.__class__.last_json = json
        return FakeResponse(self.__class__.response_payload)


class ProbeClient(FakeClient):
    responses: list[dict] = []
    json_payloads: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.last_headers = headers
        self.__class__.last_json = json
        self.__class__.json_payloads.append(json)
        return FakeResponse(self.__class__.responses.pop(0))


class TransientFailureClient(FakeClient):
    attempts = 0

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.attempts += 1
        if self.__class__.attempts < 3:
            return FakeResponse({}, status_code=502)
        return FakeResponse({"output_text": "OK"})


class TransientNetworkClient(FakeClient):
    attempts = 0

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.attempts += 1
        if self.__class__.attempts < 3:
            raise httpx.ConnectError("temporary disconnect")
        return FakeResponse({"output_text": "OK"})


class TransientTimeoutClient(FakeClient):
    attempts = 0

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.attempts += 1
        if self.__class__.attempts < 3:
            raise httpx.ReadTimeout("temporary timeout")
        return FakeResponse({"output_text": "OK"})


class PermanentFailureClient(FakeClient):
    attempts = 0

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.attempts += 1
        return FakeResponse({}, status_code=401)


class ExhaustedGatewayClient(FakeClient):
    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        return FakeResponse({}, status_code=503)


class StructuredGatewayFallbackClient(FakeClient):
    json_payloads: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.json_payloads.append(json)
        if "text" in json:
            return FakeResponse({}, status_code=502)
        return FakeResponse({"output_text": '{"echo":"schema-ok"}'})


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


def test_provider_root_is_automatically_expanded_to_v1(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", FakeClient)
    FakeClient.response_payload = {"output_text": "OK"}
    root_settings = AISettings(
        protocol="responses", base_url="https://models.example.com",
        model="test-model", api_key=SecretStr("request-key"),
    )

    check_connection(root_settings)

    assert FakeClient.last_url == "https://models.example.com/v1/responses"


def test_chat_schema_request_contains_json_requirement_and_actual_schema(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", ProbeClient)
    ProbeClient.responses = [
        {"choices": [{"message": {"content": "OK"}}]},
        {"choices": [{"message": {"content": '{"echo":"schema-ok"}'}}]},
        {"choices": [{"message": {"content": '{"kind":"complete","action":null,"visual_request":null,"question":null,"reason":"done","progress_assessment":"progress"}'}}]},
        {"choices": [{"message": {"content": "GUI_MULTI_TURN_7319"}}]},
        {"choices": [{"message": {"content": "RED"}}]},
    ]
    ProbeClient.json_payloads = []
    chat_settings = AISettings(
        protocol="chat_completions", base_url="https://models.example.com",
        model="test-model", api_key=SecretStr("request-key"),
    )

    probe_capabilities(chat_settings)

    schema_prompt = ProbeClient.json_payloads[1]["messages"][-1]["content"]
    assert "JSON Schema" in schema_prompt
    assert '"echo"' in schema_prompt
    assert "request-key" not in json.dumps(ProbeClient.json_payloads)


def test_official_kimi_k26_disables_thinking_for_chat_requests(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", FakeClient)
    FakeClient.response_payload = {"choices": [{"message": {"content": "OK"}}]}
    kimi_settings = AISettings(
        protocol="chat_completions",
        base_url="https://api.moonshot.cn/v1",
        model="kimi-k2.6",
        api_key=SecretStr("request-key"),
    )

    result = check_connection(kimi_settings)

    assert result["connected"] is True
    assert FakeClient.last_json["thinking"] == {"type": "disabled"}
    assert FakeClient.last_json["max_tokens"] == 16


def test_transient_gateway_errors_are_retried(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", TransientFailureClient)
    monkeypatch.setattr("gui_agent.planning.ai_provider.time.sleep", lambda _seconds: None)
    TransientFailureClient.attempts = 0

    result = check_connection(settings())

    assert result["connected"] is True
    assert TransientFailureClient.attempts == 3


def test_transient_network_errors_are_retried(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", TransientNetworkClient)
    monkeypatch.setattr("gui_agent.planning.ai_provider.time.sleep", lambda _seconds: None)
    TransientNetworkClient.attempts = 0

    result = check_connection(settings())

    assert result["connected"] is True
    assert TransientNetworkClient.attempts == 3


def test_probe_stage_can_request_bounded_transient_retries(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", TransientNetworkClient)
    monkeypatch.setattr("gui_agent.planning.ai_provider.time.sleep", lambda _seconds: None)
    TransientNetworkClient.attempts = 0

    result = _post(settings(), "probe", schema=None, transient_retries=2)

    assert result["output_text"] == "OK"
    assert TransientNetworkClient.attempts == 3


def test_transient_timeouts_are_retried(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", TransientTimeoutClient)
    monkeypatch.setattr("gui_agent.planning.ai_provider.time.sleep", lambda _seconds: None)
    TransientTimeoutClient.attempts = 0

    result = check_connection(settings())

    assert result["connected"] is True
    assert TransientTimeoutClient.attempts == 3


def test_permanent_provider_error_is_marked_non_retryable(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", PermanentFailureClient)
    PermanentFailureClient.attempts = 0

    with pytest.raises(AIProviderError) as captured:
        check_connection(settings())

    assert captured.value.retryable is False
    assert PermanentFailureClient.attempts == 1


def test_exhausted_gateway_error_is_marked_retryable(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", ExhaustedGatewayClient)
    monkeypatch.setattr("gui_agent.planning.ai_provider.time.sleep", lambda _seconds: None)

    with pytest.raises(AIProviderError) as captured:
        check_connection(settings())

    assert captured.value.retryable is True


def test_responses_schema_gateway_error_falls_back_to_prompt_and_local_validation(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", StructuredGatewayFallbackClient)
    StructuredGatewayFallbackClient.json_payloads = []
    schema = {
        "type": "object",
        "properties": {"echo": {"type": "string"}},
        "required": ["echo"],
        "additionalProperties": False,
    }

    result = _post(settings(), "Return the probe result.", schema=schema)

    assert result["output_text"] == '{"echo":"schema-ok"}'
    assert result["_guiStructuredOutputMode"] == "prompt_schema_local_validation"
    assert len(StructuredGatewayFallbackClient.json_payloads) == 2
    native, fallback = StructuredGatewayFallbackClient.json_payloads
    assert native["text"]["format"]["strict"] is True
    assert "text" not in fallback
    assert "JSON Schema" in fallback["input"]
    assert '"echo"' in fallback["input"]
    assert "secret-test-key" not in json.dumps(StructuredGatewayFallbackClient.json_payloads)


def test_capability_probe_requires_schema_and_multi_turn_without_claiming_vision(monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.ai_provider.httpx.Client", ProbeClient)
    ProbeClient.responses = [
        {"output_text": "OK"},
        {"output_text": '{"echo":"schema-ok"}'},
        {"output_text": '{"kind":"complete","action":null,"visual_request":null,"question":null,"reason":"probe complete","progress_assessment":"progress"}'},
        {"output_text": "GUI_MULTI_TURN_7319"},
        {"output_text": "RED"},
    ]
    result = probe_capabilities(settings())
    assert result["verifiedModelId"] == "test-model"
    assert result["capabilities"] == {
        "schema": "passed",
        "agentDecision": "passed",
        "multiTurn": "passed",
        "vision": "passed",
    }
    assert result["visionDetail"] == "合成红色测试图片识别通过；未发送任何网站截图"
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
