"""Cesium-only deterministic planning and policy adapter.

The implementation delegates to the existing battle-tested rules in
``agent_planner`` while keeping their invocation out of the generic planner
loop.  This is the first extraction step; individual rules can move here
incrementally without changing the public Agent contract.
"""

from __future__ import annotations

import json
from typing import Any

from ..benchmarks.cesium_ion.policy import SIDE_EFFECTS, is_cesium_target


class CesiumDecisionStrategy:
    adapter_name = "cesium"

    def matches(self, url: str) -> bool:
        return is_cesium_target(url)

    def pre_model_decision(self, scenario, observation, history, base_url):
        from . import agent_planner as planner

        decision = planner._cesium_session_recovery_decision(scenario, observation, history, base_url)
        decision = decision or planner._cesium_protected_reentry_decision(scenario, observation, history)
        decision = decision or planner._cesium_async_status_decision(scenario, observation, history)
        decision = decision or planner._cesium_asset_list_audit_decision(scenario, observation, history)
        decision = decision or planner._cesium_help_feedback_observation_decision(scenario, observation, history)
        decision = decision or planner._cesium_upload_info_complete_decision(scenario, observation, history)
        decision = decision or planner._cesium_story_share_approval_probe_decision(scenario, observation, history, base_url)
        if decision is None and planner._cesium_protected_route_for_goal(scenario.goal) is not None:
            decision = planner._cesium_loading_wait_decision(observation, history, scenario=scenario)
        return (
            decision
            or planner._cesium_accidental_story_cleanup_action_decision(scenario, observation, history)
            or planner._cesium_accidental_story_cleanup_entry_decision(scenario, observation, history)
            or planner._cesium_authorized_story_sharing_restore_decision(scenario, observation, history)
            or planner._cesium_story_share_approval_probe_decision(scenario, observation, history, base_url)
            or planner._cesium_token_help_decision(scenario, observation, history)
            or planner._cesium_support_completion_decision(scenario, observation, history)
            or planner._cesium_support_decision(scenario, observation, history)
            or planner._cesium_token_creation_approval_probe_decision(scenario, observation, history)
            or planner._cesium_empty_upload_approval_probe_decision(scenario, observation, history)
            or planner._cesium_asset_search_fill_decision(scenario, observation, history)
            or planner._cesium_asset_search_submit_decision(scenario, observation, history)
            or planner._cesium_asset_search_wait_decision(scenario, observation, history)
            or planner._cesium_asset_search_complete_decision(scenario, observation, history)
            or planner._cesium_asset_type_categories_wait_decision(scenario, observation, history)
            or planner._cesium_asset_type_categories_complete_decision(scenario, observation, history)
            or planner._cesium_asset_kind_access_wait_decision(scenario, observation, history)
            or planner._cesium_asset_kind_access_complete_decision(scenario, observation, history)
            or planner._cesium_upload_form_entry_decision(scenario, observation, history)
            or planner._cesium_asset_list_wait_decision(scenario, observation, history)
            or planner._cesium_asset_detail_decision(scenario, observation, history)
            or planner._cesium_asset_detail_wait_decision(scenario, observation, history)
            or planner._cesium_asset_detail_complete_decision(scenario, observation, history)
            or planner._cesium_asset_preview_decision(scenario, observation, history)
            or planner._cesium_asset_preview_wait_decision(scenario, observation, history)
            or planner._cesium_asset_preview_complete_decision(scenario, observation, history)
            or planner._cesium_token_list_wait_decision(scenario, observation, history)
            or planner._cesium_token_list_complete_decision(scenario, observation, history)
            or planner._cesium_billing_page_decision(scenario, observation, history)
            or planner._cesium_account_page_complete_decision(scenario, observation, history)
            or planner._cesium_stories_list_complete_decision(scenario, observation, history)
            or planner._cesium_existing_story_editor_entry_decision(scenario, observation, history)
            or planner._cesium_story_preview_entry_decision(scenario, observation, history)
            or planner._cesium_story_map_search_fill_decision(scenario, observation, history)
            or planner._cesium_story_map_search_submit_decision(scenario, observation, history)
            or planner._cesium_story_map_search_wait_decision(scenario, observation, history)
            or planner._cesium_story_measurement_toolbar_decision(scenario, observation, history)
            or planner._cesium_story_add_polygon_decision(scenario, observation, history)
            or planner._cesium_story_draw_concave_star_decision(scenario, observation, history)
            or planner._cesium_story_draw_location_polygon_decision(scenario, observation, history)
            or planner._cesium_story_measurement_observation_decision(scenario, observation, history)
            or planner._cesium_story_measurement_clear_decision(scenario, observation, history)
            or planner._cesium_story_measurement_clear_verify_decision(scenario, observation, history)
            or planner._cesium_story_measurement_complete_decision(scenario, observation, history)
            or planner._cesium_story_route_recovery_decision(scenario, observation, history)
            or planner._cesium_story_loading_decision(scenario, observation, history)
        )

    def post_model_decision(self, scenario, observation, history, base_url, decision):
        from . import agent_planner as planner

        for resolver in (
            lambda: planner._cesium_loading_wait_decision(observation, history, scenario=scenario),
            lambda: planner._cesium_session_recovery_decision(scenario, observation, history, base_url),
            lambda: planner._cesium_story_route_recovery_decision(scenario, observation, history),
            lambda: planner._cesium_story_loading_decision(scenario, observation, history),
            lambda: planner._cesium_asset_entry_decision(scenario, observation),
            lambda: planner._cesium_upload_form_wait_decision(scenario, observation, history),
            lambda: planner._cesium_upload_form_cancel_decision(scenario, observation, history),
            lambda: planner._cesium_upload_form_complete_decision(scenario, observation, history),
            lambda: planner._cesium_asset_detail_decision(scenario, observation, history),
            lambda: planner._cesium_asset_detail_wait_decision(scenario, observation, history),
            lambda: planner._cesium_asset_preview_decision(scenario, observation, history),
            lambda: planner._cesium_asset_preview_wait_decision(scenario, observation, history),
            lambda: planner._cesium_asset_preview_complete_decision(scenario, observation, history),
            lambda: planner._cesium_asset_filter_decision(scenario, observation, history),
            lambda: planner._cesium_asset_sort_decision(scenario, observation, history),
            lambda: planner._cesium_asset_sort_wait_decision(scenario, observation, history),
            lambda: planner._cesium_asset_empty_state_probe_decision(scenario, observation, history),
            lambda: planner._cesium_asset_empty_state_submit_decision(scenario, observation, history),
            lambda: planner._cesium_asset_empty_state_wait_decision(scenario, observation, history),
            lambda: planner._cesium_account_menu_decision(scenario, observation, history),
        ):
            candidate = resolver()
            if candidate is not None:
                decision = candidate
        candidate = planner._cesium_new_story_guard_decision(scenario, observation, decision)
        if candidate is not None:
            decision = candidate
        candidate = planner._cesium_login_takeover_decision(scenario, observation, history)
        if candidate is not None:
            decision = candidate
        return decision

    def validate_visual_request(self, request) -> None:
        policy = SIDE_EFFECTS.get((request.effect_kind or "").strip())
        if policy is None or request.effect_level is None:
            raise ValueError("Cesium 视觉请求缺少有效的 effect_kind/effect_level")
        if request.effect_level.value != policy["level"] or policy["level"] == "forbidden":
            raise ValueError("Cesium 视觉请求的副作用分类不符合安全策略")

    def prompt_rules(self) -> str:
        return (
            "目标是 Cesium ion。action 和 visual_request 中都必须填写 effect_kind 和完全匹配的 effect_level；"
            "需清理动作填写 cleanup_action；破坏性目标填写台账 target_id 与 E2E- resource_name；"
            f"只可使用此策略表：{json.dumps(SIDE_EFFECTS, ensure_ascii=False)}；"
        )
