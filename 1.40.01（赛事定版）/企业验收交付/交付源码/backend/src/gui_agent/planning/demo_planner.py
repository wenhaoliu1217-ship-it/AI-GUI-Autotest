"""首版自然语言规划器。

该模块只提供一个透明、可预测的演示模板，不冒充通用 AI。后续版本通过
providers 接口替换为模型结构化输出，但仍复用同一个 TestPlan Schema。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..domain.models import (
    ActionType,
    Assertion,
    AssertionType,
    Locator,
    Precondition,
    Step,
    TestPlan,
)


class UnsupportedIntentError(ValueError):
    """首版模板无法识别输入意图。"""


def plan_from_text(text: str, *, customer_name: str = "星河科技") -> TestPlan:
    """把受支持的管理员客户流程转换为可审核计划。"""
    normalized = text.strip()
    required_signals = ("管理员", "客户")
    if not all(signal in normalized for signal in required_signals):
        raise UnsupportedIntentError(
            "v0.1 规则规划器目前只支持包含“管理员”和“客户”的演示流程；"
            "也可以直接编写 YAML/JSON 结构化计划。"
        )

    return TestPlan(
        name="管理员创建并分配客户",
        base_url="${TEST_BASE_URL}",
        role="admin",
        preconditions=[Precondition(description="本地演示站或企业授权测试环境可用")],
        steps=[
            Step(action=ActionType.NAVIGATE, target="/", description="打开登录页"),
            Step(
                action=ActionType.FILL,
                locator=Locator(label="用户名"),
                value_from_secret="ADMIN_USERNAME",
                description="填写管理员用户名",
            ),
            Step(
                action=ActionType.FILL,
                locator=Locator(label="密码"),
                value_from_secret="ADMIN_PASSWORD",
                description="填写管理员密码",
            ),
            Step(
                action=ActionType.CLICK,
                locator=Locator(role="button", name="登录"),
                description="登录系统",
            ),
            Step(
                action=ActionType.FILL,
                locator=Locator(label="客户名称"),
                value=customer_name,
                description="填写客户名称",
            ),
            Step(
                action=ActionType.SELECT,
                locator=Locator(label="负责人"),
                value="emp1",
                description="将客户分配给员工一",
            ),
            Step(
                action=ActionType.CLICK,
                locator=Locator(role="button", name="新建客户"),
                description="提交新客户",
            ),
        ],
        assertions=[
            Assertion(
                type=AssertionType.URL_CONTAINS,
                expected="/customers",
                description="已进入客户管理页面",
            ),
            Assertion(
                type=AssertionType.VISIBLE,
                locator=Locator(role="heading", name="客户管理"),
                description="客户管理标题可见",
            ),
            Assertion(
                type=AssertionType.VISIBLE,
                locator=Locator(text=customer_name),
                description="新客户出现在列表中",
            ),
        ],
    )


def save_plan(plan: TestPlan, path: str | Path) -> Path:
    """以 UTF-8 YAML 保存计划，供人工审核和后续执行。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = plan.model_dump(mode="json", exclude_none=True)
    target.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target
