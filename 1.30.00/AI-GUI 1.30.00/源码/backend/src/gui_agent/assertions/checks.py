"""断言执行。

把领域层的 Assertion 转成对 Playwright 页面的实际检查，返回
(通过?, 实际值摘要)。不抛断言异常，由执行器统一记录 AssertionResult，
保证"断言失败"与"执行错误"在报告中可区分。

数据范围断言用 NOT_VISIBLE 和 COUNT_EQUALS 表达，例如
"员工看不到其他人的客户" -> NOT_VISIBLE，"只看到自己的 3 条" -> COUNT_EQUALS。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..domain.models import Assertion, AssertionType
from ..locating.strategies import resolve_locator

if TYPE_CHECKING:
    from playwright.sync_api import Page

# 断言级别的默认超时（毫秒）。比动作超时短，避免断言在错误页面上长时间等待。
ASSERTION_TIMEOUT_MS = 5000


class AssertionOutcome:
    """一次断言检查的结果。passed 为断言是否成立；actual 为脱敏前的实际值摘要。"""

    def __init__(self, passed: bool, actual: str) -> None:
        self.passed = passed
        self.actual = actual


def check_assertion(page: "Page", assertion: Assertion) -> AssertionOutcome:
    """执行单条断言，返回结果。定位/运行异常向上抛出，由执行器归类为 ERROR。"""
    t = assertion.type

    if t == AssertionType.PAGE_REACHED:
        url = page.url
        return AssertionOutcome(
            passed=(assertion.expected or "") in url, actual=f"url={url}"
        )

    if t == AssertionType.URL_CONTAINS:
        url = page.url
        return AssertionOutcome(
            passed=(assertion.expected or "") in url, actual=f"url={url}"
        )

    if t == AssertionType.VISIBLE:
        loc = resolve_locator(page, assertion.locator)  # type: ignore[arg-type]
        visible = loc.first.is_visible()
        return AssertionOutcome(passed=visible, actual=f"visible={visible}")

    if t == AssertionType.NOT_VISIBLE:
        loc = resolve_locator(page, assertion.locator)  # type: ignore[arg-type]
        count = loc.count()
        visible = count > 0 and loc.first.is_visible()
        return AssertionOutcome(passed=(not visible), actual=f"visible={visible}")

    if t == AssertionType.TEXT_CONTAINS:
        loc = resolve_locator(page, assertion.locator)  # type: ignore[arg-type]
        text = loc.first.inner_text(timeout=ASSERTION_TIMEOUT_MS)
        return AssertionOutcome(
            passed=(assertion.expected or "") in text, actual=f"text={text!r}"
        )

    if t == AssertionType.VALUE_EQUALS:
        loc = resolve_locator(page, assertion.locator)  # type: ignore[arg-type]
        value = loc.first.input_value(timeout=ASSERTION_TIMEOUT_MS)
        return AssertionOutcome(
            passed=(value == assertion.expected), actual=f"value={value!r}"
        )

    if t == AssertionType.COUNT_EQUALS:
        loc = resolve_locator(page, assertion.locator)  # type: ignore[arg-type]
        count = loc.count()
        return AssertionOutcome(
            passed=(count == assertion.count), actual=f"count={count}"
        )

    raise ValueError(f"未知断言类型：{t}")
