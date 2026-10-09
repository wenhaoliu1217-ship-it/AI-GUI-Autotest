"""受约束的 Playwright 测试计划执行器。"""

from __future__ import annotations

import base64
import hashlib
import os
import queue
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic, sleep
from typing import Callable
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from ..playwright_runtime import launch_browser, playwright_runtime

from ..artifacts import ArtifactManager, build_evidence_package
from ..benchmarks.cesium_ion.policy import CesiumPolicyError, validate_cesium_step
from ..assertions.checks import check_assertion
from ..commerce import (
    BusinessReference,
    CommerceActionRequest,
    CommercePolicyError,
    CommerceStateError,
    LedgerStatus,
    ResourceLedgerEntry,
    evaluate_commerce_action,
    evaluate_release_gate,
    observe_commerce_state,
    poll_commerce_state,
)
from ..domain.models import ActionType, Locator, Step, TestPlan
from ..domain.results import (
    AssertionResult,
    CauseHint,
    FailureCategory,
    RunResult,
    Status,
    StepResult,
)
from ..locating.strategies import LocatorError, resolve_locator, resolve_step_locator
from ..onboarding.session import playwright_storage_state, session_storage_init_script
from ..security.policy import DomainPolicy, SecurityError, guard_playwright_route, guard_playwright_websocket, resolve_env_placeholder, resolve_secret
from ..security.redaction import Redactor
from ..security.screenshot_privacy import screenshot_privacy_masks
from .observation import ObservationCollector
from .compiler import compile_test
from .confirmation import confirmation_match
from .findings import build_findings
from .bridge_adapter import CanvasAppBridgeAdapter, PreparedBridgeAction, create_bridge_adapter
from .stability import (
    attach_rendering_evidence,
    attach_visual_delta_evidence,
    finalize_canvas_evidence,
    prepare_action,
    strict_3d_evidence_passed,
)
from .time_budget import RunTimeBudget
from .browser_context import resolve_browser_surface
from .recovery import HttpExecutionError, SideEffectOutcomeUnknown, execute_with_recovery
from .async_state import AsyncStateError, WebSocketEvidenceCollector, wait_for_state
from .complex_components import execute_component, execute_select_value
from .component_policy import validate_component_step
from .side_effects import confirmation_rule, evaluate_side_effect
from .lifecycle import cleanup_business_objects
from .completion_gate import evaluate_completion_gate
from .verification import ActionVerificationError


SCREENSHOT_TIMEOUT_MS = 5_000
CDP_SCREENSHOT_TIMEOUT_MS = 3_000
# ``Page.captureScreenshot`` has no protocol-level timeout.  A timed-out
# daemon thread can therefore remain in-flight after the Runner has moved on.
# Keep one process-local guard so repeated fallbacks cannot accumulate blocked
# CDP calls (the guard is released by the worker, not by the timeout caller).
_CDP_SCREENSHOT_LOCK = Lock()
_RENDERING_WAIT_MARKERS = (
    "loading", "spinner", "skeleton", "render", "canvas", "webgl", "tiles", "scene",
)


