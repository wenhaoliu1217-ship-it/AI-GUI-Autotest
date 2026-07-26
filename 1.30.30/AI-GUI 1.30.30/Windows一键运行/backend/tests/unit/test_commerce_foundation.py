from decimal import Decimal

import pytest
from pydantic import ValidationError

from gui_agent.commerce import (
    BusinessReference,
    CheckoutBreakdown,
    CommerceAction,
    CommerceActionRequest,
    CommerceEnvironment,
    CommercePolicyError,
    DiscountAllocation,
    ResourceLedgerEntry,
    evaluate_commerce_action,
)
from gui_agent.onboarding.models import CommerceProfile, ProjectConfig


def _target(kind="skuId", value="E2E_SKU_001"):
    return BusinessReference.from_raw(kind, value)


def test_checkout_breakdown_uses_exact_decimal_formula_and_allocations():
    breakdown = CheckoutBreakdown(
        itemAmount="100.00",
        itemDiscount="5.00",
        shopDiscount="3.00",
        platformDiscount="2.00",
        assetDiscount="1.00",
        shippingFee="6.00",
        serviceFee="2.00",
        tax="1.00",
        payable="98.00",
        discountAllocations=[
            DiscountAllocation(lineRef="cart-line-hash-1", amount="6.00"),
            DiscountAllocation(lineRef="cart-line-hash-2", amount="5.00"),
        ],
    )

    assert breakdown.total_discount == Decimal("11.00")
    assert breakdown.expected_payable == Decimal("98.00")


@pytest.mark.parametrize("field,value", [("itemAmount", 10.1), ("payable", "10.001")])
def test_checkout_breakdown_rejects_float_or_sub_cent_amount(field, value):
    payload = {"itemAmount": "10.00", "payable": "10.00"}
    payload[field] = value
    with pytest.raises(ValidationError):
        CheckoutBreakdown(**payload)


def test_checkout_breakdown_rejects_formula_or_allocation_mismatch():
    with pytest.raises(ValidationError, match="应付金额不一致"):
        CheckoutBreakdown(itemAmount="10.00", shippingFee="1.00", payable="10.00")
    with pytest.raises(ValidationError, match="优惠分摊不守恒"):
        CheckoutBreakdown(
            itemAmount="10.00",
            itemDiscount="1.00",
            payable="9.00",
            discountAllocations=[DiscountAllocation(lineRef="line", amount="0.99")],
        )


def test_business_reference_contains_hash_and_suffix_not_raw_identifier():
    reference = BusinessReference.from_raw("orderId", "E2E_ORDER_123456")
    dumped = reference.model_dump_json()
    assert reference.sha256 != "E2E_ORDER_123456"
    assert reference.suffix == "3456"
    assert "E2E_ORDER_123456" not in dumped


def test_production_read_is_allowed_but_order_submission_is_always_denied():
    read = CommerceActionRequest(
        action="search", environment="production_readonly", runId="readonly-run"
    )
    assert evaluate_commerce_action(read).allowed is True

    order = CommerceActionRequest(
        action="submit_order",
        environment="production_readonly",
        runId="E2E_run",
        accountRef="JD_BUYER_ACCOUNT",
        target=_target(),
        beforeState="checkout_ready",
        cleanupAction="cancel order",
        idempotencyKey="E2E_run:submit",
        confirmedByHuman=True,
        e2eOwned=True,
        sandboxDriver=True,
    )
    decision = evaluate_commerce_action(order)
    assert decision.allowed is False
    with pytest.raises(CommercePolicyError, match="正式消费者站禁止"):
        decision.enforce()


def test_production_reversible_write_requires_every_control():
    request = CommerceActionRequest(
        action="add_cart",
        environment="production_readonly",
        runId="E2E_cart",
        accountRef="JD_BUYER_ACCOUNT",
        target=_target(),
        beforeState="absent",
        cleanupAction="remove target cartLineId",
        confirmedByHuman=True,
        e2eOwned=True,
        productionReversibleWriteAuthorized=True,
    )
    assert evaluate_commerce_action(request).allowed is True

    denied = request.model_copy(update={"production_reversible_write_authorized": False})
    decision = evaluate_commerce_action(denied)
    assert decision.allowed is False
    assert "正式站低副作用书面授权" in decision.missing_controls


def test_region_change_is_allowed_only_with_restore_state():
    denied = CommerceActionRequest(
        action="change_region", environment="production_readonly", runId="region-read"
    )
    assert evaluate_commerce_action(denied).allowed is False

    allowed = CommerceActionRequest(
        action="change_region",
        environment="production_readonly",
        runId="region-read",
        beforeState="region-hash-before",
        cleanupAction="restore region by stored hash reference",
    )
    decision = evaluate_commerce_action(allowed)
    assert decision.allowed is True
    assert decision.requires_confirmation is False


def test_isolated_payment_requires_idempotency_and_sandbox_driver():
    base = CommerceActionRequest(
        action="pay",
        environment="isolated_transaction",
        runId="E2E_payment",
        accountRef="JD_BUYER_ACCOUNT",
        target=_target("paymentId", "E2E_PAYMENT_001"),
        beforeState="pending",
        cleanupAction="sandbox refund",
        confirmedByHuman=True,
        e2eOwned=True,
    )
    denied = evaluate_commerce_action(base)
    assert set(denied.missing_controls) == {"幂等键", "支付／退款沙箱驱动器"}

    allowed = base.model_copy(update={"idempotency_key": "E2E_payment:1", "sandbox_driver": True})
    assert evaluate_commerce_action(allowed).allowed is True


def test_resource_ledger_only_accepts_e2e_run_and_cleaned_timestamp_pair():
    with pytest.raises(ValidationError):
        ResourceLedgerEntry(
            runId="real-order",
            reference=_target("orderId", "ORDER_1"),
            createdBy=CommerceAction.SUBMIT_ORDER,
            cleanupAction="cancel",
        )
    with pytest.raises(ValidationError, match="cleanedAt"):
        ResourceLedgerEntry(
            runId="E2E_order",
            reference=_target("orderId", "E2E_ORDER_1"),
            createdBy=CommerceAction.SUBMIT_ORDER,
            cleanupAction="cancel",
            status="cleaned",
        )


def test_commerce_profile_persists_safe_aliases_and_rejects_sandbox_on_production():
    project = ProjectConfig(
        id="project-commerce",
        name="Commerce",
        baseUrl="https://example.com",
        commerceProfile={
            "enabled": True,
            "environment": "isolated_transaction",
            "accountRef": "JD_BUYER_ACCOUNT",
            "sandboxDriver": True,
            "e2eResourcePrefix": "E2E_JD_",
            "piiMaskSelectors": ["[data-mobile]", "[data-mobile]"],
        },
    )
    assert project.commerce_profile.account_ref == "JD_BUYER_ACCOUNT"
    assert project.commerce_profile.pii_mask_selectors == ["[data-mobile]"]

    with pytest.raises(ValidationError, match="正式站项目不能启用"):
        CommerceProfile(environment="production_readonly", sandboxDriver=True)
