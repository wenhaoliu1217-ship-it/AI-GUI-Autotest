"""Internal contracts for adapting archived GUI-Agent projects.

The upstream projects are intentionally not imported here.  This module is
the product-side boundary: it probes whether an optional upstream runtime is
actually available and normalizes safe observation/tool results into the
existing domain models.  A repository being present is never enough to mark
an adapter as runtime-ready.
"""

from __future__ import annotations

import ast
import os
import json
import queue
import subprocess
import shutil
import threading
import time
import re
from pathlib import Path
from typing import Any

from ..domain.results import Observation


def _bounded_text(value: Any, limit: int = 6_000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    return str(value)[:limit]


def _bounded_lines(value: Any, limit: int = 60) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        values = value
    else:
        values = str(value).splitlines()
    return [_bounded_text(item, 500) for item in values[:limit] if _bounded_text(item, 500).strip()]


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    """Keep optional upstream payloads JSON-safe and bounded before evidence storage."""

    if depth > 3:
        return _bounded_text(value, 500)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_text(value, 2_000)
    if isinstance(value, list):
        return [_bounded_json(item, depth=depth + 1) for item in value[:40]]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _bounded_json(item, depth=depth + 1)
            for key, item in list(value.items())[:80]
        }
    return _bounded_text(value, 500)


def normalize_stagehand_candidate_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Stagehand observe/act-preview output without executing its action."""

    source = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    raw_candidates = source.get("candidates") or source.get("actions") or source.get("observe") or []
    if isinstance(raw_candidates, dict):
        raw_candidates = raw_candidates.get("actions") or raw_candidates.get("candidates") or [raw_candidates]
    if not isinstance(raw_candidates, list):
        raw_candidates = [raw_candidates]
    candidates: list[dict[str, Any]] = []
    for item in raw_candidates[:20]:
        if isinstance(item, str):
            candidates.append({"description": _bounded_text(item, 500), "action": None, "locator": None})
            continue
        if not isinstance(item, dict):
            continue
        locator = item.get("locator") or item.get("selector") or item.get("elementId")
        candidates.append({
            "action": _bounded_text(item.get("action") or item.get("method") or item.get("type"), 120) or None,
            "description": _bounded_text(item.get("description") or item.get("instruction") or item.get("text"), 500),
            "locator": _bounded_json(locator),
            "confidence": _bounded_text(item.get("confidence"), 80) or None,
            "source": "stagehand",
        })
    extraction = source.get("extraction")
    if extraction is None:
        extraction = source.get("extracted")
    if extraction is None and "result" in source:
        extraction = source.get("result")
    return {
        "adapter": "stagehand",
        "status": "candidate_only",
        "candidates": candidates,
        "extraction": _bounded_json(extraction),
        "evidence": {
            "candidateCount": len(candidates),
            "hasExtraction": extraction is not None,
            "actionPolicy": "candidate_only_no_execution",
        },
    }


def normalize_openadapt_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize an OpenAdapt checkpoint/evidence payload without replaying it."""

    source = payload.get("checkpoint") if isinstance(payload.get("checkpoint"), dict) else payload
    checkpoint_id = source.get("id") or source.get("checkpointId") or source.get("checkpoint_id")
    status = source.get("status") or source.get("state") or payload.get("status")
    current_url = source.get("currentUrl") or source.get("current_url") or source.get("url")
    step_index = source.get("stepIndex") or source.get("step_index") or source.get("index")
    evidence = payload.get("evidence") or source.get("evidence")
    if isinstance(evidence, dict):
        evidence = {
            key: _bounded_json(evidence[key])
            for key in ("status", "outcome", "eventCount", "durationMs", "result", "errorClass", "verified")
            if key in evidence
        }
    artifact_values = payload.get("artifacts") or source.get("artifacts") or []
    if isinstance(artifact_values, dict):
        artifact_values = list(artifact_values)
    return {
        "adapter": "openadapt",
        "status": "checkpoint_ready" if checkpoint_id or status else "checkpoint_missing",
        "checkpoint": {
            "id": _bounded_text(checkpoint_id, 200) or None,
            "status": _bounded_text(status, 120) or None,
            "currentUrl": _bounded_text(current_url, 2_000) or None,
            "stepIndex": step_index if isinstance(step_index, int) else None,
        },
        "evidence": _bounded_json(evidence),
        "artifactNames": [_bounded_text(item, 240) for item in artifact_values[:40] if isinstance(item, (str, int))],
        "actionPolicy": "checkpoint_only_no_execution",
    }


_BROWSER_USE_SENSITIVE_KEY_MARKERS = (
    "password",
    "passwd",
    "api_key",
    "apikey",
    "token",
    "cookie",
    "authorization",
    "secret",
    "storage_state",
    "storagestate",
    "localstorage",
    "sessionstorage",
    "access_token",
    "refresh_token",
)


