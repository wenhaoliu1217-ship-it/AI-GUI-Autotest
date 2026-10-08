"""Multimodal screenshot adapter that proposes bounded relative coordinates."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx  # kept as a shared transport dependency for compatibility tests
from pydantic import BaseModel, Field, ValidationError, model_validator

from ..domain.results import Observation
from .ai_provider import (
    AIProviderError,
    AIProviderOutputError,
    AISettings,
    _estimated_cost,
    _extract_text,
    _parse_json_object,
    _post,
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
    action: Literal[
        "click", "hover", "scroll", "drag", "zoom", "draw_polygon", "draw_rectangle"
    ] = "click"
    x_ratio: float = Field(ge=0, le=1)
    y_ratio: float = Field(ge=0, le=1)
    end_x_ratio: float | None = Field(default=None, ge=0, le=1)
    end_y_ratio: float | None = Field(default=None, ge=0, le=1)
    scroll_delta_y: int = Field(default=600, ge=-5000, le=5000)
    zoom_delta: int = Field(default=-600, ge=-5000, le=5000)
    points: list[VisualPoint] = Field(default_factory=list, max_length=20)
    gesture_finish: Literal["double_click", "enter", "none"] = "double_click"
    expected_change: str = Field(default="页面或目标的可见状态发生变化", min_length=1, max_length=800)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def validate_gesture(self) -> "VisualSuggestion":
        if self.action == "drag" and (
            self.end_x_ratio is None or self.end_y_ratio is None
        ):
            raise ValueError("drag requires an end coordinate")
        if self.action == "draw_polygon" and len(self.points) < 3:
            raise ValueError("draw_polygon requires at least three points")
        if self.action == "draw_rectangle" and len(self.points) != 2:
            raise ValueError("draw_rectangle requires exactly two points")
        return self


class VisualPageAssessment(BaseModel):
    """Bounded visible-page facts used as advisory exploration evidence."""

    model_config = {"extra": "forbid"}

    layout_summary: str = Field(min_length=1, max_length=1200)
    visible_regions: list[str] = Field(default_factory=list, max_length=20)
    interactive_targets: list[str] = Field(default_factory=list, max_length=40)
    workflow_hypotheses: list[str] = Field(default_factory=list, max_length=20)
    visible_blockers: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0, le=1)


@dataclass(frozen=True)
class VisualSuggestionResult:
    suggestion: VisualSuggestion
    model: str
    protocol: str
    elapsed_ms: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float | None


@dataclass(frozen=True)
class VisualPageAssessmentResult:
    assessment: VisualPageAssessment
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
            "动作只能是 click、hover、scroll、drag、zoom、draw_polygon、draw_rectangle，必须遵循请求动作。"
            "drag 必须返回终点坐标；zoom 返回中心点和 zoom_delta；draw_polygon 返回 3 至 12 个按顺序排列的点；"
            "draw_rectangle 返回两个对角点。所有点必须留在指定 Canvas 区域内部（建议 0.12 到 0.88），"
            "多点测量应一次返回完整手势，不要拆成多个模型回合。"
            "不得建议支付、删除、发布等危险动作；无法可靠定位时把 confidence 设为低于 0.7。\n"
            f"目标：{target}\n请求动作：{requested_action}\n预期变化：{expected_change}\n"
            f"页面 URL：{observation.url}\n页面标题：{observation.title}\n"
            f"输出 Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        data = _post_visual(self.settings, prompt, image_url, schema)
        raw = _parse_json_object(_extract_text(self.settings.protocol, data))
        try:
            suggestion = VisualSuggestion.model_validate(raw)
        except ValidationError as exc:
            raise AIProviderError(f"视觉建议未通过安全 Schema 校验：{_validation_summary(exc)}") from exc
        if suggestion.action != requested_action:
            raise AIProviderError("视觉模型返回的动作与受约束请求不一致")
        if suggestion.confidence < self.minimum_confidence:
            # A low-confidence answer is a recoverable model-output problem,
            # not a permanent adapter/configuration failure.  The runner will
            # recapture the current page and request a fresh decision through
            # its bounded recovery policy; no old coordinates are replayed.
            raise AIProviderOutputError(
                f"视觉模型无法可靠确认目标（置信度 {suggestion.confidence:.2f}）"
            )
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

    def inspect(
        self,
        screenshot_path: Path,
        observation: Observation,
    ) -> VisualPageAssessmentResult:
        """Inspect each newly observed visual state without choosing an action."""

        if not screenshot_path.is_file():
            raise AIProviderError("Visual exploration is missing the current-page screenshot")
        started = time.perf_counter()
        schema = _strict_schema(VisualPageAssessment.model_json_schema())
        image_url = "data:image/png;base64," + base64.b64encode(
            screenshot_path.read_bytes()
        ).decode("ascii")
        prompt = (
            "Inspect the currently visible browser viewport as read-only test evidence. "
            "Describe visible layout regions, controls a user could interact with, likely workflow transitions, "
            "and visible blockers. Do not choose coordinates and do not claim hidden functionality. "
            "Treat workflow hypotheses as tentative; current DOM and post-action verification remain authoritative.\n"
            f"URL: {observation.url}\nTitle: {observation.title}\n"
            f"Output schema: {json.dumps(schema, ensure_ascii=False)}"
        )
        data = _post_visual(
            self.settings,
            prompt,
            image_url,
            schema,
            schema_name="visual_page_assessment",
            instructions=(
                "You are a read-only visual website explorer. Report only visible evidence and bounded hypotheses."
            ),
        )
        raw = _parse_json_object(_extract_text(self.settings.protocol, data))
        try:
            assessment = VisualPageAssessment.model_validate(raw)
        except ValidationError as exc:
            raise AIProviderError(
                f"Visual page assessment failed schema validation: {_validation_summary(exc)}"
            ) from exc
        input_tokens, output_tokens = _usage(self.settings.protocol, data)
        return VisualPageAssessmentResult(
            assessment=assessment,
            model=self.settings.model.strip(),
            protocol=self.settings.protocol,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=_estimated_cost(self.settings, input_tokens, output_tokens),
        )


def _post_visual(
    settings: AISettings,
    prompt: str,
    image_url: str,
    schema: dict,
    *,
    schema_name: str = "visual_target",
    instructions: str | None = None,
) -> dict:
    if settings.protocol == "responses":
        # Share the same bounded provider-compatibility gateway as planner
        # decisions. This preserves current screenshot evidence when a
        # Responses proxy requires the audited Chat Completions transport
        # fallback, while local Pydantic validation remains mandatory.
        return _post(
            settings,
            [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_url},
                ],
            }],
            schema=schema,
            schema_name=schema_name,
            instructions=(
                instructions
                or "You are a read-only visual grounding adapter. Analyze the current screenshot and return one bounded JSON object."
            ),
        )
    # Text and vision requests deliberately share the same transport. This
    # keeps /v1 normalization, bounded retries, schema fallback and the
    # process-local circuit breaker identical for both decision paths.
    visual_prompt = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
    }]
    if settings.protocol == "responses":
        visual_prompt = [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": image_url},
            ],
        }]
    return _post(
        settings,
        visual_prompt,
        schema=schema,
        schema_name=schema_name,
        instructions=(
            instructions
            or "你是只读视觉定位适配器。只分析截图并输出受约束建议，不执行任何浏览器动作。"
        ),
    )


def _usage(protocol: str, data: dict) -> tuple[int, int]:
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    if protocol == "responses":
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
