"""Bounded, run-local continuation of model-planned ordinary form fills.

No site selectors, values, or business workflow are stored here. Each item
still passes the runner's normal policy, stability and postcondition checks.
"""
from __future__ import annotations

import hashlib
import json
from time import monotonic

from pydantic import BaseModel, Field

from ..domain.models import ActionType, Step
from ..domain.results import Observation, Status
from ..decision.recovery_contract import action_fingerprint


class PlannedFill(BaseModel):
    model_config = {"extra": "forbid"}
    runtime_id: str = Field(pattern=r"^ai_[0-9]+$")
    value: str = Field(max_length=2000)


def _context_key(scenario) -> str:
    return hashlib.sha256(scenario.model_dump_json().encode()).hexdigest()


def _shape(observation: Observation):
    summary = observation.semantic_summary
    if not summary or not summary.page_key or not summary.controls or not observation.screenshot:
        return None
    if (observation.page_errors or observation.failed_requests or observation.page_issues
            or summary.blocking_errors or summary.canvas.get("count", 0)):
        return None
    if any(any(word in signal.lower() for word in ("loading", "error", "invalid", "busy"))
           for signal in summary.state_signals):
        return None
    if any(c.get("invalid") or c.get("validationMessage") for c in summary.controls):
        return None
    data = summary.model_dump(mode="json", exclude={"signature", "controls"})
    data["controls"] = [
        {key: value for key, value in control.items() if key != "valueState"}
        for control in summary.controls
    ]
    return observation.url, json.dumps(data, sort_keys=True, ensure_ascii=False)


def _target(observation, runtime_id):
    controls = observation.semantic_summary.controls if observation.semantic_summary else []
    matches = [c for c in controls if c.get("runtimeId") == runtime_id]
    if len(matches) != 1:
        return None
    control = matches[0]
    label = str(control.get("name", ""))
    if (control.get("role") != "textbox" or not label or control.get("disabled")
            or control.get("valueState") == "redacted"
            or any(word in label.lower() for word in
                   ("password", "token", "secret", "api key", "密码", "验证码", "密钥"))):
        return None
    return control


class FormFastPath:
    def __init__(self):
        self.pending = []
        self.reason = "no_contract"

    def clear(self, reason):
        self.pending = []
        self.reason = reason

    def seed(self, action, fills, observation, history, scenario):
        self.clear("ineligible")
        shape = _shape(observation)
        if (not fills or shape is None or action is None or action.action != ActionType.FILL
                or action.execution_mode.value != "locator" or action.value_from_secret
                or action.action_category or action.effect_level is None
                or action.effect_level.value != "session_only" or not action.locator
                or action.locator.scope or action.browser_target.page != "current"
                or action.browser_target.frame_css or action.browser_target.url_contains
                or not _target(observation, action.locator.runtime_id)):
            return
        # Require a form/dialog surface, never batch arbitrary auto-search inputs.
        summary = observation.semantic_summary
        # Some enterprise UIs expose a modal editor as a wizard rather than
        # populating forms/dialogs.  A visible, non-blocked wizard is still a
        # bounded form surface; keep all other safety checks unchanged.
        wizard = summary.wizard or {}
        if not summary.forms and not summary.dialogs and not wizard.get("visible"):
            return
        ids = [action.locator.runtime_id] + [item.runtime_id for item in fills]
        if len(set(ids)) != len(ids) or any(not _target(observation, rid) for rid in ids):
            return
        self.pending = list(fills)
        self.shape = shape
        self.context = _context_key(scenario)
        self.history_length = len(history)
        self.previous = action_fingerprint(action)
        self.previous_id = action.locator.runtime_id
        self.values = {c.get("runtimeId"): c.get("valueState") for c in summary.controls}
        self.deadline = monotonic() + 90
        self.reason = "model_planned_form"

    def take(self, observation, history, scenario):
        if not self.pending:
            return None
        last = history[-1] if history else None
        evidence = (last.verification_evidence or {}) if last else {}
        valid = (monotonic() < self.deadline and self.context == _context_key(scenario)
                 and self.shape == _shape(observation)
                 and len(history) == self.history_length + 1 and last
                 and last.status == Status.PASSED and last.progress_assessment == "progress"
                 and last.action_fingerprint == self.previous
                 and evidence.get("status") == "passed"
                 and "control_state_verified:value" in evidence.get("facts", []))
        if not valid:
            self.clear("state_or_postcondition_changed")
            return None
        values = {c.get("runtimeId"): c.get("valueState") for c in observation.semantic_summary.controls}
        if any(value != values.get(rid) for rid, value in self.values.items() if rid != self.previous_id):
            self.clear("unrelated_field_changed")
            return None
        item = self.pending.pop(0)
        control = _target(observation, item.runtime_id)
        if not control:
            self.clear("target_unavailable")
            return None
        step = Step(action=ActionType.FILL,
                    locator={"runtime_id": item.runtime_id}, value=item.value,
                    effect_level="session_only",
                    description=f"AI 表单快速通道：填写{control['name']}")
        self.history_length = len(history)
        self.previous = action_fingerprint(step)
        self.previous_id = item.runtime_id
        self.values = values
        return step
