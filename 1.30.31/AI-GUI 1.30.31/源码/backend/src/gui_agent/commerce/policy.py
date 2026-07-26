"""正式站与隔离交易环境的动作前副作用策略。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .models import CommerceAction, CommerceActionRequest, CommerceEnvironment, RiskLevel


READ_ACTIONS = {
    CommerceAction.BROWSE,
    CommerceAction.SEARCH,
    CommerceAction.FILTER,
    CommerceAction.SORT,
    CommerceAction.PAGINATE,
    CommerceAction.VIEW_PRODUCT,
    CommerceAction.VIEW_ACCOUNT_STRUCTURE,
    CommerceAction.VIEW_HELP,
}
REVERSIBLE_ACTIONS = {
    CommerceAction.ADD_CART,
    CommerceAction.REMOVE_CART,
    CommerceAction.FAVORITE,
    CommerceAction.UNFAVORITE,
    CommerceAction.FOLLOW,
    CommerceAction.UNFOLLOW,
}
SANDBOX_DRIVER_ACTIONS = {CommerceAction.PAY, CommerceAction.REFUND}


class CommercePolicyError(RuntimeError):
    pass


class CommercePolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    risk_level: RiskLevel = Field(alias="riskLevel")
    requires_confirmation: bool = Field(alias="requiresConfirmation")
    reason: str
    missing_controls: list[str] = Field(default_factory=list, alias="missingControls")

    def enforce(self) -> None:
        if not self.allowed:
            suffix = f"；缺少：{'、'.join(self.missing_controls)}" if self.missing_controls else ""
            raise CommercePolicyError(f"{self.reason}{suffix}")


def evaluate_commerce_action(request: CommerceActionRequest) -> CommercePolicyDecision:
    if request.action in READ_ACTIONS:
        return CommercePolicyDecision(
            allowed=True,
            riskLevel=RiskLevel.READ,
            requiresConfirmation=False,
            reason="只读或无持久副作用动作",
        )

    if request.action == CommerceAction.CHANGE_REGION:
        missing = []
        if not request.before_state:
            missing.append("原配送地区")
        if not request.cleanup_action:
            missing.append("恢复原配送地区动作")
        return CommercePolicyDecision(
            allowed=not missing,
            riskLevel=RiskLevel.REVERSIBLE_WRITE,
            requiresConfirmation=False,
            reason="可恢复配送地区偏好" if not missing else "配送地区切换缺少恢复控制",
            missingControls=missing,
        )

    if request.environment == CommerceEnvironment.PRODUCTION_READONLY:
        if request.action not in REVERSIBLE_ACTIONS:
            return CommercePolicyDecision(
                allowed=False,
                riskLevel=RiskLevel.HIGH_RISK_WRITE,
                requiresConfirmation=True,
                reason="正式消费者站禁止交易、权益、个人资料、消息和后台写操作",
            )
        missing = _reversible_controls(request, require_explicit_production_authorization=True)
        return CommercePolicyDecision(
            allowed=not missing,
            riskLevel=RiskLevel.REVERSIBLE_WRITE,
            requiresConfirmation=True,
            reason=(
                "正式站专用账号的授权可逆动作"
                if not missing
                else "正式站可逆动作缺少专用账号授权或清理控制"
            ),
            missingControls=missing,
        )

    if request.action in REVERSIBLE_ACTIONS:
        missing = _reversible_controls(request, require_explicit_production_authorization=False)
        return CommercePolicyDecision(
            allowed=not missing,
            riskLevel=RiskLevel.REVERSIBLE_WRITE,
            requiresConfirmation=True,
            reason="隔离环境可逆写操作" if not missing else "隔离环境可逆写操作控制不完整",
            missingControls=missing,
        )

    missing = _high_risk_controls(request)
    return CommercePolicyDecision(
        allowed=not missing,
        riskLevel=RiskLevel.HIGH_RISK_WRITE,
        requiresConfirmation=True,
        reason="隔离交易环境高风险动作" if not missing else "隔离交易环境高风险动作控制不完整",
        missingControls=missing,
    )


def _reversible_controls(
    request: CommerceActionRequest, *, require_explicit_production_authorization: bool
) -> list[str]:
    missing: list[str] = []
    if not request.account_ref:
        missing.append("专用账号密钥引用")
    if not request.target:
        missing.append("目标业务 ID")
    if not request.before_state:
        missing.append("执行前状态")
    if not request.cleanup_action:
        missing.append("清理动作")
    if not request.confirmed_by_human:
        missing.append("人工确认")
    if not request.e2e_owned:
        missing.append("E2E 资源归属")
    if require_explicit_production_authorization and not request.production_reversible_write_authorized:
        missing.append("正式站低副作用书面授权")
    return missing


def _high_risk_controls(request: CommerceActionRequest) -> list[str]:
    missing = _reversible_controls(request, require_explicit_production_authorization=False)
    if not request.idempotency_key:
        missing.append("幂等键")
    if request.action in SANDBOX_DRIVER_ACTIONS and not request.sandbox_driver:
        missing.append("支付／退款沙箱驱动器")
    return missing
