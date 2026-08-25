from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from gui_agent.demo.server import DemoServer, find_available_port
from gui_agent.domain.models import ActionType, Assertion, AssertionType, BrowserTarget, Locator, Step, TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan
from gui_agent.planning.agent_planner import AgentDecision, AgentDecisionResult, AgentScenario, VisualRequest
from gui_agent.planning.visual_adapter import VisualSuggestion, VisualSuggestionResult
from gui_agent.planning.replay_planner import AdaptiveReplayPlanner


class ScriptedPlanner:
    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions = decisions
        self.cursor = 0
        self.scenario = AgentScenario(name="scripted", goal="initial goal")

    def decide(self, observation, history, call_index: int) -> AgentDecisionResult:
        decision = self.decisions[self.cursor]
        self.cursor += 1
        return AgentDecisionResult(
            decision=decision,
            model="scripted-agent",
            protocol="test",
            elapsed_ms=1,
            input_tokens=10,
            output_tokens=5,
            estimated_cost=0.00001,
        )


class ScriptedVisualAdapter:
    def __init__(self, ratio: float = 0.5) -> None:
        self.ratio = ratio

    def suggest(self, screenshot_path, target, observation, requested_action="click", expected_change="页面变化") -> VisualSuggestionResult:
        assert screenshot_path.is_file()
        return VisualSuggestionResult(
            suggestion=VisualSuggestion(
                target=target,
                action=requested_action,
                x_ratio=self.ratio,
                y_ratio=0.5,
                confidence=0.95,
                rationale="测试目标位于 Canvas 中心",
                expected_change=expected_change,
            ),
            model="scripted-vision",
            protocol="test",
            elapsed_ms=1,
            input_tokens=20,
            output_tokens=10,
            estimated_cost=0.00002,
        )


class CanvasServer:
    def __enter__(self):
        html = b"""<!doctype html><html><body><h1>Canvas Map</h1><canvas id='map' width='400' height='200' style='border:1px solid'></canvas><h2 id='state'>Not selected</h2><h3 id='polygon-state'>No polygon</h3><script>let clicks=0;document.querySelector('#map').addEventListener('click',()=>{clicks+=1;document.querySelector('#state').textContent='Target selected';if(clicks>=4)document.querySelector('#polygon-state').textContent='Polygon complete'});</script></body></html>"""

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _plan(base_url: str) -> ExecutionPlan:
    return ExecutionPlan(
        name="逐步 Agent 登录",
        base_url=base_url,
        steps=[Step(action=ActionType.NAVIGATE, target="/")],
        assertions=[
            Assertion(type=AssertionType.URL_CONTAINS, expected="/customers"),
            Assertion(type=AssertionType.VISIBLE, locator=Locator(role="heading", name="客户管理")),
        ],
    )


@pytest.mark.e2e
def test_agent_exploration_reobserves_and_completes_real_dom_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
    with DemoServer(port=find_available_port()) as server:
        planner = ScriptedPlanner([
            AgentDecision(kind="action", action=Step(action=ActionType.NAVIGATE, target="/"), reason="打开登录页"),
            AgentDecision(kind="action", action=Step(action=ActionType.FILL, locator=Locator(label="用户名"), value_from_secret="ADMIN_USERNAME"), reason="填写测试账号"),
            AgentDecision(kind="action", action=Step(action=ActionType.FILL, locator=Locator(label="密码"), value_from_secret="ADMIN_PASSWORD"), reason="填写密钥引用"),
            AgentDecision(kind="action", action=Step(action=ActionType.CLICK, locator=Locator(role="button", name="登录")), reason="提交登录"),
            AgentDecision(kind="complete", reason="URL 和标题表明已进入客户管理", progress_assessment="progress"),
        ])
        result, run_dir = run_plan(
            _plan(server.url),
            RunnerConfig(
                artifacts_root=tmp_path / "artifacts",
                headless=True,
                agent_planner=planner,
                max_model_calls=8,
                max_steps=8,
                max_duration_seconds=60,
                allow_private_network=True,
                approval_mode="delegate",
            ),
        )

    assert result.status.value == "passed"
    assert result.completion_reason == "agent_goal_completed"
    assert result.model_calls == 5
    assert result.input_tokens == 50 and result.output_tokens == 25
    assert len(result.steps) == 4
    assert all(step.before and step.after for step in result.steps)
    assert all(step.planner_reason for step in result.steps)
    assert (run_dir / "trace.zip").is_file()
    assert "admin123" not in (run_dir / "run.json").read_text(encoding="utf-8")


