"""Observation-plan-action loop for dynamic AI exploration."""

from __future__ import annotations

from datetime import datetime
from time import monotonic, sleep
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import sync_playwright

from ..artifacts import ArtifactManager
from ..assertions.checks import check_assertion
from ..commerce import ResourceLedgerEntry
from ..commerce import evaluate_release_gate
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
from ..security.policy import DomainPolicy, SecurityError, guard_playwright_route, guard_playwright_websocket, resolve_env_placeholder
from ..security.redaction import Redactor
from .compiler import compile_test
from .confirmation import confirmation_match
from .findings import build_findings
from .bridge_adapter import create_bridge_adapter
from .stability import finalize_canvas_evidence, prepare_action
from .observation import ObservationCollector
from .browser_context import resolve_browser_surface
from .runner import (
    _capture_screenshot,
    _commerce_preflight,
    _commerce_record_success,
    _commerce_recovery_probe,
    _commerce_run_summary,
    _commerce_state_after_action,
    _cause_hint,
    _execute_step,
    _failure_category,
    _goal_outcome,
    _now,
    _require_commerce_metadata,
    _restore_page_session,
    _step_summary,
)
from .recovery import SideEffectOutcomeUnknown, execute_with_recovery


def run_agent_plan(plan: TestPlan, cfg) -> tuple[RunResult, object]:
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
    executed_steps: list[Step] = []
    assertions: list[AssertionResult] = []
    hints = []
    model_records: list[ModelCallRecord] = []
    failed_step: int | None = None
    environment_variables = dict(cfg.environment_variables)
    secret_refs = dict(cfg.secret_refs)
    redactor.register_environment_refs(secret_refs)
    base_url = resolve_env_placeholder(plan.base_url, environment_variables).rstrip("/")
    policy = DomainPolicy(
        base_url,
        list(cfg.allowed_hosts),
        allow_private_network=cfg.allow_private_network,
    )
    policy.check_url(base_url)
    overall = Status.RUNNING
    completion_reason = "agent_running"
    no_progress_count = 0
    started_monotonic = monotonic()
    max_steps = cfg.max_steps or 50
    commerce_ledger: dict[str, ResourceLedgerEntry] = {}
    commerce_decisions: list[dict] = []

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
            "confirmation_history": list(cfg.confirmation_history),
        })

    artifacts.event("run_started", run_id=run_id, plan_name=plan.name, role=plan.role, mode="agent")
    artifacts.event(
        "app_bridge_configuration",
        enabled=bridge_adapter is not None,
        adapter=cfg.app_bridge_adapter,
        global_name=cfg.app_bridge_global_name,
        fallback_mode=(
            "visual_or_locator" if bridge_adapter is None and cfg.visual_adapter is not None
            else "locator_only" if bridge_adapter is None
            else "bridge_v1"
        ),
    )
    artifacts.write_json("plan.json", plan.model_dump(mode="json", exclude_none=True))
    emit(Status.RUNNING)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=cfg.headless, slow_mo=cfg.slow_mo_ms)
        context = browser.new_context(
            viewport={"width": cfg.viewport[0], "height": cfg.viewport[1]},
            device_scale_factor=cfg.device_scale_factor,
            storage_state=cfg.storage_state,
            accept_downloads=True,
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
                            requested_action=request.preferred_action,
                            expected_change=request.expected_change,
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
                    visual_actions = {
                        "click": ActionType.VISUAL_CLICK,
                        "hover": ActionType.VISUAL_HOVER,
                        "scroll": ActionType.VISUAL_SCROLL,
                        "drag": ActionType.VISUAL_DRAG,
                    }
                    step = Step(
                        action=visual_actions[suggestion.action],
                        locator=request.canvas_locator,
                        description=f"视觉定位并执行 {suggestion.action}：{request.target}",
                        execution_mode=ExecutionMode.VISUAL,
                        stability_level=StabilityLevel.C,
                        stability_reason="运行时视觉模型重新定位语义目标",
                        visual_target=request.target,
                        relative_position=RelativePosition(xRatio=suggestion.x_ratio, yRatio=suggestion.y_ratio),
                        relative_end_position=(RelativePosition(xRatio=suggestion.end_x_ratio, yRatio=suggestion.end_y_ratio)
                                               if suggestion.end_x_ratio is not None and suggestion.end_y_ratio is not None else None),
                        visual_expected_change=suggestion.expected_change,
                        scroll_delta_y=suggestion.scroll_delta_y,
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
                        action=suggestion.action,
                        expected_change=redactor.scrub(suggestion.expected_change),
                    )
                    emit(Status.RUNNING)
                else:
                    step = decision.action
                    assert step is not None
                index = len(executed_steps) + 1
                page, locator_root, browser_context_evidence = resolve_browser_surface(
                    context, page, step.browser_target, policy,
                    enforce_url_condition=step.action != ActionType.HUMAN_TAKEOVER,
                )
                collector = ObservationCollector(page, artifacts, redactor, cfg.ignore_rules)
                artifacts.event("browser_context_selected", index=index, **browser_context_evidence)
                step_started = _now()
                before = None
                stability_evidence = None
                canvas_evidence = None
                commerce_state_evidence = None
                recovery_evidence = None
                try:
                    _check_agent_step(
                        step, cfg.forbidden_actions,
                        visual_authorized=decision.kind == "visual",
                        bridge_authorized=bridge_adapter is not None,
                    )
                    summary = _step_summary(step, redactor)
                    artifacts.event("step_started", index=index, action=step.action.value, target=summary)
                    before = collector.capture(_capture_screenshot(page, artifacts, f"step-{index}-before"))
                    _require_commerce_metadata(step, cfg)
                    confirmation_term = confirmation_match(step)
                    confirmed_by_human = False
                    if confirmation_term:
                        if step.action == ActionType.HUMAN_TAKEOVER and cfg.headless:
                            raise SecurityError("人工接管需要可见浏览器，不能在 headless 模式执行")
                        if cfg.confirmation_callback is None:
                            raise SecurityError(f"危险动作需要人工确认：{confirmation_term}")
                        artifacts.event("dangerous_action_confirmation_requested", index=index, rule=confirmation_term, target=summary)
                        if not cfg.confirmation_callback(step, index, confirmation_term):
                            was_cancelled = cfg.cancel_event is not None and cfg.cancel_event.is_set()
                            steps.append(StepResult(
                                index=index,
                                action=step.action.value,
                                description=step.description,
                                target_summary=summary,
                                status=Status.SKIPPED,
                                started_at=step_started,
                                ended_at=_now(),
                                error_message="运行已由用户取消，动作未执行" if was_cancelled else "危险动作未获批准，动作未执行",
                                failure_category=FailureCategory.SECURITY,
                                screenshot=before.screenshot,
                                execution_mode=step.execution_mode.value,
                                stability_level=step.stability_level.value,
                                stability_reason=step.stability_reason,
                                before=before,
                                planner_reason=redactor.scrub(decision.reason),
                                progress_assessment="no_progress",
                            ))
                            overall = Status.CANCELLED
                            completion_reason = "cancelled_by_user" if was_cancelled else "dangerous_action_rejected"
                            artifacts.event("dangerous_action_rejected", index=index, rule=confirmation_term)
                            emit(Status.CANCELLED)
                            break
                        artifacts.event("dangerous_action_approved", index=index, rule=confirmation_term)
                        confirmed_by_human = True
                    _commerce_preflight(
                        step, cfg, run_id, confirmed_by_human, commerce_ledger, artifacts, index,
                        commerce_decisions,
                    )
                    prepared = prepare_action(
                        page, step,
                        bridge_adapter=bridge_adapter,
                        timeout_ms=min(cfg.timeout_ms, cfg.action_stability_timeout_ms),
                        locator_root=locator_root,
                    )
                    artifacts.event("action_stability_checked", index=index, **prepared.evidence)
                    stability_evidence = prepared.evidence
                    execution_page = [page]
                    execution_root = [locator_root]
                    recovery_url = page.url

                    def recover_session() -> None:
                        recovered_page, recovered_root = _restore_page_session(
                            context, recovery_url, step, policy, cfg
                        )
                        execution_page[0] = recovered_page
                        execution_root[0] = recovered_root

                    detail, recovery_evidence = execute_with_recovery(
                        step,
                        lambda: _execute_step(
                            execution_page[0], step, base_url, policy, redactor,
                            environment_variables=environment_variables, secret_refs=secret_refs,
                            bridge_adapter=bridge_adapter, bridge_prepared=prepared.bridge_action,
                            locator_root=execution_root[0], file_assets=dict(cfg.file_assets), artifacts=artifacts,
                        ),
                        wait=lambda milliseconds: sleep(milliseconds / 1000),
                        probe=_commerce_recovery_probe(
                            context, step, cfg, run_id, base_url, policy
                        ),
                        recover_session=recover_session,
                    )
                    page = execution_page[0]
                    locator_root = execution_root[0]
                    collector = ObservationCollector(page, artifacts, redactor, cfg.ignore_rules)
                    artifacts.event("execution_recovery_evaluated", index=index, **recovery_evidence)
                    _commerce_record_success(step, run_id, commerce_ledger, artifacts)
                    commerce_state_evidence = _commerce_state_after_action(
                        context, step, cfg, run_id, base_url, policy, artifacts, index
                    )
                    canvas_evidence = finalize_canvas_evidence(
                        page, step,
                        prepared=prepared,
                        bridge_adapter=bridge_adapter,
                        execution_detail=detail,
                        before_screenshot=before.screenshot,
                        after_screenshot=None,
                    )
                    after = collector.capture(_capture_screenshot(page, artifacts, f"step-{index}-after"))
                    if canvas_evidence is not None:
                        canvas_evidence["afterScreenshot"] = after.screenshot
                    made_progress = _made_progress(step, before, after, artifacts.run_dir)
                    if step.execution_mode == ExecutionMode.VISUAL:
                        artifacts.event(
                            "visual_action_verified",
                            index=index,
                            expected_change=redactor.scrub(step.visual_expected_change or "可见状态变化"),
                            verified=made_progress,
                            before_screenshot=before.screenshot,
                            after_screenshot=after.screenshot,
                        )
                    if step.execution_mode == ExecutionMode.VISUAL and not made_progress:
                        raise SecurityError("视觉动作后页面或应用语义状态未发生可验证变化")
                    if canvas_evidence is not None:
                        canvas_evidence["observationProgressVerified"] = made_progress
                        artifacts.event("canvas_evidence_collected", index=index, **canvas_evidence)
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
                        stability_evidence=prepared.evidence,
                        canvas_evidence=canvas_evidence,
                        browser_context_evidence={**browser_context_evidence, **detail.get("browserContext", {})},
                        commerce_state_evidence=commerce_state_evidence,
                        file_evidence=detail.get("fileEvidence"),
                        recovery_evidence=recovery_evidence,
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
                    recovery_evidence = getattr(exc, "evidence", recovery_evidence)
                    if recovery_evidence:
                        artifacts.event("execution_recovery_evaluated", index=index, **recovery_evidence)
                    category = _failure_category(exc)
                    after = collector.capture(_capture_screenshot(page, artifacts, f"step-{index}-after-failure", stop_loading=True))
                    message = redactor.scrub(str(exc))
                    if stability_evidence is None:
                        stability_evidence = {"checked": True, "passed": False, "error": message}
                        artifacts.event("action_stability_failed", index=index, error=message)
                    if step.execution_mode in {ExecutionMode.VISUAL, ExecutionMode.APP_BRIDGE}:
                        canvas_evidence = {
                            **(canvas_evidence or {}),
                            "mode": step.execution_mode.value,
                            "action": step.action.value,
                            "semanticTarget": step.visual_target or step.bridge_target_id,
                            "beforeScreenshot": before.screenshot if before else None,
                            "afterScreenshot": after.screenshot,
                            "traceArtifact": "trace.zip",
                            "collectionStatus": "failed",
                            "failurePhase": "stability" if not stability_evidence.get("passed") else "execution_or_after_state",
                            "error": message,
                        }
                        artifacts.event("canvas_evidence_failed", index=index, **canvas_evidence)
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
                        stability_evidence=stability_evidence,
                        canvas_evidence=canvas_evidence,
                        browser_context_evidence=browser_context_evidence,
                        commerce_state_evidence=commerce_state_evidence,
                        recovery_evidence=recovery_evidence,
                        before=before,
                        after=after,
                        planner_reason=redactor.scrub(decision.reason),
                        progress_assessment="no_progress",
                    ))
                    executed_steps.append(step)
                    failed_step = index
                    overall = Status.ERROR
                    completion_reason = (
                        "manual_reconciliation_required"
                        if isinstance(exc, SideEffectOutcomeUnknown)
                        else "execution_failed"
                    )
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

    commerce_summary = _commerce_run_summary(cfg, commerce_decisions, commerce_ledger)
    if commerce_summary and not commerce_summary["zeroResidual"]:
        overall = Status.ERROR
        completion_reason = "commerce_cleanup_required"
        artifacts.event(
            "commerce_cleanup_required",
            pending_count=len(commerce_summary["pendingResources"]),
            pending_resources=commerce_summary["pendingResources"],
        )
        hints.append(_cause_hint(
            FailureCategory.SECURITY,
            failed_step or len(steps),
            "电商运行结束时仍有未清理的 E2E 资源，必须人工处置并复核台账",
        ))
    if commerce_summary:
        release_gate = evaluate_release_gate(
            steps,
            pending_resources=commerce_summary["pendingResources"],
            ledger_entries=commerce_summary["ledgerEntries"],
            planned_step_count=len(plan.steps),
            additional_payload={
                "assertions": [item.model_dump(mode="json") for item in assertions],
                "reproduction": [_step_summary(item, redactor) for item in executed_steps],
            },
        )
        commerce_summary["releaseGate"] = release_gate
        artifacts.write_json("commerce-release-gate.json", release_gate)
        artifacts.event("commerce_release_gate_evaluated", **release_gate)
        if not release_gate["passed"] and overall == Status.PASSED:
            overall = Status.ERROR
            completion_reason = "commerce_release_gate_failed"
            hints.append(_cause_hint(
                FailureCategory.SECURITY, failed_step or len(steps),
                "电商发布门禁未通过：证据、隐私、零残留或重复副作用指标不满足要求",
            ))
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
        confirmation_history=list(cfg.confirmation_history),
        commerce_summary=commerce_summary,
    )
    artifacts.event("run_finished", status=overall.value, completion_reason=completion_reason, duration_ms=result.duration_ms)
    artifacts.finalize(result)
    emit(overall, ended_at=ended)
    return result, artifacts.run_dir


