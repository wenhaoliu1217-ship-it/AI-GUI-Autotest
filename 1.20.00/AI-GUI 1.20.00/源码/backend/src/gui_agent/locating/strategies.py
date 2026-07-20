"""确定性定位策略。

把领域层的 Locator 转成 Playwright 的 Locator，按 role→label→test_id→css→text
的优先级选择第一个提供的策略。这里刻意不含"让模型自己找元素"的逻辑，
AI 降级作为独立可选模块在后续阶段接入，且必须显式开启。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..domain.models import Locator

if TYPE_CHECKING:  # 仅类型检查时导入，运行时不依赖 Playwright 已安装
    from playwright.sync_api import Locator as PWLocator
    from playwright.sync_api import Page


class LocatorError(Exception):
    """无法从领域 Locator 构造 Playwright 定位器。"""


def resolve_locator(page: "Page", locator: Locator) -> "PWLocator":
    """按确定性优先级把领域 Locator 解析为 Playwright Locator。

    优先级：role > label > test_id > css > text。
    Locator 模型已保证至少有一种策略，这里按序返回第一个命中的。
    """
    if locator.role:
        if locator.name:
            return page.get_by_role(locator.role, name=locator.name)  # type: ignore[arg-type]
        return page.get_by_role(locator.role)  # type: ignore[arg-type]
    if locator.label:
        return page.get_by_label(locator.label)
    if locator.test_id:
        return page.get_by_test_id(locator.test_id)
    if locator.css:
        return page.locator(locator.css)
    if locator.text:
        return page.get_by_text(locator.text)
    raise LocatorError(f"无法解析定位器：{locator.describe()}")
