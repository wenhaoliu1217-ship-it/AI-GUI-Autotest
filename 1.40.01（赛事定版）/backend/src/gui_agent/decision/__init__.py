"""Adaptive multimodal routing primitives.

These modules are initially evaluated beside the mandatory multimodal runner.
They do not take production control until real-site acceptance enables the
adaptive_multimodal API policy.
"""

from .observation_diff import ChangeKind, ObservationDiff, diff_observations
from .router import DecisionRoute, DecisionRouteResult, route_decision
from .recovery_contract import (
    RecoveryContract,
    action_fingerprint,
    build_recovery_checkpoint,
    derive_recovery_contract,
    observation_state_key,
    is_persistent_action,
)
from .exploration_map import build_exploration_map

__all__ = [
    "ChangeKind",
    "DecisionRoute",
    "DecisionRouteResult",
    "ObservationDiff",
    "diff_observations",
    "route_decision",
    "RecoveryContract",
    "action_fingerprint",
    "build_recovery_checkpoint",
    "derive_recovery_contract",
    "observation_state_key",
    "is_persistent_action",
    "build_exploration_map",
]
