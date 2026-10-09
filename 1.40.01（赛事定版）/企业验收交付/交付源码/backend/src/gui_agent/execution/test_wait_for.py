from gui_agent.domain.models import Step
from gui_agent.domain.results import FailureCategory
from gui_agent.execution.agent_runner import (
    _should_replan_after_locator_failure,
    _should_replan_after_read_failure,
    _should_replan_after_wait_failure,
)
from gui_agent.execution import runner, stability
from gui_agent.security.redaction import Redactor


class DelayedLocator:
    def __init__(self) -> None:
        self.visible = False
        self.timeout = None
        self.events: list[str] = []

    @property
    def first(self):
        return self

    def wait_for(self, *, state: str, timeout: int) -> None:
        self.events.append("wait")
        assert state == "visible"
        self.timeout = timeout
        self.visible = True

    def count(self) -> int:
        self.events.append("count")
        return 1 if self.visible else 0


def _wait_step() -> Step:
    return Step(
        action="wait_for",
        locator={"role": "button", "name": "Sign In"},
        description="Wait for the application to finish loading",
    )


def test_wait_for_skips_presence_dependent_stability_precheck(monkeypatch) -> None:
    def unexpected_resolver(*_args, **_kwargs):
        raise AssertionError("wait_for must not resolve its target during stability precheck")

    monkeypatch.setattr(stability, "resolve_step_locator", unexpected_resolver)

    prepared = stability.prepare_action(
        object(),
        _wait_step(),
        bridge_adapter=None,
        timeout_ms=10_000,
    )

    assert prepared.evidence == {
        "checked": False,
        "passed": True,
        "mode": "deferred_wait",
    }


def test_wait_for_uses_full_execution_timeout_before_uniqueness_check(monkeypatch) -> None:
    locator = DelayedLocator()
    monkeypatch.setattr(runner, "resolve_step_locator", lambda *_args, **_kwargs: locator)

    runner._execute_step(
        object(),
        _wait_step(),
        "https://ion.cesium.com",
        object(),
        Redactor(),
        timeout_ms=30_000,
    )

    assert locator.timeout == 30_000
    assert locator.events == ["wait", "count"]


def test_hidden_wait_accepts_an_already_absent_target(monkeypatch) -> None:
    class AbsentLocator:
        events: list[tuple[str, object]] = []

        def wait_for(self, *, state: str, timeout: int) -> None:
            self.events.append((state, timeout))

        @staticmethod
        def count() -> int:
            return 0

    locator = AbsentLocator()
    monkeypatch.setattr(runner, "resolve_step_locator", lambda *_args, **_kwargs: locator)
    step = Step(
        action="wait_for",
        locator={"css": ".page-loading-placeholder"},
        value="hidden",
    )

    result = runner._execute_step(
        object(), step, "https://ion.cesium.com", object(), Redactor(), timeout_ms=30_000
    )

    assert result == {"waitState": "hidden", "matchedCount": 0}
    assert locator.events == []


def test_hidden_wait_accepts_multiple_matching_loading_indicators(monkeypatch) -> None:
    events: list[tuple[int, str]] = []

    class Match:
        def __init__(self, index: int) -> None:
            self.index = index

        def wait_for(self, *, state: str, timeout: int) -> None:
            assert timeout > 0
            events.append((self.index, state))

    class MultipleLocator:
        @staticmethod
        def count() -> int:
            return 4

        @staticmethod
        def nth(index: int) -> Match:
            return Match(index)

    monkeypatch.setattr(runner, "resolve_step_locator", lambda *_args, **_kwargs: MultipleLocator())
    step = Step(
        action="wait_for",
        locator={"css": ".loading-message"},
        value="hidden",
    )

    result = runner._execute_step(
        object(), step, "https://ion.cesium.com", object(), Redactor(), timeout_ms=30_000
    )

    assert result == {"waitState": "hidden", "matchedCount": 4}
    assert events == [(3, "hidden"), (2, "hidden"), (1, "hidden"), (0, "hidden")]


def test_navigation_readiness_uses_full_timeout_and_is_best_effort() -> None:
    calls: list[tuple[str, int]] = []

    class SlowPage:
        @staticmethod
        def wait_for_function(script: str, *, timeout: int) -> None:
            calls.append((script, timeout))
            raise runner.PlaywrightTimeoutError("still loading")

    runner._wait_for_navigation_readiness(SlowPage(), 45_000)

    assert calls[0][1] == 45_000
    assert "page-loading-placeholder" in calls[0][0]
    assert "aria-busy" in calls[0][0]
    assert "complete|completed|done|finished" in calls[0][0]
    assert "aria-valuenow" in calls[0][0]


def test_click_dispatch_does_not_wait_for_async_navigation(monkeypatch) -> None:
    calls: list[dict] = []
    settlement: list[tuple[str, bool]] = []

    class FakeTarget:
        @property
        def first(self):
            return self

        @staticmethod
        def get_attribute(name: str):
            assert name == "href"
            return None

        def click(self, **kwargs) -> None:
            calls.append(kwargs)

    class FakePage:
        url = "http://192.168.31.218:7991/#/situationPage?type=run&simulationStatus=Unstart"

    step = Step(
        action="click",
        locator={"role": "button", "name": "启动"},
        description="启动异步仿真",
    )
    target = FakeTarget()
    monkeypatch.setattr(runner, "resolve_step_locator", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(
        runner,
        "_wait_for_client_settlement",
        lambda page, before, timeout, *, require_url_change, run_budget=None: settlement.append(
            (before, require_url_change)
        ),
    )

    runner._execute_step(
        FakePage(), step, "http://192.168.31.218:7991", object(), Redactor(), timeout_ms=30_000
    )

    assert calls == [{"no_wait_after": True}]
    assert settlement == [(FakePage.url, False)]


def test_agent_replans_only_recoverable_wait_failures() -> None:
    assert _should_replan_after_wait_failure(_wait_step(), FailureCategory.LOCATOR)
    assert _should_replan_after_wait_failure(_wait_step(), FailureCategory.TIMEOUT)
    assert not _should_replan_after_wait_failure(_wait_step(), FailureCategory.SECURITY)
    assert not _should_replan_after_wait_failure(
        Step(action="click", locator={"role": "button", "name": "Next"}),
        FailureCategory.LOCATOR,
    )


def test_agent_replans_recoverable_navigation_failures() -> None:
    step = Step(
        action="navigate",
        target="/missing",
        description="Inspect a read-only page",
    )

    assert _should_replan_after_read_failure(step, FailureCategory.LOCATOR)
    assert _should_replan_after_read_failure(step, FailureCategory.NAVIGATION)
    assert _should_replan_after_read_failure(step, FailureCategory.TIMEOUT)
    assert not _should_replan_after_read_failure(step, FailureCategory.SECURITY)
    assert not _should_replan_after_read_failure(step, FailureCategory.BUSINESS_STATE)


def test_agent_replans_click_only_when_pre_action_check_failed() -> None:
    step = Step(action="click", locator={"role": "button", "name": "Next"})

    assert _should_replan_after_locator_failure(
        step, FailureCategory.LOCATOR, {"checked": True, "passed": False}
    )
    assert not _should_replan_after_locator_failure(
        step, FailureCategory.LOCATOR, {"checked": True, "passed": True}
    )
    assert not _should_replan_after_locator_failure(
        step, FailureCategory.SECURITY, {"checked": True, "passed": False}
    )
