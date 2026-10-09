"""Versioned site capability contracts used by the generic Agent core."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from ..domain.models import Assertion, AssertionType, Locator, Step
from ..domain.results import Observation, StepResult


@dataclass(frozen=True)
class WorkflowStage:
    id: str
    route_prefixes: tuple[str, ...]
    navigation_name: str | None = None


class SiteCapabilityPack:
    """A narrow, versioned adapter around site knowledge.

    The core runner remains site-neutral. A pack may classify page stages,
    provide planning facts, normalize harmless provider filler, and declare
    terminal assertions. It must never weaken the runner's safety policy.
    """

    site_id = "generic-web"
    version = "1"
    supports_auto_stable_replay = False
    # A vertical pack may opt into terminal-state proof only when it defines
    # independent required stages and terminal assertions for non-replayable
    # persistent workflows.
    supports_terminal_state_completion = False

    def matches(self, url: str) -> bool:
        return False

    def normalize_action_payload(self, action: dict[str, Any]) -> None:
        effect_level = str(action.get("effect_level") or "").strip()
        if effect_level in {"read_only", "session_only"}:
            action.pop("action_category", None)

    def effective_business_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return the context exposed to planners for this site.

        Vertical packs may reconcile legacy project facts with a newer,
        site-specific contract. The stored project remains untouched so the
        effective view is auditable and cannot silently rewrite user data.
        """
        return deepcopy(context)

    def planner_context(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> dict[str, Any]:
        return {
            "sitePack": self.site_id,
            "sitePackVersion": self.version,
            "advisoryOnly": True,
            "referencePolicy": (
                "Use only when confirmed by the latest screenshot, DOM, "
                "accessibility tree, and runtime stability checks."
            ),
            "pageStage": self.page_stage(observation),
            "requiredStages": [],
            "visitedStages": [],
            "remainingStages": [],
        }

    def page_stage(self, observation: Observation) -> str | None:
        route = self._route(observation.url)
        return route or None

    def remaining_stages(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[str]:
        return []

    def required_stage_ids(self, scenario: Any) -> list[str]:
        return []

    def completed_stage_ids(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[str]:
        return []

    def required_followup_action(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> Step | None:
        return None

    def next_required_action(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> Step | None:
        return None

    def terminal_assertions(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[Assertion]:
        return []

    def expected_transition(self, step: Step, before: Observation) -> dict[str, Any]:
        return {}

    def default_side_effect_policies(self) -> tuple[dict[str, Any], ...]:
        """Return narrow site defaults; project policy with the same id wins."""
        return ()

    @staticmethod
    def _route(url: str) -> str:
        parsed = urlparse(url)
        route = parsed.path or "/"
        if parsed.fragment:
            route = f"{route}#{parsed.fragment}"
        return route


class GenericWebCapabilityPack(SiteCapabilityPack):
    version = "2"

    def matches(self, url: str) -> bool:
        return True

    def planner_context(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> dict[str, Any]:
        """Expose a small, value-safe state contract for unfamiliar sites.

        The full bounded observation remains the source of truth. This summary
        gives a model an explicit page-state vocabulary without pretending that
        a generic site has a known business workflow.
        """
        semantic = observation.semantic_summary
        canvas = semantic.canvas if semantic is not None else {}
        return {
            "sitePack": self.site_id,
            "sitePackVersion": self.version,
            "advisoryOnly": True,
            "pageStage": self.page_stage(observation),
            "currentPageState": {
                "pageKey": semantic.page_key if semantic is not None else "",
                "route": semantic.route if semantic is not None else self._route(observation.url),
                "heading": semantic.heading if semantic is not None else observation.title,
                "dialogCount": len(semantic.dialogs) if semantic is not None else 0,
                "controlCount": len(semantic.controls) if semantic is not None else 0,
                "componentCount": len(semantic.components) if semantic is not None else 0,
                "formCount": len(semantic.forms) if semantic is not None else 0,
                "stateSignals": list(semantic.state_signals) if semantic is not None else [],
                "blockingErrors": list(semantic.blocking_errors) if semantic is not None else [],
                "canvas": {
                    "count": int(canvas.get("count") or 0),
                    "nonEmptySurface": bool(canvas.get("nonEmptySurface")),
                    "loading": bool(canvas.get("loading")),
                },
            },
            "completionRule": (
                "Complete only when every action has an independent post-action signal "
                "and each explicit expected result is proven on the current page. "
                "If no expected result is supplied, leave the run incomplete rather "
                "than infer business success from a button click."
            ),
            "remainingStages": [],
        }

    def terminal_assertions(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[Assertion]:
        """Translate only explicit scenario expectations into final checks.

        Generic pages have no trustworthy site-specific terminal state. The
        user's expected-results field is therefore the only source for a
        generic terminal assertion; unstructured expectations stay unverified.
        """
        assertions: list[Assertion] = []
        seen: set[tuple[str, str]] = set()
        for raw in getattr(scenario, "expected_results", ()) or ():
            assertion = _parse_generic_expected_result(raw)
            if assertion is None:
                continue
            key = (assertion.type.value, assertion.expected or "")
            if key in seen:
                continue
            seen.add(key)
            assertions.append(assertion)
        return assertions

    def expected_transition(self, step: Step, before: Observation) -> dict[str, Any]:
        if step.action.value == "navigate" and step.target:
            absolute = urljoin(
                before.url if before.url != "about:blank" else "http://invalid/",
                step.target,
            )
            return {"urlPathPrefix": urlparse(absolute).path or "/"}
        return {}


_GENERIC_QUOTED = re.compile(r"[\"'“”‘’]([^\"'“”‘’]+)[\"'“”‘’]")
_GENERIC_URL_EXPECTATION = re.compile(
    r"(?:url|地址)\s*(?:中|里)?\s*(?:contains|包含)\s*[:：]?\s*[\"'“”‘’]?([^\"'“”‘’\s，,。；;]+)",
    re.IGNORECASE,
)


def _parse_generic_expected_result(raw: Any) -> Assertion | None:
    """Parse the small expectation grammar accepted by generic runs."""
    text = " ".join(str(raw or "").split()).strip()
    if not text or len(text) > 500:
        return None
    url_match = _GENERIC_URL_EXPECTATION.search(text)
    if url_match:
        expected = url_match.group(1).strip()
        if expected:
            return Assertion(
                type=AssertionType.URL_CONTAINS,
                expected=expected,
                description=f"generic URL expectation: {text}",
            )

    quoted = _GENERIC_QUOTED.findall(text)
    expected = quoted[-1].strip() if quoted else ""
    if not expected:
        marker = re.search(
            r"(?:显示|看到|出现|可见|contains|contains text|visible|shows?)\s*[:：]?\s*(.+)$",
            text,
            re.IGNORECASE,
        )
        if marker:
            expected = marker.group(1).strip(" ：:，,。；;")
    if not expected or len(expected) > 200:
        return None
    return Assertion(
        type=AssertionType.TEXT_CONTAINS,
        locator=Locator(css="body"),
        expected=expected,
        description=f"generic visible expectation: {text}",
    )
