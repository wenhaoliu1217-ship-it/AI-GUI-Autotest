"""Fail-closed parameterized form actions for the fixed intranet capability.

The router is driven by current semantic controls and current-run test data.
It deliberately supports only ordinary, uniquely named inputs and final save
buttons; complex components remain with the configured external model.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..domain.models import ActionType, EffectLevel, Locator, Step
from ..domain.results import Observation, Status, StepResult
from .intranet_intent import IntranetTaskIntent


_BLOCKED_KEY_TERMS = (
    "password", "passwd", "secret", "token", "credential", "apikey",
    "密码", "密钥", "令牌", "凭据",
)
_UNSAFE_FIELD_TERMS = (
    "密码", "密钥", "token", "secret", "上传", "文件", "验证码", "captcha",
)
_SAVE_LABELS = ("保存", "确认保存", "提交", "完成")
_CREATE_LABELS = ("创建", "确认创建", "完成创建", "保存想定", "创建想定")
_EXCLUDED_STAGES = {
    None, "unauthenticated", "authenticated", "model_list", "scenario_list", "run",
}

_KEY_LABELS: dict[str, tuple[str, ...]] = {
    "scenarioname": ("想定名称", "场景名称", "名称"),
    "planname": ("想定名称", "场景名称", "名称"),
    "modelname": ("模型名称", "智能体名称", "名称"),
    "agentname": ("模型名称", "智能体名称", "名称"),
    "resourcename": ("资源名称", "名称"),
    "description": ("描述", "说明"),
    "remark": ("备注",),
    "pathname": ("路径名称", "任务路径名称"),
    "speed": ("速度",),
    "duration": ("持续时间", "时长"),
}


def next_parameterized_form_action(
    observation: Observation,
    history: list[StepResult],
    scenario: Any,
    intent: IntranetTaskIntent,
    *,
    stage: str | None,
    internal_name: Callable[[str], str],
) -> Step | None:
    """Return one deterministic fill/save action or ``None`` on ambiguity."""

    if stage in _EXCLUDED_STAGES or intent.delete_requested:
        return None
    semantic = observation.semantic_summary
    if semantic is None:
        return None

    bindings = _parameter_bindings(scenario, intent)
    for control in semantic.controls:
        if not _fillable_control(control):
            continue
        control_name = str(control.get("name") or "").strip()
        value = _value_for_control(control_name, bindings)
        if value is None or _field_already_attempted(history, control_name):
            continue
        # Multiple controls with the same semantic name are ambiguous even if
        # one happens to appear first in the bounded observation.
        matches = [
            item for item in semantic.controls
            if _fillable_control(item)
            and _normalize(str(item.get("name") or "")) == _normalize(control_name)
        ]
        if len(matches) != 1:
            return None
        return Step(
            action=ActionType.FILL,
            locator=_locator_from_control(control),
            value=value,
            description=f"按本次任务参数填写字段「{control_name}」",
            effect_level=EffectLevel.SESSION_ONLY,
            resource_name=intent.target_name,
            object_type=intent.object_kind if intent.object_kind != "unknown" else None,
        )

    if not _has_successful_form_fill(history, observation) or _save_already_succeeded(history):
        return None
    if any(
        bool(item.get("required"))
        and (str(item.get("valueState") or "") == "empty" or bool(item.get("invalid")))
        for item in semantic.controls
        if str(item.get("role") or "").casefold() in {"textbox", "spinbutton", "combobox"}
    ):
        return None

    create = intent.create_requested
    if create:
        # Lazy import avoids a package initialization cycle:
        # planning.agent_planner resolves site capability packs.
        from ..planning.task_authorization import derive_task_authorization

        if not derive_task_authorization(scenario).create_allowed:
            return None
    requested_save = create or any(
        operation in intent.operations for operation in ("modify", "save")
    )
    if not requested_save or not intent.target_name or intent.object_kind == "unknown":
        return None
    labels = _CREATE_LABELS if create else _SAVE_LABELS
    candidates = [
        item for item in semantic.controls
        if str(item.get("role") or "").casefold() == "button"
        and str(item.get("name") or "").strip() in labels
        and not bool(item.get("disabled"))
    ]
    if len(candidates) != 1:
        return None
    label = str(candidates[0].get("name") or "")
    operation = "create" if create else "update"
    return Step(
        action=ActionType.CLICK,
        locator=_locator_from_control(candidates[0]),
        description=f"{label}本次任务中对 {intent.target_name} 的参数化变更",
        effect_level=EffectLevel.REVERSIBLE_WRITE,
        effect_kind=f"{operation}_{intent.object_kind}",
        action_category=operation,
        object_type=intent.object_kind,
        business_object_name=internal_name(f"{intent.object_kind}:{intent.target_name}"),
        resource_name=intent.target_name,
    )


def _parameter_bindings(scenario: Any, intent: IntranetTaskIntent) -> list[tuple[set[str], str]]:
    bindings: list[tuple[set[str], str]] = []
    test_data = getattr(scenario, "test_data", {})
    if isinstance(test_data, dict):
        for raw_key, raw_value in test_data.items():
            key = str(raw_key or "").strip()
            normalized_key = _normalize(key)
            if not normalized_key or any(term in normalized_key for term in _BLOCKED_KEY_TERMS):
                continue
            if not isinstance(raw_value, (str, int, float, bool)):
                continue
            value = str(raw_value).strip()
            if not value or len(value) > 1000:
                continue
            labels = {_normalize(key)}
            labels.update(_normalize(item) for item in _KEY_LABELS.get(normalized_key, ()))
            bindings.append((labels, value))

    if intent.target_name:
        name_labels = {
            "scenario": ("想定名称", "场景名称", "名称"),
            "model": ("模型名称", "智能体名称", "名称"),
            "unknown": ("资源名称",),
        }[intent.object_kind]
        bindings.insert(0, ({_normalize(item) for item in name_labels}, intent.target_name))
    return bindings


def _value_for_control(name: str, bindings: list[tuple[set[str], str]]) -> str | None:
    normalized = _normalize(name)
    matches = [
        value
        for labels, value in bindings
        if any(_field_label_matches(normalized, label) for label in labels)
    ]
    return matches[0] if len(set(matches)) == 1 else None


def _field_label_matches(control_name: str, label: str) -> bool:
    if control_name == label:
        return True
    prompt_prefixes = ("请输入", "请填写", "请录入", "pleaseenter", "enter")
    return any(control_name == f"{prefix}{label}" for prefix in prompt_prefixes)


def _fillable_control(control: dict[str, str | bool]) -> bool:
    role = str(control.get("role") or "").casefold()
    name = str(control.get("name") or "").strip()
    return bool(
        role in {"textbox", "spinbutton"}
        and name
        and not bool(control.get("disabled"))
        and str(control.get("valueState") or "") == "empty"
        and not bool(control.get("invalid"))
        and "搜索" not in name
        and "search" not in name.casefold()
        and not any(term.casefold() in name.casefold() for term in _UNSAFE_FIELD_TERMS)
    )


def _locator_from_control(control: dict[str, str | bool]) -> Locator:
    runtime_id = str(control.get("runtimeId") or control.get("runtime_id") or "")
    role = str(control.get("role") or "textbox")
    name = str(control.get("name") or "")
    if re.fullmatch(r"ai_[0-9]+", runtime_id):
        return Locator(runtime_id=runtime_id, role=role, name=name)
    return Locator(role=role, name=name)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())


def _field_already_attempted(history: list[StepResult], name: str) -> bool:
    marker = f"字段「{name}」"
    return any(
        item.action == ActionType.FILL.value
        and marker in " ".join((item.description or "", item.target_summary or ""))
        for item in history
    )


def _has_successful_form_fill(history: list[StepResult], observation: Observation) -> bool:
    current_page = _page_identity(observation.url)
    return any(
        item.status == Status.PASSED
        and item.action == ActionType.FILL.value
        and "按本次任务参数填写字段「" in (item.description or "")
        and getattr(item, "after", None) is not None
        and _page_identity(item.after.url) == current_page
        for item in history
    )


def _save_already_succeeded(history: list[StepResult]) -> bool:
    return any(
        item.status == Status.PASSED
        and item.action == ActionType.CLICK.value
        and "参数化变更" in (item.description or "")
        for item in history
    )


def _page_identity(url: str) -> str:
    return str(url or "").split("?", 1)[0].rstrip("/").casefold()
