from gui_agent.domain.models import Step
from gui_agent.domain.results import Status
import pytest

from gui_agent.execution.agent_runner import (
    _ModelRecoveryStopped,
    _approval_rule,
    _agent_step_visual_authorized,
    _check_agent_step,
    _decide_with_model_recovery,
    _exclude_wait_from_timers,
    _is_recognized_cesium_loading_wait,
    _model_recovery_stop_outcome,
)
from gui_agent.execution.runner import _click_with_native_dialog_policy, _execute_step
from gui_agent.execution.confirmation import confirmation_match, request_confirmation
from gui_agent.planning.ai_provider import AIProviderError
from gui_agent.security.policy import SecurityError
from gui_agent.security.redaction import Redactor


class FakeContext:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def unroute(self, pattern: str, handler) -> None:
        self.calls.append(("unroute", pattern, handler))

    def route(self, pattern: str, handler) -> None:
        self.calls.append(("route", pattern, handler))


def test_agent_allows_bounded_canvas_geometry_without_screenshot_adapter() -> None:
    polygon = Step(
        action="visual_draw_polygon",
        execution_mode="visual",
        stability_level="C",
        canvas_region_locator={"css": "#map"},
        visual_target="地图边界",
        visual_points=[
            {"xRatio": 0.1, "yRatio": 0.1},
            {"xRatio": 0.9, "yRatio": 0.1},
            {"xRatio": 0.5, "yRatio": 0.9},
        ],
    )
    click = Step(
        action="visual_click",
        execution_mode="visual",
        stability_level="C",
        locator={"css": "#map"},
        visual_target="地图目标",
        relative_position={"xRatio": 0.5, "yRatio": 0.5},
    )

    assert _agent_step_visual_authorized(polygon, "action") is True
    assert _agent_step_visual_authorized(click, "action") is False
    assert _agent_step_visual_authorized(click, "visual") is True


class FakeArtifacts:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def event(self, name: str, **payload) -> None:
        self.calls.append(("event", name, payload))


class FakeReloadPolicy:
    def __init__(self) -> None:
        self.checked: list[str] = []

    def check_url(self, url: str) -> None:
        self.checked.append(url)

    def clear_rejection(self) -> None:
        return None

    def consume_rejection(self):
        return None


class FakeReloadPage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.goto_calls: list[str] = []
        self.reload_calls = 0

    def goto(self, url: str, *, wait_until: str):
        self.goto_calls.append(url)
        self.url = url
        return None

    def reload(self, *, wait_until: str):
        self.reload_calls += 1
        return None


class FakeDialog:
    type = "confirm"

    def __init__(self) -> None:
        self.accepted = False

    def accept(self) -> None:
        self.accepted = True


class FakeDialogPage:
    def __init__(self) -> None:
        self.handler = None
        self.removed = None

    def on(self, event: str, handler) -> None:
        assert event == "dialog"
        self.handler = handler

    def remove_listener(self, event: str, handler) -> None:
        assert event == "dialog"
        self.removed = handler


class FakeDialogTarget:
    def __init__(self, page: FakeDialogPage, dialog: FakeDialog) -> None:
        self.page = page
        self.dialog = dialog

    def click(self) -> None:
        if self.page.handler is not None:
            self.page.handler(self.dialog)


class FakeCanvasLocator:
    def __init__(self, calls: list[tuple], selector: str) -> None:
        self.calls = calls
        self.selector = selector

    @property
    def first(self):
        return self

    def bounding_box(self) -> dict[str, float]:
        return {"x": 100, "y": 50, "width": 400, "height": 200}

    def count(self) -> int:
        return 1

    def click(self) -> None:
        self.calls.append(("locator_click", self.selector))


class FakeCanvasMouse:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def move(self, x: float, y: float, **kwargs) -> None:
        self.calls.append(("move", x, y, kwargs))

    def click(self, x: float, y: float) -> None:
        self.calls.append(("click", x, y))

    def dblclick(self, x: float, y: float) -> None:
        self.calls.append(("dblclick", x, y))

    def down(self) -> None:
        self.calls.append(("down",))

    def up(self) -> None:
        self.calls.append(("up",))

    def wheel(self, x: float, y: float) -> None:
        self.calls.append(("wheel", x, y))


class FakeCanvasKeyboard:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def press(self, key: str) -> None:
        self.calls.append(("key", key))


class FakeCanvasPage:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.mouse = FakeCanvasMouse(self.calls)
        self.keyboard = FakeCanvasKeyboard(self.calls)

    def locator(self, selector: str) -> FakeCanvasLocator:
        return FakeCanvasLocator(self.calls, selector)


def test_approved_delete_click_accepts_native_confirm_and_removes_handler() -> None:
    page = FakeDialogPage()
    dialog = FakeDialog()
    target = FakeDialogTarget(page, dialog)

    detail = _click_with_native_dialog_policy(page, target, accept_native_dialog=True)

    assert dialog.accepted is True
    assert page.removed is page.handler
    assert detail == {"nativeDialog": {"seen": True, "type": "confirm"}}