def _browser_use_safe_json(value: Any, *, depth: int = 0) -> Any:
    """Bound Browser-use state before it enters product-owned evidence."""

    if depth > 3:
        return _bounded_text(value, 500)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_text(value, 2_000)
    if isinstance(value, list):
        return [_browser_use_safe_json(item, depth=depth + 1) for item in value[:40]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            key_text = str(key)[:120]
            if any(marker in key_text.lower() for marker in _BROWSER_USE_SENSITIVE_KEY_MARKERS):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _browser_use_safe_json(item, depth=depth + 1)
        return result
    return _bounded_text(value, 500)


def normalize_browser_use_agent_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Browser-use agent state as bounded, read-only product evidence.

    Browser-use can persist model output, browser state, cookies and action
    results in one object.  The product boundary keeps only a small preview of
    the state, redacts sensitive key families, and never replays an action.
    """

    source = payload.get("agent") if isinstance(payload.get("agent"), dict) else payload
    goal = source.get("goal") or source.get("task") or source.get("instruction")
    if isinstance(goal, dict):
        goal = goal.get("goal") or goal.get("task") or goal.get("instruction")
    current_url = source.get("current_url") or source.get("currentUrl") or source.get("url")
    if not isinstance(goal, (str, int, float, bool)):
        goal = None
    if not isinstance(current_url, (str, int, float, bool)):
        current_url = None
    raw_steps = source.get("history") or source.get("steps") or source.get("trajectory") or []
    if isinstance(raw_steps, dict):
        raw_steps = raw_steps.get("history") or raw_steps.get("steps") or raw_steps.get("trajectory") or []
    if not isinstance(raw_steps, list):
        raw_steps = []

    next_action = source.get("next_action")
    if next_action is None:
        next_action = source.get("nextAction")
    final_result = source.get("final_result")
    if final_result is None:
        final_result = source.get("finalResult")
    if final_result is None and "result" in source:
        final_result = source.get("result")
    status = source.get("status") or source.get("state") or source.get("run_status") or source.get("runStatus")

    steps: list[dict[str, Any]] = []
    last_url: str | None = None
    for position, item in enumerate(raw_steps[:120]):
        if not isinstance(item, dict):
            continue
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        model_output = item.get("model_output") or item.get("modelOutput")
        action = item.get("action") or item.get("next_action") or item.get("nextAction")
        if action is None and isinstance(model_output, dict):
            action = model_output.get("action") or model_output
        step_url = item.get("url") or item.get("current_url") or item.get("currentUrl")
        if step_url is None:
            step_url = state.get("url") or state.get("current_url") or state.get("currentUrl")
        if isinstance(step_url, str) and step_url:
            last_url = _bounded_text(step_url, 2_000)
        result = item.get("result")
        if result is None:
            result = item.get("extracted_content") or item.get("extractedContent")
        if result is None:
            result = item.get("evaluation") or state.get("result")
        error = item.get("error") or item.get("error_class") or item.get("errorClass")
        explicit_done = next(
            (value for value in (
                item.get("done"), item.get("is_done"), item.get("isDone"), item.get("terminated")
            ) if isinstance(value, bool)),
            None,
        )
        steps.append(
            {
                "index": item.get("step") if isinstance(item.get("step"), int) else (
                    item.get("index") if isinstance(item.get("index"), int) else position
                ),
                "action": _browser_use_safe_json(action),
                "url": _bounded_text(step_url, 2_000) or None,
                "result": _browser_use_safe_json(result),
                "error": _bounded_text(error, 800) or None,
                "done": explicit_done,
            }
        )

    if current_url is None:
        current_url = last_url
    status_text = _bounded_text(status, 120) if isinstance(status, (str, int, float, bool)) else ""
    final_result_dict = final_result if isinstance(final_result, dict) else {}
    explicit_done_values = [source.get("done"), source.get("is_done"), source.get("isDone"), source.get("terminated")]
    if steps:
        explicit_done_values.append(steps[-1].get("done"))
    done = next((value for value in explicit_done_values if isinstance(value, bool)), None)
    explicit_success_values = [source.get("success"), source.get("passed")]
    explicit_success_values.extend([final_result_dict.get("success"), final_result_dict.get("passed")])
    success = next((value for value in explicit_success_values if isinstance(value, bool)), None)
    has_state = bool(goal or current_url or steps or next_action is not None or final_result is not None)
    return {
        "adapter": "browser-use",
        "status": "agent_state_ready" if has_state else "agent_state_incomplete",
        "goal": _bounded_text(goal, 4_000) or None,
        "currentUrl": _bounded_text(current_url, 2_000) or None,
        "runStatus": status_text or None,
        "steps": steps,
        "nextAction": _browser_use_safe_json(next_action),
        "finalResult": _browser_use_safe_json(final_result),
        "summary": {
            "stepCount": len(steps),
            "done": done,
            "success": success,
        },
        "actionPolicy": "agent_state_preview_only_no_execution",
    }


_UI_TARS_ACTION_ALIASES = {
    "left_single": "click",
    "left_double": "double_click",
    "right_single": "right_click",
}
_UI_TARS_SUPPORTED_ACTIONS = {
    "click",
    "double_click",
    "right_click",
    "hover",
    "drag",
    "select",
    "scroll",
    "hotkey",
    "type",
    "press",
    "keydown",
    "keyup",
    "release",
    "finished",
}
_UI_TARS_COORDINATE_KEYS = {
    "point",
    "coordinate",
    "start_point",
    "end_point",
    "start_box",
    "end_box",
}


def _parse_ui_tars_call(value: Any) -> tuple[str | None, dict[str, Any]]:
    """Parse one UI-TARS call with AST literals only; never evaluate code."""

    if isinstance(value, dict):
        action_type = value.get("action_type") or value.get("actionType") or value.get("function")
        inputs = value.get("action_inputs") or value.get("actionInputs") or value.get("args") or {}
        return _bounded_text(action_type, 120).lower() or None, inputs if isinstance(inputs, dict) else {}
    if not isinstance(value, str):
        return None, {}
    match = re.match(r"^\s*([A-Za-z_]\w*)\s*\((.*)\)\s*$", value, re.DOTALL)
    if not match:
        return None, {}
    try:
        node = ast.parse(value, mode="eval").body
    except (SyntaxError, ValueError):
        return None, {}
    if not isinstance(node, ast.Call):
        return None, {}
    if isinstance(node.func, ast.Name):
        action_type = node.func.id
    elif isinstance(node.func, ast.Attribute):
        action_type = node.func.attr
    else:
        return None, {}
    inputs: dict[str, Any] = {}
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        try:
            inputs[keyword.arg] = ast.literal_eval(keyword.value)
        except (ValueError, SyntaxError, TypeError):
            inputs[keyword.arg] = None
    return action_type.lower(), inputs


def _split_ui_tars_action_text(value: str) -> list[str]:
    """Extract balanced function calls from a Thought/Action response."""

    text = value.split("Action:", 1)[1] if "Action:" in value else value
    calls: list[str] = []
    cursor = 0
    while cursor < len(text) and len(calls) < 20:
        match = re.search(r"[A-Za-z_]\w*\s*\(", text[cursor:])
        if not match:
            break
        start = cursor + match.start()
        opening = cursor + match.end() - 1
        depth = 1
        quote: str | None = None
        escaped = False
        position = opening + 1
        while position < len(text) and depth:
            char = text[position]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            position += 1
        if depth:
            break
        calls.append(text[start:position].strip())
        cursor = position
    return calls


def _ui_tars_numbers(value: Any) -> list[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        numbers: list[float] = []
        for item in value[:8]:
            numbers.extend(_ui_tars_numbers(item))
        return numbers
    if not isinstance(value, str):
        return []
    return [float(item) for item in re.findall(r"[-+]?(?:\d+\.?(?:\d*)?|\.\d+)", value)[:8]]


def _normalize_ui_tars_coordinates(
    inputs: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, list[float]] | None, str]:
    coordinate_values: dict[str, list[float]] = {}
    for key in _UI_TARS_COORDINATE_KEYS:
        if key in inputs:
            numbers = _ui_tars_numbers(inputs[key])
            if len(numbers) >= 2:
                coordinate_values[key] = numbers[:4]
    if not coordinate_values:
        return None, "not_applicable"

    unit = _bounded_text(payload.get("coordinateUnit") or payload.get("coordinate_unit"), 60).lower()
    model_type = _bounded_text(payload.get("modelType") or payload.get("model_type"), 60).lower()
    scale = payload.get("coordinateScale") or payload.get("coordinate_scale") or payload.get("factor")
    scale_value = float(scale) if isinstance(scale, (int, float)) and not isinstance(scale, bool) and scale > 0 else None
    width = payload.get("originResizedWidth") or payload.get("origin_resized_width") or payload.get("imageWidth") or payload.get("image_width")
    height = payload.get("originResizedHeight") or payload.get("origin_resized_height") or payload.get("imageHeight") or payload.get("image_height")
    width_value = float(width) if isinstance(width, (int, float)) and not isinstance(width, bool) and width > 0 else None
    height_value = float(height) if isinstance(height, (int, float)) and not isinstance(height, bool) and height > 0 else None
    if unit in {"normalized", "relative", "0-1", "01"}:
        x_scale = y_scale = 1.0
    elif scale_value:
        x_scale = y_scale = scale_value
    elif model_type == "qwen25vl" and width_value and height_value:
        x_scale, y_scale = width_value, height_value
    elif width_value and height_value:
        x_scale, y_scale = width_value, height_value
    elif all(abs(number) <= 1 for values in coordinate_values.values() for number in values):
        x_scale = y_scale = 1.0
    else:
        x_scale = y_scale = 1_000.0

    normalized: dict[str, list[float]] = {}
    for key, numbers in coordinate_values.items():
        if len(numbers) == 2:
            numbers = [numbers[0], numbers[1], numbers[0], numbers[1]]
        values = [numbers[0] / x_scale, numbers[1] / y_scale, numbers[2] / x_scale, numbers[3] / y_scale]
        if any(value < 0 or value > 1 for value in values):
            return None, "out_of_bounds"
        normalized[key] = [round(value, 6) for value in values]
    start_key = next(
        (key for key in ("start_box", "start_point", "point", "coordinate") if key in normalized),
        None,
    )
    end_key = next((key for key in ("end_box", "end_point") if key in normalized), None)
    if start_key is None:
        start_key = end_key
    if start_key is None:
        return None, "missing"
    start = normalized[start_key]
    end = normalized[end_key] if end_key else start
    return {"start": start, "end": end}, "normalized"


def _normalize_ui_tars_action(
    value: Any,
    payload: dict[str, Any],
    thought: Any = None,
) -> dict[str, Any]:
    action_type, raw_inputs = _parse_ui_tars_call(value)
    action_type = action_type or "unknown"
    action_type = _UI_TARS_ACTION_ALIASES.get(action_type, action_type)
    inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
    coordinates, coordinate_status = _normalize_ui_tars_coordinates(inputs, payload)
    safe_inputs: dict[str, Any] = {}
    for key, item in list(inputs.items())[:20]:
        key_text = str(key)[:120]
        if key_text in _UI_TARS_COORDINATE_KEYS:
            continue
        if key_text == "content" and action_type in {"type", "finished"}:
            content = _bounded_text(item, 8_000)
            safe_inputs["contentRedacted"] = True
            safe_inputs["contentLength"] = len(content)
            safe_inputs["contentEndsWithNewline"] = content.endswith("\n")
            continue
        safe_inputs[key_text] = _bounded_json(item)
    return {
        "actionType": action_type,
        "rawActionType": _bounded_text(raw_inputs.get("action_type") if isinstance(raw_inputs, dict) else None, 120) or None,
        "supported": action_type in _UI_TARS_SUPPORTED_ACTIONS,
        "thoughtPreview": _bounded_text(thought, 800) or None,
        "inputs": safe_inputs,
        "coordinates": coordinates,
        "coordinateStatus": coordinate_status,
    }


def normalize_ui_tars_action_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize UI-TARS visual candidates without screenshots or pyautogui execution."""

    raw = payload.get("actions")
    source_format = "structured"
    if raw is None:
        raw = payload.get("response") or payload.get("text") or payload.get("rawAction") or payload.get("action")
        source_format = "text"
    thought = payload.get("thought") or payload.get("reflection")
    items: list[Any]
    if isinstance(raw, list):
        items = raw[:20]
    elif isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, str):
        items = _split_ui_tars_action_text(raw)
    else:
        items = []
    actions: list[dict[str, Any]] = []
    parse_errors = 0
    for item in items:
        item_thought = item.get("thought") or item.get("reflection") if isinstance(item, dict) else thought
        parsed_type, _ = _parse_ui_tars_call(item)
        if parsed_type is None:
            parse_errors += 1
            continue
        actions.append(_normalize_ui_tars_action(item, payload, item_thought))
    coordinate_count = sum(1 for item in actions if item["coordinateStatus"] == "normalized")
    invalid_coordinate_count = sum(1 for item in actions if item["coordinateStatus"] == "out_of_bounds")
    supported_count = sum(1 for item in actions if item["supported"])
    return {
        "adapter": "ui-tars",
        "status": "candidate_actions_ready" if actions else "candidate_actions_incomplete",
        "modelType": _bounded_text(payload.get("modelType") or payload.get("model_type"), 80) or None,
        "sourceFormat": source_format,
        "actions": actions,
        "summary": {
            "actionCount": len(actions),
            "supportedCount": supported_count,
            "coordinateActionCount": coordinate_count,
            "invalidCoordinateCount": invalid_coordinate_count,
            "parseErrorCount": parse_errors,
        },
        "coordinatePolicy": "normalized_0_to_1_out_of_bounds_rejected",
        "actionPolicy": "visual_candidate_only_no_execution",
    }


