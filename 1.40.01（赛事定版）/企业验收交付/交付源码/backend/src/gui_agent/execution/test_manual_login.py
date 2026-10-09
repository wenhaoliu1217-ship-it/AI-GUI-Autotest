from types import SimpleNamespace
from unittest.mock import Mock
from threading import Event

import pytest

from gui_agent.execution import agent_runner, isolation
from gui_agent.execution.orchestrator import RunOrchestrator
from gui_agent.execution.runner import RunnerConfig
from gui_agent.execution.time_budget import RunTimeBudget
from gui_agent.security.policy import DomainPolicy, SecurityError


def observation(url="https://ion.cesium.com/signin/stories/id", title="Sign In | Cesium ion", facts='textbox "Username or email"'):
    return SimpleNamespace(url=url, title=title, accessibility_summary=facts, dom_summary=[])


@pytest.mark.parametrize("url,title,facts,expected", [
    ("https://ion.cesium.com/signin/stories/id", "Sign In | Cesium ion", 'textbox "Username or email"', True),
    ("http://example.com/#/login", "登录", 'input 用户名 密码', True),
    ("https://example.com/", "Home", 'link "Sign in" textbox "Search"', False),
    ("https://example.com/login-help", "Help", 'textbox "Search"', False),
    ("https://example.com/dashboard?next=/login", "Dashboard", 'textbox "Search"', False),
])
def test_login_detection_requires_page_evidence(url, title, facts, expected):
    assert agent_runner._is_interactive_login_page(observation(url, title, facts)) is expected


