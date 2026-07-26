"""受约束的 Playwright 测试计划执行器。"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Callable
from typing import Any
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
from ..security.policy import DomainPolicy, SecurityError, guard_playwright_route, guard_playwright_websocket, resolve_env_placeholder, resolve_secret
from ..security.redaction import Redactor
from ..security.screenshot_privacy import screenshot_privacy_masks
from .observation import ObservationCollector
from .compiler import compile_test
from .confirmation import confirmation_match
from .findings import build_findings
from .bridge_adapter import CanvasAppBridgeAdapter, PreparedBridgeAction, create_bridge_adapter
from .stability import finalize_canvas_evidence, prepare_action


@dataclass(frozen=True)
class RunnerConfig:
    artifacts_root: Path = Path("artifacts")
    headless: bool = True
    timeout_ms: int = 30_000
    slow_mo_ms: int = 0
    allowed_hosts: tuple[str, ...] = ()
    allow_private_network: bool = False
    storage_state: dict | None = None
    replay_mode: str = "exploration"
    onboarding_level: str | None = None
    run_id: str | None = None
    cancel_event: Event | None = None
    max_duration_seconds: int | None = None
    progress_callback: Callable[[dict], None] | None = None
    agent_planner: Any | None = None
    max_model_calls: int = 0
    max_steps: int | None = None
    no_progress_limit: int = 3
    forbidden_actions: tuple[str, ...] = ()
    visual_adapter: Any | None = None
    project_id: str | None = None
    environment_id: str | None = None
    environment_updated_at: str | None = None
    environment_variables: tuple[tuple[str, str], ...] = ()
    secret_refs: tuple[tuple[str, str], ...] = ()
    ignore_rules: tuple[str, ...] = ()
    viewport: tuple[int, int] = (1440, 960)
    device_scale_factor: float = 1.0
    artifact_retention_days: int = 30
    screenshot_mask_selectors: tuple[str, ...] = ()
    app_bridge_enabled: bool = False
    app_bridge_global_name: str = "__WEB_AI_TEST__"
    app_bridge_adapter: str = "generic"
    action_stability_timeout_ms: int = 10_000
    isolation_memory_limit_mb: int = 2048
    isolation_cancel_grace_seconds: float = 8.0
    scenario_id: str | None = None
    scenario_updated_at: str | None = None
    scenario_goal: str | None = None
    confirmation_callback: Callable[[Step, int, str], bool] | None = None
    confirmation_history: list[dict] = field(default_factory=list)


def run_plan(plan: TestPlan, config: RunnerConfig | None = None) -> tuple[RunResult, Path]:
    """执行计划并返回运行结果与产物目录。"""
    cfg = config or RunnerConfig()
    if cfg.agent_planner is not None:
        from .agent_runner import run_agent_plan
        return run_agent_plan(plan, cfg)
    started = _now()
    run_id = cfg.run_id or f"{started:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    redactor = Redactor()
    bridge_adapter = create_bridge_adapter(
        enabled=cfg.app_bridge_enabled,
        global_name=cfg.app_bridge_global_name,
        adapter_name=cfg.app_bridge_adapter,
        timeout_ms=cfg.action_stability_timeout_ms,
        redactor=redactor,
    )
    artifacts = ArtifactManager(
        cfg.artifacts_root, run_id, redactor, cfg.screenshot_mask_selectors
    )
    steps: list[StepResult] = []
    assertions: list[AssertionResult] = []
    hints: list[CauseHint] = []
    failed_step: int | None = None
    environment_variables = dict(cfg.environment_variables)
    secret_refs = dict(cfg.secret_refs)
    base_url = resolve_env_placeholder(plan.base_url, environment_variables).rstrip("/")
    policy = DomainPolicy(
        base_url,
        list(cfg.allowed_hosts),
        allow_private_network=cfg.allow_private_network,
    )
    policy.check_url(base_url)
    overall = Status.PASSED
    completion_reason = "plan_completed"
    run_started_monotonic = monotonic()

    def emit_progress(status: Status, *, ended_at: datetime | None = None) -> None:
        if cfg.progress_callback is None:
            return
        current = ended_at or _now()
        cfg.progress_callback({
            "run_id": run_id,
            "plan_name": plan.name,
            "role": plan.role,
            "base_url_summary": redactor.scrub(base_url),
            "status": status.value,
            "started_at": started.isoformat(),
            "ended_at": current.isoformat(),
            "steps": [item.model_dump(mode="json") for item in steps],
            "assertions": [item.model_dump(mode="json") for item in assertions],
            "failed_step_index": failed_step,
            "reproduction_steps": [_step_summary(item, redactor) for item in plan.steps[:len(steps)]],
            "cause_hints": [item.model_dump(mode="json") for item in hints],
            "findings": [],
            "replay_mode": cfg.replay_mode,
            "onboarding_level": cfg.onboarding_level,
            "stability_level": "A",
            "completion_reason": completion_reason,
            "project_id": cfg.project_id,
            "environment_id": cfg.environment_id,
            "environment_updated_at": cfg.environment_updated_at,
            "artifact_retention_days": cfg.artifact_retention_days,
            "scenario_id": cfg.scenario_id,
            "scenario_updated_at": cfg.scenario_updated_at,
            "scenario_goal": cfg.scenario_goal or plan.name,
            "goal_status": "in_progress",
            "goal_summary": f"已执行 {len(steps)} 个步骤",
            "model_calls": 0,
            "estimated_cost": None,
            "confirmation_history": list(cfg.confirmation_history),
        })

    artifacts.event("run_started", run_id=run_id, plan_name=plan.name, role=plan.role)
    artifacts.event(
        "app_bridge_configuration",
        enabled=bridge_adapter is not None,
        adapter=cfg.app_bridge_adapter,
        global_name=cfg.app_bridge_global_name,
        fallback_mode="locator_only" if bridge_adapter is None else "bridge_v1",
    )
    artifacts.write_json("plan.json", plan.model_dump(mode="json", exclude_none=True))
    emit_progress(Status.RUNNING)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=cfg.headless, slow_mo=cfg.slow_mo_ms)
        context = browser.new_context(
            viewport={"width": cfg.viewport[0], "height": cfg.viewport[1]},
            device_scale_factor=cfg.device_scale_factor,
            storage_state=cfg.storage_state,
            accept_downloads=False,
            service_workers="block",
        )
        context.route(
            "**/*",
            lambda route: guard_playwright_route(
                route,
                policy,
                lambda message, url, resource_type: artifacts.event(
                    "network_request_blocked",
                    reason=redactor.scrub(message),
                    url=redactor.scrub(url),
                    resource_type=resource_type,
                ),
            ),
        )
        context.route_web_socket(
            "**/*",
            lambda route: guard_playwright_websocket(
                route,
                policy,
                lambda message, url, resource_type: artifacts.event(
                    "network_request_blocked",
                    reason=redactor.scrub(message),
                    url=redactor.scrub(url),
                    resource_type=resource_type,
                ),
            ),
        )
        context.tracing.start(screenshots=False, snapshots=True, sources=False)
        artifacts.event("trace_pixel_privacy_enabled", screenshots_embedded=False)
        page = context.new_page()
        collector = ObservationCollector(page, artifacts, redactor, cfg.ignore_rules)
        page.set_default_timeout(cfg.timeout_ms)
        page.set_default_navigation_timeout(cfg.timeout_ms)
        try:
            for index, step in enumerate(plan.steps, start=1):
                if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                    overall = Status.CANCELLED
                    completion_reason = "cancelled_by_user"
                    artifacts.event("run_cancelled", before_step=index)
                    break
                if cfg.max_duration_seconds is not None and monotonic() - run_started_monotonic >= cfg.max_duration_seconds:
                    overall = Status.INCOMPLETE
                    completion_reason = "time_limit_exceeded"
                    artifacts.event("run_limit_reached", limit="max_duration_seconds", before_step=index)
                    break
                step_started = _now()
                summary = _step_summary(step, redactor)
                artifacts.event("step_started", index=index, action=step.action.value, target=summary)
                before_shot = _capture_screenshot(page, artifacts, f"step-{index}-before")
                before = collector.capture(before_shot)
                confirmation_term = confirmation_match(step)
                if confirmation_term:
                    if cfg.confirmation_callback is None:
                        raise SecurityError(f"危险动作需要人工确认：{confirmation_term}")
                    artifacts.event("dangerous_action_confirmation_requested", index=index, rule=confirmation_term, target=summary)
                    if not cfg.confirmation_callback(step, index, confirmation_term):
                        was_cancelled = cfg.cancel_event is not None and cfg.cancel_event.is_set()
                        result = StepResult(
                            index=index,
                            action=step.action.value,
                            description=step.description,
                            target_summary=summary,
                            status=Status.SKIPPED,
                            started_at=step_started,
                            ended_at=_now(),
                            error_message="运行已由用户取消，动作未执行" if was_cancelled else "危险动作未获批准，动作未执行",
                            failure_category=FailureCategory.SECURITY,
                            screenshot=before_shot,
                            locator_basis=step.locator.describe() if step.locator else None,
                            execution_mode=step.execution_mode.value,
                            stability_level=step.stability_level.value,
                            stability_reason=step.stability_reason,
                            before=before,
                        )
                        steps.append(result)
                        overall = Status.CANCELLED
                        completion_reason = "cancelled_by_user" if was_cancelled else "dangerous_action_rejected"
                        artifacts.event("dangerous_action_rejected", index=index, rule=confirmation_term)
                        emit_progress(Status.CANCELLED)
                        break
                    artifacts.event("dangerous_action_approved", index=index, rule=confirmation_term)
                stability_evidence = None
                canvas_evidence = None
                try:
                    prepared = prepare_action(
                        page, step,
                        bridge_adapter=bridge_adapter,
                        timeout_ms=min(cfg.timeout_ms, cfg.action_stability_timeout_ms),
                    )
                    artifacts.event("action_stability_checked", index=index, **prepared.evidence)
                    stability_evidence = prepared.evidence
                    execution_detail = _execute_step(
                        page, step, base_url, policy, redactor,
                        environment_variables=environment_variables, secret_refs=secret_refs,
                        bridge_adapter=bridge_adapter, bridge_prepared=prepared.bridge_action,
                    )
                    canvas_evidence = finalize_canvas_evidence(
                        page, step,
                        prepared=prepared,
                        bridge_adapter=bridge_adapter,
                        execution_detail=execution_detail,
                        before_screenshot=before_shot,
                        after_screenshot=None,
                    )
                    relative = _capture_screenshot(
                        page, artifacts, f"step-{index}-after"
                    )
                    if canvas_evidence is not None:
                        canvas_evidence["afterScreenshot"] = relative
                        artifacts.event("canvas_evidence_collected", index=index, **canvas_evidence)
                    after = collector.capture(relative)
                    result = StepResult(
                        index=index,
                        action=step.action.value,
                        description=step.description,
                        target_summary=summary,
                        status=Status.PASSED,
                        started_at=step_started,
                        ended_at=_now(),
                        screenshot=relative,
                        locator_basis=step.locator.describe() if step.locator else None,
                        execution_mode=step.execution_mode.value,
                        stability_level=step.stability_level.value,
                        stability_reason=step.stability_reason,
                        computer_use_triggered=step.computer_use_triggered,
                        computer_use_reason=step.computer_use_reason,
                        coordinate_source=execution_detail.get("coordinateSource"),
                        app_bridge_result=execution_detail.get("appBridgeResult"),
                        stability_evidence=prepared.evidence,
                        canvas_evidence=canvas_evidence,
                        before=before,
                        after=after,
                    )
                    steps.append(result)
                    artifacts.event("step_passed", index=index, duration_ms=result.duration_ms)
                    emit_progress(Status.RUNNING)
                    if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                        overall = Status.CANCELLED
                        completion_reason = "cancelled_by_user"
                        artifacts.event("run_cancelled", after_step=index)
                        break
                except Exception as exc:
                    category = _failure_category(exc)
                    relative = _capture_screenshot(
                        page, artifacts, f"step-{index}-after-failure", stop_loading=True
                    )
                    after = collector.capture(relative)
                    message = redactor.scrub(str(exc))
                    if stability_evidence is None:
                        stability_evidence = {"checked": True, "passed": False, "error": message}
                        artifacts.event("action_stability_failed", index=index, error=message)
                    if step.execution_mode.value in {"visual", "app_bridge"}:
                        canvas_evidence = {
                            **(canvas_evidence or {}),
                            "mode": step.execution_mode.value,
                            "action": step.action.value,
                            "semanticTarget": step.visual_target or step.bridge_target_id,
                            "beforeScreenshot": before_shot,
                            "afterScreenshot": relative,
                            "traceArtifact": "trace.zip",
                            "collectionStatus": "failed",
                            "failurePhase": "stability" if not stability_evidence.get("passed") else "execution_or_after_state",
                            "error": message,
                        }
                        artifacts.event("canvas_evidence_failed", index=index, **canvas_evidence)
                    was_cancelled = cfg.cancel_event is not None and cfg.cancel_event.is_set()
                    result = StepResult(
                        index=index,
                        action=step.action.value,
                        description=step.description,
                        target_summary=summary,
                        status=Status.CANCELLED if was_cancelled else Status.ERROR,
                        started_at=step_started,
                        ended_at=_now(),
                        error_message=message,
                        failure_category=category,
                        screenshot=relative,
                        locator_basis=step.locator.describe() if step.locator else None,
                        execution_mode=step.execution_mode.value,
                        stability_level=step.stability_level.value,
                        stability_reason=step.stability_reason,
                        computer_use_triggered=step.computer_use_triggered,
                        computer_use_reason=step.computer_use_reason,
                        stability_evidence=stability_evidence,
                        canvas_evidence=canvas_evidence,
                        before=before,
                        after=after,
                    )
                    steps.append(result)
                    if was_cancelled:
                        overall = Status.CANCELLED
                        completion_reason = "cancelled_by_user"
                    else:
                        failed_step = index
                        overall = Status.ERROR
                        hints.append(_cause_hint(category, index, message))
                    artifacts.event(
                        "step_failed", index=index, category=category.value,
                        error=message, screenshot=relative,
                        observed_facts={
                            "url": after.url,
                            "title": after.title,
                            "consoleErrors": after.console_errors,
                            "pageErrors": after.page_errors,
                            "failedRequests": after.failed_requests,
                        },
                    )
                    completion_reason = "cancelled_by_user" if was_cancelled else "execution_failed"
                    emit_progress(Status.RUNNING)
                    break

            if overall == Status.PASSED:
                for index, assertion in enumerate(plan.assertions, start=1):
                    if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                        overall = Status.CANCELLED
                        completion_reason = "cancelled_by_user"
                        artifacts.event("run_cancelled", before_assertion=index)
                        break
                    if cfg.max_duration_seconds is not None and monotonic() - run_started_monotonic >= cfg.max_duration_seconds:
                        overall = Status.INCOMPLETE
                        completion_reason = "time_limit_exceeded"
                        artifacts.event("run_limit_reached", limit="max_duration_seconds", before_assertion=index)
                        break
                    try:
                        outcome = check_assertion(page, assertion)
                        status = Status.PASSED if outcome.passed else Status.FAILED
                        screenshot: str | None = None
                        if not outcome.passed:
                            screenshot = _capture_screenshot(
                                page, artifacts, f"assertion-{index}-failure"
                            )
                            overall = Status.FAILED
                            completion_reason = "assertion_failed"
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
                        emit_progress(Status.RUNNING)
                    except Exception as exc:
                        message = redactor.scrub(str(exc))
                        category = _failure_category(exc)
                        relative = _capture_screenshot(
                            page, artifacts, f"assertion-{index}-error", stop_loading=True
                        )
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
                        completion_reason = "execution_failed"
                        hints.append(_cause_hint(category, index, message))
                        emit_progress(Status.RUNNING)
        finally:
            try:
                context.tracing.stop(path=str(artifacts.trace_path))
                artifacts.redact_trace()
            finally:
                context.close()
                browser.close()

    ended = _now()
    reproduction = [_step_summary(step, redactor) for step in plan.steps[:len(steps)]]
    findings = build_findings(steps, assertions, reproduction)
    source, generated_test = compile_test(plan)
    generated_test.source_path = artifacts.write_text(generated_test.source_path, source)
    stability = generated_test.stability_level
    goal_status, goal_summary = _goal_outcome(overall, assertions, completion_reason)
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
        findings=findings,
        generated_test=generated_test,
        replay_mode=cfg.replay_mode,
        onboarding_level=cfg.onboarding_level,
        stability_level=stability,
        completion_reason=completion_reason,
        project_id=cfg.project_id,
        environment_id=cfg.environment_id,
        environment_updated_at=cfg.environment_updated_at,
        artifact_retention_days=cfg.artifact_retention_days,
        scenario_id=cfg.scenario_id,
        scenario_updated_at=cfg.scenario_updated_at,
        scenario_goal=cfg.scenario_goal or plan.name,
        goal_status=goal_status,
        goal_summary=goal_summary,
        confirmation_history=list(cfg.confirmation_history),
    )
    artifacts.event("run_finished", status=overall.value, duration_ms=result.duration_ms)
    artifacts.finalize(result)
    emit_progress(overall, ended_at=ended)
    return result, artifacts.run_dir


def _goal_outcome(status: Status, assertions: list[AssertionResult], completion_reason: str) -> tuple[str, str]:
    passed = sum(item.status == Status.PASSED for item in assertions)
    total = len(assertions)
    assertion_summary = f"断言通过 {passed}/{total}" if total else "无收尾断言"
    if status == Status.PASSED:
        return "achieved", f"场景目标已完成；{assertion_summary}"
    if status in {Status.CANCELLED, Status.SYSTEM_ERROR, Status.ERROR, Status.INCOMPLETE}:
        return "incomplete", f"场景未完整执行；{assertion_summary}；结束原因 {completion_reason}"
    return "not_achieved", f"场景目标未完成；{assertion_summary}；结束原因 {completion_reason}"


def _execute_step(
    page, step: Step, base_url: str, policy: DomainPolicy, redactor: Redactor,
    *, environment_variables: dict[str, str] | None = None, secret_refs: dict[str, str] | None = None,
    bridge_adapter: CanvasAppBridgeAdapter | None = None,
    bridge_prepared: PreparedBridgeAction | None = None,
) -> dict:
    if step.action == ActionType.NAVIGATE:
        target = step.target or "/"
        url = target if urlparse(target).scheme else urljoin(base_url + "/", target.lstrip("/"))
        policy.check_url(url)
        policy.clear_rejection()
        try:
            response = page.goto(url, wait_until="commit")
        except PlaywrightError as exc:
            rejection = policy.consume_rejection()
            if rejection:
                raise SecurityError(rejection) from exc
            raise
        policy.check_url(page.url)
        if response is not None and response.status >= 400:
            raise PlaywrightError(f"页面返回 HTTP {response.status}：{url}")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8_000)
        except PlaywrightTimeoutError:
            # 已收到页面响应时，不因第三方资源拖慢 DOMContentLoaded 而制造随机失败。
            # 后续定位与断言仍会按完整超时时间验证页面是否真正可操作。
            pass
        return {}

    if step.action == ActionType.SCREENSHOT:
        # 每一步执行完成后都会统一采集页面截图；该动作只提供显式检查点。
        return {}

    if step.action == ActionType.BACK:
        policy.clear_rejection()
        try:
            page.go_back(wait_until="commit")
        except PlaywrightError as exc:
            rejection = policy.consume_rejection()
            if rejection:
                raise SecurityError(rejection) from exc
            raise
        policy.check_url(page.url)
        return {}
    if step.action == ActionType.RELOAD:
        policy.clear_rejection()
        try:
            page.reload(wait_until="commit")
        except PlaywrightError as exc:
            rejection = policy.consume_rejection()
            if rejection:
                raise SecurityError(rejection) from exc
            raise
        policy.check_url(page.url)
        return {}
    if step.action == ActionType.SCROLL and step.locator is None:
        page.mouse.wheel(0, step.scroll_delta_y)
        return {}
    if step.action == ActionType.BRIDGE_CLICK:
        if bridge_adapter is None or bridge_prepared is None:
            raise SecurityError("Bridge 动作缺少已授权的动作前准备结果")
        detail = bridge_prepared.position
        viewport = page.viewport_size or {"width": 0, "height": 0}
        if detail["x"] < 0 or detail["y"] < 0 or detail["x"] > viewport["width"] or detail["y"] > viewport["height"]:
            raise SecurityError("桥接目标坐标超出浏览器 viewport")
        page.mouse.click(detail["x"], detail["y"])
        result = bridge_adapter.complete_click(page, bridge_prepared)
        return {"coordinateSource": f"app_bridge:{step.bridge_target_id}", "appBridgeResult": result}
    if step.action == ActionType.PRESS and step.locator is None:
        page.keyboard.press(step.value or "")
        return {}

    if step.action in {ActionType.VISUAL_CLICK, ActionType.VISUAL_HOVER, ActionType.VISUAL_SCROLL, ActionType.VISUAL_DRAG}:
        if step.locator:
            locator = resolve_locator(page, step.locator)
            box = locator.first.bounding_box()
            if box is None:
                raise PlaywrightError("视觉目标所在区域不可见")
            source = "region-relative"
        else:
            viewport = page.viewport_size
            if not viewport:
                raise PlaywrightError("无法获取浏览器 viewport")
            box = {"x": 0, "y": 0, "width": viewport["width"], "height": viewport["height"]}
            source = "viewport-relative"
        position = step.relative_position
        assert position is not None
        x = box["x"] + box["width"] * position.x_ratio
        y = box["y"] + box["height"] * position.y_ratio
        if not (box["x"] <= x <= box["x"] + box["width"] and box["y"] <= y <= box["y"] + box["height"]):
            raise SecurityError("视觉建议坐标超出授权区域边界")
        if step.action == ActionType.VISUAL_CLICK:
            page.mouse.click(x, y)
        elif step.action == ActionType.VISUAL_HOVER:
            page.mouse.move(x, y)
        elif step.action == ActionType.VISUAL_SCROLL:
            page.mouse.move(x, y)
            page.mouse.wheel(0, step.scroll_delta_y)
        else:
            end = step.relative_end_position
            assert end is not None
            end_x = box["x"] + box["width"] * end.x_ratio
            end_y = box["y"] + box["height"] * end.y_ratio
            if not (box["x"] <= end_x <= box["x"] + box["width"] and box["y"] <= end_y <= box["y"] + box["height"]):
                raise SecurityError("视觉拖拽终点超出授权区域边界")
            page.mouse.move(x, y)
            page.mouse.down()
            try:
                page.mouse.move(end_x, end_y, steps=10)
            finally:
                page.mouse.up()
        return {"coordinateSource": f"{source}:{position.x_ratio:.4f},{position.y_ratio:.4f}"}

    locator = resolve_locator(page, step.locator)  # type: ignore[arg-type]
    if step.action == ActionType.CLICK:
        locator.first.click()
    elif step.action == ActionType.FILL:
        value = resolve_secret(step.value_from_secret, redactor, secret_refs) if step.value_from_secret else resolve_env_placeholder(step.value or "", environment_variables)
        locator.first.fill(value)
    elif step.action == ActionType.SELECT:
        value = resolve_secret(step.value_from_secret, redactor, secret_refs) if step.value_from_secret else resolve_env_placeholder(step.value or "", environment_variables)
        locator.first.select_option(value)
    elif step.action == ActionType.WAIT_FOR:
        locator.first.wait_for(state="visible")
    elif step.action == ActionType.CLEAR:
        locator.first.clear()
    elif step.action == ActionType.CHECK:
        locator.first.check()
    elif step.action == ActionType.UNCHECK:
        locator.first.uncheck()
    elif step.action == ActionType.HOVER:
        locator.first.hover()
    elif step.action == ActionType.SCROLL:
        locator.first.scroll_into_view_if_needed()
        page.mouse.wheel(0, step.scroll_delta_y)
    elif step.action == ActionType.PRESS:
        locator.first.press(step.value or "")
    else:
        raise ValueError(f"未实现的动作：{step.action.value}")
    return {}


def _capture_screenshot(
    page, artifacts: ArtifactManager, name: str, *, stop_loading: bool = False
) -> str | None:
    """通过 CDP 直接采集当前浏览器画面，避开页面字体/资源加载导致的截图超时。"""
    image_path, relative = artifacts.screenshot_path(name)
    session = None
    try:
        session = page.context.new_cdp_session(page)
        if stop_loading:
            try:
                session.send("Page.stopLoading")
            except PlaywrightError:
                pass
        with screenshot_privacy_masks(page, artifacts.screenshot_mask_selectors) as privacy:
            payload = session.send(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": True},
            )
            image_path.write_bytes(base64.b64decode(payload["data"]))
        artifacts.event("screenshot_privacy_applied", screenshot=relative, **privacy)
        return relative if image_path.stat().st_size > 0 else None
    except Exception:
        try:
            with screenshot_privacy_masks(page, artifacts.screenshot_mask_selectors) as privacy:
                page.screenshot(
                    path=str(image_path),
                    full_page=False,
                    animations="disabled",
                    timeout=5_000,
                )
            artifacts.event("screenshot_privacy_applied", screenshot=relative, **privacy)
            return relative if image_path.stat().st_size > 0 else None
        except Exception:
            image_path.unlink(missing_ok=True)
            return None
    finally:
        if session is not None:
            try:
                session.detach()
            except PlaywrightError:
                pass


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
