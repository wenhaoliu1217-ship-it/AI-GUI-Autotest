"""Observation-plan-action loop for dynamic AI exploration."""

from __future__ import annotations

from datetime import datetime
from time import monotonic
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import sync_playwright

from ..artifacts import ArtifactManager
from ..assertions.checks import check_assertion
from ..domain.models import ActionType, ExecutionMode, RelativePosition, StabilityLevel, Step, TestPlan
from ..domain.results import (
    AssertionResult,
    FailureCategory,
    ModelCallRecord,
    RunResult,
    Status,
    StepResult,
)
from ..planning.ai_provider import AIProviderError
from ..security.policy import DomainPolicy, SecurityError, resolve_env_placeholder
from ..security.redaction import Redactor
from .compiler import compile_test
from .findings import build_findings
from .observation import ObservationCollector
from .runner import (
    _capture_screenshot,
    _cause_hint,
    _execute_step,
    _failure_category,
    _goal_outcome,
    _now,
    _step_summary,
)


def run_agent_plan(plan: TestPlan, cfg) -> tuple[RunResult, object]:
    started = _now()
    run_id = cfg.run_id or f"{started:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    redactor = Redactor()
    artifacts = ArtifactManager(cfg.artifacts_root, run_id, redactor)
    steps: list[StepResult] = []
    executed_steps: list[Step] = []
    assertions: list[AssertionResult] = []
    hints = []
    model_records: list[ModelCallRecord] = []
    failed_step: int | None = None
    environment_variables = dict(cfg.environment_variables)
    secret_refs = dict(cfg.secret_refs)
    base_url = resolve_env_placeholder(plan.base_url, environment_variables).rstrip("/")
    policy = DomainPolicy(base_url, list(cfg.allowed_hosts))
    policy.check_url(base_url)
    overall = Status.RUNNING
    completion_reason = "agent_running"
    no_progress_count = 0
    started_monotonic = monotonic()
    max_steps = cfg.max_steps or 50

    def emit(status: Status, *, ended_at: datetime | None = None) -> None:
        if cfg.progress_callback is None:
            return
        current = ended_at or _now()
        costs = [item.estimated_cost for item in model_records]
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
            "reproduction_steps": [_step_summary(item, redactor) for item in executed_steps],
            "cause_hints": [item.model_dump(mode="json") for item in hints],
            "findings": [],
            "replay_mode": cfg.replay_mode,
            "onboarding_level": cfg.onboarding_level,
            "stability_level": _stability(executed_steps),
            "completion_reason": completion_reason,
            "project_id": cfg.project_id,
            "environment_id": cfg.environment_id,
            "environment_updated_at": cfg.environment_updated_at,
            "artifact_retention_days": cfg.artifact_retention_days,
            "scenario_id": cfg.scenario_id,
            "scenario_updated_at": cfg.scenario_updated_at,
            "scenario_goal": cfg.scenario_goal or plan.name,
            "goal_status": "in_progress",
            "goal_summary": f"已执行 {len(executed_steps)} 个探索步骤",
            "model_calls": len(model_records),
            "input_tokens": sum(item.input_tokens for item in model_records),
            "output_tokens": sum(item.output_tokens for item in model_records),
            "estimated_cost": round(sum(item for item in costs if item is not None), 8) if costs and all(item is not None for item in costs) else None,
            "model_call_records": [item.model_dump(mode="json") for item in model_records],
        })

    artifacts.event("run_started", run_id=run_id, plan_name=plan.name, role=plan.role, mode="agent")
    artifacts.write_json("plan.json", plan.model_dump(mode="json", exclude_none=True))
    emit(Status.RUNNING)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=cfg.headless, slow_mo=cfg.slow_mo_ms)
        context = browser.new_context(
            viewport={"width": cfg.viewport[0], "height": cfg.viewport[1]},
            device_scale_factor=cfg.device_scale_factor,
            storage_state=cfg.storage_state,
        )
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
        page = context.new_page()
        collector = ObservationCollector(page, artifacts, redactor, cfg.ignore_rules)
        page.set_default_timeout(cfg.timeout_ms)
        page.set_default_navigation_timeout(cfg.timeout_ms)
        observation = collector.capture(_capture_screenshot(page, artifacts, "agent-observation-0"))
        try:
            while len(executed_steps) < max_steps:
                if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                    overall = Status.CANCELLED
                    completion_reason = "cancelled_by_user"
                    break
                if cfg.max_duration_seconds is not None and monotonic() - started_monotonic >= cfg.max_duration_seconds:
                    overall = Status.INCOMPLETE
                    completion_reason = "time_limit_exceeded"
                    break
                if len(model_records) >= cfg.max_model_calls:
                    overall = Status.INCOMPLETE
                    completion_reason = "max_model_calls_exceeded"
                    break

                try:
                    decision_result = cfg.agent_planner.decide(observation, steps, len(model_records) + 1)
                except AIProviderError as exc:
                    overall = Status.SYSTEM_ERROR
                    completion_reason = "model_error"
                    hints.append(_cause_hint(FailureCategory.MODEL, len(model_records) + 1, redactor.scrub(str(exc))))
                    artifacts.event("model_call_failed", error=redactor.scrub(str(exc)))
                    break

                decision = decision_result.decision
                record = ModelCallRecord(
                    index=len(model_records) + 1,
                    model=decision_result.model,
                    protocol=decision_result.protocol,
                    elapsed_ms=decision_result.elapsed_ms,
                    input_tokens=decision_result.input_tokens,
                    output_tokens=decision_result.output_tokens,
                    estimated_cost=decision_result.estimated_cost,
                    decision=decision.kind,
                    reason=redactor.scrub(decision.reason),
                )
                model_records.append(record)
                artifacts.event(
                    "model_decision",
                    index=record.index,
                    model=record.model,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    estimated_cost=record.estimated_cost,
                    decision=record.decision,
                    reason=record.reason,
                )
                emit(Status.RUNNING)

                if decision.kind == "complete":
                    overall = Status.PASSED
                    completion_reason = "agent_goal_completed"
                    break
                if decision.kind == "blocked":
                    overall = Status.INCOMPLETE
                    completion_reason = "agent_blocked"
                    break

                if decision.kind == "visual":
                    if cfg.visual_adapter is None:
                        overall = Status.INCOMPLETE
                        completion_reason = "visual_adapter_unavailable"
                        break
                    if len(model_records) >= cfg.max_model_calls:
                        overall = Status.INCOMPLETE
                        completion_reason = "max_model_calls_exceeded"
                        break
                    request = decision.visual_request
                    assert request is not None
                    if not observation.screenshot:
                        overall = Status.INCOMPLETE
                        completion_reason = "visual_evidence_missing"
                        break
                    try:
                        visual_result = cfg.visual_adapter.suggest(
                            artifacts.run_dir / observation.screenshot,
                            request.target,
                            observation,
                        )
                    except AIProviderError as exc:
                        overall = Status.INCOMPLETE
                        completion_reason = "visual_target_unconfirmed"
                        artifacts.event(
                            "visual_fallback_failed",
                            trigger_reason=redactor.scrub(request.trigger_reason),
                            screenshot=observation.screenshot,
                            error=redactor.scrub(str(exc)),
                        )
                        break
                    suggestion = visual_result.suggestion
                    model_records.append(ModelCallRecord(
                        index=len(model_records) + 1,
                        model=visual_result.model,
                        protocol=visual_result.protocol,
                        elapsed_ms=visual_result.elapsed_ms,
                        input_tokens=visual_result.input_tokens,
                        output_tokens=visual_result.output_tokens,
                        estimated_cost=visual_result.estimated_cost,
                        decision="visual_suggestion",
                        reason=redactor.scrub(suggestion.rationale),
                    ))
                    step = Step(
                        action=ActionType.VISUAL_CLICK,
                        locator=request.canvas_locator,
                        description=f"视觉定位并点击：{request.target}",
                        execution_mode=ExecutionMode.VISUAL,
                        stability_level=StabilityLevel.C,
                        stability_reason="运行时视觉模型重新定位语义目标",
                        visual_target=request.target,
                        relative_position=RelativePosition(xRatio=suggestion.x_ratio, yRatio=suggestion.y_ratio),
                        computer_use_triggered=True,
                        computer_use_reason=request.trigger_reason,
                    )
                    artifacts.event(
                        "visual_fallback_suggested",
                        trigger_reason=redactor.scrub(request.trigger_reason),
                        screenshot=observation.screenshot,
                        model=visual_result.model,
                        target=redactor.scrub(suggestion.target),
                        x_ratio=suggestion.x_ratio,
                        y_ratio=suggestion.y_ratio,
                        confidence=suggestion.confidence,
                    )
                    emit(Status.RUNNING)
                else:
                    step = decision.action
                    assert step is not None
                index = len(executed_steps) + 1
                step_started = _now()
                before = None
                try:
                    _check_agent_step(step, cfg.forbidden_actions, visual_authorized=decision.kind == "visual")
                    summary = _step_summary(step, redactor)
                    artifacts.event("step_started", index=index, action=step.action.value, target=summary)
                    before = collector.capture(_capture_screenshot(page, artifacts, f"step-{index}-before"))
                    detail = _execute_step(
                        page, step, base_url, policy, redactor,
                        environment_variables=environment_variables, secret_refs=secret_refs,
                    )
                    after = collector.capture(_capture_screenshot(page, artifacts, f"step-{index}-after"))
                    made_progress = _made_progress(step, before, after)
                    if step.action == ActionType.VISUAL_CLICK and not made_progress:
                        raise SecurityError("视觉动作后页面或应用语义状态未发生可验证变化")
                    assessment = "progress" if made_progress else "no_progress"
                    result = StepResult(
                        index=index,
                        action=step.action.value,
                        description=step.description,
                        target_summary=summary,
                        status=Status.PASSED,
                        started_at=step_started,
                        ended_at=_now(),
                        screenshot=after.screenshot,
                        locator_basis=step.locator.describe() if step.locator else None,
                        execution_mode=step.execution_mode.value,
                        stability_level=step.stability_level.value,
                        stability_reason=step.stability_reason,
                        computer_use_triggered=step.computer_use_triggered,
                        computer_use_reason=step.computer_use_reason,
                        coordinate_source=detail.get("coordinateSource"),
                        app_bridge_result=detail.get("appBridgeResult"),
                        before=before,
                        after=after,
                        planner_reason=redactor.scrub(decision.reason),
                        progress_assessment=assessment,
                    )
                    steps.append(result)
                    executed_steps.append(step)
                    observation = after
                    no_progress_count = 0 if made_progress else no_progress_count + 1
                    artifacts.event("step_passed", index=index, progress=assessment, no_progress_count=no_progress_count)
                    emit(Status.RUNNING)
                    if no_progress_count >= cfg.no_progress_limit:
                        overall = Status.INCOMPLETE
                        completion_reason = "no_progress_limit_reached"
                        artifacts.event("run_limit_reached", limit="no_progress", count=no_progress_count)
                        break
                except Exception as exc:
                    category = _failure_category(exc)
                    after = collector.capture(_capture_screenshot(page, artifacts, f"step-{index}-after-failure", stop_loading=True))
                    message = redactor.scrub(str(exc))
                    steps.append(StepResult(
                        index=index,
                        action=step.action.value,
                        description=step.description,
                        target_summary=_step_summary(step, redactor),
                        status=Status.ERROR,
                        started_at=step_started,
                        ended_at=_now(),
                        error_message=message,
                        failure_category=category,
                        screenshot=after.screenshot,
                        execution_mode=step.execution_mode.value,
                        stability_level=step.stability_level.value,
                        stability_reason=step.stability_reason,
                        before=before,
                        after=after,
                        planner_reason=redactor.scrub(decision.reason),
                        progress_assessment="no_progress",
                    ))
                    executed_steps.append(step)
                    failed_step = index
                    overall = Status.ERROR
                    completion_reason = "execution_failed"
                    hints.append(_cause_hint(category, index, message))
                    emit(Status.RUNNING)
                    break
            else:
                overall = Status.INCOMPLETE
                completion_reason = "max_steps_exceeded"

            if overall == Status.PASSED:
                for index, assertion in enumerate(plan.assertions, start=1):
                    try:
                        outcome = check_assertion(page, assertion)
                        status = Status.PASSED if outcome.passed else Status.FAILED
                        screenshot = None
                        if not outcome.passed:
                            screenshot = _capture_screenshot(page, artifacts, f"assertion-{index}-failure")
                            overall = Status.ISSUES_FOUND
                            completion_reason = "assertion_failed"
                        assertions.append(AssertionResult(
                            index=index,
                            type=assertion.type.value,
                            description=assertion.description,
                            detail=assertion.locator.describe() if assertion.locator else assertion.type.value,
                            status=status,
                            expected_summary=str(assertion.expected if assertion.expected is not None else assertion.count),
                            actual_summary=redactor.scrub(outcome.actual),
                            screenshot=screenshot,
                        ))
                    except Exception as exc:
                        message = redactor.scrub(str(exc))
                        category = _failure_category(exc)
                        screenshot = _capture_screenshot(page, artifacts, f"assertion-{index}-error", stop_loading=True)
                        assertions.append(AssertionResult(
                            index=index,
                            type=assertion.type.value,
                            description=assertion.description,
                            detail=assertion.type.value,
                            status=Status.ERROR,
                            error_message=message,
                            screenshot=screenshot,
                        ))
                        overall = Status.SYSTEM_ERROR
                        completion_reason = "assertion_error"
                        hints.append(_cause_hint(category, index, message))
        finally:
            try:
                context.tracing.stop(path=str(artifacts.trace_path))
                artifacts.redact_trace()
            finally:
                context.close()
                browser.close()

    ended = _now()
    reproduction = [_step_summary(step, redactor) for step in executed_steps]
    findings = build_findings(steps, assertions, reproduction)
    generated_test = None
    stability = _stability(executed_steps)
    if executed_steps:
        executed_plan = plan.model_copy(update={"steps": executed_steps})
        source, generated_test = compile_test(executed_plan)
        generated_test.source_path = artifacts.write_text(generated_test.source_path, source)
    costs = [item.estimated_cost for item in model_records]
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
        replay_mode="exploration",
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
        model_calls=len(model_records),
        input_tokens=sum(item.input_tokens for item in model_records),
        output_tokens=sum(item.output_tokens for item in model_records),
        estimated_cost=round(sum(item for item in costs if item is not None), 8) if costs and all(item is not None for item in costs) else None,
        model_call_records=model_records,
    )
    artifacts.event("run_finished", status=overall.value, completion_reason=completion_reason, duration_ms=result.duration_ms)
    artifacts.finalize(result)
    emit(overall, ended_at=ended)
    return result, artifacts.run_dir


