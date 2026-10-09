"""Cesium ion vertical capability pack.

This module contains site-specific workflow knowledge so the generic planner
and runner do not accumulate Cesium branches.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from ..benchmarks.cesium_ion.policy import SIDE_EFFECTS
from ..domain.models import ActionType, Assertion, AssertionType, EffectLevel, Locator, Step
from ..domain.results import Observation, StepResult
from .base import SiteCapabilityPack, WorkflowStage


CESIUM_SMOKE_STAGES = (
    WorkflowStage("authenticated", ("/", "/stories", "/assets", "/assetdepot", "/clips", "/tokens", "/usage")),
    WorkflowStage("assets", ("/assets",), "My Assets"),
    WorkflowStage("asset_depot", ("/assetdepot",), "Asset Depot"),
    WorkflowStage("clips", ("/clips",), "Clips"),
    WorkflowStage("tokens", ("/tokens",), "Access Tokens"),
    WorkflowStage("usage", ("/usage",), "Usage"),
    WorkflowStage("account", ("/account",), "Account"),
    WorkflowStage("billing", ("/account/billing",), "Billing"),
    WorkflowStage("license", ("/account/license",), "License"),
    WorkflowStage("labels", ("/account/labels",), "Labels"),
    WorkflowStage("applications", ("/account/applications",), "Authorized Applications"),
    WorkflowStage("developer", ("/account/developer",), "Developer Settings"),
    WorkflowStage("teams", ("/account/teams",), "Teams"),
)

ASSETS_SEARCH_CHECK = "assets_search_submitted"
ASSETS_SEARCH_RESTORED_CHECK = "assets_search_restored"
CESIUM_REQUIRED_STAGE_IDS = (
    "authenticated",
    "assets",
    ASSETS_SEARCH_CHECK,
    ASSETS_SEARCH_RESTORED_CHECK,
    "asset_depot",
    "clips",
    "tokens",
    "usage",
    "account",
    "billing",
    "license",
    "labels",
    "applications",
    "developer",
    "teams",
)

_ASSETS_SEARCH_FILL_DESCRIPTION = "Enter the read-only Google search term in My Assets"
_ASSETS_SEARCH_APPLY_DESCRIPTION = "Apply the My Assets search and verify the filtered result"
_ASSETS_SEARCH_CLEAR_DESCRIPTION = "Clear the My Assets search term"
_ASSETS_SEARCH_RESTORE_DESCRIPTION = "Restore the default My Assets list"

_ASSETS_SEARCH_FILL_CHECKPOINT = "cesium.assets.search.fill"
_ASSETS_SEARCH_APPLY_CHECKPOINT = "cesium.assets.search.apply"
_ASSETS_SEARCH_CLEAR_CHECKPOINT = "cesium.assets.search.clear"
_ASSETS_SEARCH_RESTORE_CHECKPOINT = "cesium.assets.search.restore"

_SPECIFIC_GOAL_TERMS = {
    "upload", "asset", "token", "story", "clip", "usage", "billing",
    "label", "team", "oauth", "delete", "create", "download", "viewer",
}

# Broad read-only scopes may use the deterministic navigation pack. Explicit
# business mutations stay with the agent planner and must never be silently
# rewritten as a smoke test.
_WRITE_GOAL_TERMS = {
    "create", "created", "creation", "upload", "uploaded", "delete", "deletion",
    "modify", "edit", "write", "import", "launch", "execute", "submit", "publish",
    "add to my assets", "model", "modeling", "simulation",
    "创建", "新增", "上传", "删除", "修改", "编辑", "写入", "导入", "启动",
    "执行", "提交", "发布", "建模", "仿真", "测试创建", "进行创建",
}
_EXPLICIT_READ_ONLY_TERMS = {
    "read-only", "readonly", "read only", "只读", "巡检", "导航检查", "smoke",
}


class CesiumIonCapabilityPack(SiteCapabilityPack):
    site_id = "cesium-ion"
    version = "2026.08.05"
    supports_auto_stable_replay = True

    def matches(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in {"ion.cesium.com", "api.cesium.com"}

    def normalize_action_payload(self, action: dict[str, Any]) -> None:
        super().normalize_action_payload(action)
        action_type = str(action.get("action") or "")
        effect_kind = str(action.get("effect_kind") or "").strip()
        # Navigation, reload, observation waits, and screenshots cannot mutate
        # Cesium data. Some compatibility gateways omit policy metadata when
        # producing these recovery actions. Classify only these intrinsically
        # read-only actions; business controls and form actions remain subject
        # to strict model classification and validation.
        if not effect_kind and action_type in {"navigate", "reload", "wait_for", "screenshot"}:
            action["effect_kind"] = "browse_search_filter_sort"
            action["effect_level"] = "read_only"
            effect_kind = "browse_search_filter_sort"
        locator = action.get("locator") if isinstance(action.get("locator"), dict) else {}
        locator_name = str(locator.get("name") or locator.get("text") or "").strip().casefold()
        locator_href = str(locator.get("href") or "").strip().casefold()
        # Entering the editor through its current-page link is navigation, not
        # a Story mutation. Compatibility gateways frequently omit metadata
        # for this otherwise fully grounded link, so classify this one narrow
        # shape without weakening unknown button handling.
        if (
            not effect_kind
            and action_type == "click"
            and (
                locator_name in {"edit story", "edit"}
                or "/stories/editor" in locator_href
            )
        ):
            action["effect_kind"] = "browse_search_filter_sort"
            action["effect_level"] = "read_only"
            effect_kind = "browse_search_filter_sort"
        # These exact Story-editor controls begin a measurement/annotation
        # write. Bind them to the same reversible-write contract as the
        # subsequent bounded Canvas gesture. Cleanup remains manual because
        # deletion always requires explicit user approval.
        if (
            not effect_kind
            and action_type == "click"
            and locator_name in {"add point", "add polygon", "add polyline"}
        ):
            action["effect_kind"] = "story_annotation_measurement"
            action["effect_level"] = "reversible_write"
            action["cleanup_action"] = (
                action.get("cleanup_action")
                or "仅在用户明确批准删除后人工移除本次测量标注"
            )
            effect_kind = "story_annotation_measurement"
        policy = SIDE_EFFECTS.get(effect_kind)
        if policy and policy["level"] in {"read_only", "session_only"}:
            action.pop("action_category", None)

        if action_type == "human_takeover":
            action["execution_mode"] = "locator"
            action["stability_level"] = "D"
            browser_target = action.get("browser_target")
            if not isinstance(browser_target, dict):
                browser_target = {}
                action["browser_target"] = browser_target
            browser_target["wait_timeout_ms"] = max(
                int(browser_target.get("wait_timeout_ms") or 0), 600_000
            )
            if not browser_target.get("url_contains") and not action.get("takeover_resume_locator"):
                action["takeover_resume_locator"] = {"test_id": "account-button-in-header"}

        if action_type == "wait_for" and action.get("value") in {"hidden", "detached"}:
            locator = action.get("locator")
            text = str(locator.get("text") or "") if isinstance(locator, dict) else ""
            if any(0xE000 <= ord(char) <= 0xF8FF for char in text):
                action["locator"] = {"css": ".page-loading-placeholder"}

    def planner_context(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> dict[str, Any]:
        visited = self._completed_stages(observation, history)
        required = self.required_stage_ids(scenario)
        context = {
            "sitePack": self.site_id,
            "sitePackVersion": self.version,
            "pageStage": self.page_stage(observation),
            "requiredStages": required,
            "visitedStages": sorted(visited),
            "remainingStages": [item for item in required if item not in visited],
            "globalNavigation": [stage.navigation_name for stage in CESIUM_SMOKE_STAGES[1:7] if stage.navigation_name],
            "accountNavigation": [stage.navigation_name for stage in CESIUM_SMOKE_STAGES[7:] if stage.navigation_name],
            "sideEffectPolicy": SIDE_EFFECTS,
            "completionRule": "Complete only when remainingStages is empty and terminal page facts are observable.",
        }
        route = self._route(observation.url).split("#", 1)[0]
        if route.startswith("/stories/editor"):
            facts = observation.accessibility_summary.lower()
            context["storyEditorContract"] = {
                "currentPageOnly": True,
                "mainCanvasLocator": {"css": "canvas"},
                "availableAnnotationControls": [
                    name for name in ("Add point", "Add polygon", "Add polyline")
                    if name.lower() in facts
                ],
                "workflow": [
                    "select the currently visible annotation or measurement control with a DOM locator",
                    "request one draw_polygon visual gesture containing every required point",
                    "verify numeric distance/angle labels or an annotation entry plus Canvas pixel delta",
                    "verify Last saved changes or remains visible after the annotation is committed",
                ],
                "speedRule": (
                    "Never request one model decision per vertex. Ground and dispatch the complete multi-point "
                    "gesture as one visual action, then perform one independent verification observation."
                ),
                "writePolicy": {
                    "effectKind": "story_annotation_measurement",
                    "effectLevel": "reversible_write",
                    "cleanup": "do not delete unless the user explicitly approves deletion",
                },
            }
        return context

    def page_stage(self, observation: Observation) -> str | None:
        route = self._route(observation.url).split("#", 1)[0]
        authenticated = self._is_authenticated(observation)
        trusted_private_page = (
            "cesium ion" in observation.title.lower()
            and not self._is_login_page(observation)
        )
        if authenticated or trusted_private_page:
            for stage in sorted(CESIUM_SMOKE_STAGES[1:], key=lambda item: len(item.route_prefixes[0]), reverse=True):
                if any(route.startswith(prefix) for prefix in stage.route_prefixes):
                    return stage.id
            if authenticated:
                return "authenticated"
        return "unauthenticated"

    def remaining_stages(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[str]:
        visited = self._completed_stages(observation, history)
        return [stage_id for stage_id in self.required_stage_ids(scenario) if stage_id not in visited]

    def required_stage_ids(self, scenario: Any) -> list[str]:
        return list(CESIUM_REQUIRED_STAGE_IDS) if self._is_generic_smoke(scenario) else []

    def completed_stage_ids(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[str]:
        required = self.required_stage_ids(scenario)
        visited = self._completed_stages(observation, history)
        return [stage_id for stage_id in required if stage_id in visited]

    def required_followup_action(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> Step | None:
        if not self._is_generic_smoke(scenario):
            return None
        completed = self._completed_stages(observation, history)
        if self.page_stage(observation) == "assets" and (
            ASSETS_SEARCH_CHECK not in completed
            or ASSETS_SEARCH_RESTORED_CHECK not in completed
        ):
            return self._asset_search_action(observation, completed)
        if self.page_stage(observation) == "usage" and not self._usage_ready(observation):
            return Step(
                action=ActionType.WAIT_FOR,
                locator=Locator(css=".loading-message"),
                value="hidden",
                description="Wait for all Cesium Usage charts to finish loading",
                state_machine_id="site_terminal_loading",
                effect_kind="browse_search_filter_sort",
                effect_level=EffectLevel.READ_ONLY,
            )
        return None

    def next_required_action(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> Step | None:
        remaining = self.remaining_stages(observation, history, scenario)
        if not remaining or remaining[0] == "authenticated":
            return None
        if remaining[0] in {ASSETS_SEARCH_CHECK, ASSETS_SEARCH_RESTORED_CHECK}:
            return self._asset_search_action(
                observation, self._completed_stages(observation, history)
            )
        stage = next(item for item in CESIUM_SMOKE_STAGES if item.id == remaining[0])
        if stage.id in {"account", "billing", "license", "labels", "applications", "developer", "teams"}:
            return Step(
                action=ActionType.NAVIGATE,
                target=stage.route_prefixes[0],
                description=f"Verify the required Cesium ion stage: {stage.id}",
                effect_kind="browse_search_filter_sort",
                effect_level=EffectLevel.READ_ONLY,
            )
        return Step(
            action=ActionType.CLICK,
            locator=Locator(role="link", name=stage.navigation_name),
            description=f"Verify the required Cesium ion stage: {stage.id}",
            effect_kind="browse_search_filter_sort",
            effect_level=EffectLevel.READ_ONLY,
        )

    def terminal_assertions(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[Assertion]:
        if not self._is_generic_smoke(scenario) or self.remaining_stages(observation, history, scenario):
            return []
        return [
            Assertion(
                type=AssertionType.URL_CONTAINS,
                expected="/account/teams",
                description="Cesium read-only workflow reached the final Teams stage",
            ),
            Assertion(
                type=AssertionType.VISIBLE,
                locator=Locator(test_id="account-button-in-header"),
                description="Authenticated Cesium global navigation remains available",
            ),
        ]

    def expected_transition(self, step: Step, before: Observation) -> dict[str, Any]:
        if step.action == ActionType.CLICK and step.locator and step.locator.name:
            stage = next(
                (item for item in CESIUM_SMOKE_STAGES if item.navigation_name == step.locator.name),
                None,
            )
            if stage and stage.route_prefixes:
                return {
                    "urlPathPrefix": stage.route_prefixes[0],
                    "headingText": stage.navigation_name,
                    "stage": stage.id,
                }
        if step.action == ActionType.NAVIGATE and step.target:
            stage = next(
                (item for item in CESIUM_SMOKE_STAGES if item.route_prefixes[0] == step.target),
                None,
            )
            if stage:
                return {
                    "urlPathPrefix": stage.route_prefixes[0],
                    "headingText": stage.navigation_name,
                    "stage": stage.id,
                }
        return {}

    def _completed_stages(self, observation: Observation, history: list[StepResult]) -> set[str]:
        visited: set[str] = set()
        observations = [item.after for item in history if item.after is not None]
        observations.append(observation)
        assets_search_seen = False
        for item in observations:
            stage = self.page_stage(item)
            if stage and stage != "unauthenticated":
                visited.add("authenticated")
                if stage != "usage" or self._usage_ready(item):
                    visited.add(stage)
            if self._asset_search_filtered(item):
                assets_search_seen = True
                visited.add(ASSETS_SEARCH_CHECK)
            elif assets_search_seen and self._asset_search_restored(item):
                visited.add(ASSETS_SEARCH_RESTORED_CHECK)
        return visited

    def _asset_search_action(
        self,
        observation: Observation,
        completed: set[str],
    ) -> Step:
        if self.page_stage(observation) != "assets":
            return Step(
                action=ActionType.CLICK,
                locator=Locator(role="link", name="My Assets"),
                description="Return to My Assets for the required search contract",
                effect_kind="browse_search_filter_sort",
                effect_level=EffectLevel.READ_ONLY,
            )
        search_locator = Locator(role="searchbox", name="Search")
        if ASSETS_SEARCH_CHECK not in completed:
            if self._searchbox_has_value(observation, "Google"):
                return Step(
                    action=ActionType.PRESS,
                    locator=search_locator,
                    value="Enter",
                    description=_ASSETS_SEARCH_APPLY_DESCRIPTION,
                    state_machine_id=_ASSETS_SEARCH_APPLY_CHECKPOINT,
                    effect_kind="browse_search_filter_sort",
                    effect_level=EffectLevel.READ_ONLY,
                )
            return Step(
                action=ActionType.FILL,
                locator=search_locator,
                value="Google",
                description=_ASSETS_SEARCH_FILL_DESCRIPTION,
                state_machine_id=_ASSETS_SEARCH_FILL_CHECKPOINT,
                effect_kind="browse_search_filter_sort",
                effect_level=EffectLevel.READ_ONLY,
            )
        if self._searchbox_has_value(observation, "Google"):
            return Step(
                action=ActionType.CLEAR,
                locator=search_locator,
                description=_ASSETS_SEARCH_CLEAR_DESCRIPTION,
                state_machine_id=_ASSETS_SEARCH_CLEAR_CHECKPOINT,
                effect_kind="browse_search_filter_sort",
                effect_level=EffectLevel.READ_ONLY,
            )
        return Step(
            action=ActionType.PRESS,
            locator=search_locator,
            value="Enter",
            description=_ASSETS_SEARCH_RESTORE_DESCRIPTION,
            state_machine_id=_ASSETS_SEARCH_RESTORE_CHECKPOINT,
            effect_kind="browse_search_filter_sort",
            effect_level=EffectLevel.READ_ONLY,
        )

    @staticmethod
    def _searchbox_has_value(observation: Observation, value: str) -> bool:
        expected = f'searchbox "Search": {value}'.lower()
        return expected in observation.accessibility_summary.lower()

    @staticmethod
    def _asset_search_filtered(observation: Observation) -> bool:
        parsed = urlparse(observation.url)
        search = parse_qs(parsed.query).get("search", [])
        facts = observation.accessibility_summary
        return (
            parsed.path.startswith("/assets")
            and search == ["Google"]
            and "Google Maps" in facts
            and "Cesium OSM Buildings" not in facts
        )

    @staticmethod
    def _asset_search_restored(observation: Observation) -> bool:
        parsed = urlparse(observation.url)
        search = parse_qs(parsed.query).get("search", [])
        facts = observation.accessibility_summary
        return (
            parsed.path.startswith("/assets")
            and not any(item.strip() for item in search)
            and "Cesium OSM Buildings" in facts
            and "assets total" in facts
        )

    @staticmethod
    def _usage_ready(observation: Observation) -> bool:
        summary = observation.semantic_summary
        if summary is not None and "loading" in summary.state_signals:
            return False
        facts = f"{observation.title}\n{observation.accessibility_summary}"
        return all(text in facts for text in ("Usage", "Data Streaming", "Imagery"))

    @staticmethod
    def _is_authenticated(observation: Observation) -> bool:
        summary = observation.semantic_summary
        if summary is not None:
            for control in summary.controls:
                if control.get("testId") == "account-button-in-header":
                    return True
        return "account-button-in-header" in "\n".join(observation.dom_summary)

    @staticmethod
    def _is_login_page(observation: Observation) -> bool:
        route = urlparse(observation.url).path.lower()
        facts = f"{observation.title}\n{observation.accessibility_summary}".lower()
        return (
            any(token in route for token in ("/login", "/signin", "/sign-in"))
            or "sign in to cesium" in facts
            or 'button "sign in"' in facts
        )

    @staticmethod
    def _is_generic_smoke(scenario: Any) -> bool:
        goal = f"{getattr(scenario, 'name', '')} {getattr(scenario, 'goal', '')}".lower()
        if any(term in goal for term in _WRITE_GOAL_TERMS):
            return False
        if any(term in goal for term in _EXPLICIT_READ_ONLY_TERMS):
            return True
        matched = {term for term in _SPECIFIC_GOAL_TERMS if term in goal}
        # URL-only starts and generated broad scopes usually name several top
        # level modules. One or two domain terms indicate a focused scenario.
        return not matched or len(matched) >= 3
