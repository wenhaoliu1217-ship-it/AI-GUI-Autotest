from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from gui_agent.demo.server import DemoServer, find_available_port
from gui_agent.domain.models import ActionType, Assertion, AssertionType, Locator, Step, TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan
from gui_agent.planning.agent_planner import AgentDecision, AgentDecisionResult, VisualRequest
from gui_agent.planning.visual_adapter import VisualSuggestion, VisualSuggestionResult


class ScriptedPlanner:
    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions = decisions
        self.cursor = 0

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
    def suggest(self, screenshot_path, target, observation) -> VisualSuggestionResult:
        assert screenshot_path.is_file()
        return VisualSuggestionResult(
            suggestion=VisualSuggestion(
                target=target,
                x_ratio=0.5,
                y_ratio=0.5,
                confidence=0.95,
                rationale="测试目标位于 Canvas 中心",
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
        html = b"""<!doctype html><html><body><h1>Canvas Map</h1><canvas id='map' width='400' height='200' style='border:1px solid'></canvas><h2 id='state'>Not selected</h2><script>document.querySelector('#map').addEventListener('click',()=>{document.querySelector('#state').textContent='Target selected'});</script></body></html>"""

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
def test_agent_exploration_stops_after_three_real_no_progress_actions(tmp_path: Path) -> None:
    with DemoServer(port=find_available_port()) as server:
        planner = ScriptedPlanner([
            AgentDecision(kind="action", action=Step(action=ActionType.NAVIGATE, target="/"), reason="打开页面"),
            AgentDecision(kind="action", action=Step(action=ActionType.SCREENSHOT), reason="观察一"),
            AgentDecision(kind="action", action=Step(action=ActionType.SCREENSHOT), reason="观察二"),
            AgentDecision(kind="action", action=Step(action=ActionType.SCREENSHOT), reason="观察三"),
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
            ),
        )

    assert result.status.value == "incomplete"
    assert result.completion_reason == "no_progress_limit_reached"
    assert [step.progress_assessment for step in result.steps[-3:]] == ["no_progress"] * 3
    assert result.model_calls == 4


@pytest.mark.e2e
def test_visual_adapter_suggests_and_playwright_executes_real_canvas_click(tmp_path: Path) -> None:
    with CanvasServer() as server:
        plan = ExecutionPlan(
            name="Canvas 视觉选择",
            base_url=server.url,
            steps=[Step(action=ActionType.NAVIGATE, target="/")],
            assertions=[Assertion(type=AssertionType.VISIBLE, locator=Locator(text="Target selected"))],
        )
        planner = ScriptedPlanner([
            AgentDecision(kind="action", action=Step(action=ActionType.NAVIGATE, target="/"), reason="打开 Canvas 页面"),
            AgentDecision(
                kind="visual",
                visual_request=VisualRequest(
                    canvas_locator=Locator(css="#map"),
                    target="Canvas 中心目标",
                    trigger_reason="DOM 无法表达 Canvas 内部目标",
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
            ),
        )

    visual_step = result.steps[1]
    assert result.status.value == "passed"
    assert visual_step.execution_mode == "visual"
    assert visual_step.computer_use_triggered is True
    assert visual_step.coordinate_source == "canvas-relative:0.5000,0.5000"
    assert visual_step.progress_assessment == "progress"
    assert result.stability_level == "C"
    assert [item.decision for item in result.model_call_records] == ["action", "visual", "visual_suggestion", "complete"]
    assert any('"type": "visual_fallback_suggested"' in line for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines())
