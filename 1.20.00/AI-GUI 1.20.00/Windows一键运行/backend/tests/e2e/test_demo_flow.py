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
            RunnerConfig(tmp_path / "artifacts", headless=True),
        )
    assert result.exit_code == 0
    assert (run_dir / "run.json").exists()
    assert (run_dir / "events.jsonl").exists()
    assert (run_dir / "report.html").exists()
    assert (run_dir / "trace.zip").exists()
    assert all(step.screenshot for step in result.steps)
    assert all((run_dir / step.screenshot).exists() for step in result.steps if step.screenshot)
    with zipfile.ZipFile(run_dir / "trace.zip") as trace:
        assert all(b"admin123" not in trace.read(name) for name in trace.namelist())
