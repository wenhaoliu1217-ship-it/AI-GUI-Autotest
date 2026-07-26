import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gui_agent.artifacts.lifecycle import ArtifactLifecycle, ArtifactLifecycleError


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _write_run(
    root: Path,
    run_id: str,
    *,
    status: str = "passed",
    age_days: int = 31,
    retention: object = 30,
    include_retention: bool = True,
) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    payload = {
        "run_id": run_id,
        "status": status,
        "ended_at": (NOW - timedelta(days=age_days)).isoformat(),
        "project_id": "project-one",
        "environment_id": "environment-one",
    }
    if include_retention:
        payload["artifact_retention_days"] = retention
    (run_dir / "run.json").write_text(json.dumps(payload), encoding="utf-8")
    (run_dir / "evidence.txt").write_text("evidence", encoding="utf-8")
    return run_dir


def test_cleanup_deletes_only_expired_terminal_runs_and_preserves_audit(tmp_path: Path) -> None:
    lifecycle = ArtifactLifecycle(tmp_path)
    expired = _write_run(tmp_path, "expired", age_days=31)
    current = _write_run(tmp_path, "current", age_days=29)

    result = lifecycle.cleanup_expired(actor="retention-worker", now=NOW)

    assert result["deleted"] == ["expired"]
    assert not expired.exists()
    assert current.is_dir()
    records = lifecycle.read_audit()
    assert len(records) == 1
    assert records[0]["action"] == "automatic_retention_cleanup"
    assert records[0]["actor"] == "retention-worker"
    assert records[0]["reason"] == "artifactRetentionDays expired"
    assert records[0]["details"][0] == {
        "run_id": "expired",
        "status": "passed",
        "ended_at": (NOW - timedelta(days=31)).isoformat(),
        "artifact_retention_days": 30,
        "file_count": 2,
        "bytes": records[0]["details"][0]["bytes"],
        "project_id": "project-one",
        "environment_id": "environment-one",
    }
    assert records[0]["details"][0]["bytes"] > 0


@pytest.mark.parametrize("status", ["queued", "running", "pending_confirmation"])
def test_cleanup_never_deletes_active_runs(tmp_path: Path, status: str) -> None:
    run_dir = _write_run(tmp_path, f"active-{status}", status=status, age_days=400, retention=1)

    result = ArtifactLifecycle(tmp_path).cleanup_expired(now=NOW)

    assert result["deleted"] == []
    assert result["skippedActive"] == [run_dir.name]
    assert run_dir.is_dir()


@pytest.mark.parametrize(
    ("retention", "include_retention"),
    [(None, False), ("invalid", True), (0, True), (366, True)],
)
def test_missing_or_invalid_retention_defaults_to_thirty_days(
    tmp_path: Path, retention: object, include_retention: bool
) -> None:
    run_dir = _write_run(
        tmp_path,
        "default-retention",
        age_days=31,
        retention=retention,
        include_retention=include_retention,
    )

    ArtifactLifecycle(tmp_path).cleanup_expired(now=NOW)

    assert not run_dir.exists()


def test_manual_batch_validates_every_run_before_deleting_anything(tmp_path: Path) -> None:
    lifecycle = ArtifactLifecycle(tmp_path)
    valid = _write_run(tmp_path, "valid")
    active = _write_run(tmp_path, "active", status="running")

    with pytest.raises(ArtifactLifecycleError, match="活动运行不能删除"):
        lifecycle.delete_runs(
            ["valid", "active"], action="manual_batch_delete", actor="tester", reason="cleanup"
        )

    assert valid.is_dir()
    assert active.is_dir()
    assert lifecycle.read_audit() == []


def test_reserved_audit_directory_cannot_be_deleted(tmp_path: Path) -> None:
    lifecycle = ArtifactLifecycle(tmp_path)
    lifecycle.audit_path.parent.mkdir(parents=True)
    lifecycle.audit_path.write_text("", encoding="utf-8")

    with pytest.raises(ArtifactLifecycleError, match="保留目录不能删除"):
        lifecycle.delete_runs(
            ["_audit"], action="manual_batch_delete", actor="tester", reason="cleanup"
        )

    assert lifecycle.audit_path.is_file()


def test_manual_deletion_audit_survives_deleted_run_directory(tmp_path: Path) -> None:
    lifecycle = ArtifactLifecycle(tmp_path)
    run_dir = _write_run(tmp_path, "manual")

    result = lifecycle.delete_runs(
        ["manual"], action="manual_batch_delete", actor="local_user", reason="selected in history"
    )

    assert not run_dir.exists()
    record = lifecycle.read_audit()[0]
    assert record["audit_id"] == result["auditId"]
    assert record["status"] == "completed"
    assert record["run_ids"] == ["manual"]
    assert record["actor"] == "local_user"
    assert record["reason"] == "selected in history"
