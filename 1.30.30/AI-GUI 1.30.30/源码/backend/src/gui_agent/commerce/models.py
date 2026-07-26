"""电商动作、业务标识和 E2E 资源台账模型。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CommerceEnvironment(str, Enum):
    PRODUCTION_READONLY = "production_readonly"
    ISOLATED_TRANSACTION = "isolated_transaction"


class RiskLevel(str, Enum):
    READ = "read"
    REVERSIBLE_WRITE = "reversible_write"
    HIGH_RISK_WRITE = "high_risk_write"


class CommerceAction(str, Enum):
    BROWSE = "browse"
    SEARCH = "search"
    FILTER = "filter"
    SORT = "sort"
    PAGINATE = "paginate"
    VIEW_PRODUCT = "view_product"
    VIEW_ACCOUNT_STRUCTURE = "view_account_structure"
    VIEW_HELP = "view_help"
    CHANGE_REGION = "change_region"
    ADD_CART = "add_cart"
    REMOVE_CART = "remove_cart"
    FAVORITE = "favorite"
    UNFAVORITE = "unfavorite"
    FOLLOW = "follow"
    UNFOLLOW = "unfollow"
    CLAIM_COUPON = "claim_coupon"
    WRITE_ADDRESS = "write_address"
    WRITE_INVOICE_PROFILE = "write_invoice_profile"
    SUBMIT_ORDER = "submit_order"
    PAY = "pay"
    CANCEL_ORDER = "cancel_order"
    CONFIRM_RECEIPT = "confirm_receipt"
    REQUEST_AFTER_SALE = "request_after_sale"
    REFUND = "refund"
    REVIEW = "review"
    SEND_MESSAGE = "send_message"
    DOWNLOAD_INVOICE = "download_invoice"
    MERCHANT_MUTATION = "merchant_mutation"


class LedgerStatus(str, Enum):
    CREATED = "created"
    CLEANUP_PENDING = "cleanup_pending"
    CLEANED = "cleaned"
    CLEANUP_FAILED = "cleanup_failed"


BusinessIdType = Literal[
    "skuId",
    "spuId",
    "cartLineId",
    "orderId",
    "paymentId",
    "refundId",
    "afterSaleId",
    "shipmentId",
    "invoiceId",
]


class CommerceStateProbe(BaseModel):
    """Read-only backend evidence contract using safe run/resource aliases."""

    model_config = ConfigDict(extra="forbid")

    domain: Literal["order", "inventory", "payment", "refund"]
    url: str = Field(min_length=1, max_length=1000)
    json_path: str = Field(alias="jsonPath", pattern=r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*|\.[0-9]+)*$")
    expected_state: str = Field(alias="expectedState", min_length=1, max_length=80)
    timeout_ms: int = Field(default=15_000, alias="timeoutMs", ge=500, le=120_000)
    interval_ms: int = Field(default=500, alias="intervalMs", ge=100, le=10_000)

    @field_validator("url")
    @classmethod
    def validate_safe_url_template(cls, value: str) -> str:
        placeholders = re.findall(r"\$\{([^}]+)\}", value)
        if any(item not in {"RUN_ID", "TARGET_REF"} for item in placeholders):
            raise ValueError("状态探针 URL 只允许 RUN_ID 和 TARGET_REF 占位符")
        if any(token in value.lower() for token in ("password=", "token=", "secret=", "cookie=")):
            raise ValueError("状态探针 URL 禁止包含明文凭据")
        return value


class CommerceStepMetadata(BaseModel):
    """计划中的安全电商语义；targetRef 只能是引用，不得放真实交易 ID。"""

    model_config = ConfigDict(extra="forbid")

    action: CommerceAction
    target_kind: BusinessIdType | None = Field(default=None, alias="targetKind")
    target_ref: str | None = Field(default=None, alias="targetRef")
    before_state: str | None = Field(default=None, alias="beforeState", max_length=120)
    cleanup_action: str | None = Field(default=None, alias="cleanupAction", max_length=200)
    idempotency_key_ref: str | None = Field(default=None, alias="idempotencyKeyRef", max_length=160)
    e2e_owned: bool = Field(default=False, alias="e2eOwned")
    ledger_operation: Literal["none", "register", "cleanup"] = Field(
        default="none", alias="ledgerOperation"
    )
    state_probe: CommerceStateProbe | None = Field(default=None, alias="stateProbe")

    @field_validator("target_ref")
    @classmethod
    def validate_target_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not re.fullmatch(r"(?:resource|secret|public-sku):[A-Za-z0-9_.:-]{2,100}", normalized):
            raise ValueError("targetRef 必须是 resource:、secret: 或 public-sku: 引用，不能保存真实交易 ID")
        return normalized

    @model_validator(mode="after")
    def validate_reference_pair(self) -> "CommerceStepMetadata":
        if bool(self.target_kind) != bool(self.target_ref):
            raise ValueError("targetKind 与 targetRef 必须同时提供")
        if self.ledger_operation != "none" and not self.target_ref:
            raise ValueError("资源台账操作需要 targetRef")
        if self.ledger_operation == "register" and not self.cleanup_action:
            raise ValueError("登记资源台账时必须提供 cleanupAction")
        return self


class BusinessReference(BaseModel):
    """可进入证据的业务标识；不保存原始订单、支付或个人资产 ID。"""

    model_config = ConfigDict(extra="forbid")

    kind: BusinessIdType
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    suffix: str = Field(default="", max_length=8)

    @classmethod
    def from_raw(cls, kind: BusinessIdType, raw_value: str) -> "BusinessReference":
        normalized = raw_value.strip()
        if not normalized:
            raise ValueError("业务 ID 不能为空")
        return cls(
            kind=kind,
            sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            suffix=normalized[-4:] if len(normalized) >= 4 else "***",
        )


class CommerceActionRequest(BaseModel):
    """动作执行前的授权输入；策略评估必须发生在页面点击之前。"""

    model_config = ConfigDict(extra="forbid")

    action: CommerceAction
    environment: CommerceEnvironment
    run_id: str = Field(alias="runId", min_length=1, max_length=120)
    account_ref: str | None = Field(default=None, alias="accountRef")
    target: BusinessReference | None = None
    before_state: str | None = Field(default=None, alias="beforeState", max_length=120)
    cleanup_action: str | None = Field(default=None, alias="cleanupAction", max_length=200)
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey", max_length=160)
    confirmed_by_human: bool = Field(default=False, alias="confirmedByHuman")
    e2e_owned: bool = Field(default=False, alias="e2eOwned")
    production_reversible_write_authorized: bool = Field(
        default=False, alias="productionReversibleWriteAuthorized"
    )
    sandbox_driver: bool = Field(default=False, alias="sandboxDriver")

    @field_validator("account_ref")
    @classmethod
    def validate_account_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", normalized):
            raise ValueError("账号只能使用大写环境密钥别名，不得保存真实账号")
        return normalized

    @model_validator(mode="after")
    def protect_e2e_ownership_claim(self) -> "CommerceActionRequest":
        if self.e2e_owned and not self.run_id.startswith("E2E_"):
            raise ValueError("E2E 资源的 runId 必须以 E2E_ 开头")
        return self


class ResourceLedgerEntry(BaseModel):
    """由测试创建或占用的资源台账，用于逆序清理和零残留证明。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(alias="runId", pattern=r"^E2E_[A-Za-z0-9_.:-]+$")
    reference: BusinessReference
    created_by: CommerceAction = Field(alias="createdBy")
    cleanup_action: str = Field(alias="cleanupAction", min_length=1, max_length=200)
    status: LedgerStatus = LedgerStatus.CREATED
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(), alias="createdAt"
    )
    cleaned_at: str | None = Field(default=None, alias="cleanedAt")

    @model_validator(mode="after")
    def validate_cleanup_state(self) -> "ResourceLedgerEntry":
        if self.status == LedgerStatus.CLEANED and not self.cleaned_at:
            raise ValueError("资源标记 cleaned 时必须记录 cleanedAt")
        if self.status != LedgerStatus.CLEANED and self.cleaned_at:
            raise ValueError("未清理资源不能记录 cleanedAt")
        return self
