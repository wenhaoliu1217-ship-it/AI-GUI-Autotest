from pathlib import Path

import pytest
from pydantic import ValidationError

from gui_agent.commerce import CommerceStepMetadata, LedgerStatus
from gui_agent.domain.models import Step
from gui_agent.execution.runner import (
    RunnerConfig,
    _commerce_preflight,
    _commerce_record_success,
    _require_commerce_metadata,
)
from gui_agent.security.policy import SecurityError


class ArtifactProbe:
    def __init__(self) -> None:
        self.events = []
        self.documents = {}

    def event(self, name, **payload) -> None:
        self.events.append((name, payload))

    def write_json(self, name, payload) -> None:
        self.documents[name] = payload


def _step(action: str, **metadata) -> Step:
    return Step(
        action="click",
        locator={"text": action},
        description=action,
        commerce={"action": action, **metadata},
    )


def _sandbox_config(**overrides) -> RunnerConfig:
    values = {
        "artifacts_root": Path("artifacts"),
        "commerce_enabled": True,
        "commerce_environment": "isolated_transaction",
        "commerce_account_ref": "JD_BUYER_ACCOUNT",
        "commerce_sandbox_driver": True,
    }
    values.update(overrides)
    return RunnerConfig(**values)


def test_target_reference_rejects_raw_business_identifier() -> None:
    with pytest.raises(ValidationError, match="targetRef"):
        CommerceStepMetadata(
            action="submit_order",
            targetKind="orderId",
            targetRef="123456789012345678",
            e2eOwned=True,
        )


def test_commerce_write_without_structured_metadata_is_rejected() -> None:
    step = Step(action="click", locator={"text": "提交订单"}, description="提交订单")
    with pytest.raises(SecurityError, match="commerce"):
        _require_commerce_metadata(step, _sandbox_config())


def test_production_order_is_rejected_even_after_confirmation() -> None:
    step = _step(
        "submit_order",
        targetKind="orderId",
        targetRef="resource:E2E_ORDER_1",
        beforeState="checkout_ready",
        cleanupAction="cancel sandbox order",
        idempotencyKeyRef="secret:E2E_ORDER_IDEMPOTENCY",
        e2eOwned=True,
    )
    with pytest.raises(SecurityError, match="正式消费者站禁止"):
        _commerce_preflight(
            step,
            _sandbox_config(commerce_environment="production_readonly"),
            "run-1",
            True,
            {},
            ArtifactProbe(),
            1,
        )


def test_isolated_payment_requires_idempotency_and_sandbox_driver() -> None:
    step = _step(
        "pay",
        targetKind="paymentId",
        targetRef="resource:E2E_PAYMENT_1",
        beforeState="pending",
        cleanupAction="refund sandbox payment",
        e2eOwned=True,
    )
    with pytest.raises(SecurityError, match="幂等键"):
        _commerce_preflight(
            step,
            _sandbox_config(commerce_sandbox_driver=False),
            "run-2",
            True,
            {},
            ArtifactProbe(),
            1,
        )


def test_ledger_rejects_duplicate_and_unknown_cleanup_then_records_cleanup() -> None:
    artifacts = ArtifactProbe()
    ledger = {}
    register = _step(
        "add_cart",
        targetKind="cartLineId",
        targetRef="resource:E2E_CART_LINE_1",
        beforeState="absent",
        cleanupAction="remove cart line",
        e2eOwned=True,
        ledgerOperation="register",
    )
    cleanup = _step(
        "remove_cart",
        targetKind="cartLineId",
        targetRef="resource:E2E_CART_LINE_1",
        beforeState="present",
        cleanupAction="verify cart line absent",
        e2eOwned=True,
        ledgerOperation="cleanup",
    )

    with pytest.raises(SecurityError, match="未在本次运行台账"):
        _commerce_preflight(cleanup, _sandbox_config(), "run-3", True, ledger, artifacts, 1)

    _commerce_preflight(register, _sandbox_config(), "run-3", True, ledger, artifacts, 1)
    _commerce_record_success(register, "run-3", ledger, artifacts)
    with pytest.raises(SecurityError, match="拒绝重复副作用"):
        _commerce_preflight(register, _sandbox_config(), "run-3", True, ledger, artifacts, 2)

    _commerce_preflight(cleanup, _sandbox_config(), "run-3", True, ledger, artifacts, 3)
    _commerce_record_success(cleanup, "run-3", ledger, artifacts)

    assert ledger["resource:E2E_CART_LINE_1"].status == LedgerStatus.CLEANED
    saved = artifacts.documents["commerce-resource-ledger.json"]
    assert saved["entries"][0]["status"] == "cleaned"
    assert saved["entries"][0]["cleanedAt"]