@pytest.mark.e2e
def test_agent_navigation_checks_destination_after_leaving_about_blank(tmp_path: Path) -> None:
    with DemoServer(port=find_available_port()) as server:
        planner = ScriptedPlanner([
            AgentDecision(
                kind="action",
                action=Step(
                    action=ActionType.NAVIGATE,
                    target="/",
                    browser_target=BrowserTarget(
                        page="current",
                        url_contains="127.0.0.1",
                        wait_timeout_ms=1_000,
                    ),
                ),
                reason="打开目标网站",
            ),
            AgentDecision(kind="complete", reason="目标网站已打开", progress_assessment="progress"),
        ])
        result, _ = run_plan(
            _plan(server.url).model_copy(update={"assertions": []}),
            RunnerConfig(
                artifacts_root=tmp_path / "artifacts",
                headless=True,
                agent_planner=planner,
                max_model_calls=4,
                max_steps=4,
                max_duration_seconds=30,
                allow_private_network=True,
            ),
        )

    assert result.status.value == "passed"
    assert result.completion_reason == "agent_goal_completed"
    assert [step.action for step in result.steps] == [ActionType.NAVIGATE]


@pytest.mark.e2e
def test_agent_exploration_stops_after_three_real_no_progress_actions(tmp_path: Path) -> None:
    with DemoServer(port=find_available_port()) as server:
        planner = ScriptedPlanner([
            AgentDecision(kind="action", action=Step(action=ActionType.NAVIGATE, target="/"), reason="打开页面"),
            AgentDecision(
                kind="action",
                action=Step(
                    action=ActionType.CLICK,
                    locator=Locator(role="button", name="不存在的按钮一"),
                ),
                reason="尝试无效操作一",
            ),
            AgentDecision(
                kind="action",
                action=Step(
                    action=ActionType.CLICK,
                    locator=Locator(role="button", name="不存在的按钮二"),
                ),
                reason="尝试无效操作二",
            ),
            AgentDecision(
                kind="action",
                action=Step(
                    action=ActionType.CLICK,
                    locator=Locator(role="button", name="不存在的按钮三"),
                ),
                reason="尝试无效操作三",
            ),
        ])
        result, _ = run_plan(
            _plan(server.url),
            RunnerConfig(
                artifacts_root=tmp_path / "artifacts",
                headless=True,
                agent_planner=planner,
                max_model_calls=10,
                max_steps=10,
                no_progress_limit=3,
                max_duration_seconds=60,
                allow_private_network=True,
            ),
        )

    assert result.status.value == "incomplete"
    assert result.completion_reason == "consecutive_step_failures_reached"
    assert [step.progress_assessment for step in result.steps[-3:]] == ["no_progress"] * 3
    assert result.model_calls == 4