_PLAYWRIGHT_CLI_WRITE_MARKERS = (
    ".fill(",
    ".type(",
    ".press(",
    ".check(",
    ".uncheck(",
    ".select_option(",
    ".set_input_files(",
    "browser_click",
    "browser_type",
)
_PLAYWRIGHT_CLI_UNSAFE_MARKERS = (
    "run_code",
    "evaluate",
    "javascript:",
    "page.on(",
)


def _playwright_cli_url(value: Any) -> str | None:
    if isinstance(value, str):
        match = re.search(r"https?://[^\s'\")]+", value)
        if match:
            return _bounded_text(match.group(0).split("?", 1)[0].split("#", 1)[0], 2_000)
    return None


def _playwright_cli_target(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(?:locator|get_by_role|get_by_text|get_by_label)\(([^)]{1,240})\)", value)
    if match:
        return _bounded_text(match.group(1), 240)
    return None


def normalize_playwright_cli_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Import Playwright CLI history as bounded evidence without replaying commands."""

    source = payload.get("trace") if isinstance(payload.get("trace"), dict) else payload
    raw_items = source.get("commands") or source.get("events") or source.get("steps") or source.get("history") or []
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("commands") or raw_items.get("events") or raw_items.get("steps") or []
    if not isinstance(raw_items, list):
        raw_items = []
    items: list[dict[str, Any]] = []
    for position, item in enumerate(raw_items[:120]):
        if isinstance(item, str):
            command = _bounded_text(item, 2_000)
            kind = "command"
            url = _playwright_cli_url(command)
            target = _playwright_cli_target(command)
        elif isinstance(item, dict):
            command_value = item.get("command") or item.get("text") or item.get("action")
            command = _bounded_text(command_value, 2_000) if isinstance(command_value, str) else ""
            kind = _bounded_text(item.get("kind") or item.get("type") or item.get("name"), 120).lower() or "event"
            url = _playwright_cli_url(item.get("url")) or _playwright_cli_url(command)
            target = _bounded_text(item.get("target") or item.get("selector"), 240) or _playwright_cli_target(command)
        else:
            continue
        lowered = command.lower()
        if kind in {"command", "event"}:
            if "navigate" in lowered or "goto(" in lowered:
                kind = "navigate"
            elif "snapshot" in lowered or "content(" in lowered:
                kind = "observe"
            elif any(marker in lowered for marker in _PLAYWRIGHT_CLI_WRITE_MARKERS):
                kind = "write_preview"
            elif "click" in lowered or "hover" in lowered:
                kind = "interaction_preview"
        write_like = kind == "write_preview" or any(marker in lowered for marker in _PLAYWRIGHT_CLI_WRITE_MARKERS)
        unsafe = any(marker in lowered for marker in _PLAYWRIGHT_CLI_UNSAFE_MARKERS)
        items.append(
            {
                "index": item.get("step") if isinstance(item, dict) and isinstance(item.get("step"), int) else position,
                "kind": kind,
                "url": url,
                "target": target,
                "writeLike": write_like,
                "unsafe": unsafe,
                "inputRedacted": write_like and any(marker in lowered for marker in (".fill(", ".type(", "browser_type")),
            }
        )
    return {
        "adapter": "playwright-cli",
        "status": "trace_ready" if items else "trace_incomplete",
        "commands": items,
        "summary": {
            "commandCount": len(items),
            "navigationCount": sum(1 for item in items if item["kind"] == "navigate"),
            "writeLikeCount": sum(1 for item in items if item["writeLike"]),
            "unsafeCount": sum(1 for item in items if item["unsafe"]),
        },
        "actionPolicy": "trace_import_only_no_execution",
    }


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _safe_observation_summary(observation: Any) -> dict[str, Any] | None:
    """Summarize BrowserGym observations without retaining screenshots or DOM blobs."""

    if not isinstance(observation, dict):
        return None
    urls = observation.get("open_pages_urls") or observation.get("openPagesUrls")
    titles = observation.get("open_pages_titles") or observation.get("openPagesTitles")
    summary: dict[str, Any] = {
        "url": _bounded_text(observation.get("url"), 2_000) or None,
        "lastAction": _bounded_text(
            observation.get("last_action") or observation.get("lastAction"), 500
        )
        or None,
        "lastActionError": _bounded_text(
            observation.get("last_action_error") or observation.get("lastActionError"), 800
        )
        or None,
        "hasScreenshot": "screenshot" in observation,
        "hasAccessibilityTree": any(
            key in observation for key in ("axtree_object", "axtree", "accessibilityTree")
        ),
        "hasDomObject": any(key in observation for key in ("dom_object", "domObject")),
    }
    if isinstance(urls, list):
        summary["openPageCount"] = min(len(urls), 40)
        summary["openPageUrls"] = [_bounded_text(item, 2_000) for item in urls[:20]]
    if isinstance(titles, list):
        summary["openPageTitles"] = [_bounded_text(item, 500) for item in titles[:20]]
    return summary


def _safe_evaluation(payload: Any) -> dict[str, Any] | None:
    """Keep only evaluator facts that are useful in reports and safe to display."""

    if not isinstance(payload, dict):
        return None
    allowed = (
        "success",
        "passed",
        "score",
        "reward",
        "done",
        "status",
        "message",
        "errorClass",
        "error_class",
    )
    result = {key: _bounded_json(payload[key]) for key in allowed if key in payload}
    return result or None


def normalize_browsergym_trajectory(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize BrowserGym task/env/trajectory evidence without creating an env.

    BrowserGym and AgentLab both persist step records that may contain screenshots,
    DOM trees, Playwright objects, and model metadata.  The product boundary keeps
    only bounded action/reward/lifecycle facts and a small observation summary.
    """

    source = payload.get("episode") if isinstance(payload.get("episode"), dict) else payload
    task = source.get("task") if isinstance(source.get("task"), dict) else {}
    environment = source.get("environment") if isinstance(source.get("environment"), dict) else {}
    summary = source.get("summary") if isinstance(source.get("summary"), dict) else {}
    raw_steps = source.get("steps") or source.get("trajectory") or source.get("steps_info") or []
    if not isinstance(raw_steps, list):
        raw_steps = []

    task_id = (
        source.get("taskId")
        or source.get("task_id")
        or task.get("taskId")
        or task.get("task_id")
        or task.get("name")
    )
    instruction = (
        source.get("instruction")
        or source.get("goal")
        or task.get("instruction")
        or task.get("goal")
    )
    steps: list[dict[str, Any]] = []
    total_reward = 0.0
    total_raw_reward = 0.0
    for position, item in enumerate(raw_steps[:120]):
        if not isinstance(item, dict):
            continue
        reward = _number_or_none(item.get("reward"))
        raw_reward = _number_or_none(item.get("rawReward") or item.get("raw_reward"))
        if isinstance(reward, (int, float)):
            total_reward += reward
        if isinstance(raw_reward, (int, float)):
            total_raw_reward += raw_reward
        action = item.get("action")
        if isinstance(action, (dict, list)):
            action_value: Any = _bounded_json(action)
        else:
            action_value = _bounded_text(action, 2_000) or None
        steps.append(
            {
                "index": item.get("step") if isinstance(item.get("step"), int) else (
                    item.get("index") if isinstance(item.get("index"), int) else position
                ),
                "kind": _bounded_text(item.get("kind"), 80)
                or ("reset" if position == 0 and not action_value else "step"),
                "action": action_value,
                "observation": _safe_observation_summary(
                    item.get("obs") or item.get("observation")
                ),
                "reward": reward,
                "rawReward": raw_reward,
                "terminated": _bool_or_none(item.get("terminated")),
                "truncated": _bool_or_none(item.get("truncated")),
                "hasAgentInfo": isinstance(item.get("agent_info") or item.get("agentInfo"), dict),
                "hasTaskInfo": isinstance(item.get("task_info") or item.get("taskInfo"), dict),
            }
        )

    reported_total = _number_or_none(summary.get("cum_reward") or summary.get("totalReward"))
    reported_raw_total = _number_or_none(
        summary.get("cum_raw_reward") or summary.get("totalRawReward")
    )
    final_step = steps[-1] if steps else {}
    explicit_success = source.get("success")
    if explicit_success is None:
        explicit_success = summary.get("success") or summary.get("passed")
    return {
        "adapter": "browsergym",
        "status": "episode_ready" if (task_id or steps or summary) else "episode_incomplete",
        "task": {
            "id": _bounded_text(task_id, 240) or None,
            "instruction": _bounded_text(instruction, 4_000) or None,
            "seed": task.get("seed") if isinstance(task.get("seed"), int) else source.get("seed"),
        },
        "environment": {
            "name": _bounded_text(
                environment.get("name") or environment.get("taskName") or source.get("envName"),
                240,
            )
            or None,
            "taskName": _bounded_text(
                environment.get("taskName") or source.get("taskName"), 240
            )
            or None,
            "headless": _bool_or_none(environment.get("headless")),
            "maxSteps": environment.get("maxSteps")
            if isinstance(environment.get("maxSteps"), int)
            else None,
        },
        "steps": steps,
        "summary": {
            "stepCount": len(steps),
            "totalReward": reported_total if reported_total is not None else total_reward,
            "totalRawReward": reported_raw_total
            if reported_raw_total is not None
            else total_raw_reward,
            "success": _bool_or_none(explicit_success),
            "terminated": _bool_or_none(
                source.get("terminated") if source.get("terminated") is not None else final_step.get("terminated")
            ),
            "truncated": _bool_or_none(
                source.get("truncated") if source.get("truncated") is not None else final_step.get("truncated")
            ),
        },
        "evaluation": _safe_evaluation(
            source.get("evaluation") or source.get("evaluator") or source.get("result")
        ),
        "actionPolicy": "trajectory_evaluation_only",
    }


