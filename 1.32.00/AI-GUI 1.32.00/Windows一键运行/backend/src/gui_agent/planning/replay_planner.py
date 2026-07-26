"""Deterministic plan traversal with runtime visual re-localization."""

from __future__ import annotations

from ..domain.models import ActionType, ExecutionMode, Step, TestPlan
from .agent_planner import AgentDecision, AgentDecisionResult, VisualRequest


class AdaptiveReplayPlanner:
    """Replays approved semantics while delegating fresh coordinates to vision."""

    def __init__(self, plan: TestPlan) -> None:
        self.plan = plan
        self.cursor = 0

    def decide(self, observation, history, call_index: int) -> AgentDecisionResult:
        if self.cursor >= len(self.plan.steps):
            decision = AgentDecision(kind="complete", reason="已执行全部审核步骤，进入收尾断言", progress_assessment="progress")
        else:
            step = self.plan.steps[self.cursor]
            self.cursor += 1
            if step.execution_mode == ExecutionMode.VISUAL:
                action = {
                    ActionType.VISUAL_CLICK: "click",
                    ActionType.VISUAL_HOVER: "hover",
                    ActionType.VISUAL_SCROLL: "scroll",
                    ActionType.VISUAL_DRAG: "drag",
                }.get(step.action)
                if action is None:
                    raise ValueError(f"不支持的自适应视觉动作：{step.action.value}")
                decision = AgentDecision(
                    kind="visual",
                    visual_request=VisualRequest(
                        canvas_locator=step.locator,
                        target=step.visual_target or step.description or step.action.value,
                        trigger_reason="显式 adaptive 回放要求按当前截图重新定位",
                        preferred_action=action,
                        expected_change=step.visual_expected_change or "页面或目标的可见状态发生变化",
                    ),
                    reason="按审核计划重新定位视觉语义目标",
                )
            else:
                decision = AgentDecision(kind="action", action=step, reason="按审核后的固定计划执行非视觉步骤")
        return AgentDecisionResult(
            decision=decision,
            model="adaptive-replay-controller",
            protocol="local",
            elapsed_ms=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0.0,
        )