@pytest.mark.e2e
def test_agent_records_a_locator_failure_and_continues_in_the_same_run(tmp_path: Path) -> None:
    with DemoServer(port=find_available_port()) as server:
        plan = ExecutionPlan(
            name="失败后继续",
            base_url=server.url,
            steps=[Step(action=ActionType.NAVIGATE, target="/")],
            assertions=[],
        )
        planner = ScriptedPlanner([
            AgentDecision(kind="action", action=Step(action=ActionType.NAVIGATE, target="/"), reason="打开页面"),
            AgentDecision(kind="action", action=Step(action=ActionType.CLICK, locator=Locator(role="button", name="不存在的按钮")), reason="尝试失效定位"),
            AgentDecision(kind="action", action=Step(action=ActionType.SCREENSHOT), reason="记录失败后的当前页面"),
            AgentDecision(kind="complete", reason="已记录失败并完成剩余检查", progress_assessment="progress"),
        ])
        result, run_dir = run_plan(
            plan,
            RunnerConfig(
                artifacts_root=tmp_path / "artifacts",
                headless=True,
                agent_planner=planner,
                max_model_calls=8,
                max_steps=8,
                no_progress_limit=3,
                max_duration_seconds=60,
                allow_private_network=True,
            ),
        )

    assert result.status.value == "passed"
    assert result.completion_reason == "agent_goal_completed"
    assert [step.status.value for step in result.steps] == ["passed", "error", "passed"]
    assert result.failed_step_index == 2
    assert result.model_calls == 4
    assert "step_failure_recorded_and_continuing" in (run_dir / "events.jsonl").read_text(encoding="utf-8")


@pytest.mark.e2e
def test_agent_keeps_the_browser_session_for_a_follow_up_goal(tmp_path: Path) -> None:
    with DemoServer(port=find_available_port()) as server:
        planner = ScriptedPlanner([
            AgentDecision(kind="complete", reason="first goal complete", progress_assessment="progress"),
            AgentDecision(kind="complete", reason="follow-up goal complete", progress_assessment="progress"),
        ])
        answers = iter(["继续检查当前页面标题", "结束本次测试"])
        result, run_dir = run_plan(
            _plan(server.url).model_copy(update={"assertions": []}),
            RunnerConfig(
                artifacts_root=tmp_path / "artifacts",
                headless=True,
                agent_planner=planner,
                max_model_calls=2,
                max_steps=2,
                max_duration_seconds=60,
                allow_private_network=True,
                continuous_agent_session=True,
                clarification_callback=lambda _question, _round: next(answers),
            ),
        )

    assert result.status.value == "passed"
    assert result.completion_reason == "agent_session_completed"
    assert result.model_calls == 2
    assert result.scenario_goal == "继续检查当前页面标题"
    assert result.clarification_history == [{
        "kind": "follow_up",
        "round": 0,
        "question": "当前任务已完成，可以继续告诉 AI 下一项要测试什么，或选择结束本次测试。",
        "answer": "继续检查当前页面标题",
        "completed_goal": "逐步 Agent 登录",
    }]
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "agent_goal_completed_waiting_follow_up" in events
    assert "agent_follow_up_received" in events
    assert "agent_session_completed" in events


@pytest.mark.e2e
def test_agent_dangerous_action_is_rejected_before_locator_execution(tmp_path: Path) -> None:
    with DemoServer(port=find_available_port()) as server:
        planner = ScriptedPlanner([
            AgentDecision(kind="action", action=Step(action=ActionType.NAVIGATE, target="/"), reason="打开页面"),
            AgentDecision(kind="action", action=Step(
                action=ActionType.CLICK,
                locator=Locator(role="button", name="删除客户"),
                description="删除客户",
            ), reason="尝试危险动作"),
        ])
        result, _ = run_plan(
            _plan(server.url),
            RunnerConfig(
                artifacts_root=tmp_path / "artifacts",
                headless=True,
                agent_planner=planner,
                max_model_calls=4,
                max_steps=4,
                max_duration_seconds=60,
                allow_private_network=True,
                confirmation_callback=lambda _step, _index, _rule: False,
            ),
        )

    assert result.status.value == "cancelled"
    assert result.completion_reason == "dangerous_action_rejected"
    assert result.steps[-1].status.value == "skipped"


