import pytest

from gui_agent.planning.demo_planner import plan_from_text, save_plan
from gui_agent.planning.loader import load_plan, summarize_plan


def test_demo_planner_round_trip(tmp_path) -> None:
    plan = plan_from_text("管理员登录后新建客户并分配给员工")
    path = save_plan(plan, tmp_path / "plan.yaml")
    loaded = load_plan(path, check_secrets=False)
    assert loaded == plan
    summary = summarize_plan(loaded)
    assert "<secret:ADMIN_PASSWORD>" in summary
    assert "admin123" not in summary


def test_demo_planner_rejects_unknown_intent() -> None:
    with pytest.raises(ValueError, match="v0.1"):
        plan_from_text("查询库存")
