"""Role and side-effect policy for the Cesium ion acceptance suite."""

from __future__ import annotations

from urllib.parse import urlparse


ROLE_MATRIX = {
    "personal_e2e_account": ["owned assets", "owned labels", "owned stories", "owned clips", "owned tokens"],
    "team_owner": ["team assets", "team billing read", "members", "roles", "team settings"],
    "team_member": ["team assets", "team stories", "team tokens", "developer applications"],
    "read_only_token": ["selected asset read"],
    "unauthenticated": ["public login", "public story when explicitly shared"],
}

SIDE_EFFECTS = {
    "browse_search_filter_sort": {"level": "read_only", "confirmation": False, "cleanup": "none"},
    "viewer_camera_clock": {"level": "session_only", "confirmation": False, "cleanup": "restore home and clock"},
    "upload_or_cloud_import": {"level": "reversible_write", "confirmation": False, "cleanup": "delete ledger-owned asset/task"},
    "archive": {"level": "reversible_quota_write", "confirmation": False, "cleanup": "delete archive and disable download"},
    "download": {"level": "isolated_local_write", "confirmation": False, "cleanup": "delete isolated local copy"},
    "s3_export": {"level": "high_risk_external_write", "confirmation": True, "cleanup": "delete dedicated E2E prefix"},
    "add_to_my_assets": {"level": "reversible_write", "confirmation": False, "cleanup": "remove E2E reference"},
    "create_clip": {"level": "reversible_quota_write", "confirmation": True, "cleanup": "delete clip and output asset"},
    "create_token": {"level": "sensitive_reversible_write", "confirmation": True, "cleanup": "revoke E2E token"},
    "rotate_or_revoke_e2e_token": {"level": "high_risk_write", "confirmation": True, "cleanup": "revoke E2E token"},
    "regenerate_default_token": {"level": "high_risk_irreversible", "confirmation": True, "cleanup": "no direct rollback"},
    "create_story": {"level": "reversible_write", "confirmation": False, "cleanup": "delete E2E story"},
    "share_story": {"level": "high_risk_public_write", "confirmation": True, "cleanup": "revoke and verify anonymous denial"},
    "edit_label": {"level": "reversible_write", "confirmation": False, "cleanup": "detach and delete E2E label"},
    "delete_resource": {"level": "high_risk_write", "confirmation": True, "cleanup": "recreate only from fixed source data"},
    "account_security_change": {"level": "high_risk_identity_write", "confirmation": True, "cleanup": "identity recovery procedure"},
    "delete_account": {"level": "forbidden", "confirmation": True, "cleanup": "impossible"},
    "team_membership_change": {"level": "high_risk_write", "confirmation": True, "cleanup": "restore role or remove invitation"},
    "billing_change": {"level": "forbidden", "confirmation": True, "cleanup": "billing sandbox only"},
}

_NO_CLEANUP_REQUIRED = {"none", "restore home and clock"}
_LEDGER_OWNED_TARGETS = {"delete_resource", "regenerate_default_token", "rotate_or_revoke_e2e_token"}


class CesiumPolicyError(ValueError):
    pass


def is_cesium_target(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"ion.cesium.com", "api.cesium.com"}


def validate_cesium_plan(plan, target_url: str, ledger_entries: list[dict]) -> None:
    """Reject unclassified, forbidden, or non-owned Cesium side effects before launch."""
    if not is_cesium_target(target_url):
        return
    for index, step in enumerate(plan.steps, start=1):
        validate_cesium_step(step, index, ledger_entries)


def validate_cesium_step(step, index: int, ledger_entries: list[dict]) -> None:
    kind = (step.effect_kind or "").strip()
    if not kind or step.effect_level is None:
        raise CesiumPolicyError(f"Cesium 计划第 {index} 步缺少 effect_kind/effect_level")
    policy = SIDE_EFFECTS.get(kind)
    if policy is None:
        raise CesiumPolicyError(f"Cesium 计划第 {index} 步使用未知副作用类型：{kind}")
    declared_level = step.effect_level.value
    if declared_level != policy["level"]:
        raise CesiumPolicyError(
            f"Cesium 计划第 {index} 步副作用等级不匹配：{declared_level} != {policy['level']}"
        )
    if declared_level == "forbidden":
        raise CesiumPolicyError(f"Cesium 计划第 {index} 步属于禁止操作：{kind}")
    if policy["cleanup"] not in _NO_CLEANUP_REQUIRED and not (step.cleanup_action or "").strip():
        raise CesiumPolicyError(f"Cesium 计划第 {index} 步缺少 cleanup_action：{kind}")
    if kind in _LEDGER_OWNED_TARGETS:
        target_id = (step.target_id or "").strip()
        resource_name = (step.resource_name or "").strip()
        if not target_id or not resource_name.startswith("E2E-"):
            raise CesiumPolicyError(f"Cesium 计划第 {index} 步破坏性目标必须提供台账 ID 和 E2E- 名称")
        if not any(
            item.get("resourceId") == target_id
            and item.get("name") == resource_name
            and item.get("cleanupStatus") == "pending"
            for item in ledger_entries
        ):
            raise CesiumPolicyError(f"Cesium 计划第 {index} 步目标不属于待清理 E2E 资源台账")


def cesium_confirmation_rule(step) -> str | None:
    policy = SIDE_EFFECTS.get((step.effect_kind or "").strip())
    return f"cesium:{step.effect_kind}" if policy and policy["confirmation"] else None


def policy_payload() -> dict:
    return {
        "version": "1.32.00",
        "ownershipRule": "a destructive target must have an E2E- name and a matching resource ID in the run ledger",
        "roles": ROLE_MATRIX,
        "sideEffects": SIDE_EFFECTS,
    }

