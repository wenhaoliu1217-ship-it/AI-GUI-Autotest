from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from gui_agent.domain.results import Observation
from gui_agent.planning import visual_adapter
from gui_agent.planning.ai_provider import AISettings
from gui_agent.planning.ai_provider import AIProviderOutputError
from gui_agent.planning.visual_adapter import OpenAIVisualAdapter


def test_visual_inspection_returns_advisory_page_facts(monkeypatch, tmp_path) -> None:
    screenshot = tmp_path / "page.png"
    screenshot.write_bytes(b"not-a-real-image-needed-for-mocked-provider")
    captured = {}

    def fake_post(_settings, _prompt, _image_url, _schema, **kwargs):
        captured.update(kwargs)
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "layout_summary": "A wizard dialog is visible.",
                        "visible_regions": ["dialog"],
                        "interactive_targets": ["model selector"],
                        "workflow_hypotheses": ["open selector before choosing"],
                        "visible_blockers": ["next button disabled"],
                        "confidence": 0.91,
                    })
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
        }

    monkeypatch.setattr(visual_adapter, "_post_visual", fake_post)
    adapter = OpenAIVisualAdapter(AISettings(
        protocol="chat_completions",
        base_url="https://model.example.test/v1",
        model="vision-model",
        api_key=SecretStr("test-key"),
    ))

    result = adapter.inspect(
        screenshot,
        Observation(url="https://example.test/wizard", title="Wizard"),
    )

    assert result.assessment.confidence == 0.91
    assert result.assessment.interactive_targets == ["model selector"]
    assert captured["schema_name"] == "visual_page_assessment"


def test_visual_request_uses_openai_v1_root_for_pathless_gateway(monkeypatch) -> None:
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
            return type("Response", (), {
                "status_code": 200,
                "json": lambda self: {
                    "choices": [{"message": {"content": "{}"}}]
                },
            })()

    monkeypatch.setattr(visual_adapter.httpx, "Client", Client)
    visual_adapter._post_visual(
        AISettings(
            protocol="chat_completions",
            base_url="https://gateway.example.test",
            model="vision-model",
            api_key=SecretStr("test-key"),
        ),
        "inspect",
        "data:image/png;base64,AAAA",
        {"type": "object"},
    )

    assert calls == ["https://gateway.example.test/v1/chat/completions"]


def test_low_visual_confidence_is_classified_as_recoverable_model_output(monkeypatch, tmp_path) -> None:
    screenshot = tmp_path / "page.png"
    screenshot.write_bytes(b"mock")
    payload = {
        "choices": [{"message": {"content": json.dumps({
            "target": "展开已有实例",
            "action": "click",
            "x_ratio": 0.5,
            "y_ratio": 0.5,
            "expected_change": "实例详情展开",
            "confidence": 0.58,
            "rationale": "目标部分可见",
        })}}],
    }
    monkeypatch.setattr(visual_adapter, "_post_visual", lambda *_args, **_kwargs: payload)
    adapter = OpenAIVisualAdapter(AISettings(
        protocol="chat_completions",
        base_url="https://model.example.test/v1",
        model="vision-model",
        api_key=SecretStr("test-key"),
    ))

    with pytest.raises(AIProviderOutputError, match="0.58"):
        adapter.suggest(
            screenshot,
            "展开已有实例",
            Observation(url="https://example.test/editor", title="Editor"),
        )
