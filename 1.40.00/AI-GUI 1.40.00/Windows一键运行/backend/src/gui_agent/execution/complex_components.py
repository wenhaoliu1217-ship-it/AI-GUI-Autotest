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
