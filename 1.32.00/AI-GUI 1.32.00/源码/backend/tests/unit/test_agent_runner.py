from gui_agent.domain.models import Step
import pytest

from gui_agent.execution.agent_runner import (
    _ModelRecoveryStopped,
    _approval_rule,
    _check_agent_step,
    _decide_with_model_recovery,
    _exclude_wait_from_timers,
    _is_recognized_cesium_loading_wait,
)
from gui_agent.execution.confirmation import confirmation_match, request_confirmation
from gui_agent.planning.ai_provider import AIProviderError
from gui_agent.security.policy import SecurityError


class FakeContext:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def unroute(self, pattern: str, handler) -> None:
        self.calls.append(("unroute", pattern, handler))

    def route(self, pattern: str, handler) -> None:
        self.calls.append(("route", pattern, handler))


class FakeArtifacts:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def event(self, name: str, **payload) -> None:
        self.calls.append(("event", name, payload))


def test_read_only_search_fill_does_not_request_write_approval() -> None:
    step = Step(
        action="fill",
        locator={"role": "searchbox", "name": "Search"},
        value="no-match",
        effect_kind="browse_search_filter_sort",
        effect_level="read_only",
    )

    assert _approval_rule(step, "ask", None) is None


def test_only_known_cesium_loading_wait_is_exempt_from_generic_no_progress_limit() -> None:
    loading = Step(
        action="screenshot",
        description="Cesium ion 仍在启动，保持当前页面并短暂等待可交互内容出现。",
        waitBeforeMs=10_000,
        effect_kind="browse_search_filter_sort",
        effect_level="read_only",
    )
    generic = Step(
        action="screenshot",
        description="等待其他页面",
        effect_kind="browse_search_filter_sort",
        effect_level="read_only",
    )

    assert _is_recognized_cesium_loading_wait(loading) is True
    assert _is_recognized_cesium_loading_wait(generic) is False


def test_unclassified_fill_and_human_takeover_still_request_approval() -> None:
    fill = Step(action="fill", locator={"label": "Name"}, value="new value")
    takeover = Step(
        action="human_takeover",
        takeoverReason="other",
        browserTarget={"urlContains": "ion.cesium.com"},
        stability_level="D",
        effect_kind="browse_search_filter_sort",
        effect_level="read_only",
    )

    assert _approval_rule(fill, "ask", None) == "approval-mode:write-action"
    assert _approval_rule(takeover, "ask", None) == "approval-mode:write-action"


def test_human_takeover_temporarily_pauses_network_guard() -> None:
    calls: list[tuple] = []
    handler = object()
    context = FakeContext(calls)
    artifacts = FakeArtifacts(calls)
    step = Step(
        action="human_takeover",
        takeoverReason="other",
        browserTarget={"urlContains": "ion.cesium.com"},
        stability_level="D",
    )

    approved = request_confirmation(
        context,
        handler,
        artifacts.event,
        lambda *_: calls.append(("callback",)) or True,
        step,
        4,
        "human_takeover:other",
    )

    assert approved is True
    assert [call[0] for call in calls] == ["unroute", "event", "callback", "route", "event"]
    assert calls[0][2] is handler
    assert calls[3][2] is handler


def test_regular_confirmation_keeps_network_guard_enabled() -> None:
    calls: list[tuple] = []
    approved = request_confirmation(
        FakeContext(calls),
        object(),
        FakeArtifacts(calls).event,
        lambda *_: calls.append(("callback",)) or False,
        Step(action="click", locator={"role": "button", "name": "提交"}),
        2,
        "side_effect:create",
    )

    assert approved is False
    assert calls == [("callback",)]


def test_confirmation_ignores_negated_danger_words_in_description() -> None:
    browse = Step(
        action="click",
        locator={"role": "link", "name": "My Assets"},
        description="只读浏览，不创建、不修改或删除任何资产",
    )
    search = Step(
        action="fill",
        locator={"role": "searchbox", "name": "Search"},
        value="E2E_NONEXISTENT",
        description="检查空状态，不上传、创建、修改或删除资产",
    )

    assert confirmation_match(browse) is None
    assert confirmation_match(search) is None


def test_forbidden_policy_ignores_negated_safety_boundary_in_description() -> None:
    preview = Step(
        action="click",
        locator={"role": "button", "name": "Preview"},
        description="检查预览加载反馈，不修改资产",
    )

    _check_agent_step(preview, ("修改资产",))


def test_forbidden_policy_still_blocks_real_action_target() -> None:
    modify = Step(
        action="click",
        locator={"role": "button", "name": "修改资产"},
        description="执行操作",
    )

    with pytest.raises(SecurityError, match="Agent 动作命中禁止策略：修改资产"):
        _check_agent_step(modify, ("修改资产",))


def test_confirmation_still_matches_dangerous_locator_target() -> None:
    step = Step(
        action="click",
        locator={"role": "button", "name": "删除客户"},
        description="执行已授权的清理动作",
    )

    assert confirmation_match(step) == "删除"


def test_confirmation_ignores_future_cleanup_action_for_read_only_entry_click() -> None:
    step = Step(
        action="click",
        locator={"role": "button", "name": "Add data"},
        description="打开上传入口，不选择或提交文件",
        effect_kind="upload_or_cloud_import",
        effect_level="reversible_write",
        cleanup_required=True,
        cleanup_action="delete ledger-owned asset/task",
    )

    assert confirmation_match(step) is None


def test_transient_model_failure_recovers_in_same_decision() -> None:
    calls = 0
    sleeps: list[float] = []

    def decide():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AIProviderError("temporary", retryable=True)
        return "continued"

    assert _decide_with_model_recovery(decide, sleep_fn=sleeps.append) == "continued"
    assert calls == 2
    assert sleeps == [2.0]


def test_permanent_model_failure_is_not_retried() -> None:
    calls = 0

    def decide():
        nonlocal calls
        calls += 1
        raise AIProviderError("HTTP 401")

    with pytest.raises(AIProviderError, match="401"):
        _decide_with_model_recovery(decide, sleep_fn=lambda _seconds: None)
    assert calls == 1


def test_exhausted_transient_failures_wait_then_resume_without_losing_state() -> None:
    calls = 0
    completed_steps = ["first Cesium check"]

    def decide():
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise AIProviderError("temporary", retryable=True)
        return "second check continued"

    result = _decide_with_model_recovery(
        decide,
        wait_for_retry=lambda _error: completed_steps == ["first Cesium check"],
        sleep_fn=lambda _seconds: None,
    )

    assert result == "second check continued"
    assert completed_steps == ["first Cesium check"]
    assert calls == 4


def test_exhausted_transient_failures_can_stop_recovery_wait() -> None:
    with pytest.raises(_ModelRecoveryStopped):
        _decide_with_model_recovery(
            lambda: (_ for _ in ()).throw(AIProviderError("temporary", retryable=True)),
            wait_for_retry=lambda _error: False,
            sleep_fn=lambda _seconds: None,
        )


def test_user_wait_is_excluded_from_run_and_current_goal_timers() -> None:
    assert _exclude_wait_from_timers(100.0, 120.0, 150.0, 450.0) == (400.0, 420.0)
