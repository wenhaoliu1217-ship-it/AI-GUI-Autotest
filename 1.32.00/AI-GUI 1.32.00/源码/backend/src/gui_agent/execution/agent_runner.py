"""Observation-plan-action loop for dynamic AI exploration."""

from __future__ import annotations

from datetime import datetime
from time import monotonic, sleep
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
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
from .confirmation import confirmation_match, request_confirmation
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
    _run_business_cleanup,
    _restore_page_session,
    _step_summary,
)
from .recovery import SideEffectOutcomeUnknown, execute_with_recovery
from .async_state import WebSocketEvidenceCollector
from .side_effects import confirmation_rule, evaluate_side_effect


_WRITE_ACTIONS_REQUIRING_APPROVAL = {
    ActionType.FILL, ActionType.CLEAR, ActionType.SELECT, ActionType.CHECK, ActionType.UNCHECK,
    ActionType.PRESS, ActionType.UPLOAD, ActionType.UPLOAD_FILE, ActionType.COMPONENT,
    ActionType.BRIDGE_CLICK, ActionType.VISUAL_DRAW_POLYGON, ActionType.VISUAL_DRAW_RECTANGLE,
    ActionType.HUMAN_TAKEOVER,
}

_SESSION_END_COMMANDS = {
    "结束本次测试", "停止整个测试", "结束测试", "停止测试", "不再继续", "完成并结束",
}

_MODEL_RECOVERY_DELAYS = (2.0, 5.0)


class _ModelRecoveryStopped(RuntimeError):
    """The user ended or cancelled a recoverable model outage wait."""


def _exclude_wait_from_timers(
    started_at: float,
    goal_started_at: float,
    waiting_started_at: float,
    now: float,
) -> tuple[float, float]:
    waited = max(0.0, now - waiting_started_at)
    return started_at + waited, goal_started_at + waited


def _decide_with_model_recovery(
    decide,
    *,
    on_event=None,
    wait_for_retry=None,
    sleep_fn=sleep,
):
    """Retry transient provider outages without discarding the active browser session."""
    failure_count = 0
    while True:
        try:
            return decide()
        except AIProviderError as exc:
            if not exc.retryable:
                raise
            failure_count += 1
            if on_event is not None:
                on_event("model_call_transient_failure", failure_count=failure_count, error=str(exc))
            if failure_count <= len(_MODEL_RECOVERY_DELAYS):
                delay = _MODEL_RECOVERY_DELAYS[failure_count - 1]
                if on_event is not None:
                    on_event("model_call_retry_scheduled", failure_count=failure_count, delay_seconds=delay)
                sleep_fn(delay)
                continue
            if wait_for_retry is None or not wait_for_retry(exc):
                raise _ModelRecoveryStopped(str(exc)) from exc
            failure_count = 0


