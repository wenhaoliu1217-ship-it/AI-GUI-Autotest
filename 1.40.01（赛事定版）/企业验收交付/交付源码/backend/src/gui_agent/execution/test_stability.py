from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError

from gui_agent.domain.models import ActionType, Locator, Step
from gui_agent.execution import stability


class FakeLocator:
    def __init__(
        self,
        evidence: dict,
        *,
        wait_error: Exception | None = None,
        visible: bool = True,
    ) -> None:
        self.evidence = evidence
        self.expression = ""
        self.wait_error = wait_error
        self.visible = visible

    def count(self) -> int:
        return 1

    def wait_for(self, **_kwargs) -> None:
        if self.wait_error is not None:
            raise self.wait_error
        return None

    def is_visible(self, **_kwargs) -> bool:
        return self.visible

    def evaluate(self, expression: str, _options: dict) -> dict:
        self.expression = expression
        return dict(self.evidence)


def _evidence(
    *,
    unoccluded: bool,
    composite_proxy: bool = False,
    shadow_pierced: bool = False,
    shadow_host_proxy: bool = False,
) -> dict:
    return {
        "checked": True,
        "mode": "locator",
        "visible": True,
        "enabled": True,
        "stable": True,
        "unoccluded": unoccluded,
        "shadowPierced": shadow_pierced,
        "shadowHostProxy": shadow_host_proxy,
        "compositeProxy": composite_proxy,
    }


def test_closed_combobox_can_use_its_visible_composite_as_occlusion_proxy(monkeypatch) -> None:
    locator = FakeLocator(_evidence(unoccluded=True, composite_proxy=True))
    monkeypatch.setattr(stability, "resolve_step_locator", lambda *_args, **_kwargs: locator)

    prepared = stability.prepare_action(
        object(),
        Step(action=ActionType.FILL, locator=Locator(css='input[role="combobox"]'), value="E2E"),
        bridge_adapter=None,
        timeout_ms=1_000,
    )

    assert prepared.evidence["passed"] is True
    assert prepared.evidence["compositeProxy"] is True
    assert "element.getAttribute('role') === 'combobox'" in locator.expression
    assert "aria-expanded') === 'true'" not in locator.expression
    assert "compositeProxy" in locator.expression


def test_open_shadow_dom_target_uses_deep_hit_test(monkeypatch) -> None:
    locator = FakeLocator(
        _evidence(unoccluded=True, shadow_pierced=True, shadow_host_proxy=True)
    )
    monkeypatch.setattr(stability, "resolve_step_locator", lambda *_args, **_kwargs: locator)

    prepared = stability.prepare_action(
        object(),
        Step(action=ActionType.FILL, locator=Locator(role="searchbox", name="Search"), value="E2E"),
        bridge_adapter=None,
        timeout_ms=1_000,
    )

    assert prepared.evidence["passed"] is True
    assert prepared.evidence["shadowPierced"] is True
    assert prepared.evidence["shadowHostProxy"] is True
    assert "shadowRoot.elementFromPoint" in locator.expression
    assert "shadowHostProxy" in locator.expression


def test_press_action_uses_locator_stability_checks(monkeypatch) -> None:
    locator = FakeLocator(_evidence(unoccluded=True, shadow_pierced=True))
    monkeypatch.setattr(stability, "resolve_step_locator", lambda *_args, **_kwargs: locator)

    prepared = stability.prepare_action(
        object(),
        Step(
            action=ActionType.PRESS,
            locator=Locator(role="searchbox", name="Search"),
            value="Enter",
        ),
        bridge_adapter=None,
        timeout_ms=1_000,
    )

    assert prepared.evidence["passed"] is True
    assert prepared.evidence["shadowPierced"] is True
    assert "shadowRoot.elementFromPoint" in locator.expression


def test_non_composite_occlusion_is_still_rejected(monkeypatch) -> None:
    locator = FakeLocator(_evidence(unoccluded=False))
    monkeypatch.setattr(stability, "resolve_step_locator", lambda *_args, **_kwargs: locator)

    with pytest.raises(PlaywrightError, match="unoccluded"):
        stability.prepare_action(
            object(),
            Step(action=ActionType.CLICK, locator=Locator(role="button", name="Save")),
            bridge_adapter=None,
            timeout_ms=1_000,
        )


def test_visible_rerendered_input_recovers_from_spurious_wait_timeout(monkeypatch) -> None:
    locator = FakeLocator(
        _evidence(unoccluded=True),
        wait_error=PlaywrightError("wait timed out after resolving a visible input"),
    )
    monkeypatch.setattr(stability, "resolve_step_locator", lambda *_args, **_kwargs: locator)

    prepared = stability.prepare_action(
        object(),
        Step(
            action=ActionType.FILL,
            locator=Locator(css="#basic_wpsKeyword"),
            value="test_H_path",
        ),
        bridge_adapter=None,
        timeout_ms=1_000,
    )

    assert prepared.evidence["passed"] is True
    assert prepared.evidence["visibilityWaitFallback"] == "immediate_current_document_check"


def test_click_does_not_bypass_visibility_wait_timeout(monkeypatch) -> None:
    locator = FakeLocator(
        _evidence(unoccluded=True),
        wait_error=PlaywrightError("wait timed out"),
    )
    monkeypatch.setattr(stability, "resolve_step_locator", lambda *_args, **_kwargs: locator)

    with pytest.raises(PlaywrightError, match="wait timed out"):
        stability.prepare_action(
            object(),
            Step(action=ActionType.CLICK, locator=Locator(role="button", name="Save")),
            bridge_adapter=None,
            timeout_ms=1_000,
        )