def test_unapproved_click_never_installs_native_dialog_acceptance() -> None:
    page = FakeDialogPage()
    dialog = FakeDialog()
    target = FakeDialogTarget(page, dialog)

    detail = _click_with_native_dialog_policy(page, target, accept_native_dialog=False)

    assert dialog.accepted is False
    assert page.handler is None
    assert detail == {}


def test_reload_recovers_internal_browser_error_to_authorized_entry_url() -> None:
    page = FakeReloadPage("chrome-error://chromewebdata/")
    policy = FakeReloadPolicy()

    detail = _execute_step(
        page,
        Step(action="reload"),
        "https://example.com/stories",
        policy,
        Redactor(),
    )

    assert page.goto_calls == ["https://example.com/stories"]
    assert page.reload_calls == 0
    assert policy.checked == ["https://example.com/stories", "https://example.com/stories"]
    assert detail == {"browserContext": {"recoveredFromInternalErrorPage": True}}


def test_reload_keeps_normal_page_reload_behavior() -> None:
    page = FakeReloadPage("https://example.com/stories")
    policy = FakeReloadPolicy()

    detail = _execute_step(
        page,
        Step(action="reload"),
        "https://example.com/stories",
        policy,
        Redactor(),
    )

    assert page.goto_calls == []
    assert page.reload_calls == 1
    assert policy.checked == ["https://example.com/stories"]
    assert detail == {}


def test_canvas_polygon_executes_relative_vertices_and_double_click_finish() -> None:
    page = FakeCanvasPage()
    step = Step(
        action="visual_draw_polygon",
        execution_mode="visual",
        stability_level="C",
        canvas_region_locator={"css": "#map"},
        visual_target="广场边界",
        visual_points=[
            {"xRatio": 0.1, "yRatio": 0.2},
            {"xRatio": 0.9, "yRatio": 0.2},
            {"xRatio": 0.9, "yRatio": 0.8},
            {"xRatio": 0.1, "yRatio": 0.8},
        ],
        gesture_finish="double_click",
    )

    detail = _execute_step(page, step, "https://example.com", FakeReloadPolicy(), Redactor())

    assert page.calls == [
        ("click", 140.0, 90.0),
        ("click", 460.0, 90.0),
        ("click", 460.0, 210.0),
        ("dblclick", 140.0, 210.0),
    ]
    assert detail["coordinateSource"].startswith("canvas-region-relative:polygon:")
    assert detail["coordinateSource"].endswith("finish=double_click")


def test_canvas_rectangle_executes_bounded_drag() -> None:
    page = FakeCanvasPage()
    step = Step(
        action="visual_draw_rectangle",
        execution_mode="visual",
        stability_level="B",
        canvas_region_locator={"css": "#map"},
        visual_target="矩形测量区域",
        visual_points=[
            {"xRatio": 0.25, "yRatio": 0.25},
            {"xRatio": 0.75, "yRatio": 0.75},
        ],
    )

    detail = _execute_step(page, step, "https://example.com", FakeReloadPolicy(), Redactor())

    assert page.calls == [
        ("move", 200.0, 100.0, {}),
        ("down",),
        ("move", 400.0, 200.0, {"steps": 10}),
        ("up",),
    ]
    assert detail["coordinateSource"] == (
        "canvas-region-relative:rectangle:0.2500,0.2500;0.7500,0.7500"
    )


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


def test_share_controls_require_confirmation_but_navigation_link_does_not() -> None:
    direct_share = Step(
        action="click",
        locator={"role": "button", "name": "Share"},
        effect_kind="browse_search_filter_sort",
        effect_level="read_only",
    )
    sharing_switch = Step(
        action="click",
        locator={"role": "switch", "name": "Enable sharing"},
        effect_kind="browse_search_filter_sort",
        effect_level="read_only",
    )
    panel_link = Step(
        action="click",
        locator={"role": "link", "name": "Share"},
        effect_kind="browse_search_filter_sort",
        effect_level="read_only",
    )

    assert confirmation_match(direct_share) == "share_public_content"
    assert confirmation_match(sharing_switch) == "share_public_content"
    assert confirmation_match(panel_link) is None


def test_new_story_button_requires_confirmation_even_if_mislabeled_read_only() -> None:
    step = Step(
        action="click",
        locator={"role": "button", "name": "New story"},
        effect_kind="browse_search_filter_sort",
        effect_level="read_only",
    )

    assert confirmation_match(step) == "create_story"


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


def test_ending_during_model_recovery_is_incomplete_not_passed() -> None:
    assert _model_recovery_stop_outcome("ended") == (
        Status.INCOMPLETE,
        "model_recovery_ended_incomplete",
    )
    assert _model_recovery_stop_outcome("cancelled") == (
        Status.CANCELLED,
        "cancelled_by_user",
    )


def test_user_wait_is_excluded_from_run_and_current_goal_timers() -> None:
    assert _exclude_wait_from_timers(100.0, 120.0, 150.0, 450.0) == (400.0, 420.0)
