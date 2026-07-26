"""Bind project runs to reviewed component adapter entries."""

from __future__ import annotations

from ..domain.models import ActionType, ComponentAction, Step
from ..security.policy import SecurityError


def validate_component_step(step: Step, adapters: tuple[dict, ...], *, require_adapter: bool) -> None:
    if step.action != ActionType.COMPONENT:
        return
    if not require_adapter and not step.component_adapter_id:
        return
    if not step.component_adapter_id:
        raise SecurityError("项目复杂组件动作必须引用 component_adapter_id")
    adapter = next((item for item in adapters if item.get("id") == step.component_adapter_id), None)
    if adapter is None:
        raise SecurityError(f"复杂组件适配条目不存在：{step.component_adapter_id}")
    if adapter.get("status") != "configured":
        raise SecurityError(f"复杂组件适配仍为 blocked：{step.component_adapter_id}")
    expected = ComponentAction.model_validate(adapter.get("action") or {}).model_dump(mode="json", by_alias=True, exclude_none=True)
    actual = step.component.model_dump(mode="json", by_alias=True, exclude_none=True) if step.component else {}
    if actual != expected:
        raise SecurityError(f"复杂组件动作与已审核适配条目不一致：{step.component_adapter_id}")
