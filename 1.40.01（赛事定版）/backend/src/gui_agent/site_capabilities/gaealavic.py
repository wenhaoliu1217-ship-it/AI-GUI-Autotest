"""GAEALaViC vertical capability contract.

The generic agent is responsible for locating and executing controls.  This
adapter supplies the business vocabulary and the completion rules for the
GAEALaViC application.  It deliberately never chooses a hard-coded model,
participant, equipment path, or simulation option: those values must come
from the current observation and the scenario goal.
"""

from __future__ import annotations

import json
import hashlib
import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from ..domain.models import (
    ActionType,
    Assertion,
    AssertionType,
    EffectLevel,
    Locator,
    LocatorScope,
    Step,
)
from ..domain.results import Observation, Status, StepResult
from .intranet_intent import compile_intranet_intent
from .parameterized_form import next_parameterized_form_action
from .base import SiteCapabilityPack
from .naming import next_test_name, parse_test_name_index


GAEA_LIFECYCLE_STAGES = (
    "authenticated",
    "model_list",
    "model_wizard_step_1",
    "model_wizard_step_2",
    "model_wizard_step_3",
    "model_wizard_step_4",
    "model_created_verified",
    "model_editor_3d",
    "model_dynamics_configured",
    "model_mission_path_configured",
    "scenario_list",
    "scenario_create_form",
    "scenario_model_selection",
    "scenario_instance_configuration",
    "scenario_3d_editor",
    "scenario_path_configuration",
    "scenario_path_saved",
    "scenario_confirmation",
    "scenario_created_verified",
    "run",
    "reinforcement_learning",
)

SCENARIO_STAGE_ORDER = (
    "scenario_list",
    "scenario_create_form",
    "scenario_model_selection",
    "scenario_instance_configuration",
    "scenario_3d_editor",
    "scenario_path_configuration",
    "scenario_confirmation",
    "scenario_created_verified",
)

EXISTING_SCENARIO_STAGE_ORDER = (
    "scenario_list",
    "scenario_instance_configuration",
    "scenario_3d_editor",
    "scenario_path_configuration",
    "scenario_path_saved",
    "run",
)

WIZARD_STAGE_ORDER = (
    "model_wizard_step_1",
    "model_wizard_step_2",
    "model_wizard_step_3",
    "model_wizard_step_4",
)

# These are semantic ids, not selectors.  The page may render them as text,
# an icon tooltip, or an accessibility name.  The planner receives both the
# ids and the labels and chooses a locator from the fresh page observation.
MODEL_EDITOR_SECTIONS = (
    ("child_entities", "\u5b50\u7ea7\u5b9e\u4f53", "child entity"),
    ("formal_model", "\u5f62\u5f0f\u5316\u6a21\u578b", "formal model"),
    ("dynamics", "\u52a8\u529b\u5b66", "dynamics"),
    ("sensing_range", "\u611f\u77e5\u8303\u56f4", "sensing range"),
    ("communication_stack", "\u901a\u4fe1\u534f\u8bae\u6808", "communication protocol stack"),
    ("mission_path", "\u4efb\u52a1\u8def\u5f84", "mission path"),
    ("poi", "\u5174\u8da3\u70b9", "point of interest"),
    ("time_space_annotation", "\u65f6\u7a7a\u6807\u6ce8", "time-space annotation"),
    ("constraint_fence", "\u7ea6\u675f\u56f4\u680f", "constraint fence"),
    ("parameters", "\u53c2\u6570\u5b9a\u4e49", "parameters"),
    ("actions_commands", "\u52a8\u4f5c\u6307\u4ee4", "actions and commands"),
    ("ooda", "\u611f\u77e5\u884c\u4e3a", "OODA"),
    ("behavior_tree", "\u884c\u4e3a\u6811", "behavior tree"),
    ("doctrine", "\u6761\u4ee4", "doctrine"),
    ("cognition", "\u8ba4\u77e5\u6a21\u578b", "cognition model"),
    ("evaluation_metrics", "\u8bc4\u4f30\u6307\u6807", "evaluation metrics"),
)

EDITOR_SECTION_STAGE_IDS = {
    "dynamics": "model_dynamics_configured",
    "mission_path": "model_mission_path_configured",
}

_LOGIN_TERMS = ("\u767b\u5f55", "\u5bc6\u7801", "sign in", "log in", "login")
_FULL_TERMS = (
    "\u7aef\u5230\u7aef", "\u5168\u6d41\u7a0b", "\u95ed\u73af", "\u751f\u547d\u5468\u671f",
    "end-to-end", "lifecycle", "complete workflow",
)
_MODEL_TERMS = ("\u5efa\u6a21", "\u6a21\u578b", "\u667a\u80fd\u4f53", "model", "modeling", "agent")
_MODEL_WORKFLOW_TERMS = (
    "\u5efa\u6a21", "\u521b\u5efa\u6a21\u578b", "\u65b0\u5efa\u6a21\u578b",
    "\u521b\u5efa\u667a\u80fd\u4f53", "\u65b0\u5efa\u667a\u80fd\u4f53",
    "modeling", "create model", "new model", "create agent", "new agent",
)
_CREATE_TERMS = ("\u521b\u5efa", "\u65b0\u589e", "create", "add model", "new model")
_EDITOR_TERMS = ("3d", "\u4e09\u7ef4", "\u8def\u5f84", "\u7f16\u8f91", "canvas", "editor", "path")
_COMPLETE_EDITOR_TERMS = (
    "3d\u5efa\u6a21", "3d \u5efa\u6a21", "\u4e09\u7ef4\u5efa\u6a21",
    "\u5b8c\u65743d", "\u5b8c\u6574 3d", "complete 3d", "complete model editor",
)
_DYNAMICS_TERMS = ("\u52a8\u529b\u5b66", "dynamics", "dynamic model")
_SCENARIO_TERMS = ("\u60f3\u5b9a", "\u573a\u666f", "scenario", "plan")
_SCENARIO_SCOPE_TERMS = ("\u60f3\u5b9a", "scenario", "scenario workflow")
_SCENARIO_CREATE_TERMS = (
    "\u521b\u5efa\u60f3\u5b9a", "\u65b0\u5efa\u60f3\u5b9a", "\u65b0\u589e\u60f3\u5b9a",
    "create scenario", "new scenario", "add scenario",
)
_SCENARIO_PATH_TERMS = (
    "\u8def\u5f84", "\u822a\u70b9", "\u8f68\u8ff9", "path", "waypoint", "route",
)
_SCENARIO_INSTANCE_TERMS = (
    "\u5b9e\u4f8b", "\u88c5\u5907", "instance", "equipment",
)
_RUN_TERMS = ("\u8fd0\u884c", "\u4eff\u771f", "simulation", "run")
_REAL_RUN_TERMS = (
    "\u542f\u52a8\u4eff\u771f", "\u8fd0\u884c\u4eff\u771f", "\u505c\u6b62\u4eff\u771f",
    "\u5b9e\u4f53\u79fb\u52a8", "\u5207\u6362 x50", "\u5207\u6362x50",
    "start simulation", "startsimulation", "stop simulation", "entity movement",
)
_TRAIN_TERMS = ("\u5f3a\u5316\u5b66\u4e60", "\u8bad\u7ec3", "reinforcement", "training", "train")


