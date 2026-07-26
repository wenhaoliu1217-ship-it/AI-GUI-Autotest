import json
from pathlib import Path

from pydantic import SecretStr

from gui_agent.domain.results import Observation
from gui_agent.planning.ai_provider import AISettings
from gui_agent.planning.visual_adapter import OpenAIVisualAdapter


class FakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({
                "target": "地图标记 A",
                "x_ratio": 0.4,
                "y_ratio": 0.6,
                "confidence": 0.91,
                "rationale": "目标位于 Canvas 中部偏左",
            }, ensure_ascii=False)}]}],
            "usage": {"input_tokens": 200, "output_tokens": 40},
        }


class FakeClient:
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
        return FakeResponse()


def test_visual_adapter_sends_screenshot_and_returns_bounded_relative_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.visual_adapter.httpx.Client", FakeClient)
    screenshot = tmp_path / "page.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nvisual-test")
    adapter = OpenAIVisualAdapter(AISettings(
        protocol="responses",
        base_url="https://api.openai.com/v1",
        model="vision-test-model",
        api_key=SecretStr("visual-private-key"),
        input_cost_per_million=1,
        output_cost_per_million=5,
    ))

    result = adapter.suggest(screenshot, "地图标记 A", Observation(url="https://example.com/map", title="地图"))

    assert result.suggestion.x_ratio == 0.4 and result.suggestion.y_ratio == 0.6
    assert result.suggestion.confidence == 0.91
    assert result.input_tokens == 200 and result.output_tokens == 40
    assert result.estimated_cost == 0.0004
    content = FakeClient.last_json["input"][0]["content"]
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert FakeClient.last_headers["Authorization"] == "Bearer visual-private-key"
    assert "visual-private-key" not in json.dumps(FakeClient.last_json)
