from datetime import datetime, timezone

from gui_agent.domain.models import Locator, Step
from gui_agent.domain.results import Observation, Status, StepResult
from gui_agent.planning.agent_planner import AgentDecision
from gui_agent.planning.decision_gate import audit_agent_decision


def _result(step: Step, *, progress: str = "no_progress", status: Status = Status.ERROR) -> StepResult:
    now = datetime.now(timezone.utc)
    return StepResult(
        index=1,
        action=step.action.value,
        description=step.description,
        target_summary=step.locator.describe() if step.locator else step.target or step.action.value,
        status=status,
        started_at=now,
        ended_at=now,
        progress_assessment=progress,
    )


def test_gate_warns_when_semantic_target_is_missing_from_bounded_observation() -> None:
    step = Step(action="click", locator=Locator(role="button", name="提交订单"))
    result = audit_agent_decision(
        AgentDecision(kind="action", action=step, reason="点击提交"),
        Observation(url="https://example.test", title="订单", dom_summary=["role=button label=取消"]),
        [],
    )

    assert result.status == "warn"
    assert result.evidence["targetObserved"] is False


def test_gate_blocks_repeated_non_progressing_action() -> None:
    step = Step(action="click", locator=Locator(role="button", name="下一步"))
    history = [_result(step), _result(step)]

    result = audit_agent_decision(
        AgentDecision(kind="action", action=step, reason="继续点击"),
        Observation(url="https://example.test", title="表单"),
        history,
    )

    assert result.status == "block"
    assert result.evidence["repeatedWithoutProgress"] is True


def test_gate_blocks_completion_after_failed_or_non_progressing_step() -> None:
    step = Step(action="click", locator=Locator(role="button", name="保存"))
    result = audit_agent_decision(
        AgentDecision(kind="complete", reason="已经完成"),
        Observation(url="https://example.test", title="表单"),
        [_result(step)],
    )

    assert result.status == "block"
    assert "不能直接宣称任务完成" in result.reasons[0]


def test_gate_allows_clarification_without_page_assumption() -> None:
    result = audit_agent_decision(
        AgentDecision(kind="clarification", question="请选择测试范围", reason="范围不明确"),
        None,
        [],
    )

    assert result.status == "pass"


def test_gate_retains_external_evaluation_context_without_using_it_as_action_authority() -> None:
    result = audit_agent_decision(
        AgentDecision(kind="action", action=Step(action="navigate", target="/"), reason="打开页面"),
        Observation(url="https://example.test", title="首页"),
        [],
        external_evaluation={
            "provider": "agentlab",
            "status": "ready",
            "runStatus": "done",
            "upstreamRuntimeStarted": True,
            "actionPolicy": "local_fixture_only",
            "qualityMetrics": {"qualityStatus": "passed"},
        },
    )

    assert result.status == "pass"
    assert result.evidence["externalEvaluation"] == {
        "provider": "agentlab",
        "status": "ready",
        "runStatus": "done",
        "upstreamRuntimeStarted": True,
        "actionPolicy": "local_fixture_only",
        "qualityMetrics": {"qualityStatus": "passed"},
    }