class GAEALaViCCapabilityPack(SiteCapabilityPack):
    SIDE_EFFECT_POLICY_VERSION = "2026.08.21.4"
    site_id = "gaealavic"
    version = "2026.08.26.1"
    supports_auto_stable_replay = False
    supports_terminal_state_completion = True

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return (parsed.hostname or "").lower() == "192.168.31.218" and parsed.port in {None, 7991}

    def normalize_action_payload(self, action: dict[str, Any]) -> None:
        """Keep user-requested names visible while retaining the ledger guard.

        ``Step`` validation intentionally remains site-neutral and requires the
        internal E2E namespace for side effects. Only this vertical adapter
        creates that opaque alias; it must never leak into a page field.
        """
        super().normalize_action_payload(action)
        # Historical component locators are reference material only. Runtime
        # grounding must resolve the current page instead of rewriting the
        # model's action to a site-specific CSS selector.
        allow_reference_locator_rewrite = False
        component = action.get("component")
        if allow_reference_locator_rewrite and action.get("action") == "component" and isinstance(component, dict):
            semantic_target = " ".join(
                str(value or "")
                for value in (
                    component.get("semanticTarget"),
                    component.get("semantic_target"),
                    component.get("expectedText"),
                    action.get("description"),
                )
            ).lower()
            locators = component.get("locators")
            values = component.get("values")
            is_simulation_model_select = (
                component.get("kind") == "searchable_select"
                and any(
                    term in semantic_target
                    for term in ("仿真模型", "simulation model")
                )
            )
            if is_simulation_model_select and isinstance(values, list) and values:
                # Ant Select exposes both an accessibility option and a virtual
                # visual option with the same accessible name. Use the model's
                # observed value, but constrain the click to the one visible
                # option layer so strict uniqueness remains meaningful.
                action["action"] = "click"
                action["locator"] = {
                    "css": self._visible_ant_option_selector(str(values[0]))
                }
                action.pop("component", None)
                action.pop("component_adapter_id", None)
                action.pop("value", None)
                action.pop("value_from_secret", None)
            elif is_simulation_model_select and (
                not isinstance(locators, list)
                or len(locators) < 3
                or not isinstance(values, list)
                or not values
            ) and any(
                term in semantic_target for term in ("仿真模型", "simulation model")
            ):
                # A closed select has no option value yet, so it cannot satisfy
                # the searchable_select contract. Preserve the model's intent
                # to open it as one ordinary click; the next observation lets
                # the model choose from the options that actually appeared.
                action["action"] = "click"
                action["locator"] = {"css": "dialog[open] .ant-select-selector"}
                action.pop("component", None)
                action.pop("component_adapter_id", None)
                action.pop("value", None)
                action.pop("value_from_secret", None)
        locator = action.get("locator")
        if allow_reference_locator_rewrite and action.get("action") == "click" and isinstance(locator, dict):
            descriptor = " ".join(
                str(locator.get(key) or "")
                for key in ("name", "label", "placeholder", "text")
            ).lower()
            if locator.get("role") == "combobox" and any(
                term in descriptor for term in ("仿真模型", "simulation model")
            ):
                # The accessible combobox is a transparent input underneath
                # Ant Design's visible selector shell. Click the visible shell,
                # then re-observe its dynamic options before choosing one.
                action["locator"] = {"css": "dialog[open] .ant-select-selector"}
                action.pop("value", None)
                action.pop("value_from_secret", None)
            elif locator.get("role") == "option" and str(locator.get("name") or "").strip():
                action["locator"] = {
                    "css": self._visible_ant_option_selector(str(locator["name"]))
                }
                action.pop("value", None)
                action.pop("value_from_secret", None)
        resource_name = str(action.get("resource_name") or "").strip()
        supplied_alias = str(action.get("business_object_name") or "").strip()
        if not resource_name and re.fullmatch(r"E2E_test_[A-Z]+", supplied_alias):
            # Backward compatibility for plans created before opaque aliases.
            resource_name = supplied_alias.removeprefix("E2E_")

        if resource_name:
            internal_alias = self.internal_ledger_name(resource_name)
            visible_value = str(action.get("value") or "").strip()
            if visible_value in {supplied_alias, internal_alias} or (
                re.fullmatch(r"E2E_test_[A-Z]+", visible_value)
                and visible_value.removeprefix("E2E_") == resource_name
            ):
                action["value"] = resource_name
            action["resource_name"] = resource_name
            action["business_object_name"] = internal_alias
            return

    @staticmethod
    def _visible_ant_option_selector(option_text: str) -> str:
        encoded = json.dumps(option_text.strip(), ensure_ascii=False)
        return (
            ".ant-select-dropdown:not(.ant-select-dropdown-hidden) "
            f".ant-select-item-option:has-text({encoded})"
        )

    def effective_business_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """Reconcile legacy E2E_ wording with user-controlled visible names."""
        effective = deepcopy(context)
        existing_naming = effective.get("resourceNaming")
        if (
            isinstance(existing_naming, dict)
            and existing_naming.get("internalLedgerPattern") == r"^E2E_GAEALAVIC_[A-F0-9]{24}$"
        ):
            return effective
        description = str(effective.get("description") or "").strip()
        description = re.sub(
            r"\bE2E_\s*resources\b",
            "user-requested visible resources tracked by opaque internal ledger aliases",
            description,
            flags=re.IGNORECASE,
        )
        effective["description"] = (
            description
            + (" " if description else "")
            + "GAEALaViC browser-visible resource names follow the current user's explicit naming requirement. "
              "E2E_GAEALAVIC_* identifiers are opaque internal side-effect-ledger aliases only."
        )

        boundary_key = "operatingBoundaries" if "operatingBoundaries" in effective else "operating_boundaries"
        boundaries = [str(item) for item in effective.get(boundary_key, [])]
        boundaries = [item for item in boundaries if not self._legacy_visible_name_boundary(item)]
        boundaries.extend([
            "Browser-visible model search, keyword, name and confirmation fields must follow the current user's naming requirement exactly.",
            "The test_A, test_B ... sequence is used only when the current user explicitly requests that sequence.",
            "Internal E2E_GAEALAVIC_* ledger aliases must never be typed into or searched in the target website.",
            "Unknown resources and resources not owned by the test ledger must never be overwritten or deleted.",
        ])
        effective[boundary_key] = list(dict.fromkeys(boundaries))

        examples_key = "exampleGoals" if "exampleGoals" in effective else "example_goals"
        examples = []
        for item in effective.get(examples_key, []):
            value = str(item)
            if "E2E_" in value:
                value = (
                    "Create and clean up a resource using the current user's visible naming requirement after explicit confirmation; "
                    "track it with an opaque internal ledger alias."
                )
            examples.append(value)
        effective[examples_key] = list(dict.fromkeys(examples))

        facts = []
        for item in effective.get("facts", []):
            fact = deepcopy(item) if isinstance(item, dict) else item
            if isinstance(fact, dict) and str(fact.get("id")) == "gaealavic.e2e_boundary":
                fact["statement"] = (
                    "GAEALaViC visible resource names follow the current user's explicit requirement. "
                    "Only the internal side-effect ledger uses an opaque E2E_GAEALAVIC_* alias; "
                    "unknown resources must not be deleted."
                )
                fact["source"] = "GAEALaViC capability pack 2026.08.11.2"
            facts.append(fact)
        effective["facts"] = facts
        allowed_key = "allowedActions" if "allowedActions" in effective else "allowed_actions"
        effective[allowed_key] = [
            {
                "create_e2e_resource": "create_ledger_tracked_resource",
                "delete_ledger_owned_e2e_resource": "delete_ledger_owned_resource",
            }.get(str(item), str(item))
            for item in effective.get(allowed_key, [])
        ]
        commerce_profile = effective.get("commerceProfile")
        if isinstance(commerce_profile, dict) and not commerce_profile.get("enabled"):
            commerce_profile = deepcopy(commerce_profile)
            commerce_profile.pop("e2eResourcePrefix", None)
            effective["commerceProfile"] = commerce_profile
        effective["resourceNaming"] = {
            "mode": "user_requirement",
            "pageVisibleRule": "follow the current scenario's explicit user naming requirement",
            "testSequenceRule": "use test_A, test_B ... only when explicitly requested by the current user",
            "internalLedgerPattern": r"^E2E_GAEALAVIC_[A-F0-9]{24}$",
            "pageInputRule": "use the user-requested visible name for every browser fill, search, select and confirmation value",
            "ledgerRule": "use the opaque internal alias only as business_object_name and side-effect evidence",
        }
        return effective

    def default_side_effect_policies(self) -> tuple[dict[str, Any], ...]:
        rollback = "Find the exact user-named model recorded by this ledger entry, remove only that object, and verify zero exact matches."
        update_verification = (
            "Never delete the existing model as cleanup. Capture pre-save evidence, "
            "save only the explicitly named test model, then independently reopen or "
            "re-observe it and verify the requested value persisted. If the outcome is "
            "unknown, stop without replaying the save."
        )
        return (
            {
                "id": "gaealavic_create_test_model",
                "actionCategory": "create",
                "objectType": "model",
                "namePattern": r"^E2E_GAEALAVIC_[A-F0-9]{24}$",
                "role": "tester",
                "decision": "conditional",
                "rollbackRule": rollback,
                "policyVersion": self.SIDE_EFFECT_POLICY_VERSION,
            },
            {
                "id": "gaealavic_update_test_model",
                "actionCategory": "update",
                "objectType": "model",
                "namePattern": r"^E2E_GAEALAVIC_[A-F0-9]{24}$",
                "role": "tester",
                "decision": "allow",
                "rollbackRule": update_verification,
                "policyVersion": self.SIDE_EFFECT_POLICY_VERSION,
            },
        )

    def planner_context(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> dict[str, Any]:
        required = self.required_stage_ids(scenario)
        completed = self.completed_stage_ids(observation, history, scenario)
        semantic = observation.semantic_summary
        components = semantic.components if semantic else []
        wizard = semantic.wizard if semantic else {}
        canvas = semantic.canvas if semantic else {}
        observed_names = self._observed_test_names(observation, history)
        observed_sections = self._observed_editor_sections(observation)
        exercised_sections = self._exercised_editor_sections(history)
        required_editor_sections = self._required_editor_section_ids(scenario)
        completed_editor_sections = self._completed_editor_section_ids(history)
        remaining_editor_sections = [
            section_id
            for section_id in required_editor_sections
            if section_id not in completed_editor_sections
        ]
        resource_name_contract = self._resource_name_contract(
            scenario, observed_names, observation, history
        )
        scenario_contract = self._scenario_contract(
            scenario, observation, history
        )
        run_contract = self._run_contract(scenario, observation, history)
        return {
            "sitePack": self.site_id,
            "sitePackVersion": self.version,
            "pageStage": self.page_stage(observation),
            "requiredStages": required,
            "visitedStages": completed,
            "remainingStages": self.remaining_stages(observation, history, scenario),
            "businessChain": ["model", "instance", "scenario", "run", "trainingConfig"],
            "objectRelations": {
                "model": "has_instance",
                "instance": "is_used_by_scenario",
                "scenario": "creates_run",
                "run": "provides_state_for_trainingConfig",
            },
            "wizardContract": {
                "steps": list(WIZARD_STAGE_ORDER),
                "rule": "re-observe wizard, components and visible options after every transition; choose only a currently visible legal option",
                "writeGate": "creation is a side effect and requires the configured confirmation policy",
            },
            "knownFailurePatterns": [
                {
                    "id": "model-wizard-existing-simulation-select-v1",
                    "status": "confirmed_regression_guard",
                    "advisoryOnly": True,
                    "trigger": (
                        "current observation shows an expanded searchable_select for 选择已有仿真模型, "
                        "selectedText is empty, visibleOptions is non-empty, and 下一步 is disabled"
                    ),
                    "rootCause": (
                        "a wizard transition that introduced the selector was previously misclassified as an "
                        "add-row action, so repeatableFormContract rejected the next Agent action"
                    ),
                    "recovery": (
                        "choose exactly one enabled option that is present in the latest visibleOptions using its "
                        "current runtimeId or a complete searchable_select component action; never reuse a prior option"
                    ),
                    "verification": (
                        "recapture the page and require selectedText to become non-empty and the wizard state or "
                        "下一步 enabled state to change before advancing"
                    ),
                    "forbiddenShortcut": (
                        "do not click 下一步 while disabled and do not classify a selector introduced by 下一步 as a repeatable row"
                    ),
                },
                {
                    "id": "visual-focus-structured-fill-mode-v1",
                    "status": "confirmed_regression_guard",
                    "advisoryOnly": True,
                    "trigger": (
                        "the current screenshot shows an input, DOM/accessibility data is empty, the Agent first "
                        "uses a visual click, and the next fill/select/click contains both a locator and visual metadata"
                    ),
                    "rootCause": (
                        "visual focus metadata leaked into an ordinary structured action, causing the runner to "
                        "treat text entry as a visual coordinate action"
                    ),
                    "recovery": (
                        "use the visual click only to focus; demote the following action to locator mode, fill or "
                        "select through the current locator, then recapture and verify the value and validation state"
                    ),
                    "verification": (
                        "the current input value or selected text is observed after the action and the next page "
                        "state is independently confirmed"
                    ),
                    "forbiddenShortcut": (
                        "do not fill text by visual coordinates and do not reuse a coordinate from a prior screenshot"
                    ),
                },
                {
                    "id": "model-editor-fatal-rendering-stopped-v1",
                    "status": "confirmed_regression_guard",
                    "advisoryOnly": True,
                    "trigger": (
                        "the latest visible page reports 'An error occurred while rendering. Rendering has stopped.' "
                        "or a client-side exception and exposes a TypeError/ReferenceError detail"
                    ),
                    "rootCause": (
                        "the target application entered a fatal client rendering state even though some surrounding "
                        "DOM controls remained interactive"
                    ),
                    "recovery": (
                        "stop all further writes, preserve the current screenshot, DOM/accessibility facts, console and "
                        "network evidence, and report a target-application runtime defect"
                    ),
                    "verification": (
                        "a fresh observation no longer contains the fatal banner and the 3D surface is independently "
                        "verified before any later write is considered"
                    ),
                    "forbiddenShortcut": (
                        "do not dismiss the error and continue saving merely because the configuration panel is clickable"
                    ),
                },
                {
                    "id": "existing-model-update-cleanup-contract-v1",
                    "status": "confirmed_regression_guard",
                    "advisoryOnly": True,
                    "trigger": (
                        "the current authorized goal modifies and saves an existing exact test model without creating it"
                    ),
                    "rootCause": (
                        "an update was previously evaluated with the create-resource deletion cleanup requirement"
                    ),
                    "recovery": (
                        "treat the action as a reversible update, never delete the existing model as cleanup, and stop "
                        "without replay when the save outcome is unknown"
                    ),
                    "verification": (
                        "re-observe or reopen the exact model and require the requested value to persist with no field error"
                    ),
                    "forbiddenShortcut": (
                        "do not convert an existing-model update into create/delete cleanup semantics"
                    ),
                },
                {
                    "id": "existing-scenario-jump-to-model-v1",
                    "status": "confirmed_regression_guard",
                    "advisoryOnly": False,
                    "trigger": (
                        "the current goal edits or runs an existing exact scenario on situationPage and an instance menu "
                        "offers \u8df3\u8f6c\u5230\u4eff\u771f\u6a21\u578b"
                    ),
                    "rootCause": (
                        "following that menu leaves the scenario workflow for agentEditPage and turns path association "
                        "into an unintended model-editing workflow"
                    ),
                    "recovery": (
                        "stay on situationPage, close the instance menu, and use only the current scenario's instance, "
                        "task-editing, path-management and run-mode controls"
                    ),
                    "verification": (
                        "the latest route still contains situationPage and the exact scenario name before any later write or run action"
                    ),
                    "forbiddenShortcut": (
                        "never click \u8df3\u8f6c\u5230\u4eff\u771f\u6a21\u578b and never enter agentEditPage while completing an existing-scenario workflow"
                    ),
                },
            ],
            "scenarioContract": scenario_contract,
            "runContract": run_contract,
            "resourceNameContract": resource_name_contract,
            # Compatibility alias for previously recorded plans. Its content
            # is now user-driven and is no longer an unconditional test_* rule.
            "testNameContract": resource_name_contract,
            "observedComponents": components[:40],
            "observedWizard": wizard,
            "observedCanvas": canvas,
            "modelEditorContract": {
                "sections": [
                    {"id": section_id, "label": label, "english": english}
                    for section_id, label, english in MODEL_EDITOR_SECTIONS
                ],
                "observedSections": observed_sections,
                "exercisedSections": exercised_sections,
                "requiredSections": required_editor_sections,
                "completedSections": completed_editor_sections,
                "remainingSections": remaining_editor_sections,
                "nextRequiredSection": (
                    remaining_editor_sections[0] if remaining_editor_sections else None
                ),
                "flowOrder": ["dynamics", "mission_path"],
                "rule": (
                    "Complete one editor section at a time from a fresh screenshot, DOM and accessibility observation. "
                    "A section is not complete merely because its panel opened or a field was filled: click the panel's "
                    "current Save button, re-observe the result, and reopen or inspect the saved child entry before advancing."
                ),
                "excludedDetailTests": ["actions_commands.parameter_configuration"],
                "dynamicsRule": (
                    "when dynamics is required, open the plus control beside the current Dynamics row, inspect the newly "
                    "visible panel and its current legal options, fill every current required field, save once, then verify "
                    "the saved dynamics child/configuration is present before opening Mission Path; never reuse a recorded coordinate. "
                    "do not click Cancel before Save. On this site, after Save has been attempted and the page has been allowed to process, click Cancel on "
                    "that same Dynamics panel to close it; Save followed by that same-panel Cancel is the confirmed completion sequence."
                ),
                "missionPathRule": (
                    "after dynamics is saved, add exactly one mission-path definition and fill the editable path-point keyword "
                    "with the current browser-visible resource name plus _path. Inspect General Parameters, Path Point Settings, "
                    "Path Generation and Natural-language Description from fresh page state; fill only current required controls "
                    "and preserve legal defaults when no reliable value is required. Scenario waypoint drawing is validated "
                    "later from Scenario after selecting an instance. Click Save once, re-observe validation and "
                    "reopen or inspect the saved path child to prove persistence before leaving the editor. "
                    "If Save produces no observable response because of the site behavior, click Cancel on this same mission-path panel "
                    "as a narrow fallback after the path keyword and Path Point Settings evidence are present; do not use this fallback "
                    "for any other editor section."
                ),
                "saveGate": (
                    "submit each required editor panel with Save first. Never use Cancel before that Save attempt. "
                    "For Dynamics and Mission Path, allow the site to process after Save and then click Cancel on that same panel "
                    "to close it. Cancel without an earlier same-section Save never completes a section. Mission Path additionally "
                    "requires the path keyword and Path Point Settings evidence before Save."
                ),
            },
            "threeDContract": {
                "preferred": "GAEALaViC semantic bridge when explicitly enabled",
                "fallback": "DOM/accessibility first, then visual action with before/after evidence",
                "required": ["scene ready", "non-empty canvas", "independent state change"],
                "unknown": "block when no semantic bridge or independently verifiable visual change is available",
                "scenarioRules": {
                    "coordinatePolicy": "derive relative coordinates from the current canvas bounds; never reuse absolute screen coordinates",
                    "pathEvidence": "require a fresh canvas observation plus a DOM/Bridge path-count or waypoint state when available",
                    "visualOnlyLimit": "a pixel change alone proves interaction, not that the intended business instance or path was saved",
                    "realSiteCalibration": "unverified until an authenticated real-site run confirms the business postconditions",
                },
            },
        }

    def _scenario_contract(
        self,
        scenario: Any,
        observation: Observation,
        history: list[StepResult],
    ) -> dict[str, Any]:
        instruction = self._scenario_instruction_text(scenario)
        name = self._requested_scenario_name(scenario, instruction)
        operation = self._scenario_operation(scenario, name)
        required = set(self.required_stage_ids(scenario))
        stage_source = (
            SCENARIO_STAGE_ORDER
            if operation == "create_scenario"
            else EXISTING_SCENARIO_STAGE_ORDER
        )
        stage_order = [stage for stage in stage_source if stage in required]
        completed = set(self.completed_stage_ids(observation, history, scenario))
        rules = [
            "Use only controls and options present in the latest observation; capability hints never choose a fixed option.",
            "Re-observe after opening every custom selector or Portal overlay before choosing an option.",
            "Canvas interaction uses current relative bounds and requires an independent instance/path postcondition.",
        ]
        if operation == "create_scenario":
            rules.extend([
                "The selected model and instance must remain visible in the current form or structured bridge state before entering 3D editing.",
                "Only the final Save/Create action is a persistent write and it requires current-run creation authorization.",
                "After the final write, return to the scenario list and verify the exact visibleName; search input text is never existence proof.",
            ])
        else:
            rules.extend([
                "Search the exact existing scenario at most once, open its Modify action, and remain on situationPage.",
                "In Instance Configuration, a card surface is inventory, not a verification control. Use the card's current Add/Join action only when the goal explicitly requires another instance; otherwise verify the configured instance tree and continue.",
                "Never click \u8df3\u8f6c\u5230\u4eff\u771f\u6a21\u578b and never enter agentEditPage; existing scenario path work belongs to the task-editing/path-management controls on situationPage.",
                "Save a scenario path from its current path editor and verify the saved path remains visible before entering Run mode.",
            ])
            if self._requires_real_run(scenario):
                rules.append(
                    "This goal explicitly requests a simulation run: require the ordered Start -> Running -> speed/movement -> Stop -> Unstart evidence; a screenshot or unchanged Start button is not success."
                )
            else:
                rules.append(
                    "When the goal only asks to enter Run mode, a fresh Run-mode observation with the visible Start control is the terminal checkpoint; do not click Start."
                )
        return {
            "verificationStatus": "offline_contract_unverified_on_real_site",
            "operation": operation,
            "currentStage": self.page_stage(observation),
            "stageOrder": stage_order,
            "completedStages": [stage for stage in stage_order if stage in completed],
            "remainingStages": [stage for stage in stage_order if stage not in completed],
            "visibleName": name,
            "internalLedgerName": self.internal_ledger_name(f"scenario:{name}") if name else None,
            "forbiddenActions": (
                ["\u8df3\u8f6c\u5230\u4eff\u771f\u6a21\u578b", "navigate to agentEditPage", "create model", "create scenario"]
                if operation == "update_existing_scenario"
                else []
            ),
            "rules": rules,
            "unverifiedRealSiteFacts": [
                "exact scenario form field labels and route names",
                "model-to-instance option dependencies",
                "3D instance placement and path business-state bridge",
                "save response business id and cleanup endpoint",
            ],
        }

    def _run_contract(
        self,
        scenario: Any,
        observation: Observation,
        history: list[StepResult],
    ) -> dict[str, Any]:
        requested = self._has_any(self._positive_scope_text(scenario), _RUN_TERMS)
        evidence = self._run_workflow_evidence(observation, history)
        evidence_order = self._run_required_evidence(scenario)
        next_action = next(
            (name for name in evidence_order if not evidence[name]),
            "complete",
        )
        return {
            "enabled": requested,
            "currentState": self._run_state(observation),
            "requiredEvidenceOrder": evidence_order,
            "evidence": evidence,
            "nextRequiredEvidence": next_action,
            "rules": [
                "Enter Run mode only from the existing scenario's situationPage; never use agentEditPage.",
                (
                    "The goal requests a real simulation: verify Start -> Running -> x50/movement -> Stop -> Unstart in order; a failed start request is a failed run."
                    if self._requires_real_run(scenario)
                    else "The terminal checkpoint is a fresh Run-mode observation with the visible Start control (启动); do not click Start."
                ),
            ],
        }

    @classmethod
    def _requires_real_run(cls, scenario: Any) -> bool:
        return cls._has_any(cls._positive_scope_text(scenario), _REAL_RUN_TERMS)

    @classmethod
    def _run_required_evidence(cls, scenario: Any) -> list[str]:
        if cls._requires_real_run(scenario):
            return [
                "runModeReady",
                "startClicked",
                "runningObserved",
                "x50Selected",
                "movementObserved",
                "stopClicked",
                "stoppedObserved",
            ]
        return ["runModeReady"]

    def _scenario_operation(self, scenario: Any, name: str | None = None) -> str:
        goal = self._positive_scope_text(scenario)
        requested_name = name or self._requested_scenario_name(
            scenario, self._scenario_instruction_text(scenario)
        )
        create_requested = self._has_any(goal, _SCENARIO_CREATE_TERMS) or (
            self._has_any(goal, _CREATE_TERMS)
            and self._has_any(goal, _SCENARIO_SCOPE_TERMS)
        )
        if create_requested:
            return "create_scenario"
        if requested_name:
            return "update_existing_scenario"
        return "unspecified"

    @staticmethod
    def _requested_path_keyword(scenario: Any) -> str | None:
        text = GAEALaViCCapabilityPack._scenario_instruction_text(scenario)
        match = re.search(
            r"\b(?:test_[A-Z]+_path|[A-Za-z][A-Za-z0-9_]{1,119}_path)\b",
            text,
        )
        return match.group(0) if match else None

    @staticmethod
    def internal_ledger_name(resource_name: str) -> str:
        normalized = str(resource_name).strip()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24].upper()
        return f"E2E_GAEALAVIC_{digest}"

    def _resource_name_contract(
        self,
        scenario: Any,
        observed_test_names: list[str],
        observation: Observation,
        history: list[StepResult],
    ) -> dict[str, Any]:
        instruction = self._scenario_instruction_text(scenario)
        current_goal = str(getattr(scenario, "goal", "") or "").strip()
        exact_name = self._requested_exact_name(scenario, instruction)
        requested_keyword = self._requested_keyword(scenario, instruction)
        sequence_requested = self._requests_test_sequence(current_goal)
        creation_requested = self._has_any(
            self._positive_scope_text(scenario), _CREATE_TERMS
        )
        allocated_sequence_name = self._locked_sequence_name_from_history(history)
        next_sequence_name = allocated_sequence_name or next_test_name(observed_test_names)

        if sequence_requested:
            mode = "test_sequence"
            visible_name = next_sequence_name
            source = "explicit_user_sequence"
            rule = (
                "The current user explicitly requested the test_A, test_B ... sequence. "
                "Scan test_* results and pagination, choose the first free sequence name, "
                "then exact-search that browser-visible name immediately before creation."
            )
        elif exact_name:
            mode = "exact_user_name"
            visible_name = exact_name
            source = "explicit_user_name"
            rule = (
                "Use the exact browser-visible resource name explicitly requested by the current user. "
                + (
                    "Exact-search it immediately before creation."
                    if creation_requested
                    else "Search it at most once, open only that existing resource, and never allocate another name."
                )
            )
        else:
            mode = "user_defined"
            visible_name = None
            source = "current_user_goal"
            rule = (
                "Derive the browser-visible name from the current user's goal. If the goal contains no naming "
                "requirement, ask one naming clarification before the final write; never substitute a fixed test_* or E2E_* name."
            )

        operation = (
            "open_existing_resource"
            if mode == "exact_user_name" and not creation_requested
            else "create_resource" if visible_name else "unspecified"
        )
        allocation_state = (
            self._existing_resource_target_state(
                visible_name, observed_test_names, observation, history
            )
            if operation == "open_existing_resource"
            else self._resource_allocation_state(
                visible_name, observed_test_names, observation, history
            )
        )
        return {
            "mode": mode,
            "source": source,
            "operation": operation,
            "visibleName": visible_name,
            "internalLedgerName": self.internal_ledger_name(visible_name) if visible_name else None,
            "keyword": requested_keyword or (self._browser_keyword(visible_name) if visible_name else None),
            "observedTestSequenceNames": observed_test_names,
            "nextTestSequenceName": next_sequence_name if sequence_requested else None,
            "sequence": "test_A through test_Z, then test_AA, test_AB ..." if sequence_requested else None,
            "internalLedgerPattern": r"^E2E_GAEALAVIC_[A-F0-9]{24}$",
            "pageValueRule": "Every website search and form value uses visibleName; never use internalLedgerName in the browser.",
            "keywordRule": "The key-information field uses keyword, which contains only ASCII letters and underscore; the later resource-name field uses visibleName exactly.",
            "allocationState": allocation_state,
            "clarificationRule": "Never ask the user to add an E2E_ prefix; the internal alias mapping is already authorized.",
            "rule": (
                rule
                + " Once allocationState.lockedName is set, it is immutable unless the exact conflict check proves it occupied."
                + " A search-box value is never evidence that a resource exists."
                + " When conflictCheckStatus=available, do not search again; follow allocationState.nextRequiredBusinessAction from the latest page."
                + " When operation=open_existing_resource, never allocate a replacement name; search lockedName at most once and open only that target."
                + " Never overwrite or delete an occupied or unknown name."
            ),
        }

    @staticmethod
    def _locked_sequence_name_from_history(history: list[StepResult]) -> str | None:
        """Keep the first zero-result exact-search name immutable for this run."""
        search_terms = ("search", "searchbox", "搜索", "精确", "conflict")
        for item in history:
            if item.action != "fill" or item.status.value != "passed" or item.after is None:
                continue
            text = " ".join(filter(None, [
                item.description or "",
                item.target_summary or "",
                item.planner_reason or "",
            ]))
            lowered = text.lower()
            if not any(term in lowered for term in search_terms):
                continue
            names = re.findall(r"\btest_[A-Z]+\b", text)
            if not names:
                continue
            candidate = names[-1]
            semantic = item.after.semantic_summary
            if semantic is None or "loading" in semantic.state_signals:
                continue
            occupied = {
                str(name).strip()
                for name in semantic.resource_names
                if re.fullmatch(r"test_[A-Z]+", str(name).strip())
            }
            if candidate not in occupied:
                return candidate
        return None

    @staticmethod
    def _resource_allocation_state(
        visible_name: str | None,
        observed_test_names: list[str],
        observation: Observation,
        history: list[StepResult],
    ) -> dict[str, Any]:
        if not visible_name:
            return {
                "lockedName": None,
                "conflictCheckStatus": "not_applicable",
                "exactSearchAttempts": 0,
                "searchAllowed": False,
                "nextRequiredBusinessAction": "ask_for_name",
            }
        search_terms = ("search", "searchbox", "搜索", "精确", "未占用", "conflict")
        attempts = 0
        for item in history:
            if item.action != "fill":
                continue
            text = " ".join(filter(None, [
                item.description or "",
                item.target_summary or "",
                item.planner_reason or "",
            ])).lower()
            if visible_name.lower() in text and any(term in text for term in search_terms):
                attempts += 1

        semantic = observation.semantic_summary
        current_names = {
            str(name).strip()
            for name in (semantic.resource_names if semantic else [])
            if re.fullmatch(r"test_[A-Z]+", str(name).strip())
        }
        # A confirmation page may render the pending form name as ordinary
        # text. It is not list evidence that the resource already exists.
        if semantic and bool(semantic.wizard.get("visible")):
            current_names = set()
        loading = bool(semantic and "loading" in semantic.state_signals)
        created_name = GAEALaViCCapabilityPack._created_name_from_history(history)
        if created_name == visible_name:
            status = "created_verified" if visible_name in current_names else "created_pending_verification"
            next_action = "finish" if status == "created_verified" else "search_created_name_once"
            search_allowed = status != "created_verified"
        elif attempts == 0:
            status = "not_started"
            next_action = "exact_search_locked_name_once"
            search_allowed = True
        elif loading:
            status = "loading"
            next_action = "wait_for_loading_to_finish"
            search_allowed = False
        elif visible_name in current_names:
            status = "occupied"
            next_action = "allocate_next_name"
            search_allowed = False
        else:
            status = "available"
            wizard_visible = bool(semantic and semantic.wizard.get("visible"))
            if wizard_visible:
                current_stage = GAEALaViCCapabilityPack._wizard_stage(observation)
                next_action = (
                    "submit_locked_resource"
                    if current_stage == "model_wizard_step_4"
                    else "continue_current_wizard"
                )
            else:
                next_action = "open_create_wizard"
            search_allowed = False
        return {
            "lockedName": visible_name,
            "observedOccupiedNames": observed_test_names,
            "conflictCheckStatus": status,
            "exactSearchAttempts": attempts,
            "searchAllowed": search_allowed,
            "nextRequiredBusinessAction": next_action,
        }

    @staticmethod
    def _existing_resource_target_state(
        visible_name: str,
        observed_test_names: list[str],
        observation: Observation,
        history: list[StepResult],
    ) -> dict[str, Any]:
        search_terms = ("search", "searchbox", "搜索", "精确", "查找")
        attempts = 0
        target_open = False
        for item in history:
            text = " ".join(filter(None, [
                item.description or "",
                item.target_summary or "",
                item.planner_reason or "",
            ]))
            lowered = text.lower()
            if (
                item.action == "fill"
                and visible_name.lower() in lowered
                and any(term in lowered for term in search_terms)
            ):
                attempts += 1
            after_url = item.after.url.lower() if item.after else ""
            if (
                item.status.value == "passed"
                and visible_name.lower() in lowered
                and ("agentedit" in after_url or "agenteditpage" in after_url)
            ):
                target_open = True

        semantic = observation.semantic_summary
        current_names = {
            str(name).strip()
            for name in (semantic.resource_names if semantic else [])
        }
        loading = bool(semantic and "loading" in semantic.state_signals)
        if target_open:
            status = "target_open"
            next_action = "continue_existing_resource_workflow"
            search_allowed = False
        elif attempts == 0:
            status = "not_started"
            next_action = "exact_search_existing_name_once"
            search_allowed = True
        elif loading:
            status = "loading"
            next_action = "wait_for_loading_to_finish"
            search_allowed = False
        elif visible_name in current_names:
            status = "target_visible"
            next_action = "open_existing_resource"
            search_allowed = False
        else:
            status = "target_not_found"
            next_action = "report_exact_target_not_found"
            search_allowed = False
        return {
            "lockedName": visible_name,
            "observedOccupiedNames": observed_test_names,
            "conflictCheckStatus": status,
            "exactSearchAttempts": attempts,
            "searchAllowed": search_allowed,
            "nextRequiredBusinessAction": next_action,
        }

    @staticmethod
    def _scenario_instruction_text(scenario: Any) -> str:
        values = [getattr(scenario, "name", ""), getattr(scenario, "goal", "")]
        test_data = getattr(scenario, "test_data", {})
        if isinstance(test_data, dict):
            values.append(json.dumps(test_data, ensure_ascii=False))
        return "\n".join(str(value) for value in values if value)

    @classmethod
    def _requested_exact_name(cls, scenario: Any, instruction: str) -> str | None:
        # The latest user goal is authoritative.  Frontends can retain stale
        # testData from an earlier run (for example modelName=test_I while the
        # newly entered goal explicitly says to open test_A).  Never let that
        # cached form value override the current run target.
        goal = str(getattr(scenario, "goal", "") or "").strip()
        if goal and not cls._requests_test_sequence(goal):
            compiled = compile_intranet_intent(scenario)
            if compiled.object_kind == "model" and compiled.target_name:
                return compiled.target_name
            explicit_goal_name = cls._labeled_requested_name(goal)
            if explicit_goal_name:
                return explicit_goal_name
            goal_test_names = list(dict.fromkeys(
                re.findall(r"\btest_[A-Z]+\b", goal)
            ))
            if len(goal_test_names) == 1:
                return goal_test_names[0]

        test_data = getattr(scenario, "test_data", {})
        if isinstance(test_data, dict):
            for key in (
                "resourceName", "resource_name", "modelName", "model_name",
                "agentName", "agent_name", "智能体名称", "模型名称", "资源名称",
            ):
                candidate = cls._valid_requested_name(test_data.get(key))
                if candidate:
                    return candidate

        return cls._labeled_requested_name(instruction)

    @classmethod
    def _labeled_requested_name(cls, instruction: str) -> str | None:
        quoted = re.search(
            r"(?:智能体名称|模型名称|资源名称|创建名称|命名为|agent name|model name|resource name)"
            r"\s*(?:为|是|使用|[:：=])?\s*[\"'“]([^\"'”\r\n]{1,120})[\"'”]",
            instruction,
            flags=re.IGNORECASE,
        )
        if quoted:
            candidate = cls._valid_requested_name(quoted.group(1))
            if candidate:
                return candidate

        plain = re.search(
            r"(?:智能体名称|模型名称|资源名称|创建名称|命名为|agent name|model name|resource name)"
            r"\s*(?:为|是|使用|[:：=])\s*([A-Za-z0-9_\-\u4e00-\u9fff（）()]{1,120})",
            instruction,
            flags=re.IGNORECASE,
        )
        return cls._valid_requested_name(plain.group(1)) if plain else None

    @classmethod
    def _requested_keyword(cls, scenario: Any, instruction: str) -> str | None:
        test_data = getattr(scenario, "test_data", {})
        if isinstance(test_data, dict):
            for key in ("agentKeyword", "agent_keyword", "keyword", "智能体关键字"):
                candidate = str(test_data.get(key) or "").strip()
                if re.fullmatch(r"[A-Za-z_]{1,120}", candidate):
                    return candidate
        match = re.search(
            r"(?:智能体关键字|agent keyword)\s*(?:为|是|使用|[:：=])\s*[\"'“]?([A-Za-z_]{1,120})[\"'”]?",
            instruction,
            flags=re.IGNORECASE,
        )
        return match.group(1) if match else None

    @classmethod
    def _requested_scenario_name(cls, scenario: Any, instruction: str) -> str | None:
        goal = str(getattr(scenario, "goal", "") or "").strip()
        if goal:
            compiled = compile_intranet_intent(scenario)
            if compiled.object_kind == "scenario" and compiled.target_name:
                return compiled.target_name
            quoted_goal = re.search(
                r"(?:\u60f3\u5b9a\u540d\u79f0|\u573a\u666f\u540d\u79f0|scenario name|plan name|\u60f3\u5b9a\u547d\u540d\u4e3a|\u573a\u666f\u547d\u540d\u4e3a)"
                r"\s*(?:\u4e3a|\u662f|\u4f7f\u7528|[:\uff1a=])?\s*[\"'\u201c]([^\"'\u201d\r\n]{1,120})[\"'\u201d]",
                goal,
                flags=re.IGNORECASE,
            )
            if quoted_goal:
                candidate = cls._valid_requested_name(quoted_goal.group(1))
                if candidate:
                    return candidate
            goal_test_names = list(dict.fromkeys(
                re.findall(r"\btest_[A-Z]+\b", goal)
            ))
            if len(goal_test_names) == 1 and cls._has_any(
                goal, _SCENARIO_SCOPE_TERMS
            ):
                return goal_test_names[0]

        test_data = getattr(scenario, "test_data", {})
        if isinstance(test_data, dict):
            for key in (
                "scenarioName", "scenario_name", "planName", "plan_name",
                "想定名称", "场景名称",
            ):
                candidate = cls._valid_requested_name(test_data.get(key))
                if candidate:
                    return candidate
        quoted = re.search(
            r"(?:想定名称|场景名称|scenario name|plan name|想定命名为|场景命名为)"
            r"\s*(?:为|是|使用|[:：=])?\s*[\"'“]([^\"'”\r\n]{1,120})[\"'”]",
            instruction,
            flags=re.IGNORECASE,
        )
        if quoted:
            return cls._valid_requested_name(quoted.group(1))
        plain = re.search(
            r"(?:想定名称|场景名称|scenario name|plan name|想定命名为|场景命名为)"
            r"\s*(?:为|是|使用|[:：=])\s*([A-Za-z0-9_\-\u4e00-\u9fff（）()]{1,120})",
            instruction,
            flags=re.IGNORECASE,
        )
        return cls._valid_requested_name(plain.group(1)) if plain else None

    @staticmethod
    def _browser_keyword(resource_name: str) -> str:
        if re.fullmatch(r"[A-Za-z_]{1,120}", resource_name):
            return resource_name
        digest = hashlib.sha256(resource_name.encode("utf-8")).hexdigest()[:12]
        letters_only = digest.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))
        return f"agent_{letters_only}"

    @staticmethod
    def _valid_requested_name(value: Any) -> str | None:
        candidate = str(value or "").strip()
        if not candidate or len(candidate) > 120:
            return None
        instruction_terms = ("本次", "扫描", "第一个", "未占用", "规则", "前缀", "自动", "current", "first", "available")
        if any(term in candidate.lower() for term in instruction_terms):
            return None
        return candidate

    @staticmethod
    def _requests_test_sequence(instruction: str) -> bool:
        lowered = instruction.lower()
        for clause in re.split(r"[\n\r。！？；;]+", lowered):
            names = set(re.findall(r"\btest_[a-z]+\b", clause))
            mentions_test_names = bool(names or "test_*" in clause)
            if not mentions_test_names:
                continue
            allocation_terms = (
                "test_z", "test_aa", "test_*", "第一个未占用", "首个未占用",
                "下一个未占用", "first available", "next available",
            )
            if any(term in clause for term in allocation_terms):
                return True
            ordering = any(term in clause for term in ("依次", "递增", "顺序", "sequence"))
            naming_action = any(term in clause for term in (
                "选择", "命名", "名称", "分配", "创建", "name", "allocate", "create",
            ))
            if ordering and naming_action and (len(names) > 1 or "test_*" in clause):
                return True
        return False

    def page_stage(self, observation: Observation) -> str | None:
        route = self._route(observation.url).lower()
        if "login" in route or self._looks_like_login(observation):
            return "unauthenticated"
        if "situationpage" in route:
            if "type=run" in route or self._run_state(observation) in {
                "starting", "running", "stopping", "stopped_ready",
            }:
                return "run"
            return self._scenario_stage(observation) or "scenario_3d_editor"
        if "agenteditpage" in route or "agentedit" in route:
            return "model_editor_3d"
        if "minemodellist" in route or "model" in route:
            wizard = observation.semantic_summary.wizard if observation.semantic_summary else {}
            if wizard.get("visible"):
                return self._wizard_stage(observation)
            return "model_list"
        if self._has_any(route, ("scenario", "plan", "mineplan")):
            return self._scenario_stage(observation) or "scenario_list"
        if self._has_any(route, ("run", "record", "simulation")):
            return "run"
        if self._has_any(route, ("reinforcement", "learning", "train")):
            return "reinforcement_learning"
        heading = observation.semantic_summary.heading.lower() if observation.semantic_summary else ""
        if self._has_any(heading, ("\u5efa\u6a21", "modeling")):
            return "model_list"
        if self._has_any(heading, _SCENARIO_TERMS):
            return self._scenario_stage(observation) or "scenario_list"
        if self._has_any(heading, _RUN_TERMS):
            return "run"
        if self._has_any(heading, _TRAIN_TERMS):
            return "reinforcement_learning"
        if self._looks_authenticated(observation):
            return "authenticated"
        return None

    def required_stage_ids(self, scenario: Any) -> list[str]:
        goal = self._positive_scope_text(scenario)
        full_scope = self._has_any(goal, _FULL_TERMS)
        scenario_scope = self._has_any(goal, _SCENARIO_SCOPE_TERMS)
        model_scope = self._has_any(goal, _MODEL_WORKFLOW_TERMS) or (
            self._has_any(goal, _MODEL_TERMS) and not scenario_scope
        )
        other_scope = model_scope or any(
            self._has_any(goal, terms) for terms in (_RUN_TERMS, _TRAIN_TERMS)
        )
        if full_scope and (not scenario_scope or other_scope):
            return list(GAEA_LIFECYCLE_STAGES)
        selected = ["authenticated"]
        if model_scope:
            selected.append("model_list")
            if self._has_any(goal, _CREATE_TERMS):
                selected.extend(WIZARD_STAGE_ORDER)
                selected.append("model_created_verified")
            if self._has_any(goal, _EDITOR_TERMS):
                selected.append("model_editor_3d")
                selected.extend(
                    EDITOR_SECTION_STAGE_IDS[section_id]
                    for section_id in self._required_editor_section_ids(scenario)
                )
        if scenario_scope:
            selected.append("scenario_list")
            scenario_name = self._requested_scenario_name(
                scenario, self._scenario_instruction_text(scenario)
            )
            scenario_operation = self._scenario_operation(scenario, scenario_name)
            scenario_workflow_requested = (
                full_scope
                or self._has_any(goal, _SCENARIO_CREATE_TERMS)
                or self._has_any(goal, _CREATE_TERMS)
                or self._has_any(goal, _EDITOR_TERMS)
                or self._has_any(goal, _SCENARIO_PATH_TERMS)
            )
            if scenario_workflow_requested:
                if scenario_operation == "create_scenario":
                    selected.extend(SCENARIO_STAGE_ORDER[1:])
                else:
                    if self._has_any(goal, _SCENARIO_INSTANCE_TERMS):
                        selected.append("scenario_instance_configuration")
                    selected.append("scenario_3d_editor")
                    if self._has_any(goal, _SCENARIO_PATH_TERMS):
                        selected.extend([
                            "scenario_path_configuration",
                            "scenario_path_saved",
                        ])
        if self._has_any(goal, _RUN_TERMS):
            selected.append("run")
        if self._has_any(goal, _TRAIN_TERMS):
            selected.append("reinforcement_learning")
        return list(dict.fromkeys(selected))

    def completed_stage_ids(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[str]:
        required = self.required_stage_ids(scenario)
        existing_scenario = self._scenario_operation(scenario) == "update_existing_scenario"
        stages: set[str] = set()
        observations = [item.after for item in history if item.after is not None]
        observations.append(observation)
        for item in observations:
            stage = self.page_stage(item)
            if stage and stage != "unauthenticated":
                stages.add("authenticated")
            if stage in WIZARD_STAGE_ORDER:
                stages.add("model_list")
                stages.update(WIZARD_STAGE_ORDER[: WIZARD_STAGE_ORDER.index(stage) + 1])
            if stage == "model_editor_3d":
                # Reaching the route is not enough: the 3D surface must be
                # ready and non-empty so a blank editor cannot close a run.
                if self._editor_ready(item):
                    stages.add(stage)
            elif stage in SCENARIO_STAGE_ORDER:
                if existing_scenario:
                    stages.add("scenario_list")
                    stages.add(stage)
                else:
                    stages.update(SCENARIO_STAGE_ORDER[: SCENARIO_STAGE_ORDER.index(stage) + 1])
            elif stage == "run":
                # Merely entering Run mode is not completion. The ordered
                # Start/Running/x50/movement/Stop/Unstart evidence below owns it.
                pass
            elif stage:
                stages.add(stage)
        if self._created_resource_verified(observation, history, scenario):
            stages.add("model_created_verified")
        for section_id in self._completed_editor_section_ids(history):
            stage_id = EDITOR_SECTION_STAGE_IDS.get(section_id)
            if stage_id:
                stages.add(stage_id)
        stages.update(self._completed_scenario_stages(observation, history, scenario))
        if self._scenario_path_saved(history):
            stages.add("scenario_path_saved")
        run_evidence = self._run_workflow_evidence(observation, history)
        if all(
            run_evidence.get(key, False)
            for key in self._run_required_evidence(scenario)
        ):
            stages.add("run")
        return [stage for stage in required if stage in stages]

    def remaining_stages(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[str]:
        required = self.required_stage_ids(scenario)
        completed = set(self.completed_stage_ids(observation, history, scenario))
        return [stage for stage in required if stage not in completed]

    def next_required_action(self, observation: Observation, history: list[StepResult], scenario: Any) -> Step | None:
        """Accelerate parameterized list routing without screenshots or coordinates.

        This intentionally handles only fresh, unique semantic evidence. Simple
        native forms may be filled and explicitly authorized create/save actions
        may be submitted; delete is never emitted here. Start/stop is allowed
        only after an exact scenario binding and still passes side-effect policy.
        """

        intent = compile_intranet_intent(scenario)
        target = intent.target_name
        if not target:
            return None
        stage = self.page_stage(observation)
        form_action = next_parameterized_form_action(
            observation,
            history,
            scenario,
            intent,
            stage=stage,
            internal_name=self.internal_ledger_name,
        )
        if form_action is not None:
            return form_action
        if not intent.safe_for_local_routing:
            return None
        desired_stage = "scenario_list" if intent.object_kind == "scenario" else "model_list"

        if stage == "authenticated":
            label = "想定" if intent.object_kind == "scenario" else "建模"
            control = self._unique_semantic_control(observation, roles={"menuitem", "link"}, name=label)
            if control:
                return Step(
                    action=ActionType.CLICK,
                    locator=self._locator_from_control(control, fallback=Locator(role="menuitem", name=label)),
                    description=f"进入{label}列表以查找 {target}",
                    effect_level=EffectLevel.READ_ONLY,
                )
            return None

        if stage == "run" and intent.object_kind == "scenario":
            if not self._target_bound_to_current_workflow(observation, history, target):
                return None
            state = self._run_state(observation)
            evidence = self._run_workflow_evidence(observation, history)
            if "start" in intent.operations and state == "stopped_ready" and not evidence["startClicked"]:
                start = self._unique_semantic_control(
                    observation, roles={"button"}, name="启动"
                )
                if start:
                    return Step(
                        action=ActionType.CLICK,
                        locator=self._locator_from_control(
                            start, fallback=Locator(role="button", name="启动")
                        ),
                        description=f"启动已精确绑定的想定 {target}",
                        effect_level=EffectLevel.REVERSIBLE_WRITE,
                        effect_kind="start_simulation",
                        action_category="start_simulation",
                        object_type="scenario",
                        business_object_name=self.internal_ledger_name(f"scenario:{target}"),
                        resource_name=target,
                    )
            can_stop = not (
                "start" in intent.operations
                and self._requires_real_run(scenario)
                and not evidence["movementObserved"]
            )
            if "stop" in intent.operations and state == "running" and can_stop:
                stop = self._unique_semantic_control(
                    observation, roles={"button"}, name="停止"
                )
                if stop:
                    return Step(
                        action=ActionType.CLICK,
                        locator=self._locator_from_control(
                            stop, fallback=Locator(role="button", name="停止")
                        ),
                        description=f"停止已精确绑定的想定 {target}",
                        effect_level=EffectLevel.REVERSIBLE_WRITE,
                        effect_kind="stop_simulation",
                        action_category="stop_simulation",
                        object_type="scenario",
                        business_object_name=self.internal_ledger_name(f"scenario:{target}"),
                        resource_name=target,
                    )
            return None

        if stage != desired_stage:
            return None
        search_name = "搜索想定名称" if intent.object_kind == "scenario" else "搜索模型名称"
        search = self._unique_semantic_control(
            observation,
            roles={"textbox", "searchbox"},
            name=search_name,
            allow_contains=True,
        )
        if search is None:
            search = self._unique_resource_search_control(observation, intent.object_kind)
        searched = self._successful_parameterized_search(history, target)
        if search and str(search.get("valueState") or "") == "empty" and not searched:
            observed_search_name = str(search.get("name") or search_name)
            observed_search_role = str(search.get("role") or "textbox")
            return Step(
                action=ActionType.FILL,
                locator=self._locator_from_control(
                    search,
                    fallback=Locator(role=observed_search_role, name=observed_search_name),
                ),
                value=target,
                description=f"按本次任务参数精确搜索 {target}",
                effect_level=EffectLevel.SESSION_ONLY,
            )

        if not searched and not self._target_visible(observation, target):
            return None
        if not self._target_visible(observation, target):
            return None
        return Step(
            action=ActionType.CLICK,
            locator=Locator(
                role="button",
                name="修改",
                scope=LocatorScope(
                    kind="card",
                    locator=Locator(text=target),
                    identity=target,
                ),
            ),
            description=f"在名称为 {target} 的唯一卡片内打开修改入口",
            effect_level=EffectLevel.READ_ONLY,
            business_object_name=target,
            object_type=intent.object_kind,
        )

    @staticmethod
    def _unique_semantic_control(
        observation: Observation,
        *,
        roles: set[str],
        name: str,
        allow_contains: bool = False,
    ) -> dict[str, str | bool] | None:
        semantic = observation.semantic_summary
        if semantic is None:
            return None
        matches = []
        for control in semantic.controls:
            role = str(control.get("role") or "").casefold()
            actual_name = str(control.get("name") or "").strip()
            name_matches = actual_name == name or (allow_contains and name in actual_name)
            if role in roles and name_matches and not bool(control.get("disabled")):
                matches.append(control)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _locator_from_control(
        control: dict[str, str | bool], *, fallback: Locator
    ) -> Locator:
        runtime_id = str(control.get("runtimeId") or control.get("runtime_id") or "")
        if re.fullmatch(r"ai_[0-9]+", runtime_id):
            return fallback.model_copy(update={"runtime_id": runtime_id})
        return fallback

    @staticmethod
    def _unique_resource_search_control(
        observation: Observation, object_kind: str
    ) -> dict[str, str | bool] | None:
        semantic = observation.semantic_summary
        if semantic is None:
            return None
        kind_terms = ("想定", "场景") if object_kind == "scenario" else ("模型", "智能体", "建模")
        matches = []
        for control in semantic.controls:
            role = str(control.get("role") or "").casefold()
            name = str(control.get("name") or "").strip()
            is_search = "搜索" in name or "search" in name.casefold()
            if (
                role in {"textbox", "searchbox"}
                and is_search
                and any(term in name for term in kind_terms)
                and not bool(control.get("disabled"))
            ):
                matches.append(control)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _successful_parameterized_search(history: list[StepResult], target: str) -> bool:
        for item in reversed(history):
            if item.status != Status.PASSED or item.action != ActionType.FILL.value:
                continue
            description = " ".join((item.description or "", item.target_summary or ""))
            if target in description and ("搜索" in description or "search" in description.casefold()):
                return True
        return False

    @staticmethod
    def _target_visible(observation: Observation, target: str) -> bool:
        semantic = observation.semantic_summary
        if semantic and target in semantic.resource_names:
            return True
        bounded_text = "\n".join(
            [observation.accessibility_summary, *observation.dom_summary]
        )
        return target in bounded_text

    @classmethod
    def _target_bound_to_current_workflow(
        cls,
        observation: Observation,
        history: list[StepResult],
        target: str,
    ) -> bool:
        if cls._target_visible(observation, target):
            return True
        for item in reversed(history):
            if item.status != Status.PASSED:
                continue
            context = " ".join((item.description or "", item.target_summary or ""))
            if target in context and ("唯一卡片" in context or "精确绑定" in context):
                return True
        return False

    def terminal_assertions(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[Assertion]:
        required = self.required_stage_ids(scenario)
        run_evidence = self._run_workflow_evidence(observation, history)
        if "run" in required and all(
            run_evidence.get(key, False)
            for key in self._run_required_evidence(scenario)
        ):
            return [
                Assertion(
                    type=AssertionType.URL_CONTAINS,
                    expected="situationPage",
                    description="existing scenario reached situationPage in Run mode",
                ),
                Assertion(
                    type=AssertionType.TEXT_CONTAINS,
                    locator=Locator(css="body"),
                    expected="启动",
                    description="Start control is visible in Run mode",
                ),
            ]
        if "scenario_path_saved" in required and self._scenario_path_saved(history):
            path_keyword = self._requested_path_keyword(scenario) or "任务路径"
            return [
                Assertion(
                    type=AssertionType.URL_CONTAINS,
                    expected="situationPage",
                    description="scenario path was saved while remaining on the existing scenario page",
                ),
                Assertion(
                    type=AssertionType.TEXT_CONTAINS,
                    locator=Locator(css="body"),
                    expected=path_keyword,
                    description=f"saved scenario path keyword {path_keyword} remains visible",
                ),
            ]
        if "scenario_created_verified" in required:
            visible_name = self._requested_scenario_name(
                scenario, self._scenario_instruction_text(scenario)
            )
            if not visible_name or not self._scenario_created_verified(
                observation, history, scenario
            ):
                return []
            return [
                Assertion(
                    type=AssertionType.TEXT_CONTAINS,
                    locator=Locator(css="body"),
                    expected=visible_name,
                    description="created scenario is present in the returned scenario list",
                )
            ]
        if "model_created_verified" in required:
            visible_name = self._created_name_from_history(history) or str(
                self._resource_name_contract(
                    scenario,
                    list(observation.semantic_summary.resource_names if observation.semantic_summary else []),
                    observation,
                    history,
                ).get("visibleName") or ""
            ).strip()
            if not self._created_resource_verified(observation, history, scenario) or not visible_name:
                return []
            return [
                Assertion(
                    type=AssertionType.TEXT_CONTAINS,
                    locator=Locator(css="body"),
                    expected=visible_name,
                    description="created model is present in the returned model list",
                )
            ]
        if "model_editor_3d" in required and self.page_stage(observation) == "model_editor_3d":
            return [
                Assertion(type=AssertionType.PAGE_REACHED, expected="agentEditPage", description="model editor route reached"),
                Assertion(type=AssertionType.VISIBLE, locator=Locator(css="canvas"), description="3D canvas is visible"),
            ]
        return []

    @staticmethod
    def _run_state(observation: Observation) -> str:
        route = GAEALaViCCapabilityPack._route(observation.url).lower()
        text = GAEALaViCCapabilityPack._observation_text(observation).lower()
        if "正在停止仿真" in text or "stopping simulation" in text:
            return "stopping"
        if "正在启动仿真" in text or "starting simulation" in text:
            return "starting"
        if re.search(r"(?:simulationstatus|simulation_status)=(?:running|run)", route):
            return "running"
        if re.search(r"(?:simulationstatus|simulation_status)=(?:unstart|stopped|stop)", route):
            return "stopped_ready"
        if GAEALaViCCapabilityPack._has_any(text, ("暂停", "pause")) and GAEALaViCCapabilityPack._has_any(text, ("停止", "stop")):
            return "running"
        if GAEALaViCCapabilityPack._has_any(text, ("启动", "start")) and "situationpage" in route:
            return "stopped_ready"
        return "unknown"

    @staticmethod
    def _action_context(item: StepResult) -> str:
        return " ".join(filter(None, [
            item.description or "",
            item.target_summary or "",
            item.planner_reason or "",
        ])).lower()

    @classmethod
    def _run_workflow_evidence(
        cls,
        observation: Observation,
        history: list[StepResult],
    ) -> dict[str, bool]:
        run_mode_ready = False
        start_clicked = False
        running_observed = False
        x50_selected = False
        movement_observed = False
        stop_clicked = False
        stopped_observed = False
        x50_index: int | None = None
        stop_index: int | None = None
        motion_fingerprints: list[str] = []
        pre_x50_fingerprints: list[str] = []
        post_x50_fingerprints: list[str] = []
        current_route = cls._route(observation.url).lower()
        current_state = cls._run_state(observation)
        current_text = cls._observation_text(observation)
        # ``simulationStatus=Unstart`` is also present in edit mode.  The
        # explicit route mode is therefore the only reliable signal that the
        # user actually entered Run mode; status alone would complete too
        # early on the edit page where the same Start control is shown.
        if "situationpage" in current_route and "type=run" in current_route:
            run_mode_ready = True
        if current_state == "running":
            running_observed = True
        if cls._has_any(current_text, ("x50", "50倍", "50x")):
            x50_selected = True
        observations = [item.after for item in history if item.after is not None]
        observations.append(observation)
        for item in history:
            context = cls._action_context(item)
            after = item.after
            if after is not None:
                after_route = cls._route(after.url).lower()
                if "situationpage" in after_route and "type=run" in after_route:
                    run_mode_ready = True
            if item.action in {"click", "press", "visual_click"} and cls._has_any(context, ("启动仿真", "启动", "start simulation", "start")) and not cls._has_any(context, ("停止", "stop")):
                start_clicked = True
            if after is not None and cls._run_state(after) == "running":
                running_observed = True
            if cls._has_any(context, ("x50", "50倍", "50x")) or (
                after is not None and cls._has_any(cls._observation_text(after), ("x50", "50倍", "50x"))
            ):
                x50_selected = True
                x50_index = item.index if x50_index is None else x50_index
            if item.action in {"click", "press", "visual_click"} and cls._has_any(context, ("停止仿真", "停止", "stop simulation", "stop")):
                stop_clicked = True
                stop_index = item.index if stop_index is None else stop_index
            if after is not None and stop_index is not None and item.index > stop_index and cls._run_state(after) == "stopped_ready":
                stopped_observed = True
            if after is not None and x50_index is not None and item.index >= x50_index and cls._run_state(after) == "running":
                semantic = after.semantic_summary
                state_text = " ".join([
                    after.accessibility_summary or "",
                    " ".join(semantic.state_signals if semantic else []),
                    " ".join(str(value) for value in (semantic.headings if semantic else [])),
                ])
                fingerprint = re.sub(r"\s+", " ", state_text).strip()
                if fingerprint:
                    motion_fingerprints.append(fingerprint)
                    if item.index < x50_index:
                        pre_x50_fingerprints.append(fingerprint)
                    else:
                        post_x50_fingerprints.append(fingerprint)
        if x50_index is not None:
            for item in history:
                if item.after is None or cls._run_state(item.after) != "running":
                    continue
                semantic = item.after.semantic_summary
                state_text = " ".join([
                    item.after.accessibility_summary or "",
                    " ".join(semantic.state_signals if semantic else []),
                    " ".join(str(value) for value in (semantic.headings if semantic else [])),
                ])
                fingerprint = re.sub(r"\s+", " ", state_text).strip()
                if not fingerprint:
                    continue
                if item.index < x50_index:
                    pre_x50_fingerprints.append(fingerprint)
                else:
                    post_x50_fingerprints.append(fingerprint)
        if len(set(motion_fingerprints)) >= 2 or (
            pre_x50_fingerprints and post_x50_fingerprints
            and set(pre_x50_fingerprints) != set(post_x50_fingerprints)
        ):
            movement_observed = True
        # A changed semantic signature is an independent fallback when the
        # application exposes only canvas/map state and no position text.
        if not movement_observed and x50_index is not None:
            signatures = [
                item.after.semantic_summary.signature
                for item in history
                if item.after is not None
                and item.index >= x50_index
                and cls._run_state(item.after) == "running"
                and item.after.semantic_summary is not None
                and item.after.semantic_summary.signature
            ]
            before_signatures = [
                item.after.semantic_summary.signature
                for item in history
                if item.after is not None
                and item.index < x50_index
                and cls._run_state(item.after) == "running"
                and item.after.semantic_summary is not None
                and item.after.semantic_summary.signature
            ]
            movement_observed = len(set(signatures)) >= 2 or (
                before_signatures and signatures
                and set(before_signatures) != set(signatures)
            )
        return {
            "runModeReady": run_mode_ready,
            "startClicked": start_clicked,
            "runningObserved": running_observed,
            "x50Selected": x50_selected,
            "movementObserved": movement_observed,
            "stopClicked": stop_clicked,
            "stoppedObserved": stopped_observed,
        }

    @classmethod
    def _scenario_path_saved(
        cls,
        history: list[StepResult],
    ) -> bool:
        save_index: int | None = None
        save_blocked = False
        path_seen = False
        path_keywords: set[str] = set()
        for item in history:
            context = cls._action_context(item)
            discovered_paths = re.findall(
                r"\b(?:[a-z][a-z0-9-]{1,119}_path|test_[a-z]+_path)\b",
                context,
                flags=re.IGNORECASE,
            )
            path_keywords.update(discovered_paths)
            if cls._has_any(context, ("任务路径", "path management", "路径管理", "mission path", "waypoint")) or discovered_paths:
                path_seen = True
            save_action = (
                item.action in {"click", "press", "visual_click"}
                and cls._has_any(context, ("保存", "save"))
                and path_seen
            )
            if save_action:
                if item.status.value not in {"passed", "incomplete"}:
                    # Do not let a stale earlier Save attempt authorize a
                    # later Cancel after a failed action.
                    save_index = None
                    save_blocked = True
                    continue
                save_index = item.index
                save_blocked = cls._save_attempt_has_blocking_failure(item)
                # A structured business proof is sufficient on its own. The
                # target site historically required closing the panel after
                # Save, so retain that compatibility path below, but do not
                # require a Cancel when the save response is independently
                # observable.
                verification = item.verification_evidence or {}
                if (
                    not save_blocked
                    and verification.get("status") == "passed"
                    and "business_state_verified" in {
                        str(value) for value in verification.get("facts", [])
                    }
                ):
                    return True
                continue
            if (
                save_index is not None
                and item.index > save_index
                and not save_blocked
                and item.action in {"click", "press", "visual_click"}
                and cls._has_any(context, ("取消", "cancel"))
            ):
                return True
            if save_index is None or item.index <= save_index:
                continue
            # A fresh wait/screenshot/inspect step is an acceptable generic
            # save verification when the latest observation exposes the path
            # keyword (or an explicit saved notification) and no validation
            # error remains. This keeps the rule portable beyond test_D_path.
            if item.status.value != "passed" or item.after is None:
                continue
            verification_text = " ".join((context, cls._observation_text(item.after))).lower()
            keyword_seen = any(keyword.lower() in verification_text for keyword in path_keywords)
            semantic = item.after.semantic_summary
            saved_signal = bool(
                semantic
                and any(
                    signal in {"saved", "save_success", "notification_visible"}
                    for signal in semantic.state_signals
                )
            )
            no_validation_error = not bool(
                semantic
                and (
                    semantic.blocking_errors
                    or any(
                        isinstance(control, dict)
                        and (
                            control.get("invalid") is True
                            or str(control.get("validationMessage") or "").strip()
                        )
                        for control in semantic.controls
                    )
                )
            )
            if (keyword_seen or saved_signal) and no_validation_error:
                return True
        return False

    @staticmethod
    def _save_attempt_has_blocking_failure(item: StepResult) -> bool:
        """Identify failures that must not be rescued by the site's Cancel quirk."""
        verification = dict(item.verification_evidence or {})
        if str(verification.get("status") or "").lower() == "failed":
            return True
        facts = {str(value).lower() for value in verification.get("facts", [])}
        if any(
            fact.startswith(("runtime_failure:", "validation_failure:", "control_state_mismatch:"))
            for fact in facts
        ):
            return True
        category = getattr(item.failure_category, "value", item.failure_category)
        if str(category or "").lower() in {"business_state", "navigation", "assertion", "security"}:
            return True
        after = item.after
        if after is None:
            return False
        if after.page_errors:
            return True
        return any(
            re.search(
                r"\bHTTP\s+5\d\d\b|ERR_(?:CONNECTION|NAME_NOT_RESOLVED|TIMED_OUT|FAILED|INTERNET_DISCONNECTED|NETWORK_CHANGED|RESET)",
                str(raw),
                re.IGNORECASE,
            )
            for raw in after.failed_requests
        )

    def _scenario_stage(self, observation: Observation) -> str | None:
        route = self._route(observation.url).lower()
        if not self._has_any(route, ("scenario", "plan", "mineplan", "situationpage")):
            return None
        semantic = observation.semantic_summary
        wizard = semantic.wizard if semantic else {}
        canvas = semantic.canvas if semantic else {}
        text = self._observation_text(observation).lower()
        visible_control_text = " ".join(
            str(item.get("name") or "")
            for item in (semantic.controls if semantic else [])
            if isinstance(item, dict)
        ).lower()
        visible_heading_text = " ".join(
            [
                str(semantic.heading if semantic else ""),
                *(str(item) for item in (semantic.headings if semantic else [])),
            ]
        ).lower()
        visible_component_text = " ".join(
            " ".join(filter(None, [
                str(item.get("label") or ""),
                str(item.get("placeholder") or ""),
                str(item.get("selectedText") or ""),
            ]))
            for item in (semantic.components if semantic else [])
            if isinstance(item, dict)
        ).lower()
        wizard_text = str(wizard.get("text") or "").lower()
        visible_page_text = " ".join(
            [visible_heading_text, visible_control_text, visible_component_text, wizard_text]
        )
        bridge_path_state = any(
            key in canvas
            for key in ("pathCount", "waypointCount", "activePath", "selectedPath")
        )
        if self._has_any(
            " ".join([visible_heading_text, wizard_text]),
            ("确认创建", "确认保存", "提交想定", "confirm scenario", "review and create"),
        ):
            return "scenario_confirmation"
        if (
            self._editor_ready(observation)
            and (
                self._has_any(visible_control_text, _SCENARIO_PATH_TERMS)
                or self._has_any(wizard_text, _SCENARIO_PATH_TERMS)
                or bridge_path_state
            )
        ):
            return "scenario_path_configuration"
        if self._has_any(visible_page_text, ("实例配置", "添加实例", "实例名称", "instance configuration", "add instance")):
            return "scenario_instance_configuration"
        if self._editor_ready(observation) or bool(canvas.get("semanticBridge")):
            return "scenario_3d_editor"
        if self._has_any(visible_page_text, ("选择模型", "仿真模型", "选择实例", "select model", "existing model")):
            return "scenario_model_selection"
        if wizard.get("visible") or self._has_any(
            " ".join([visible_heading_text, wizard_text, visible_component_text]),
            ("想定名称", "场景名称", "基本信息", "create scenario", "scenario name"),
        ):
            return "scenario_create_form"
        return "scenario_list"

    def _completed_scenario_stages(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> list[str]:
        completed: set[str] = set()
        existing_scenario = self._scenario_operation(scenario) == "update_existing_scenario"
        observations = [item.after for item in history if item.after is not None]
        observations.append(observation)
        for item in observations:
            stage = self._scenario_stage(item)
            if stage in SCENARIO_STAGE_ORDER:
                if existing_scenario:
                    completed.add("scenario_list")
                    completed.add(stage)
                else:
                    completed.update(SCENARIO_STAGE_ORDER[: SCENARIO_STAGE_ORDER.index(stage) + 1])
        if self._scenario_created_verified(observation, history, scenario):
            completed.add("scenario_created_verified")
        return [stage for stage in SCENARIO_STAGE_ORDER if stage in completed]

    def _scenario_created_verified(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> bool:
        if self._scenario_stage(observation) != "scenario_list":
            return False
        visible_name = self._requested_scenario_name(
            scenario, self._scenario_instruction_text(scenario)
        )
        semantic = observation.semantic_summary
        if not visible_name or semantic is None or visible_name not in semantic.resource_names:
            return False
        return any(
            item.action in {"click", "press"}
            and (
                item.status.value == "passed"
                or (
                    item.status.value == "incomplete"
                    and item.progress_assessment == "pending_business_verification"
                )
            )
            and self._has_any(
                " ".join(filter(None, [
                    item.description or "",
                    item.target_summary or "",
                    item.planner_reason or "",
                ])).lower(),
                ("创建想定", "保存想定", "确认创建", "create scenario", "save scenario"),
            )
            for item in history
        )

    def _created_resource_verified(
        self,
        observation: Observation,
        history: list[StepResult],
        scenario: Any,
    ) -> bool:
        if self.page_stage(observation) != "model_list":
            return False
        visible_name = self._created_name_from_history(history) or str(
            self._resource_name_contract(
                scenario,
                list(observation.semantic_summary.resource_names if observation.semantic_summary else []),
                observation,
                history,
            ).get("visibleName") or ""
        ).strip()
        if not visible_name or observation.semantic_summary is None:
            return False
        if visible_name not in observation.semantic_summary.resource_names:
            return False
        return any(
            (
                item.status.value == "passed"
                or (
                    item.status.value == "incomplete"
                    and item.progress_assessment == "pending_business_verification"
                )
            )
            and item.action in {"click", "press"}
            and any(term in " ".join(filter(None, [
                item.description or "",
                item.target_summary or "",
                item.planner_reason or "",
            ])).lower() for term in ("\u521b\u5efa", "create"))
            for item in history
        )

    @staticmethod
    def _created_name_from_history(history: list[StepResult]) -> str | None:
        for item in reversed(history):
            if (
                item.status.value != "passed"
                and not (
                    item.status.value == "incomplete"
                    and item.progress_assessment == "pending_business_verification"
                )
                or item.action not in {"click", "press"}
            ):
                continue
            action_text = " ".join(filter(None, [
                item.description or "",
                item.target_summary or "",
            ]))
            positive_create_action = bool(re.search(
                r"(?:点击|按下|提交|确认|最终|实际|完成)[^，。；\n]{0,20}(?:创建|新建)"
                r"|(?:name|text)=(?:确认创建|创建|立即创建|完成创建)(?:\]|,|$)"
                r"|\b(?:click|press|submit|confirm|final(?:ly)?)\b[^.\n]{0,32}\bcreat(?:e|ion)\b",
                action_text,
                flags=re.IGNORECASE,
            ))
            if not positive_create_action:
                continue
            names = re.findall(r"\btest_[A-Z]+\b", action_text)
            if names:
                return names[-1]
        return None

    @staticmethod
    def _wizard_stage(observation: Observation) -> str:
        semantic = observation.semantic_summary
        wizard = semantic.wizard if semantic else {}
        active = str(wizard.get("activeStep", ""))
        if GAEALaViCCapabilityPack._has_any(active, ("\u786e\u8ba4\u521b\u5efa", "confirm")):
            return "model_wizard_step_4"
        if GAEALaViCCapabilityPack._has_any(active, ("\u57fa\u672c\u4fe1\u606f", "basic information")):
            return "model_wizard_step_3"
        if GAEALaViCCapabilityPack._has_any(active, ("\u5173\u952e\u4fe1\u606f", "key information")):
            return "model_wizard_step_2"
        if GAEALaViCCapabilityPack._has_any(active, ("\u9009\u62e9\u7c7b\u578b", "select type")):
            return "model_wizard_step_1"
        # Some component libraries do not expose the active marker or a
        # dedicated wizard body.  Current dialog text and visible buttons are
        # stronger evidence than falling back to step one.
        dialog_text = " ".join(
            str(value or "")
            for dialog in (semantic.dialogs if semantic else [])
            for value in (dialog.get("name"), dialog.get("text"))
        )
        text = " ".join(filter(None, [str(wizard.get("text", "")), dialog_text]))
        button_names = {
            str(control.get("name") or "").strip().lower()
            for control in (semantic.controls if semantic else [])
            if str(control.get("role") or "").lower() == "button"
        }
        has_final_create_button = bool(
            button_names.intersection({"创建", "确认创建", "立即创建", "完成创建", "create", "confirm create"})
        )
        has_next_button = bool(button_names.intersection({"下一步", "next"}))
        if (
            GAEALaViCCapabilityPack._has_any(text, ("请确认以下信息", "please confirm the following"))
            or (has_final_create_button and not has_next_button)
        ):
            return "model_wizard_step_4"
        if GAEALaViCCapabilityPack._has_any(text, ("智能体名称 *", "智能体国际化名称", "input agent name", "input international name")):
            return "model_wizard_step_3"
        if GAEALaViCCapabilityPack._has_any(text, ("智能体关键字", "选择已有仿真", "仿真模型 *", "agent keyword")):
            return "model_wizard_step_2"
        return "model_wizard_step_1"

    @staticmethod
    def _positive_scope_text(scenario: Any) -> str:
        """Extract requested modules without treating safety denials as scope.

        Persisted business context is deliberately excluded: it describes what
        the site can do, not what the current user asked this run to test.
        """

        raw = " ".join(
            str(value or "")
            for value in (
                getattr(scenario, "name", ""),
                getattr(scenario, "goal", ""),
            )
        ).lower()
        denied_markers = (
            "\u7981\u6b62",
            "\u4e0d\u5f97",
            "\u4e0d\u8981",
            "\u4e0d\u5141\u8bb8",
            "\u65e0\u9700",
            "\u4e0d\u5728\u672c\u6b21\u6d4b\u8bd5\u8303\u56f4",
            "do not",
            "don't",
            "must not",
            "without",
            "exclude",
        )
        clauses = re.split(r"[\n\r\u3002\uff01\uff1f\uff1b;]+", raw)
        positive = [
            clause
            for clause in clauses
            if clause.strip() and not any(marker in clause for marker in denied_markers)
        ]
        text = " ".join(positive)
        # Existing resources are navigation/editing scope, not authorization
        # to execute a new create workflow.
        return re.sub(
            r"(?:\u5df2|\u5df2\u7ecf)\u521b\u5efa|already\s+created|pre-?existing",
            "existing",
            text,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _observed_test_names(
        observation: Observation,
        history: list[StepResult] | None = None,
    ) -> list[str]:
        observed: set[str] = set()
        observations = [item.after for item in (history or []) if item.after is not None]
        observations.append(observation)
        for item in observations:
            semantic = item.semantic_summary
            if semantic is not None:
                if bool(semantic.wizard.get("visible")):
                    continue
                observed.update(
                    str(name).strip()
                    for name in semantic.resource_names
                    if re.fullmatch(r"(?:scenario_)?test_[A-Z]+", str(name).strip())
                )
                continue
            # Compatibility fallback for old evidence without semantic resource
            # rows. Input values and DOM summaries are deliberately excluded so
            # an exact-search query is never mistaken for an occupied resource.
            observed.update(re.findall(r"\b(?:scenario_)?test_[A-Z]+\b", item.accessibility_summary))
        return sorted(observed, key=lambda name: parse_test_name_index(name) or 0)

    @staticmethod
    def _observed_editor_sections(observation: Observation) -> list[str]:
        text = GAEALaViCCapabilityPack._observation_text(observation).lower()
        return [section_id for section_id, label, english in MODEL_EDITOR_SECTIONS if label.lower() in text or english in text]

    @staticmethod
    def _exercised_editor_sections(history: list[StepResult]) -> list[str]:
        text = "\n".join(
            str(value)
            for item in history
            for value in (item.description or "", item.target_summary or "", item.planner_reason or "")
        ).lower()
        return [section_id for section_id, label, english in MODEL_EDITOR_SECTIONS if label.lower() in text or english in text]

    @staticmethod
    def _required_editor_section_ids(scenario: Any) -> list[str]:
        goal = GAEALaViCCapabilityPack._positive_scope_text(scenario)
        complete_editor = GAEALaViCCapabilityPack._has_any(
            goal, _FULL_TERMS + _COMPLETE_EDITOR_TERMS
        )
        required: list[str] = []
        if complete_editor or GAEALaViCCapabilityPack._has_any(goal, _DYNAMICS_TERMS):
            required.append("dynamics")
        if complete_editor or GAEALaViCCapabilityPack._has_any(goal, _SCENARIO_PATH_TERMS):
            required.append("mission_path")
        return required

    @staticmethod
    def _completed_editor_section_ids(history: list[StepResult]) -> list[str]:
        """Count an editor section only after its own Save action succeeded.

        Opening a panel, focusing a field, or filling a value is staging state,
        not a saved business result. The active section is derived from the
        current run's action text, so this does not embed a coordinate or a
        locator from an earlier recording.
        """
        active_section: str | None = None
        last_saved_section: str | None = None
        pending_verification: dict[str, int] = {}
        save_attempts: dict[str, int] = {}
        save_blocked: dict[str, bool] = {}
        mission_path_keyword_at: int | None = None
        mission_path_points_tab_at: int | None = None
        completed: set[str] = set()
        for item in history:
            context_text = " ".join(filter(None, [
                item.description or "",
                item.target_summary or "",
                item.planner_reason or "",
            ])).lower()
            for section_id, label, english in MODEL_EDITOR_SECTIONS:
                if label.lower() in context_text or english in context_text:
                    active_section = section_id

            action_text = " ".join(filter(None, [
                item.description or "",
                item.target_summary or "",
            ])).lower()
            if (
                item.action == "fill"
                and re.search(r"(?:任务路径点关键字|path point keyword)", action_text)
                and "_path" in action_text
            ):
                mission_path_keyword_at = item.index
            if (
                item.status.value == "passed"
                and re.search(r"(?:路径点设置|path point settings)", action_text)
            ):
                mission_path_points_tab_at = item.index
            verification_intent = bool(re.search(
                r"(?:\u91cd\u65b0\u6253\u5f00|\u56de\u8bfb|\u68c0\u67e5|\u9a8c\u8bc1|reopen|inspect|verify|persist)",
                context_text,
            ))
            save_action = (
                item.action in {"click", "press", "visual_click"}
                and bool(re.search(r"(?:\u70b9\u51fb|\u6309\u4e0b|\u63d0\u4ea4|\u786e\u8ba4)?[^\uff0c\u3002\uff1b\n]{0,16}(?:\u4fdd\u5b58|save)", action_text))
                and not verification_intent
            )
            successful = item.status.value == "passed"
            if save_action and active_section in EDITOR_SECTION_STAGE_IDS:
                saved_section = active_section
                save_attempts[saved_section] = item.index
                save_blocked[saved_section] = GAEALaViCCapabilityPack._save_attempt_has_blocking_failure(item)
                last_saved_section = saved_section
            if save_action and (
                successful
                or item.progress_assessment == "pending_business_verification"
            ) and active_section in EDITOR_SECTION_STAGE_IDS:
                verification = dict(item.verification_evidence or {})
                facts = {str(value) for value in verification.get("facts", [])}
                if verification.get("status") == "passed" and "business_state_verified" in facts:
                    completed.add(active_section)
                else:
                    pending_verification[active_section] = item.index
                active_section = None
                continue

            cancel_action = (
                item.action in {"click", "press", "visual_click"}
                and bool(re.search(r"(?:取消|cancel)", action_text))
            )
            if (
                cancel_action
                and last_saved_section in EDITOR_SECTION_STAGE_IDS
                and item.index > save_attempts.get(last_saved_section, -1)
                and not save_blocked.get(last_saved_section, False)
                and (
                    last_saved_section != "mission_path"
                    or (
                        mission_path_keyword_at is not None
                        and mission_path_points_tab_at is not None
                        and mission_path_keyword_at < save_attempts["mission_path"]
                        and mission_path_points_tab_at < save_attempts["mission_path"]
                    )
                )
            ):
                # The site uses Cancel to close a panel after Save. It is a
                # completion signal only for the same section and only after
                # the Save attempt in this run.
                completed.add(last_saved_section)
                active_section = None
                last_saved_section = None
                continue

            if not successful or not verification_intent or item.after is None:
                continue
            for section_id, saved_at in list(pending_verification.items()):
                if item.index <= saved_at or section_id in completed:
                    continue
                label = next(
                    (label for candidate, label, _english in MODEL_EDITOR_SECTIONS if candidate == section_id),
                    "",
                )
                if label.lower() not in context_text and section_id not in context_text:
                    continue
                semantic = item.after.semantic_summary
                invalid = bool(semantic and (
                    semantic.blocking_errors
                    or any(
                        isinstance(control, dict)
                        and (control.get("invalid") is True or str(control.get("validationMessage") or "").strip())
                        for control in semantic.controls
                    )
                ))
                observed_text = GAEALaViCCapabilityPack._observation_text(item.after).lower()
                if not invalid and (label.lower() in observed_text or section_id in observed_text):
                    completed.add(section_id)
        return [
            section_id
            for section_id in ("dynamics", "mission_path")
            if section_id in completed
        ]

    @staticmethod
    def _editor_ready(observation: Observation) -> bool:
        semantic = observation.semantic_summary
        if not semantic or not isinstance(semantic.canvas, dict):
            return False
        canvas = semantic.canvas
        if canvas.get("nonEmptySurface") is True:
            return True
        return any(
            isinstance(item, dict) and int(item.get("width", 0) or 0) > 20 and int(item.get("height", 0) or 0) > 20
            for item in canvas.get("surfaces", [])
        )

    @staticmethod
    def _observation_text(observation: Observation) -> str:
        semantic = observation.semantic_summary
        extra = json.dumps(semantic.model_dump(mode="json"), ensure_ascii=False) if semantic else ""
        return "\n".join([observation.title, observation.accessibility_summary, *observation.dom_summary, extra])

    @staticmethod
    def _has_text(observation: Observation, terms: tuple[str, ...]) -> bool:
        return GAEALaViCCapabilityPack._has_any(GAEALaViCCapabilityPack._observation_text(observation).lower(), terms)

    @staticmethod
    def _has_any(value: str, terms: tuple[str, ...] | list[str]) -> bool:
        lowered = value.lower()
        return any(str(term).lower() in lowered for term in terms)

    @staticmethod
    def _legacy_visible_name_boundary(value: str) -> bool:
        lowered = str(value).lower()
        return "e2e_" in lowered and any(
            term in lowered for term in ("names begin", "name must", "must use", "prefix")
        )

    @staticmethod
    def _looks_like_login(observation: Observation) -> bool:
        text = GAEALaViCCapabilityPack._observation_text(observation).lower()
        if "\u9000\u51fa\u767b\u5f55" in text or "log out" in text or "logout" in text:
            return False
        return GAEALaViCCapabilityPack._has_any(text, ("\u8bf7\u8f93\u5165\u5bc6\u7801", "\u6b22\u8fce\u767b\u5f55", "password", "sign in"))

    @staticmethod
    def _looks_authenticated(observation: Observation) -> bool:
        return GAEALaViCCapabilityPack._has_text(observation, ("\u5efa\u6a21", "\u60f3\u5b9a", "\u5f3a\u5316\u5b66\u4e60", "\u8fd0\u884c\u8bb0\u5f55", "modeling"))
