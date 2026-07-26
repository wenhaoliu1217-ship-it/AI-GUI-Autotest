"""Dangerous-action classification shared by fixed and Agent execution."""

from __future__ import annotations

import json

from ..domain.models import Step
from ..benchmarks.cesium_ion.policy import cesium_confirmation_rule


DEFAULT_CONFIRMATION_ACTIONS = (
    "删除", "退款", "支付", "付款", "提交订单", "提交生产表单", "发布", "发送邀请",
    "delete", "refund", "pay", "purchase", "checkout", "submit", "publish", "invite",
)


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
    # Human-readable descriptions commonly state safety boundaries such as
    # "do not delete". Classify the actual target and structured side-effect
    # metadata instead of treating those negated instructions as an action.
    serialized = json.dumps(
        step.model_dump(
            mode="json",
            exclude_none=True,
            exclude={"description", "stability_reason", "visual_expected_change", "cleanup_action"},
        ),
        ensure_ascii=False,
    ).lower()
    return next((term for term in DEFAULT_CONFIRMATION_ACTIONS if term in serialized), None)


def request_confirmation(
    context,
    guarded_route_handler,
    event_callback,
    callback,
    step: Step,
    index: int,
    confirmation_term: str,
) -> bool:
    if step.action.value != "human_takeover":
        return bool(callback(step, index, confirmation_term))
    context.unroute("**/*", guarded_route_handler)
    event_callback(
        "human_takeover_network_guard_paused",
        index=index,
        reason="user_controlled_login",
    )
    try:
        return bool(callback(step, index, confirmation_term))
    finally:
        context.route("**/*", guarded_route_handler)
        event_callback("human_takeover_network_guard_restored", index=index)
