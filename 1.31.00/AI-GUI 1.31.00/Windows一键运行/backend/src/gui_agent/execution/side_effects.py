"""Object-level side-effect policy evaluation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..domain.models import Step
from ..security.policy import SecurityError


def evaluate_side_effect(
    step: Step, policies: tuple[dict, ...], *, environment_id: str | None, role: str | None,
) -> dict[str, Any] | None:
    if not step.action_category:
        return None
    evidence: dict[str, Any] = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "actionCategory": step.action_category, "objectType": step.object_type,
        "objectName": step.business_object_name, "businessId": step.business_object_id,
        "actorRole": role, "environmentId": environment_id,
        "preconditionState": step.precondition_state, "cleanupRequired": step.cleanup_required,
    }
    if not step.business_object_name or not step.business_object_name.startswith("E2E_"):
        evidence.update(decision="forbid", rule="absolute-e2e-prefix")
        raise SecurityError("副作用动作只允许操作 E2E_ 前缀测试对象")
    matching = []
    for policy in policies:
        if policy.get("actionCategory") != step.action_category or policy.get("objectType") != step.object_type:
            continue
        if policy.get("environmentId") and policy.get("environmentId") != environment_id:
            continue
        if policy.get("role") and policy.get("role") != role:
            continue
        if policy.get("preconditionState") and policy.get("preconditionState") != step.precondition_state:
            continue
        if not re.search(str(policy.get("namePattern", r"^E2E_")), step.business_object_name):
            continue
        matching.append(policy)
    if not matching:
        evidence.update(decision="confirm", rule="conservative-default")
        return evidence
    policy = matching[0]
    decision = str(policy.get("decision"))
    evidence.update(decision=decision, rule=policy.get("id"), rollbackRule=policy.get("rollbackRule", ""))
    if decision == "forbid":
        raise SecurityError(f"副作用策略 {policy.get('id')} 禁止此动作")
    if decision == "conditional" and (not step.cleanup_required or not policy.get("rollbackRule")):
        evidence["decision"] = "forbid"
        raise SecurityError(f"副作用策略 {policy.get('id')} 的条件未满足：需要清理标记和回滚规则")
    return evidence


def confirmation_rule(evidence: dict[str, Any] | None) -> str | None:
    if evidence and evidence.get("decision") == "confirm":
        return f"side-effect:{evidence.get('rule')}"
    return None
