"""把受约束的中文自然语言流程转换为可审核计划。

这不是“万能 AI”。无法确定动作或元素时必须返回警告或拒绝规划，绝不
把未执行的步骤伪装成成功。复杂流程可通过模型供应商生成同一 TestPlan
Schema，或由用户在 GUI 中补充稳定定位器。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.models import (
    ActionType,
    Assertion,
    AssertionType,
    Locator,
    Precondition,
    Step,
    TestPlan,
)


class PlanningError(ValueError):
    """自然语言不足以生成安全、确定的执行计划。"""


@dataclass(frozen=True)
class PlanningResult:
    plan: TestPlan
    warnings: list[str]
    mode: str = "deterministic-rules"


_QUOTED = re.compile(r"[\"'“”‘’]([^\"'“”‘’]+)[\"'“”‘’]")


def plan_from_draft(
    *,
    name: str,
    target_url: str,
    flow: str,
    role: str | None = None,
    preconditions: str | None = None,
    expectation: str | None = None,
) -> PlanningResult:
    if not target_url.strip().startswith(("http://", "https://")):
        raise PlanningError("目标地址必须以 http:// 或 https:// 开头")
    clauses = [
        item.strip(" ，,。；;\t")
        for item in re.split(r"[\n；;。]+", flow.strip())
        if item.strip(" ，,。；;\t")
    ]
    if not clauses:
        raise PlanningError("请填写至少一个可执行步骤")

    steps: list[Step] = [
        Step(action=ActionType.NAVIGATE, target="/", description="打开目标网站")
    ]
    assertions: list[Assertion] = []
    warnings: list[str] = []

    for clause in clauses:
        pieces = [p.strip() for p in re.split(r"[,，](?=(?:[^\"“”]*[\"“”][^\"“”]*[\"“”])*[^\"“”]*$)", clause) if p.strip()]
        for piece in pieces:
            parsed = _parse_piece(piece)
            if isinstance(parsed, Step):
                steps.append(parsed)
            elif isinstance(parsed, Assertion):
                assertions.append(parsed)
            else:
                warnings.append(f"无法确定执行方式：{piece}")

    if expectation and expectation.strip():
        parsed_expectation = _parse_expectation(expectation.strip())
        if parsed_expectation:
            if not any(
                _assertion_key(item) == _assertion_key(parsed_expectation)
                for item in assertions
            ):
                assertions.append(parsed_expectation)
        else:
            warnings.append(f"期望结果需要补充为可验证文本、URL 或元素：{expectation.strip()}")

    executable = [step for step in steps if step.action != ActionType.NAVIGATE]
    if not executable and not assertions:
        raise PlanningError(
            "没有识别出可执行动作或断言。请使用明确格式，例如：点击“登录”；"
            "在“用户名”输入“admin”；确认看到“控制台”。"
        )

    return PlanningResult(
        plan=TestPlan(
            name=name.strip() or "未命名测试",
            base_url=target_url.strip(),
            role=role.strip() if role and role.strip() else None,
            preconditions=[Precondition(description=preconditions.strip())]
            if preconditions and preconditions.strip()
            else [],
            steps=steps,
            assertions=assertions,
        ),
        warnings=warnings,
    )


def _quoted_or_tail(text: str, marker: str) -> str | None:
    quoted = _QUOTED.findall(text)
    if quoted:
        return quoted[0].strip()
    tail = text.split(marker, 1)[-1]
    tail = re.sub(r"(?:按钮|链接|菜单|选项)$", "", tail).strip(" ：:")
    return tail or None


def _parse_piece(text: str) -> Step | Assertion | None:
    normalized = re.sub(r"^(然后|随后|接着|并且|并|再|最后)", "", text).strip()
    if normalized.startswith(("打开", "访问", "进入")):
        target = _QUOTED.findall(normalized)
        if target and target[0].startswith("/"):
            return Step(action=ActionType.NAVIGATE, target=target[0], description=normalized)
        if normalized.startswith(("打开网站", "访问网站", "进入网站", "打开目标")):
            return None  # 首个 navigate 已覆盖。
        label = _quoted_or_tail(normalized, normalized[:2])
        return Step(
            action=ActionType.CLICK,
            locator=Locator(text=label),
            description=normalized,
        ) if label else None
    if "点击" in normalized:
        label = _quoted_or_tail(normalized, "点击")
        return Step(
            action=ActionType.CLICK,
            locator=Locator(role="button", name=label),
            description=normalized,
        ) if label else None
    if "输入" in normalized or "填写" in normalized:
        quoted = _QUOTED.findall(normalized)
        if len(quoted) >= 2:
            sensitive = _is_sensitive_label(quoted[0])
            return Step(
                action=ActionType.FILL,
                locator=Locator(label=quoted[0]),
                value=None if sensitive else quoted[1],
                value_from_secret="TEST_PASSWORD" if sensitive else None,
                description=f'在“{quoted[0]}”输入密钥引用' if sensitive else normalized,
            )
        match = re.search(r"(?:在)?(.+?)(?:中|里)?(?:输入|填写)(?:为|：|:)?(.+)$", normalized)
        if match:
            label, value = (item.strip(" ：:") for item in match.groups())
            sensitive = _is_sensitive_label(label)
            return Step(
                action=ActionType.FILL,
                locator=Locator(label=label),
                value=None if sensitive else value,
                value_from_secret="TEST_PASSWORD" if sensitive else None,
                description=f'在“{label}”输入密钥引用' if sensitive else normalized,
            )
        return None
    if "选择" in normalized:
        quoted = _QUOTED.findall(normalized)
        if len(quoted) >= 2:
            return Step(
                action=ActionType.SELECT,
                locator=Locator(label=quoted[0]),
                value=quoted[1],
                description=normalized,
            )
        return None
    if normalized.startswith("等待"):
        label = _quoted_or_tail(normalized, "等待")
        return Step(
            action=ActionType.WAIT_FOR,
            locator=Locator(text=label),
            description=normalized,
        ) if label else None
    if "截图" in normalized:
        return Step(action=ActionType.SCREENSHOT, description=normalized)
    if any(token in normalized for token in ("确认看到", "应看到", "可见", "出现")):
        text_value = _QUOTED.findall(normalized)
        value = text_value[-1].strip() if text_value else re.sub(
            r"^(确认看到|应看到)|(?:可见|出现)$", "", normalized
        ).strip(" ：:")
        return Assertion(
            type=AssertionType.VISIBLE,
            locator=Locator(text=value),
            description=normalized,
        ) if value else None
    if "地址包含" in normalized or "URL包含" in normalized.upper():
        value = _QUOTED.findall(normalized)
        expected = value[-1] if value else normalized.split("包含", 1)[-1].strip()
        return Assertion(type=AssertionType.URL_CONTAINS, expected=expected, description=normalized)
    return None


def _is_sensitive_label(label: str) -> bool:
    normalized = label.lower().replace(" ", "")
    return any(token in normalized for token in ("密码", "password", "passwd", "token", "apikey", "secret"))


def _parse_expectation(text: str) -> Assertion | None:
    parsed = _parse_piece(text)
    if isinstance(parsed, Assertion):
        return parsed
    quoted = _QUOTED.findall(text)
    if quoted and any(token in text for token in ("显示", "看到", "出现", "可见")):
        return Assertion(
            type=AssertionType.VISIBLE,
            locator=Locator(text=quoted[-1]),
            description=text,
        )
    return None


def _assertion_key(assertion: Assertion) -> tuple:
    locator = assertion.locator.model_dump(exclude_none=True) if assertion.locator else None
    return assertion.type.value, str(locator), assertion.expected, assertion.count
