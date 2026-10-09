"""Dangerous-action classification shared by fixed and Agent execution."""

from __future__ import annotations

import re

from ..domain.models import Step
from ..benchmarks.cesium_ion.policy import cesium_confirmation_rule


DEFAULT_CONFIRMATION_ACTIONS = (
    "删除", "退款", "支付", "付款", "提交订单", "提交生产表单", "发布", "发送邀请",
    "delete", "refund", "pay", "purchase", "checkout", "submit", "publish", "invite",
)

_NEGATION_MARKERS = (
    "do not", "don't", "dont", "never", "not", "without", "avoid",
    "禁止", "不要", "不允许", "不得", "无需", "不会", "不执行", "避免",
)


def _is_negated(text: str, term: str) -> bool:
    """Treat a dangerous word as intent only when it is not negated nearby."""

    normalized = text.lower()
    start = 0
    found_negated = False
    while True:
        index = normalized.find(term, start)
        if index < 0:
            return found_negated
        prefix = normalized[max(0, index - 48):index]
        chinese_clause = re.split(r"[。；;！？!?\n]", prefix)[-1]
        chinese_list_negated = bool(re.search(
            r"(?:禁止|不要|不得|不允许|不执行|不进行|不触碰|避免|不)"
            r"[^。；;！？!?\n]{0,40}$",
            chinese_clause,
        ))
        if not any(marker in prefix for marker in _NEGATION_MARKERS) and not chinese_list_negated:
            return False
        found_negated = True
        start = index + len(term)


def _intent_text(step: Step) -> str:
    """Collect intent fields while excluding user-entered values."""

    locator = step.locator
    locator_values = [] if locator is None else [
        locator.name, locator.text, locator.label, locator.placeholder,
        locator.href, locator.css,
    ]
    return " ".join(str(item) for item in (
        step.action_category,
        step.object_type,
        step.description,
        step.target,
        step.business_object_name,
        *locator_values,
    ) if item).lower()


def confirmation_match(step: Step) -> str | None:
    cesium_rule = cesium_confirmation_rule(step)
    if cesium_rule:
        return cesium_rule
    if step.action.value == "human_takeover":
        return f"human_takeover:{step.takeover_reason or 'other'}"
    if step.commerce is not None and step.commerce.action.value not in {
        "browse", "search", "filter", "sort", "paginate", "view_product",
        "view_account_structure", "view_help", "change_region",
    }:
        return f"commerce:{step.commerce.action.value}"
    intent = _intent_text(step)
    for term in DEFAULT_CONFIRMATION_ACTIONS:
        if term in intent and not _is_negated(intent, term):
            return term
    return None
