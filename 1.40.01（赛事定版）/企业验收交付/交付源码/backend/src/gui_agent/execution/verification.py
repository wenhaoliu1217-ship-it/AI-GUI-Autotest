"""Proof-oriented action contracts and post-action verification."""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field

from ..domain.models import ActionType, EffectLevel, Step
from ..domain.results import Observation
from ..decision.recovery_contract import is_persistent_action
from ..site_capabilities import SiteCapabilityPack


class ActionVerificationError(RuntimeError):
    pass


class ActionContract(BaseModel):
    contract_id: str
    action: str
    precondition_page_key: str = ""
    expected_signals: list[str] = Field(default_factory=list)
    expected_route_prefix: str | None = None
    expected_heading: str | None = None
    business_verification_required: bool = False


class ActionVerification(BaseModel):
    status: Literal["passed", "inconclusive", "failed"]
    contract: ActionContract
    facts: list[str] = Field(default_factory=list)
    reason: str

    @property
    def proved_progress(self) -> bool:
        return self.status == "passed"


def build_action_contract(
    step: Step,
    before: Observation,
    site_pack: SiteCapabilityPack,
    *,
    index: int,
) -> ActionContract:
    expected = site_pack.expected_transition(step, before)
    route_prefix = str(expected.get("urlPathPrefix") or "").strip() or None
    expected_heading = str(expected.get("headingText") or "").strip() or None
    signals: list[str] = []
    if step.action == ActionType.NAVIGATE:
        signals.append("target_url_reached")
        if route_prefix is None and step.target:
            absolute = urljoin(before.url if before.url != "about:blank" else "http://invalid/", step.target)
            route_prefix = urlparse(absolute).path or "/"
    elif step.action == ActionType.WAIT_FOR:
        signals.append("wait_condition_satisfied")
    elif step.action in {ActionType.CLICK, ActionType.PRESS, ActionType.CHECK, ActionType.UNCHECK, ActionType.SELECT}:
        signals.extend(("url_changed", "page_signature_changed", "dialog_or_state_changed"))
    elif step.action in {ActionType.FILL, ActionType.CLEAR}:
        signals.extend(("page_signature_changed", "control_state_changed"))
    elif step.execution_mode.value in {"visual", "app_bridge"}:
        signals.append("visual_or_bridge_state_changed")
    else:
        signals.append("execution_completed")

    effect_level = step.effect_level.value if step.effect_level is not None else EffectLevel.READ_ONLY.value
    business_required = (
        effect_level not in {"read_only", "session_only", "isolated_local_write"}
        or is_persistent_action(step)
    )
    return ActionContract(
        contract_id=f"step-{index}:{step.action.value}",
        action=step.action.value,
        precondition_page_key=_page_key(before),
        expected_signals=signals,
        expected_route_prefix=route_prefix,
        expected_heading=expected_heading,
        business_verification_required=business_required,
    )


