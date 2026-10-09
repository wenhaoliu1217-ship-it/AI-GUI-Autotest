"""确定性定位策略。

把领域层的 Locator 转成 Playwright 的 Locator，按 role→label→test_id→css→text
的优先级选择第一个提供的策略。这里刻意不含"让模型自己找元素"的逻辑，
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


def resolve_locator(page: "Page", locator: Locator, *, allow_unresolved: bool = False) -> "PWLocator":
    """按确定性优先级把领域 Locator 解析为 Playwright Locator。

    优先级：role > label > test_id > css > text。
    Locator 模型已保证至少有一种策略，这里按序返回第一个命中的。
    """
    # Runtime IDs are assigned by the latest browser observation. They are
    # global to the current document so Portal-rendered options must not be
    # constrained by a dialog/card scope supplied by the model.
    if locator.runtime_id:
        resolved = page.locator(
            f'[data-ai-gui-runtime-id={json.dumps(locator.runtime_id)}]'
        )
        # Runtime ids are intentionally short-lived.  A React/Vue rerender can
        # replace the node between observation and execution; when semantic
        # hints are present, continue through the normal strategies instead of
        # treating that stale id as authoritative.
        if resolved.count() > 0:
            return _prefer_unique_visible(resolved)
    context = page
    if locator.scope:
        context = _resolve_scope_context(page, locator.scope)
    for host_selector in locator.shadow_hosts:
        context = context.locator(host_selector)
    if locator.test_id:
        resolved = context.get_by_test_id(locator.test_id)
        if resolved.count() > 0:
            return _prefer_unique_visible(resolved)
    if locator.role:
        if locator.name:
            resolved = context.get_by_role(locator.role, name=locator.name, exact=locator.exact)  # type: ignore[arg-type]
            if resolved.count() > 0:
                return _prefer_unique_visible(resolved)
            if locator.role == "button" and locator.scope:
                icon_button = _scoped_icon_button_fallback(page, locator)
                if icon_button is not None and icon_button.count() == 1:
                    return icon_button
                adjacent = _adjacent_label_button(context, locator.name, exact=locator.exact)
                if adjacent.count() > 0:
                    return adjacent
        elif not any((locator.label, locator.placeholder, locator.test_id, locator.css, locator.text)):
            return _prefer_unique_visible(context.get_by_role(locator.role))  # type: ignore[arg-type]
    if locator.label:
        resolved = context.get_by_label(locator.label, exact=locator.exact)
        if resolved.count() > 0:
            return _prefer_unique_visible(resolved)
        # Ant/Vue and similar component libraries frequently expose the
        # visible field name through aria-label but do not render a native
        # <label for=...> association.  Playwright's get_by_label then
        # returns zero even though the control has an unambiguous accessible
        # name.  Try common form-control roles as a constrained fallback;
        # return only a unique match and keep ambiguity fail-closed.
        for role in ("textbox", "searchbox", "combobox", "spinbutton"):
            try:
                named = context.get_by_role(role, name=locator.label, exact=locator.exact)
                if named.count() == 1:
                    return named
                if named.count() > 1:
                    unique_visible = _prefer_unique_visible(named)
                    if unique_visible.count() == 1:
                        return unique_visible
            except Exception:
                # A lightweight test double or a browser backend may not
                # implement every ARIA role. Continue to the next strategy.
                continue
    if locator.placeholder:
        resolved = context.get_by_placeholder(locator.placeholder, exact=locator.exact)
        if resolved.count() > 0:
            return _prefer_unique_visible(resolved)
    if locator.attribute_name:
        return _prefer_unique_visible(context.locator(f"[name={json.dumps(locator.attribute_name)}]"))
    if locator.href:
        return _prefer_unique_visible(context.locator(f"a[href={json.dumps(locator.href)}]"))
    if locator.attribute:
        escaped = locator.attribute.value.replace('"', '\\"')
        return _prefer_unique_visible(context.locator(f'[{locator.attribute.name}="{escaped}"]'))
    if locator.css:
        resolved = context.locator(locator.css)
        if resolved.count() > 0 or allow_unresolved:
            return _prefer_unique_visible(resolved)
    if locator.text:
        resolved = context.get_by_text(locator.text, exact=locator.exact)
        if resolved.count() > 0 or allow_unresolved:
            return _prefer_unique_visible(resolved)
    if locator.role:
        # The model may concatenate visible options into an accessible name.
        # Only use this fallback inside an explicit business scope and only
        # when the current page exposes exactly one control with that role.
        scoped_role = context.get_by_role(locator.role)  # type: ignore[arg-type]
        if locator.scope:
            count = scoped_role.count()
            if count == 1:
                return scoped_role
            raise LocatorError(
                f"作用域内的 {locator.role} 目标必须唯一，实际匹配 {count} 个：{locator.describe()}"
            )
        return _prefer_unique_visible(scoped_role)
    raise LocatorError(f"无法解析定位器：{locator.describe()}")


def _resolve_scope_context(page: "Page", scope) -> "PWLocator":
    if scope.kind == "dialog" and scope.identity:
        dialog = page.get_by_role("dialog").filter(has_text=scope.identity)
        if dialog.count() > 0:
            return dialog
        # Many Vue/Ant Design dialogs expose a visible title but omit
        # aria-labelledby. Recover the semantic container from that title.
        modal = page.locator(
            '[role="dialog"], .ant-modal, .ant-modal-wrap, '
            '[class*="modal" i], [class*="dialog" i]'
        ).filter(has_text=scope.identity)
        if modal.count() > 0:
            return modal
        heading = page.get_by_role("heading", name=scope.identity, exact=True)
        if heading.count() == 0:
            heading = page.get_by_text(scope.identity, exact=True)
        if heading.count() > 0:
            container = heading.locator("xpath=ancestor::*[.//button][1]")
            if container.count() > 0:
                return container
    if scope.kind == "card" and scope.identity:
        known_cards = page.locator(
            ".ant-card, [class*='mantine-Card-root'], [data-card], "
            "[data-testid*='card' i], [class*='model-card' i], [class*='resource-card' i], "
            "[role='article']"
        ).filter(has_text=scope.identity)
        if known_cards.count() > 0:
            return known_cards
    # Resolve the visible business identity before falling back to a model
    # supplied locator. This handles cards whose class is generated or whose
    # action buttons have no accessible name.
    anchor = None
    used_fuzzy_identity = False
    if scope.identity:
        exact_text = page.get_by_text(scope.identity, exact=True)
        if exact_text.count() > 0:
            anchor = exact_text
        elif not (
            getattr(scope.locator, "text", None) == scope.identity
            and getattr(scope.locator, "exact", False)
        ):
            fuzzy_text = page.get_by_text(scope.identity, exact=False)
            if fuzzy_text.count() > 0:
                anchor = fuzzy_text
                used_fuzzy_identity = True
    if anchor is None:
        try:
            candidate = resolve_locator(page, scope.locator)
        except LocatorError as exc:
            raise LocatorError(
                f"无法定位 {scope.kind} 作用域：当前页面没有找到业务标识 {scope.identity!r}"
            ) from exc
        if candidate.count() == 0:
            # Some component libraries expose no dialog node at all while
            # still rendering one unique target control. Keep the scope as a
            # soft hint in this narrow case; resolve_locator below still
            # requires the final role to be unique before returning it.
            if (
                scope.kind == "dialog"
                and getattr(scope.locator, "role", None) == "dialog"
                and not getattr(scope.locator, "name", None)
            ):
                return page
            raise LocatorError(
                f"无法定位 {scope.kind} 作用域：作用域锚点没有匹配当前 DOM"
            )
        anchor = candidate
    if scope.identity and anchor.count() > 0 and not used_fuzzy_identity:
        anchor = anchor.filter(has_text=scope.identity)
    if scope.kind != "card":
        return anchor

    # Card titles are often separate from icon-only action buttons. Resolve the
    # business identity first, then move to the closest card-like container.
    cards = anchor.locator(
        "xpath=ancestor::*[contains(translate(@class,'CARD','card'),'card')][1]"
    )
    if cards.count() == 0:
        cards = anchor.locator("xpath=ancestor::*[count(.//button) >= 2][1]")
    if cards.count() == 0:
        cards = anchor.locator("xpath=ancestor::*[count(.//button) >= 1][1]")
    if cards.count() == 0:
        raise LocatorError(
            f"无法定位 card 作用域：已找到 {scope.identity!r}，但其祖先没有可操作按钮"
        )
    return cards


def _prefer_unique_visible(locator: "PWLocator") -> "PWLocator":
    """Use the sole visible match when libraries keep hidden duplicates mounted.

    Component libraries commonly retain a hidden menu, a mobile copy, or a
    previous virtualized row in the DOM.  Returning ``nth(0)`` would guess;
    narrowing is safe only when exactly one candidate is currently visible.
    """
    count = locator.count()
    if count <= 1:
        return locator
    visible: list[int] = []
    for index in range(count):
        try:
            if locator.nth(index).is_visible():
                visible.append(index)
        except Exception:
            # A detached candidate is not usable, but it must not make a
            # semantic locator crash while another candidate is still live.
            continue
    return locator.nth(visible[0]) if len(visible) == 1 else locator


def _adjacent_label_button(context, name: str, *, exact: bool):
    labels = context.get_by_text(name, exact=exact)
    return labels.locator(
        "xpath=preceding-sibling::button[1] | "
        "ancestor::*[count(.//button)=1][1]//button"
    )


def _scoped_icon_button_fallback(page, locator: Locator):
    scope = locator.scope
    if scope is None or scope.kind != "card" or not scope.identity or not locator.name:
        return None
    ordinal = {"修改": 1, "预览": 2, "详情": 3, "复制": 4, "删除": 5}.get(locator.name)
    if ordinal is None:
        return None
    identity = _xpath_literal(scope.identity)
    return page.locator(
        f"xpath=(//*[contains(normalize-space(.), {identity})]"
        f"/ancestor-or-self::div[count(.//button)>=5][1]//button)[{ordinal}]"
    )


def _xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


def resolve_step_locator(page: "Page", step: Step, *, scroll_page=None, allow_unresolved: bool = False) -> "PWLocator":
    if step.locator is None:
        raise LocatorError("步骤缺少 locator")
    scope = step.commerce_scope
    if scope is None:
        return resolve_locator(page, step.locator, allow_unresolved=allow_unresolved)

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


def resolve_action_locator(page: "Page", locator: Locator) -> "PWLocator":
    resolved = resolve_locator(page, locator)
    count = resolved.count()
    if count != 1:
        raise LocatorError(f"动作目标必须唯一，实际匹配 {count} 个：{locator.describe()}")
    return resolved
