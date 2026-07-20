"""Convert deterministic failures and runtime facts into reviewable findings."""

from __future__ import annotations

from uuid import uuid4

from ..domain.results import AssertionResult, Finding, Status, StepResult


def build_findings(steps: list[StepResult], assertions: list[AssertionResult], reproduction: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for step in steps:
        if step.status != Status.PASSED:
            facts = [f"步骤 {step.index} 状态为 {step.status.value}"]
            if step.error_message:
                facts.append(step.error_message)
            findings.append(Finding(
                id=f"finding-{uuid4().hex[:10]}",
                title=f"步骤 {step.index} 未完成：{step.description or step.action}",
                category=step.failure_category.value if step.failure_category else "execution",
                severity="High" if step.failure_category and step.failure_category.value == "security" else "Medium",
                actual_result=step.error_message or "动作未成功完成",
                expected_result="动作成功并进入可验证的下一状态",
                facts=facts,
                inference="可能是产品、环境、测试数据、定位或时序问题，需结合证据人工确认。",
                evidence=[path for path in [step.before.screenshot if step.before else None, step.after.screenshot if step.after else None] if path],
                reproduction_steps=reproduction,
            ))
        after = step.after
        if after:
            runtime = after.console_errors + after.page_errors + after.failed_requests
            if runtime:
                findings.append(Finding(
                    id=f"finding-{uuid4().hex[:10]}",
                    title=f"步骤 {step.index} 观察到运行时异常",
                    category="runtime",
                    severity="Medium",
                    confidence="high",
                    actual_result=f"采集到 {len(runtime)} 条控制台、页面或网络异常",
                    expected_result="关键页面操作期间不出现未忽略的运行时异常",
                    facts=runtime,
                    inference="异常是否影响业务目标尚未确定。",
                    evidence=[after.screenshot] if after.screenshot else [],
                    reproduction_steps=reproduction[:step.index],
                ))
    for assertion in assertions:
        if assertion.status != Status.PASSED:
            expected = assertion.expected_summary
            if not expected or expected.strip().lower() in {"none", "null"}:
                expected = assertion.description or assertion.type
            findings.append(Finding(
                id=f"finding-{uuid4().hex[:10]}",
                title=f"预期结果未满足：{assertion.description or assertion.type}",
                category="assertion",
                severity="High",
                confidence="high",
                actual_result=assertion.actual_summary or assertion.error_message or "无法获得实际结果",
                expected_result=expected,
                facts=[f"断言状态为 {assertion.status.value}", assertion.actual_summary or ""],
                inference="明确断言未满足，但仍需排除测试数据和环境问题。",
                evidence=[assertion.screenshot] if assertion.screenshot else [],
                reproduction_steps=reproduction,
            ))
    return findings
