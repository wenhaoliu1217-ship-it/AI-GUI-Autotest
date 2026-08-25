"""Product-owned runtime integration profiles for archived GUI-Agent projects.

The reference repositories remain dependency-isolated.  Their useful runtime
ideas are exposed through one product-owned contract: observe/candidate/state
context enters the planner, the resulting decision is checked by the selected
adapter, and the existing guarded Runner remains the only action executor.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..domain.models import ActionType


EXECUTION_PROVIDERS = (
    "native",
    "stagehand",
    "browser-use",
    "ui-tars",
    "playwright-cli",
    "openadapt",
)


_PROFILES: dict[str, dict[str, Any]] = {
    "native": {
        "id": "native",
        "projectIds": [],
        "name": "Native guarded Runner",
        "mode": "product_default",
        "capabilities": ["observation", "decision_gate", "native_action_execution"],
        "actionPolicy": "native_runner_only",
        "status": "built_in",
    },
    "stagehand": {
        "id": "stagehand",
        "projectIds": ["stagehand"],
        "name": "Stagehand candidate router",
        "mode": "candidate_to_guarded_action",
        "capabilities": ["observe_candidates", "semantic_locator_preference", "extraction_evidence"],
        "actionPolicy": "candidate_must_pass_product_gate",
        "status": "product_runtime_integrated",
    },
    "browser-use": {
        "id": "browser-use",
        "projectIds": ["browser-use"],
        "name": "Browser-use state adapter",
        "mode": "stateful_goal_loop",
        "capabilities": ["goal_state", "trajectory_tail", "next_action_boundary", "sensitive_state_redaction"],
        "actionPolicy": "one_action_per_observation",
        "status": "product_runtime_integrated",
    },
    "ui-tars": {
        "id": "ui-tars",
        "projectIds": ["ui-tars"],
        "name": "UI-TARS visual action adapter",
        "mode": "visual_candidate_to_bounded_action",
        "capabilities": ["visual_candidate_contract", "normalized_coordinates", "visual_authorization_gate"],
        "actionPolicy": "visual_action_requires_screenshot_authorization",
        "status": "product_runtime_integrated",
    },
    "playwright-cli": {
        "id": "playwright-cli",
        "projectIds": ["playwright-cli"],
        "name": "Playwright CLI trace adapter",
        "mode": "trace_facts_to_locator_action",
        "capabilities": ["locator_facts", "command_classification", "input_value_redaction", "trace_evidence"],
        "actionPolicy": "trace_facts_never_replay_external_command",
        "status": "product_runtime_integrated",
    },
    "openadapt": {
        "id": "openadapt",
        "projectIds": ["openadapt"],
        "name": "OpenAdapt checkpoint adapter",
        "mode": "checkpointed_workflow",
        "capabilities": ["step_checkpoint", "pause_resume_evidence", "reobserve_before_resume"],
        "actionPolicy": "checkpoint_before_resume_and_revalidate_writes",
        "status": "product_runtime_integrated",
    },
}


def normalize_execution_provider(value: Any) -> str:
    provider = str(value or "native").strip().lower()
    return provider if provider in EXECUTION_PROVIDERS else "native"


def execution_profiles_payload() -> list[dict[str, Any]]:
    """Return the profiles that can be selected by the product GUI."""

    return [dict(profile) for profile in _PROFILES.values()]


def execution_profile(provider: Any) -> dict[str, Any]:
    normalized = normalize_execution_provider(provider)
    return dict(_PROFILES[normalized])


def _bounded(value: Any, limit: int = 1_200) -> str:
    return str(value or "")[:limit]


def _interactive_candidates(observation: Any) -> list[dict[str, str]]:
    lines = getattr(observation, "dom_summary", []) or []
    candidates: list[dict[str, str]] = []
    prefixes = ("button", "a", "input", "select", "textarea", "role=")
    for line in lines[:60]:
        text = _bounded(line, 500).strip()
        if text and text.lower().startswith(prefixes):
            candidates.append({"description": text, "source": "native_observation"})
    return candidates[:24]


def _locator_is_grounded(locator: Any, candidates: list[dict[str, str]]) -> bool:
    """Check that a proposed semantic locator is present in current facts."""

    if locator is None:
        return False
    candidate_text = " ".join(item["description"] for item in candidates).lower()
    specific_values = [
        getattr(locator, "name", None),
        getattr(locator, "label", None),
        getattr(locator, "placeholder", None),
        getattr(locator, "test_id", None),
        getattr(locator, "attribute_name", None),
        getattr(locator, "text", None),
    ]
    values = [str(value).strip().lower() for value in specific_values if str(value or "").strip()]
    if not values and getattr(locator, "role", None):
        values = [str(locator.role).strip().lower()]
    return bool(values) and any(value in candidate_text for value in values)


def _observation_fingerprint(observation: Any) -> str:
    payload = {
        "url": getattr(observation, "url", ""),
        "title": getattr(observation, "title", ""),
        "dom": list((getattr(observation, "dom_summary", []) or [])[:40]),
        "accessibility": _bounded(getattr(observation, "accessibility_summary", ""), 4_000),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def execution_observation_context(provider: Any, observation: Any, history: list[Any]) -> dict[str, Any]:
    """Build bounded provider-specific context for the model planner."""

    normalized = normalize_execution_provider(provider)
    context: dict[str, Any] = {
        "provider": normalized,
        "profile": execution_profile(normalized)["mode"],
        "currentUrl": _bounded(getattr(observation, "url", ""), 2_000),
        "currentTitle": _bounded(getattr(observation, "title", ""), 500),
        "observationFingerprint": _observation_fingerprint(observation),
    }
    if normalized == "stagehand":
        context["candidateActions"] = _interactive_candidates(observation)
        context["candidateRule"] = "Choose only from observed semantic candidates; do not invent a selector."
    elif normalized == "browser-use":
        context["trajectoryTail"] = [
            {
                "index": item.index,
                "action": item.action,
                "status": item.status.value,
                "target": _bounded(item.target_summary, 500),
            }
            for item in history[-8:]
        ]
        context["nextActionBoundary"] = "The planner must return exactly one next action or a safe completion/clarification."
    elif normalized == "ui-tars":
        context["screenshotAvailable"] = bool(getattr(observation, "screenshot", None))
        context["visualContract"] = "Use normalized 0..1 coordinates and only the authorized visual region."
    elif normalized == "playwright-cli":
        context["traceContract"] = {
            "allowedFacts": ["url", "role", "accessible_name", "label", "test_id", "command_class"],
            "redactedFields": ["input_value", "password", "token", "cookie", "authorization"],
            "replayPolicy": "facts_only",
        }
    elif normalized == "openadapt":
        context["checkpoint"] = {
            "fingerprint": _observation_fingerprint(observation),
            "lastStep": history[-1].index if history else 0,
            "resumePolicy": "reobserve_before_resume_and_revalidate_writes",
        }
    return context


def execution_prompt_rules(provider: Any, *, visual_enabled: bool) -> str:
    normalized = normalize_execution_provider(provider)
    if normalized == "native":
        return "当前使用产品原生安全 Runner；保持每轮只决定一个动作。"
    profile = _PROFILES[normalized]
    rules = [
        f"当前启用 {profile['name']} 适配策略。它只负责观察、候选和决策约束，实际动作必须交给产品安全 Runner。",
        f"动作权限：{profile['actionPolicy']}。不得调用外部 CLI、桌面自动化或绕过产品权限。",
    ]
    if normalized == "stagehand":
        rules.append("先从当前 DOM/可访问性事实形成候选，再选择一个候选；定位器必须来自当前观察。")
    elif normalized == "browser-use":
        rules.append("维护目标、当前 URL 和最近轨迹；每轮只输出一个可验证的下一动作，不要批量执行。")
    elif normalized == "ui-tars":
        rules.append(
            "只有截图已授权且视觉 fallback 可用时才返回 visual；坐标必须是 0..1 相对坐标，无法确认时返回 clarification 或 blocked。"
            if visual_enabled else
            "当前没有启用截图视觉 fallback；不要返回 visual 决策，优先使用 DOM/ARIA，信息不足时 clarification。"
        )
    elif normalized == "playwright-cli":
        rules.append("把 CLI 轨迹当作定位和诊断事实，不要重放外部命令；输入值和敏感字段不能进入决策理由。")
    elif normalized == "openadapt":
        rules.append("每个动作后都要验证页面事实并形成检查点；恢复时不得跳过未验证的写入动作。")
    return "\n".join(rules)


def _blocked_decision(decision: Any, reason: str) -> Any:
    return decision.model_copy(update={
        "kind": "blocked",
        "action": None,
        "visual_request": None,
        "question": None,
        "reason": reason[:800],
        "progress_assessment": "no_progress",
    })


def adapt_decision(
    provider: Any,
    decision: Any,
    observation: Any,
    history: list[Any],
    *,
    visual_enabled: bool,
) -> tuple[Any, dict[str, Any]]:
    """Apply a provider contract before the product decision gate.

    The returned decision is still a normal product ``AgentDecision``.  This
    is the point where an open-source strategy can reject an unsafe or
    ungrounded proposal without ever obtaining direct browser access.
    """

    normalized = normalize_execution_provider(provider)
    profile = _PROFILES[normalized]
    action = getattr(decision, "action", None)
    action_name = getattr(getattr(action, "action", None), "value", getattr(action, "action", None))
    evidence: dict[str, Any] = {
        "provider": normalized,
        "status": "applied",
        "adapterMode": profile["mode"],
        "actionPolicy": profile["actionPolicy"],
        "decisionKind": getattr(decision, "kind", None),
        "action": action_name,
        "observationFingerprint": _observation_fingerprint(observation),
        "historyLength": len(history),
        "executionBoundary": "product_guarded_runner",
    }

    if normalized == "stagehand":
        candidates = _interactive_candidates(observation)
        evidence["candidateCount"] = len(candidates)
        if action is not None and action_name in {
            ActionType.CLICK.value, ActionType.FILL.value, ActionType.SELECT.value,
            ActionType.CLEAR.value, ActionType.CHECK.value, ActionType.UNCHECK.value,
            ActionType.HOVER.value,
        } and (
            getattr(action, "locator", None) is None
            or not _locator_is_grounded(getattr(action, "locator", None), candidates)
        ):
            evidence["status"] = "blocked"
            return _blocked_decision(decision, "Stagehand 适配器要求动作绑定当前观察到的语义定位器候选"), evidence
    elif normalized == "ui-tars":
        if getattr(decision, "kind", None) == "visual" and not visual_enabled:
            evidence["status"] = "blocked"
            return _blocked_decision(decision, "UI-TARS 视觉动作需要先授权截图并启用视觉 fallback"), evidence
        evidence["visualAuthorized"] = visual_enabled
    elif normalized == "playwright-cli":
        evidence["traceReplay"] = "disabled"
        evidence["inputValuesPersisted"] = False
    elif normalized == "openadapt":
        evidence["checkpointRequired"] = True
        evidence["checkpointBeforeResume"] = True
    elif normalized == "browser-use":
        evidence["oneActionBoundary"] = True

    return decision, evidence


def create_execution_summary(provider: Any) -> dict[str, Any]:
    normalized = normalize_execution_provider(provider)
    profile = execution_profile(normalized)
    return {
        "provider": normalized,
        "status": "native" if normalized == "native" else "integrated",
        "profile": profile,
        "actionPolicy": profile["actionPolicy"],
        "executionBoundary": "product_guarded_runner",
        "decisionCount": 0,
        "blockedDecisionCount": 0,
        "stepCount": 0,
        "checkpointCount": 0,
        "evidencePath": "evaluations/open-source-execution.json",
    }


def record_decision(summary: dict[str, Any], evidence: dict[str, Any]) -> None:
    summary["decisionCount"] = int(summary.get("decisionCount", 0)) + 1
    if evidence.get("status") == "blocked":
        summary["blockedDecisionCount"] = int(summary.get("blockedDecisionCount", 0)) + 1


def record_step(summary: dict[str, Any], *, checkpoint: bool = False) -> None:
    summary["stepCount"] = int(summary.get("stepCount", 0)) + 1
    if checkpoint:
        summary["checkpointCount"] = int(summary.get("checkpointCount", 0)) + 1
