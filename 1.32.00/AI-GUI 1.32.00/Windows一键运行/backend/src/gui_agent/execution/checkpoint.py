"""可验证的普通 Agent Run 检查点与安全恢复辅助函数。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from ..domain.models import ActionType, EffectLevel, Step
from ..domain.results import Observation, Status, StepResult


CHECKPOINT_VERSION = 1

_READ_ONLY_ACTIONS = {
    ActionType.NAVIGATE,
    ActionType.WAIT_FOR,
    ActionType.SCREENSHOT,
    ActionType.HOVER,
    ActionType.SCROLL,
    ActionType.BACK,
    ActionType.RELOAD,
}


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def observation_fingerprint(observation: Observation | None) -> str | None:
    """生成不包含时间戳和截图路径的页面状态指纹。"""

    if observation is None:
        return None
    health = observation.page_health.model_dump(mode="json") if observation.page_health else None
    return _json_hash({
        "url": observation.url,
        "title": observation.title,
        "dom": observation.dom_summary[:60],
        "accessibility": observation.accessibility_summary[:6000],
        "health": health,
    })


def step_effect_level(step: Step | None, result: StepResult) -> str:
    """返回恢复判定使用的副作用等级；未知动作默认按不安全处理。"""

    if step is not None and step.effect_level is not None:
        return step.effect_level.value
    action = step.action if step is not None else result.action
    if action in _READ_ONLY_ACTIONS:
        return EffectLevel.READ_ONLY.value
    evidence = result.side_effect_evidence or {}
    level = evidence.get("effectLevel") or evidence.get("effect_level")
    return str(level or "unknown")


def build_checkpoint(
    *,
    run_id: str,
    status: Status | str,
    observation: Observation | None,
    steps: list[StepResult],
    executed_steps: list[Step],
    current_goal: str,
    resume_from_run_id: str | None = None,
    cleanup_status: str = "not_started",
    login_state_ref: str | None = None,
) -> dict[str, Any]:
    """构造并可安全写入 checkpoint.json 的状态快照。"""

    step_map = {step_index: step for step_index, step in enumerate(executed_steps, start=1)}
    completed: list[dict[str, Any]] = []
    pending_writes: list[dict[str, Any]] = []
    for result in steps:
        level = step_effect_level(step_map.get(result.index), result)
        safe_to_skip = result.status == Status.PASSED and level == EffectLevel.READ_ONLY.value
        item = {
            "index": result.index,
            "action": result.action,
            "description": result.description,
            "target": result.target_summary,
            "status": result.status.value,
            "effectLevel": level,
            "safeToSkip": safe_to_skip,
            "afterUrl": result.after.url if result.after else None,
            "afterTitle": result.after.title if result.after else None,
            "afterFingerprint": observation_fingerprint(result.after),
            "evidence": result.after.screenshot if result.after else result.screenshot,
        }
        completed.append(item)
        if result.status == Status.PASSED and not safe_to_skip:
            pending_writes.append({
                **item,
                "requiresRevalidation": True,
                "reason": "该步骤可能改变网站状态，恢复时必须先核对是否实际发生。",
            })

    safe_steps = [item for item in completed if item["safeToSkip"]]
    current_url = observation.url if observation else None
    return {
        "version": CHECKPOINT_VERSION,
        "runId": run_id,
        "resumeFromRunId": resume_from_run_id,
        "createdAt": datetime.now().astimezone().isoformat(),
        "status": status.value if isinstance(status, Status) else str(status),
        "currentGoal": current_goal,
        "currentUrl": current_url,
        "currentHost": (urlparse(current_url).hostname or "").lower() if current_url else None,
        "pageFingerprint": observation_fingerprint(observation),
        "pageTitle": observation.title if observation else None,
        "completedSteps": completed,
        "safeReadOnlySteps": safe_steps,
        "lastSafeStepIndex": safe_steps[-1]["index"] if safe_steps else None,
        "pendingWriteRevalidations": pending_writes,
        "loginStateRef": login_state_ref,
        "cleanupStatus": cleanup_status,
        "lastSafeRecoveryPoint": {
            "url": current_url,
            "pageFingerprint": observation_fingerprint(observation),
            "stepIndex": safe_steps[-1]["index"] if safe_steps else 0,
        },
        "recoveryPolicy": {
            "reobserveBeforeResume": True,
            "skipOnlyVerifiedReadOnly": True,
            "revalidateWritesBeforeContinue": True,
            "failClosedOnPageMismatch": True,
        },
    }


def verify_checkpoint_page(checkpoint: dict[str, Any], observation: Observation) -> dict[str, Any]:
    """恢复前确认仍在同一页面状态；不一致时返回不可恢复结论。"""

    expected_url = str(checkpoint.get("currentUrl") or "")
    expected_host = str(checkpoint.get("currentHost") or "").lower()
    actual_host = (urlparse(observation.url).hostname or "").lower()
    actual_fingerprint = observation_fingerprint(observation)
    expected_fingerprint = checkpoint.get("pageFingerprint")
    same_page = bool(expected_url and observation.url == expected_url)
    same_host = bool(expected_host and actual_host == expected_host)
    fingerprint_match = bool(expected_fingerprint and actual_fingerprint == expected_fingerprint)
    verified = same_page and same_host and fingerprint_match
    return {
        "verified": verified,
        "sameUrl": same_page,
        "sameHost": same_host,
        "fingerprintMatch": fingerprint_match,
        "expectedUrl": expected_url,
        "actualUrl": observation.url,
        "expectedFingerprint": expected_fingerprint,
        "actualFingerprint": actual_fingerprint,
        "reason": "页面状态与检查点一致" if verified else "页面状态与检查点不一致，必须暂停并由用户选择从头重试",
    }
