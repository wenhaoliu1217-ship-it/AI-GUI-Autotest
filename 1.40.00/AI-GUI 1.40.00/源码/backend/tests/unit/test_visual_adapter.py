import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from gui_agent.domain.results import Observation
from gui_agent.planning.ai_provider import AIProviderError, AISettings
from gui_agent.planning.visual_adapter import OpenAIVisualAdapter
from gui_agent.planning.visual_adapter import VisualSuggestion


def test_visual_polygon_requires_and_accepts_bounded_points() -> None:
    suggestion = VisualSuggestion(
        target="广场边界",
        action="draw_polygon",
        points=[
            {"x_ratio": 0.1, "y_ratio": 0.2},
            {"x_ratio": 0.8, "y_ratio": 0.2},
            {"x_ratio": 0.9, "y_ratio": 0.8},
            {"x_ratio": 0.2, "y_ratio": 0.9},
        ],
        confidence=0.9,
        rationale="边界清晰",
    )

    assert suggestion.x_ratio is None
    assert len(suggestion.points) == 4

    with pytest.raises(ValueError, match="至少需要三个"):
        VisualSuggestion(
            target="无效边界",
            action="draw_polygon",
            points=[{"x_ratio": 0.1, "y_ratio": 0.2}],
            confidence=0.9,
            rationale="点不足",
        )


def test_visual_inspect_requires_observed_text_and_no_coordinates() -> None:
    suggestion = VisualSuggestion(
        target="面积测量结果",
        action="inspect",
        observed_text="149,661.49 m²",
        confidence=0.94,
        rationale="面积标签清晰可见",
    )
    assert suggestion.x_ratio is None
    assert suggestion.observed_text == "149,661.49 m²"

    with pytest.raises(ValueError, match="实际观察到"):
        VisualSuggestion(
            target="面积测量结果",
            action="inspect",
            confidence=0.9,
            rationale="未返回结果",
        )


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


class TransientFakeClient(FakeClient):
    attempts = 0

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.attempts += 1
        if self.__class__.attempts < 3:
            raise __import__("httpx").ConnectError("temporary")
        return super().post(url, headers=headers, json=json)


class KimiFakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": json.dumps({
                "target": "天安门搜索建议",
                "action": "inspect",
                "observed_text": "Tian'anmen Square, China",
                "confidence": 0.95,
                "rationale": "搜索建议在截图右上角清晰可见",
            }, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 50},
        }


class KimiFakeClient(FakeClient):
    def post(self, url: str, *, headers: dict, json: dict) -> KimiFakeResponse:
        self.__class__.last_headers = headers
        self.__class__.last_json = json
        return KimiFakeResponse()


class LowConfidencePolygonResponse:
    status_code = 200

    def json(self) -> dict:
        return {
            "output_text": json.dumps({
                "target": "边界不可确认",
                "action": "draw_polygon",
                "points": [],
                "confidence": 0.52,
                "rationale": "当前比例尺下没有可辨认的完整边界",
            }, ensure_ascii=False),
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }


class LowConfidencePolygonClient(FakeClient):
    def post(self, url: str, *, headers: dict, json: dict) -> LowConfidencePolygonResponse:
        return LowConfidencePolygonResponse()


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


def test_visual_adapter_retries_transient_network_failures(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.visual_adapter.httpx.Client", TransientFakeClient)
    monkeypatch.setattr("gui_agent.planning.visual_adapter.time.sleep", lambda _: None)
    TransientFakeClient.attempts = 0
    screenshot = tmp_path / "page.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nvisual-test")
    adapter = OpenAIVisualAdapter(AISettings(
        protocol="responses", base_url="https://api.openai.com/v1",
        model="vision-test-model", api_key=SecretStr("visual-private-key"),
    ))

    result = adapter.suggest(screenshot, "地图标记 A", Observation(url="https://example.com/map"))

    assert TransientFakeClient.attempts == 3
    assert result.suggestion.confidence == 0.91


def test_visual_adapter_disables_thinking_for_official_kimi_k26(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.visual_adapter.httpx.Client", KimiFakeClient)
    screenshot = tmp_path / "page.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nvisual-test")
    adapter = OpenAIVisualAdapter(AISettings(
        protocol="chat_completions",
        base_url="https://api.moonshot.cn/v1",
        model="kimi-k2.6",
        api_key=SecretStr("visual-private-key"),
    ))

    result = adapter.suggest(
        screenshot,
        "天安门搜索建议",
        Observation(url="https://ion.cesium.com/stories/editor"),
        requested_action="inspect",
    )

    assert result.suggestion.action == "inspect"
    assert KimiFakeClient.last_json["thinking"] == {"type": "disabled"}
    assert KimiFakeClient.last_json["response_format"] == {"type": "json_object"}


def test_low_confidence_polygon_without_points_is_reported_as_unconfirmed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("gui_agent.planning.visual_adapter.httpx.Client", LowConfidencePolygonClient)
    screenshot = tmp_path / "page.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nvisual-test")
    adapter = OpenAIVisualAdapter(AISettings(
        protocol="responses",
        base_url="https://api.openai.com/v1",
        model="vision-test-model",
        api_key=SecretStr("visual-private-key"),
    ))

    with pytest.raises(AIProviderError, match="置信度 0.52"):
        adapter.suggest(
            screenshot,
            "真实地标边界",
            Observation(url="https://example.com/map"),
            requested_action="draw_polygon",
        )
