"""确定性定位策略。

把领域层的 Locator 转成 Playwright 的 Locator，按当前页面可观察的
role→label→placeholder→test_id→attribute→css→text 语义回退。这里刻意不含
"让模型自己找元素"的逻辑，
AI 降级作为独立可选模块在后续阶段接入，且必须显式开启。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..domain.models import Locator, Step

if TYPE_CHECKING:  # 仅类型检查时导入，运行时不依赖 Playwright 已安装
    from playwright.sync_api import Locator as PWLocator
    from playwright.sync_api import Page


class LocatorError(Exception):
    """无法从领域 Locator 构造 Playwright 定位器。"""


def resolve_locator(page: "Page", locator: Locator) -> "PWLocator":
    """按确定性优先级把领域 Locator 解析为 Playwright Locator。

    优先级：role > label > placeholder > test_id > attribute > css > text。
    当前页面不再支持某个旧定位器时，会继续尝试同一个 Locator 中的
    其他语义策略；如果所有候选都没有命中，返回首个候选，让上层保留
    原有的唯一性、等待和错误证据。
    """
    context = page
    if locator.scope and not locator.shadow_hosts:
        context = resolve_locator(page, locator.scope.locator)
        if locator.scope.identity:
            context = context.filter(has_text=locator.scope.identity)
    for host_selector in locator.shadow_hosts:
        context = context.locator(host_selector)
    candidates = _locator_candidates(context, locator)
    if not candidates:
        raise LocatorError(f"无法解析定位器：{locator.describe()}")
    for candidate in candidates:
        try:
            if candidate.count() > 0:
                return candidate
        except Exception:
            # A candidate can be temporarily unavailable while a page or an
            # iframe is changing. Let the next semantic strategy try first.
            continue
    return candidates[0]


def _locator_candidates(context, locator: Locator) -> list["PWLocator"]:
    candidates: list["PWLocator"] = []
    if locator.role:
        if locator.name:
            candidates.append(context.get_by_role(locator.role, name=locator.name, exact=locator.exact))  # type: ignore[arg-type]
        else:
            candidates.append(context.get_by_role(locator.role))  # type: ignore[arg-type]
    if locator.label:
        candidates.append(context.get_by_label(locator.label, exact=locator.exact))
    if locator.placeholder:
        candidates.append(context.get_by_placeholder(locator.placeholder, exact=locator.exact))
    if locator.test_id:
        candidates.append(context.get_by_test_id(locator.test_id))
    if locator.attribute_name:
        candidates.append(context.locator(f"[name={json.dumps(locator.attribute_name)}]"))
    if locator.href:
        candidates.append(context.locator(f"a[href={json.dumps(locator.href)}]"))
    if locator.attribute:
        escaped = locator.attribute.value.replace('"', '\\"')
        candidates.append(context.locator(f'[{locator.attribute.name}="{escaped}"]'))
    if locator.css:
        candidates.append(context.locator(locator.css))
    if locator.text:
        candidates.append(context.get_by_text(locator.text, exact=locator.exact))
    return candidates


def resolve_step_locator(page: "Page", step: Step, *, scroll_page=None) -> "PWLocator":
    if step.locator is None:
        raise LocatorError("步骤缺少 locator")
    scope = step.commerce_scope
    if scope is None:
        resolved = resolve_locator(page, step.locator)
        if step.locator.shadow_hosts and resolved.count() == 0:
            fallback = _resolve_shadow_fallback(page, step.locator)
            if fallback is not None:
                return fallback
        return resolved

    for attempt in range(scope.max_scroll_attempts + 1):
        containers = resolve_locator(page, scope.container).filter(
            has=resolve_locator(page, scope.anchor)
        )
        for marker in scope.excluded_markers:
            containers = containers.filter(has_not=resolve_locator(page, marker))
        container_count = containers.count()
        if container_count > 1:
            raise LocatorError(
                f"电商作用域匹配到 {container_count} 个 {scope.kind} 容器，拒绝猜测目标"
            )
        if container_count == 1:
            target = resolve_locator(containers, step.locator)
            target_count = target.count()
            if target_count == 1:
                return target
            if target_count > 1:
                raise LocatorError(
                    f"电商作用域内匹配到 {target_count} 个目标控件，拒绝使用 first()"
                )
        if attempt < scope.max_scroll_attempts:
            scrolling_surface = scroll_page or page
            scrolling_surface.mouse.wheel(0, 700)
            scrolling_surface.wait_for_timeout(120)
    raise LocatorError(
        f"滚动 {scope.max_scroll_attempts} 次后仍未找到唯一 {scope.kind} 目标"
    )


def _resolve_shadow_fallback(page: "Page", locator: Locator) -> "PWLocator | None":
    """Use observed text when a custom element has no inferred ARIA role."""
    context = page
    if locator.scope and not locator.shadow_hosts:
        context = resolve_locator(page, locator.scope.locator)
        if locator.scope.identity:
            context = context.filter(has_text=locator.scope.identity)
    for host_selector in locator.shadow_hosts:
        context = context.locator(host_selector)
    if locator.text:
        return context.get_by_text(locator.text, exact=locator.exact)
    if locator.label and locator.role:
        return context.get_by_label(locator.label, exact=locator.exact)
    return None


def resolve_action_locator(page: "Page", locator: Locator) -> "PWLocator":
    resolved = resolve_locator(page, locator)
    count = resolved.count()
    if count != 1:
        raise LocatorError(f"动作目标必须唯一，实际匹配 {count} 个：{locator.describe()}")
    return resolved