def verify_action_result(
    contract: ActionContract,
    step: Step,
    before: Observation,
    after: Observation,
    *,
    execution_detail: dict[str, Any] | None = None,
    business_evidence: dict[str, Any] | None = None,
    visual_changed: bool | None = None,
) -> ActionVerification:
    detail = execution_detail or {}
    facts: list[str] = []
    before_key = _page_key(before)
    after_key = _page_key(after)
    before_signature = _signature(before)
    after_signature = _signature(after)

    target_service_failure = target_service_not_implemented_evidence(step, after)
    if target_service_failure is not None:
        # The click has already completed by the time post-action verification
        # runs. Keep the transport fact and the observed target state together
        # so the runner can stop recovery without guessing or replaying it.
        target_status = str(target_service_failure.get("targetSimulationStatus") or "unknown")
        status_clause = (
            "the simulation remained Unstart"
            if target_status == "Unstart"
            else f"the observed simulation status was {target_status}"
        )
        return ActionVerification(
            status="failed",
            contract=contract,
            facts=list(target_service_failure["facts"]),
            reason=(
                "The start button click was dispatched, but the target "
                "startSimulation service returned HTTP 501 Not Implemented; "
                f"{status_clause} and automatic replay is forbidden"
            ),
        )

    # A screenshot or URL mutation is not proof that a business action
    # succeeded.  SPAs often repaint after a request fails, which previously
    # let a visual click on "启动" pass even though startSimulation returned
    # HTTP 500/501.  Reject high-confidence transport and page-runtime errors
    # before considering any visual or DOM change.
    transport_failures = _blocking_transport_failures(after)
    if transport_failures:
        return ActionVerification(
            status="failed",
            contract=contract,
            facts=[f"runtime_failure:{item}" for item in transport_failures[:8]],
            reason="The current page reported a failed business request or uncaught runtime error",
        )

    if contract.expected_route_prefix:
        path = urlparse(after.url).path or "/"
        if path.startswith(contract.expected_route_prefix):
            facts.append(f"route={path}")
        elif step.action == ActionType.NAVIGATE:
            return ActionVerification(
                status="failed",
                contract=contract,
                facts=[f"actual_route={path}"],
                reason=f"Navigation did not reach expected route prefix {contract.expected_route_prefix}",
            )

    if before.url != after.url:
        facts.append("url_changed")
    if before_signature and after_signature and before_signature != after_signature:
        facts.append("page_signature_changed")
    if before_key != after_key:
        facts.append("page_key_changed")
    if _dialog_state(before) != _dialog_state(after):
        facts.append("dialog_state_changed")
    if _state_signals(before) != _state_signals(after):
        facts.append("state_signals_changed")
    if visual_changed:
        facts.append("visual_state_changed")
    if step.action == ActionType.WAIT_FOR and "waitState" in detail:
        facts.append(f"wait_condition={detail['waitState']}")
    if business_evidence and any(
        business_evidence.get(key) for key in ("verified", "businessObjectId", "generatedBusinessId", "businessId")
    ):
        facts.append("business_state_verified")

    validation_failures = _validation_failures(after)
    if contract.business_verification_required and validation_failures:
        return ActionVerification(
            status="failed",
            contract=contract,
            facts=[f"validation_failure:{item}" for item in validation_failures[:8]],
            reason="The persistent action left visible validation or blocking errors on the current page",
        )

    control_state = detail.get("controlState")
    if isinstance(control_state, dict):
        if control_state.get("verified") is True:
            facts.append(f"control_state_verified:{control_state.get('kind', 'unknown')}")
        elif control_state.get("verified") is False:
            return ActionVerification(
                status="failed",
                contract=contract,
                facts=[f"control_state_mismatch:{control_state.get('kind', 'unknown')}"],
                reason="The control did not expose the state requested by the action",
            )

    if (
        step.action == ActionType.PRESS
        and before.accessibility_summary != after.accessibility_summary
    ):
        facts.append("accessibility_state_changed")

    if contract.business_verification_required and "business_state_verified" not in facts:
        return ActionVerification(
            status="inconclusive",
            contract=contract,
            facts=facts,
            reason="The action was dispatched but no independent business-state proof was captured",
        )

    if facts:
        return ActionVerification(
            status="passed",
            contract=contract,
            facts=facts,
            reason="At least one independent post-action signal satisfied the contract",
        )

    if step.action in {ActionType.SCREENSHOT, ActionType.HOVER, ActionType.SCROLL}:
        return ActionVerification(
            status="passed",
            contract=contract,
            facts=["non-mutating_action_completed"],
            reason="The non-mutating action completed and does not require a state transition",
        )

    return ActionVerification(
        status="inconclusive",
        contract=contract,
        facts=[],
        reason="Execution returned without an independently observable post-action change",
    )


def _page_key(observation: Observation) -> str:
    summary = observation.semantic_summary
    return summary.page_key if summary is not None else f"{observation.url}|{observation.title}"


def _signature(observation: Observation) -> str:
    summary = observation.semantic_summary
    return summary.signature if summary is not None else ""


def _dialog_state(observation: Observation) -> tuple[tuple[str, str], ...]:
    summary = observation.semantic_summary
    if summary is None:
        return ()
    return tuple((str(item.get("role", "")), str(item.get("name", ""))) for item in summary.dialogs)


def _state_signals(observation: Observation) -> tuple[str, ...]:
    summary = observation.semantic_summary
    return tuple(summary.state_signals) if summary is not None else ()


def _validation_failures(observation: Observation) -> list[str]:
    summary = observation.semantic_summary
    if summary is None:
        return []
    failures = [str(item) for item in summary.blocking_errors if str(item).strip()]
    for control in summary.controls:
        if not isinstance(control, dict):
            continue
        invalid = control.get("invalid") is True
        message = str(control.get("validationMessage") or "").strip()
        if not invalid and not message:
            continue
        identity = next((
            str(control.get(key) or "").strip()
            for key in ("name", "label", "placeholder", "runtimeId", "role")
            if str(control.get(key) or "").strip()
        ), "unknown-control")
        failures.append(f"{identity}: {message or 'invalid'}")
    return list(dict.fromkeys(failures))


_BENIGN_STATIC_404 = re.compile(
    r"/assets/(?:font-files|fonts)/[^/\s]+\.(?:woff2?|ttf|otf)(?:[?#][^\s]*|\s|\]|\)|$)",
    re.I,
)
_HTTP_FAILURE = re.compile(r"\bHTTP\s+([45]\d\d)\b", re.I)
_HTTP_501 = re.compile(r"\bHTTP\s+501\b", re.I)
_START_SIMULATION_ENDPOINT = re.compile(
    r"/api/v1/lavic-core/startSimulation(?:[/?#\s\]\)]|$)",
    re.I,
)
_NETWORK_FAILURE = re.compile(
    r"ERR_(?:CONNECTION|NAME_NOT_RESOLVED|TIMED_OUT|FAILED|INTERNET_DISCONNECTED|NETWORK_CHANGED|RESET|ABORTED)",
    re.I,
)


