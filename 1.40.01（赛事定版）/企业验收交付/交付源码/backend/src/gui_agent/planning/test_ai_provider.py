import httpx
import pytest
from pydantic import SecretStr

from gui_agent.planning import ai_provider
from gui_agent.planning.ai_provider import AIProviderError, AIProviderUnavailableError, AISettings


def _settings() -> AISettings:
    return AISettings(
        protocol="chat_completions",
        base_url="https://model.example.test/v1",
        model="test-model",
        api_key=SecretStr("test-key"),
    )


def test_post_retries_a_transient_connection_failure(monkeypatch) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **_kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise httpx.ConnectError("temporary", request=httpx.Request("POST", url))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OK"}}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    result = ai_provider._post(_settings(), "hello", schema=None)

    assert result["choices"][0]["message"]["content"] == "OK"
    assert len(calls) == 2


def test_pathless_gateway_uses_the_openai_v1_root(monkeypatch) -> None:
    calls = []
    settings = AISettings(
        protocol="responses",
        base_url="https://gateway.example.test",
        model="test-model",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **_kwargs):
            calls.append(url)
            return httpx.Response(200, json={"output_text": "OK"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)

    ai_provider._post(settings, "hello", schema=None, connection_test=True)

    assert calls == ["https://gateway.example.test/v1/responses"]


def test_kimi_k3_connection_probe_reserves_reasoning_budget(monkeypatch) -> None:
    payloads = []
    settings = AISettings(
        protocol="chat_completions",
        base_url="https://api.moonshot.cn/v1",
        model="kimi-k3",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **kwargs):
            payloads.append(kwargs["json"])
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)

    result = ai_provider.test_connection(settings)

    assert result["connected"] is True
    assert payloads == [{
        "model": "kimi-k3",
        "messages": [
            {"role": "system", "content": "你是 GUI 自动化测试规划器。严格遵守输出约束，不编造执行结果。"},
            {"role": "user", "content": "只回复 OK。"},
        ],
        "max_tokens": 512,
        "reasoning_effort": "low",
    }]


def test_kimi_k3_plan_uses_bounded_compact_output(monkeypatch) -> None:
    payloads = []
    settings = AISettings(
        protocol="chat_completions",
        base_url="https://api.moonshot.cn/v1",
        model="kimi-k3",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **kwargs):
            payloads.append(kwargs["json"])
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)

    ai_provider._post(settings, "plan", schema={"type": "object"})

    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert payloads[0]["reasoning_effort"] == "low"
    assert payloads[0]["max_tokens"] == ai_provider._KIMI_K3_PLAN_MAX_TOKENS


def test_chat_plan_schema_keeps_optional_fields_optional(monkeypatch) -> None:
    captured = {}

    def fake_post(_settings, _prompt, *, schema, **_kwargs):
        captured["schema"] = schema
        return {"choices": [{"message": {"content": "{\"steps\":[{\"action\":\"navigate\",\"target\":\"/\"}],\"assertions\":[{\"type\":\"url_contains\",\"expected\":\"example.test\"}]}"}}]}

    monkeypatch.setattr(ai_provider, "_post", fake_post)
    result = ai_provider.plan_with_ai(
        settings=_settings(),
        name="compact plan",
        target_url="https://example.test/",
        flow="确认页面可访问",
        role=None,
        preconditions=None,
        expectation="页面可访问",
    )

    assert "role" not in captured["schema"]["required"]
    assert result.plan.steps[0].action.value == "navigate"


def test_connection_reports_exhausted_thinking_budget(monkeypatch) -> None:
    monkeypatch.setattr(ai_provider, "_post", lambda *_args, **_kwargs: {
        "choices": [{
            "message": {"content": "", "reasoning_content": "正在思考"},
            "finish_reason": "length",
        }]
    })
    settings = AISettings(
        protocol="chat_completions",
        base_url="https://api.moonshot.cn/v1",
        model="kimi-k3",
        api_key=SecretStr("test-key"),
    )

    with pytest.raises(AIProviderError, match="输出预算在最终回答前已耗尽"):
        ai_provider.test_connection(settings)


