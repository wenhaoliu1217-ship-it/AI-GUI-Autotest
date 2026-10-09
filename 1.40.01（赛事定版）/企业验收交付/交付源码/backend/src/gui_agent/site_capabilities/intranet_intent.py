"""Compile bounded GAEALaViC intranet intent from natural-language tasks.

This module extracts runtime parameters; it does not contain a script per
resource name.  Ambiguous instructions deliberately produce no target so the
configured external planner remains authoritative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


ObjectKind = Literal["scenario", "model", "unknown"]

_SCENARIO_TERMS = ("想定", "场景", "scenario", "plan")
_MODEL_TERMS = ("建模", "模型", "智能体", "model", "agent")
_CREATE_TERMS = ("创建", "新建", "新增", "create", "new ", "add ")
_DELETE_TERMS = ("删除", "移除", "销毁", "delete", "remove", "destroy")
_EXISTING_TERMS = (
    "找到", "搜索", "查找", "打开", "进入", "选择", "修改", "编辑", "保存",
    "启动", "停止", "运行", "find", "search", "open", "enter", "select",
    "modify", "edit", "save", "start", "stop", "run",
)
_GENERIC_NAMES = {
    "想定", "场景", "模型", "智能体", "建模", "测试", "页面", "列表",
    "scenario", "model", "agent", "test", "page", "list",
}


@dataclass(frozen=True)
class IntranetTaskIntent:
    object_kind: ObjectKind
    target_name: str | None
    operations: tuple[str, ...]
    create_requested: bool
    delete_requested: bool
    existing_object_requested: bool

    @property
    def safe_for_local_routing(self) -> bool:
        return bool(
            self.target_name
            and self.existing_object_requested
            and not self.create_requested
            and not self.delete_requested
        )


def compile_intranet_intent(scenario: Any) -> IntranetTaskIntent:
    """Extract stable task parameters without guessing an object name."""

    goal = str(getattr(scenario, "goal", "") or "").strip()
    scenario_name = str(getattr(scenario, "name", "") or "").strip()
    text = "\n".join(value for value in (goal, scenario_name) if value)
    lowered = text.casefold()
    object_kind: ObjectKind = "unknown"
    if _contains_any(lowered, _SCENARIO_TERMS):
        object_kind = "scenario"
    elif _contains_any(lowered, _MODEL_TERMS):
        object_kind = "model"

    create_requested = _contains_any(lowered, _CREATE_TERMS)
    delete_requested = _contains_any(lowered, _DELETE_TERMS)
    operations = tuple(
        operation
        for operation, terms in (
            ("find", ("找到", "搜索", "查找", "find", "search")),
            ("open", ("打开", "进入", "open", "enter")),
            ("modify", ("修改", "编辑", "modify", "edit")),
            ("save", ("保存", "save")),
            ("start", ("启动", "运行", "start", "run")),
            ("stop", ("停止", "stop")),
            ("delete", _DELETE_TERMS),
        )
        if _contains_any(lowered, terms)
    )
    # The live goal is authoritative. UI forms may retain testData from a
    # previous run, so only consult it when this goal names no exact target.
    target = _target_from_text(goal, object_kind)
    if not target:
        target = _target_from_test_data(scenario, object_kind)
    existing_requested = bool(target and _contains_any(lowered, _EXISTING_TERMS))
    return IntranetTaskIntent(
        object_kind=object_kind,
        target_name=target,
        operations=operations,
        create_requested=create_requested,
        delete_requested=delete_requested,
        existing_object_requested=existing_requested,
    )


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.casefold() in text for term in terms)


def _clean_name(value: object) -> str | None:
    candidate = str(value or "").strip().strip("\"'“”‘’")
    candidate = re.sub(r"\s+", " ", candidate)
    if not candidate or len(candidate) > 120:
        return None
    if candidate.casefold() in _GENERIC_NAMES:
        return None
    generic_prefixes = (
        "指定", "当前", "已有", "指定想定", "指定场景", "指定模型", "指定智能体", "指定的想定", "指定的场景",
        "指定的模型", "指定的智能体", "一个想定", "一个场景",
        "一个模型", "一个智能体", "已存在的", "已经存在的", "对应的",
    )
    if candidate.casefold().startswith(tuple(item.casefold() for item in generic_prefixes)):
        return None
    if any(char in candidate for char in "\r\n/\\?#"):
        return None
    return candidate


def _target_from_test_data(scenario: Any, object_kind: ObjectKind) -> str | None:
    test_data = getattr(scenario, "test_data", {})
    if not isinstance(test_data, dict):
        return None
    kind_keys = {
        "scenario": ("scenarioName", "scenario_name", "planName", "plan_name", "想定名称", "场景名称"),
        "model": ("modelName", "model_name", "agentName", "agent_name", "模型名称", "智能体名称"),
        "unknown": (),
    }[object_kind]
    for key in (*kind_keys, "resourceName", "resource_name", "资源名称", "targetName", "target_name"):
        candidate = _clean_name(test_data.get(key))
        if candidate:
            return candidate
    return None


def _target_from_text(text: str, object_kind: ObjectKind) -> str | None:
    labels = {
        "scenario": r"想定名称|场景名称|scenario name|plan name",
        "model": r"模型名称|智能体名称|model name|agent name",
        "unknown": r"资源名称|目标名称|resource name|target name",
    }[object_kind]
    labeled = re.search(
        rf"(?:{labels}|资源名称|目标名称|命名为)\s*(?:为|是|使用|[:：=])?\s*"
        r"(?:[\"'“](?P<quoted>[^\"'”\r\n]{1,120})[\"'”]|"
        r"(?P<plain>[A-Za-z0-9_\- .（）()\u4e00-\u9fff]{1,120}))",
        text,
        flags=re.IGNORECASE,
    )
    if labeled:
        raw = labeled.group("quoted") or labeled.group("plain") or ""
        # An unquoted label stops at the next instruction connector.
        raw = re.split(r"\s*(?:，|,|。|；|;|然后|并)\s*", raw, maxsplit=1)[0]
        candidate = _clean_name(raw)
        if candidate:
            return candidate

    exact_test_names = list(dict.fromkeys(re.findall(
        r"\b(?:scenario_)?test_[A-Z]+\b", text
    )))
    if len(exact_test_names) == 1:
        return exact_test_names[0]

    # Common identifiers remain fully dynamic; there is no test_D allowlist.
    identifiers = list(dict.fromkeys(re.findall(
        r"\b(?:scenario_)?test(?:[_ -][A-Za-z0-9]+)+\b|\b[A-Za-z][A-Za-z0-9_-]{2,119}\b",
        text,
        flags=re.IGNORECASE,
    )))
    ignored = {"find", "search", "open", "enter", "scenario", "model", "agent", "start", "stop", "save", "run"}
    identifiers = [item for item in identifiers if item.casefold() not in ignored]
    if len(identifiers) == 1:
        return _clean_name(identifiers[0])

    verb_target = re.search(
        r"(?:找到|搜索|查找|打开|选择|修改|编辑)\s*"
        r"[\"'“]?(?P<name>[^，,。；;\r\n]{1,120}?)[\"'”]?"
        r"(?=\s*(?:，|,|。|；|;|然后|并|进入|打开|$))",
        text,
        flags=re.IGNORECASE,
    )
    return _clean_name(verb_target.group("name")) if verb_target else None
