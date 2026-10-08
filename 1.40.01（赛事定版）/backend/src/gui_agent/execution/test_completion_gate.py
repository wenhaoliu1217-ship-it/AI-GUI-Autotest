from datetime import datetime, timezone

import pytest

from gui_agent.artifacts.manager import ArtifactManager
from gui_agent.domain.results import AssertionResult, CauseHint, FailureCategory, RunResult, Status
from gui_agent.execution.completion_gate import evaluate_completion_gate
from gui_agent.execution.runner import _apply_completion_gate
from gui_agent.security.redaction import Redactor


def _assertion(status: Status) -> AssertionResult:
    return AssertionResult(index=1, type="business", description="terminal", detail="terminal", status=status)


def _kwargs(**overrides):
    value = {
        "status": Status.PASSED,
        "completion_reason": "plan_completed",
        "assertions": [_assertion(Status.PASSED)],
        "required_stage_count": 1,
        "completed_stage_count": 1,
        "evidence_manifest": {"completeness": 1.0},
        "cleanup_report": None,
        "commerce_summary": None,
        "cleanup_required": False,
        "replay_mode": "stable",
    }
    value.update(overrides)
    return value


def test_completion_gate_requires_every_condition() -> None:
    result = evaluate_completion_gate(**_kwargs())
    assert result.goal_status == "achieved"
    assert result.run_status == "passed"
    assert result.completion_proof_mode == "stable_replay"


def test_empty_terminal_assertions_cannot_achieve() -> None:
    result = evaluate_completion_gate(**_kwargs(assertions=[]))
    assert result.goal_status == "incomplete"
    assert "terminal_assertions_missing" in result.reasons


def test_unknown_terminal_assertion_is_incomplete() -> None:
    result = evaluate_completion_gate(**_kwargs(assertions=[_assertion(Status.ERROR)]))
    assert result.goal_status == "incomplete"


def test_cleanup_failure_blocks_completion() -> None:
    result = evaluate_completion_gate(
        **_kwargs(
            cleanup_required=True,
            cleanup_report={"status": "failed", "objects": []},
        )
    )
    assert result.goal_status == "incomplete"
    assert "zero_residual_unproven" in result.reasons


def test_unstable_replay_blocks_completion() -> None:
    result = evaluate_completion_gate(**_kwargs(replay_mode="exploration"))
    assert result.goal_status == "incomplete"
    assert "stable_replay_required" in result.reasons


def test_embedded_stable_replay_proof_closes_exploration_gate() -> None:
    result = evaluate_completion_gate(
        **_kwargs(replay_mode="exploration", stable_replay_result=True)
    )

    assert result.goal_status == "achieved"
    assert result.stable_replay_passed is True


def test_terminal_state_proof_can_replace_unsafe_replay() -> None:
    result = evaluate_completion_gate(
        **_kwargs(
            replay_mode="exploration",
            stable_replay_required=False,
        )
    )

    assert result.goal_status == "achieved"
    assert result.stable_replay_required is False
    assert result.stable_replay_passed is False
    assert result.completion_proof_mode == "terminal_state"
    assert "stable_replay_required" not in result.reasons


def test_strict_3d_evidence_is_required_when_enabled() -> None:
    result = evaluate_completion_gate(
        **_kwargs(strict_3d_required=True, strict_3d_passed=False)
    )

    assert result.goal_status == "incomplete"
    assert "strict_3d_evidence_required" in result.reasons


