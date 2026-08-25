from __future__ import annotations

import json

from gui_agent.artifacts import ArtifactManager
from gui_agent.domain.results import Observation
from gui_agent.execution.opensource_observation import capture_open_source_observation, record_open_source_runtime_evaluation
from gui_agent.execution.runner import RunnerConfig, _record_open_source_observation
from gui_agent.security.redaction import Redactor


def test_runner_records_read_only_open_source_observation_as_separate_evidence(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "gui_agent.execution.runner.capture_open_source_observation",
        lambda _provider, _url, _hosts: {
            "provider": "playwright-mcp",
            "status": "ready",
            "observation": {"url": "https://example.test/", "title": "Fixture"},
            "runtimeEvidence": {"toolCount": 23, "unsafeTools": ["browser_evaluate"]},
            "toolResult": {"ok": True},
        },
    )
    artifacts = ArtifactManager(tmp_path, "run-1", Redactor())

    _record_open_source_observation(
        artifacts,
        RunnerConfig(open_source_observation_provider="playwright-mcp", allowed_hosts=("example.test",)),
        "https://example.test/",
    )

    evidence = json.loads((tmp_path / "run-1" / "observations" / "open-source-observation.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (tmp_path / "run-1" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    captured = next(item for item in events if item["type"] == "opensource_observation_captured")
    assert evidence["runtimeEvidence"]["toolCount"] == 23
    assert captured["action_policy"] == "read_only_navigate_snapshot_only"
    assert captured["evidence_path"] == "observations/open-source-observation.json"


def test_runner_records_provider_failure_without_failing_the_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "gui_agent.execution.runner.capture_open_source_observation",
        lambda _provider, _url, _hosts: {
            "provider": "playwright-mcp",
            "status": "unavailable",
            "errorClass": "PlaywrightMcpProbeError",
            "error": "sidecar unavailable",
        },
    )
    artifacts = ArtifactManager(tmp_path, "run-2", Redactor())

    _record_open_source_observation(
        artifacts,
        RunnerConfig(open_source_observation_provider="playwright-mcp"),
        "https://example.test/",
    )

    events = [json.loads(line) for line in (tmp_path / "run-2" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    unavailable = next(item for item in events if item["type"] == "opensource_observation_unavailable")
    assert unavailable["status"] == "unavailable"
    assert unavailable["action_policy"] == "read_only_navigate_snapshot_only"


def test_runner_records_isolated_upstream_evaluation_without_changing_action_loop(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "gui_agent.execution.opensource_observation.run_open_source_runtime",
        lambda provider: {
            "adapter": provider,
            "status": "episode_ready",
            "summary": {"success": True, "totalReward": 1.0},
            "metrics": {"success_rate": 1.0},
            "qualityMetrics": {"qualityStatus": "passed", "metricCount": 1},
            "runtimeEvidence": {
                "source": "upstream_child_process",
                "upstreamRuntimeStarted": True,
                "executionBoundary": "isolated_child_process",
                "module": "browsergym.core",
                "version": "0.14.3",
                "actionPolicy": "local_fixture_only",
            },
        },
    )
    artifacts = ArtifactManager(tmp_path, "run-3", Redactor())

    evidence = record_open_source_runtime_evaluation(
        artifacts,
        RunnerConfig(open_source_evaluation_provider="browsergym"),
    )

    saved = json.loads((tmp_path / "run-3" / "evaluations" / "open-source-runtime.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (tmp_path / "run-3" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    captured = next(item for item in events if item["type"] == "opensource_runtime_evaluation_captured")
    assert evidence["upstreamRuntimeStarted"] is True
    assert evidence["actionPolicy"] == "local_fixture_only"
    assert evidence["qualityMetrics"]["qualityStatus"] == "passed"
    assert evidence["metrics"]["success_rate"] == 1.0
    assert saved["summary"]["success"] is True
    assert captured["evidence_path"] == "evaluations/open-source-runtime.json"


def test_runner_keeps_upstream_evaluation_disabled_by_default(tmp_path) -> None:
    artifacts = ArtifactManager(tmp_path, "run-4", Redactor())

    assert record_open_source_runtime_evaluation(artifacts, RunnerConfig()) is None
    assert not (tmp_path / "run-4" / "evaluations").exists()


def test_runner_can_capture_stagehand_and_openadapt_compatibility_evidence_from_native_observation() -> None:
    observation = Observation(
        url="https://example.test/dashboard",
        title="Dashboard",
        dom_summary=["button | role=button | text=Open details", "div | text=Summary"],
        accessibility_summary='- button "Open details"',
    )

    stagehand = capture_open_source_observation(
        "stagehand",
        observation.url,
        ("example.test",),
        native_observation=observation,
        step_index=1,
    )
    assert stagehand["status"] == "ready"
    assert stagehand["runtimeEvidence"]["upstreamRuntimeStarted"] is False
    assert stagehand["candidateResult"]["evidence"]["actionPolicy"] == "candidate_only_no_execution"
    assert len(stagehand["candidateResult"]["candidates"]) == 1

    openadapt = capture_open_source_observation(
        "openadapt",
        observation.url,
        ("example.test",),
        native_observation=observation,
        step_index=1,
    )
    assert openadapt["status"] == "ready"
    assert openadapt["runtimeEvidence"]["upstreamRuntimeStarted"] is False
    assert openadapt["checkpointResult"]["checkpoint"]["id"] == "native-observation-1"
    assert openadapt["checkpointResult"]["actionPolicy"] == "checkpoint_only_no_execution"