def _check_agent_step(step: Step, forbidden_actions: tuple[str, ...], *, visual_authorized: bool = False) -> None:
    if step.action == ActionType.VISUAL_CLICK and not visual_authorized:
        raise SecurityError("当前 Agent 未配置真实视觉适配器，拒绝执行视觉坐标动作")
    text = " ".join(filter(None, [
        step.description or "",
        step.target or "",
        step.locator.describe() if step.locator else "",
    ])).lower()
    defaults = ("支付", "付款", "删除", "提交订单", "发送邀请", "发布内容", "purchase", "delete", "pay")
    blocked = tuple(item.strip().lower() for item in (*defaults, *forbidden_actions) if item.strip())
    matched = next((item for item in blocked if item in text), None)
    if matched:
        raise SecurityError(f"Agent 动作命中禁止策略：{matched}")
    if step.action == ActionType.NAVIGATE and step.target and urlparse(step.target).scheme not in {"", "http", "https"}:
        raise SecurityError("Agent 只能导航到 http/https 地址或相对路径")


def _made_progress(step: Step, before, after) -> bool:
    if step.action in {ActionType.FILL, ActionType.SELECT, ActionType.CLEAR, ActionType.CHECK, ActionType.UNCHECK, ActionType.PRESS}:
        return True
    before_facts = (before.url, before.title, tuple(before.dom_summary), before.accessibility_summary)
    after_facts = (after.url, after.title, tuple(after.dom_summary), after.accessibility_summary)
    return before_facts != after_facts


def _stability(steps: list[Step]) -> str:
    ranks = {"A": 0, "B": 1, "C": 2, "D": 3}
    return max((step.stability_level.value for step in steps), key=lambda item: ranks[item], default="A")
