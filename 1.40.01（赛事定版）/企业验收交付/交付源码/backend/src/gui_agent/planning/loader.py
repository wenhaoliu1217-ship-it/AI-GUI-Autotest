"""计划加载与校验。

职责：
- 从 YAML/JSON 文件加载原始数据。
- 通过 Pydantic 完成 schema 校验（非法结构在此被拒绝）。
- 完成安全预检：动作白名单已由枚举保证，这里再校验密钥引用完整性。

注意：加载器只做静态校验，不解析密钥真实值（那是执行期的事），
只检查被引用的环境变量是否存在，尽早暴露配置缺失。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..domain.models import ActionType, Step, TestPlan


class PlanLoadError(ValueError):
    """计划加载或校验失败。"""


def _read_raw(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        raise PlanLoadError(f"不支持的计划文件类型：{suffix}（仅支持 .yaml/.yml/.json）")
    if not isinstance(data, dict):
        raise PlanLoadError("计划文件顶层必须是一个映射对象")
    return data


def load_plan(path: str | Path, *, check_secrets: bool = True) -> TestPlan:
    """加载并校验测试计划。

    ``check_secrets=False`` 用于只查看或校验计划结构；真正执行时必须保留默认值，
    在浏览器启动前确认所有密钥引用都可解析。
    """
    p = Path(path)
    if not p.exists():
        raise PlanLoadError(f"计划文件不存在：{p}")
    raw = _read_raw(p)
    try:
        plan = TestPlan.model_validate(raw)
    except ValidationError as exc:
        raise PlanLoadError(f"计划 schema 校验失败：\n{exc}") from exc
    if check_secrets:
        _check_secret_references(plan)
    return plan


def _check_secret_references(plan: TestPlan) -> None:
    """静态检查：被引用的密钥环境变量必须已设置。

    尽早失败，避免执行到一半才发现缺密钥。
    """
    missing: list[str] = []
    for idx, step in enumerate(plan.steps, start=1):
        if step.value_from_secret and step.value_from_secret not in os.environ:
            missing.append(f"步骤{idx}({step.action.value}) -> {step.value_from_secret}")
    if missing:
        raise PlanLoadError(
            "以下密钥环境变量未设置，无法执行：\n  " + "\n  ".join(missing)
        )


def summarize_plan(plan: TestPlan) -> str:
    """生成可审核的计划摘要，供人工确认。敏感值只显示引用名，不显示明文。"""
    lines: list[str] = []
    lines.append(f"计划名称：{plan.name}")
    lines.append(f"基础地址：{plan.base_url}")
    if plan.role:
        lines.append(f"执行角色：{plan.role}")
    if plan.preconditions:
        lines.append("前置条件：")
        for pc in plan.preconditions:
            lines.append(f"  - {pc.description}")
    lines.append(f"步骤（{len(plan.steps)}）：")
    for idx, step in enumerate(plan.steps, start=1):
        lines.append(f"  {idx}. {_describe_step(step)}")
    if plan.assertions:
        lines.append(f"收尾断言（{len(plan.assertions)}）：")
        for idx, a in enumerate(plan.assertions, start=1):
            loc = f" @ {a.locator.describe()}" if a.locator else ""
            exp = f" 期望={a.expected}" if a.expected is not None else ""
            cnt = f" 数量={a.count}" if a.count is not None else ""
            lines.append(f"  {idx}. {a.type.value}{loc}{exp}{cnt}")
    return "\n".join(lines)


def _describe_step(step: Step) -> str:
    if step.action == ActionType.NAVIGATE:
        return f"navigate -> {step.target}"
    loc = step.locator.describe() if step.locator else ""
    if step.value_from_secret:
        val = f" 值=<secret:{step.value_from_secret}>"
    elif step.value is not None:
        val = f" 值={step.value}"
    else:
        val = ""
    return f"{step.action.value} @ {loc}{val}"
