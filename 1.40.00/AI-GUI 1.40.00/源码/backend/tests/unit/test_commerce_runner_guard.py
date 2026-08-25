from pathlib import Path

import pytest
from pydantic import ValidationError

from gui_agent.commerce import CommerceStepMetadata, LedgerStatus
from gui_agent.domain.models import Step
from gui_agent.domain.models import TestPlan as DomainTestPlan
from gui_agent.api.server import _apply_scenario_commerce, _validate_scenario_commerce
from gui_agent.onboarding.models import ScenarioConfig
from fastapi import HTTPException
from gui_agent.execution.runner import (
    RunnerConfig,
    _commerce_preflight,
    _commerce_record_success,
    _commerce_run_summary,
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


def test_production_order_requires_special_controls_then_allows_pending_payment() -> None:
    step = _step(
        "submit_order",
        targetKind="orderId",
        targetRef="resource:E2E_ORDER_1",
        beforeState="checkout_ready",
        cleanupAction="cancel sandbox order",
        idempotencyKeyRef="secret:E2E_ORDER_IDEMPOTENCY",
        e2eOwned=True,
    )
    with pytest.raises(SecurityError, match="缺少完整专项授权"):
        _commerce_preflight(
            step,
            _sandbox_config(commerce_environment="production_readonly"),
            "run-1",
            True,
            {},
            ArtifactProbe(),
            1,
        )
    _commerce_preflight(
        step,
        _sandbox_config(
            commerce_environment="production_readonly",
            commerce_production_reversible_write_authorized=True,
            commerce_fixed_product_ref="public-sku:TEST_SKU",
            commerce_fixed_address_ref="JD_TEST_ADDRESS",
            commerce_written_authorization_ref="AUTH-2026-001",
            commerce_automatic_cancellation_verified=True,
        ),
        "run-1", True, {}, ArtifactProbe(), 1,
    )


def test_isolated_payment_is_absolutely_forbidden() -> None:
    step = _step(
        "pay",
        targetKind="paymentId",
        targetRef="resource:E2E_PAYMENT_1",
        beforeState="pending",
        cleanupAction="refund sandbox payment",
        e2eOwned=True,
    )
    with pytest.raises(SecurityError, match="绝对禁止"):
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
    pending_summary = _commerce_run_summary(_sandbox_config(), [], ledger)
    assert pending_summary["zeroResidual"] is False
    assert len(pending_summary["pendingResources"]) == 1
    with pytest.raises(SecurityError, match="拒绝重复副作用"):
        _commerce_preflight(register, _sandbox_config(), "run-3", True, ledger, artifacts, 2)

    _commerce_preflight(cleanup, _sandbox_config(), "run-3", True, ledger, artifacts, 3)
    _commerce_record_success(cleanup, "run-3", ledger, artifacts)

    assert ledger["resource:E2E_CART_LINE_1"].status == LedgerStatus.CLEANED
    saved = artifacts.documents["commerce-resource-ledger.json"]
    assert saved["entries"][0]["status"] == "cleaned"
    assert saved["entries"][0]["cleanedAt"]
    cleaned_summary = _commerce_run_summary(_sandbox_config(), [], ledger)
    assert cleaned_summary["zeroResidual"] is True
    assert cleaned_summary["pendingResources"] == []


def test_saved_scenario_restores_and_enforces_commerce_step_metadata() -> None:
    scenario = ScenarioConfig(
        id="scenario-commerce",
        projectId="project-commerce",
        name="cart scenario",
        preconditions=["dedicated account"],
        goal="add an E2E item to cart",
        expectedResults=["cart line exists"],
        commerceSteps=[{
            "stepIndex": 2,
            "commerce": {
                "action": "add_cart",
                "targetKind": "cartLineId",
                "targetRef": "resource:E2E_CART_LINE_1",
                "beforeState": "absent",
                "cleanupAction": "remove cart line",
                "e2eOwned": True,
                "ledgerOperation": "register",
            },
        }],
    )
    plan = DomainTestPlan(
        name="cart",
        base_url="https://example.com",
        steps=[
            Step(action="navigate", target="/"),
            Step(action="click", locator={"text": "add to cart"}),
        ],
    )

    restored = _apply_scenario_commerce(plan, scenario)
    assert restored.steps[1].commerce.action.value == "add_cart"
    _validate_scenario_commerce(restored, scenario)

    with pytest.raises(HTTPException, match="电商安全语义"):
        _validate_scenario_commerce(plan, scenario)


def test_saved_scenario_rejects_duplicate_commerce_step_indexes() -> None:
    binding = {
        "stepIndex": 1,
        "commerce": {"action": "browse", "e2eOwned": False, "ledgerOperation": "none"},
    }
    with pytest.raises(ValidationError, match="不能重复"):
        ScenarioConfig(
            id="scenario-duplicate",
            projectId="project-commerce",
            name="duplicate",
            preconditions=["none"],
            goal="browse",
            expectedResults=["visible"],
            commerceSteps=[binding, binding],
        )


def test_saved_scenario_restores_browser_context_and_human_takeover() -> None:
    scenario = ScenarioConfig(
        id="scenario-login",
        projectId="project-commerce",
        name="protected login",
        preconditions=["none"],
        goal="complete QR login",
        expectedResults=["account visible"],
        executionSteps=[{
            "stepIndex": 1,
            "browserTarget": {"page": "newest", "urlContains": "/account", "waitTimeoutMs": 120000},
            "action": "human_takeover",
            "takeoverReason": "qr_login",
        }],
    )
    plan = DomainTestPlan(
        name="login", base_url="https://example.com",
        steps=[Step(action="navigate", target="/")],
    )
    restored = _apply_scenario_commerce(plan, scenario)
    assert restored.steps[0].action.value == "human_takeover"
    assert restored.steps[0].browser_target.page == "newest"
    assert restored.steps[0].stability_level.value == "D"
    _validate_scenario_commerce(restored, scenario)
