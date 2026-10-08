import json

from gui_agent.execution.agent_runner import (
    _ModelCallBudget,
    _ModelRecoveryState,
    _build_model_recovery_checkpoint,
    _model_failure_disposition,
)
from gui_agent.execution.runner import RunnerConfig
from gui_agent.planning.ai_provider import (
    AIProviderConfigurationError,
    AIProviderError,
    AIProviderLocalContractError,
    AIProviderOutputError,
    AIProviderUnavailableError,
)
from gui_agent.planning.agent_planner import AIAgentPlanner
from gui_agent.planning.replay_planner import AdaptiveReplayPlanner


def test_runner_has_bounded_model_recovery_defaults() -> None:
    config = RunnerConfig()

    assert config.model_recovery_attempts == 3
    assert config.model_recovery_backoff_seconds == (2.0, 5.0, 10.0)


def test_model_recovery_checkpoint_is_value_safe_and_forbids_replay() -> None:
    checkpoint = _build_model_recovery_checkpoint(
        run_id="run-1",
        executed_step_count=6,
        recovery_attempt=1,
        recovery_limit=3,
        current_url="http://example.test/#/wizard",
        page_state_key="wizard|#/wizard|signature",
        screenshot="screenshots/model-recovery-1.png",
        total_recovery_attempt=4,
    )
    serialized = json.dumps(checkpoint, ensure_ascii=False)

    assert checkpoint["automaticActionReplayAllowed"] is False
    assert checkpoint["resumePolicy"] == "recapture_current_page_then_request_fresh_decision"
    assert checkpoint["executedStepCount"] == 6
    assert checkpoint["totalModelRecoveryAttempt"] == 4
    assert "cookie" not in serialized.lower()
    assert "api_key" not in serialized.lower()
    assert "password" not in serialized.lower()
    assert "inputValue" not in serialized


def test_model_failure_dispositions_are_explicit() -> None:
    assert _model_failure_disposition(
        AIProviderUnavailableError("temporary")
    ) == ("model_service_unavailable", True)
    assert _model_failure_disposition(
        AIProviderOutputError("invalid output")
    ) == ("model_output_recovery_exhausted", True)
    assert _model_failure_disposition(
        AIProviderConfigurationError("invalid model")
    ) == ("model_configuration_error", False)
    assert _model_failure_disposition(
        AIProviderLocalContractError("missing screenshot")
    ) == ("model_local_contract_error", False)
    assert _model_failure_disposition(
        AIProviderError("unknown")
    ) == ("model_error", False)


def test_only_the_primary_agent_planner_consumes_decision_model_budget() -> None:
    assert AIAgentPlanner.uses_external_model is True
    assert AdaptiveReplayPlanner.uses_external_model is False


def test_failed_model_calls_still_consume_the_budget() -> None:
    budget = _ModelCallBudget(maximum=2)

    assert budget.begin() == 1
    # No successful ModelCallRecord is needed for an attempted call to count.
    assert budget.attempts == 1
    assert budget.exhausted is False
    assert budget.begin() == 2
    assert budget.exhausted is True


def test_zero_model_call_limit_is_unlimited_but_tracks_attempts() -> None:
    budget = _ModelCallBudget(maximum=0)

    for _ in range(3):
        budget.begin()

    assert budget.attempts == 3
    assert budget.exhausted is False


def test_successful_decision_resets_only_consecutive_recovery_count() -> None:
    recovery = _ModelRecoveryState()

    assert recovery.begin() == (1, 1)
    assert recovery.begin() == (2, 2)
    recovery.decision_succeeded()

    assert recovery.consecutive == 0
    assert recovery.total == 2
    assert recovery.begin() == (1, 3)
