from gui_agent.acceptance.benchmark import _summarize, load_scenarios
from gui_agent.acceptance.l4 import L4Orchestrator
from gui_agent.acceptance.models import AcceptanceAttempt
from gui_agent.api.server import _run_payload
from gui_agent.api.server import GAE_BENCHMARK_ROOT


def _workflow():
    return {
        "stages": [
            {"id": "MODEL", "dependsOn": [], "requiredOutputs": ["modelStatus"], "terminalAssertions": [{"field": "modelStatus", "equals": "ready"}]},
            {"id": "CLOSE", "dependsOn": ["MODEL"], "requiredOutputs": ["zeroResidual"], "terminalAssertions": [{"field": "zeroResidual", "equals": True}]},
        ]
    }


def test_l4_close_false_is_not_success(tmp_path):
    result = L4Orchestrator().run(
        _workflow(), tmp_path,
        stage_executors={
            "MODEL": lambda _ctx: {"status": "passed", "outputs": {"modelStatus": "ready"}},
            "CLOSE": lambda _ctx: {"status": "passed", "outputs": {"zeroResidual": False}},
        },
    )
    assert result["goalStatus"] == "incomplete"
    assert result["failedStage"] == "CLOSE"
    assert result["zeroResidual"] is False


def test_l4_close_true_requires_cleanup_and_completes(tmp_path):
    result = L4Orchestrator().run(
        _workflow(), tmp_path,
        stage_executors={
            "MODEL": lambda _ctx: {"status": "passed", "outputs": {"modelStatus": "ready"}},
            "CLOSE": lambda _ctx: {"status": "passed", "outputs": {"zeroResidual": True}},
        },
    )
    assert result["goalStatus"] == "achieved"
    assert result["cleanupSuccess"] is True


def test_run_payload_never_infers_achieved_from_passed():
    payload = _run_payload({"run_id": "forged", "status": "passed", "plan_name": "forged"})
    assert payload["goal_status"] == "incomplete"


def test_run_payload_rejects_explicit_achieved_without_gate():
    payload = _run_payload({
        "run_id": "forged", "status": "passed", "goal_status": "achieved",
        "plan_name": "forged",
    })
    assert payload["goal_status"] == "incomplete"
    assert "CompletionGateResult" in payload["goal_summary"]


def test_catalog_is_exactly_s01_through_s30():
    scenarios = load_scenarios(GAE_BENCHMARK_ROOT / "scenarios")
    assert [item.id for item in scenarios] == [f"S{index:02d}" for index in range(1, 31)]


def test_acceptance_completion_denominator_is_fixed_at_150():
    attempt = AcceptanceAttempt(
        scenarioId="S01", repeat=1, status="passed", goalStatus="achieved",
        runId="run-1", completionReason="complete", stableCandidate=True,
        stableSuccess=True, evidencePresent=1, evidenceRequired=1,
        cleanupCleared=1, cleanupRequired=1,
    )
    summary = _summarize(
        "batch-test", [attempt],
        {"goalStatus": "achieved", "cleanupSuccess": True, "zeroResidual": True},
    )
    assert summary["metrics"]["plannedRuns"] == 150
    assert summary["metrics"]["scenarioCompletionRate"] == round(1 / 150, 4)
    assert summary["metrics"]["l4Success"] is True
