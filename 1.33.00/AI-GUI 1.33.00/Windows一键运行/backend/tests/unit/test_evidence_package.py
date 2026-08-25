from datetime import datetime

from gui_agent.api.server import _environment_snapshot
from gui_agent.artifacts import ArtifactManager
from gui_agent.artifacts.evidence_package import build_evidence_package
from gui_agent.domain.results import RunResult, Status, StepResult
from gui_agent.onboarding.models import EnvironmentConfig
from gui_agent.security.redaction import Redactor


def _result(*, screenshot: str | None = None) -> RunResult:
    now = datetime.now().astimezone()
    return RunResult(
        run_id="run-evidence-1",
        plan_name="evidence package",
        base_url_summary="https://example.test",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
        steps=[StepResult(
            index=1,
            action="click",
            target_summary="submit",
            status=Status.PASSED,
            started_at=now,
            ended_at=now,
            screenshot=screenshot,
            side_effect_evidence={"businessObjectId": "object-42"},
        )],
    )


def test_manifest_excludes_not_applicable_and_links_run_step_and_business_ids(tmp_path) -> None:
    artifacts = ArtifactManager(tmp_path, "run-evidence-1", Redactor())
    artifacts.write_json("plan.json", {"name": "evidence package"})
    artifacts.trace_path.write_bytes(b"trace")
    result = _result(screenshot="screenshots/step-1.png")
    (artifacts.run_dir / "screenshots" / "step-1.png").write_bytes(b"png")

    manifest, path = build_evidence_package(artifacts, result)

    applicable = [item for item in manifest["items"] if item["status"] != "not_applicable"]
    assert manifest["applicableCount"] == len(applicable)
    assert all(item["runId"] == result.run_id for item in manifest["items"])
    actions = next(item for item in manifest["items"] if item["id"] == "actions")
    assert actions["stepIds"] == ["step-1"]
    assert actions["businessIds"] == ["object-42"]
    assert path == "evidence/evidence-manifest.json"


def test_missing_required_artifacts_lower_completeness(tmp_path) -> None:
    artifacts = ArtifactManager(tmp_path, "run-evidence-1", Redactor())
    artifacts.write_json("plan.json", {"name": "evidence package"})

    manifest, _ = build_evidence_package(artifacts, _result())

    missing = {item["id"] for item in manifest["items"] if item["status"] == "missing"}
    assert {"screenshots", "trace", "generated_test"}.issubset(missing)
    assert manifest["completeness"] < 1
    assert manifest["missingCount"] == manifest["applicableCount"] - manifest["presentCount"]


def test_environment_snapshot_contains_names_but_no_values_or_secret_targets() -> None:
    environment = EnvironmentConfig(
        id="qa",
        projectId="project-1",
        name="QA",
        variables={"BASE_PATH": "CANARY_VARIABLE_VALUE"},
        secretRefs={"LOGIN_PASSWORD": "CANARY_SECRET_TARGET"},
    )

    snapshot = _environment_snapshot(environment)
    serialized = str(snapshot)

    assert snapshot["variableNames"] == ["BASE_PATH"]
    assert snapshot["secretAliases"] == ["LOGIN_PASSWORD"]
    assert "CANARY_VARIABLE_VALUE" not in serialized
    assert "CANARY_SECRET_TARGET" not in serialized