def normalize_agentlab_experiment(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize AgentLab experiment metadata and StepInfo evidence read-only."""

    source = payload.get("experiment") if isinstance(payload.get("experiment"), dict) else payload
    exp_args = source.get("exp_args") or source.get("expArgs")
    if not isinstance(exp_args, dict):
        exp_args = {}
    env_args = exp_args.get("env_args") or exp_args.get("envArgs")
    if not isinstance(env_args, dict):
        env_args = {}
    agent_args = exp_args.get("agent_args") or exp_args.get("agentArgs")
    if not isinstance(agent_args, dict):
        agent_args = {}
    summary = source.get("summary_info") or source.get("summaryInfo")
    if not isinstance(summary, dict):
        summary = {}
    raw_steps = source.get("steps_info") or source.get("stepsInfo") or source.get("steps") or []
    if not isinstance(raw_steps, list):
        raw_steps = []
    browsergym = normalize_browsergym_trajectory(
        {
            "taskId": env_args.get("task_name") or env_args.get("taskName"),
            "task": {
                "seed": env_args.get("task_seed") or env_args.get("taskSeed"),
            },
            "environment": {
                "taskName": env_args.get("task_name") or env_args.get("taskName"),
                "headless": env_args.get("headless"),
                "maxSteps": env_args.get("max_steps") or env_args.get("maxSteps"),
            },
            "steps": raw_steps,
            "summary": summary,
            "terminated": summary.get("terminated"),
            "truncated": summary.get("truncated"),
        }
    )
    metrics: dict[str, Any] = {}
    supplied_metrics = source.get("metrics")
    if isinstance(supplied_metrics, dict):
        metrics.update({str(key)[:120]: _bounded_json(value) for key, value in list(supplied_metrics.items())[:50]})
    for key, value in summary.items():
        if str(key).startswith("stats.") or key in {"cum_reward", "cum_raw_reward", "n_steps"}:
            metrics[str(key)[:120]] = _bounded_json(value)
    status = source.get("status") or ("error" if summary.get("err_msg") else None)
    if status is None:
        status = "done" if summary.get("terminated") or summary.get("truncated") else "incomplete"
    artifact_values = source.get("artifacts") or source.get("artifactNames") or []
    if not isinstance(artifact_values, list):
        artifact_values = []
    return {
        "adapter": "agentlab",
        "status": "experiment_ready" if (exp_args or raw_steps or summary) else "experiment_incomplete",
        "runStatus": _bounded_text(status, 120),
        "agent": {
            "name": _bounded_text(
                source.get("agentName") or agent_args.get("agent_name") or agent_args.get("agentName"),
                240,
            )
            or None,
        },
        "task": browsergym["task"],
        "environment": browsergym["environment"],
        "trajectorySummary": browsergym["summary"],
        "metrics": metrics,
        "qualityMetrics": _quality_metrics(summary, metrics, status),
        "artifactNames": [_bounded_text(item, 240) for item in artifact_values[:40] if isinstance(item, (str, int))],
        "actionPolicy": "experiment_metadata_only",
    }


def _quality_metrics(summary: dict[str, Any], metrics: dict[str, Any], status: Any) -> dict[str, Any]:
    """Derive bounded, provider-neutral quality facts from AgentLab output."""

    error = summary.get("err_msg") or summary.get("error")
    terminated = _bool_or_none(summary.get("terminated"))
    truncated = _bool_or_none(summary.get("truncated"))
    if error:
        quality_status = "failed"
    elif terminated is True:
        quality_status = "passed"
    elif truncated is True:
        quality_status = "incomplete"
    else:
        quality_status = "incomplete"
    derived_summary_keys = {"cum_reward", "cum_raw_reward", "n_steps"}
    metric_count = sum(1 for key in metrics if key not in derived_summary_keys)
    return {
        "qualityStatus": quality_status,
        "runStatus": _bounded_text(status, 120) or None,
        "terminated": terminated,
        "truncated": truncated,
        "stepCount": _number_or_none(summary.get("n_steps")) or metrics.get("n_steps"),
        "cumulativeReward": _number_or_none(summary.get("cum_reward")),
        "cumulativeRawReward": _number_or_none(summary.get("cum_raw_reward")),
        "metricCount": metric_count,
        "errorPresent": bool(error),
    }


def normalize_webarena_trajectory(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize WebArena task/trajectory/evaluator records without running sites."""

    source = payload.get("episode") if isinstance(payload.get("episode"), dict) else payload
    raw_steps = source.get("trajectory") or source.get("steps") or source.get("records") or []
    if not isinstance(raw_steps, list):
        raw_steps = []
    steps: list[dict[str, Any]] = []
    total_reward = 0.0
    for position, item in enumerate(raw_steps[:100]):
        if not isinstance(item, dict):
            continue
        reward = _number_or_none(item.get("reward") or item.get("score"))
        if isinstance(reward, (int, float)):
            total_reward += reward
        action = item.get("action") or item.get("action_str") or item.get("command")
        observation = item.get("observation") or item.get("obs") or item.get("state")
        observation_summary = None
        if isinstance(observation, dict):
            observation_summary = {
                "url": _bounded_text(observation.get("url"), 2_000) or None,
                "hasDom": any(key in observation for key in ("dom", "dom_object", "html")),
                "hasScreenshot": any(key in observation for key in ("screenshot", "image")),
            }
        steps.append({
            "index": item.get("step") if isinstance(item.get("step"), int) else position,
            "action": _bounded_json(action),
            "observation": observation_summary,
            "reward": reward,
            "terminated": _bool_or_none(item.get("terminated") or item.get("done")),
            "truncated": _bool_or_none(item.get("truncated")),
            "error": _bounded_text(item.get("error") or item.get("errorClass"), 800) or None,
        })
    summary = source.get("summary") if isinstance(source.get("summary"), dict) else {}
    evaluator = _safe_evaluation(source.get("evaluator") or source.get("evaluation") or source.get("result"))
    explicit_success = source.get("success")
    if explicit_success is None:
        explicit_success = summary.get("success") or summary.get("passed")
    return {
        "adapter": "webarena",
        "status": "trajectory_ready" if (source.get("task_id") or source.get("taskId") or steps or summary) else "trajectory_incomplete",
        "task": {
            "id": _bounded_text(source.get("task_id") or source.get("taskId") or source.get("task"), 240) or None,
            "intent": _bounded_text(source.get("intent") or source.get("goal") or source.get("instruction"), 4_000) or None,
        },
        "environment": {
            "name": _bounded_text(source.get("environment") or source.get("site") or source.get("site_name"), 240) or None,
            "selfHosted": True,
        },
        "steps": steps,
        "summary": {
            "stepCount": len(steps),
            "totalReward": _number_or_none(summary.get("totalReward") or summary.get("reward")) if summary else total_reward,
            "success": _bool_or_none(explicit_success),
            "terminated": _bool_or_none(summary.get("terminated")) if summary else (steps[-1].get("terminated") if steps else None),
            "truncated": _bool_or_none(summary.get("truncated")) if summary else (steps[-1].get("truncated") if steps else None),
        },
        "evaluation": evaluator,
        "actionPolicy": "trajectory_evaluation_only",
    }


def normalize_playwright_mcp_observation(payload: dict[str, Any], *, fallback_url: str = "about:blank") -> Observation:
    """Normalize an MCP browser snapshot without letting it execute actions.

    Playwright MCP has used both text snapshots and structured content across
    versions.  The generic runner only receives bounded, redacted facts here;
    it never receives a callable or an upstream page object.
    """

    structured = payload.get("structuredContent") if isinstance(payload.get("structuredContent"), dict) else {}
    source = {**structured, **payload}
    snapshot = source.get("snapshot") or source.get("accessibilitySnapshot") or source.get("accessibility")
    dom = source.get("domSummary") or source.get("dom") or source.get("elements")
    errors = source.get("errors") if isinstance(source.get("errors"), list) else []
    return Observation(
        url=_bounded_text(source.get("url") or fallback_url, 2_000),
        title=_bounded_text(source.get("title"), 500),
        dom_summary=_bounded_lines(dom),
        accessibility_summary=_bounded_text(snapshot),
        console_errors=_bounded_lines(source.get("consoleErrors")),
        page_errors=_bounded_lines(source.get("pageErrors")),
        failed_requests=_bounded_lines(source.get("failedRequests") or errors),
    )


def normalize_playwright_mcp_tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Turn an MCP tool response into evidence-safe, JSON-serializable data."""

    structured = payload.get("structuredContent")
    text = payload.get("text") or payload.get("content")
    is_error = bool(payload.get("isError") or payload.get("error"))
    return {
        "adapter": "playwright-mcp",
        "ok": not is_error,
        "text": _bounded_text(text),
        "structuredContent": structured if isinstance(structured, (dict, list, str, int, float, bool)) else None,
        "error": _bounded_text(payload.get("error")) if is_error else None,
    }


def _find_playwright_core(root: Path | None) -> Path | None:
    candidates: list[Path] = []
    configured = os.getenv("GUI_AGENT_NODE_MODULES", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    if root:
        candidates.append(root / "playwright-mcp" / "node_modules")
    module_path = Path(__file__).resolve()
    candidates.extend(ancestor / "node_modules" for ancestor in (module_path.parent, *module_path.parents))
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "playwright-core").is_dir():
            return resolved
    return None


class PlaywrightMcpProbeError(RuntimeError):
    """The optional MCP sidecar could not complete a read-only handshake."""


def _mcp_send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if process.stdin is None:
        raise PlaywrightMcpProbeError("MCP sidecar stdin 不可用")
    process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _mcp_handshake(node: str, entrypoint: Path, node_modules: Path, timeout_seconds: float = 8.0) -> dict[str, Any]:
    """Initialize the upstream MCP server and list tools, without browser actions."""

    output: queue.Queue[str] = queue.Queue()
    environment = os.environ.copy()
    environment["NODE_PATH"] = str(node_modules)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [node, str(entrypoint), "--browser", "chromium", "--headless"],
        cwd=str(entrypoint.parent),
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
        creationflags=creation_flags,
    )

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.put(line)

    reader = threading.Thread(target=read_output, name="playwright-mcp-probe", daemon=True)
    reader.start()
    started = time.monotonic()
    try:
        _mcp_send(process, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "jingcai-opc-probe", "version": "1.33.00"},
            },
        })
        initialize: dict[str, Any] | None = None
        tools: dict[str, Any] | None = None
        while time.monotonic() - started < timeout_seconds:
            try:
                line = output.get(timeout=0.25)
            except queue.Empty:
                if process.poll() is not None:
                    raise PlaywrightMcpProbeError(f"MCP sidecar 提前退出（exit={process.returncode}）")
                continue
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == 1:
                initialize = message.get("result")
                _mcp_send(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
                _mcp_send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            elif message.get("id") == 2:
                tools = message.get("result")
                break
        if initialize is None:
            raise PlaywrightMcpProbeError("MCP initialize 未在限定时间内返回")
        if tools is None:
            raise PlaywrightMcpProbeError("MCP tools/list 未在限定时间内返回")
        tool_list = tools.get("tools") if isinstance(tools, dict) else []
        if not isinstance(tool_list, list):
            tool_list = []
        return {
            "serverInfo": initialize.get("serverInfo", {}) if isinstance(initialize, dict) else {},
            "protocolVersion": initialize.get("protocolVersion") if isinstance(initialize, dict) else None,
            "toolCount": len(tool_list),
            "toolNames": [item.get("name") for item in tool_list[:32] if isinstance(item, dict) and item.get("name")],
            "probe": "initialize + tools/list",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        raise PlaywrightMcpProbeError(f"MCP sidecar 启动失败：{type(exc).__name__}") from exc
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


class _PlaywrightMcpSession:
    """Minimal newline JSON-RPC client for the two read-only MCP calls we use."""

    def __init__(self, node: str, entrypoint: Path, node_modules: Path, browser: Path | None, allowed_hosts: tuple[str, ...]) -> None:
        self.node = node
        self.entrypoint = entrypoint
        self.node_modules = node_modules
        self.browser = browser
        self.allowed_hosts = allowed_hosts
        self.process: subprocess.Popen[str] | None = None
        self.output: queue.Queue[str] = queue.Queue()
        self.next_id = 1
        self.evidence: dict[str, Any] = {}

    def start(self) -> dict[str, Any]:
        environment = os.environ.copy()
        environment["NODE_PATH"] = str(self.node_modules)
        args = [self.node, str(self.entrypoint), "--browser", "chromium", "--headless", "--isolated"]
        if self.browser:
            args.extend(["--executable-path", str(self.browser)])
        if self.allowed_hosts:
            args.extend(["--allowed-hosts", *self.allowed_hosts])
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            args,
            cwd=str(self.entrypoint.parent),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creation_flags,
        )

        def read_output() -> None:
            assert self.process is not None and self.process.stdout is not None
            for line in self.process.stdout:
                self.output.put(line)

        threading.Thread(target=read_output, name="playwright-mcp-session", daemon=True).start()
        initialize = self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "jingcai-opc-adapter", "version": "1.33.00"},
        })
        self.notify("notifications/initialized", {})
        tools = self.request("tools/list", {})
        tool_list = tools.get("tools") if isinstance(tools, dict) else []
        tool_list = tool_list if isinstance(tool_list, list) else []
        self.evidence = {
            "serverInfo": initialize.get("serverInfo", {}) if isinstance(initialize, dict) else {},
            "protocolVersion": initialize.get("protocolVersion") if isinstance(initialize, dict) else None,
            "toolCount": len(tool_list),
            "toolNames": [item.get("name") for item in tool_list[:32] if isinstance(item, dict) and item.get("name")],
            "probe": "initialize + tools/list + browser_navigate + browser_snapshot",
        }
        return self.evidence

    def notify(self, method: str, params: dict[str, Any]) -> None:
        if self.process is None:
            raise PlaywrightMcpProbeError("MCP session 尚未启动")
        _mcp_send(self.process, {"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any], timeout_seconds: float = 20.0) -> dict[str, Any]:
        if self.process is None:
            raise PlaywrightMcpProbeError("MCP session 尚未启动")
        request_id = self.next_id
        self.next_id += 1
        _mcp_send(self.process, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                line = self.output.get(timeout=0.25)
            except queue.Empty:
                if self.process.poll() is not None:
                    raise PlaywrightMcpProbeError(f"MCP session 提前退出（exit={self.process.returncode}）")
                continue
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue
            if message.get("error"):
                raise PlaywrightMcpProbeError(_bounded_text(message["error"], 1_000))
            result = message.get("result")
            if not isinstance(result, dict):
                raise PlaywrightMcpProbeError(f"MCP {method} 返回格式不受支持")
            if result.get("isError"):
                raise PlaywrightMcpProbeError(_bounded_text(result, 1_000))
            return result
        raise PlaywrightMcpProbeError(f"MCP {method} 未在限定时间内返回")

    def observe(self, url: str) -> dict[str, Any]:
        navigate = self.request("tools/call", {"name": "browser_navigate", "arguments": {"url": url}})
        snapshot = self.request("tools/call", {"name": "browser_snapshot", "arguments": {}})
        return {"navigate": navigate, "snapshot": snapshot}

    def close(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)


def _find_browser_executable() -> Path | None:
    configured = os.getenv("GUI_AGENT_BROWSER_EXECUTABLE", "").strip()
    if configured and Path(configured).is_file():
        return Path(configured).resolve()
    module_path = Path(__file__).resolve()
    runtime_roots: list[Path] = []
    for ancestor in (module_path.parent, *module_path.parents):
        runtime_roots.extend([ancestor / "runtime" / "ms-playwright", ancestor / "Windows一键运行" / "runtime" / "ms-playwright"])
    for runtime_root in runtime_roots:
        if not runtime_root.is_dir():
            continue
        for candidate in runtime_root.glob("chromium-*/chrome-win/chrome.exe"):
            if candidate.is_file():
                return candidate.resolve()
        for candidate in runtime_root.glob("chromium-*/chrome-linux/chrome"):
            if candidate.is_file():
                return candidate.resolve()
    return None


def _snapshot_observation_payload(result: dict[str, Any], fallback_url: str) -> dict[str, Any]:
    content = result.get("content") if isinstance(result.get("content"), list) else []
    text = "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("type") == "text")
    url_match = re.search(r"Page URL:\s*(\S+)", text)
    title_match = re.search(r"Page Title:\s*(.*)", text)
    return {
        "url": url_match.group(1) if url_match else fallback_url,
        "title": title_match.group(1).strip() if title_match else "",
        "accessibility": _bounded_text(text),
    }


def observe_playwright_mcp_url(url: str, allowed_hosts: tuple[str, ...]) -> dict[str, Any]:
    """Navigate and snapshot one authorized URL through the P0 MCP adapter."""

    from .catalog import _reference_root

    root = _reference_root()
    project = root / "playwright-mcp" if root else None
    node = shutil.which("node")
    node_modules = _find_playwright_core(root)
    entrypoint = project / "cli.js" if project else None
    if not node or not node_modules or not entrypoint or not entrypoint.is_file():
        raise PlaywrightMcpProbeError("Playwright MCP 运行时依赖未就绪，无法执行只读观察")
    session = _PlaywrightMcpSession(node, entrypoint, node_modules, _find_browser_executable(), allowed_hosts)
    try:
        evidence = session.start()
        results = session.observe(url)
        snapshot_result = results["snapshot"]
        normalized = normalize_playwright_mcp_observation(_snapshot_observation_payload(snapshot_result, url), fallback_url=url)
        tool_result = normalize_playwright_mcp_tool_result(snapshot_result)
        evidence = {**evidence, "unsafeTools": [
            item for item in evidence.get("toolNames", [])
            if item in {"browser_evaluate", "browser_run_code_unsafe"}
        ]}
        return {
            "adapter": "playwright-mcp",
            "runtimeEvidence": evidence,
            "observation": normalized.model_dump(mode="json"),
            "toolResult": tool_result,
        }
    finally:
        session.close()


def _probe_playwright_mcp(root: Path | None, runtime: dict[str, Any]) -> dict[str, Any]:
    project = root / "playwright-mcp" if root else None
    checkout = bool(project and project.is_dir() and (project / ".git").exists())
    entrypoint = project / "cli.js" if project else None
    package_json = project / "package.json" if project else None
    node = shutil.which("node") if runtime.get("node", {}).get("available") else None
    node_modules = _find_playwright_core(root)
    reasons: list[str] = []
    evidence: dict[str, Any] | None = None
    if not checkout:
        reasons.append("本地 Playwright MCP 仓库不可见")
    if not entrypoint or not entrypoint.is_file() or not package_json or not package_json.is_file():
        reasons.append("缺少 MCP cli.js 或 package.json")
    if not node:
        reasons.append("未发现 Node.js")
    if not node_modules:
        reasons.append("未找到可用于 MCP sidecar 的 playwright-core 依赖")
    if not reasons and entrypoint and node and node_modules:
        try:
            evidence = _mcp_handshake(node, entrypoint, node_modules)
        except PlaywrightMcpProbeError as exc:
            reasons.append(str(exc))
    runtime_ready = evidence is not None and not reasons
    unsafe_tools = [
        item for item in (evidence or {}).get("toolNames", [])
        if item in {"browser_run_code_unsafe", "browser_evaluate"}
    ]
    safety = "本探针只初始化 MCP 并读取 tools/list，不打开目标网站、不执行浏览器动作"
    if unsafe_tools:
        safety += f"；工具列表含高风险能力 {', '.join(unsafe_tools)}，产品适配器必须过滤并继续经过安全门禁"
        if evidence is not None:
            evidence["unsafeTools"] = unsafe_tools
    return {
        "id": "playwright-mcp-adapter",
        "projectId": "playwright-mcp",
        "name": "Playwright MCP 观察适配器",
        "contractStatus": "contract_ready",
        "runtimeStatus": "runtime_ready" if runtime_ready else "not_ready",
        "runtimeReady": runtime_ready,
        "transport": "stdio sidecar",
        "entrypoint": "cli.js",
        "capabilities": ["MCP 页面观察归一化", "MCP 工具结果证据化", "只读能力探针"],
        "normalization": "Observation + StepResult evidence",
        "configuredCommand": bool(os.getenv("GUI_AGENT_PLAYWRIGHT_MCP_COMMAND", "").strip()),
        "runtimeEvidence": evidence,
        "reasons": reasons or ["MCP initialize 和 tools/list 已通过，可进入受控目标页探针"],
        "safety": safety,
    }


def _probe_node_sidecar(
    *,
    root: Path | None,
    runtime: dict[str, Any],
    project_id: str,
    name: str,
    entrypoint: str,
    package_file: str,
    command_env: str,
    capabilities: list[str],
    normalization: str,
) -> dict[str, Any]:
    project = root / project_id if root else None
    checkout = bool(project and project.is_dir() and (project / ".git").exists())
    entrypoint_ready = bool(project and (project / entrypoint).is_file())
    package_ready = bool(project and (project / package_file).is_file())
    node_available = bool(runtime.get("node", {}).get("available"))
    dependency_ready = bool(project and (project / "node_modules").is_dir())
    configured_command = os.getenv(command_env, "").strip()
    runtime_ready = checkout and entrypoint_ready and package_ready and node_available and (dependency_ready or bool(configured_command))
    reasons: list[str] = []
    if not checkout:
        reasons.append(f"本地 {name} 仓库不可见")
    if not entrypoint_ready or not package_ready:
        reasons.append("缺少已约定的 sidecar 入口或 package.json")
    if not node_available:
        reasons.append("未发现 Node.js")
    if not dependency_ready and not configured_command:
        reasons.append(f"未安装上游 {project_id} 依赖，且未配置受控 sidecar 命令")
    return {
        "id": f"{project_id}-adapter",
        "projectId": project_id,
        "name": name,
        "contractStatus": "contract_ready",
        "runtimeStatus": "runtime_ready" if runtime_ready else "not_ready",
        "runtimeReady": runtime_ready,
        "transport": "stdio sidecar",
        "entrypoint": entrypoint,
        "capabilities": capabilities,
        "normalization": normalization,
        "configuredCommand": bool(configured_command),
        "reasons": reasons or ["sidecar 依赖和受控命令均已满足，可进入真实探针"],
        "safety": "本探针不启动外部进程；真实执行仍需安全门禁、目标授权和动作前后验证",
    }


def _probe_openadapt(root: Path | None, runtime: dict[str, Any]) -> dict[str, Any]:
    project = root / "openadapt" if root else None
    checkout = bool(project and project.is_dir() and (project / ".git").exists())
    entrypoint_ready = bool(project and (project / "openadapt" / "cli.py").is_file())
    python_available = bool(runtime.get("python", {}).get("available"))
    flow_ready = bool(shutil.which("openadapt-flow"))
    configured_command = os.getenv("GUI_AGENT_OPENADAPT_COMMAND", "").strip()
    runtime_ready = checkout and entrypoint_ready and python_available and (flow_ready or bool(configured_command))
    reasons: list[str] = []
    if not checkout:
        reasons.append("本地 OpenAdapt 仓库不可见")
    if not entrypoint_ready:
        reasons.append("缺少 openadapt/cli.py 入口")
    if not python_available:
        reasons.append("未发现 Python 运行时")
    if not flow_ready and not configured_command:
        reasons.append("未发现外部 openadapt-flow，且未配置受控工作流命令")
    return {
        "id": "openadapt-adapter",
        "projectId": "openadapt",
        "name": "OpenAdapt 工作流证据适配器",
        "contractStatus": "contract_ready",
        "runtimeStatus": "runtime_ready" if runtime_ready else "not_ready",
        "runtimeReady": runtime_ready,
        "transport": "workflow package",
        "entrypoint": "openadapt/cli.py",
        "capabilities": ["工作流检查点", "暂停/恢复", "回放证据", "certify 状态归一化"],
        "normalization": "checkpoint + evidence package",
        "configuredCommand": bool(configured_command),
        "reasons": reasons or ["外部工作流引擎和受控命令均已满足，可进入真实探针"],
        "safety": "本探针不启动外部进程；真实回放必须经过动作权限和结果证据校验",
    }


def probe_adapter_contracts(root: Path | None, runtime: dict[str, Any]) -> list[dict[str, Any]]:
    """Probe P0 adapter contracts; only Playwright MCP performs a read-only handshake."""

    return [
        _probe_playwright_mcp(root, runtime),
        _probe_node_sidecar(
            root=root,
            runtime=runtime,
            project_id="stagehand",
            name="Stagehand 候选动作适配器",
            entrypoint="packages/core/lib/inference.ts",
            package_file="packages/core/package.json",
            command_env="GUI_AGENT_STAGEHAND_COMMAND",
            capabilities=["observe 候选动作", "act 前预览", "extract 结构化结果", "动作缓存证据"],
            normalization="candidate action + extraction evidence",
        ),
        _probe_openadapt(root, runtime),
    ]


def evaluation_contracts_payload(runtime_status: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Describe P1 evaluation contracts without claiming their upstream runners are installed."""

    contracts = [
        {
            "id": "browsergym-evaluator",
            "projectId": "browsergym",
            "name": "BrowserGym 任务轨迹评测契约",
            "contractStatus": "contract_ready",
            "runtimeStatus": "not_ready",
            "runtimeReady": False,
            "transport": "Python evaluation provider",
            "entrypoint": "browsergym/core/env.py + experiments/loop.py",
            "capabilities": ["task/seed", "reset/step", "reward/terminated", "trajectory evidence"],
            "normalization": "task + environment + bounded trajectory + evaluator facts",
            "configuredCommand": False,
            "reasons": ["已完成产品侧只读数据契约；尚未启动 BrowserGym 上游环境"],
            "safety": "只读取任务/轨迹/评测事实，不在目录探针中创建浏览器环境或执行动作",
        },
        {
            "id": "agentlab-experiment",
            "projectId": "agentlab",
            "name": "AgentLab 实验轨迹评测契约",
            "contractStatus": "contract_ready",
            "runtimeStatus": "not_ready",
            "runtimeReady": False,
            "transport": "Python experiment provider",
            "entrypoint": "agentlab/experiments/loop.py",
            "capabilities": ["ExpArgs", "StepInfo", "summary_info", "metrics/artifacts"],
            "normalization": "experiment metadata + trajectory summary + metrics",
            "configuredCommand": False,
            "reasons": ["已完成产品侧只读数据契约；尚未启动 AgentLab 上游实验"],
            "safety": "只读取实验元数据和证据摘要，不在目录探针中启动 Agent、浏览器或模型调用",
        },
        {
            "id": "webarena-evaluator",
            "projectId": "webarena",
            "name": "WebArena task/trajectory evaluator contract",
            "contractStatus": "contract_ready",
            "runtimeStatus": "not_ready",
            "runtimeReady": False,
            "transport": "archived evaluation provider",
            "entrypoint": "browser_env/envs.py + trajectory.py + evaluator",
            "capabilities": ["task intent", "site/environment", "Playwright Script action", "trajectory", "evaluator"],
            "normalization": "self-hosted task + bounded trajectory + evaluator facts",
            "configurationEndpoint": "/api/opensource/evaluation/webarena/configuration",
            "trajectoryEndpoint": "/api/opensource/evaluation/webarena/trajectory",
            "configImportEndpoint": "/api/opensource/evaluation/webarena/import-config",
            "siteHealthEndpoint": "/api/opensource/evaluation/webarena/site-health",
            "configuredCommand": False,
            "reasons": ["Self-hosted configuration and trajectory boundary are available; WebArena sites are still required for upstream execution"],
            "safety": "Read-only normalization only; no WebArena site or browser is started by the catalog probe",
        },
        {
            "id": "browser-use-state",
            "projectId": "browser-use",
            "name": "Browser-use Agent 状态契约",
            "contractStatus": "contract_ready",
            "runtimeStatus": "not_ready",
            "runtimeReady": False,
            "transport": "Python sidecar evidence import",
            "entrypoint": "browser_use/agent/service.py + browser_use/browser/session.py",
            "capabilities": ["goal/current URL", "history/steps", "next action preview", "sensitive-field redaction"],
            "normalization": "bounded agent state + redacted evidence",
            "configuredCommand": False,
            "reasons": ["Product-side read-only state adapter and fixture are ready; Browser-use sidecar remains optional"],
            "safety": "State preview only; no Browser-use model, browser session, or upstream action is started by the catalog probe",
        },
        {
            "id": "ui-tars-visual-action",
            "projectId": "ui-tars",
            "name": "UI-TARS 视觉动作契约",
            "contractStatus": "contract_ready",
            "runtimeStatus": "not_ready",
            "runtimeReady": False,
            "transport": "visual-model evidence import",
            "entrypoint": "codes/ui_tars/action_parser.py",
            "capabilities": ["AST literal parsing", "normalized coordinates", "out-of-bounds rejection", "input redaction"],
            "normalization": "visual action candidate + safe coordinate evidence",
            "configuredCommand": False,
            "reasons": ["Product-side read-only visual action adapter and fixture are ready; model/screenshot authorization remains external"],
            "safety": "Candidate preview only; no screenshot, visual model, pyautogui, or desktop/browser action is started by the catalog probe",
        },
        {
            "id": "playwright-cli-trace",
            "projectId": "playwright-cli",
            "name": "Playwright CLI 轨迹契约",
            "contractStatus": "contract_ready",
            "runtimeStatus": "not_ready",
            "runtimeReady": False,
            "transport": "CLI trace evidence import",
            "entrypoint": "skills/ + scripts/ + package.json",
            "capabilities": ["command classification", "navigation/locator facts", "URL query stripping", "input-value omission"],
            "normalization": "trace facts + write/unsafe classification",
            "configuredCommand": False,
            "reasons": ["Product-side read-only trace importer and fixture are ready; Playwright CLI process/replay remains optional"],
            "safety": "Trace import only; no Playwright CLI process, navigation, replay, or write action is started by the catalog probe",
        },
    ]
    provider_status = {
        item.get("provider"): item
        for item in (runtime_status or {}).get("providers", [])
        if isinstance(item, dict)
    }
    for contract in contracts:
        status = provider_status.get(contract["projectId"])
        if not status or not status.get("runtimeReady"):
            continue
        contract["runtimeStatus"] = "runtime_ready"
        contract["runtimeReady"] = True
        contract["configuredCommand"] = True
        contract["reasons"] = ["隔离上游模块探针通过；可运行产品拥有的本地契约"]
    return contracts


def adapter_catalog_payload(root: Path | None, runtime: dict[str, Any]) -> dict[str, Any]:
    adapters = probe_adapter_contracts(root, runtime)
    runtime_status = None
    try:
        from .runtime import open_source_runtime_status

        runtime_status = open_source_runtime_status()
    except Exception:
        runtime_status = None
    evaluation_contracts = evaluation_contracts_payload(runtime_status)
    return {
        "contractVersion": "1",
        "adapters": adapters,
        "evaluationContracts": evaluation_contracts,
        "summary": {
            "contractReady": sum(item["contractStatus"] == "contract_ready" for item in adapters),
            "runtimeReady": sum(item["runtimeReady"] for item in adapters),
            "blocked": sum(not item["runtimeReady"] for item in adapters),
        },
    }