def _check_agent_step(
    step: Step,
    forbidden_actions: tuple[str, ...],
    *,
    visual_authorized: bool = False,
    bridge_authorized: bool = False,
) -> None:
    if step.execution_mode == ExecutionMode.VISUAL and not visual_authorized:
        raise SecurityError("当前 Agent 未配置真实视觉适配器，拒绝执行视觉坐标动作")
    if step.execution_mode == ExecutionMode.APP_BRIDGE and not bridge_authorized:
        raise SecurityError("当前环境未启用 App Bridge，拒绝执行 Bridge 动作")
    text = " ".join(filter(None, [
        step.description or "",
        step.target or "",
        step.locator.describe() if step.locator else "",
    ])).lower()
    blocked = tuple(item.strip().lower() for item in forbidden_actions if item.strip())
    matched = next((item for item in blocked if item in text), None)
    if matched:
        raise SecurityError(f"Agent 动作命中禁止策略：{matched}")
    if step.action == ActionType.NAVIGATE and step.target and urlparse(step.target).scheme not in {"", "http", "https"}:
        raise SecurityError("Agent 只能导航到 http/https 地址或相对路径")


def _made_progress(step: Step, before, after, run_dir=None) -> bool:
    if step.action in {ActionType.FILL, ActionType.SELECT, ActionType.CLEAR, ActionType.CHECK, ActionType.UNCHECK, ActionType.PRESS}:
        return True
    before_facts = (before.url, before.title, tuple(before.dom_summary), before.accessibility_summary)
    after_facts = (after.url, after.title, tuple(after.dom_summary), after.accessibility_summary)
    if before_facts != after_facts:
        return True
    if step.execution_mode == ExecutionMode.VISUAL and run_dir and before.screenshot and after.screenshot:
        before_path = run_dir / before.screenshot
        after_path = run_dir / after.screenshot
        if before_path.is_file() and after_path.is_file():
            return before_path.read_bytes() != after_path.read_bytes()
    return False


def _stability(steps: list[Step]) -> str:
    ranks = {"A": 0, "B": 1, "C": 2, "D": 3}
    return max((step.stability_level.value for step in steps), key=lambda item: ranks[item], default="A")
