"""Run-scoped user authorization for persistent browser side effects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..domain.models import ActionType, Step


_CREATE_DENIALS = (
    r"(?:不|不要|不允许|禁止|不得|不能|无需|无须|拒绝)(?:实际)?(?:创建|新建)",
    r"(?:创建|新建)(?:前|之前)停止",
    r"(?:只|仅)(?:检查|查看|探索|测试)(?:创建|新建)(?:流程|页面|按钮)",
    r"(?:不|不要|不得|禁止|无需|无须)(?:点击|按下|提交|确认)(?:[^，。；,\n]{0,16})?(?:创建|新建)",
    r"(?:不|不要|不得|禁止|无需|无须)(?:点击|按下|提交|确认)",
    r"(?:创建|新建)(?:按钮|步骤|页面|流程)?(?:前|之前)(?:停止|结束|退出)",
    r"do\s+not\s+create",
    r"don't\s+create",
    r"without\s+creat(?:e|ing)",
    r"creation\s+is\s+not\s+allowed",
    r"(?:不|不要|不允许|禁止|不得|不能|无需|无须|拒绝)(?:实际)?(?:保存|提交)(?:想定|场景)",
    r"(?:不|不要|不得|禁止|无需|无须)(?:点击|按下|提交|确认)(?:[^，。；,\n]{0,16})?(?:保存想定|保存场景)",
)

_CREATE_GRANTS = (
    # Direct Chinese imperatives are grants too; do not require polite words
    # such as 请/授权. Anchor at a clause boundary so inspecting a create flow
    # is not mistaken for permission to persist it.
    r"(?:^|[，。；,;\n])\s*(?:创建|新建)(?:一个|一份|独立|测试|名称为)",
    r"(?:明确允许|允许|授权|同意)(?:[^，。；,\n]{0,16})?(?:实际|最终|确认)?(?:创建|新建)",
    r"(?:请|必须)(?:直接|实际|最终|确认)?(?:创建|新建)",
    r"(?:实际创建|最终创建|确认创建|完成创建|立即创建)",
    r"(?:点击|按下|提交|确认)(?:[^，。；,\n]{0,12})?(?:创建|新建)",
    r"(?:allow|authorize|please|must|actually)\s+(?:the\s+agent\s+to\s+)?creat(?:e|ion)",
    r"creat(?:e|ing)\s+(?:a|an|the|one|test|new)\b",
    r"(?:明确允许|允许|授权|同意)(?:[^，。；,\n]{0,16})?(?:实际|最终|确认)?(?:保存|提交)(?:想定|场景)",
    r"(?:请|必须)(?:直接|实际|最终|确认)?(?:保存|提交)(?:想定|场景)",
    r"(?:实际保存|最终保存|确认保存|保存并提交)(?:想定|场景)",
)

_CREATE_SCOPE_GUARDS = (
    r"(?:不得|不要|禁止|不允许)(?:[^，。；,\n]{0,24})?重复创建(?:[^，。；,\n]{0,24})?",
    r"(?:不得|不要|禁止|不允许)(?:[^，。；,\n]{0,24})?创建(?:第二个|多个)(?:[^，。；,\n]{0,24})?",
    r"do\s+not\s+(?:create\s+)?(?:a\s+)?second|do\s+not\s+create\s+duplicates?",
    # These phrases require the write to happen.  Their inner "不提交/不点击"
    # must not be interpreted as a denial of the enclosing create instruction.
    r"(?:禁止|不得|不允许|不能)(?:[^，。；,\n]{0,32})?不(?:点击|按下|提交|确认)(?:[^，。；,\n]{0,16})?",
)

_FINAL_CREATE_LABELS = {
    "创建",
    "确认创建",
    "立即创建",
    "完成创建",
    "保存并创建",
    "create",
    "confirm create",
    "create now",
    "创建想定",
    "保存想定",
    "确认保存",
    "create scenario",
    "save scenario",
}


@dataclass(frozen=True)
class TaskAuthorization:
    create_allowed: bool
    source: str
    evidence: str

    def as_context(self) -> dict[str, Any]:
        return {
            "createAllowed": self.create_allowed,
            "source": self.source,
            "evidence": self.evidence,
            "scope": "current_run_only",
        }


def derive_task_authorization(scenario: Any | None) -> TaskAuthorization:
    """Derive permission only from the current user's run instructions.

    Site capability packs and persisted project facts are intentionally not
    considered. Ambiguous creation wording fails closed.
    """

    if scenario is None:
        return TaskAuthorization(False, "not_provided", "No current-run scenario")

    current_user_text: list[tuple[str, str]] = []
    for field_name in ("goal", "preconditions"):
        value = str(getattr(scenario, field_name, "") or "").strip()
        if value:
            current_user_text.append((field_name, value))
    for value in getattr(scenario, "expected_results", []) or []:
        text = str(value or "").strip()
        if text:
            current_user_text.append(("expected_results", text))
    for item in getattr(scenario, "clarification_history", []) or []:
        if not isinstance(item, dict):
            continue
        answer = str(item.get("answer") or "").strip()
        if answer:
            current_user_text.append(("clarification_answer", answer))

    for source, text in current_user_text:
        lowered = text.lower()
        for guard in _CREATE_SCOPE_GUARDS:
            lowered = re.sub(guard, " ", lowered, flags=re.IGNORECASE)
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in _CREATE_DENIALS):
            return TaskAuthorization(False, source, "Current-run instruction explicitly denies creation")

    for source, text in current_user_text:
        lowered = text.lower()
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in _CREATE_GRANTS):
            return TaskAuthorization(True, source, "Current-run instruction explicitly authorizes creation")

    return TaskAuthorization(False, "not_provided", "Creation was not explicitly authorized for this run")


def is_creation_step(step: Step) -> bool:
    """Recognize a final persistent create action even if model metadata is incomplete."""

    if str(step.action_category or "").strip().lower() == "create":
        return True
    if "create" in str(step.effect_kind or "").strip().lower():
        return True
    if step.action not in {ActionType.CLICK, ActionType.PRESS, ActionType.COMPONENT}:
        return False

    locator_values: list[str] = []
    if step.locator is not None:
        locator_values.extend(
            str(value or "").strip().lower()
            for value in (
                step.locator.name,
                step.locator.text,
                step.locator.label,
                step.locator.placeholder,
            )
            if str(value or "").strip()
        )
    if any(value in _FINAL_CREATE_LABELS for value in locator_values):
        return True

    description = str(step.description or "").strip().lower()
    if re.search(r"(?:打开|进入|查看|检查|探索)(?:[^，。；,\n]{0,16})?(?:创建|新建)(?:向导|页面|流程)", description):
        return False
    return bool(re.search(
        r"(?:最终|确认|提交|点击|完成|实际)(?:[^，。；,\n]{0,16})?(?:创建|新建)|"
        r"(?:最终|确认|提交|点击|完成|实际)(?:[^，。；,\n]{0,16})?(?:保存想定|创建想定)|"
        r"(?:final|confirm|submit|complete)\s+creat(?:e|ion)",
        description,
        flags=re.IGNORECASE,
    ))