def test_chat_text_extraction_supports_segmented_content() -> None:
    data = {"choices": [{"message": {"content": [
        {"type": "text", "text": "O"},
        {"type": "text", "text": "K"},
    ]}}]}

    assert ai_provider._extract_text("chat_completions", data) == "OK"


def test_repair_recoverable_plan_shape_infers_text_assertion_expected() -> None:
    raw = {
        "assertions": [
            {
                "type": "text_contains",
                "locator": {"text": "Edit story"},
                "description": "确认编辑入口存在",
            },
            {"type": "url_contains", "expected": "/stories/"},
        ]
    }

    repaired = ai_provider._repair_recoverable_plan_shape(raw)

    assert repaired["assertions"][0]["expected"] == "Edit story"
    assert "expected" not in raw["assertions"][0]


def test_repair_recoverable_plan_shape_does_not_invent_missing_text() -> None:
    raw = {
        "assertions": [
            {
                "type": "text_contains",
                "locator": {"css": ".story-title"},
                "description": "确认标题存在",
            }
        ]
    }

    repaired = ai_provider._repair_recoverable_plan_shape(raw)

    assert "expected" not in repaired["assertions"][0]


def test_repair_recoverable_plan_shape_removes_empty_wait_and_repairs_page_url() -> None:
    raw = {
        "base_url": "https://ion.cesium.com/stories/story-id",
        "steps": [
            {"action": "navigate", "target": "/"},
            {"action": "wait_for", "value": "visible", "description": "等待页面稳定"},
            {"action": "wait_for_state", "description": "等待页面加载"},
        ],
        "assertions": [{"type": "page_reached", "description": "确认进入目标 Story"}],
    }

    repaired = ai_provider._repair_recoverable_plan_shape(raw)

    assert repaired["steps"] == [{"action": "navigate", "target": "/"}]
    assert repaired["assertions"][0]["expected"] == "/stories/story-id"


def test_repair_recoverable_plan_shape_drops_non_executable_extra_notes() -> None:
    raw = {
        "steps": [{
            "action": "navigate",
            "target": "/",
            "assertions_note": "assertions are listed below",
        }],
        "assertions": [{
            "type": "url_contains",
            "expected": "/stories/",
            "evidence_note": "read-only",
        }],
    }

    repaired = ai_provider._repair_recoverable_plan_shape(raw)

    assert repaired["steps"] == [{"action": "navigate", "target": "/"}]
    assert repaired["assertions"] == [{
        "type": "url_contains",
        "expected": "/stories/",
    }]


