"""受约束的 Playwright 测试计划执行器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from ..artifacts import ArtifactManager
from ..assertions.checks import check_assertion
from ..domain.models import ActionType, Step, TestPlan
from ..domain.results import (
    AssertionResult,
    CauseHint,
    FailureCategory,
    RunResult,
    Status,
    StepResult,
)
from ..locating.strategies import resolve_locator
from ..security.policy import DomainPolicy, SecurityError, resolve_env_placeholder, resolve_secret
from ..security.redaction import Redactor


@dataclass(frozen=True)
class RunnerConfig:
    artifacts_root: Path = Path("artifacts")
    headless: bool = True
    timeout_ms: int = 10_000
    slow_mo_ms: int = 0


def run_plan(plan: TestPlan, config: RunnerConfig | None = None) -> tuple[RunResult, Path]:
    """执行计划并返回运行结果与产物目录。"""
    cfg = config or RunnerConfig()
    started = _now()
    run_id = f"{started:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    redactor = Redactor()
    artifacts = ArtifactManager(cfg.artifacts_root, run_id, redactor)
    steps: list[StepResult] = []
    assertions: list[AssertionResult] = []
    hints: list[CauseHint] = []
    failed_step: int | None = None
    base_url = resolve_env_placeholder(plan.base_url).rstrip("/")
    policy = DomainPolicy(base_url)
    policy.check_url(base_url)
    overall = Status.PASSED

    artifacts.event("run_started", run_id=run_id, plan_name=plan.name, role=plan.role)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=cfg.headless, slow_mo=cfg.slow_mo_ms)
        context = browser.new_context(viewport={"width": 1440, "height": 960})
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
        page = context.new_page()
        page.set_default_timeout(cfg.timeout_ms)
        try:
            for index, step in enumerate(plan.steps, start=1):
                step_started = _now()
                summary = _step_summary(step, redactor)
                artifacts.event("step_started", index=index, action=step.action.value, target=summary)
                try:
                    _execute_step(page, step, base_url, policy, redactor)
                    image_path, relative = artifacts.screenshot_path(f"step-{index}-passed")
                    try:
                        page.screenshot(path=str(image_path), full_page=True)
                    except PlaywrightError:
                        relative = None
                    result = StepResult(
                        index=index,
                        action=step.action.value,
                        description=step.description,
                        target_summary=summary,
                        status=Status.PASSED,
                        started_at=step_started,
                        ended_at=_now(),
                        screenshot=relative,
                    )
                    steps.append(result)
                    artifacts.event("step_passed", index=index, duration_ms=result.duration_ms)
                except Exception as exc:
                    category = _failure_category(exc)
                    image_path, relative = artifacts.screenshot_path(f"step-{index}-failure")
                    try:
                        page.screenshot(path=str(image_path), full_page=True)
                    except PlaywrightError:
                        relative = None
                    message = redactor.scrub(str(exc))
                    result = StepResult(
                        index=index,
                        action=step.action.value,
                        description=step.description,
                        target_summary=summary,
                        status=Status.ERROR,
                        started_at=step_started,
                        ended_at=_now(),
                        error_message=message,
                        failure_category=category,
                        screenshot=relative,
                    )
                    steps.append(result)
                    failed_step = index
                    overall = Status.ERROR
                    hints.append(_cause_hint(category, index, message))
                    artifacts.event(
                        "step_failed", index=index, category=category.value,
                        error=message, screenshot=relative,
                    )
                    break

            if overall == Status.PASSED:
                for index, assertion in enumerate(plan.assertions, start=1):
                    try:
                        outcome = check_assertion(page, assertion)
                        status = Status.PASSED if outcome.passed else Status.FAILED
                        screenshot: str | None = None
                        if not outcome.passed:
                            image_path, screenshot = artifacts.screenshot_path(
                                f"assertion-{index}-failure"
                            )
                            page.screenshot(path=str(image_path), full_page=True)
                            overall = Status.FAILED
                            hints.append(
                                _cause_hint(
                                    FailureCategory.ASSERTION, index,
                                    f"断言未满足：{assertion.description or assertion.type.value}",
                                )
                            )
                        result = AssertionResult(
                            index=index,
                            type=assertion.type.value,
                            description=assertion.description,
                            detail=redactor.scrub(assertion.locator.describe()) if assertion.locator else assertion.type.value,
                            status=status,
                            expected_summary=redactor.scrub(str(assertion.expected if assertion.expected is not None else assertion.count)),
                            actual_summary=redactor.scrub(outcome.actual),
                            screenshot=screenshot,
                        )
                        assertions.append(result)
                        artifacts.event("assertion_finished", index=index, status=status.value, actual=result.actual_summary)
                    except Exception as exc:
                        message = redactor.scrub(str(exc))
                        category = _failure_category(exc)
                        image_path, relative = artifacts.screenshot_path(f"assertion-{index}-error")
                        try:
                            page.screenshot(path=str(image_path), full_page=True)
                        except PlaywrightError:
                            relative = None
                        assertions.append(
                            AssertionResult(
                                index=index,
                                type=assertion.type.value,
                                description=assertion.description,
                                detail=assertion.type.value,
                                status=Status.ERROR,
                                error_message=message,
                                screenshot=relative,
                            )
                        )
                        overall = Status.ERROR
                        hints.append(_cause_hint(category, index, message))
        finally:
            try:
                context.tracing.stop(path=str(artifacts.trace_path))
                artifacts.redact_trace()
            finally:
                context.close()
                browser.close()

    ended = _now()
    reproduction = [_step_summary(step, redactor) for step in plan.steps[:len(steps)]]
    result = RunResult(
        run_id=run_id,
        plan_name=plan.name,
        role=plan.role,
        base_url_summary=redactor.scrub(base_url),
        status=overall,
        started_at=started,
        ended_at=ended,
        steps=steps,
        assertions=assertions,
        failed_step_index=failed_step,
        reproduction_steps=reproduction,
        cause_hints=hints,
    )
    artifacts.event("run_finished", status=overall.value, duration_ms=result.duration_ms)
    artifacts.finalize(result)
    return result, artifacts.run_dir


def _execute_step(page, step: Step, base_url: str, policy: DomainPolicy, redactor: Redactor) -> None:
    if step.action == ActionType.NAVIGATE:
        target = step.target or "/"
        url = target if urlparse(target).scheme else urljoin(base_url + "/", target.lstrip("/"))
        policy.check_url(url)
        page.goto(url, wait_until="domcontentloaded")
        return

    if step.action == ActionType.SCREENSHOT:
        # 每一步执行完成后都会统一采集页面截图；该动作只提供显式检查点。
        return

    locator = resolve_locator(page, step.locator)  # type: ignore[arg-type]
    if step.action == ActionType.CLICK:
        locator.first.click()
    elif step.action == ActionType.FILL:
        value = resolve_secret(step.value_from_secret, redactor) if step.value_from_secret else step.value or ""
        locator.first.fill(value)
    elif step.action == ActionType.SELECT:
        value = resolve_secret(step.value_from_secret, redactor) if step.value_from_secret else step.value or ""
        locator.first.select_option(value)
    elif step.action == ActionType.WAIT_FOR:
        locator.first.wait_for(state="visible")
    else:
        raise ValueError(f"未实现的动作：{step.action.value}")


def _step_summary(step: Step, redactor: Redactor) -> str:
    description = step.description or step.action.value
    if step.action == ActionType.NAVIGATE:
        return redactor.scrub(f"{description} -> {step.target}")
    if step.action == ActionType.SCREENSHOT:
        return redactor.scrub(description)
    target = step.locator.describe() if step.locator else ""
    value = f" value={step.value}" if step.value is not None else ""
    if step.value_from_secret:
        value = f" value=<secret:{step.value_from_secret}>"
    return redactor.scrub(f"{description} @ {target}{value}")


def _failure_category(exc: Exception) -> FailureCategory:
    if isinstance(exc, SecurityError):
        return FailureCategory.SECURITY
    if isinstance(exc, PlaywrightTimeoutError):
        message = str(exc).lower()
        return FailureCategory.LOCATOR if "locator" in message or "waiting for" in message else FailureCategory.TIMEOUT
    if isinstance(exc, PlaywrightError):
        return FailureCategory.NAVIGATION if "navigation" in str(exc).lower() else FailureCategory.LOCATOR
    return FailureCategory.UNKNOWN


def _cause_hint(category: FailureCategory, index: int, message: str) -> CauseHint:
    messages = {
        FailureCategory.LOCATOR: "页面元素可能不存在、尚未加载或定位信息已变化，请检查页面和稳定定位属性。",
        FailureCategory.TIMEOUT: "页面或网络响应可能超过配置超时，请结合 trace 检查加载状态。",
        FailureCategory.NAVIGATION: "页面导航或网络请求可能失败，请检查地址、服务状态和网络策略。",
        FailureCategory.SECURITY: "运行被安全策略拒绝，请核对域名、动作和密钥授权范围。",
        FailureCategory.ASSERTION: "页面实际状态与预期不一致，可能是业务缺陷、测试数据或预期配置问题。",
        FailureCategory.UNKNOWN: "发生未分类异常，请结合错误消息、截图和 trace 人工复核。",
    }
    return CauseHint(
        category=category,
        message=messages.get(category, messages[FailureCategory.UNKNOWN]),
        evidence=[f"步骤/断言 {index}", message[:300]],
        confidence="medium" if category != FailureCategory.UNKNOWN else "low",
    )


def _now() -> datetime:
    return datetime.now().astimezone()