@dataclass(frozen=True)
class RunnerConfig:
    artifacts_root: Path = Path("artifacts")
    headless: bool = True
    browser_name: str = ""
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
    rendering_wait_grace_seconds: int = 600
    progress_callback: Callable[[dict], None] | None = None
    agent_planner: Any | None = None
    decision_policy: str = "deterministic_plan"
    max_model_calls: int = 0
    model_recovery_attempts: int = 3
    model_recovery_backoff_seconds: tuple[float, ...] = (2.0, 5.0, 10.0)
    max_steps: int | None = None
    no_progress_limit: int = 3
    forbidden_actions: tuple[str, ...] = ()
    visual_adapter: Any | None = None
    success_experience_root: Path | None = None
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
    # A model request can block for 45s and may be retried before the worker
    # observes cancellation. Eight seconds caused the host to kill healthy
    # containers mid-step, so the default is now a bounded two-minute drain.
    isolation_cancel_grace_seconds: float = 120.0
    scenario_id: str | None = None
    scenario_updated_at: str | None = None
    scenario_goal: str | None = None
    approval_mode: str = "ask"
    confirmation_callback: Callable[[Step, int, str], bool] | None = None
    confirmation_history: list[dict] = field(default_factory=list)
    clarification_callback: Callable[[str, int], str | None] | None = None
    clarification_history: list[dict] = field(default_factory=list)
    # Process-local callbacks only; never serialized, persisted or sent to a model.
    manual_login_surface: dict = field(default_factory=dict)
    model_data_authorization: dict | None = None
    commerce_enabled: bool = False
    commerce_environment: str = "production_readonly"
    commerce_account_ref: str | None = None
    commerce_production_reversible_write_authorized: bool = False
    commerce_sandbox_driver: bool = False
    commerce_fixed_product_ref: str | None = None
    commerce_fixed_address_ref: str | None = None
    commerce_written_authorization_ref: str | None = None
    commerce_automatic_cancellation_verified: bool = False
    commerce_e2e_resource_prefix: str = "E2E_"
    file_assets: tuple[tuple[str, str], ...] = ()
    async_state_machines: tuple[dict, ...] = ()
    side_effect_policies: tuple[dict, ...] = ()
    component_adapters: tuple[dict, ...] = ()
    business_objects: tuple[dict, ...] = ()
    account_id: str | None = None
    account_role: str | None = None
    cesium_policy_enabled: bool = False
    cesium_owned_resources: tuple[tuple[str, str, str], ...] = ()
    project_snapshot: dict | None = None
    environment_snapshot: dict | None = None
    business_context_snapshot: dict | None = None
    app_map_snapshot: dict | None = None
    test_files: tuple[dict, ...] = ()
    business_context: dict | None = None


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
    redactor.register_environment_refs(secret_refs)
    base_url = resolve_env_placeholder(plan.base_url, environment_variables).rstrip("/")
    policy = DomainPolicy(
        base_url,
        list(cfg.allowed_hosts),
        allow_private_network=cfg.allow_private_network,
    )
    policy.check_url(base_url)
    overall = Status.PASSED
    completion_reason = "plan_completed"
    runtime_budget = RunTimeBudget(cfg.max_duration_seconds)
    commerce_ledger: dict[str, ResourceLedgerEntry] = {}
    commerce_decisions: list[dict] = []
    cleanup_report: dict | None = None

    def on_rendering_pause(phase: str, reason: str, duration: float) -> None:
        artifacts.event(
            "rendering_wait_" + phase,
            reason=reason,
            duration_seconds=round(duration, 3),
            **runtime_budget.snapshot(),
        )
        emit_progress(Status.RUNNING)

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
            **runtime_budget.snapshot(),
        })

    runtime_budget.rendering_pause_callback = on_rendering_pause

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

    with playwright_runtime() as playwright:
        shared_browser = False
        cdp_url = os.getenv("GUI_BROWSER_CDP_URL", "").strip()
        if not cfg.headless and os.getenv("GUI_RUNNER_MODE", "").lower() == "process" and cdp_url:
            try:
                browser = playwright.chromium.connect_over_cdp(cdp_url)
                shared_browser = True
                artifacts.event("managed_edge_attached", mode="shared_edge_cdp")
            except PlaywrightError as exc:
                artifacts.event(
                    "managed_edge_attach_failed",
                    error=redactor.scrub(str(exc)),
                    recovery="launch_isolated_browser",
                )
                browser = launch_browser(
                    playwright,
                    browser_name=cfg.browser_name,
                    headless=False,
                    slow_mo_ms=cfg.slow_mo_ms,
                )
                artifacts.event("managed_edge_recovered", mode="isolated_headful_browser")
        else:
            browser = launch_browser(
                playwright,
                browser_name=cfg.browser_name,
                headless=cfg.headless,
                slow_mo_ms=cfg.slow_mo_ms,
            )
        context = browser.new_context(
            viewport={"width": cfg.viewport[0], "height": cfg.viewport[1]},
            device_scale_factor=cfg.device_scale_factor,
            locale="en-US",
            storage_state=playwright_storage_state(cfg.storage_state),
            accept_downloads=True,
            service_workers="block",
        )
        session_storage_script = session_storage_init_script(cfg.storage_state)
        if session_storage_script:
            context.add_init_script(script=session_storage_script)
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
        context.tracing.start(screenshots=False, snapshots=False, sources=False)
        artifacts.event(
            "trace_pixel_privacy_enabled",
            screenshots_embedded=False,
            dom_snapshots_embedded=False,
        )
        page = context.new_page()
        websocket_collector = WebSocketEvidenceCollector(redactor)
        websocket_collector.attach(page)
        collector = ObservationCollector(page, artifacts, redactor, cfg.ignore_rules)
        page.set_default_timeout(cfg.timeout_ms)
        page.set_default_navigation_timeout(cfg.timeout_ms)
        try:
            for index, step in enumerate(plan.steps, start=1):
                if cfg.cesium_policy_enabled:
                    _validate_runtime_cesium_step(step, index, cfg.cesium_owned_resources)
                if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                    overall = Status.CANCELLED
                    completion_reason = "cancelled_by_user"
                    artifacts.event("run_cancelled", before_step=index)
                    break
                if runtime_budget.exceeded():
                    overall = Status.INCOMPLETE
                    completion_reason = "time_limit_exceeded"
                    artifacts.event("run_limit_reached", limit="max_duration_seconds", before_step=index)
                    break
                page, locator_root, browser_context_evidence = resolve_browser_surface(
                    context, page, step.browser_target, policy,
                    enforce_url_condition=step.action != ActionType.HUMAN_TAKEOVER,
                )
                collector = ObservationCollector(page, artifacts, redactor, cfg.ignore_rules)
                artifacts.event("browser_context_selected", index=index, **browser_context_evidence)
                step_started = _now()
                summary = _step_summary(step, redactor)
                artifacts.event("step_started", index=index, action=step.action.value, target=summary)
                previous_step = plan.steps[index - 2] if index > 1 else None
                next_step = plan.steps[index] if index < len(plan.steps) else None
                defer_before_evidence = _should_defer_before_transition_wait(step, previous_step)
                if defer_before_evidence:
                    before_shot = None
                    before = collector.capture(None, detailed=False)
                    artifacts.event(
                        "evidence_capture_deferred",
                        index=index,
                        phase="before",
                        reason="preceding_route_transition",
                    )
                else:
                    before_shot = _capture_screenshot(page, artifacts, f"step-{index}-before")
                    before = collector.capture(before_shot)
                _require_commerce_metadata(step, cfg)
                side_effect_evidence = evaluate_side_effect(
                    step, cfg.side_effect_policies,
                    environment_id=cfg.environment_id, role=plan.role or cfg.account_role,
                )
                if side_effect_evidence:
                    artifacts.event("side_effect_policy_evaluated", index=index, **side_effect_evidence)
                confirmation_term = confirmation_match(step) or confirmation_rule(side_effect_evidence)
                confirmed_by_human = False
                if confirmation_term:
                    if step.action == ActionType.HUMAN_TAKEOVER and cfg.headless:
                        raise SecurityError("人工接管需要可见浏览器，不能在 headless 模式执行")
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
                        artifacts.event("dangerous_action_rejected", index=index, rule=confirmation_term)
                        if was_cancelled:
                            overall = Status.CANCELLED
                            completion_reason = "cancelled_by_user"
                            emit_progress(Status.CANCELLED)
                            break
                        completion_reason = "dangerous_action_rejected_continuing"
                        artifacts.event(
                            "action_skipped_after_rejection",
                            index=index,
                            action=step.action.value,
                        )
                        emit_progress(Status.RUNNING)
                        continue
                    artifacts.event("dangerous_action_approved", index=index, rule=confirmation_term)
                    confirmed_by_human = True
                _commerce_preflight(
                    step, cfg, run_id, confirmed_by_human, commerce_ledger, artifacts, index,
                    commerce_decisions,
                )
                stability_evidence = None
                canvas_evidence = None
                commerce_state_evidence = None
                recovery_evidence = None
                try:
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
                    action_url_before = page.url

                    def recover_session() -> None:
                        recovered_page, recovered_root = _restore_page_session(
                            context, recovery_url, step, policy, cfg
                        )
                        execution_page[0] = recovered_page
                        execution_root[0] = recovered_root

                    execution_detail, recovery_evidence = execute_with_recovery(
                        step,
                        lambda: _execute_step(
                            execution_page[0], step, base_url, policy, redactor,
                            environment_variables=environment_variables, secret_refs=secret_refs,
                            bridge_adapter=bridge_adapter, bridge_prepared=prepared.bridge_action,
                            click_target=prepared.click_target,
                            locator_root=execution_root[0], file_assets=dict(cfg.file_assets), artifacts=artifacts,
                            async_state_machines=cfg.async_state_machines,
                            component_adapters=cfg.component_adapters,
                            test_files=cfg.test_files,
                            timeout_ms=cfg.timeout_ms,
                            run_budget=runtime_budget,
                        ),
                        wait=lambda milliseconds: sleep(milliseconds / 1000),
                        probe=_commerce_recovery_probe(
                            context, step, cfg, run_id, base_url, policy
                        ),
                        recover_session=recover_session,
                        max_read_attempts=(
                            2
                            if step.action in {ActionType.NAVIGATE, ActionType.RELOAD, ActionType.BACK, ActionType.WAIT_FOR}
                            else 3
                        ),
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
                        execution_detail=execution_detail,
                        before_screenshot=before_shot,
                        after_screenshot=None,
                    )
                    defer_after_evidence = _should_defer_after_route_transition(
                        step,
                        next_step,
                        action_url_before,
                        page.url,
                    )
                    if defer_after_evidence:
                        relative = None
                        artifacts.event(
                            "evidence_capture_deferred",
                            index=index,
                            phase="after",
                            reason="route_transition_followed_by_wait",
                        )
                    else:
                        relative = _capture_screenshot(
                            page, artifacts, f"step-{index}-after"
                        )
                    if canvas_evidence is not None:
                        canvas_evidence["afterScreenshot"] = relative
                        artifacts.event("canvas_evidence_collected", index=index, **canvas_evidence)
                    after = collector.capture(
                        relative,
                        detailed=not defer_after_evidence,
                        skip_canvas_readback=(
                            step.action in {
                                ActionType.VISUAL_CLICK,
                                ActionType.VISUAL_HOVER,
                                ActionType.VISUAL_SCROLL,
                                ActionType.VISUAL_DRAG,
                            }
                            and (urlparse(page.url).hostname or "").lower() == "ion.cesium.com"
                        ),
                    )
                    rendering_observation = after
                    if (
                        canvas_evidence is not None
                        and after.semantic_summary is None
                        and before.semantic_summary is not None
                    ):
                        # A busy WebGL frame can briefly block the post-action
                        # semantic probe. Keep the bound Canvas facts from the
                        # immediately preceding observation and require the
                        # post-action screenshot delta below to prove change.
                        canvas_evidence["renderingEvidencePhase"] = "before_action_fallback"
                        rendering_observation = before
                    canvas_evidence = attach_rendering_evidence(canvas_evidence, rendering_observation)
                    canvas_evidence = attach_visual_delta_evidence(canvas_evidence, artifacts)
                    if canvas_evidence is not None:
                        artifacts.event("canvas_rendering_evidence", index=index, **canvas_evidence)
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
                        browser_context_evidence={**browser_context_evidence, **execution_detail.get("browserContext", {})},
                        commerce_state_evidence=commerce_state_evidence,
                        file_evidence=execution_detail.get("fileEvidence"),
                        async_evidence=execution_detail.get("asyncEvidence"),
                        component_evidence=execution_detail.get("componentEvidence"),
                        side_effect_evidence=side_effect_evidence,
                        recovery_evidence=recovery_evidence,
                        before=before,
                        after=after,
                    )
                    steps.append(result)
                    if defer_before_evidence and relative and _link_deferred_after_evidence(
                        steps, after, relative
                    ):
                        artifacts.event(
                            "deferred_evidence_linked",
                            source_step=index,
                            target_step=index - 1,
                            screenshot=relative,
                            reason="route_transition_followed_by_wait",
                        )
                    artifacts.event("step_passed", index=index, duration_ms=result.duration_ms)
                    emit_progress(Status.RUNNING)
                    if cfg.clarification_callback is not None:
                        from .agent_runner import _is_interactive_login_page, _wait_for_manual_login
                        if _is_interactive_login_page(after):
                            resumed = _wait_for_manual_login(
                                context, page, after, cfg, collector, artifacts, runtime_budget, policy
                            )
                            if resumed is None:
                                overall = Status.CANCELLED
                                completion_reason = "manual_login_cancelled"
                                break
                            emit_progress(Status.RUNNING)
                    if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                        overall = Status.CANCELLED
                        completion_reason = "cancelled_by_user"
                        artifacts.event("run_cancelled", after_step=index)
                        break
                except Exception as exc:
                    recovery_evidence = getattr(exc, "evidence", recovery_evidence)
                    if recovery_evidence:
                        artifacts.event("execution_recovery_evaluated", index=index, **recovery_evidence)
                    category = _failure_category(exc)
                    if defer_before_evidence:
                        relative = None
                        after = collector.capture(None, detailed=False)
                        artifacts.event(
                            "evidence_capture_deferred",
                            index=index,
                            phase="failure",
                            reason="transition_wait_failed",
                        )
                    else:
                        relative = _capture_screenshot(
                            page, artifacts, f"step-{index}-after-failure", stop_loading=True
                        )
                        after = collector.capture(
                            relative,
                            skip_canvas_readback=(
                                step.action in {
                                    ActionType.VISUAL_CLICK,
                                    ActionType.VISUAL_HOVER,
                                    ActionType.VISUAL_SCROLL,
                                    ActionType.VISUAL_DRAG,
                                }
                                and (urlparse(page.url).hostname or "").lower() == "ion.cesium.com"
                            ),
                        )
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
                        browser_context_evidence=browser_context_evidence,
                        commerce_state_evidence=commerce_state_evidence,
                        recovery_evidence=recovery_evidence,
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
                    completion_reason = (
                        "cancelled_by_user" if was_cancelled else
                        "manual_reconciliation_required" if isinstance(exc, SideEffectOutcomeUnknown) else
                        "execution_failed"
                    )
                    emit_progress(Status.RUNNING)
                    break

            if overall == Status.PASSED:
                for index, assertion in enumerate(plan.assertions, start=1):
                    if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                        overall = Status.CANCELLED
                        completion_reason = "cancelled_by_user"
                        artifacts.event("run_cancelled", before_assertion=index)
                        break
                    if runtime_budget.exceeded():
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
                if not shared_browser:
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
                "reproduction": [_step_summary(item, redactor) for item in plan.steps[:len(steps)]],
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
    reproduction = [_step_summary(step, redactor) for step in plan.steps[:len(steps)]]
    findings = build_findings(steps, assertions, reproduction)
    source, generated_test = compile_test(plan)
    generated_test.source_path = artifacts.write_text(generated_test.source_path, source)
    stability = generated_test.stability_level
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
        goal_status="incomplete",
        goal_summary="等待 CompletionGate 判定",
        confirmation_history=list(cfg.confirmation_history),
        commerce_summary=commerce_summary,
        result_classification="fixed_passed" if overall == Status.PASSED else "fixed_failed",
        decision_policy=cfg.decision_policy,
        account_id=cfg.account_id,
        account_role=cfg.account_role,
        project_snapshot=cfg.project_snapshot,
        environment_snapshot=cfg.environment_snapshot,
        business_context_snapshot=cfg.business_context_snapshot or cfg.business_context,
        app_map_snapshot=cfg.app_map_snapshot,
        websocket_timeline=websocket_collector.timeline,
        cleanup_report=cleanup_report,
    )
    result = _apply_completion_gate(
        result,
        artifacts,
        required_stage_count=len(plan.steps),
        cleanup_required=bool(cfg.business_objects or cfg.commerce_enabled),
    )
    overall = result.status
    completion_reason = result.completion_reason
    artifacts.event("run_finished", status=overall.value, duration_ms=result.duration_ms)
    artifacts.finalize(result)
    emit_progress(overall, ended_at=ended)
    return result, artifacts.run_dir