def test_model_service_unavailable_remains_incomplete_with_specific_summary(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    result = RunResult(
        run_id="model-unavailable",
        plan_name="modeling",
        base_url_summary="http://192.168.31.218:7991",
        status=Status.INCOMPLETE,
        started_at=now,
        ended_at=now,
        completion_reason="model_service_unavailable",
        result_classification="model_service_unavailable",
        cause_hints=[CauseHint(
            category=FailureCategory.MODEL_SERVICE,
            message="模型网关暂时不可用",
            evidence=["HTTP 502, retried 5 times"],
            confidence="medium",
        )],
    )
    artifacts = ArtifactManager(tmp_path, result.run_id, Redactor())

    final = _apply_completion_gate(
        result,
        artifacts,
        required_stage_count=1,
        completed_stage_count=0,
        cleanup_required=False,
    )

    assert final.status == Status.INCOMPLETE
    assert final.result_classification == "model_service_unavailable"
    assert "模型服务暂不可用" in final.goal_summary
    assert "不代表目标网站存在业务缺陷" in final.goal_summary


def test_scoped_goal_remains_passed_when_model_is_throttled_after_completion(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    result = RunResult(
        run_id="scoped-model-warning",
        plan_name="form-run",
        base_url_summary="http://192.168.31.218:7991",
        status=Status.INCOMPLETE,
        started_at=now,
        ended_at=now,
        completion_reason="model_service_unavailable",
        result_classification="model_service_unavailable",
        scenario_goal="仅当前页面暂存；不点击保存；填写三个字段",
    )
    artifacts = ArtifactManager(tmp_path, result.run_id, Redactor())

    final = _apply_completion_gate(
        result,
        artifacts,
        required_stage_count=10,
        completed_stage_count=3,
        cleanup_required=False,
    )

    assert final.status == Status.PASSED
    assert final.completion_reason == "scoped_goal_achieved_model_warning"
    assert final.result_classification == "scoped_goal_achieved_with_model_warning"
    assert "局部测试目标已完成" in final.goal_summary
    assert "网关问题不影响本次局部目标结果" in final.goal_summary


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("model_configuration_error", "模型配置无效"),
        ("model_output_recovery_exhausted", "模型连续返回无法通过本地契约校验的输出"),
        ("model_local_contract_error", "本地 Agent 决策契约未满足"),
    ],
)
def test_other_model_failures_are_not_reported_as_website_bugs(
    tmp_path, reason: str, expected: str
) -> None:
    now = datetime.now(timezone.utc)
    result = RunResult(
        run_id=reason,
        plan_name="modeling",
        base_url_summary="http://192.168.31.218:7991",
        status=Status.INCOMPLETE,
        started_at=now,
        ended_at=now,
        completion_reason=reason,
        result_classification="agent_failed",
    )
    artifacts = ArtifactManager(tmp_path, result.run_id, Redactor())

    final = _apply_completion_gate(
        result,
        artifacts,
        required_stage_count=1,
        completed_stage_count=0,
        cleanup_required=False,
    )

    assert final.result_classification == reason
    assert expected in final.goal_summary
    assert "不代表目标网站存在业务缺陷" in final.goal_summary


def test_target_rendering_failure_is_reported_as_website_bug(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    result = RunResult(
        run_id="target-rendering-failure",
        plan_name="modeling",
        base_url_summary="http://192.168.31.218:7991",
        status=Status.ISSUES_FOUND,
        started_at=now,
        ended_at=now,
        completion_reason="target_application_runtime_error",
        result_classification="agent_failed",
    )
    artifacts = ArtifactManager(tmp_path, result.run_id, Redactor())

    final = _apply_completion_gate(
        result,
        artifacts,
        required_stage_count=1,
        completed_stage_count=0,
        cleanup_required=False,
    )

    assert final.status == Status.ISSUES_FOUND
    assert final.result_classification == "target_application_runtime_error"
    assert "目标网站显示致命客户端渲染错误" in final.goal_summary
    assert "阻止后续写入" in final.goal_summary


def test_target_service_not_implemented_has_terminal_classification(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    result = RunResult(
        run_id="target-service-not-implemented",
        plan_name="situation-run",
        base_url_summary="http://192.168.31.218:7991",
        status=Status.INCOMPLETE,
        started_at=now,
        ended_at=now,
        completion_reason="target_service_not_implemented",
        result_classification="agent_failed",
    )
    artifacts = ArtifactManager(tmp_path, result.run_id, Redactor())

    final = _apply_completion_gate(
        result,
        artifacts,
        required_stage_count=6,
        completed_stage_count=5,
        cleanup_required=False,
    )

    assert final.status == Status.INCOMPLETE
    assert final.result_classification == "target_service_not_implemented"
    assert "HTTP 501 Not Implemented" in final.goal_summary
    assert "禁止自动重放" in final.goal_summary
