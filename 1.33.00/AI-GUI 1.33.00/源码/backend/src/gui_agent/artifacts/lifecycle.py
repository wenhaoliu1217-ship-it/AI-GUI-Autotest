"""Retention cleanup and independent deletion audit for run artifacts."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4


ACTIVE_STATUSES = {"queued", "running", "pending_confirmation"}
_AUDIT_DIR = "_audit"
_AUDIT_FILE = "run-deletions.jsonl"
_AUDIT_LOCK = Lock()


class ArtifactLifecycleError(ValueError):
    pass


class ArtifactLifecycle:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def audit_path(self) -> Path:
        return self.root / _AUDIT_DIR / _AUDIT_FILE

    def delete_runs(
        self,
        run_ids: list[str],
        *,
        action: str,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict:
        unique_ids = list(dict.fromkeys(run_ids))
        if not unique_ids:
            raise ArtifactLifecycleError("至少选择一条运行记录")
        validated = [self._validate_deletable(run_id) for run_id in unique_ids]
        timestamp = (now or datetime.now(timezone.utc)).astimezone()
        details = [self._metadata(run_id, run_dir, payload) for run_id, run_dir, payload in validated]
        try:
            for _, run_dir, _ in validated:
                shutil.rmtree(run_dir)
        except OSError as exc:
            self._append_audit({
                "audit_id": f"deletion-{uuid4().hex[:12]}",
                "timestamp": timestamp.isoformat(),
                "action": action,
                "actor": actor,
                "reason": reason,
                "status": "failed",
                "run_ids": unique_ids,
                "details": details,
                "error": str(exc),
            })
            raise ArtifactLifecycleError(f"删除运行工件失败：{exc}") from exc
        record = {
            "audit_id": f"deletion-{uuid4().hex[:12]}",
            "timestamp": timestamp.isoformat(),
            "action": action,
            "actor": actor,
            "reason": reason,
            "status": "completed",
            "run_ids": unique_ids,
            "details": details,
        }
        self._append_audit(record)
        return {"deleted": unique_ids, "count": len(unique_ids), "auditId": record["audit_id"]}

    def cleanup_expired(self, *, actor: str = "system", now: datetime | None = None) -> dict:
        current = (now or datetime.now(timezone.utc)).astimezone()
        expired: list[str] = []
        skipped_active: list[str] = []
        for run_dir in self.root.iterdir():
            if not run_dir.is_dir() or run_dir.name.startswith("_"):
                continue
            payload = self._read_payload(run_dir)
            if payload is None:
                continue
            if str(payload.get("status", "")) in ACTIVE_STATUSES:
                skipped_active.append(run_dir.name)
                continue
            retention_days = self._retention_days(payload)
            ended_at = self._ended_at(payload, run_dir)
            if ended_at + timedelta(days=retention_days) <= current:
                expired.append(run_dir.name)
        if not expired:
            return {"deleted": [], "count": 0, "skippedActive": skipped_active, "auditId": None}
        result = self.delete_runs(
            expired,
            action="automatic_retention_cleanup",
            actor=actor,
            reason="artifactRetentionDays expired",
            now=current,
        )
        result["skippedActive"] = skipped_active
        return result

    def read_audit(self) -> list[dict]:
        if not self.audit_path.is_file():
            return []
        records: list[dict] = []
        with _AUDIT_LOCK:
            for line in self.audit_path.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
        return records

    def _validate_deletable(self, run_id: str) -> tuple[str, Path, dict]:
        if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in run_id):
            raise ArtifactLifecycleError(f"运行编号非法：{run_id}")
        if run_id.startswith("_"):
            raise ArtifactLifecycleError(f"保留目录不能删除：{run_id}")
        run_dir = (self.root / run_id).resolve()
        if self.root not in run_dir.parents:
            raise ArtifactLifecycleError(f"运行编号非法：{run_id}")
        payload = self._read_payload(run_dir)
        if payload is None:
            raise ArtifactLifecycleError(f"运行记录不存在：{run_id}")
        status = str(payload.get("status", ""))
        if status in ACTIVE_STATUSES:
            raise ArtifactLifecycleError(f"活动运行不能删除：{run_id}（{status}）")
        if not (run_dir / "run.json").is_file():
            raise ArtifactLifecycleError(f"运行尚未形成最终报告，不能删除：{run_id}")
        return run_id, run_dir, payload

    @staticmethod
    def _read_payload(run_dir: Path) -> dict | None:
        for name in ("run.json", "run-state.json"):
            target = run_dir / name
            if not target.is_file():
                continue
            try:
                value = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return value if isinstance(value, dict) else None
        return None

    @staticmethod
    def _retention_days(payload: dict) -> int:
        try:
            value = int(payload.get("artifact_retention_days", 30))
        except (TypeError, ValueError):
            return 30
        return value if 1 <= value <= 365 else 30

    @staticmethod
    def _ended_at(payload: dict, run_dir: Path) -> datetime:
        try:
            value = datetime.fromisoformat(str(payload.get("ended_at")))
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)

    @staticmethod
    def _metadata(run_id: str, run_dir: Path, payload: dict) -> dict:
        files = [path for path in run_dir.rglob("*") if path.is_file()]
        return {
            "run_id": run_id,
            "status": payload.get("status"),
            "ended_at": payload.get("ended_at"),
            "artifact_retention_days": ArtifactLifecycle._retention_days(payload),
            "file_count": len(files),
            "bytes": sum(path.stat().st_size for path in files),
            "project_id": payload.get("project_id"),
            "environment_id": payload.get("environment_id"),
        }

    def _append_audit(self, record: dict) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _AUDIT_LOCK, self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
