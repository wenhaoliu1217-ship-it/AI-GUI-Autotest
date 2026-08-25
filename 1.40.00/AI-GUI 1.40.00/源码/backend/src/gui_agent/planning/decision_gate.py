"""Adapter-neutral gate for model decisions before browser execution.

The gate is deliberately smaller than a site policy.  It checks whether a
decision is grounded in the latest observation and trajectory, while domain
adapters remain responsible for their own business rules.  This keeps the
generic Agent loop explainable and gives the UI a machine-readable reason when
an otherwise well-formed model response is paused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from ..domain.models import ActionType, EffectLevel, Step
from ..domain.results import Observation, Status, StepResult
if TYPE_CHECKING:
    from .agent_planner import AgentDecision


GateStatus = Literal["pass", "warn", "block"]

_READ_ONLY_ACTIONS = {
    ActionType.NAVIGATE,
    ActionType.WAIT_FOR,
    ActionType.SCREENSHOT,
    ActionType.HOVER,
    ActionType.SCROLL,
    ActionType.BACK,
    ActionType.RELOAD,
    ActionType.DOWNLOAD,
}

_LOCATOR_ACTIONS = {
    ActionType.CLICK,
    ActionType.FILL,
    ActionType.SELECT,
    ActionType.WAIT_FOR,
    ActionType.CLEAR,
    ActionType.CHECK,
    ActionType.UNCHECK,
    ActionType.HOVER,
    ActionType.DOWNLOAD,
    ActionType.UPLOAD_FILE,
    ActionType.UPLOAD,
    ActionType.WAIT_FOR_STATE,
}


@dataclass(frozen=True)
class DecisionGateResult:
    """A small, serializable explanation for the pre-execution decision."""

    status: GateStatus
    reasons: tuple[str, ...] = ()
    evidence: dict[str, object] | None = None

    @property
    def passed(self) -> bool:
        return self.status != "block"


def audit_agent_decision(
    decision: "AgentDecision",
    observation: Observation | None,
    history: list[StepResult],
    external_evaluation: dict[str, object] | None = None,
) -> DecisionGateResult:
    """Audit one decision without executing it or calling a site adapter.

    The audit is intentionally fail-closed only for contradictions that are
    unsafe to hand to the executor: repeated non-progressing writes, a missing
    required target, or a completion claim immediately after a failed step.
    Locator absence from a bounded DOM summary is a warning because the
    summary is not guaranteed to contain every off-screen or lazy control.
    """

    evidence: dict[str, object] = {
        "observationAvailable": observation is not None,
        "historyCount": len(history),
        "decisionKind": decision.kind,
    }
    if external_evaluation:
        evidence["externalEvaluation"] = {
            key: external_evaluation.get(key)
            for key in ("provider", "status", "upstreamStatus", "runStatus", "upstreamRuntimeStarted", "actionPolicy", "qualityMetrics")
            if key in external_evaluation
        }
    if decision.kind in {"clarification", "blocked"}:
        return DecisionGateResult("pass", evidence=evidence)

    if decision.kind == "complete":
        if history and _failed_without_progress(history[-1]):
            return DecisionGateResult(
                "block",
                ("最近一步失败或没有取得进展，不能直接宣称任务完成。",),
                {**evidence, "lastStepFailed": True},
            )
        if not history:
            return DecisionGateResult(
                "warn",
                ("尚无动作轨迹；完成结论只能依赖当前页面观察和后续断言。",),
                {**evidence, "completionWithoutAction": True},
            )
        return DecisionGateResult("pass", evidence=evidence)

    if decision.kind == "visual":
        request = decision.visual_request
        if request is None:
            return DecisionGateResult("block", ("视觉决策缺少视觉请求。",), evidence)
        if observation is None or not observation.screenshot:
            return DecisionGateResult(
                "block",
                ("视觉决策缺少当前页面截图证据。",),
                {**evidence, "screenshotAvailable": False},
            )
        evidence = {**evidence, "screenshotAvailable": True, "visualTargetProvided": bool(request.target.strip())}
        if request.effect_level in _HIGH_RISK_EFFECTS:
            return DecisionGateResult(
                "warn",
                ("视觉动作涉及高风险影响等级，执行前仍必须经过专项策略和人工确认。",),
                evidence,
            )
        return DecisionGateResult("pass", evidence=evidence)

    step = decision.action
    if step is None:
        return DecisionGateResult("block", ("动作决策缺少可执行步骤。",), evidence)

    reasons: list[str] = []
    evidence = {
        **evidence,
        "action": step.action.value,
        "executionMode": step.execution_mode.value,
        "effectLevel": step.effect_level.value if step.effect_level else None,
    }
    if step.action in _LOCATOR_ACTIONS:
        if step.locator is None:
            return DecisionGateResult("block", ("该动作需要确定性定位器，模型没有提供。",), evidence)
        target_observed = _target_appears_in_observation(step, observation)
        evidence["targetObserved"] = target_observed
        if target_observed is False:
            reasons.append("目标未出现在最近一次有限页面观察中，执行器必须再次定位并验证。")

    if step.action == ActionType.NAVIGATE and step.target:
        parsed = urlparse(step.target)
        evidence["absoluteNavigation"] = bool(parsed.scheme or parsed.netloc)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            return DecisionGateResult("block", ("导航协议不在 http/https 白名单内。",), evidence)

    if step.effect_level is None and step.action not in _READ_ONLY_ACTIONS:
        reasons.append("动作没有显式 effect_level，将按保守审批规则处理。")

    if step.action not in _READ_ONLY_ACTIONS and _repeats_without_progress(step, history):
        return DecisionGateResult(
            "block",
            ("同一动作在没有取得进展后被重复提出，已暂停以避免 Agent 原地循环。",),
            {**evidence, "repeatedWithoutProgress": True},
        )

    return DecisionGateResult("warn" if reasons else "pass", tuple(reasons), evidence)


_HIGH_RISK_EFFECTS = {
    EffectLevel.HIGH_RISK_WRITE,
    EffectLevel.HIGH_RISK_EXTERNAL_WRITE,
    EffectLevel.HIGH_RISK_IRREVERSIBLE,
    EffectLevel.HIGH_RISK_PUBLIC_WRITE,
    EffectLevel.HIGH_RISK_IDENTITY_WRITE,
    EffectLevel.FORBIDDEN,
}


def _failed_without_progress(result: StepResult) -> bool:
    return result.status in {Status.ERROR, Status.FAILED} or result.progress_assessment == "no_progress"


def _repeats_without_progress(step: Step, history: list[StepResult]) -> bool:
    if len(history) < 2:
        return False
    signature = _step_signature(step)
    recent = history[-2:]
    return all(_result_signature(item) == signature and _failed_without_progress(item) for item in recent)


def _step_signature(step: Step) -> tuple[str, str, str]:
    return (
        step.action.value,
        step.locator.describe() if step.locator else "",
        step.target or "",
    )


def _result_signature(result: StepResult) -> tuple[str, str, str]:
    target = result.target_summary
    if " @ " in target:
        target = target.split(" @ ", 1)[1]
    if " value=" in target:
        target = target.split(" value=", 1)[0]
    return (result.action, target, "")


def _target_appears_in_observation(step: Step, observation: Observation | None) -> bool | None:
    if observation is None or step.locator is None:
        return None
    facts = "\n".join([
        *observation.dom_summary,
        observation.accessibility_summary,
    ]).casefold()
    hints = (
        step.locator.name,
        step.locator.label,
        step.locator.placeholder,
        step.locator.test_id,
        step.locator.attribute_name,
        step.locator.attribute.value if step.locator.attribute else None,
        step.locator.href,
        step.locator.text,
    )
    usable = [item.strip().casefold() for item in hints if item and item.strip()]
    if not usable:
        return None
    return any(item in facts for item in usable)
