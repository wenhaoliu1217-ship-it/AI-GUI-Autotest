from datetime import datetime

from gui_agent.artifacts.evidence_package import build_evidence_package
from gui_agent.artifacts.manager import ArtifactManager
from gui_agent.artifacts.report import render_html, render_markdown
from gui_agent.domain.results import RunResult, Status
from gui_agent.security.redaction import Redactor


def _result(**updates) -> RunResult:
    now = datetime.now().astimezone()
    payload = {
        "run_id": "agent-evidence-run",
        "plan_name": "Agent evidence closure",
        "base_url_summary": "https://example.test",
        "status": Status.INCOMPLETE,
        "started_at": now,
        "ended_at": now,
        "multimodal_required": True,
        "agent_evaluation": {"taskSuccess": False, "evidenceCompleteness": 0.0},
        "agent_exploration": {"scope": "current_run_only", "visitedStateCount": 1},
    }
    payload.update(updates)
    return RunResult(**payload)


def test_agent_evaluation_and_exploration_are_standard_evidence(tmp_path) -> None:
    artifacts = ArtifactManager(tmp_path, "agent-evidence-run", Redactor())
    artifacts.write_json("plan.json", {"name": "Agent evidence closure"})
    artifacts.trace_path.write_bytes(b"trace")

    manifest, _ = build_evidence_package(artifacts, _result())
    items = {item["id"]: item for item in manifest["items"]}

    assert items["agent_evaluation"]["status"] == "present"
    assert items["agent_exploration"]["status"] == "present"
    assert (artifacts.run_dir / "evidence" / "agent-evaluation.json").is_file()
    assert (artifacts.run_dir / "evidence" / "agent-exploration.json").is_file()


def test_missing_agent_closure_evidence_reduces_completeness(tmp_path) -> None:
    artifacts = ArtifactManager(tmp_path, "agent-evidence-run", Redactor())
    artifacts.write_json("plan.json", {"name": "Agent evidence closure"})
    artifacts.trace_path.write_bytes(b"trace")

    manifest, _ = build_evidence_package(
        artifacts,
        _result(agent_evaluation=None, agent_exploration=None),
    )
    items = {item["id"]: item for item in manifest["items"]}

    assert items["agent_evaluation"]["status"] == "missing"
    assert items["agent_exploration"]["status"] == "missing"
    assert manifest["completeness"] < 1.0


def test_downloadable_reports_expose_agent_closure_metrics() -> None:
    result = _result(
        agent_evaluation={
            "taskSuccess": False,
            "executedStepCount": 4,
            "failedStepCount": 1,
            "noProgressStepCount": 1,
            "recoveryAttemptCount": 2,
            "recoverySuccessCount": 1,
            "multimodalCoverage": 1.0,
            "evidenceCompleteness": 0.99,
            "acceptanceLevel": "not_accepted",
        },
        agent_exploration={
            "scope": "current_run_only",
            "visitedStateCount": 3,
            "currentStateKey": "wizard|step-2|sig",
        },
    )

    markdown = render_markdown(result)
    html = render_html(result)

    assert "Agent 闭环评测" in markdown
    assert "恢复成功：1" in markdown
    assert "已访问页面状态：3" in markdown
    assert "Agent 闭环评测" in html
    assert "证据完整率：99.00%" in html