def test_manual_login_rechecks_and_waits_without_model_calls(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("gui_agent.execution.time_budget.monotonic", lambda: clock[0])
    monkeypatch.setattr(agent_runner, "_capture_screenshot", lambda *a: "masked.png")
    page = Mock(url="https://ion.cesium.com/stories/id")
    logged_in = observation(page.url, "Stories | Cesium ion", 'button "Account"')
    collector = Mock()
    collector.capture.side_effect = [observation(), logged_in]
    questions = []
    def answer(question, number):
        assert "handle" in cfg.manual_login_surface
        questions.append(question)
        clock[0] += 120
        return "我已登录"
    cfg = RunnerConfig(headless=True, clarification_callback=answer)
    budget = RunTimeBudget(1)
    policy = DomainPolicy("https://ion.cesium.com", resolver=lambda _host: {"8.8.8.8"})
    result = agent_runner._wait_for_manual_login(Mock(), page, observation(), cfg, collector, Mock(), budget, policy)
    assert result is logged_in
    assert len(questions) == 2
    assert "仍是登录页面" in questions[-1]
    assert not cfg.manual_login_surface
    assert budget.paused_seconds > 0


def test_manual_login_event_pump_runs_and_isolates_page_errors():
    cfg = RunnerConfig()
    pump = Mock()
    cfg.manual_login_surface["pump"] = pump
    isolation._pump_manual_login_events(cfg)
    pump.assert_called_once_with()

    cfg.manual_login_surface["pump"] = Mock(side_effect=RuntimeError("page navigated"))
    isolation._pump_manual_login_events(cfg)


def test_manual_login_cancel_clears_control():
    cfg = RunnerConfig(headless=True, clarification_callback=lambda *a: None)
    policy = DomainPolicy("https://ion.cesium.com", resolver=lambda _host: {"8.8.8.8"})
    result = agent_runner._wait_for_manual_login(Mock(), Mock(), observation(), cfg, Mock(), Mock(), RunTimeBudget(1), policy)
    assert result is None
    assert not cfg.manual_login_surface


def test_login_relay_only_supports_bounded_inputs():
    page = Mock(viewport_size={"width": 1000, "height": 600})
    policy = Mock()
    agent_runner._manual_login_command(page, policy, {"kind": "click", "x": .5, "y": .25})
    page.mouse.click.assert_called_once_with(500, 150)
    with pytest.raises(ValueError):
        agent_runner._manual_login_command(page, policy, {"kind": "key", "key": "Control+L"})
    with pytest.raises(ValueError):
        agent_runner._manual_login_command(page, policy, {"kind": "evaluate", "text": "x"})


def test_cesium_oauth_hosts_are_only_allowed_during_manual_login():
    policy = DomainPolicy("https://ion.cesium.com", resolver=lambda _host: {"8.8.8.8"})
    with pytest.raises(SecurityError):
        policy.check_url("https://github.com/login/oauth/authorize")
    with pytest.raises(SecurityError):
        policy.check_url("https://accounts.google.com/o/oauth2/v2/auth")
    with pytest.raises(SecurityError):
        policy.check_url("https://accounts.youtube.com/accounts/CheckConnection")
    with policy.allow_temporary_navigation_hosts(agent_runner._temporary_login_navigation_hosts("https://ion.cesium.com/signin/story")):
        policy.check_url("https://github.com/login/oauth/authorize")
        policy.check_url("https://accounts.google.com/o/oauth2/v2/auth")
        policy.check_url("https://accounts.youtube.com/accounts/CheckConnection")
        with pytest.raises(SecurityError):
            policy.check_url("https://evil.github.com/login")
        with pytest.raises(SecurityError):
            policy.check_url("https://evil.google.com/login")
    with pytest.raises(SecurityError):
        policy.check_url("https://github.com/login/oauth/authorize")
    with pytest.raises(SecurityError):
        policy.check_url("https://accounts.google.com/o/oauth2/v2/auth")
    with pytest.raises(SecurityError):
        policy.check_url("https://accounts.youtube.com/accounts/CheckConnection")


def test_blocked_login_error_page_recovers_to_trusted_login_url():
    policy = DomainPolicy("https://ion.cesium.com", resolver=lambda _host: {"8.8.8.8"})
    policy.remember_rejection("目标主机不在白名单：example.invalid")
    page = Mock(url="chrome-error://chromewebdata/")
    reason = agent_runner._recover_blocked_login_navigation(
        page, policy, "https://ion.cesium.com/signin/story"
    )
    assert reason == "目标主机不在白名单：example.invalid"
    page.goto.assert_called_once_with(
        "https://ion.cesium.com/signin/story", wait_until="domcontentloaded"
    )


def test_login_relay_rejects_stale_confirmation_and_discards_pixels():
    runner = RunOrchestrator(runner_mode="process")
    with pytest.raises(RuntimeError):
        runner.login_control("run", "stale", {"kind": "frame"})
    # Receive must happen outside send's registry lock, like the real supervisor.
    connection = Mock()
    runner._clarifications["run"] = {"id": "id", "question": "【需要手动登录】", "connection": connection}
    from threading import Thread
    def send(message):
        Thread(target=runner._receive_login_control, args=("run", {"request_id": message["request_id"], "payload": {"image": "pixels"}})).start()
    connection.send.side_effect = send
    assert runner.login_control("run", "id", {"kind": "frame"}) == {"image": "pixels"}
    assert runner._login_requests == {}


def test_manual_wait_is_not_killed_by_supervisor_deadline(monkeypatch, tmp_path):
    runner = RunOrchestrator(runner_mode="process")
    config = RunnerConfig(artifacts_root=tmp_path, max_duration_seconds=1)
    clock = [0.0]
    monkeypatch.setattr("gui_agent.execution.orchestrator.monotonic", lambda: clock[0])
    messages = iter([
        {"type": "clarification_requested", "payload": {"id": "c", "round": 1, "question": "【需要手动登录】"}},
        {"type": "clarification_resolved", "payload": {"id": "c", "answer": "ready"}},
        {"type": "result", "payload": {"status": "passed"}},
    ])
    connection = Mock()
    def receive():
        message = next(messages)
        clock[0] = {"clarification_requested": 0.5, "clarification_resolved": 500.0, "result": 500.1}[message["type"]]
        return message
    connection.recv.side_effect = receive
    cancel = Event()
    monkeypatch.setattr(runner, "_write_state", lambda *a: None)
    monkeypatch.setattr(runner, "read", lambda *a: {})
    runner._supervise_isolated("run", config, Mock(pid=1), cancel, connection, Mock(assigned=False), "key")
    assert not cancel.is_set()
