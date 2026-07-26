"""确定性定位策略。

把领域层的 Locator 转成 Playwright 的 Locator，按 role→label→test_id→css→text
的优先级选择第一个提供的策略。这里刻意不含"让模型自己找元素"的逻辑，
AI 降级作为独立可选模块在后续阶段接入，且必须显式开启。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..domain.models import Locator

if TYPE_CHECKING:  # 仅类型检查时导入，运行时不依赖 Playwright 已安装
    from playwright.sync_api import Locator as PWLocator
    from playwright.sync_api import Page


class LocatorError(Exception):
    """无法从领域 Locator 构造 Playwright 定位器。"""


def _resolve_from(root, locator: Locator) -> "PWLocator":
    if locator.test_id:
        return root.get_by_test_id(locator.test_id)
    if locator.role:
        if locator.name:
            return root.get_by_role(locator.role, name=locator.name, exact=locator.exact)
        return root.get_by_role(locator.role)
    if locator.label:
        return root.get_by_label(locator.label, exact=locator.exact)
    if locator.placeholder:
        return root.get_by_placeholder(locator.placeholder, exact=locator.exact)
    if locator.attribute_name:
        return root.locator(f"[name={json.dumps(locator.attribute_name)}]")
    if locator.href:
        return root.locator(f"a[href={json.dumps(locator.href)}]")
    if locator.css:
        return root.locator(locator.css)
    if locator.text:
        return root.get_by_text(locator.text, exact=locator.exact)
    raise LocatorError(f"无法解析定位器：{locator.describe()}")


def resolve_locator(page: "Page", locator: Locator) -> "PWLocator":
    """按确定性优先级把领域 Locator 解析为 Playwright Locator。

    优先级：role > label > test_id > css > text。
    Locator 模型已保证至少有一种策略，这里按序返回第一个命中的。
    """
    root = resolve_locator(page, locator.within) if locator.within else page
    for host_selector in locator.shadow_hosts:
        root = root.locator(host_selector)
    return _resolve_from(root, locator)
