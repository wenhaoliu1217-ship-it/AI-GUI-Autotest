"""Classify value-safe browser observation changes for decision routing."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from ..domain.results import Observation, PageSemanticSummary


class ChangeKind(str, Enum):
    INITIAL = "initial"
    NONE = "none"
    CONTROL_STATE = "control_state"
    LOCAL_REGION = "local_region"
    OVERLAY_OPENED = "overlay_opened"
    WIZARD_STAGE = "wizard_stage"
    ROUTE_CHANGE = "route_change"
    DOCUMENT_CHANGE = "document_change"
    VISUAL_SURFACE = "visual_surface"
    ERROR_STATE = "error_state"


class ObservationDiff(BaseModel):
    primary: ChangeKind
    changes: list[ChangeKind] = Field(default_factory=list)
    before_page_key: str = ""
    after_page_key: str = ""
    before_signature: str = ""
    after_signature: str = ""
    details: list[str] = Field(default_factory=list)

    def has(self, kind: ChangeKind) -> bool:
        return kind in self.changes


_PRIORITY = [
    ChangeKind.ERROR_STATE,
    ChangeKind.DOCUMENT_CHANGE,
    ChangeKind.ROUTE_CHANGE,
    ChangeKind.VISUAL_SURFACE,
    ChangeKind.OVERLAY_OPENED,
    ChangeKind.WIZARD_STAGE,
    ChangeKind.LOCAL_REGION,
    ChangeKind.CONTROL_STATE,
]


def diff_observations(
    before: Observation | None,
    after: Observation,
) -> ObservationDiff:
    current = after.semantic_summary or PageSemanticSummary()
    if before is None:
        return ObservationDiff(
            primary=ChangeKind.INITIAL,
            changes=[ChangeKind.INITIAL],
            after_page_key=current.page_key,
            after_signature=current.signature,
            details=["first bounded observation"],
        )

    previous = before.semantic_summary or PageSemanticSummary()
    changes: list[ChangeKind] = []
    details: list[str] = []

    before_origin = _origin(before.url)
    after_origin = _origin(after.url)
    if before_origin != after_origin or (
        previous.page_key and current.page_key and previous.page_key != current.page_key
        and previous.route == current.route
    ):
        changes.append(ChangeKind.DOCUMENT_CHANGE)
        details.append("document identity changed")
    if previous.route != current.route:
        changes.append(ChangeKind.ROUTE_CHANGE)
        details.append(f"route changed: {previous.route or '-'} -> {current.route or '-'}")

    previous_dialogs = _identities(previous.dialogs)
    current_dialogs = _identities(current.dialogs)
    if current_dialogs - previous_dialogs:
        changes.append(ChangeKind.OVERLAY_OPENED)
        details.append("new overlay identity observed")

    if _wizard_identity(previous.wizard) != _wizard_identity(current.wizard):
        changes.append(ChangeKind.WIZARD_STAGE)
        details.append("wizard stage changed")

    if _visual_identity(previous) != _visual_identity(current):
        changes.append(ChangeKind.VISUAL_SURFACE)
        details.append("canvas or visual surface state changed")

    previous_errors = _error_facts(before)
    current_errors = _error_facts(after)
    if current_errors - previous_errors:
        changes.append(ChangeKind.ERROR_STATE)
        details.append("new runtime error fact observed")

    if _control_states(previous.controls) != _control_states(current.controls):
        changes.append(ChangeKind.CONTROL_STATE)
        details.append("control state changed")

    if (
        previous.signature != current.signature
        and not any(
            kind in changes
            for kind in {
                ChangeKind.DOCUMENT_CHANGE,
                ChangeKind.ROUTE_CHANGE,
                ChangeKind.OVERLAY_OPENED,
                ChangeKind.WIZARD_STAGE,
                ChangeKind.VISUAL_SURFACE,
                ChangeKind.CONTROL_STATE,
            }
        )
    ):
        changes.append(ChangeKind.LOCAL_REGION)
        details.append("page signature changed without a stronger semantic change")

    unique_changes = list(dict.fromkeys(changes))
    primary = next((kind for kind in _PRIORITY if kind in unique_changes), ChangeKind.NONE)
    return ObservationDiff(
        primary=primary,
        changes=unique_changes or [ChangeKind.NONE],
        before_page_key=previous.page_key,
        after_page_key=current.page_key,
        before_signature=previous.signature,
        after_signature=current.signature,
        details=details or ["no bounded semantic change"],
    )


def _origin(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}" if parsed.netloc else ""


def _identities(items: list[dict[str, str]]) -> set[str]:
    return {
        str(item.get("identity") or item.get("name") or item.get("title") or "").strip()
        for item in items
        if str(item.get("identity") or item.get("name") or item.get("title") or "").strip()
    }


def _wizard_identity(wizard: dict) -> tuple:
    return (
        wizard.get("current"),
        wizard.get("currentStep"),
        wizard.get("activeIndex"),
        wizard.get("title"),
    )


def _visual_identity(summary: PageSemanticSummary) -> tuple:
    canvas = summary.canvas or {}
    return (
        bool(canvas),
        canvas.get("count"),
        canvas.get("webgl"),
        canvas.get("signature"),
    )


def _control_states(controls: list[dict[str, str | bool]]) -> dict[str, tuple]:
    states: dict[str, tuple] = {}
    for index, control in enumerate(controls):
        identity = str(
            control.get("runtimeId")
            or control.get("testId")
            or control.get("name")
            or f"control-{index}"
        )
        states[identity] = (
            control.get("valueState"),
            control.get("selected"),
            control.get("checked"),
            control.get("disabled"),
            control.get("invalid"),
        )
    return states


def _error_facts(observation: Observation) -> set[str]:
    return {
        *observation.console_errors,
        *observation.page_errors,
        *observation.failed_requests,
        *(f"{issue.kind}:{issue.message}" for issue in observation.page_issues),
    }
