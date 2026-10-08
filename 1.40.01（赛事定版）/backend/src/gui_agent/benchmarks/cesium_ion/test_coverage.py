import json

from gui_agent.benchmarks.cesium_ion.coverage import cesium_coverage_payload


def _run(run_id: str, *, scenario_id: str | None = "C25", strict: bool = True) -> dict:
    return {
        "run_id": run_id,
        "base_url_summary": "https://ion.cesium.com",
        "status": "passed",
        "goal_status": "achieved",
        "scenario_id": scenario_id,
        "stable_replay": {"passed": True},
        "completion_gate": {
            "strict_3d_required": strict,
            "strict_3d_passed": strict,
            "stable_replay_passed": True,
        },
        "evidence_manifest": {"completeness": 1.0},
    }


def test_coverage_does_not_mark_release_ready_before_five_replays(tmp_path) -> None:
    for index in range(4):
        run_dir = tmp_path / f"run-{index}"
        run_dir.mkdir()
        (run_dir / "run.json").write_text(json.dumps(_run(f"run-{index}")), encoding="utf-8")

    payload = cesium_coverage_payload(tmp_path)

    assert payload["runs"]["cesiumRunCount"] == 4
    assert payload["cases"][24]["id"] == "C25"
    assert payload["cases"][24]["status"] == "completed_below_repetition_gate"
    assert payload["gates"]["stableReplay"]["passed"] is False
    assert payload["gates"]["strictWebgl"]["passed"] is False
    assert payload["releaseReady"] is False


def test_unassigned_run_is_not_credited_to_any_case(tmp_path) -> None:
    run_dir = tmp_path / "unassigned"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps(_run("unassigned", scenario_id=None)), encoding="utf-8")

    payload = cesium_coverage_payload(tmp_path)

    assert payload["runs"]["unassignedRunCount"] == 1
    assert payload["cases"][24]["executedRuns"] == 0
    assert payload["releaseReady"] is False
