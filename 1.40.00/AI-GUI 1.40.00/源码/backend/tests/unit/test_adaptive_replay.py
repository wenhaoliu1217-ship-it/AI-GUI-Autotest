from gui_agent.domain.models import ActionType, ExecutionMode, RelativePosition, StabilityLevel, Step, TestPlan as ExecutionPlan
from gui_agent.planning.replay_planner import AdaptiveReplayPlanner


def _visual_plan() -> ExecutionPlan:
    return ExecutionPlan(
        name="自适应视觉回放",
        base_url="https://example.com",
        steps=[
            Step(action=ActionType.NAVIGATE, target="/"),
            Step(
                action=ActionType.VISUAL_CLICK,
                execution_mode=ExecutionMode.VISUAL,
                stability_level=StabilityLevel.C,
                visual_target="当前页面的运行按钮",
                relative_position=RelativePosition(xRatio=0.1, yRatio=0.2),
                visual_expected_change="出现运行结果",
            ),
        ],
    )


def test_adaptive_replay_discards_recorded_coordinates_and_requests_fresh_location() -> None:
    planner = AdaptiveReplayPlanner(_visual_plan())
    first = planner.decide(None, [], 1).decision
    second = planner.decide(None, [], 2).decision
    done = planner.decide(None, [], 3).decision

    assert first.kind == "action" and first.action.action == ActionType.NAVIGATE
    assert second.kind == "visual"
    assert second.visual_request.target == "当前页面的运行按钮"
    assert second.visual_request.expected_change == "出现运行结果"
    assert second.visual_request.model_dump().get("relative_position") is None
    assert done.kind == "complete"