def test_post_retries_a_transient_gateway_failure(monkeypatch) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **_kwargs):
            calls.append(url)
            status = 502 if len(calls) < 2 else 200
            return httpx.Response(
                status,
                json={"choices": [{"message": {"content": "OK"}}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    result = ai_provider._post(_settings(), "hello", schema=None)

    assert result["choices"][0]["message"]["content"] == "OK"
    assert len(calls) == 2


def test_post_does_not_retry_an_invalid_api_key(monkeypatch) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **_kwargs):
            calls.append(url)
            return httpx.Response(401, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)

    try:
        ai_provider._post(_settings(), "hello", schema=None)
    except AIProviderError as exc:
        assert "HTTP 401" in str(exc)
    else:
        raise AssertionError("HTTP 401 must remain a terminal configuration error")
    assert len(calls) == 1


def test_post_exhausts_transient_retries_and_reports_safe_upstream_ids(monkeypatch) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **_kwargs):
            calls.append(url)
            return httpx.Response(
                502,
                json={"error": {"type": "server_error", "code": "upstream_busy", "message": "not logged"}},
                headers={"x-request-id": "req-test-123"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    try:
        ai_provider._post(_settings(), "hello", schema={"type": "object"})
    except AIProviderUnavailableError as exc:
        message = str(exc)
        assert "HTTP 502" in message
        assert "已重试 2 次" in message
        assert "upstream_code=upstream_busy" in message
        assert "request_id=req-test-123" in message
        assert "not logged" not in message
        assert exc.status_code == 502
        assert exc.attempts == 2
    else:
        raise AssertionError("persistent HTTP 502 must remain terminal")
    assert len(calls) == 2


def test_exhausted_connection_failures_are_classified_as_provider_unavailable(monkeypatch) -> None:
    calls = []

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **_kwargs):
            calls.append(url)
            raise httpx.ConnectError("temporary", request=httpx.Request("POST", url))

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    try:
        ai_provider._post(_settings(), "hello", schema=None)
    except AIProviderUnavailableError as exc:
        assert "已重试 2 次" in str(exc)
        assert exc.status_code is None
        assert exc.attempts == 2
    else:
        raise AssertionError("persistent connection failure must be provider unavailable")
    assert len(calls) == 2


def test_connection_probe_fails_fast_with_actionable_base_url_hint(monkeypatch) -> None:
    calls = []
    settings = AISettings(
        protocol="responses",
        base_url="https://gateway.example.test",
        model="test-model",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **_kwargs):
            calls.append(url)
            raise httpx.ConnectError("unreachable", request=httpx.Request("POST", url))

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    try:
        ai_provider._post(settings, "hello", schema=None, connection_test=True)
    except AIProviderUnavailableError as exc:
        assert exc.attempts == 2
        assert "host=gateway.example.test" in str(exc)
        assert "/v1" in str(exc)
    else:
        raise AssertionError("connection probes must fail after the bounded fast retry")
    assert len(calls) == 2


def test_deepseek_transient_failure_falls_back_from_json_mode(monkeypatch) -> None:
    calls = []
    settings = AISettings(
        protocol="chat_completions",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **kwargs):
            calls.append(kwargs["json"].copy())
            status = 502 if len(calls) < 2 else 200
            return httpx.Response(
                status,
                json={"choices": [{"message": {"content": "{}"}}]},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    ai_provider._post(settings, "return JSON", schema={"type": "object"})

    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert len(calls) == 2


def test_compatible_responses_gateway_falls_back_from_nested_schema(monkeypatch) -> None:
    calls = []
    settings = AISettings(
        protocol="responses",
        base_url="https://gateway.example.test/v1",
        model="compatible-model",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **kwargs):
            calls.append(kwargs["json"].copy())
            status = 502 if len(calls) < 2 else 200
            return httpx.Response(
                status,
                json={"output_text": "{}"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    ai_provider._post(settings, "return JSON", schema={"type": "object"})

    assert "text" in calls[0]
    assert "text" not in calls[1]
    assert len(calls) == 2


def test_compatible_responses_gateway_retries_precise_invalid_schema_400(monkeypatch) -> None:
    calls = []
    settings = AISettings(
        protocol="responses",
        base_url="https://gateway.example.test/v1",
        model="compatible-model",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **kwargs):
            calls.append(kwargs["json"].copy())
            if len(calls) == 1:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "type": "invalid_request_error",
                            "code": "invalid_json_schema",
                            "message": "sensitive upstream implementation detail",
                        }
                    },
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={"output_text": "{}"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    result = ai_provider._post(settings, "return JSON", schema={"type": "object"})

    assert result["output_text"] == "{}"
    assert "text" in calls[0]
    assert "text" not in calls[1]
    assert len(calls) == 2


def test_responses_gateway_schema_incompatibility_is_cached(monkeypatch) -> None:
    calls = []
    settings = AISettings(
        protocol="responses",
        base_url="https://schema-cache.example.test/v1",
        model="schema-cache-model",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **kwargs):
            payload = kwargs["json"].copy()
            calls.append(payload)
            if len(calls) == 1:
                return httpx.Response(
                    400,
                    json={"error": {"code": "invalid_json_schema"}},
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={"output_text": "{}"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    ai_provider._post(settings, "first", schema={"type": "object"})
    ai_provider._post(settings, "second", schema={"type": "object"})

    assert len(calls) == 3
    assert "text" in calls[0]
    assert "text" not in calls[1]
    assert "text" not in calls[2]


def test_responses_gateway_uses_bounded_chat_transport_after_double_schema_rejection(
    monkeypatch,
) -> None:
    calls = []
    settings = AISettings(
        protocol="responses",
        base_url="https://double-schema-reject.example.test/v1",
        model="compatible-model",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **kwargs):
            calls.append((url, kwargs["json"].copy()))
            if url.endswith("/responses"):
                return httpx.Response(
                    400,
                    json={"error": {"code": "invalid_json_schema"}},
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "{}"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)
    prompt = [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "return JSON"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        ],
    }]

    result = ai_provider._post(settings, prompt, schema={"type": "object"})

    assert [url.rsplit("/", 1)[-1] for url, _payload in calls] == [
        "responses",
        "responses",
        "completions",
    ]
    assert "text" in calls[0][1]
    assert "text" not in calls[1][1]
    chat_content = calls[2][1]["messages"][1]["content"]
    assert chat_content[0] == {"type": "text", "text": "return JSON"}
    assert chat_content[1]["type"] == "image_url"
    assert result["output_text"] == "{}"
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert result["_gui_compatibility_mode"] == (
        ai_provider.RESPONSES_CHAT_COMPATIBILITY_MODE
    )

    calls.clear()
    ai_provider._post(settings, "second", schema={"type": "object"})
    assert len(calls) == 1
    assert calls[0][0].endswith("/chat/completions")


def test_official_responses_endpoint_never_switches_transport(monkeypatch) -> None:
    calls = []
    settings = AISettings(
        protocol="responses",
        base_url="https://api.openai.com/v1",
        model="official-model-double-reject",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **kwargs):
            calls.append((url, kwargs["json"].copy()))
            return httpx.Response(
                400,
                json={"error": {"code": "invalid_json_schema"}},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    with pytest.raises(ai_provider.AIProviderConfigurationError):
        ai_provider._post(settings, "return JSON", schema={"type": "object"})

    assert len(calls) == 2
    assert all(url.endswith("/responses") for url, _payload in calls)


def test_compatible_responses_gateway_does_not_retry_unrelated_400(monkeypatch) -> None:
    calls = []
    settings = AISettings(
        protocol="responses",
        base_url="https://gateway.example.test/v1",
        model="compatible-model",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **kwargs):
            calls.append(kwargs["json"].copy())
            return httpx.Response(
                400,
                json={"error": {"type": "invalid_request_error", "code": "invalid_model"}},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)

    with pytest.raises(AIProviderError):
        ai_provider._post(settings, "return JSON", schema={"type": "object"})

    assert len(calls) == 1


def test_official_looking_responses_endpoint_still_falls_back_on_schema_code(monkeypatch) -> None:
    calls = []
    settings = AISettings(
        protocol="responses",
        base_url="https://api.openai.com/v1",
        model="compatible-proxy-model",
        api_key=SecretStr("test-key"),
    )

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def post(self, url, **kwargs):
            calls.append(kwargs["json"].copy())
            if len(calls) == 1:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "type": "invalid_request_error",
                            "code": "invalid_json_schema",
                        }
                    },
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={"output_text": "{}"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(ai_provider.httpx, "Client", Client)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)

    result = ai_provider._post(settings, "return JSON", schema={"type": "object"})

    assert result["output_text"] == "{}"
    assert "text" in calls[0]
    assert "text" not in calls[1]
    assert len(calls) == 2
