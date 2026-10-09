from __future__ import annotations

import base64
import io

from PIL import Image
from pydantic import SecretStr

from gui_agent.planning import ai_provider


def test_vision_probe_uses_an_opaque_red_image(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(settings, prompt, **kwargs):
        captured["prompt"] = prompt
        return {"output_text": "RED"}

    monkeypatch.setattr(ai_provider, "_post", fake_post)
    settings = ai_provider.AISettings(
        protocol="responses",
        model="vision-model",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("test-key"),
    )

    ai_provider._post_vision_probe(settings)

    data_url = captured["prompt"][0]["content"][1]["image_url"]
    raw = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)).convert("RGBA") as image:
        assert image.size == (2, 2)
        assert set(image.getdata()) == {(255, 0, 0, 255)}


def test_capability_probe_exposes_safe_visual_failure_reason(monkeypatch) -> None:
    settings = ai_provider.AISettings(
        protocol="responses",
        model="text-only-model",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("test-key"),
    )

    monkeypatch.setattr(ai_provider, "test_connection", lambda _settings: {
        "connected": True,
        "model": "text-only-model",
        "protocol": "responses",
        "elapsedMs": 1,
    })

    def fake_post(_settings, _prompt, **kwargs):
        if kwargs.get("schema_name") == "gui_capability_probe":
            return {"output_text": '{"echo":"schema-ok"}'}
        return {"output_text": "GUI_MULTI_TURN_7319"}

    monkeypatch.setattr(ai_provider, "_post", fake_post)
    monkeypatch.setattr(
        ai_provider,
        "_post_vision_probe",
        lambda _settings: (_ for _ in ()).throw(ai_provider.AIProviderError("视觉模型服务返回错误（HTTP 400）")),
    )

    result = ai_provider.probe_capabilities(settings)

    assert result["capabilities"]["vision"] == "failed"
    assert result["capabilities"]["visionError"] == "视觉模型服务返回错误（HTTP 400）"