@pytest.mark.e2e
def test_visual_adapter_suggests_and_playwright_executes_real_canvas_click(tmp_path: Path) -> None:
    with CanvasServer() as server:
        plan = ExecutionPlan(
            name="Canvas 视觉选择",
            base_url=server.url,
            steps=[Step(
                action=ActionType.NAVIGATE,
                target="/",
                effect_kind="browse_search_filter_sort",
                effect_level="read_only",
            )],
            assertions=[Assertion(type=AssertionType.VISIBLE, locator=Locator(text="Target selected"))],
        )
        planner = ScriptedPlanner([
            AgentDecision(
                kind="action",
                action=Step(
                    action=ActionType.NAVIGATE,
                    target="/",
                    effect_kind="browse_search_filter_sort",
                    effect_level="read_only",
                ),
                reason="打开 Canvas 页面",
            ),
            AgentDecision(
                kind="visual",
                visual_request=VisualRequest(
                    canvas_locator=Locator(css="#map"),
                    target="Canvas 中心目标",
                    trigger_reason="DOM 无法表达 Canvas 内部目标",
                    effect_kind="browse_search_filter_sort",
                    effect_level="read_only",
                ),
                reason="请求视觉适配器定位 Canvas 目标",
            ),
            AgentDecision(kind="complete", reason="页面显示 Target selected", progress_assessment="progress"),
        ])
        result, run_dir = run_plan(
            plan,
            RunnerConfig(
                artifacts_root=tmp_path / "artifacts",
                headless=True,
                agent_planner=planner,
                visual_adapter=ScriptedVisualAdapter(),
                max_model_calls=8,
                max_steps=8,
                max_duration_seconds=60,
                allow_private_network=True,
                cesium_policy_enabled=True,
            ),
        )

    visual_step = result.steps[1]
    assert result.status.value == "passed"
    assert visual_step.execution_mode == "visual"
    assert visual_step.computer_use_triggered is True
    assert visual_step.coordinate_source == "region-relative:0.5000,0.5000"
    assert visual_step.progress_assessment == "progress"
    assert visual_step.canvas_evidence["mode"] == "visual"
    assert visual_step.canvas_evidence["bridgeAvailable"] is False
    assert visual_step.canvas_evidence["observationProgressVerified"] is True
    assert result.stability_level == "C"
    assert [item.decision for item in result.model_call_records] == ["action", "visual", "visual_suggestion", "complete"]
    assert any('"type": "visual_fallback_suggested"' in line for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines())


@pytest.mark.e2e
def test_playwright_executes_bounded_canvas_polygon_gesture(tmp_path: Path) -> None:
    with CanvasServer() as server:
        plan = ExecutionPlan(
            name="Canvas 多边形测量",
            base_url=server.url,
            steps=[
                Step(action=ActionType.NAVIGATE, target="/"),
                Step(
                    action=ActionType.VISUAL_DRAW_POLYGON,
                    execution_mode="visual",
                    stability_level="C",
                    canvas_region_locator=Locator(css="#map"),
                    visual_target="地图测量边界",
                    visual_points=[
                        {"xRatio": 0.1, "yRatio": 0.1},
                        {"xRatio": 0.9, "yRatio": 0.1},
                        {"xRatio": 0.9, "yRatio": 0.9},
                        {"xRatio": 0.1, "yRatio": 0.9},
                    ],
                    gesture_finish="double_click",
                    visual_expected_change="地图显示闭合测量面和面积结果",
                ),
            ],
            assertions=[Assertion(type=AssertionType.VISIBLE, locator=Locator(text="Polygon complete"))],
        )

        result, _ = run_plan(
            plan,
            RunnerConfig(
                artifacts_root=tmp_path / "artifacts",
                headless=True,
                max_duration_seconds=60,
                allow_private_network=True,
            ),
        )

    polygon_step = result.steps[1]
    assert result.status.value == "passed"
    assert polygon_step.coordinate_source == (
        "canvas-region-relative:polygon:0.1000,0.1000;0.9000,0.1000;"
        "0.9000,0.9000;0.1000,0.9000;finish=double_click"
    )
    assert polygon_step.canvas_evidence["action"] == "visual_draw_polygon"
    assert polygon_step.canvas_evidence["collectionStatus"] == "complete"


