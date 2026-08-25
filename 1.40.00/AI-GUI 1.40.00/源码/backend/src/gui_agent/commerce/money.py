"""精确到分的电商金额与优惠分摊合同。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CENT = Decimal("0.01")


def parse_money(value: object) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("金额禁止使用二进制浮点数，请传入字符串、整数或 Decimal")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("金额格式无效") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("金额必须是非负有限数")
    if amount.quantize(CENT) != amount:
        raise ValueError("金额最多保留两位小数")
    return amount.quantize(CENT)


class DiscountAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_ref: str = Field(alias="lineRef", min_length=1, max_length=120)
    amount: Decimal

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: object) -> Decimal:
        return parse_money(value)


class CheckoutBreakdown(BaseModel):
    """页面金额与预结算接口必须共同满足的精确公式。"""

    model_config = ConfigDict(extra="forbid")

    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    item_amount: Decimal = Field(alias="itemAmount")
    item_discount: Decimal = Field(default=Decimal("0.00"), alias="itemDiscount")
    shop_discount: Decimal = Field(default=Decimal("0.00"), alias="shopDiscount")
    platform_discount: Decimal = Field(default=Decimal("0.00"), alias="platformDiscount")
    asset_discount: Decimal = Field(default=Decimal("0.00"), alias="assetDiscount")
    shipping_fee: Decimal = Field(default=Decimal("0.00"), alias="shippingFee")
    service_fee: Decimal = Field(default=Decimal("0.00"), alias="serviceFee")
    tax: Decimal = Decimal("0.00")
    payable: Decimal
    discount_allocations: list[DiscountAllocation] = Field(
        default_factory=list, alias="discountAllocations"
    )

    @field_validator(
        "item_amount",
        "item_discount",
        "shop_discount",
        "platform_discount",
        "asset_discount",
        "shipping_fee",
        "service_fee",
        "tax",
        "payable",
        mode="before",
    )
    @classmethod
    def validate_money_fields(cls, value: object) -> Decimal:
        return parse_money(value)

    @property
    def total_discount(self) -> Decimal:
        return (
            self.item_discount
            + self.shop_discount
            + self.platform_discount
            + self.asset_discount
        ).quantize(CENT)

    @property
    def expected_payable(self) -> Decimal:
        return (
            self.item_amount
            - self.total_discount
            + self.shipping_fee
            + self.service_fee
            + self.tax
        ).quantize(CENT)

    @model_validator(mode="after")
    def validate_formula_and_allocations(self) -> "CheckoutBreakdown":
        if self.total_discount > self.item_amount:
            raise ValueError("优惠总额不能超过商品金额")
        if self.payable != self.expected_payable:
            raise ValueError(
                f"应付金额不一致：期望 {self.expected_payable}，实际 {self.payable}"
            )
        if self.discount_allocations:
            allocated = sum((item.amount for item in self.discount_allocations), Decimal("0.00"))
            if allocated.quantize(CENT) != self.total_discount:
                raise ValueError(
                    f"优惠分摊不守恒：优惠 {self.total_discount}，分摊 {allocated.quantize(CENT)}"
                )
        return self