def _approval_rule(step: Step, configured_mode: str, safety_rule: str | None) -> str | None:
    """Add beginner approval semantics without weakening existing absolute safety gates."""
    if safety_rule:
        return safety_rule
    if configured_mode == "ask" and step.effect_level is not None and step.effect_level.value not in {
        "read_only", "session_only", "isolated_local_write",
    }:
        return f"approval-mode:site-write:{step.effect_kind or step.effect_level.value}"
    if (
        configured_mode == "ask"
        and step.action in _WRITE_ACTIONS_REQUIRING_APPROVAL
        and (step.effect_level is None or step.action == ActionType.HUMAN_TAKEOVER)
    ):
        return "approval-mode:write-action"
    return None


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
    current_goal = cfg.scenario_goal or plan.name
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
    goal_started_monotonic = started_monotonic
    goal_model_call_start = 0
    goal_step_start = 0
    clarification_round = 0
    max_steps = cfg.max_steps or 50
    commerce_ledger: dict[str, ResourceLedgerEntry] = {}
    commerce_decisions: list[dict] = []
    cleanup_report: dict | None = None

    def emit(
        status: Status, *, ended_at: datetime | None = None,
        active_step: dict | None = None,
    ) -> None:
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
            "scenario_goal": current_goal,
            "goal_status": "in_progress",
            "goal_summary": f"已执行 {len(executed_steps)} 个探索步骤",
            "model_calls": len(model_records),
            "input_tokens": sum(item.input_tokens for item in model_records),
            "output_tokens": sum(item.output_tokens for item in model_records),
            "estimated_cost": round(sum(item for item in costs if item is not None), 8) if costs and all(item is not None for item in costs) else None,
            "model_call_records": [item.model_dump(mode="json") for item in model_records],
            "confirmation_history": list(cfg.confirmation_history),
            "clarification_history": list(cfg.clarification_history),
            "result_classification": "agent_running",
            "model_data_authorization": cfg.model_data_authorization,
            "active_step": active_step,
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
        if cfg.headless:
            browser = playwright.chromium.launch(headless=True, slow_mo=cfg.slow_mo_ms)
        else:
            try:
                browser = playwright.chromium.launch(
                    channel="msedge", headless=False, slow_mo=cfg.slow_mo_ms,
                )
            except PlaywrightError:
                browser = playwright.chromium.launch(headless=False, slow_mo=cfg.slow_mo_ms)
        context = browser.new_context(
            viewport={"width": cfg.viewport[0], "height": cfg.viewport[1]},
            device_scale_factor=cfg.device_scale_factor,
            storage_state=cfg.storage_state,
            accept_downloads=True,
            service_workers="block",
        )
        def guarded_route_handler(route) -> None:
            guard_playwright_route(
                route,
                policy,
                lambda message, url, resource_type: artifacts.event(
                    "network_request_blocked",
                    reason=redactor.scrub(message),
                    url=redactor.scrub(url),
                    resource_type=resource_type,
                ),
            )

        context.route("**/*", guarded_route_handler)
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
        websocket_collector = WebSocketEvidenceCollector(redactor)
        websocket_collector.attach(page)
        collector = ObservationCollector(page, artifacts, redactor, cfg.ignore_rules)
        page.set_default_timeout(cfg.timeout_ms)
        page.set_default_navigation_timeout(cfg.timeout_ms)
        try:
            page.goto(
                base_url,
                wait_until="domcontentloaded",
                timeout=min(cfg.timeout_ms, 30_000),
            )
            artifacts.event("agent_bootstrap_navigation", url=redactor.scrub(page.url), succeeded=True)
        except PlaywrightError as exc:
            artifacts.event(
                "agent_bootstrap_navigation",
                url=redactor.scrub(base_url),
                succeeded=False,
                error=redactor.scrub(str(exc)),
            )
        observation = collector.capture(_capture_screenshot(page, artifacts, "agent-observation-0"))
        try:
            while len(executed_steps) - goal_step_start < max_steps:
                if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                    overall = Status.CANCELLED
                    completion_reason = "cancelled_by_user"
                    break
                if cfg.max_duration_seconds is not None and monotonic() - goal_started_monotonic >= cfg.max_duration_seconds:
                    overall = Status.INCOMPLETE
                    completion_reason = "time_limit_exceeded"
                    break
                if len(model_records) - goal_model_call_start >= cfg.max_model_calls:
                    overall = Status.INCOMPLETE
                    completion_reason = "max_model_calls_exceeded"
                    break

                model_recovery_state = {"action": None}

                def wait_for_model_retry(exc: AIProviderError) -> bool:
                    nonlocal started_monotonic, goal_started_monotonic, completion_reason
                    if not cfg.continuous_agent_session or cfg.clarification_callback is None:
                        return False
                    question = (
                        "AI 服务暂时无法连接，当前网页、登录状态和已完成结果都已保留。"
                        "请发送“重试”继续当前任务，或发送“结束本次测试”。"
                    )
                    completion_reason = "agent_waiting_for_model_recovery"
                    artifacts.event("agent_waiting_for_model_recovery", error=redactor.scrub(str(exc)))
                    emit(Status.RUNNING)
                    waiting_started = monotonic()
                    answer = cfg.clarification_callback(question, 0)
                    started_monotonic, goal_started_monotonic = _exclude_wait_from_timers(
                        started_monotonic, goal_started_monotonic, waiting_started, monotonic()
                    )
                    if answer is None or not answer.strip():
                        model_recovery_state["action"] = "cancelled"
                        return False
                    normalized_answer = answer.strip()
                    if normalized_answer in _SESSION_END_COMMANDS:
                        model_recovery_state["action"] = "ended"
                        return False
                    entry = {
                        "kind": "model_recovery",
                        "round": 0,
                        "question": redactor.scrub(question),
                        "answer": redactor.scrub(normalized_answer),
                        "goal": redactor.scrub(current_goal),
                    }
                    cfg.clarification_history.append(entry)
                    scenario = getattr(cfg.agent_planner, "scenario", None)
                    if scenario is not None:
                        scenario.clarification_history = list(cfg.clarification_history)
                    artifacts.event("model_recovery_requested", **entry)
                    completion_reason = "agent_running"
                    emit(Status.RUNNING)
                    return True

                try:
                    decision_result = _decide_with_model_recovery(
                        lambda: cfg.agent_planner.decide(
                            observation,
                            steps,
                            len(model_records) - goal_model_call_start + 1,
                        ),
                        on_event=lambda name, **payload: artifacts.event(
                            name,
                            **{key: redactor.scrub(value) if isinstance(value, str) else value for key, value in payload.items()},
                        ),
                        wait_for_retry=wait_for_model_retry,
                    )
                except _ModelRecoveryStopped as exc:
                    action = model_recovery_state["action"]
                    if action == "ended":
                        overall = Status.PASSED
                        completion_reason = "agent_session_completed"
                        artifacts.event("agent_session_completed", final_goal=redactor.scrub(current_goal))
                    elif action == "cancelled":
                        overall = Status.CANCELLED
                        completion_reason = "cancelled_by_user"
                    else:
                        overall = Status.SYSTEM_ERROR
                        completion_reason = "model_error"
                        hints.append(_cause_hint(FailureCategory.MODEL, len(model_records) + 1, redactor.scrub(str(exc))))
                        artifacts.event("model_call_failed", error=redactor.scrub(str(exc)))
                    break
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
                    if cfg.continuous_agent_session and cfg.clarification_callback is not None:
                        completed_goal = current_goal
                        question = "当前任务已完成，可以继续告诉 AI 下一项要测试什么，或选择结束本次测试。"
                        completion_reason = "agent_goal_completed_waiting_follow_up"
                        artifacts.event("agent_goal_completed_waiting_follow_up", goal=redactor.scrub(completed_goal))
                        emit(Status.RUNNING)
                        waiting_started = monotonic()
                        answer = cfg.clarification_callback(question, 0)
                        started_monotonic, goal_started_monotonic = _exclude_wait_from_timers(
                            started_monotonic, goal_started_monotonic, waiting_started, monotonic()
                        )
                        if answer is None or not answer.strip():
                            overall = Status.CANCELLED
                            completion_reason = "cancelled_by_user"
                            break
                        normalized_answer = answer.strip()
                        if normalized_answer in _SESSION_END_COMMANDS:
                            overall = Status.PASSED
                            completion_reason = "agent_session_completed"
                            artifacts.event("agent_session_completed", final_goal=redactor.scrub(completed_goal))
                            break
                        entry = {
                            "kind": "follow_up",
                            "round": 0,
                            "question": redactor.scrub(question),
                            "answer": redactor.scrub(normalized_answer),
                            "completed_goal": redactor.scrub(completed_goal),
                        }
                        cfg.clarification_history.append(entry)
                        current_goal = normalized_answer
                        scenario = getattr(cfg.agent_planner, "scenario", None)
                        if scenario is not None:
                            scenario.goal = normalized_answer
                            scenario.clarification_history = list(cfg.clarification_history)
                        artifacts.event("agent_follow_up_received", **entry)
                        goal_model_call_start = len(model_records)
                        goal_step_start = len(executed_steps)
                        goal_started_monotonic = monotonic()
                        clarification_round = 0
                        no_progress_count = 0
                        completion_reason = "agent_running"
                        emit(Status.RUNNING)
                        continue
                    overall = Status.PASSED
                    completion_reason = "agent_goal_completed"
                    break
                if decision.kind == "clarification":
                    question = decision.question or decision.reason
                    clarification_round += 1
                    round_number = clarification_round
                    if round_number > 3:
                        overall = Status.INCOMPLETE
                        completion_reason = "clarification_round_limit_exceeded"
                        artifacts.event("clarification_limit_exceeded", maximum_rounds=3)
                        break
                    if cfg.clarification_callback is None:
                        overall = Status.INCOMPLETE
                        completion_reason = "clarification_channel_unavailable"
                        break
                    waiting_started = monotonic()
                    answer = cfg.clarification_callback(question, round_number)
                    started_monotonic, goal_started_monotonic = _exclude_wait_from_timers(
                        started_monotonic, goal_started_monotonic, waiting_started, monotonic()
                    )
                    if answer is None or not answer.strip():
                        overall = Status.CANCELLED
                        completion_reason = "clarification_cancelled"
                        break
                    entry = {
                        "kind": "clarification",
                        "round": round_number,
                        "question": redactor.scrub(question),
                        "answer": redactor.scrub(answer.strip()),
                    }
                    cfg.clarification_history.append(entry)
                    scenario = getattr(cfg.agent_planner, "scenario", None)
                    if scenario is not None:
                        scenario.clarification_history = list(cfg.clarification_history)
                    artifacts.event("clarification_resolved", **entry)
                    emit(Status.RUNNING)
                    continue
                if decision.kind == "blocked":
                    overall = Status.INCOMPLETE
                    completion_reason = "agent_blocked"
                    break

                if decision.kind == "visual":
                    if cfg.visual_adapter is None:
                        overall = Status.INCOMPLETE
                        completion_reason = "visual_adapter_unavailable"
                        break
                    if len(model_records) - goal_model_call_start >= cfg.max_model_calls:
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
                if cfg.cesium_policy_enabled:
                    from .runner import _validate_runtime_cesium_step
                    _validate_runtime_cesium_step(step, index, cfg.cesium_owned_resources)
                page, locator_root, browser_context_evidence = resolve_browser_surface(
                    context, page, step.browser_target, policy,
                    enforce_url_condition=step.action not in {
                        ActionType.HUMAN_TAKEOVER,
                        ActionType.NAVIGATE,
                    },
                )
                collector = ObservationCollector(page, artifacts, redactor, cfg.ignore_rules)
                artifacts.event("browser_context_selected", index=index, **browser_context_evidence)
                step_started = _now()
                before = None
                stability_evidence = None
                canvas_evidence = None
                commerce_state_evidence = None
                recovery_evidence = None
                side_effect_evidence = None
                try:
                    _check_agent_step(
                        step, cfg.forbidden_actions,
                        visual_authorized=decision.kind == "visual",
                        bridge_authorized=bridge_adapter is not None,
                    )
                    summary = _step_summary(step, redactor)
                    artifacts.event("step_started", index=index, action=step.action.value, target=summary)
                    emit(Status.RUNNING, active_step={
                        "index": index,
                        "action": step.action.value,
                        "target": summary,
                        "started_at": step_started.isoformat(),
                    })
                    before = collector.capture(_capture_screenshot(page, artifacts, f"step-{index}-before"))
                    _require_commerce_metadata(step, cfg)
                    side_effect_evidence = evaluate_side_effect(
                        step, cfg.side_effect_policies,
                        environment_id=cfg.environment_id, role=plan.role,
                    )
                    if side_effect_evidence:
                        artifacts.event("side_effect_policy_evaluated", index=index, **side_effect_evidence)
                    confirmation_term = _approval_rule(
                        step,
                        cfg.approval_mode,
                        confirmation_match(step) or confirmation_rule(side_effect_evidence),
                    )
                    confirmed_by_human = False
                    if confirmation_term:
                        if step.action == ActionType.HUMAN_TAKEOVER and cfg.headless:
                            raise SecurityError("人工接管需要可见浏览器，不能在 headless 模式执行")
                        if cfg.confirmation_callback is None:
                            raise SecurityError(f"危险动作需要人工确认：{confirmation_term}")
                        artifacts.event("dangerous_action_confirmation_requested", index=index, rule=confirmation_term, target=summary)
                        waiting_started = monotonic()
                        approved = request_confirmation(
                            context,
                            guarded_route_handler,
                            artifacts.event,
                            cfg.confirmation_callback,
                            step,
                            index,
                            confirmation_term,
                        )
                        started_monotonic, goal_started_monotonic = _exclude_wait_from_timers(
                            started_monotonic, goal_started_monotonic, waiting_started, monotonic()
                        )
                        if not approved:
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
                            async_state_machines=cfg.async_state_machines,
                            component_adapters=cfg.component_adapters,
                            test_files=cfg.test_files,
                            timeout_ms=cfg.timeout_ms,
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
                        async_evidence=detail.get("asyncEvidence"),
                        component_evidence=detail.get("componentEvidence"),
                        side_effect_evidence=side_effect_evidence,
                        recovery_evidence=recovery_evidence,
                        before=before,
                        after=after,
                        planner_reason=redactor.scrub(decision.reason),
                        progress_assessment=assessment,
                    )
                    steps.append(result)
                    executed_steps.append(step)
                    observation = after
                    if made_progress:
                        no_progress_count = 0
                    elif not _is_recognized_cesium_loading_wait(step):
                        no_progress_count += 1
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
                        side_effect_evidence=side_effect_evidence,
                        recovery_evidence=recovery_evidence,
                        before=before,
                        after=after,
                        planner_reason=redactor.scrub(decision.reason),
                        progress_assessment="no_progress",
                    ))
                    executed_steps.append(step)
                    failed_step = failed_step or index
                    hints.append(_cause_hint(category, index, message))
                    can_continue = (
                        category in {FailureCategory.LOCATOR, FailureCategory.TIMEOUT}
                        and not isinstance(exc, SideEffectOutcomeUnknown)
                    )
                    if can_continue:
                        observation = after
                        no_progress_count += 1
                        completion_reason = "step_failed_continuing"
                        artifacts.event(
                            "step_failure_recorded_and_continuing",
                            index=index,
                            category=category.value,
                            no_progress_count=no_progress_count,
                        )
                        emit(Status.RUNNING)
                        if no_progress_count >= cfg.no_progress_limit:
                            overall = Status.INCOMPLETE
                            completion_reason = "consecutive_step_failures_reached"
                            artifacts.event(
                                "run_limit_reached",
                                limit="consecutive_step_failures",
                                count=no_progress_count,
                            )
                            break
                        continue
                    overall = Status.ERROR
                    completion_reason = (
                        "manual_reconciliation_required"
                        if isinstance(exc, SideEffectOutcomeUnknown)
                        else "execution_failed"
                    )
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
            if cfg.business_objects:
                cleanup_report = _run_business_cleanup(
                    page, cfg, base_url, policy, redactor, artifacts,
                    role=plan.role, bridge_adapter=bridge_adapter,
                )
                if cleanup_report["status"] != "passed":
                    overall = Status.ERROR
                    completion_reason = "business_cleanup_failed"
                    hints.append(_cause_hint(
                        FailureCategory.BUSINESS_STATE, failed_step or len(steps),
                        "业务对象反向清理未全部通过，必须按清理报告人工复核残留对象",
                    ))
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
        scenario_goal=current_goal,
        goal_status=goal_status,
        goal_summary=goal_summary,
        model_calls=len(model_records),
        input_tokens=sum(item.input_tokens for item in model_records),
        output_tokens=sum(item.output_tokens for item in model_records),
        estimated_cost=round(sum(item for item in costs if item is not None), 8) if costs and all(item is not None for item in costs) else None,
        model_call_records=model_records,
        confirmation_history=list(cfg.confirmation_history),
        clarification_history=list(cfg.clarification_history),
        result_classification="agent_passed" if overall == Status.PASSED else "agent_failed",
        model_data_authorization=cfg.model_data_authorization,
        commerce_summary=commerce_summary,
        account_id=cfg.account_id,
        account_role=cfg.account_role,
        project_snapshot=cfg.project_snapshot,
        environment_snapshot=cfg.environment_snapshot,
        business_context_snapshot=cfg.business_context_snapshot or cfg.business_context,
        app_map_snapshot=cfg.app_map_snapshot,
        websocket_timeline=websocket_collector.timeline,
        cleanup_report=cleanup_report,
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
    # Descriptions commonly restate safety boundaries such as "不修改资产".
    # Enforce forbidden actions against executable targets and locators instead
    # of treating those negated instructions as the action being performed.
    text = " ".join(filter(None, [
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


def _is_recognized_cesium_loading_wait(step: Step) -> bool:
    return (
        step.action == ActionType.SCREENSHOT
        and step.description == "Cesium ion 仍在启动，保持当前页面并短暂等待可交互内容出现。"
        and step.effect_kind == "browse_search_filter_sort"
        and step.effect_level is not None
        and step.effect_level.value == "read_only"
    )


def _stability(steps: list[Step]) -> str:
    ranks = {"A": 0, "B": 1, "C": 2, "D": 3}
    return max((step.stability_level.value for step in steps), key=lambda item: ranks[item], default="A")
