"""Pure decision routing for the future adaptive multimodal runner."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from ..domain.results import FailureCategory
from .observation_diff import ChangeKind, ObservationDiff


class DecisionRoute(str, Enum):
    EXECUTE_CONTRACT = "execute_contract"
    CALL_MULTIMODAL_AGENT = "call_multimodal_agent"
    VERIFY_ONLY = "verify_only"
    RECOVER = "recover"
    BLOCK = "block"


class DecisionRouteResult(BaseModel):
    route: DecisionRoute
    reason: str
    contract_invalidated: bool = False
    visual_required: bool = False


def route_decision(
    diff: ObservationDiff,
    *,
    has_active_contract: bool = False,
    contract_matches_page: bool = False,
    contract_postcondition_pending: bool = False,
    last_failure: FailureCategory | None = None,
    no_progress_count: int = 0,
    ambiguous_targets: bool = False,
    unlabeled_target: bool = False,
    visual_surface_active: bool = False,
) -> DecisionRouteResult:
    if last_failure is FailureCategory.SECURITY:
        return DecisionRouteResult(
            route=DecisionRoute.BLOCK,
            reason="security policy failures require approval or a changed task authorization",
            contract_invalidated=has_active_contract,
        )
    if last_failure is not None or no_progress_count > 0 or diff.has(ChangeKind.ERROR_STATE):
        return DecisionRouteResult(
            route=DecisionRoute.RECOVER,
            reason="failure, error state, or no-progress evidence requires classified recovery",
            contract_invalidated=has_active_contract,
            visual_required=(
                ambiguous_targets
                or unlabeled_target
                or visual_surface_active
                or no_progress_count >= 2
            ),
        )
    if ambiguous_targets or unlabeled_target or visual_surface_active or diff.has(ChangeKind.VISUAL_SURFACE):
        return DecisionRouteResult(
            route=DecisionRoute.CALL_MULTIMODAL_AGENT,
            reason="the target requires visual disambiguation or visual-surface understanding",
            contract_invalidated=has_active_contract,
            visual_required=True,
        )
    if any(
        diff.has(kind)
        for kind in {
            ChangeKind.INITIAL,
            ChangeKind.DOCUMENT_CHANGE,
            ChangeKind.ROUTE_CHANGE,
            ChangeKind.OVERLAY_OPENED,
            ChangeKind.WIZARD_STAGE,
        }
    ):
        return DecisionRouteResult(
            route=DecisionRoute.CALL_MULTIMODAL_AGENT,
            reason="a new business page, overlay, or wizard stage requires a fresh Agent decision",
            contract_invalidated=has_active_contract,
            visual_required=True,
        )
    if has_active_contract and contract_matches_page:
        return DecisionRouteResult(
            route=DecisionRoute.EXECUTE_CONTRACT,
            reason="the bounded action contract still matches the latest page identity",
        )
    if has_active_contract and not contract_matches_page:
        return DecisionRouteResult(
            route=DecisionRoute.CALL_MULTIMODAL_AGENT,
            reason="the action contract no longer matches the current page identity",
            contract_invalidated=True,
            visual_required=True,
        )
    if contract_postcondition_pending and diff.primary in {
        ChangeKind.CONTROL_STATE,
        ChangeKind.LOCAL_REGION,
        ChangeKind.NONE,
    }:
        return DecisionRouteResult(
            route=DecisionRoute.VERIFY_ONLY,
            reason="only the current contract postcondition needs deterministic verification",
        )
    return DecisionRouteResult(
        route=DecisionRoute.CALL_MULTIMODAL_AGENT,
        reason="no valid action contract covers the current business state",
        visual_required=True,
    )
