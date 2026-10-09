from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from gui_agent.domain.models import ActionType, Locator, Step
from gui_agent.execution.recovery import execute_with_recovery


def test_read_only_wait_timeout_rebuilds_page_once_and_retries() -> None:
    step = Step(action=ActionType.WAIT_FOR, locator=Locator(text="Name"), value="visible")
    calls = 0
    rebuilt = 0

    def execute() -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PlaywrightTimeoutError("SPA bootstrap did not expose Name")
        return {"ready": True}

    def recover() -> None:
        nonlocal rebuilt
        rebuilt += 1

    result, evidence = execute_with_recovery(
        step,
        execute,
        wait=lambda _milliseconds: None,
        recover_session=recover,
        max_read_attempts=2,
    )

    assert result == {"ready": True}
    assert calls == 2
    assert rebuilt == 1
    assert evidence["decision"] == "succeeded_after_retry"
    assert evidence["attempts"][0]["failureClass"] == "condition_timeout"


def test_read_only_navigation_commit_timeout_rebuilds_page_once_and_retries() -> None:
    step = Step(action=ActionType.NAVIGATE, target="/stories/example")
    calls = 0
    rebuilt = 0

    def execute() -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PlaywrightTimeoutError("Page.goto timed out waiting for commit")
        return {"navigationReady": True}

    def recover() -> None:
        nonlocal rebuilt
        rebuilt += 1

    result, evidence = execute_with_recovery(
        step,
        execute,
        wait=lambda _milliseconds: None,
        recover_session=recover,
        max_read_attempts=2,
    )

    assert result == {"navigationReady": True}
    assert calls == 2
    assert rebuilt == 1
    assert evidence["attempts"][0]["failureClass"] == "navigation_timeout"