@pytest.mark.e2e
def test_agent_executes_structured_canvas_polygon_without_visual_adapter(tmp_path: Path) -> None:
    with CanvasServer() as server:
        plan = ExecutionPlan(
            name="Agent Canvas 多边形",
            base_url=server.url,
            steps=[Step(action=ActionType.NAVIGATE, target="/")],
            assertions=[Assertion(type=AssertionType.VISIBLE, locator=Locator(text="Polygon complete"))],
        )
        planner = ScriptedPlanner([
            AgentDecision(
                kind="action",
                action=Step(action=ActionType.NAVIGATE, target="/"),
                reason="打开 Canvas 页面",
            ),
            AgentDecision(
                kind="action",
                action=Step(
                    action=ActionType.VISUAL_DRAW_POLYGON,
                    execution_mode="visual",
                    stability_level="C",
                    canvas_region_locator=Locator(css="#map"),
                    visual_target="地图五边形边界",
                    visual_points=[
                        {"xRatio": 0.15, "yRatio": 0.20},
                        {"xRatio": 0.75, "yRatio": 0.10},
                        {"xRatio": 0.90, "yRatio": 0.55},
                        {"xRatio": 0.55, "yRatio": 0.90},
                        {"xRatio": 0.15, "yRatio": 0.75},
                    ],
                    gesture_finish="double_click",
                    visual_expected_change="页面显示 Polygon complete",
                ),
                reason="使用唯一 Canvas 区域和五个受约束相对顶点绘制",
            ),
            AgentDecision(kind="complete", reason="页面显示 Polygon complete", progress_assessment="progress"),
        ])
        result, _ = run_plan(
            plan,
            RunnerConfig(
                artifacts_root=tmp_path / "artifacts-agent-polygon",
                headless=True,
                agent_planner=planner,
                max_model_calls=6,
                max_steps=5,
                max_duration_seconds=60,
                allow_private_network=True,
                approval_mode="delegate",
            ),
        )

    assert result.status.value == "passed"
    polygon_step = result.steps[1]
    assert polygon_step.action == "visual_draw_polygon"
    assert polygon_step.computer_use_triggered is False
    assert polygon_step.canvas_evidence["collectionStatus"] == "complete"


@pytest.mark.e2e
def test_adaptive_replay_relocates_visual_target_on_every_run(tmp_path: Path) -> None:
    with CanvasServer() as server:
        plan = ExecutionPlan(
            name="Canvas 自适应回放",
            base_url=server.url,
            steps=[
                Step(action=ActionType.NAVIGATE, target="/"),
                Step(
                    action=ActionType.VISUAL_CLICK,
                    locator=Locator(css="#map"),
                    execution_mode="visual",
                    stability_level="C",
                    visual_target="Canvas 目标",
                    relative_position={"xRatio": 0.1, "yRatio": 0.5},
                ),
            ],
            assertions=[Assertion(type=AssertionType.VISIBLE, locator=Locator(text="Target selected"))],
        )
        sources = []
        for index, ratio in enumerate((0.25, 0.75), start=1):
            result, _ = run_plan(
                plan,
                RunnerConfig(
                    artifacts_root=tmp_path / f"artifacts-{index}",
                    headless=True,
                    replay_mode="adaptive",
                    agent_planner=AdaptiveReplayPlanner(plan),
                    visual_adapter=ScriptedVisualAdapter(ratio),
                    max_model_calls=8,
                    max_steps=4,
                    max_duration_seconds=60,
                    allow_private_network=True,
                ),
            )
            assert result.status.value == "passed"
            sources.append(result.steps[1].coordinate_source)

    assert sources == ["region-relative:0.2500,0.5000", "region-relative:0.7500,0.5000"]