def target_service_not_implemented_evidence(
    step: Step,
    observation: Observation,
) -> dict[str, Any] | None:
    """Classify a dispatched simulation start click rejected with HTTP 501.

    This is deliberately narrow: only click-like actions and the exact
    ``startSimulation`` endpoint qualify. A 501 from another request, or a
    page reload that merely observes old state, must continue through the
    normal transport/error handling path.
    """
    action = getattr(step.action, "value", step.action)
    if str(action) not in {"click", "visual_click", "press", "bridge_click"}:
        return None
    matching = [
        str(raw).strip()
        for raw in observation.failed_requests
        if _HTTP_501.search(str(raw)) and _START_SIMULATION_ENDPOINT.search(str(raw))
    ]
    if not matching:
        return None

    status = _observed_simulation_status(observation)
    facts = [
        "target_service_not_implemented",
        "click_dispatched",
        f"target_simulation_status={status or 'unknown'}",
        "automatic_replay_allowed=false",
        *[f"runtime_failure:{item[:500]}" for item in matching[:8]],
    ]
    return {
        "classification": "target_service_not_implemented",
        "httpStatus": 501,
        "endpoint": "/api/v1/lavic-core/startSimulation",
        "clickDispatched": True,
        "targetSimulationStatus": status or "unknown",
        "automaticReplayAllowed": False,
        "noReplayReason": "target_service_not_implemented",
        "facts": facts,
        "failedRequests": matching[:8],
    }


def target_service_not_implemented_from_verification(
    verification_evidence: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Recover the terminal 501 classification after an exception boundary.

    The runner takes a second observation while constructing a failure result;
    that capture can consume the original response event. The verification
    facts therefore remain the authoritative hand-off for the terminal
    predicate.
    """
    if not isinstance(verification_evidence, dict):
        return None
    if str(verification_evidence.get("status") or "").lower() != "failed":
        return None
    facts = [str(value) for value in verification_evidence.get("facts", [])]
    if "target_service_not_implemented" not in facts:
        return None
    status_fact = next(
        (value for value in facts if value.startswith("target_simulation_status=")),
        "target_simulation_status=unknown",
    )
    status = status_fact.split("=", 1)[1] or "unknown"
    failed_requests = [
        value.split("runtime_failure:", 1)[1]
        for value in facts
        if value.startswith("runtime_failure:")
    ]
    return {
        "classification": "target_service_not_implemented",
        "httpStatus": 501,
        "endpoint": "/api/v1/lavic-core/startSimulation",
        "clickDispatched": "click_dispatched" in facts,
        "targetSimulationStatus": status,
        "automaticReplayAllowed": False,
        "noReplayReason": "target_service_not_implemented",
        "failedRequests": failed_requests[:8],
    }


def _observed_simulation_status(observation: Observation) -> str | None:
    """Extract a bounded simulation status from route/semantic evidence."""
    sources: list[str] = [observation.url]
    summary = observation.semantic_summary
    if summary is not None:
        sources.extend((summary.route, " ".join(summary.state_signals)))
    sources.extend((observation.accessibility_summary, " ".join(observation.dom_summary)))
    joined = " ".join(str(item) for item in sources if item)
    match = re.search(
        r"(?:simulationstatus|simulation_status)\s*[=:]\s*(unstart|running|run|stopped|stop)",
        joined,
        re.I,
    )
    if match:
        value = match.group(1).lower()
        return "Running" if value in {"running", "run"} else "Unstart"
    if re.search(r"\bunstart\b", joined, re.I):
        return "Unstart"
    return None


def _blocking_transport_failures(observation: Observation) -> list[str]:
    """Return current-page failures that invalidate the just-dispatched action."""
    failures: list[str] = []
    for raw in observation.failed_requests:
        text = str(raw).strip()
        if not text or _BENIGN_STATIC_404.search(text):
            continue
        status = _HTTP_FAILURE.search(text)
        if status or _NETWORK_FAILURE.search(text):
            # An aborted analytics/media request is already filtered by the
            # collector; keep this guard for older evidence files.
            if "ERR_ABORTED" in text.upper() and not status:
                continue
            failures.append(text[:500])
    for raw in observation.page_errors:
        text = str(raw).strip()
        if text:
            failures.append(f"pageerror: {text[:500]}")
    return list(dict.fromkeys(failures))
