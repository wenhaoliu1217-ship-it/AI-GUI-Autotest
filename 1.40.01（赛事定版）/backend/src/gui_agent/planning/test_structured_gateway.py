from pydantic import BaseModel, SecretStr

from gui_agent.planning.ai_provider import AISettings
from gui_agent.planning.structured_gateway import StructuredModelGateway


class Decision(BaseModel):
    count: int


def test_structured_gateway_repairs_one_schema_failure(monkeypatch) -> None:
    responses = iter([
        {"choices": [{"message": {"content": '{"count":""}'}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}},
        {"choices": [{"message": {"content": '{"count":4}'}}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}},
    ])
    monkeypatch.setattr(
        "gui_agent.planning.structured_gateway._post",
        lambda *_args, **_kwargs: next(responses),
    )
    gateway = StructuredModelGateway(AISettings(
        protocol="chat_completions",
        base_url="https://api.example.test/v1",
        model="test-model",
        api_key=SecretStr("temporary-key"),
    ))

    result = gateway.request(
        prompt="Return a count",
        schema=Decision.model_json_schema(),
        schema_name="decision",
        model_type=Decision,
        instructions="Return JSON",
    )

    assert result.value.count == 4
    assert result.attempt_count == 2
    assert result.repair_count == 1
    assert result.input_tokens == 8
    assert result.output_tokens == 3


def test_structured_gateway_does_not_repair_valid_output(monkeypatch) -> None:
    calls = []

    def respond(*_args, **kwargs):
        calls.append(kwargs)
        return {"choices": [{"message": {"content": '{"count":2}'}}]}

    monkeypatch.setattr("gui_agent.planning.structured_gateway._post", respond)
    gateway = StructuredModelGateway(AISettings(
        protocol="chat_completions",
        base_url="https://api.example.test/v1",
        model="test-model",
        api_key=SecretStr("temporary-key"),
    ))

    result = gateway.request(
        prompt="Return a count",
        schema=Decision.model_json_schema(),
        schema_name="decision",
        model_type=Decision,
        instructions="Return JSON",
    )

    assert result.value.count == 2
    assert result.attempt_count == 1
    assert result.repair_count == 0
    assert len(calls) == 1
    assert calls[0]["request_timeout_seconds"] == 30.0