def _apply_completion_gate(
    result: RunResult,
    artifacts: ArtifactManager,
    *,
    required_stage_count: int,
    completed_stage_count: int | None = None,
    cleanup_required: bool,
    stable_replay_required: bool = True,
    strict_3d_required: bool | None = None,
) -> RunResult:
    manifest, manifest_path = build_evidence_package(artifacts, result)
    if strict_3d_required is None:
        strict_3d_required = (
            (urlparse(result.base_url_summary).hostname or "").lower() == "ion.cesium.com"
            and any(
                getattr(item, "execution_mode", "") == "visual"
                or getattr(getattr(item, "execution_mode", ""), "value", "") == "visual"
                for item in result.steps
            )
        )
    strict_3d_passed = strict_3d_evidence_passed(result.steps) if strict_3d_required else True
    gate = evaluate_completion_gate(
        status=result.status,
        completion_reason=result.completion_reason,
        assertions=result.assertions,
        required_stage_count=required_stage_count,
        completed_stage_count=(
            completed_stage_count
            if completed_stage_count is not None
            else sum(item.status == Status.PASSED for item in result.steps)
        ),
        evidence_manifest=manifest,
        cleanup_report=result.cleanup_report,
        commerce_summary=result.commerce_summary,
        cleanup_required=cleanup_required,
        replay_mode=result.replay_mode,
        stable_replay_required=stable_replay_required,
        stable_replay_result=(
            bool(result.stable_replay.get("passed"))
            if result.stable_replay is not None else None
        ),
        strict_3d_required=strict_3d_required,
        strict_3d_passed=strict_3d_passed,
        scoped_task=all(token in (result.scenario_goal or "") for token in ("仅当前页面暂存", "不点击保存")),
    )
    gate_payload = gate.model_dump(mode="json")
    final_status = result.status
    final_reason = result.completion_reason
    scoped_goal_achieved = gate.goal_status == "achieved" and all(
        token in (result.scenario_goal or "") for token in ("仅当前页面暂存", "不点击保存")
    )
    # A scoped form task can be complete even when the model service is
    # throttled immediately afterwards.  Preserve the gateway warning in the
    # summary, but do not let it overwrite the already-achieved user goal.
    if scoped_goal_achieved and result.status == Status.INCOMPLETE:
        final_status = Status.PASSED
        final_reason = "scoped_goal_achieved_model_warning"
    if result.status == Status.PASSED and gate.goal_status != "achieved":
        final_status = Status.INCOMPLETE
        final_reason = "completion_gate_failed"
    model_failure_summaries = {
        "target_service_not_implemented": (
            "启动按钮点击已派发，但目标 startSimulation 服务返回 HTTP 501 Not Implemented；"
            "目标仿真状态未进入运行态，系统已保留请求、截图和验证证据，并禁止自动重放。"
            "这表示目标服务能力未实现，不是控件定位或点击派发失败。"
        ),
        "model_service_unavailable": (
            "模型服务暂不可用，运行已安全停止；该结果不代表目标网站存在业务缺陷。"
            "请重新运行模型能力探针后再测试。"
        ),
        "model_configuration_error": (
            "模型配置无效，运行未进入后续网站操作；该结果不代表目标网站存在业务缺陷。"
            "请检查 API 地址、模型名、密钥和接口权限。"
        ),
        "model_output_recovery_exhausted": (
            "模型连续返回无法通过本地契约校验的输出，系统已在限定次数后安全停止；"
            "该结果不代表目标网站存在业务缺陷。请检查所选模型的结构化输出能力。"
        ),
        "model_local_contract_error": (
            "本地 Agent 决策契约未满足，系统已在执行任何新动作前停止；"
            "该结果属于本地执行问题，不代表目标网站存在业务缺陷。"
        ),
        "target_application_runtime_error": (
            "目标网站显示致命客户端渲染错误，系统已保存当前截图和页面事实，并阻止后续写入；"
            "该结果属于目标应用故障，不能作为测试通过。"
        ),
    }
    model_failure_summary = model_failure_summaries.get(result.completion_reason)
    if scoped_goal_achieved and model_failure_summary is not None:
        summary = (
            "局部测试目标已完成；后续模型服务触发限流，未继续执行非必要步骤。"
            "该网关问题不影响本次局部目标结果。"
        )
        result_classification = "scoped_goal_achieved_with_model_warning"
    elif model_failure_summary is not None:
        summary = model_failure_summary + "CompletionGate 原因：" + "；".join(gate.reasons)
        result_classification = result.completion_reason
    else:
        summary = (
            "CompletionGate 已通过"
            if gate.goal_status == "achieved"
            else "CompletionGate 未通过：" + "；".join(gate.reasons)
        )
        result_classification = f"{result.result_classification.split('_', 1)[0]}_{gate.run_status}"
    result = result.model_copy(update={
        "status": final_status,
        "completion_reason": final_reason,
        "goal_status": gate.goal_status,
        "goal_summary": summary,
        "completion_gate": gate_payload,
        "evidence_manifest": manifest,
        "evidence_manifest_path": manifest_path,
        "result_classification": result_classification,
    })
    # Refresh run metadata after the verdict so every evidence file agrees with run.json.
    manifest, manifest_path = build_evidence_package(artifacts, result)
    artifacts.write_json("completion-gate.json", gate_payload)
    artifacts.event("completion_gate_evaluated", **gate_payload)
    return result.model_copy(update={
        "evidence_manifest": manifest,
        "evidence_manifest_path": manifest_path,
    })


