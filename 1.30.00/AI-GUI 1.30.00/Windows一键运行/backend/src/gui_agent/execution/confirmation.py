"""Dangerous-action classification shared by fixed and Agent execution."""

from __future__ import annotations

import json

from ..domain.models import Step


DEFAULT_CONFIRMATION_ACTIONS = (
    "删除", "退款", "支付", "付款", "提交订单", "提交生产表单", "发布", "发送邀请",
    "delete", "refund", "pay", "purchase", "checkout", "submit", "publish", "invite",
)


def confirmation_match(step: Step) -> str | None:
    serialized = json.dumps(step.model_dump(mode="json", exclude_none=True), ensure_ascii=False).lower()
    return next((term for term in DEFAULT_CONFIRMATION_ACTIONS if term in serialized), None)
