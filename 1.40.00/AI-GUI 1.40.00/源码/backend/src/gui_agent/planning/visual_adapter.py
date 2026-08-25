"""Multimodal screenshot adapter that proposes bounded relative coordinates."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from ..domain.results import Observation
from .ai_provider import (
    AIProviderError,
    AISettings,
    _chat_compatibility_options,
    _estimated_cost,
    _extract_text,
    _openai_endpoint_base,
    _parse_json_object,
    _strict_schema,
    _validation_summary,
)


class VisualPoint(BaseModel):
    model_config = {"extra": "forbid"}

    x_ratio: float = Field(ge=0, le=1)
    y_ratio: float = Field(ge=0, le=1)


class VisualSuggestion(BaseModel):
    model_config = {"extra": "forbid"}

    target: str = Field(min_length=1, max_length=500)
    action: Literal["click", "hover", "scroll", "drag", "draw_polygon", "inspect"] = "click"
    x_ratio: float | None = Field(default=None, ge=0, le=1)
    y_ratio: float | None = Field(default=None, ge=0, le=1)
    end_x_ratio: float | None = Field(default=None, ge=0, le=1)
    end_y_ratio: float | None = Field(default=None, ge=0, le=1)
    points: list[VisualPoint] = Field(default_factory=list, max_length=64)
    observed_text: str | None = Field(default=None, min_length=1, max_length=2_000)
    scroll_delta_y: int = Field(default=600, ge=-5000, le=5000)
    expected_change: str = Field(default="页面或目标的可见状态发生变化", min_length=1, max_length=800)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def validate_coordinates(self) -> "VisualSuggestion":
        if self.action == "inspect":
            if not self.observed_text:
                raise ValueError("inspect 必须返回截图中实际观察到的文字或状态")
        elif self.action == "draw_polygon":
            if len(self.points) < 3:
                raise ValueError("draw_polygon 至少需要三个受区域约束的相对顶点")
        elif self.x_ratio is None or self.y_ratio is None:
            raise ValueError(f"{self.action} 需要起点相对坐标")
        if self.action == "drag" and (self.end_x_ratio is None or self.end_y_ratio is None):
            raise ValueError("drag 需要终点相对坐标")
        return self


@dataclass(frozen=True)
class VisualSuggestionResult:
    suggestion: VisualSuggestion
    model: str
    protocol: str
    elapsed_ms: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float | None


class OpenAIVisualAdapter:
    def __init__(self, settings: AISettings, *, minimum_confidence: float = 0.7) -> None:
        self.settings = settings.validated()
        self.minimum_confidence = minimum_confidence

    def suggest(
        self,
        screenshot_path: Path,
        target: str,
        observation: Observation,
        requested_action: str = "click",
        expected_change: str = "页面或目标的可见状态发生变化",
    ) -> VisualSuggestionResult:
        if not screenshot_path.is_file():
            raise AIProviderError("视觉 fallback 缺少当前页面截图")
        started = time.perf_counter()
        schema = _strict_schema(VisualSuggestion.model_json_schema())
        image_url = "data:image/png;base64," + base64.b64encode(screenshot_path.read_bytes()).decode("ascii")
        prompt = (
            "在截图中定位指定语义目标。坐标相对于调用方指定的区域；未指定区域时相对于整个视口。"
            "动作只能是 click、hover、scroll、drag、draw_polygon、inspect，必须遵循请求动作；"
            "drag 必须返回终点坐标；draw_polygon 必须按边界顺序返回至少三个 points，"
            "每个点均为指定 Canvas 区域内的 0..1 相对坐标，且不要重复首点。"
            "inspect 是严格只读动作：不得建议坐标或点击，只在 observed_text 中返回截图里确实可见的目标文字、数值、单位或状态；"
            "不得建议支付、删除、发布等危险动作；无法可靠定位时把 confidence 设为低于 0.7。\n"
            f"目标：{target}\n请求动作：{requested_action}\n预期变化：{expected_change}\n"
            f"页面 URL：{observation.url}\n页面标题：{observation.title}\n"
            f"输出 Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        data: dict | None = None
        last_error: AIProviderError | None = None
        for attempt in range(3):
            try:
                data = _post_visual(self.settings, prompt, image_url, schema)
                break
            except AIProviderError as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                time.sleep(2 ** attempt)
        if data is None:
            assert last_error is not None
            raise last_error
        raw = _parse_json_object(_extract_text(self.settings.protocol, data))
        # Models correctly express uncertainty by returning a low confidence,
        # but may deliberately omit unsafe coordinates in that case.  Surface
        # the uncertainty before coordinate-schema validation so a refused
        # polygon is reported as "unconfirmed" rather than a malformed action.
        raw_confidence = raw.get("confidence") if isinstance(raw, dict) else None
        if isinstance(raw_confidence, (int, float)) and raw_confidence < self.minimum_confidence:
            raise AIProviderError(f"视觉模型无法可靠确认目标（置信度 {raw_confidence:.2f}）")
        try:
            suggestion = VisualSuggestion.model_validate(raw)
        except ValidationError as exc:
            raise AIProviderError(f"视觉建议未通过安全 Schema 校验：{_validation_summary(exc)}") from exc
        if suggestion.action != requested_action:
            raise AIProviderError("视觉模型返回的动作与受约束请求不一致")
        if suggestion.confidence < self.minimum_confidence:
            raise AIProviderError(f"视觉模型无法可靠确认目标（置信度 {suggestion.confidence:.2f}）")
        input_tokens, output_tokens = _usage(self.settings.protocol, data)
        return VisualSuggestionResult(
            suggestion=suggestion,
            model=self.settings.model.strip(),
            protocol=self.settings.protocol,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=_estimated_cost(self.settings, input_tokens, output_tokens),
        )


def _post_visual(settings: AISettings, prompt: str, image_url: str, schema: dict) -> dict:
    base = _openai_endpoint_base(settings.base_url)
    headers = {"Authorization": f"Bearer {settings.api_key.get_secret_value()}", "Content-Type": "application/json"}
    if settings.protocol == "responses":
        endpoint = f"{base}/responses"
        payload = {
            "model": settings.model.strip(),
            "instructions": "你是只读视觉定位适配器。只分析截图并输出受约束建议，不执行任何浏览器动作。",
            "input": [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_url},
                ],
            }],
            "text": {"format": {"type": "json_schema", "name": "visual_target", "schema": schema, "strict": True}},
        }
    else:
        endpoint = f"{base}/chat/completions"
        payload = {
            "model": settings.model.strip(),
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            "response_format": {"type": "json_object"},
        }
        payload.update(_chat_compatibility_options(settings))
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=False) as client:
            response = client.post(endpoint, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise AIProviderError("视觉模型 API 连接超时") from exc
    except httpx.HTTPError as exc:
        raise AIProviderError("无法连接视觉模型 API") from exc
    if response.status_code >= 400:
        raise AIProviderError(f"视觉模型服务返回错误（HTTP {response.status_code}）")
    try:
        return response.json()
    except ValueError as exc:
        raise AIProviderError("视觉模型服务返回的不是 JSON") from exc


def _usage(protocol: str, data: dict) -> tuple[int, int]:
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    if protocol == "responses":
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
