from datetime import datetime

from gui_agent.domain.models import ActionType, Locator, Step
from gui_agent.domain.results import Observation, Status, StepResult
from gui_agent.execution.runner import (
    _link_deferred_after_evidence,
    _should_defer_after_route_transition,
    _should_defer_before_transition_wait,
)


def test_route_transition_evidence_is_deferred_to_the_following_wait() -> None:
    click = Step(
        action=ActionType.CLICK,
        locator=Locator(role="menuitem", name="Projects"),
        description="Open Projects",
    )
    wait = Step(
        action=ActionType.WAIT_FOR,
        locator=Locator(role="heading", name="Projects"),
        value="visible",
        description="Wait for Projects",
    )

    assert _should_defer_after_route_transition(
        click,
        wait,
        "https://app.example.test/#/dashboard",
        "https://app.example.test/#/projects",
    )
    assert _should_defer_before_transition_wait(wait, click)


def test_same_route_click_followed_by_wait_also_defers_evidence() -> None:
    click = Step(
        action=ActionType.CLICK,
        locator=Locator(role="button", name="筛选"),
        description="Apply a filter",
    )
    wait = Step(
        action=ActionType.WAIT_FOR,
        locator=Locator(role="table"),
        value="visible",
        description="Wait for results",
    )

    assert _should_defer_after_route_transition(
        click,
        wait,
        "https://app.example.test/#/list",
        "https://app.example.test/#/list",
    )


def test_wait_screenshot_is_linked_to_deferred_route_step() -> None:
    now = datetime.now().astimezone()
    unsettled = Observation(url="https://ion.cesium.com/usage", title="Usage")
    settled = Observation(
        url="https://ion.cesium.com/usage",
        title="Usage | Cesium ion",
        screenshot="screenshots/step-11-after.png",
    )
    steps = [
        StepResult(
            index=10,
            action="click",
            target_summary="Usage",
            status=Status.PASSED,
            started_at=now,
            ended_at=now,
            screenshot=None,
            after=unsettled,
        ),
        StepResult(
            index=11,
            action="wait_for",
            target_summary="Usage loading hidden",
            status=Status.PASSED,
            started_at=now,
            ended_at=now,
            screenshot="screenshots/step-11-after.png",
            after=settled,
        ),
    ]

    linked = _link_deferred_after_evidence(
        steps,
        settled,
        "screenshots/step-11-after.png",
    )

    assert linked is True
    assert steps[0].screenshot == "screenshots/step-11-after.png"
    assert steps[0].after == settled
