import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from gui_agent.domain.models import Step
from gui_agent.domain.results import Observation
from gui_agent.execution.transition import stabilize_after_action
from gui_agent.execution.verification import ActionContract, ActionVerificationError


class ReadyPage:
    url = "https://ion.cesium.com/usage"

    def __init__(self) -> None:
        self.expected = None

    def wait_for_function(self, expression: str, *, arg: dict, timeout: int) -> None:
        assert "document.title" in expression
        assert timeout == 30_000
        self.expected = arg

    @staticmethod
    def title() -> str:
        return "Usage | Cesium ion"


def test_spa_transition_waits_for_route_and_heading_contract() -> None:
    page = ReadyPage()
    evidence = stabilize_after_action(
        page,
        Step(action="click", locator={"role": "link", "name": "Usage"}),
        ActionContract(
            contract_id="step-1:click",
            action="click",
            expected_route_prefix="/usage",
            expected_heading="Usage",
        ),
        Observation(url="https://ion.cesium.com/tokens", title="Access Tokens | Cesium ion"),
        timeout_ms=30_000,
    )

    assert page.expected == {"routePrefix": "/usage", "heading": "Usage"}
    assert evidence["passed"] is True
    assert evidence["actualTitle"] == "Usage | Cesium ion"


def test_spa_transition_timeout_is_a_verification_failure() -> None:
    class StalePage(ReadyPage):
        def wait_for_function(self, *_args, **_kwargs) -> None:
            raise PlaywrightTimeoutError("stale shell")

    with pytest.raises(ActionVerificationError, match="expected route content"):
        stabilize_after_action(
            StalePage(),
            Step(action="click", locator={"role": "link", "name": "Usage"}),
            ActionContract(
                contract_id="step-1:click",
                action="click",
                expected_route_prefix="/usage",
                expected_heading="Usage",
            ),
            Observation(url="https://ion.cesium.com/tokens"),
            timeout_ms=30_000,
        )


def test_unknown_route_transition_waits_for_generic_loading_to_settle() -> None:
    class GenericPage:
        url = "http://192.168.31.218:7991/#/agentEditPage"

        def __init__(self) -> None:
            self.sleep_ms = 0
            self.expression = ""

        def wait_for_timeout(self, milliseconds: int) -> None:
            self.sleep_ms = milliseconds

        def wait_for_function(self, expression: str, *, timeout: int) -> None:
            self.expression = expression
            assert timeout == 30_000

    page = GenericPage()
    evidence = stabilize_after_action(
        page,
        Step(action="visual_click", execution_mode="visual", visual_target="编辑", relative_position={"xRatio": 0.5, "yRatio": 0.5}, stability_level="C"),
        ActionContract(contract_id="step-5:visual_click", action="visual_click"),
        Observation(url="http://192.168.31.218:7991/#/mineModelList"),
        timeout_ms=30_000,
    )

    assert evidence["reason"] == "generic_route_settlement"
    assert evidence["settled"] is True
    assert page.sleep_ms == 350
    assert ".page-loading-placeholder" in page.expression


def test_unknown_route_transition_keeps_unsettled_state_evidence() -> None:
    class StuckPage:
        url = "http://192.168.31.218:7991/#/agentEditPage"

        @staticmethod
        def wait_for_timeout(_milliseconds: int) -> None:
            return None

        @staticmethod
        def wait_for_function(*_args, **_kwargs) -> None:
            raise PlaywrightTimeoutError("3D loader remains visible")

    evidence = stabilize_after_action(
        StuckPage(),
        Step(action="click", locator={"role": "button", "name": "编辑"}),
        ActionContract(contract_id="step-5:click", action="click"),
        Observation(url="http://192.168.31.218:7991/#/mineModelList"),
        timeout_ms=30_000,
    )

    assert evidence["reason"] == "generic_route_settlement_timeout"
    assert evidence["settled"] is False
