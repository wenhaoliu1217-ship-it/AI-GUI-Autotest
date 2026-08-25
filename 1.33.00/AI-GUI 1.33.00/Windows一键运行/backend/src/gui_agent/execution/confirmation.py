"""Dangerous-action classification shared by fixed and Agent execution."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

from ..domain.models import ActionType, Step


DEFAULT_CONFIRMATION_ACTIONS = (
    "删除", "退款", "支付", "付款", "提交订单", "提交生产表单", "发布", "发送邀请",
    "delete", "refund", "pay", "purchase", "checkout", "submit", "publish", "invite",
)

_WRITE_ACTIONS_REQUIRING_APPROVAL = {
    ActionType.FILL, ActionType.CLEAR, ActionType.SELECT, ActionType.CHECK, ActionType.UNCHECK,
    ActionType.PRESS, ActionType.UPLOAD, ActionType.UPLOAD_FILE, ActionType.COMPONENT,
    ActionType.BRIDGE_CLICK, ActionType.VISUAL_CLEAR, ActionType.VISUAL_DRAW_POLYGON,
    ActionType.VISUAL_DRAW_RECTANGLE, ActionType.HUMAN_TAKEOVER,
}


def approval_rule(step: Step, configured_mode: str, safety_rule: str | None) -> str | None:
    """Apply the same beginner approval semantics to fixed and Agent runs."""
    if safety_rule:
        return safety_rule
    if configured_mode == "ask" and step.effect_level is not None and step.effect_level.value not in {
        "read_only", "session_only", "isolated_local_write",
    }:
        return f"approval-mode:site-write:{step.effect_kind or step.effect_level.value}"
    if (
        configured_mode == "ask"
        and step.action in _WRITE_ACTIONS_REQUIRING_APPROVAL
        and (step.effect_level is None or step.action == ActionType.HUMAN_TAKEOVER)
    ):
        return "approval-mode:write-action"
    return None


def confirmation_match(
    step: Step,
    *,
    specialized_rules: Iterable[Callable[[Step], str | None]] = (),
) -> str | None:
    """Return a generic confirmation rule plus any injected site rules.

    Site-specific policies are supplied by the composition layer.  Keeping
    them as callbacks prevents the ordinary execution path from importing or
    knowing about Cesium (or any future site adapter).
    """
    for specialized_rule in specialized_rules:
        rule = specialized_rule(step)
        if rule:
            return rule
    share_rule = _share_confirmation_rule(step)
    if share_rule:
        return share_rule
    story_rule = _story_creation_confirmation_rule(step)
    if story_rule:
        return story_rule
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


def _share_confirmation_rule(step: Step) -> str | None:
    """Fail closed for controls that can immediately make content public."""
    if step.action.value not in {"click", "check"} or step.locator is None:
        return None
    locator = step.locator
    hint = " ".join(filter(None, (
        locator.name,
        locator.label,
        locator.text,
        locator.test_id,
        locator.attribute_name,
    ))).lower()
    share_target = any(marker in hint for marker in (
        "share", "sharing", "public", "分享", "公开",
    ))
    direct_control = locator.role in {"button", "switch", "checkbox"} or (
        locator.test_id is not None and "sharing-toggle" in locator.test_id.lower()
    )
    return "share_public_content" if share_target and direct_control else None


def _story_creation_confirmation_rule(step: Step) -> str | None:
    """Do not trust a read-only label on controls that immediately create a Story."""
    if step.action.value != "click" or step.locator is None:
        return None
    locator = step.locator
    hint = " ".join(filter(None, (locator.name, locator.label, locator.text))).lower()
    story_creation = any(marker in hint for marker in (
        "new story", "create story", "新建 story", "创建 story", "新建故事", "创建故事",
    ))
    return "create_story" if story_creation and locator.role == "button" else None


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
