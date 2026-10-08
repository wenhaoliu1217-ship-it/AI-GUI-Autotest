"""Validated structured model calls with one bounded repair attempt."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from .ai_provider import (
    AIProviderError,
    AIProviderOutputError,
    AISettings,
    _RUNTIME_MODEL_REQUEST_TIMEOUT_SECONDS,
    _extract_text,
    _parse_json_object,
    _post,
    _validation_summary,
)


T = TypeVar("T", bound=BaseModel)
Normalizer = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class StructuredCallResult(Generic[T]):
    value: T
    elapsed_ms: int
    input_tokens: int
    output_tokens: int
    attempt_count: int
    repair_count: int
    compatibility_modes: tuple[str, ...] = ()


class StructuredModelGateway:
    """Central structured-output boundary for planner calls.

    HTTP retry remains in the provider transport. This layer handles only
    parse/schema failures and performs at most one schema-preserving repair.
    """

    def __init__(self, settings: AISettings) -> None:
        self.settings = settings.validated()

    def request(
        self,
        *,
        prompt: str | list[dict[str, Any]],
        schema: dict[str, Any],
        schema_name: str,
        model_type: type[T],
        instructions: str,
        normalizer: Normalizer | None = None,
    ) -> StructuredCallResult[T]:
        started = time.perf_counter()
        first = _post(
            self.settings,
            prompt,
            schema=schema,
            schema_name=schema_name,
            instructions=instructions,
            request_timeout_seconds=_RUNTIME_MODEL_REQUEST_TIMEOUT_SECONDS,
        )
        input_tokens, output_tokens = _usage(self.settings.protocol, first)
        first_text = _extract_text(self.settings.protocol, first)
        try:
            value = self._validate(first_text, model_type, normalizer)
            return StructuredCallResult(
                value=value,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                attempt_count=1,
                repair_count=0,
                compatibility_modes=_compatibility_modes(first),
            )
        except (AIProviderError, ValidationError) as first_error:
            repair_prompt = _repair_prompt(first_text, first_error, schema)

        repaired = _post(
            self.settings,
            _append_repair_prompt(prompt, repair_prompt, self.settings.protocol),
            schema=schema,
            schema_name=schema_name,
            instructions=instructions,
            request_timeout_seconds=_RUNTIME_MODEL_REQUEST_TIMEOUT_SECONDS,
        )
        repaired_input, repaired_output = _usage(self.settings.protocol, repaired)
        repaired_text = _extract_text(self.settings.protocol, repaired)
        try:
            value = self._validate(repaired_text, model_type, normalizer)
        except (AIProviderError, ValidationError) as exc:
            detail = _error_summary(exc)
            raise AIProviderOutputError(
                f"Structured model output failed validation after one bounded repair: {detail}"
            ) from exc
        return StructuredCallResult(
            value=value,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            input_tokens=input_tokens + repaired_input,
            output_tokens=output_tokens + repaired_output,
            attempt_count=2,
            repair_count=1,
            compatibility_modes=_compatibility_modes(first, repaired),
        )

    @staticmethod
    def _validate(text: str, model_type: type[T], normalizer: Normalizer | None) -> T:
        raw = _parse_json_object(text)
        if normalizer is not None:
            raw = normalizer(raw)
        return model_type.model_validate(raw)


def _repair_prompt(text: str, error: Exception, schema: dict[str, Any]) -> str:
    invalid = text[:12_000]
    return (
        "The previous JSON object failed local validation. Correct only its structure and bounded field types. "
        "Do not add actions, weaken safety metadata, or claim execution results. Return one JSON object only.\n\n"
        f"Validation errors: {_error_summary(error)}\n\n"
        f"Previous output: {invalid}\n\n"
        f"Required schema: {json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
    )


def _append_repair_prompt(
    original: str | list[dict[str, Any]],
    repair: str,
    protocol: str,
) -> str | list[dict[str, Any]]:
    """Preserve the original image evidence during bounded schema repair."""
    if isinstance(original, str):
        return repair
    content_type = "input_text" if protocol == "responses" else "text"
    return [*original, {"role": "user", "content": [{"type": content_type, "text": repair}]}]


def _error_summary(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return _validation_summary(error)
    return str(error)[:1_500]


def _usage(protocol: str, data: dict[str, Any]) -> tuple[int, int]:
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    if protocol == "responses":
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _compatibility_modes(*responses: dict[str, Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(item.get("_gui_compatibility_mode") or "").strip()
        for item in responses
        if str(item.get("_gui_compatibility_mode") or "").strip()
    ))