def _goal_outcome(status: Status, assertions: list[AssertionResult], completion_reason: str) -> tuple[str, str]:
    """Legacy helper retained for imports; it can no longer grant achieved."""
    passed = sum(item.status == Status.PASSED for item in assertions)
    total = len(assertions)
    assertion_summary = f"断言通过 {passed}/{total}" if total else "无收尾断言"
    if status == Status.PASSED:
        return "incomplete", f"尚未执行 CompletionGate；{assertion_summary}"
    if status in {Status.CANCELLED, Status.SYSTEM_ERROR, Status.ERROR, Status.INCOMPLETE}:
        return "incomplete", f"场景未完整执行；{assertion_summary}；结束原因 {completion_reason}"
    return "not_achieved", f"场景目标未完成；{assertion_summary}；结束原因 {completion_reason}"


def _execute_step(
    page, step: Step, base_url: str, policy: DomainPolicy, redactor: Redactor,
    *, environment_variables: dict[str, str] | None = None, secret_refs: dict[str, str] | None = None,
    bridge_adapter: CanvasAppBridgeAdapter | None = None,
    bridge_prepared: PreparedBridgeAction | None = None,
    click_target=None,
    locator_root=None,
    file_assets: dict[str, str] | None = None,
    artifacts: ArtifactManager | None = None,
    async_state_machines: tuple[dict, ...] = (),
    component_adapters: tuple[dict, ...] = (),
    test_files: tuple[dict, ...] = (),
    timeout_ms: int = 30_000,
    run_budget: RunTimeBudget | None = None,
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
            raise HttpExecutionError(response.status, url)
        try:
            with (run_budget.rendering_wait("navigation_domcontentloaded") if run_budget else nullcontext()):
                page.wait_for_load_state("domcontentloaded", timeout=8_000)
        except PlaywrightTimeoutError:
            # 已收到页面响应时，不因第三方资源拖慢 DOMContentLoaded 而制造随机失败。
            # 后续定位与断言仍会按完整超时时间验证页面是否真正可操作。
            pass
        ready = _wait_for_navigation_readiness(page, timeout_ms, run_budget=run_budget)
        return {"navigationReady": ready}

    if step.action == ActionType.SCREENSHOT:
        # 每一步执行完成后都会统一采集页面截图；该动作只提供显式检查点。
        return {}

    if step.action == ActionType.HUMAN_TAKEOVER:
        deadline = monotonic() + step.browser_target.wait_timeout_ms / 1000
        while step.browser_target.url_contains and step.browser_target.url_contains not in page.url:
            if monotonic() >= deadline:
                raise PlaywrightTimeoutError("人工接管后 URL 未达到恢复条件")
            page.wait_for_timeout(100)
        if step.takeover_resume_locator:
            resolve_locator(locator_root or page, step.takeover_resume_locator).wait_for(
                state="visible", timeout=step.browser_target.wait_timeout_ms
            )
        policy.check_url(page.url)
        return {"browserContext": {
            "humanTakeover": True,
            "takeoverReason": step.takeover_reason,
            "resumeUrlVerified": bool(step.browser_target.url_contains),
            "resumeLocatorVerified": bool(step.takeover_resume_locator),
        }}

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

    if step.action == ActionType.WAIT_FOR_STATE:
        machine = next(
            (item for item in async_state_machines if item.get("id") == step.state_machine_id),
            None,
        )
        if machine is None:
            raise SecurityError(f"异步状态机未在当前项目登记：{step.state_machine_id}")
        return {"asyncEvidence": wait_for_state(page, step, machine, run_budget=run_budget)}

    if step.action == ActionType.COMPONENT:
        validate_component_step(step, component_adapters, require_adapter=bool(component_adapters))
        if artifacts is None:
            raise SecurityError("复杂组件动作缺少单次运行工件目录")
        evidence = execute_component(page, step, test_files, artifacts, timeout_ms)
        return {"componentEvidence": evidence}

    if step.action in {
        ActionType.VISUAL_CLICK, ActionType.VISUAL_HOVER, ActionType.VISUAL_SCROLL,
        ActionType.VISUAL_DRAG, ActionType.VISUAL_ZOOM,
        ActionType.VISUAL_DRAW_POLYGON, ActionType.VISUAL_DRAW_RECTANGLE,
    }:
        region_locator = step.canvas_region_locator or step.locator
        if region_locator:
            locator = resolve_locator(page, region_locator)
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
        position = step.relative_position or (
            step.visual_points[0] if step.visual_points else None
        )
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
            # Cesium can leave the CDP mouse-wheel call pending while its
            # WebGL frame is settling. Dispatch the same wheel event through
            # the bound DOM target so the action remains bounded and the
            # viewer's camera handler still receives it.
            wheel_result = page.evaluate(
                """({x, y, deltaY}) => {
                    const target = document.elementFromPoint(x, y);
                    if (!target) return {dispatched: false, target: null};
                    const event = new WheelEvent('wheel', {
                        deltaX: 0,
                        deltaY,
                        clientX: x,
                        clientY: y,
                        bubbles: true,
                        cancelable: true,
                    });
                    return {
                        dispatched: target.dispatchEvent(event),
                        target: target.tagName.toLowerCase(),
                        defaultPrevented: event.defaultPrevented,
                    };
                }""",
                {"x": x, "y": y, "deltaY": step.scroll_delta_y},
            )
            if not isinstance(wheel_result, dict) or not wheel_result.get("target"):
                raise PlaywrightError("视觉滚动目标无法接收 wheel 事件")
            page.wait_for_timeout(100)
        elif step.action == ActionType.VISUAL_DRAG:
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
        elif step.action == ActionType.VISUAL_ZOOM:
            page.mouse.move(x, y)
            page.mouse.wheel(0, step.zoom_delta)
        elif step.action in {
            ActionType.VISUAL_DRAW_POLYGON,
            ActionType.VISUAL_DRAW_RECTANGLE,
        }:
            points = []
            for point in step.visual_points:
                point_x = box["x"] + box["width"] * point.x_ratio
                point_y = box["y"] + box["height"] * point.y_ratio
                if not (
                    box["x"] <= point_x <= box["x"] + box["width"]
                    and box["y"] <= point_y <= box["y"] + box["height"]
                ):
                    raise SecurityError("视觉绘制点超出授权 Canvas 区域边界")
                points.append((point_x, point_y))
            if step.action == ActionType.VISUAL_DRAW_RECTANGLE:
                start, end = points
                page.mouse.move(*start)
                page.mouse.down()
                try:
                    page.mouse.move(*end, steps=12)
                finally:
                    page.mouse.up()
            else:
                for point_index, point in enumerate(points):
                    is_last = point_index == len(points) - 1
                    if is_last and step.gesture_finish == "double_click":
                        page.mouse.dblclick(*point, delay=80)
                    else:
                        page.mouse.click(*point)
                    page.wait_for_timeout(80)
                if step.gesture_finish == "enter":
                    page.keyboard.press("Enter")
        return {
            "coordinateSource": f"{source}:{position.x_ratio:.4f},{position.y_ratio:.4f}",
            "visualPointCount": len(step.visual_points),
            "gestureFinish": step.gesture_finish,
            "zoomDelta": step.zoom_delta if step.action == ActionType.VISUAL_ZOOM else None,
        }

    locator = resolve_step_locator(
        locator_root or page,
        step,
        scroll_page=page,
        allow_unresolved=step.action == ActionType.WAIT_FOR,
    )
    if step.action == ActionType.WAIT_FOR:
        state = step.value or "visible"
        locator_description = step.locator.describe().lower() if step.locator else ""
        description = (step.description or "").lower()
        is_rendering_wait = any(
            marker in f"{locator_description} {description}"
            for marker in _RENDERING_WAIT_MARKERS
        )
        with (
            run_budget.rendering_wait("wait_for_rendering")
            if run_budget and is_rendering_wait else nullcontext()
        ):
            if state in {"hidden", "detached"}:
                matched_count = locator.count()
                deadline = monotonic() + timeout_ms / 1000
                for match_index in reversed(range(matched_count)):
                    remaining_ms = max(1, round((deadline - monotonic()) * 1000))
                    locator.nth(match_index).wait_for(state=state, timeout=remaining_ms)
                return {"waitState": state, "matchedCount": matched_count}
            locator.wait_for(state=state, timeout=timeout_ms)
            if state in {"visible", "attached"}:
                count = locator.count()
                if count != 1:
                    raise LocatorError(
                        f"等待目标必须唯一，实际匹配 {count} 个：{step.locator.describe()}"
                    )
            return {"waitState": state, "matchedCount": count}
    target = locator if step.commerce_scope else locator.first
    if step.action == ActionType.UPLOAD_FILE:
        path_text = (file_assets or {}).get(step.file_asset_ref or "")
        if not path_text:
            raise SecurityError("上传文件引用未由当前项目授权")
        path = Path(path_text).resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if step.file_asset_ref != f"asset:{digest}":
            raise SecurityError("上传文件在执行前完整性校验失败")
        target.set_input_files(str(path))
        return {"fileEvidence": {"direction": "upload", "assetRef": step.file_asset_ref, "sha256": digest, "bytes": path.stat().st_size}}
    if step.action == ActionType.DOWNLOAD:
        if artifacts is None:
            raise SecurityError("下载动作缺少单次运行工件目录")
        with page.expect_download() as download_info:
            target.click()
        download = download_info.value
        filename = Path(download.suggested_filename).name
        target_dir = artifacts.run_dir / "downloads"
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / filename
        download.save_as(str(destination))
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if step.expected_download_sha256 and digest != step.expected_download_sha256:
            raise SecurityError("下载文件 SHA-256 与期望不一致")
        return {"fileEvidence": {"direction": "download", "filename": filename, "sha256": digest, "bytes": destination.stat().st_size, "artifact": f"downloads/{filename}"}}
    if step.action == ActionType.CLICK:
        url_before_click = page.url
        effective_target = click_target or target
        target_href = effective_target.get_attribute("href")
        # The target may start a long-lived async workflow without navigating.
        # Playwright's default click waits for every scheduled navigation and
        # can therefore time out after the click has already dispatched (the
        # GAEALaViC Start button changes the route to ``Starting`` and opens an
        # SSE request).  The runner owns post-click settlement and verification,
        # so dispatch the click without Playwright's implicit navigation wait.
        effective_target.click(no_wait_after=True)
        if artifacts is not None:
            artifacts.event(
                "click_dispatched",
                action=step.action.value,
                target=step.locator.describe() if step.locator else step.description,
                before_url=redactor.scrub(url_before_click),
                href=redactor.scrub(target_href) if target_href else None,
                navigation_wait="runner_settlement",
            )
        _wait_for_client_settlement(
            page,
            url_before_click,
            min(timeout_ms, 8_000),
            require_url_change=bool(target_href),
            run_budget=run_budget,
        )
    elif step.action == ActionType.FILL:
        value = resolve_secret(step.value_from_secret, redactor, secret_refs) if step.value_from_secret else resolve_env_placeholder(step.value or "", environment_variables)
        try:
            target.fill(value)
        except PlaywrightTimeoutError:
            # React/Ant Design may replace a visible input between the
            # stability probe and dispatch. Re-resolve the structured locator
            # once against the current document; never fall back to a stale
            # coordinate for text input.
            target = resolve_step_locator(locator_root or page, step, scroll_page=page).first
            target.wait_for(state="visible", timeout=min(timeout_ms, 5_000))
            target.fill(value)
        try:
            actual = target.input_value()
        except PlaywrightError:
            # ``fill`` also supports contenteditable editors, which expose
            # their state through textContent rather than input_value().
            actual = target.inner_text()
        return {
            "controlState": {
                "kind": "value",
                "verified": actual == value,
                "actualLength": len(actual),
                "expectedLength": len(value),
            }
        }
    elif step.action == ActionType.SELECT:
        value = resolve_secret(step.value_from_secret, redactor, secret_refs) if step.value_from_secret else resolve_env_placeholder(step.value or "", environment_variables)
        try:
            control_state = execute_select_value(page, target, value, timeout_ms)
        except (PlaywrightTimeoutError, PlaywrightError):
            target = resolve_step_locator(locator_root or page, step, scroll_page=page).first
            target.wait_for(state="visible", timeout=min(timeout_ms, 5_000))
            control_state = execute_select_value(page, target, value, timeout_ms)
        control_state.update({
            # Keep the evidence value-safe while still exposing enough detail
            # for the action contract to verify the selection.
            "actualLength": len(str(control_state.get("selectedText") or "")),
            "expectedLength": len(value),
        })
        return {"controlState": control_state}
    elif step.action == ActionType.CLEAR:
        target.clear()
        try:
            actual = target.input_value()
        except PlaywrightError:
            actual = target.inner_text()
        return {
            "controlState": {
                "kind": "value",
                "verified": actual == "",
                "actualLength": len(actual),
                "expectedLength": 0,
            }
        }
    elif step.action == ActionType.CHECK:
        target.check()
        return {"controlState": {"kind": "checked", "verified": target.is_checked()}}
    elif step.action == ActionType.UNCHECK:
        target.uncheck()
        return {"controlState": {"kind": "checked", "verified": not target.is_checked()}}
    elif step.action == ActionType.HOVER:
        target.hover()
    elif step.action == ActionType.SCROLL:
        locator.first.scroll_into_view_if_needed()
        page.mouse.wheel(0, step.scroll_delta_y)
    elif step.action == ActionType.PRESS:
        locator.first.press(step.value or "")
    else:
        raise ValueError(f"未实现的动作：{step.action.value}")
    return {}


def _wait_for_client_settlement(
    page, url_before: str, timeout_ms: int, *, require_url_change: bool,
    run_budget: RunTimeBudget | None = None,
) -> None:
    """Let SPA route and loading state settle before the next planned action."""
    try:
        with (run_budget.rendering_wait("client_settlement") if run_budget else nullcontext()):
            page.wait_for_timeout(250)
            page.wait_for_function(
                """({ before, requireChange }) => {
                const visible = (element) => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                        Number(style.opacity || 1) > 0.01 && rect.width > 0 && rect.height > 0;
                };
                const stillLoading = (element) => {
                    if (!visible(element)) return false;
                    if (element.getAttribute('role') === 'progressbar') {
                        const now = Number(element.getAttribute('aria-valuenow'));
                        const max = Number(element.getAttribute('aria-valuemax'));
                        if (Number.isFinite(now) && Number.isFinite(max) && max > 0 && now >= max) return false;
                        const summary = `${element.getAttribute('aria-label') || ''} ${element.textContent || ''}`.toLowerCase();
                        if (/\b(complete|completed|done|finished)\b|100%|上传完成|已完成/.test(summary)) return false;
                    }
                    return true;
                };
                const busy = Array.from(document.querySelectorAll(
                    '[aria-busy="true"], [role="progressbar"], .page-loading-placeholder, .loading, .spinner'
                )).some(stillLoading);
                const changed = location.href !== before;
                return document.readyState !== 'loading' && !busy && (!requireChange || changed);
            }""",
                arg={"before": url_before, "requireChange": require_url_change},
                timeout=timeout_ms,
            )
            page.wait_for_timeout(180)
    except PlaywrightTimeoutError:
        # The following observation still records the unresolved loading state;
        # settlement is a bounded aid, not a reason to hide a product timeout.
        return


def _wait_for_navigation_readiness(
    page, timeout_ms: int, *, run_budget: RunTimeBudget | None = None
) -> bool:
    """Best-effort wait for startup placeholders to leave the viewport."""
    try:
        with (run_budget.rendering_wait("navigation_render_readiness") if run_budget else nullcontext()):
            page.wait_for_function(
                """() => {
                const visible = (element) => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                        Number(style.opacity || 1) > 0.01 && rect.width > 0 && rect.height > 0;
                };
                const stillLoading = (element) => {
                    if (!visible(element)) return false;
                    if (element.getAttribute('role') === 'progressbar') {
                        const now = Number(element.getAttribute('aria-valuenow'));
                        const max = Number(element.getAttribute('aria-valuemax'));
                        if (Number.isFinite(now) && Number.isFinite(max) && max > 0 && now >= max) return false;
                        const summary = `${element.getAttribute('aria-label') || ''} ${element.textContent || ''}`.toLowerCase();
                        if (/\b(complete|completed|done|finished)\b|100%|上传完成|已完成/.test(summary)) return false;
                    }
                    return true;
                };
                const loading = Array.from(document.querySelectorAll(
                    '.page-loading-placeholder, [aria-busy="true"], [role="progressbar"], .loading, .spinner, [class*="spinner" i], [class*="loading" i], svg[class*="spin" i]'
                )).some(stillLoading);
                return document.readyState !== 'loading' && document.body && !loading;
            }""",
                timeout=timeout_ms,
            )
        return True
    except PlaywrightTimeoutError:
        return False


def _run_business_cleanup(
    page, cfg: RunnerConfig, base_url: str, policy: DomainPolicy,
    redactor: Redactor, artifacts: ArtifactManager, *, role: str | None,
    bridge_adapter: CanvasAppBridgeAdapter | None,
) -> dict:
    def execute(item: dict) -> dict:
        step = Step.model_validate(item["cleanupStep"])
        evidence = evaluate_side_effect(
            step, cfg.side_effect_policies,
            environment_id=cfg.environment_id, role=role,
        )
        rule = confirmation_match(step) or confirmation_rule(evidence)
        if rule:
            if cfg.confirmation_callback is None or not cfg.confirmation_callback(step, 0, rule):
                raise SecurityError(f"清理动作未获人工确认：{rule}")
        detail = _execute_step(
            page, step, base_url, policy, redactor,
            environment_variables=dict(cfg.environment_variables),
            secret_refs=dict(cfg.secret_refs), bridge_adapter=bridge_adapter,
            file_assets=dict(cfg.file_assets), artifacts=artifacts,
            async_state_machines=cfg.async_state_machines,
            component_adapters=cfg.component_adapters, timeout_ms=cfg.timeout_ms,
            test_files=cfg.test_files,
        )
        return {**detail, "sideEffectEvidence": evidence}

    def verify(item: dict) -> bool:
        locator_payload = item.get("verificationLocator")
        if not locator_payload:
            return False
        return resolve_locator(page, Locator.model_validate(locator_payload)).count() == 0

    report = cleanup_business_objects(cfg.business_objects, execute, verify)
    artifacts.write_json("cleanup-report.json", report)
    artifacts.event(
        "business_cleanup_finished",
        status=report["status"], object_count=len(report["objects"]),
        manual_actions=report["manualActions"],
    )
    return report


def _capture_screenshot(
    page, artifacts: ArtifactManager, name: str, *, stop_loading: bool = False
) -> str | None:
    """Capture bounded visual evidence without letting a busy page stall the run."""
    image_path, relative = artifacts.screenshot_path(name)
    if stop_loading:
        try:
            page.locator("html").evaluate(
                "() => window.stop()",
                timeout=500,
            )
        except Exception:
            pass
    try:
        with screenshot_privacy_masks(page, artifacts.screenshot_mask_selectors) as privacy:
            is_cesium = (
                (urlparse(str(getattr(page, "url", ""))).hostname or "").lower()
                == "ion.cesium.com"
            )
            # Prefer Playwright's bounded screenshot API. CDP is only a
            # fallback because Page.captureScreenshot has no protocol timeout.
            # Keep the privacy mask context around both methods so fallback
            # captures cannot bypass redaction.
            # The GAEALaViC editor and run views can keep a web font load in
            # the document indefinitely while the compositor is already
            # paint-ready. Prefer the compositor path there so visual-agent
            # steps are not blocked by Playwright's font-settlement wait.
            if _prefer_cdp_screenshot(page):
                cdp_ok, cdp_reason = _capture_cdp_screenshot(page, image_path)
                artifacts.event(
                    "screenshot_cdp_primary",
                    screenshot=relative,
                    status="captured" if cdp_ok else "skipped",
                    reason=cdp_reason,
                )
                if cdp_ok:
                    artifacts.event("screenshot_privacy_applied", screenshot=relative, **privacy)
                    return relative if image_path.stat().st_size > 0 else None
            try:
                page.screenshot(
                    path=str(image_path),
                    full_page=False,
                    animations="allow" if is_cesium else "disabled",
                    timeout=SCREENSHOT_TIMEOUT_MS,
                )
            except Exception as playwright_error:
                # Retrying on the owning Playwright thread also avoids the
                # animation fast-forward path, which can stall a live SPA.
                try:
                    page.screenshot(
                        path=str(image_path), full_page=False,
                        animations="allow", timeout=SCREENSHOT_TIMEOUT_MS,
                    )
                    artifacts.event("screenshot_retry_captured", screenshot=relative,
                                    strategy="same_thread_animations_allow")
                    artifacts.event("screenshot_privacy_applied", screenshot=relative, **privacy)
                    return relative if image_path.stat().st_size > 0 else None
                except Exception:
                    pass
                cdp_ok, cdp_reason = _capture_cdp_screenshot(page, image_path)
                artifacts.event(
                    "screenshot_cdp_fallback",
                    screenshot=relative,
                    status="captured" if cdp_ok else "skipped",
                    reason=cdp_reason,
                )
                if not cdp_ok:
                    raise playwright_error
        artifacts.event("screenshot_privacy_applied", screenshot=relative, **privacy)
        return relative if image_path.stat().st_size > 0 else None
    except Exception as exc:
        artifacts.event(
            "screenshot_capture_failed",
            screenshot=relative,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
            fallback="playwright_then_cdp",
        )
        image_path.unlink(missing_ok=True)
        return None


def _prefer_cdp_screenshot(page) -> bool:
    """Use compositor capture first for dynamic GAEALaViC views."""
    url = str(getattr(page, "url", "") or "")
    return "192.168.31.218" in url and any(
        marker in url for marker in ("agentEditPage", "situationPage", "runPage")
    )


def _capture_cdp_screenshot(page, image_path: Path) -> tuple[bool, str]:
    """Capture a compositor screenshot with a hard host-side timeout.

    Playwright's screenshot API is the primary path.  CDP ``send`` does not
    expose a timeout and can remain blocked when Chromium's compositor is
    wedged, so run it on a daemon thread and abandon the attempt after a
    small bound.  The worker only returns bytes; it cannot race the fallback
    file write after the timeout.  The process-local lock prevents another
    fallback from starting while that abandoned worker is still in flight.
    """
    # Do not wait behind a stuck compositor call.  The caller records this
    # reason in the structured ``screenshot_cdp_fallback`` event.
    cdp_lock = _CDP_SCREENSHOT_LOCK
    if not cdp_lock.acquire(blocking=False):
        image_path.unlink(missing_ok=True)
        return False, "busy"

    results: queue.Queue[tuple[bool, bytes | None, str]] = queue.Queue(maxsize=1)

    def capture() -> None:
        client = None
        payload: tuple[bool, bytes | None, str] = (False, None, "worker_error")
        try:
            try:
                client = page.context.new_cdp_session(page)
                result = client.send(
                    "Page.captureScreenshot",
                    {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
                )
                encoded = result.get("data") if isinstance(result, dict) else None
                if not encoded:
                    payload = (False, None, "empty_response")
                else:
                    payload = (True, base64.b64decode(encoded, validate=True), "captured")
            except Exception as exc:
                payload = (False, None, type(exc).__name__)
        finally:
            if client is not None:
                try:
                    client.detach()
                except Exception:
                    pass
            try:
                results.put_nowait(payload)
            except queue.Full:
                pass
            finally:
                # Keep the lock held after a host-side timeout until the
                # protocol call actually returns. This is what bounds the
                # number of orphaned CDP workers to one.
                cdp_lock.release()

    try:
        Thread(target=capture, name="cdp-screenshot", daemon=True).start()
    except Exception:
        # Thread creation is unusual, but do not strand the process-wide guard
        # if the host rejects it.
        cdp_lock.release()
        image_path.unlink(missing_ok=True)
        return False, "thread_start_failed"
    try:
        success, data, reason = results.get(timeout=CDP_SCREENSHOT_TIMEOUT_MS / 1000)
    except queue.Empty:
        image_path.unlink(missing_ok=True)
        return False, "timeout"
    if not success or not data:
        image_path.unlink(missing_ok=True)
        return False, reason
    try:
        image_path.write_bytes(data)
    except OSError:
        image_path.unlink(missing_ok=True)
        return False, "write_failed"
    return image_path.stat().st_size > 0, "captured"


_ROUTE_TRANSITION_ACTIONS = {
    ActionType.NAVIGATE,
    ActionType.CLICK,
    ActionType.PRESS,
    ActionType.SELECT,
    ActionType.VISUAL_CLICK,
    ActionType.BRIDGE_CLICK,
}


def _should_defer_before_transition_wait(step: Step, previous_step: Step | None) -> bool:
    return (
        step.action == ActionType.WAIT_FOR
        and previous_step is not None
        and previous_step.action in _ROUTE_TRANSITION_ACTIONS
    )


def _should_defer_after_route_transition(
    step: Step,
    next_step: Step | None,
    before_url: str,
    after_url: str,
) -> bool:
    if next_step is None or next_step.action != ActionType.WAIT_FOR:
        return False
    return step.action in _ROUTE_TRANSITION_ACTIONS


def _link_deferred_after_evidence(
    steps: list[StepResult],
    settled_after: Any,
    screenshot: str,
) -> bool:
    """Attach a wait step's settled evidence to its deferred route transition."""
    if len(steps) < 2:
        return False
    previous = steps[-2]
    if previous.screenshot is not None or previous.status != Status.PASSED:
        return False
    steps[-2] = previous.model_copy(update={
        "screenshot": screenshot,
        "after": settled_after,
    })
    return True


def _commerce_preflight(
    step: Step,
    cfg: RunnerConfig,
    run_id: str,
    confirmed_by_human: bool,
    ledger: dict[str, ResourceLedgerEntry],
    artifacts: ArtifactManager,
    index: int,
    decisions: list[dict] | None = None,
) -> None:
    metadata = step.commerce
    if metadata is None:
        return
    if not cfg.commerce_enabled:
        raise SecurityError("计划包含电商动作元数据，但项目未启用电商安全配置")
    if metadata.ledger_operation == "register" and metadata.target_ref in ledger and ledger[metadata.target_ref].status != LedgerStatus.CLEANED:
        raise SecurityError("同一电商资源已登记且尚未清理，拒绝重复副作用")
    if metadata.ledger_operation == "cleanup" and metadata.target_ref not in ledger:
        raise SecurityError("清理动作引用的电商资源未在本次运行台账中登记")
    target = (
        BusinessReference.from_raw(metadata.target_kind, metadata.target_ref)
        if metadata.target_kind and metadata.target_ref else None
    )
    try:
        decision = evaluate_commerce_action(CommerceActionRequest(
            action=metadata.action,
            environment=cfg.commerce_environment,
            runId=f"E2E_{run_id}" if metadata.e2e_owned else run_id,
            accountRef=cfg.commerce_account_ref,
            target=target,
            beforeState=metadata.before_state,
            cleanupAction=metadata.cleanup_action,
            idempotencyKey=metadata.idempotency_key_ref,
            confirmedByHuman=confirmed_by_human,
            e2eOwned=metadata.e2e_owned,
            productionReversibleWriteAuthorized=cfg.commerce_production_reversible_write_authorized,
            sandboxDriver=cfg.commerce_sandbox_driver,
            fixedProductRef=cfg.commerce_fixed_product_ref,
            fixedAddressRef=cfg.commerce_fixed_address_ref,
            writtenAuthorizationRef=cfg.commerce_written_authorization_ref,
            automaticCancellationVerified=cfg.commerce_automatic_cancellation_verified,
        ))
        decision_record = {
            "index": index,
            "action": metadata.action.value,
            "allowed": decision.allowed,
            "riskLevel": decision.risk_level.value,
            "requiresConfirmation": decision.requires_confirmation,
            "reason": decision.reason,
            "missingControls": decision.missing_controls,
            "target": target.model_dump(mode="json") if target else None,
        }
        if decisions is not None:
            decisions.append(decision_record)
        artifacts.event("commerce_action_preflight", **decision_record)
        decision.enforce()
    except CommercePolicyError as exc:
        raise SecurityError(str(exc)) from exc


def _commerce_record_success(
    step: Step,
    run_id: str,
    ledger: dict[str, ResourceLedgerEntry],
    artifacts: ArtifactManager,
) -> None:
    metadata = step.commerce
    if metadata is None or not metadata.target_ref:
        return
    if metadata.ledger_operation == "register":
        ledger[metadata.target_ref] = ResourceLedgerEntry(
            runId=f"E2E_{run_id}",
            reference=BusinessReference.from_raw(metadata.target_kind, metadata.target_ref),
            createdBy=metadata.action,
            cleanupAction=metadata.cleanup_action or "",
        )
    elif metadata.ledger_operation == "cleanup":
        current = ledger[metadata.target_ref]
        ledger[metadata.target_ref] = current.model_copy(update={
            "status": LedgerStatus.CLEANED,
            "cleaned_at": _now().isoformat(),
        })
    else:
        return
    artifacts.write_json("commerce-resource-ledger.json", {
        "runId": run_id,
        "entries": [
            item.model_dump(mode="json", by_alias=True)
            for item in ledger.values()
        ],
    })


def _commerce_run_summary(
    cfg: RunnerConfig,
    decisions: list[dict],
    ledger: dict[str, ResourceLedgerEntry],
) -> dict | None:
    if not cfg.commerce_enabled:
        return None
    entries = [item.model_dump(mode="json", by_alias=True) for item in ledger.values()]
    pending = [
        {
            "reference": item.reference.model_dump(mode="json"),
            "status": item.status.value,
            "cleanupAction": item.cleanup_action,
        }
        for item in ledger.values()
        if item.status != LedgerStatus.CLEANED
    ]
    return {
        "environment": cfg.commerce_environment,
        "policyEvaluations": decisions,
        "ledgerEntries": entries,
        "pendingResources": pending,
        "zeroResidual": not pending,
    }


def _commerce_state_after_action(
    context,
    step: Step,
    cfg: RunnerConfig,
    run_id: str,
    base_url: str,
    policy: DomainPolicy,
    artifacts: ArtifactManager,
    index: int,
) -> dict | None:
    metadata = step.commerce
    if metadata is None or metadata.state_probe is None:
        return None
    evidence = poll_commerce_state(
        context.request,
        metadata.state_probe,
        base_url=base_url,
        run_id=f"E2E_{run_id}" if metadata.e2e_owned else run_id,
        target_ref=metadata.target_ref,
        policy=policy,
    )
    artifacts.write_json(f"step-{index}-commerce-state.json", evidence)
    artifacts.event("commerce_state_consistent", index=index, **evidence)
    return evidence


def _commerce_recovery_probe(context, step: Step, cfg: RunnerConfig, run_id: str, base_url: str, policy: DomainPolicy):
    metadata = step.commerce
    if metadata is None or metadata.state_probe is None:
        return None

    def probe() -> dict:
        return observe_commerce_state(
            context.request,
            metadata.state_probe,
            base_url=base_url,
            run_id=f"E2E_{run_id}" if metadata.e2e_owned else run_id,
            target_ref=metadata.target_ref,
            policy=policy,
        )

    return probe


def _restore_page_session(context, recovery_url: str, step: Step, policy: DomainPolicy, cfg: RunnerConfig):
    """Rebuild a crashed page while retaining the isolated browser context."""
    page = context.new_page()
    page.set_default_timeout(cfg.timeout_ms)
    page.set_default_navigation_timeout(cfg.timeout_ms)
    if recovery_url and recovery_url != "about:blank":
        policy.check_url(recovery_url)
        response = page.goto(recovery_url, wait_until="commit")
        if response is not None and response.status >= 400:
            raise HttpExecutionError(response.status, recovery_url)
    root = page.frame_locator(step.browser_target.frame_css) if step.browser_target.frame_css else page
    return page, root


_COMMERCE_WRITE_TERMS = (
    "add cart", "favorite", "follow", "claim coupon", "submit order", "checkout", "pay",
    "refund", "cancel order", "confirm receipt", "after sale", "review", "send message",
    "加入购物车", "收藏", "关注", "领券", "提交订单", "立即购买", "支付", "退款",
    "取消订单", "确认收货", "申请售后", "评价", "发送消息", "新增地址", "发票抬头",
)


def _require_commerce_metadata(step: Step, cfg: RunnerConfig) -> None:
    if not cfg.commerce_enabled or step.commerce is not None:
        return
    serialized = step.model_dump_json(exclude_none=True).lower()
    matched = next((term for term in _COMMERCE_WRITE_TERMS if term in serialized), None)
    if matched:
        raise SecurityError(f"电商写动作必须声明结构化 commerce 元数据：{matched}")


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
    if isinstance(exc, (CommerceStateError, AsyncStateError, ActionVerificationError)):
        return FailureCategory.BUSINESS_STATE
    if isinstance(exc, SecurityError):
        return FailureCategory.SECURITY
    if isinstance(exc, LocatorError):
        return FailureCategory.LOCATOR
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
        FailureCategory.MODEL: "模型服务调用失败，请根据已脱敏的具体错误检查模型配置或服务状态。",
        FailureCategory.MODEL_SERVICE: "模型网关暂时不可用，系统已在重试后停止后续 Agent 动作；请先重新运行模型能力探针，再开始真实网站测试。",
        FailureCategory.ASSERTION: "页面实际状态与预期不一致，可能是业务缺陷、测试数据或预期配置问题。",
        FailureCategory.BUSINESS_STATE: "页面动作已完成，但后台业务状态未达到一致或发生非法跃迁，请核对状态 API 与业务事件。",
        FailureCategory.UNKNOWN: "发生未分类异常，请结合错误消息、截图和 trace 人工复核。",
    }
    display_message = messages.get(category, messages[FailureCategory.UNKNOWN])
    if category == FailureCategory.MODEL and message.startswith("Agent 单步决策未通过安全 Schema 校验："):
        detail = message.split("：", 1)[1]
        display_message = f"AI 决策格式未通过安全校验：{detail}"
    return CauseHint(
        category=category,
        message=display_message,
        evidence=[f"步骤/断言 {index}", message[:300]],
        confidence="medium" if category != FailureCategory.UNKNOWN else "low",
    )


def _now() -> datetime:
    return datetime.now().astimezone()


def _validate_runtime_cesium_step(
    step: Step,
    index: int,
    owned_resources: tuple[tuple[str, str, str], ...],
) -> None:
    """执行每一步前再次验证，防止 Agent 或回放绕过入口校验。"""
    entries = [
        {"resourceId": resource_id, "name": name, "cleanupStatus": cleanup_status}
        for resource_id, name, cleanup_status in owned_resources
    ]
    try:
        validate_cesium_step(step, index, entries)
    except CesiumPolicyError as exc:
        raise SecurityError(str(exc)) from exc
