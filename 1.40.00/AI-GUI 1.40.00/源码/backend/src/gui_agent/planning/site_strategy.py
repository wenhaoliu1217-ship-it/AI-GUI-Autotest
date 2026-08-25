"""Small adapter contract for site-specific Agent planning behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlparse

if TYPE_CHECKING:
    from ..domain.results import Observation, StepResult
    from .agent_planner import AgentDecision, AgentScenario


class SiteDecisionStrategy(Protocol):
    """A site strategy may add policy and deterministic recovery decisions."""

    def matches(self, url: str) -> bool: ...

    def pre_model_decision(
        self,
        scenario: "AgentScenario",
        observation: "Observation",
        history: list["StepResult"],
        base_url: str,
    ) -> "AgentDecision | None": ...

    def post_model_decision(
        self,
        scenario: "AgentScenario",
        observation: "Observation",
        history: list["StepResult"],
        base_url: str,
        decision: "AgentDecision",
    ) -> "AgentDecision": ...

    def validate_visual_request(self, request) -> None: ...

    def prompt_rules(self) -> str: ...


def default_site_strategy(base_url: str) -> SiteDecisionStrategy | None:
    """Load a site adapter only when the target is actually in its scope."""

    host = (urlparse(base_url).hostname or "").lower()
    if host == "jd.com" or host.endswith(".jd.com"):
        from .jd_strategy import JDDecisionStrategy

        return JDDecisionStrategy()
    if host not in {"ion.cesium.com", "api.cesium.com"}:
        return None
    from .cesium_strategy import CesiumDecisionStrategy

    return CesiumDecisionStrategy()
