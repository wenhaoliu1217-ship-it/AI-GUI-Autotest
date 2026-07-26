from pathlib import Path
import zipfile

import pytest

from gui_agent.demo.server import DemoServer, find_available_port
from gui_agent.execution import RunnerConfig, run_plan
from gui_agent.planning.demo_planner import plan_from_text


@pytest.mark.e2e
def test_demo_flow_end_to_end(tmp_path: Path, monkeypatch) -> None:
    with DemoServer(port=find_available_port()) as server:
        monkeypatch.setenv("TEST_BASE_URL", server.url)
        monkeypatch.setenv("ADMIN_USERNAME", "admin")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
        result, run_dir = run_plan(
            plan_from_text("管理员登录后新建客户并分配给员工"),
            RunnerConfig(tmp_path / "artifacts", headless=True, allow_private_network=True),
        )
    assert result.exit_code == 0
    assert (run_dir / "run.json").exists()
    assert (run_dir / "events.jsonl").exists()
    assert (run_dir / "report.html").exists()
    assert (run_dir / "trace.zip").exists()
    assert (run_dir / "plan.json").exists()
    assert (run_dir / "generated-test.spec.ts").exists()
    assert result.generated_test and result.generated_test.ci_eligible
    assert result.scenario_goal == result.plan_name
    assert result.goal_status == "achieved"
    assert "断言通过 3/3" in result.goal_summary
    assert all(step.screenshot for step in result.steps)
    assert all(step.before and step.after for step in result.steps)
    assert all(step.before.screenshot for step in result.steps if step.before)
    assert all(step.after.url for step in result.steps if step.after)
    assert any(step.after.dom_summary for step in result.steps if step.after)
    assert all((run_dir / step.screenshot).exists() for step in result.steps if step.screenshot)
    with zipfile.ZipFile(run_dir / "trace.zip") as trace:
        assert all(b"admin123" not in trace.read(name) for name in trace.namelist())
    assert "admin123" not in (run_dir / "generated-test.spec.ts").read_text(encoding="utf-8")
