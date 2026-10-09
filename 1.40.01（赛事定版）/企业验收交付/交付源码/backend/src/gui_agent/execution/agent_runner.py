"""Observation-plan-action loop for dynamic AI exploration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import os
import re
import base64
from time import monotonic, sleep
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError

from ..playwright_runtime import launch_browser, playwright_runtime

from ..artifacts import ArtifactManager, build_evidence_package
from ..assertions.checks import check_assertion
from ..commerce import ResourceLedgerEntry
from ..commerce import evaluate_release_gate
from ..decision import (
    action_fingerprint,
    build_exploration_map,
    build_recovery_checkpoint,
    derive_recovery_contract,
    diff_observations,
    route_decision,
)
from ..domain.models import ActionType, ExecutionMode, Locator, RelativePosition, StabilityLevel, Step, TestPlan
from ..domain.results import (
    AssertionResult,
    FailureCategory,
    ModelCallRecord,
    RunResult,
    Status,
    StepResult,
)
from ..planning.ai_provider import (
    AIProviderConfigurationError,
    AIProviderError,
    AIProviderLocalContractError,
    AIProviderOutputError,
    AIProviderUnavailableError,
)
from ..planning.agent_planner import _is_navigation_target
from ..planning.task_authorization import derive_task_authorization, is_creation_step
from ..planning.visual_adapter import VisualSuggestion
from ..site_capabilities import resolve_site_capability_pack
from ..onboarding.session import playwright_storage_state, session_storage_init_script
from ..security.policy import (
    DomainPolicy,
    SecurityError,
    guard_playwright_route,
    guard_playwright_websocket,
    resolve_env_placeholder,
    temporary_login_navigation_hosts as _temporary_login_navigation_hosts,
)
from ..security.redaction import Redactor
from .compiler import compile_test
from .confirmation import confirmation_match
from .findings import build_findings
from .bridge_adapter import create_bridge_adapter
from .stability import (
    attach_rendering_evidence,
    attach_visual_delta_evidence,
    finalize_canvas_evidence,
    prepare_action,
)
from .time_budget import RunTimeBudget
from .observation import ObservationCollector, blocking_page_error
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
    _apply_completion_gate,
    _now,
    _require_commerce_metadata,
    _run_business_cleanup,
    _restore_page_session,
    _step_summary,
)
from .recovery import SideEffectOutcomeUnknown, execute_with_recovery
from .async_state import WebSocketEvidenceCollector
from .side_effects import confirmation_rule, evaluate_side_effect
from .verification import (
    ActionVerificationError,
    build_action_contract,
    target_service_not_implemented_from_verification,
    verify_action_result,
)
from .transition import stabilize_after_action
from .agent_evaluation import evaluate_agent_run


_WRITE_ACTIONS_REQUIRING_APPROVAL = {
    ActionType.FILL, ActionType.CLEAR, ActionType.SELECT, ActionType.CHECK, ActionType.UNCHECK,
    ActionType.PRESS, ActionType.UPLOAD, ActionType.UPLOAD_FILE, ActionType.COMPONENT,
    ActionType.BRIDGE_CLICK, ActionType.VISUAL_DRAW_POLYGON, ActionType.VISUAL_DRAW_RECTANGLE,
}

_READ_ONLY_QUERY_ACTIONS = {
    ActionType.FILL,
    ActionType.CLEAR,
    ActionType.SELECT,
    ActionType.CHECK,
    ActionType.UNCHECK,
    ActionType.PRESS,
}

_SESSION_ONLY_FORM_ACTIONS = {
    ActionType.FILL,
    ActionType.CLEAR,
    ActionType.SELECT,
    ActionType.CHECK,
    ActionType.UNCHECK,
}


@dataclass
class _ModelCallBudget:
    """Track model attempts, with zero meaning no call-count ceiling.

    Runs still remain bounded by the configured wall-clock, step, and
    no-progress guards.  A zero maximum therefore removes only the arbitrary
    per-run model-call cutoff; it does not make a run unbounded.
    """

    maximum: int
    attempts: int = 0

    @property
    def exhausted(self) -> bool:
        return self.maximum > 0 and self.attempts >= self.maximum

    def begin(self) -> int:
        if self.exhausted:
            raise RuntimeError("model call budget exhausted")
        self.attempts += 1
        return self.attempts


@dataclass
class _ModelRecoveryState:
    consecutive: int = 0
    total: int = 0

    def begin(self) -> tuple[int, int]:
        self.consecutive += 1
        self.total += 1
        return self.consecutive, self.total

    def decision_succeeded(self) -> None:
        self.consecutive = 0


def _build_model_recovery_checkpoint(
    *,
    run_id: str,
    executed_step_count: int,
    recovery_attempt: int,
    recovery_limit: int,
    current_url: str,
    page_state_key: str,
    screenshot: str | None,
    total_recovery_attempt: int | None = None,
) -> dict[str, object]:
    """Build a value-safe checkpoint for a failed model decision.

    The checkpoint deliberately contains no action payload, form values,
    cookies, or credentials. A model failure happens before a new browser
    action is accepted, so resuming must recapture the page and request a
    fresh decision instead of replaying anything.
    """
    checkpoint = {
        "schemaVersion": 1,
        "runId": run_id,
        "executedStepCount": executed_step_count,
        "modelRecoveryAttempt": recovery_attempt,
        "modelRecoveryLimit": recovery_limit,
        "currentUrl": current_url,
        "pageStateKey": page_state_key,
        "screenshot": screenshot,
        "automaticActionReplayAllowed": False,
        "resumePolicy": "recapture_current_page_then_request_fresh_decision",
    }
    if total_recovery_attempt is not None:
        checkpoint["totalModelRecoveryAttempt"] = total_recovery_attempt
    return checkpoint


def _emit_adaptive_route_shadow(
    artifacts: ArtifactManager,
    *,
    before,
    after,
    no_progress_count: int = 0,
    last_failure: FailureCategory | None = None,
) -> None:
    """Record P1 routing advice without changing mandatory Agent behavior."""
    diff = diff_observations(before, after)
    route = route_decision(
        diff,
        last_failure=last_failure,
        no_progress_count=no_progress_count,
        # A static canvas is not by itself a reason to call vision again on
        # every step. VISUAL_SURFACE in the observation diff handles changes.
        visual_surface_active=False,
    )
    artifacts.event(
        "adaptive_route_shadow",
        production_policy="mandatory_multimodal",
        proposed_route=route.route.value,
        route_reason=route.reason,
        visual_required=route.visual_required,
        contract_invalidated=route.contract_invalidated,
        change_kind=diff.primary.value,
        changes=[item.value for item in diff.changes],
        change_details=diff.details,
        before_page_key=diff.before_page_key,
        after_page_key=diff.after_page_key,
        before_signature=diff.before_signature,
        after_signature=diff.after_signature,
    )


def _emit_decision_route_gate(
    artifacts: ArtifactManager,
    *,
    before,
    after,
    no_progress_count: int = 0,
    last_failure: FailureCategory | None = None,
    contract_postcondition_pending: bool = False,
) -> object:
    """Audit and safety-gate the pre-decision route without claiming action reuse."""
    diff = diff_observations(before, after)
    route = route_decision(
        diff,
        has_active_contract=False,
        contract_matches_page=False,
        contract_postcondition_pending=contract_postcondition_pending,
        last_failure=last_failure,
        no_progress_count=no_progress_count,
        visual_surface_active=False,
    )
    artifacts.event(
        "decision_route_gate",
        safety_gate_enforced=True,
        production_policy="mandatory_multimodal",
        route=route.route.value,
        route_reason=route.reason,
        visual_required=route.visual_required,
        active_contract=False,
        contract_execution_enabled=False,
        adaptive_execution_enabled=False,
        model_call_required=True,
        form_continuation_check_pending=os.getenv("GUI_AGENT_FORM_FAST_PATH", "1") != "0",
        contract_postcondition_pending=contract_postcondition_pending,
        change_kind=diff.primary.value,
        changes=[item.value for item in diff.changes],
        before_page_key=diff.before_page_key,
        after_page_key=diff.after_page_key,
    )
    return route


def _is_session_only_form_staging(step: Step) -> bool:
    """Allow local form state changes until a real submit is requested."""
    if step.value_from_secret is not None or step.action_category is not None:
        return False
    if step.effect_level is None or step.effect_level.value not in {"read_only", "session_only"}:
        return False
    if step.action in _SESSION_ONLY_FORM_ACTIONS:
        return True
    if step.action == ActionType.COMPONENT and step.component is not None:
        return step.component.kind in {
            "cascade_select", "cascader", "searchable_select", "date_time_range", "tab", "local_scroll",
        }
    return False


def _external_model_call_count(records: list[ModelCallRecord]) -> int:
    return sum(record.protocol != "local" for record in records)


def _stable_replay_required(site_pack, required_site_stages: list[str]) -> bool:
    """Choose a completion proof that the selected capability can provide.

    Vertical packs may require stable replay or independently verified terminal
    stages. A generic unfamiliar site has neither a replay contract nor a
    site-specific stage model, so it must rely on explicit final assertions
    instead of being rejected solely because stable replay is unavailable.
    """
    if required_site_stages and site_pack.supports_terminal_state_completion:
        return False
    return bool(site_pack.supports_auto_stable_replay)


def _model_failure_disposition(exc: AIProviderError) -> tuple[str, bool]:
    """Return the stable runner outcome and whether a fresh decision is safe."""
    if isinstance(exc, AIProviderConfigurationError):
        return "model_configuration_error", False
    if isinstance(exc, AIProviderLocalContractError):
        return "model_local_contract_error", False
    if isinstance(exc, AIProviderUnavailableError):
        return "model_service_unavailable", True
    if isinstance(exc, AIProviderOutputError):
        return "model_output_recovery_exhausted", True
    return "model_error", False


def _terminal_target_service_failure(
    verification_evidence: dict | None,
) -> dict | None:
    """Return a definitive target-backend failure, never a replay route."""
    evidence = target_service_not_implemented_from_verification(verification_evidence)
    if (
        evidence is None
        or evidence.get("automaticReplayAllowed") is not False
        or evidence.get("clickDispatched") is not True
    ):
        return None
    return evidence


def _refresh_visual_step(step: Step, suggestion: VisualSuggestion) -> Step:
    """Apply a fresh screenshot grounding result to an immutable Pydantic Step."""

    return step.model_copy(
        update={
            "relative_position": RelativePosition(
                xRatio=suggestion.x_ratio,
                yRatio=suggestion.y_ratio,
            ),
            "relative_end_position": (
                RelativePosition(
                    xRatio=suggestion.end_x_ratio,
                    yRatio=suggestion.end_y_ratio,
                )
                if suggestion.end_x_ratio is not None
                and suggestion.end_y_ratio is not None
                else None
            ),
            "visual_points": [
                RelativePosition(xRatio=point.x_ratio, yRatio=point.y_ratio)
                for point in suggestion.points
            ],
            "zoom_delta": suggestion.zoom_delta,
            "gesture_finish": suggestion.gesture_finish,
            "visual_expected_change": suggestion.expected_change,
        }
    )


def _ground_cesium_start_request_to_locator(
    page,
    request,
    *,
    base_step: Step | None = None,
) -> tuple[Step | None, dict[str, object]]:
    """Resolve the GAEALaViC Run-mode Start control without coordinates.

    The visual adapter is still authoritative for deciding that a visual
    action is needed.  Once the request reaches the live page, however, the
    Start control has a stable accessible name.  Grounding it through the
    current DOM avoids a second screenshot/model round-trip (which previously
    prevented dispatch when the model gateway was unavailable).  Every guard
    is deliberately narrow so this cannot turn an arbitrary visual click into
    a write action on another route or simulation state.
    """

    evidence: dict[str, object] = {
        "eligible": False,
        "reason": "not_checked",
        "route": "",
        "simulationStatus": "",
        "candidateCount": 0,
        "visibleCandidateCount": 0,
        "enabledCandidateCount": 0,
    }
    try:
        current_url = str(getattr(page, "url", "") or "")
        parsed = urlparse(current_url)
        fragment_route, separator, fragment_query = (parsed.fragment or "").partition("?")
        params = parse_qs(fragment_query if separator else "", keep_blank_values=True)
        route = fragment_route.rstrip("/") or "/"
        mode = str((params.get("type") or [""])[0]).casefold()
        status = str((params.get("simulationStatus") or [""])[0]).casefold()
        evidence.update({"route": route, "simulationStatus": status})
        if (parsed.hostname or "").casefold() != "192.168.31.218":
            evidence["reason"] = "host_not_target"
            return None, evidence
        if route != "/situationPage" or mode != "run" or status != "unstart":
            evidence["reason"] = "run_mode_unstart_guard_failed"
            return None, evidence

        preferred_action = str(getattr(request, "preferred_action", "") or "").casefold()
        request_text = " ".join(
            str(getattr(request, field, "") or "")
            for field in ("target", "trigger_reason")
        ).casefold()
        start_markers = ("启动", "start simulation", "startsimulation", "launch simulation")
        if preferred_action != "click" or not any(marker in request_text for marker in start_markers):
            evidence["reason"] = "request_is_not_start_click"
            return None, evidence

        buttons = page.get_by_role("button", name="启动", exact=True)
        total = int(buttons.count())
        evidence["candidateCount"] = total
        # A very large match set is an ambiguous page, not a safe fallback.
        if total == 0 or total > 20:
            evidence["reason"] = "start_button_not_unique"
            return None, evidence
        visible_indices: list[int] = []
        enabled_indices: list[int] = []
        for item_index in range(total):
            candidate = buttons.nth(item_index)
            try:
                visible = bool(candidate.is_visible())
            except Exception:
                visible = False
            if visible:
                visible_indices.append(item_index)
                try:
                    if bool(candidate.is_enabled()):
                        enabled_indices.append(item_index)
                except Exception:
                    pass
        evidence.update({
            "visibleCandidateCount": len(visible_indices),
            "enabledCandidateCount": len(enabled_indices),
        })
        if len(visible_indices) != 1 or len(enabled_indices) != 1 or visible_indices != enabled_indices:
            evidence["reason"] = "start_button_not_unique_visible_enabled"
            return None, evidence

        evidence.update({
            "eligible": True,
            "reason": "unique_visible_enabled_start_button",
            "locator": "role=button[name=启动, exact=true]",
        })
        updates = {
            "action": ActionType.CLICK,
            "locator": Locator(role="button", name="启动", exact=True),
            "description": "在运行模式确定性点击“启动”按钮",
            "execution_mode": ExecutionMode.LOCATOR,
            "stability_level": StabilityLevel.A,
            "stability_reason": (
                "当前 situationPage 运行模式为 Unstart，且 DOM 中存在唯一可见启用的启动按钮"
            ),
            # The action is no longer dispatched by coordinates after
            # grounding. Clear the visual-only execution marker so the
            # resulting Step remains valid under the domain model validator.
            "computer_use_triggered": False,
            "computer_use_reason": None,
            "visual_target": str(getattr(request, "target", "") or "启动"),
            "visual_expected_change": str(
                getattr(request, "expected_change", "") or "仿真状态变为 Running"
            ),
        }
        # Preserve effect/confirmation/business metadata from the planner's
        # original step when grounding a visual request. Only the locator and
        # execution representation change; the safety contract must not.
        grounded = (
            Step.model_validate({**base_step.model_dump(mode="python"), **updates})
            if base_step is not None
            else Step(**updates)
        )
        return grounded, evidence
    except Exception as exc:
        evidence["reason"] = "dom_grounding_error"
        evidence["error"] = str(exc)[:300]
        return None, evidence


def _ground_cesium_speed_request_to_locator(
    page,
    request,
    *,
    base_step: Step | None = None,
) -> tuple[Step | None, dict[str, object]]:
    """Ground an explicit x50 request through the live native combobox.

    The Cesium toolbar exposes the simulation speed as a native ``select``.
    Vision can reliably identify the menu, but a viewport coordinate at the
    bottom option is not a reliable selection proof: the menu may close while
    the selected value remains x1.  Convert only an explicit x50 selection
    request, on the run page, when the current DOM exposes one visible enabled
    speed combobox.  The normal SELECT executor then records the selected value
    as an independent postcondition.
    """

    evidence: dict[str, object] = {
        "eligible": False,
        "reason": "not_checked",
        "route": "",
        "simulationStatus": "",
        "candidateCount": 0,
        "visibleCandidateCount": 0,
        "enabledCandidateCount": 0,
    }
    try:
        current_url = str(getattr(page, "url", "") or "")
        parsed = urlparse(current_url)
        fragment_route, separator, fragment_query = (parsed.fragment or "").partition("?")
        params = parse_qs(fragment_query if separator else "", keep_blank_values=True)
        route = fragment_route.rstrip("/") or "/"
        mode = str((params.get("type") or [""])[0]).casefold()
        status = str((params.get("simulationStatus") or [""])[0]).casefold()
        evidence.update({"route": route, "simulationStatus": status})
        if (parsed.hostname or "").casefold() != "192.168.31.218":
            evidence["reason"] = "host_not_target"
            return None, evidence
        if route != "/situationPage" or mode != "run":
            evidence["reason"] = "run_mode_guard_failed"
            return None, evidence
        request_text = " ".join(
            str(getattr(request, field, "") or "")
            for field in ("target", "trigger_reason", "expected_change")
        ).casefold()
        if str(getattr(request, "preferred_action", "") or "").casefold() != "click":
            evidence["reason"] = "request_is_not_click"
            return None, evidence
        if not any(marker in request_text for marker in ("x50", "50倍", "50x")):
            evidence["reason"] = "request_is_not_x50_selection"
            return None, evidence

        controls = page.get_by_role("combobox", name="仿真倍速调整", exact=True)
        total = int(controls.count())
        evidence["candidateCount"] = total
        if total == 0 or total > 5:
            evidence["reason"] = "speed_combobox_not_unique"
            return None, evidence
        visible_indices: list[int] = []
        enabled_indices: list[int] = []
        for item_index in range(total):
            candidate = controls.nth(item_index)
            try:
                visible = bool(candidate.is_visible())
            except Exception:
                visible = False
            if visible:
                visible_indices.append(item_index)
                try:
                    if bool(candidate.is_enabled()):
                        enabled_indices.append(item_index)
                except Exception:
                    pass
        evidence.update({
            "visibleCandidateCount": len(visible_indices),
            "enabledCandidateCount": len(enabled_indices),
        })
        if len(visible_indices) != 1 or visible_indices != enabled_indices:
            evidence["reason"] = "speed_combobox_not_unique_visible_enabled"
            return None, evidence

        evidence.update({
            "eligible": True,
            "reason": "unique_visible_enabled_speed_combobox",
            "locator": "role=combobox[name=仿真倍速调整, exact=true]",
            "selectedValue": "x50",
        })
        updates = {
            "action": ActionType.SELECT,
            "locator": Locator(role="combobox", name="仿真倍速调整", exact=True),
            "value": "x50",
            "description": "使用仿真倍速选择器选择 x50",
            "execution_mode": ExecutionMode.LOCATOR,
            "stability_level": StabilityLevel.A,
            "stability_reason": "当前运行页面存在唯一可见启用的仿真倍速选择器",
            "computer_use_triggered": False,
            "computer_use_reason": None,
            "visual_target": str(getattr(request, "target", "") or "x50"),
            "visual_expected_change": str(
                getattr(request, "expected_change", "") or "倍率控件显示 x50"
            ),
        }
        grounded = (
            Step.model_validate({**base_step.model_dump(mode="python"), **updates})
            if base_step is not None
            else Step(**updates)
        )
        return grounded, evidence
    except Exception as exc:
        evidence["reason"] = "dom_grounding_error"
        evidence["error"] = str(exc)[:300]
        return None, evidence


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
        and step.action in _READ_ONLY_QUERY_ACTIONS
        and step.effect_kind == "browse_search_filter_sort"
        and step.effect_level is not None
        and step.effect_level.value in {"read_only", "session_only"}
        and step.value_from_secret is None
        and step.action_category is None
    ):
        return None
    if configured_mode == "ask" and _is_session_only_form_staging(step):
        return None
    if configured_mode == "ask" and step.action in _WRITE_ACTIONS_REQUIRING_APPROVAL:
        return "approval-mode:write-action"
    return None


def _should_replan_after_wait_failure(step: Step, category: FailureCategory) -> bool:
    return step.action == ActionType.WAIT_FOR and category in {
        FailureCategory.LOCATOR,
        FailureCategory.TIMEOUT,
    }


def _should_replan_after_read_failure(
    step: Step,
    category: FailureCategory,
    *,
    stability_evidence: dict | None = None,
) -> bool:
    if _should_replan_after_wait_failure(step, category):
        return True
    return step.action == ActionType.NAVIGATE and category in {
        FailureCategory.LOCATOR,
        FailureCategory.NAVIGATION,
        FailureCategory.TIMEOUT,
        FailureCategory.UNKNOWN,
    }


def _should_replan_after_locator_failure(
    step: Step,
    category: FailureCategory,
    stability_evidence: dict | None,
    error_message: str = "",
    side_effect_evidence: dict | None = None,
    failure_phase: str = "unknown",
) -> bool:
    """Return recoverable failures to the Agent with a fresh observation.

    Every pre-action failure is safe because no browser action was dispatched.
    During execution or verification, only non-persistent actions can return to
    the Agent. Possible creates and other side effects remain non-replayable.
    """
    if failure_phase == "pre_action":
        return category in {
            FailureCategory.LOCATOR,
            FailureCategory.TIMEOUT,
            FailureCategory.UNKNOWN,
            FailureCategory.BUSINESS_STATE,
        }
    if is_creation_step(step) or step.action_category is not None or side_effect_evidence is not None:
        return False
    if category in {FailureCategory.BUSINESS_STATE, FailureCategory.UNKNOWN}:
        return step.effect_level is None or step.effect_level.value in {
            "read_only", "session_only", "isolated_local_write",
        }
    if category not in {FailureCategory.LOCATOR, FailureCategory.TIMEOUT}:
        return False
    if stability_evidence is not None and stability_evidence.get("passed") is False:
        return True
    if step.action not in {ActionType.CLICK, ActionType.HOVER}:
        return False
    if step.action_category is not None or side_effect_evidence is not None:
        return False
    message = error_message.lower()
    return any(marker in message for marker in (
        "intercepts pointer events",
        "does not receive pointer events",
        "element is not attached",
        "element was detached",
        "element is outside of the viewport",
    ))


def _show_human_takeover_window(context, page) -> None:
    """Restore and foreground Chromium before waiting for user interaction."""
    page.bring_to_front()
    session = None
    try:
        session = context.new_cdp_session(page)
        window = session.send("Browser.getWindowForTarget")
        window_id = window.get("windowId") if isinstance(window, dict) else None
        if window_id is not None:
            session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "normal"}},
            )
    except Exception:
        # WebKit/Firefox and some remote Chromium transports do not expose this CDP API.
        pass
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass


def _is_interactive_login_page(observation) -> bool:
    """Require a login route/title AND visible form evidence, not a header link."""
    parsed = urlparse(observation.url)
    route = parsed.path + "/" + parsed.fragment.split("?", 1)[0]
    login_route = re.search(r"(?:^|[/#])(?:login|signin|sign-in|sign_in)(?:[/?.#]|$)", route, re.I)
    title = re.match(r"^\s*(?:sign\s*in|log\s*in|登录|用户登录|账号登录)(?:\b|\s|[|｜-]|$)", observation.title, re.I)
    facts = "\n".join([observation.accessibility_summary, *observation.dom_summary]).lower()
    form = any(token in facts for token in (
        "textbox", "input", "password", "username", "email", "二维码", "扫码", "验证码", "密码",
    ))
    return bool((login_route or title) and form)


def _recover_blocked_login_navigation(page, policy, login_url: str) -> str | None:
    """Restore the last trusted login URL after Chromium displays its error page."""
    if urlparse(page.url).scheme in {"http", "https"}:
        policy.check_url(page.url)
        return None
    rejection = policy.consume_rejection()
    policy.check_url(login_url)
    page.goto(login_url, wait_until="domcontentloaded")
    return rejection or "登录跳转被浏览器或网络策略中断"


def _wait_for_manual_login(context, page, observation, cfg, collector, artifacts, runtime_budget, policy):
    """Pause in the same visible context; never collect credentials or trust a click as proof."""
    if cfg.clarification_callback is None:
        raise SecurityError("登录接管通道不可用，请在 GUI 中重新启动测试。")
    attempt = 0
    login_url = observation.url
    temporary_hosts = _temporary_login_navigation_hosts(login_url)
    artifacts.event(
        "manual_login_navigation_scope_opened",
        temporary_hosts=list(temporary_hosts),
        scope="manual_login_only",
    )
    try:
        with runtime_budget.excluded_wait("manual_login"), policy.allow_temporary_navigation_hosts(temporary_hosts):
            while _is_interactive_login_page(observation):
                if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                    return None
                if not cfg.headless:
                    _show_human_takeover_window(context, page)
                artifacts.event("human_takeover_window_shown", reason="login_required")
                question = (
                    "【需要手动登录】请在下方登录交互窗口中完成登录，再点击“我已登录，检查并继续”。"
                    "不要在测试助手中提供密码或验证码。等待登录不计入任务执行时限。"
                )
                if temporary_hosts:
                    question += f"\n本次登录临时允许认证域名：{', '.join(temporary_hosts)}；登录结束后自动收回。"
                if attempt:
                    question += "\n刚才检查仍是登录页面，请完成登录后再次继续。"
                cfg.manual_login_surface["handle"] = lambda command: _manual_login_command(
                    page, policy, command, recovery_url=login_url
                )
                # clarification_callback blocks on IPC in process/container mode.
                # Expose a same-thread event pump so browser OAuth navigation and
                # route callbacks continue to run throughout the human wait.
                cfg.manual_login_surface["pump"] = lambda: page.wait_for_timeout(50)
                try:
                    answer = cfg.clarification_callback(question, len(cfg.clarification_history) + 1)
                finally:
                    cfg.manual_login_surface.clear()
                if not answer or (cfg.cancel_event is not None and cfg.cancel_event.is_set()):
                    return None
                # Reading through Playwright pumps pending navigation events after IPC wait.
                page.wait_for_timeout(100)
                recovered = _recover_blocked_login_navigation(page, policy, login_url)
                if recovered:
                    artifacts.event("manual_login_navigation_recovered", reason=recovered)
                attempt += 1
                observation = collector.capture(
                    _capture_screenshot(page, artifacts, f"login-resume-{attempt}")
                )
                artifacts.event("manual_login_rechecked", still_login=_is_interactive_login_page(observation))
    finally:
        artifacts.event(
            "manual_login_navigation_scope_closed",
            temporary_hosts=list(temporary_hosts),
            business_hosts_restored=True,
        )
    artifacts.event("manual_login_resumed", fresh_observation=True, credentials_collected=False)
    return observation


def _manual_login_command(page, policy, command: dict, *, recovery_url: str | None = None) -> dict:
    """Narrow user-input relay while paused; no arbitrary code, selectors or URLs."""
    recovered = None
    if recovery_url:
        recovered = _recover_blocked_login_navigation(page, policy, recovery_url)
    else:
        policy.check_url(page.url)
    kind = command.get("kind")
    if kind == "frame":
        # In-memory only: these pixels are never an artifact or model input.
        result = {"image": base64.b64encode(page.screenshot(type="jpeg", quality=70, timeout=5000)).decode("ascii")}
        if recovered:
            result["notice"] = "刚才的登录跳转被安全策略拦截，已返回登录页面。"
        return result
    if kind == "click":
        size = page.viewport_size or {"width": 1440, "height": 960}
        x, y = float(command["x"]), float(command["y"])
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("登录点击坐标无效")
        page.mouse.click(x * size["width"], y * size["height"])
    elif kind == "text":
        value = command.get("text", "")
        if not isinstance(value, str) or not 0 < len(value) <= 2048:
            raise ValueError("登录输入长度无效")
        page.keyboard.insert_text(value)
    elif kind == "key":
        key = command.get("key")
        if key not in {"Tab", "Shift+Tab", "Enter", "Backspace", "Delete", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "Control+A"}:
            raise ValueError("不支持该登录按键")
        page.keyboard.press(key)
    elif kind == "scroll":
        page.mouse.wheel(0, max(-800, min(800, float(command.get("dy", 0)))))
    else:
        raise ValueError("不支持的登录操作")
    return {"ok": True}


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
    site_pack = resolve_site_capability_pack(base_url)
    agent_scenario = getattr(cfg.agent_planner, "scenario", None)
    task_authorization = derive_task_authorization(agent_scenario)
    policy = DomainPolicy(
        base_url,
        list(cfg.allowed_hosts),
        allow_private_network=cfg.allow_private_network,
    )
    policy.check_url(base_url)
    overall = Status.RUNNING
    completion_reason = "agent_running"
    no_progress_count = 0
    previous_decision_observation = None
    last_recoverable_failure: FailureCategory | None = None
    runtime_budget = RunTimeBudget(cfg.max_duration_seconds)
    max_steps = cfg.max_steps or 50
    commerce_ledger: dict[str, ResourceLedgerEntry] = {}
    commerce_decisions: list[dict] = []
    cleanup_report: dict | None = None
    effective_assertions = list(plan.assertions)
    stable_replay: dict | None = None
    multimodal_decision_count = 0
    model_budget = _ModelCallBudget(maximum=max(0, int(cfg.max_model_calls)))
    model_recovery = _ModelRecoveryState()
    planner_uses_external_model = bool(
        getattr(cfg.agent_planner, "uses_external_model", True)
    )

    def on_rendering_pause(phase: str, reason: str, duration: float) -> None:
        artifacts.event(
            "rendering_wait_" + phase,
            reason=reason,
            duration_seconds=round(duration, 3),
            **runtime_budget.snapshot(),
        )
        emit(Status.RUNNING)

    runtime_budget.rendering_pause_callback = on_rendering_pause

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
            "model_calls": model_budget.attempts,
            "successful_model_calls": _external_model_call_count(model_records),
            "model_recovery_attempts": model_recovery.total,
            "input_tokens": sum(item.input_tokens for item in model_records),
            "output_tokens": sum(item.output_tokens for item in model_records),
            "estimated_cost": round(sum(item for item in costs if item is not None), 8) if costs and all(item is not None for item in costs) else None,
            "model_call_records": [item.model_dump(mode="json") for item in model_records],
            "confirmation_history": list(cfg.confirmation_history),
            "clarification_history": list(cfg.clarification_history),
            "result_classification": "agent_running",
            "model_data_authorization": cfg.model_data_authorization,
            "decision_policy": cfg.decision_policy,
            **runtime_budget.snapshot(),
        })

    artifacts.event("run_started", run_id=run_id, plan_name=plan.name, role=plan.role, mode="agent")
    if getattr(cfg.agent_planner, "multimodal_required", False):
        authorization = cfg.model_data_authorization or {}
        if authorization.get("allowScreenshots") is not True:
            raise SecurityError(
                "Primary multimodal Agent requires current-site screenshot authorization"
            )
        if cfg.visual_adapter is None:
            raise SecurityError(
                "Primary multimodal Agent requires the visual execution adapter"
            )
        artifacts.event(
            "multimodal_agent_required",
            screenshot_authorized=True,
            silent_dom_fallback_allowed=False,
        )
    artifacts.event("task_authorization_bound", **task_authorization.as_context())
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
        if plan.steps and plan.steps[0].action == ActionType.NAVIGATE:
            step = plan.steps[0]
            index = 1
            if cfg.cesium_policy_enabled:
                from .runner import _validate_runtime_cesium_step
                _validate_runtime_cesium_step(step, index, cfg.cesium_owned_resources)
            step_started = _now()
            summary = _step_summary(step, redactor)
            artifacts.event(
                "step_started",
                index=index,
                action=step.action.value,
                target=summary,
                source="deterministic_agent_bootstrap",
            )
            _execute_step(
                page,
                step,
                base_url,
                policy,
                redactor,
                environment_variables=environment_variables,
                secret_refs=secret_refs,
                file_assets=dict(cfg.file_assets),
                artifacts=artifacts,
                async_state_machines=cfg.async_state_machines,
                component_adapters=cfg.component_adapters,
                test_files=cfg.test_files,
                timeout_ms=cfg.timeout_ms,
                run_budget=runtime_budget,
            )
            observation = collector.capture(
                _capture_screenshot(page, artifacts, "step-1-after")
            )
            _emit_adaptive_route_shadow(
                artifacts,
                before=None,
                after=observation,
            )
            steps.append(StepResult(
                index=index,
                action=step.action.value,
                description=step.description,
                target_summary=summary,
                status=Status.PASSED,
                started_at=step_started,
                ended_at=_now(),
                screenshot=observation.screenshot,
                execution_mode=step.execution_mode.value,
                stability_level=step.stability_level.value,
                stability_reason=step.stability_reason,
                after=observation,
                planner_reason="确定性打开目标网站后再交给 AI",
                progress_assessment="progress",
            ))
            executed_steps.append(step)
            artifacts.event(
                "step_passed",
                index=index,
                progress="progress",
                source="deterministic_agent_bootstrap",
            )
            emit(Status.RUNNING)
        else:
            observation = collector.capture(
                _capture_screenshot(page, artifacts, "agent-observation-0")
            )
            _emit_adaptive_route_shadow(
                artifacts,
                before=None,
                after=observation,
            )

        def recover_model_failure(exc: AIProviderError, *, source: str) -> bool:
            """Recover only errors for which a fresh model decision is safe.

            No browser action is retained or replayed. A successful return
            always means a new screenshot and semantic observation were saved.
            """
            nonlocal observation
            nonlocal overall, completion_reason
            message = redactor.scrub(str(exc))
            failure_reason, recoverable = _model_failure_disposition(exc)
            if failure_reason == "model_configuration_error":
                overall = Status.INCOMPLETE
                completion_reason = failure_reason
                hints.append(_cause_hint(
                    FailureCategory.MODEL,
                    model_budget.attempts,
                    message,
                ))
                artifacts.event(
                    "model_configuration_error",
                    source=source,
                    error=message,
                    retryable=False,
                    website_bug=False,
                )
                return False
            if failure_reason == "model_local_contract_error":
                overall = Status.SYSTEM_ERROR
                completion_reason = failure_reason
                hints.append(_cause_hint(
                    FailureCategory.MODEL,
                    model_budget.attempts,
                    message,
                ))
                artifacts.event(
                    "model_local_contract_error",
                    source=source,
                    error=message,
                    retryable=False,
                    website_bug=False,
                )
                return False

            if not recoverable:
                overall = Status.SYSTEM_ERROR
                completion_reason = failure_reason
                hints.append(_cause_hint(
                    FailureCategory.MODEL,
                    model_budget.attempts,
                    message,
                ))
                artifacts.event(
                    "model_call_failed",
                    source=source,
                    error=message,
                    retryable=False,
                    website_bug=False,
                )
                return False

            unavailable = isinstance(exc, AIProviderUnavailableError)
            category = (
                FailureCategory.MODEL_SERVICE if unavailable else FailureCategory.MODEL
            )
            hints.append(_cause_hint(category, model_budget.attempts, message))
            recovery_limit = max(0, int(cfg.model_recovery_attempts))
            recovery_number = model_recovery.consecutive + 1
            total_recovery_number = model_recovery.total + 1
            backoff_values = tuple(cfg.model_recovery_backoff_seconds or ())
            backoff_seconds = (
                float(backoff_values[min(recovery_number - 1, len(backoff_values) - 1)])
                if unavailable and backoff_values else 0.0
            )
            elapsed = runtime_budget.elapsed_seconds()
            remaining = (
                float(cfg.max_duration_seconds) - elapsed
                if cfg.max_duration_seconds is not None else None
            )
            enough_time = remaining is None or remaining > backoff_seconds
            artifacts.event(
                "model_service_unavailable" if unavailable else "model_output_invalid",
                source=source,
                error=message,
                status_code=getattr(exc, "status_code", None),
                provider_attempt_count=getattr(exc, "attempts", 1),
                retryable=True,
                website_bug=False,
            )
            if recovery_number > recovery_limit or not enough_time:
                overall = Status.INCOMPLETE
                completion_reason = failure_reason
                artifacts.event(
                    "model_recovery_exhausted",
                    source=source,
                    recovery_attempt=model_recovery.consecutive,
                    total_recovery_attempt=model_recovery.total,
                    recovery_limit=recovery_limit,
                    remaining_seconds=remaining,
                    required_backoff_seconds=backoff_seconds,
                    reason=(
                        "recovery_limit_reached"
                        if recovery_number > recovery_limit
                        else "run_time_budget_insufficient"
                    ),
                )
                return False

            actual_recovery_number, actual_total_recovery_number = model_recovery.begin()
            assert actual_recovery_number == recovery_number
            assert actual_total_recovery_number == total_recovery_number
            recovery_before = observation
            page_state_key = derive_recovery_contract(observation, steps).page_state_key
            checkpoint = _build_model_recovery_checkpoint(
                run_id=run_id,
                executed_step_count=len(executed_steps),
                recovery_attempt=model_recovery.consecutive,
                recovery_limit=recovery_limit,
                current_url=redactor.scrub(observation.url or page.url),
                page_state_key=page_state_key,
                screenshot=observation.screenshot,
                total_recovery_attempt=model_recovery.total,
            )
            checkpoint_path = artifacts.write_json(
                f"checkpoints/model-recovery-{model_recovery.total}.json",
                checkpoint,
            )
            artifacts.event(
                "model_recovery_waiting",
                source=source,
                error=message,
                status_code=getattr(exc, "status_code", None),
                provider_attempt_count=getattr(exc, "attempts", 1),
                recovery_attempt=model_recovery.consecutive,
                total_recovery_attempt=model_recovery.total,
                recovery_limit=recovery_limit,
                backoff_seconds=backoff_seconds,
                checkpoint=checkpoint_path,
                automatic_action_replay_allowed=False,
            )
            completion_reason = "model_recovery_waiting"
            emit(Status.RUNNING)

            wait_started = monotonic()
            while monotonic() - wait_started < backoff_seconds:
                if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                    overall = Status.CANCELLED
                    completion_reason = "cancelled_by_user"
                    return False
                if (
                    cfg.max_duration_seconds is not None
                    and runtime_budget.exceeded()
                ):
                    overall = Status.INCOMPLETE
                    completion_reason = "time_limit_exceeded"
                    return False
                sleep(min(0.25, backoff_seconds - (monotonic() - wait_started)))

            try:
                observation = collector.capture(
                    _capture_screenshot(
                        page,
                        artifacts,
                        f"model-recovery-{model_recovery.total}",
                    )
                )
                recovery_diff = diff_observations(recovery_before, observation)
                _emit_adaptive_route_shadow(
                    artifacts,
                    before=recovery_before,
                    after=observation,
                    last_failure=category,
                )
            except Exception as recapture_error:
                overall = Status.INCOMPLETE
                completion_reason = "model_recovery_recapture_failed"
                artifacts.event(
                    "model_recovery_recapture_failed",
                    recovery_attempt=model_recovery.consecutive,
                    total_recovery_attempt=model_recovery.total,
                    error=redactor.scrub(str(recapture_error)),
                )
                return False
            completion_reason = "agent_running"
            artifacts.event(
                "model_recovery_resumed",
                source=source,
                recovery_attempt=model_recovery.consecutive,
                total_recovery_attempt=model_recovery.total,
                screenshot=observation.screenshot,
                page_state_key=derive_recovery_contract(observation, steps).page_state_key,
                page_change=recovery_diff.primary.value,
                semantic_signature=(
                    observation.semantic_summary.signature
                    if observation.semantic_summary is not None else ""
                ),
                fresh_agent_decision_required=True,
                automatic_action_replay_allowed=False,
            )
            emit(Status.RUNNING)
            return True

        try:
            while len(executed_steps) < max_steps:
                if cfg.cancel_event is not None and cfg.cancel_event.is_set():
                    overall = Status.CANCELLED
                    completion_reason = "cancelled_by_user"
                    break
                if _is_interactive_login_page(observation) and cfg.clarification_callback is not None:
                    observation = _wait_for_manual_login(
                        context, page, observation, cfg, collector, artifacts, runtime_budget, policy
                    )
                    if observation is None:
                        overall = Status.CANCELLED
                        completion_reason = "manual_login_cancelled"
                        break
                    # A user may navigate while taking over. Invalidate old page contracts.
                    previous_decision_observation = None
                    no_progress_count = 0
                    emit(Status.RUNNING)
                target_error = blocking_page_error(observation)
                if target_error:
                    failed_step = steps[-1].index if steps else None
                    overall = Status.ISSUES_FOUND
                    completion_reason = "target_application_runtime_error"
                    hints.append(_cause_hint(
                        FailureCategory.BUSINESS_STATE,
                        failed_step or 0,
                        target_error,
                    ))
                    artifacts.event(
                        "target_application_runtime_error",
                        after_step=failed_step,
                        error=redactor.scrub(target_error),
                        screenshot=observation.screenshot,
                        website_bug=True,
                        further_write_actions_blocked=True,
                    )
                    break
                if runtime_budget.exceeded():
                    overall = Status.INCOMPLETE
                    completion_reason = "time_limit_exceeded"
                    break
                recovery_contract = derive_recovery_contract(observation, steps)
                decision_route = _emit_decision_route_gate(
                    artifacts,
                    before=previous_decision_observation,
                    after=observation,
                    no_progress_count=no_progress_count,
                    last_failure=last_recoverable_failure,
                    contract_postcondition_pending=any(
                        item.progress_assessment == "pending_business_verification"
                        for item in steps
                    ),
                )
                if decision_route.route.value == "block":
                    overall = Status.INCOMPLETE
                    completion_reason = "adaptive_route_blocked"
                    artifacts.event(
                        "adaptive_route_blocked",
                        reason=decision_route.reason,
                    )
                    break
                artifacts.event(
                    "recovery_contract_evaluated",
                    **recovery_contract.model_dump(mode="json"),
                )
                previous_decision_observation = observation
                last_recoverable_failure = None
                decision_result = None
                local_decider = getattr(cfg.agent_planner, "decide_locally", None)
                if callable(local_decider):
                    try:
                        decision_result = local_decider(
                            observation, steps, len(model_records) + 1
                        )
                    except Exception as exc:
                        # Site acceleration is optional and fail-closed. A bug
                        # or an ambiguous page must preserve the gateway path.
                        artifacts.event(
                            "local_decision_fallback",
                            error=redactor.scrub(str(exc)),
                        )
                        decision_result = None
                if decision_result is None and planner_uses_external_model and not observation.screenshot:
                    # A failed evidence capture is not a model/schema error.
                    # Recapture only: never repeat the preceding browser action.
                    for capture_attempt in range(1, 3):
                        artifacts.event("observation_screenshot_recovery", attempt=capture_attempt,
                                        automatic_action_replay_allowed=False)
                        fresh_screenshot = _capture_screenshot(
                            page, artifacts, f"observation-recovery-{len(steps)}-{capture_attempt}"
                        )
                        if fresh_screenshot:
                            observation = collector.capture(fresh_screenshot)
                            break
                    if not observation.screenshot:
                        question = "【页面截图暂不可用】已保留当前页面和已完成动作，未重复提交。请确认目标浏览器页面可见且已加载，恢复后提交说明继续；不会在缺少当前截图时盲目操作。"
                        if cfg.clarification_callback is None:
                            overall = Status.INCOMPLETE
                            completion_reason = "observation_screenshot_unavailable"
                            break
                        completion_reason = "waiting_for_clarification"
                        with runtime_budget.excluded_wait("clarification"):
                            answer = cfg.clarification_callback(question, len(cfg.clarification_history) + 1)
                        if not answer or not answer.strip():
                            overall = Status.CANCELLED
                            completion_reason = "clarification_cancelled"
                            break
                        observation = collector.capture(_capture_screenshot(
                            page, artifacts, f"observation-resume-{len(steps)}"
                        ))
                        previous_decision_observation = None
                        emit(Status.RUNNING)
                        continue
                if decision_result is None and planner_uses_external_model:
                    if model_budget.exhausted:
                        overall = Status.INCOMPLETE
                        completion_reason = "max_model_calls_exceeded"
                        break
                    call_index = model_budget.begin()
                else:
                    call_index = len(model_records) + 1
                artifacts.event(
                    "actual_decision_route",
                    route="local_verified_action" if decision_result is not None else "external_multimodal",
                    model_call_required=decision_result is None,
                    normal_policy_checks_required=True,
                )
                try:
                    if decision_result is None:
                        decision_result = cfg.agent_planner.decide(
                            observation,
                            steps,
                            call_index,
                            screenshot_path=(
                                artifacts.run_dir / observation.screenshot
                                if observation.screenshot else None
                            ),
                        )
                except AIProviderError as exc:
                    if recover_model_failure(exc, source="planner"):
                        continue
                    break

                model_recovery.decision_succeeded()
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
                    attempt_count=decision_result.attempt_count,
                    repair_count=decision_result.repair_count,
                    multimodal=decision_result.multimodal,
                    input_screenshot=decision_result.input_screenshot,
                )
                model_records.append(record)
                if record.multimodal:
                    multimodal_decision_count += 1
                artifacts.event(
                    "local_decision" if record.protocol == "local" else "model_decision",
                    index=record.index,
                    model=record.model,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    estimated_cost=record.estimated_cost,
                    decision=record.decision,
                    reason=record.reason,
                    attempt_count=record.attempt_count,
                    repair_count=record.repair_count,
                    multimodal=record.multimodal,
                    input_screenshot=record.input_screenshot,
                )
                for compatibility_mode in getattr(
                    decision_result, "compatibility_modes", ()
                ):
                    artifacts.event(
                        "model_transport_compatibility_applied",
                        decision_index=record.index,
                        configured_protocol=record.protocol,
                        compatibility_mode=compatibility_mode,
                        local_schema_validation_required=True,
                    )
                for normalization_event in getattr(
                    decision_result, "normalization_events", ()
                ):
                    event_payload = dict(normalization_event)
                    event_type = str(
                        event_payload.pop("type", "agent_payload_normalized")
                    )
                    artifacts.event(
                        event_type,
                        decision_index=record.index,
                        **event_payload,
                    )
                if record.multimodal:
                    artifacts.event(
                        "multimodal_decision_evidence",
                        decision_index=record.index,
                        screenshot=record.input_screenshot,
                        semantic_signature=(
                            observation.semantic_summary.signature
                            if observation.semantic_summary is not None else ""
                        ),
                    )
                emit(Status.RUNNING)

                if decision.kind == "complete":
                    remaining = (
                        site_pack.remaining_stages(observation, steps, agent_scenario)
                        if agent_scenario is not None else []
                    )
                    if remaining:
                        overall = Status.INCOMPLETE
                        completion_reason = "site_capability_stages_incomplete"
                        artifacts.event(
                            "site_capability_completion_rejected",
                            site_pack=site_pack.site_id,
                            remaining_stages=remaining,
                        )
                        break
                    if site_pack.supports_terminal_state_completion:
                        resolved_indexes: list[int] = []
                        for position, item in enumerate(steps):
                            if item.progress_assessment != "pending_business_verification":
                                continue
                            verification = dict(item.verification_evidence or {})
                            verification.update({
                                "status": "passed",
                                "resolvedBy": "site_terminal_state_completion",
                                "reason": (
                                    "Independent site stages and terminal business state "
                                    "resolved the pending write verification"
                                ),
                            })
                            steps[position] = item.model_copy(update={
                                "status": Status.PASSED,
                                "progress_assessment": "progress",
                                "verification_evidence": verification,
                            })
                            resolved_indexes.append(item.index)
                        if resolved_indexes:
                            artifacts.event(
                                "pending_business_verification_resolved",
                                site_pack=site_pack.site_id,
                                step_indexes=resolved_indexes,
                            )
                    if not effective_assertions and agent_scenario is not None:
                        effective_assertions.extend(
                            site_pack.terminal_assertions(
                                observation, steps, agent_scenario
                            )
                        )
                        if effective_assertions:
                            artifacts.event(
                                "site_capability_assertions_bound",
                                site_pack=site_pack.site_id,
                                assertion_count=len(effective_assertions),
                            )
                    overall = Status.PASSED
                    completion_reason = "agent_goal_completed"
                    break
                if decision.kind == "clarification":
                    question = decision.question or decision.reason
                    round_number = len(cfg.clarification_history) + 1
                    if round_number > 3:
                        overall = Status.INCOMPLETE
                        completion_reason = "clarification_round_limit_exceeded"
                        artifacts.event("clarification_limit_exceeded", maximum_rounds=3)
                        break
                    if cfg.clarification_callback is None:
                        overall = Status.INCOMPLETE
                        completion_reason = "clarification_channel_unavailable"
                        break
                    with runtime_budget.excluded_wait("clarification"):
                        answer = cfg.clarification_callback(question, round_number)
                    if answer is None or not answer.strip():
                        overall = Status.CANCELLED
                        completion_reason = "clarification_cancelled"
                        break
                    entry = {
                        "round": round_number,
                        "question": redactor.scrub(question),
                        "answer": redactor.scrub(answer.strip()),
                    }
                    cfg.clarification_history.append(entry)
                    cfg.agent_planner.scenario.clarification_history = list(cfg.clarification_history)
                    task_authorization = derive_task_authorization(cfg.agent_planner.scenario)
                    artifacts.event("clarification_resolved", **entry)
                    observation = collector.capture(
                        _capture_screenshot(page, artifacts, f"clarification-{round_number}-after")
                    )
                    previous_decision_observation = None
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
                    if model_budget.exhausted:
                        overall = Status.INCOMPLETE
                        completion_reason = "max_model_calls_exceeded"
                        break
                    request = decision.visual_request
                    assert request is not None
                    if not observation.screenshot:
                        overall = Status.INCOMPLETE
                        completion_reason = "visual_evidence_missing"
                        break
                    model_budget.begin()
                    try:
                        visual_result = cfg.visual_adapter.suggest(
                            artifacts.run_dir / observation.screenshot,
                            request.target,
                            observation,
                            requested_action=request.preferred_action,
                            expected_change=request.expected_change,
                        )
                    except AIProviderError as exc:
                        if planner_uses_external_model and recover_model_failure(
                            exc, source="visual_adapter"
                        ):
                            continue
                        if not planner_uses_external_model:
                            overall = Status.INCOMPLETE
                            completion_reason = "visual_target_unconfirmed"
                            artifacts.event(
                                "visual_fallback_failed",
                                trigger_reason=redactor.scrub(request.trigger_reason),
                                screenshot=observation.screenshot,
                                error=redactor.scrub(str(exc)),
                                retryable=False,
                                reason="local_replay_cursor_cannot_request_a_fresh_agent_decision",
                            )
                        break
                    model_recovery.decision_succeeded()
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
                        multimodal=True,
                        input_screenshot=observation.screenshot,
                    ))
                    visual_actions = {
                        "click": ActionType.VISUAL_CLICK,
                        "hover": ActionType.VISUAL_HOVER,
                        "scroll": ActionType.VISUAL_SCROLL,
                        "drag": ActionType.VISUAL_DRAG,
                        "zoom": ActionType.VISUAL_ZOOM,
                        "draw_polygon": ActionType.VISUAL_DRAW_POLYGON,
                        "draw_rectangle": ActionType.VISUAL_DRAW_RECTANGLE,
                    }
                    canvas_gesture = suggestion.action in {
                        "zoom", "draw_polygon", "draw_rectangle"
                    }
                    canvas_locator = request.canvas_locator or (
                        Locator(css="canvas") if canvas_gesture else None
                    )
                    default_effect_kind = (
                        "story_annotation_measurement"
                        if suggestion.action in {"draw_polygon", "draw_rectangle"}
                        else "viewer_camera_clock"
                        if suggestion.action == "zoom"
                        else None
                    )
                    default_effect_level = (
                        "reversible_write"
                        if suggestion.action in {"draw_polygon", "draw_rectangle"}
                        else "session_only"
                        if suggestion.action == "zoom"
                        else None
                    )
                    step = Step(
                        action=visual_actions[suggestion.action],
                        locator=(None if canvas_gesture else request.canvas_locator),
                        canvas_region_locator=canvas_locator,
                        description=f"视觉定位并执行 {suggestion.action}：{request.target}",
                        execution_mode=ExecutionMode.VISUAL,
                        stability_level=StabilityLevel.C,
                        stability_reason="运行时视觉模型重新定位语义目标",
                        visual_target=request.target,
                        relative_position=RelativePosition(xRatio=suggestion.x_ratio, yRatio=suggestion.y_ratio),
                        relative_end_position=(RelativePosition(xRatio=suggestion.end_x_ratio, yRatio=suggestion.end_y_ratio)
                                               if suggestion.end_x_ratio is not None and suggestion.end_y_ratio is not None else None),
                        visual_points=[
                            RelativePosition(xRatio=point.x_ratio, yRatio=point.y_ratio)
                            for point in suggestion.points
                        ],
                        zoom_delta=suggestion.zoom_delta,
                        gesture_finish=suggestion.gesture_finish,
                        visual_expected_change=suggestion.expected_change,
                        scroll_delta_y=suggestion.scroll_delta_y,
                        computer_use_triggered=True,
                        computer_use_reason=request.trigger_reason,
                        effect_kind=request.effect_kind or default_effect_kind,
                        effect_level=request.effect_level or default_effect_level,
                        cleanup_action=(
                            request.cleanup_action
                            or (
                                "仅在用户明确批准删除后人工移除本次测量标注"
                                if suggestion.action in {"draw_polygon", "draw_rectangle"}
                                else None
                            )
                        ),
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
                side_effect_evidence = None
                verification_evidence = None
                failure_phase = "pre_action"
                try:
                    _check_agent_step(
                        step, cfg.forbidden_actions,
                        visual_authorized=decision.kind == "visual",
                        bridge_authorized=bridge_adapter is not None,
                        create_authorized=task_authorization.create_allowed,
                    )
                    summary = _step_summary(step, redactor)
                    artifacts.event("step_started", index=index, action=step.action.value, target=summary)
                    before = collector.capture(_capture_screenshot(page, artifacts, f"step-{index}-before"))
                    visual_grounded_to_locator = False
                    if decision.kind == "visual":
                        # The Run-mode Start control has an unambiguous
                        # accessible name.  Re-check it against the live DOM
                        # before falling back to a second vision request.
                        grounded_step, grounding = _ground_cesium_start_request_to_locator(
                            page, request, base_step=step
                        )
                        grounding_event = "visual_request_grounded_to_locator"
                        if grounded_step is None:
                            grounded_step, grounding = _ground_cesium_speed_request_to_locator(
                                page, request, base_step=step
                            )
                            grounding_event = "speed_request_grounded_to_locator"
                        if grounded_step is not None:
                            step = grounded_step
                            # The initial step summary describes the model's
                            # visual proposal.  Keep subsequent evidence and
                            # confirmation text aligned with the locator that
                            # will actually be dispatched.
                            summary = _step_summary(step, redactor)
                            visual_grounded_to_locator = True
                            artifacts.event(
                                grounding_event,
                                index=index,
                                target=redactor.scrub(request.target),
                                expected_change=redactor.scrub(request.expected_change),
                                url=redactor.scrub(page.url),
                                **grounding,
                                model_call_skipped=True,
                            )
                            emit(Status.RUNNING)
                    if decision.kind == "visual" and not visual_grounded_to_locator:
                        if _visual_observation_key(observation) == _visual_observation_key(before):
                            artifacts.event(
                                "visual_action_grounding_reused",
                                index=index,
                                screenshot=observation.screenshot,
                                current_screenshot=before.screenshot,
                                target=redactor.scrub(request.target),
                                reason="page semantic state unchanged; latest visual grounding remains bounded",
                                model_call_skipped=True,
                            )
                            emit(Status.RUNNING)
                    if (
                        decision.kind == "visual"
                        and not visual_grounded_to_locator
                        and _visual_observation_key(observation) != _visual_observation_key(before)
                    ):
                        # The planner's observation can be several seconds old
                        # by the time a browser surface is selected and the
                        # page settles. Never dispatch a coordinate from that
                        # earlier screenshot. Ground the same semantic target
                        # once more against the exact before-action evidence.
                        if not before.screenshot:
                            # A compositor screenshot can transiently fail
                            # while a WebGL route is settling.  Capture once
                            # more from the current page, then fail closed if
                            # the fresh evidence is still unavailable.
                            retry_screenshot = _capture_screenshot(
                                page, artifacts, f"step-{index}-before-retry"
                            )
                            before = collector.capture(
                                retry_screenshot,
                                skip_canvas_readback=True,
                            )
                            if before.screenshot:
                                artifacts.event(
                                    "visual_before_screenshot_recovered",
                                    index=index,
                                    screenshot=before.screenshot,
                                    retry_count=1,
                                )
                            else:
                                artifacts.event(
                                    "visual_before_screenshot_unavailable",
                                    index=index,
                                    retry_count=1,
                                )
                                raise AIProviderLocalContractError(
                                    "视觉动作执行前没有取得最新截图，拒绝使用旧坐标"
                                )
                        if model_budget.exhausted:
                            raise AIProviderLocalContractError(
                                "视觉动作执行前的重新定位没有可用模型调用预算"
                            )
                        model_budget.begin()
                        try:
                            refreshed_visual = cfg.visual_adapter.suggest(
                                artifacts.run_dir / before.screenshot,
                                request.target,
                                before,
                                requested_action=request.preferred_action,
                                expected_change=request.expected_change,
                            )
                        except AIProviderError as exc:
                            if planner_uses_external_model and recover_model_failure(
                                exc, source="visual_adapter_reground"
                            ):
                                continue
                            raise
                        refreshed = refreshed_visual.suggestion
                        model_records.append(ModelCallRecord(
                            index=len(model_records) + 1,
                            model=refreshed_visual.model,
                            protocol=refreshed_visual.protocol,
                            elapsed_ms=refreshed_visual.elapsed_ms,
                            input_tokens=refreshed_visual.input_tokens,
                            output_tokens=refreshed_visual.output_tokens,
                            estimated_cost=refreshed_visual.estimated_cost,
                            decision="visual_reground",
                            reason=redactor.scrub(refreshed.rationale),
                            multimodal=True,
                            input_screenshot=before.screenshot,
                        ))
                        # Step is a Pydantic model; dataclasses.replace() would
                        # raise here before the mouse action is dispatched.
                        step = _refresh_visual_step(step, refreshed)
                        artifacts.event(
                            "visual_action_regrounded",
                            index=index,
                            screenshot=before.screenshot,
                            target=redactor.scrub(refreshed.target),
                            x_ratio=refreshed.x_ratio,
                            y_ratio=refreshed.y_ratio,
                            confidence=refreshed.confidence,
                        )
                    action_contract = build_action_contract(
                        step, before, site_pack, index=index
                    )
                    artifacts.event(
                        "action_contract_bound",
                        index=index,
                        **action_contract.model_dump(mode="json"),
                    )
                    _require_commerce_metadata(step, cfg)
                    side_effect_evidence = evaluate_side_effect(
                        step, cfg.side_effect_policies,
                        environment_id=cfg.environment_id, role=plan.role or cfg.account_role,
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
                        if not cfg.confirmation_callback(step, index, confirmation_term):
                            was_cancelled = cfg.cancel_event is not None and cfg.cancel_event.is_set()
                            rejected_result = StepResult(
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
                                action_fingerprint=action_fingerprint(step),
                                recovery_strategy="user_rejected_choose_alternative",
                            )
                            steps.append(rejected_result)
                            artifacts.event("dangerous_action_rejected", index=index, rule=confirmation_term)
                            if was_cancelled:
                                overall = Status.CANCELLED
                                completion_reason = "cancelled_by_user"
                                emit(Status.CANCELLED)
                                break
                            executed_steps.append(step)
                            observation = before
                            last_recoverable_failure = FailureCategory.SECURITY
                            completion_reason = "dangerous_action_rejected_replanning"
                            artifacts.event(
                                "action_replan_requested",
                                index=index,
                                action=step.action.value,
                                category=FailureCategory.SECURITY.value,
                                failure_phase="confirmation",
                                no_progress_count=no_progress_count,
                                user_rejected=True,
                            )
                            emit(Status.RUNNING)
                            continue
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
                    failure_phase = "execution"
                    if step.action == ActionType.HUMAN_TAKEOVER:
                        _show_human_takeover_window(context, page)
                        artifacts.event("human_takeover_window_shown", index=index)
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
                    )
                    page = execution_page[0]
                    locator_root = execution_root[0]
                    collector = ObservationCollector(page, artifacts, redactor, cfg.ignore_rules)
                    transition_evidence = stabilize_after_action(
                        page,
                        step,
                        action_contract,
                        before,
                        timeout_ms=cfg.timeout_ms,
                        run_budget=runtime_budget,
                    )
                    artifacts.event(
                        "spa_transition_stabilized",
                        index=index,
                        **transition_evidence,
                    )
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
                    after = collector.capture(
                        _capture_screenshot(page, artifacts, f"step-{index}-after"),
                        skip_canvas_readback=(
                            step.action in {
                                ActionType.VISUAL_CLICK,
                                ActionType.VISUAL_HOVER,
                                ActionType.VISUAL_SCROLL,
                                ActionType.VISUAL_DRAG,
                                ActionType.VISUAL_ZOOM,
                                ActionType.VISUAL_DRAW_POLYGON,
                                ActionType.VISUAL_DRAW_RECTANGLE,
                            }
                            and (urlparse(page.url).hostname or "").lower() == "ion.cesium.com"
                        ),
                    )
                    failure_phase = "verification"
                    if canvas_evidence is not None:
                        canvas_evidence["afterScreenshot"] = after.screenshot
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
                    visual_changed = (
                        _made_progress(step, before, after, artifacts.run_dir)
                        if step.execution_mode == ExecutionMode.VISUAL else None
                    )
                    verification = verify_action_result(
                        action_contract,
                        step,
                        before,
                        after,
                        execution_detail=detail,
                        business_evidence=(
                            commerce_state_evidence
                            or detail.get("asyncEvidence")
                            or detail.get("fileEvidence")
                        ),
                        visual_changed=visual_changed,
                    )
                    verification_evidence = verification.model_dump(mode="json")
                    artifacts.event(
                        "action_result_verified",
                        index=index,
                        **verification_evidence,
                    )
                    if verification.status == "failed":
                        raise ActionVerificationError(verification.reason)
                    pending_business_verification = False
                    if verification.status == "inconclusive":
                        if action_contract.business_verification_required or is_creation_step(step):
                            if not verification.facts:
                                raise SideEffectOutcomeUnknown(
                                    "持久写入动作已发出，但没有取得独立业务状态证明；禁止自动重放",
                                    {
                                        "policy": "proof_first_side_effect_verification",
                                        "outcome": "side_effect_outcome_unknown",
                                        "retried": False,
                                        "verification": verification_evidence,
                                    },
                                )
                            pending_business_verification = True
                        else:
                            raise ActionVerificationError(verification.reason)
                    made_progress = verification.proved_progress
                    if pending_business_verification:
                        made_progress = False
                    _emit_adaptive_route_shadow(
                        artifacts,
                        before=before,
                        after=after,
                        no_progress_count=0,
                    )
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
                    assessment = (
                        "pending_business_verification"
                        if pending_business_verification
                        else ("progress" if made_progress else "no_progress")
                    )
                    result = StepResult(
                        index=index,
                        action=step.action.value,
                        description=step.description,
                        target_summary=summary,
                        status=(Status.INCOMPLETE if pending_business_verification else Status.PASSED),
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
                        verification_evidence=verification_evidence,
                        before=before,
                        after=after,
                        planner_reason=redactor.scrub(decision.reason),
                        progress_assessment=assessment,
                        action_fingerprint=action_fingerprint(step),
                        recovery_attempt=(
                            recovery_contract.attempt + 1
                            if recovery_contract.active else None
                        ),
                        recovery_strategy=(
                            redactor.scrub(decision.reason)
                            if recovery_contract.active else None
                        ),
                    )
                    steps.append(result)
                    checkpoint = build_recovery_checkpoint(
                        index=index,
                        step=step,
                        before=before,
                        after=after,
                        status=result.status,
                        verification_evidence=verification_evidence,
                    )
                    artifacts.write_json(f"checkpoints/step-{index}.json", checkpoint)
                    artifacts.event("recovery_checkpoint_saved", **checkpoint)
                    executed_steps.append(step)
                    observation = after
                    no_progress_count = (
                        0
                        if made_progress or pending_business_verification
                        else no_progress_count + 1
                    )
                    artifacts.event("step_passed", index=index, progress=assessment, no_progress_count=no_progress_count)
                    emit(Status.RUNNING)
                    if no_progress_count >= cfg.no_progress_limit:
                        overall = Status.INCOMPLETE
                        completion_reason = "no_progress_limit_reached"
                        artifacts.event("run_limit_reached", limit="no_progress", count=no_progress_count)
                        break
                except Exception as exc:
                    recovery_evidence = getattr(exc, "evidence", recovery_evidence)
                    target_service_evidence = _terminal_target_service_failure(
                        verification_evidence
                    )
                    if target_service_evidence is not None:
                        # The response event was consumed by the first
                        # post-action observation. Carry the structured
                        # classification across this exception boundary so a
                        # later failure capture cannot turn a definitive 501
                        # into a recoverable/replayable route.
                        verification_evidence = {
                            **(verification_evidence or {}),
                            "targetService": target_service_evidence,
                        }
                        recovery_evidence = {
                            **(recovery_evidence or {}),
                            "decision": "target_service_not_implemented",
                            "automaticReplayAllowed": False,
                            "noReplayReason": "target_service_not_implemented",
                        }
                    if recovery_evidence:
                        artifacts.event("execution_recovery_evaluated", index=index, **recovery_evidence)
                    category = _failure_category(exc)
                    terminal_condition_failed = step.state_machine_id == "site_terminal_loading"
                    if terminal_condition_failed:
                        category = FailureCategory.BUSINESS_STATE
                    stop_loading = (
                        step.action == ActionType.NAVIGATE
                        and category in {FailureCategory.NAVIGATION, FailureCategory.TIMEOUT}
                    )
                    if target_service_evidence is None:
                        after = collector.capture(
                            _capture_screenshot(
                                page,
                                artifacts,
                                f"step-{index}-after-failure",
                                stop_loading=stop_loading,
                            )
                        )
                    elif after is None:
                        # A terminal 501 is normally raised after the regular
                        # post-action observation. Keep that evidence intact;
                        # only capture a fallback if a caller raised before it
                        # was available.
                        after = collector.capture(
                            _capture_screenshot(
                                page,
                                artifacts,
                                f"step-{index}-after-failure",
                                stop_loading=stop_loading,
                            )
                        )
                    else:
                        artifacts.event(
                            "target_service_failure_evidence_preserved",
                            index=index,
                            screenshot=after.screenshot,
                        )
                    if target_service_evidence is not None and after is not None:
                        observed_requests = [
                            str(item) for item in target_service_evidence.get("failedRequests", [])
                            if str(item).strip()
                        ]
                        if observed_requests:
                            after = after.model_copy(update={
                                "failed_requests": list(dict.fromkeys(
                                    [*observed_requests, *after.failed_requests]
                                )),
                            })
                    message = redactor.scrub(str(exc))
                    if target_service_evidence is not None:
                        target_status = str(
                            target_service_evidence.get("targetSimulationStatus") or "unknown"
                        )
                        status_clause = (
                            "仿真仍为 Unstart"
                            if target_status == "Unstart"
                            else f"观测到的仿真状态为 {target_status}"
                        )
                        message = (
                            "启动按钮点击已派发，但目标 startSimulation 服务返回 HTTP 501 "
                            f"Not Implemented；{status_clause}，已禁止自动重放"
                        )
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
                    failed_result = StepResult(
                        index=index,
                        action=step.action.value,
                        description=step.description,
                        target_summary=_step_summary(step, redactor),
                        status=Status.ERROR,
                        started_at=step_started,
                        ended_at=_now(),
                        error_message=message,
                        failure_category=category,
                        failure_phase=failure_phase,
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
                        verification_evidence=verification_evidence,
                        before=before,
                        after=after,
                        planner_reason=redactor.scrub(decision.reason),
                        progress_assessment="no_progress",
                        action_fingerprint=action_fingerprint(step),
                        recovery_attempt=(
                            recovery_contract.attempt + 1
                            if recovery_contract.active else None
                        ),
                        recovery_strategy=(
                            redactor.scrub(decision.reason)
                            if recovery_contract.active else "fresh_observation_and_replan"
                        ),
                    )
                    steps.append(failed_result)
                    checkpoint = build_recovery_checkpoint(
                        index=index,
                        step=step,
                        before=before,
                        after=after,
                        status=failed_result.status,
                        failure_phase=failure_phase,
                        verification_evidence=verification_evidence,
                    )
                    artifacts.write_json(f"checkpoints/step-{index}.json", checkpoint)
                    artifacts.event("recovery_checkpoint_saved", **checkpoint)
                    executed_steps.append(step)
                    if target_service_evidence is not None:
                        failed_step = index
                        overall = Status.INCOMPLETE
                        completion_reason = "target_service_not_implemented"
                        hints.append(_cause_hint(
                            FailureCategory.BUSINESS_STATE,
                            index,
                            message,
                        ))
                        artifacts.event(
                            "target_service_not_implemented",
                            index=index,
                            **target_service_evidence,
                        )
                        emit(Status.RUNNING)
                        break
                    if terminal_condition_failed:
                        failed_step = index
                        overall = Status.ISSUES_FOUND
                        completion_reason = "terminal_loading_condition_failed"
                        hints.append(_cause_hint(
                            FailureCategory.BUSINESS_STATE,
                            index,
                            "Cesium Usage loading indicators did not all disappear within the bounded timeout",
                        ))
                        artifacts.event(
                            "site_terminal_condition_failed",
                            index=index,
                            state_machine_id=step.state_machine_id,
                            error=message,
                        )
                        emit(Status.RUNNING)
                        break
                    if (
                        _should_replan_after_read_failure(
                            step, category, stability_evidence=stability_evidence
                        )
                        or _should_replan_after_locator_failure(
                            step,
                            category,
                            stability_evidence,
                            message,
                            side_effect_evidence,
                            failure_phase,
                        )
                    ):
                        _emit_adaptive_route_shadow(
                            artifacts,
                            before=before,
                            after=after,
                            no_progress_count=no_progress_count + 1,
                            last_failure=category,
                        )
                        observation = after
                        no_progress_count += 1
                        last_recoverable_failure = category
                        artifacts.event(
                            "action_replan_requested",
                            index=index,
                            action=step.action.value,
                            category=category.value,
                            failure_phase=failure_phase,
                            no_progress_count=no_progress_count,
                        )
                        emit(Status.RUNNING)
                        if no_progress_count >= cfg.no_progress_limit:
                            overall = Status.INCOMPLETE
                            completion_reason = "no_progress_limit_reached"
                            artifacts.event(
                                "run_limit_reached",
                                limit="no_progress",
                                count=no_progress_count,
                            )
                            break
                        continue
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
                for index, assertion in enumerate(effective_assertions, start=1):
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
                if not shared_browser:
                    browser.close()

    discovered_plan = plan.model_copy(
        update={"steps": executed_steps, "assertions": effective_assertions}
    )
    if executed_steps:
        artifacts.write_json(
            "discovered-plan.json",
            discovered_plan.model_dump(mode="json", exclude_none=True),
        )
        artifacts.event(
            "discovered_plan_persisted",
            step_count=len(executed_steps),
            assertion_count=len(effective_assertions),
        )

    if (
        overall == Status.PASSED
        and effective_assertions
        and site_pack.supports_auto_stable_replay
        and _auto_stable_replay_safe(executed_steps)
    ):
        stable_run_id = f"{run_id}-stable"
        artifacts.event(
            "stable_replay_started",
            stable_run_id=stable_run_id,
            step_count=len(executed_steps),
        )

        def stable_progress(payload: dict) -> None:
            if cfg.progress_callback is None:
                return
            forwarded = dict(payload)
            forwarded["run_id"] = run_id
            forwarded["plan_name"] = plan.name
            forwarded["execution_phase"] = "stable_replay"
            cfg.progress_callback(forwarded)

        try:
            from .runner import run_plan

            stable_cfg = replace(
                cfg,
                artifacts_root=_stable_replay_artifacts_root(artifacts.run_dir),
                run_id=stable_run_id,
                replay_mode="stable",
                agent_planner=None,
                visual_adapter=None,
                max_model_calls=0,
                max_steps=None,
                progress_callback=stable_progress,
            )
            stable_result, stable_dir = run_plan(discovered_plan, stable_cfg)
            stable_passed = (
                stable_result.status == Status.PASSED
                and stable_result.goal_status == "achieved"
            )
            stable_replay = {
                "runId": stable_result.run_id,
                "status": stable_result.status.value,
                "goalStatus": stable_result.goal_status,
                "completionReason": stable_result.completion_reason,
                "artifactDirectory": str(stable_dir),
                "passed": stable_passed,
            }
            artifacts.event("stable_replay_finished", **stable_replay)
            if not stable_passed:
                overall = (
                    Status.ISSUES_FOUND
                    if stable_result.status in {Status.FAILED, Status.ISSUES_FOUND}
                    else Status.INCOMPLETE
                )
                completion_reason = "stable_replay_failed"
        except Exception as exc:
            stable_replay = {
                "runId": stable_run_id,
                "status": Status.SYSTEM_ERROR.value,
                "goalStatus": "incomplete",
                "completionReason": "stable_replay_exception",
                "passed": False,
                "error": redactor.scrub(str(exc)),
            }
            overall = Status.SYSTEM_ERROR
            completion_reason = "stable_replay_exception"
            artifacts.event("stable_replay_failed", **stable_replay)

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
        source, generated_test = compile_test(discovered_plan)
        generated_test.source_path = artifacts.write_text(generated_test.source_path, source)
    costs = [item.estimated_cost for item in model_records]
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
        model_calls=model_budget.attempts,
        successful_model_calls=_external_model_call_count(model_records),
        model_recovery_attempts=model_recovery.total,
        input_tokens=sum(item.input_tokens for item in model_records),
        output_tokens=sum(item.output_tokens for item in model_records),
        estimated_cost=round(sum(item for item in costs if item is not None), 8) if costs and all(item is not None for item in costs) else None,
        model_call_records=model_records,
        confirmation_history=list(cfg.confirmation_history),
        clarification_history=list(cfg.clarification_history),
        result_classification="agent_passed" if overall == Status.PASSED else "agent_failed",
        model_data_authorization=cfg.model_data_authorization,
        decision_policy=cfg.decision_policy,
        commerce_summary=commerce_summary,
        account_id=cfg.account_id,
        account_role=cfg.account_role,
        project_snapshot=cfg.project_snapshot,
        environment_snapshot=cfg.environment_snapshot,
        business_context_snapshot=cfg.business_context_snapshot or cfg.business_context,
        app_map_snapshot=cfg.app_map_snapshot,
        websocket_timeline=websocket_collector.timeline,
        cleanup_report=cleanup_report,
        stable_replay=stable_replay,
        multimodal_required=getattr(cfg.agent_planner, "multimodal_required", False),
        multimodal_decision_count=multimodal_decision_count,
    )
    required_site_stages = (
        site_pack.required_stage_ids(agent_scenario)
        if agent_scenario is not None else []
    )
    completed_site_stages = (
        site_pack.completed_stage_ids(observation, steps, agent_scenario)
        if required_site_stages else []
    )
    agent_exploration = build_exploration_map(observation, result.steps)
    preliminary_evaluation = evaluate_agent_run(
        status=result.status,
        goal_status="incomplete",
        steps=result.steps,
        model_calls=result.model_call_records,
        evidence_manifest=result.evidence_manifest,
        clarification_count=len(cfg.clarification_history),
        visited_state_count=agent_exploration["visitedStateCount"],
    )
    result = result.model_copy(update={
        "agent_evaluation": preliminary_evaluation,
        "agent_exploration": agent_exploration,
    })
    result = _apply_completion_gate(
        result,
        artifacts,
        required_stage_count=(len(required_site_stages) if required_site_stages else len(steps)),
        completed_stage_count=(len(completed_site_stages) if required_site_stages else None),
        cleanup_required=bool(cfg.business_objects or cfg.commerce_enabled),
        stable_replay_required=_stable_replay_required(
            site_pack, required_site_stages
        ),
    )
    agent_evaluation = evaluate_agent_run(
        status=result.status,
        goal_status=result.goal_status,
        steps=result.steps,
        model_calls=result.model_call_records,
        evidence_manifest=result.evidence_manifest,
        clarification_count=len(cfg.clarification_history),
        visited_state_count=agent_exploration["visitedStateCount"],
    )
    result = result.model_copy(update={
        "agent_evaluation": agent_evaluation,
        "agent_exploration": agent_exploration,
    })
    artifacts.write_json("agent-evaluation.json", agent_evaluation)
    artifacts.write_json("agent-exploration.json", agent_exploration)
    final_manifest, final_manifest_path = build_evidence_package(artifacts, result)
    agent_evaluation = {
        **agent_evaluation,
        "evidenceCompleteness": float(final_manifest.get("completeness") or 0.0),
    }
    result = result.model_copy(update={
        "agent_evaluation": agent_evaluation,
        "evidence_manifest": final_manifest,
        "evidence_manifest_path": final_manifest_path,
    })
    artifacts.write_json("agent-evaluation.json", agent_evaluation)
    artifacts.write_json(
        "evidence/agent-evaluation.json",
        {"runId": result.run_id, "data": agent_evaluation},
    )
    artifacts.event("agent_evaluation_completed", **agent_evaluation)
    overall = result.status
    completion_reason = result.completion_reason
    artifacts.event("run_finished", status=overall.value, completion_reason=completion_reason, duration_ms=result.duration_ms)
    artifacts.finalize(result)
    emit(overall, ended_at=ended)
    return result, artifacts.run_dir


def _visual_observation_key(observation) -> str:
    semantic = observation.semantic_summary
    if semantic is None:
        return f"{observation.url}|{observation.title}"
    dialog_ids = ",".join(
        sorted(
            str(item.get("identity") or item.get("name") or "")
            for item in semantic.dialogs
            if isinstance(item, dict)
        )
    )
    wizard = semantic.wizard if isinstance(semantic.wizard, dict) else {}
    wizard_state = str(
        wizard.get("currentStep")
        or wizard.get("current_step")
        or wizard.get("activeStep")
        or ""
    )
    return "|".join((semantic.page_key, semantic.route, dialog_ids, wizard_state, semantic.signature))


def _check_agent_step(
    step: Step,
    forbidden_actions: tuple[str, ...],
    *,
    visual_authorized: bool = False,
    bridge_authorized: bool = False,
    create_authorized: bool = False,
) -> None:
    if step.execution_mode == ExecutionMode.VISUAL and not visual_authorized:
        raise SecurityError(
            "模型直接输出了 visual 模式动作，但该动作未经过受控视觉适配器的当前截图重新定位；"
            "拒绝执行未经验证的视觉坐标"
        )
    if step.execution_mode == ExecutionMode.APP_BRIDGE and not bridge_authorized:
        raise SecurityError("当前环境未启用 App Bridge，拒绝执行 Bridge 动作")
    if is_creation_step(step) and not create_authorized:
        raise SecurityError(
            "Persistent creation is not authorized by the current user's run instruction"
        )
    text = " ".join(filter(None, [
        step.description or "",
        step.target or "",
        step.locator.describe() if step.locator else "",
    ])).lower()
    blocked = tuple(item.strip().lower() for item in forbidden_actions if item.strip())
    matched = next((item for item in blocked if item in text), None)
    if matched:
        raise SecurityError(f"Agent 动作命中禁止策略：{matched}")
    if step.action == ActionType.NAVIGATE and step.target and not _is_navigation_target(step.target):
        raise SecurityError("Agent 只能导航到 http/https 地址或有效相对路径")


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


def _auto_stable_replay_safe(steps: list[Step]) -> bool:
    """Allow automatic replay only for explicitly classified locator reads."""
    unsafe_actions = {
        ActionType.FILL,
        ActionType.CLEAR,
        ActionType.SELECT,
        ActionType.CHECK,
        ActionType.UNCHECK,
        ActionType.PRESS,
        ActionType.UPLOAD,
        ActionType.UPLOAD_FILE,
        ActionType.COMPONENT,
        ActionType.BRIDGE_CLICK,
        ActionType.HUMAN_TAKEOVER,
        ActionType.VISUAL_DRAW_POLYGON,
        ActionType.VISUAL_DRAW_RECTANGLE,
    }
    for step in steps:
        if step.execution_mode != ExecutionMode.LOCATOR:
            return False
        replayable_checkpoint = (
            step.action in _READ_ONLY_QUERY_ACTIONS
            and (step.state_machine_id or "").startswith("cesium.assets.search.")
            and step.effect_kind == "browse_search_filter_sort"
            and step.effect_level is not None
            and step.effect_level.value in {"read_only", "session_only"}
            and step.value_from_secret is None
            and step.action_category is None
        )
        if step.action in unsafe_actions and not replayable_checkpoint:
            return False
        if step.cleanup_required:
            return False
        if step.effect_level is None or step.effect_level.value not in {
            "read_only", "session_only", "isolated_local_write",
        }:
            return False
    return True


def _stable_replay_artifacts_root(run_dir: Path) -> Path:
    """Keep replay evidence inside the current writable container mount."""
    return run_dir / "stable-replay"


def _stability(steps: list[Step]) -> str:
    ranks = {"A": 0, "B": 1, "C": 2, "D": 3}
    return max((step.stability_level.value for step in steps), key=lambda item: ranks[item], default="A")
