"""Target-independent semantic actions for common complex Web components."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..domain.models import ActionType, Step
from ..locating.strategies import resolve_action_locator
from .file_transfer import execute_upload


def execute_component(page, step: Step, test_files: tuple[dict, ...], artifacts, timeout_ms: int) -> dict[str, Any]:
    component = step.component
    if component is None:
        raise ValueError("复杂组件动作缺少语义配置")
    locators: list[Any | None] = [None] * len(component.locators)
    def at(index: int):
        if locators[index] is None:
            locators[index] = resolve_action_locator(page, component.locators[index])
        return locators[index]
    at(0).wait_for(state="visible", timeout=timeout_ms)
    if component.kind in {"cascade_select", "date_time_range"}:
        for index in range(1, len(component.locators)):
            at(index).wait_for(state="visible", timeout=timeout_ms)
    evidence: dict[str, Any] = {
        "kind": component.kind, "semanticTarget": component.semantic_target,
        "adapterId": step.component_adapter_id, "locatorCount": len(locators),
        "status": "complete",
    }

    if component.kind == "cascade_select":
        selections = []
        for index, value in enumerate(component.values):
            locator = at(index)
            locator.select_option(value)
            actual = locator.input_value()
            if actual != value:
                raise PlaywrightError(f"级联下拉选择结果不一致：期望 {value}，实际 {actual}")
            selections.append({"expected": value, "actual": actual})
        evidence["selections"] = selections
    elif component.kind == "cascader":
        # Ant Design and similar cascaders do not expose native <select>
        # elements. The planner supplies the semantic path values after
        # observing the currently visible menu options; execution resolves
        # only visible exact-text options and re-reads the trigger afterwards.
        trigger = at(0)
        if not _has_visible_options(page):
            trigger.click()
        selected_path: list[dict[str, str]] = []
        for value in component.values:
            option = _visible_exact_option(page, value, timeout_ms)
            actual_option = " ".join(option.inner_text().split())
            option.click()
            selected_path.append({"expected": value, "actual": actual_option})
            page.wait_for_timeout(min(150, max(0, timeout_ms // 20)))
        actual_trigger = " ".join(trigger.inner_text().split())
        expected_text = component.expected_text or component.values[-1]
        if expected_text and expected_text not in actual_trigger:
            raise PlaywrightError(
                f"级联菜单选择结果未回读到预期文本：期望包含 {expected_text}，实际为 {actual_trigger}"
            )
        evidence["selections"] = selected_path
        evidence["triggerText"] = actual_trigger[:500]
    elif component.kind == "searchable_select":
        at(0).click()
        at(1).wait_for(state="visible", timeout=timeout_ms)
        at(1).fill(component.values[0])
        at(2).wait_for(state="visible", timeout=timeout_ms)
        at(2).click()
        evidence["query"] = component.values[0]
        evidence["selectedText"] = at(2).inner_text()
    elif component.kind == "date_time_range":
        for index, value in enumerate(component.values[:2]):
            at(index).fill(value)
        actual = [at(index).input_value() for index in range(2)]
        if actual != component.values[:2]:
            raise PlaywrightError(f"日期时间范围回读不一致：{actual}")
        evidence["range"] = actual
    elif component.kind == "pagination":
        before = page.url
        at(0).click()
        evidence.update(urlBefore=before, urlAfter=page.url, controlText=at(0).inner_text())
    elif component.kind == "statistics_card":
        actual = at(0).inner_text()
        if component.expected_text not in actual:
            raise PlaywrightError(f"统计卡片未包含预期文本：{component.expected_text}")
        evidence["actualText"] = actual[:1000]
    elif component.kind == "tab":
        at(0).click()
        evidence["selected"] = at(0).get_attribute("aria-selected")
        if evidence["selected"] == "false":
            raise PlaywrightError("页签点击后仍未选中")
    elif component.kind == "upload_dialog":
        at(0).click()
        at(1).wait_for(state="visible", timeout=timeout_ms)
        upload_step = Step(
            action=ActionType.UPLOAD, locator=component.locators[1], file_id=component.file_id,
            business_object_name=step.business_object_name,
            expected_file_validity=step.expected_file_validity,
            residual_object_locator=step.residual_object_locator,
            expected_residual_count=step.expected_residual_count,
        )
        evidence["fileEvidence"] = execute_upload(page, upload_step, test_files, artifacts, timeout_ms)
    elif component.kind == "image_preview":
        at(0).click()
        at(1).wait_for(state="visible", timeout=timeout_ms)
        actual = at(1).get_attribute("alt") or at(1).get_attribute("aria-label") or at(1).inner_text()
        if component.expected_text not in actual:
            raise PlaywrightError(f"图片预览未关联预期对象：{component.expected_text}")
        evidence["previewIdentity"] = actual
    elif component.kind == "local_scroll":
        before = at(0).evaluate("element => element.scrollTop")
        after = at(0).evaluate("(element, delta) => { element.scrollBy(0, delta); return element.scrollTop; }", component.scroll_delta_y)
        if component.scroll_delta_y and before == after:
            raise PlaywrightError("局部滚动容器位置未发生变化")
        evidence.update(scrollTopBefore=before, scrollTopAfter=after, deltaY=component.scroll_delta_y)
    else:
        raise ValueError(f"未实现复杂组件：{component.kind}")
    return evidence


def execute_select_value(page, target, value: str, timeout_ms: int) -> dict[str, Any]:
    """Select a value from either a native select or a custom ARIA combobox.

    The planner is allowed to emit the simple ``select`` action after seeing a
    component's current observation.  Native ``<select>`` controls use
    Playwright's value API; custom Ant/Vue controls are opened and resolved
    from the currently visible option layer.  No site-specific option text or
    coordinates are introduced here.
    """
    try:
        tag_name = str(target.evaluate("element => element.tagName.toLowerCase()"))
    except AttributeError:
        # Lightweight test doubles and older Playwright wrappers may not
        # expose evaluate; the native select API is still an unambiguous hint.
        tag_name = "select" if hasattr(target, "select_option") else ""
    if tag_name == "select":
        target.select_option(value)
        actual = target.input_value()
        selected_label = ""
        try:
            selected_label = " ".join(target.locator("option:checked").first.inner_text().split())
        except PlaywrightError:
            pass
        expected = " ".join(value.split())
        return {
            "kind": "selection",
            "verified": actual == value or selected_label == expected,
            "selectedText": selected_label or actual,
            "native": True,
        }

    target.click()
    option = _visible_exact_option(page, value, timeout_ms)
    selected_text = " ".join(option.inner_text().split())
    option.click()
    # Ant Select keeps the selected item in a sibling node while some ARIA
    # comboboxes expose it through the input value. Read both without storing
    # the user's raw input in the evidence.
    actual_text = ""
    try:
        actual_text = " ".join(target.input_value().split())
    except PlaywrightError:
        pass
    if not actual_text:
        try:
            actual_text = " ".join(target.inner_text().split())
        except PlaywrightError:
            pass
    verified = selected_text == value or value in actual_text or selected_text in actual_text
    if not verified:
        raise PlaywrightError(
            f"自定义下拉选择结果未回读到预期文本：期望 {value!r}，实际为 {actual_text or selected_text!r}"
        )
    return {
        "kind": "selection",
        "verified": True,
        "selectedText": actual_text or selected_text,
        "native": False,
    }


def _visible_exact_option(page, value: str, timeout_ms: int):
    """Resolve a visible option without relying on a site-specific adapter."""
    selectors = (
        '[role="option"]',
        '.ant-cascader-menu-item',
        '.ant-select-item-option',
        '[class*="option" i]',
    )
    deadline = timeout_ms
    for selector in selectors:
        candidates = page.locator(selector).all()
        for candidate in candidates:
            try:
                if not candidate.is_visible():
                    continue
                if candidate.get_attribute("aria-disabled") == "true":
                    continue
                text = " ".join(candidate.inner_text().split())
                if text == value:
                    return candidate
            except PlaywrightError:
                continue
        if deadline <= 0:
            break
        page.wait_for_timeout(min(100, deadline))
        deadline -= 100
    raise PlaywrightError(f"当前可见级联菜单中找不到精确选项：{value}")


def _has_visible_options(page) -> bool:
    selector = '[role="option"],.ant-cascader-menu-item,.ant-select-item-option,[class*="option" i]'
    for candidate in page.locator(selector).all():
        try:
            if candidate.is_visible():
                return True
        except PlaywrightError:
            continue
    return False
