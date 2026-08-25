"""Persistent ownership ledger used to prevent cleanup of non-E2E resources."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")
_RESOURCE_TYPES = {"asset", "archive", "export", "clip", "token", "story", "label", "oauth_app", "team_invitation"}
_CLEANUP_STATES = {"pending", "completed", "failed", "not_required"}


class LedgerError(ValueError):
    pass


class ResourceLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def list(self, run_id: str | None = None) -> list[dict]:
        entries = self._load()
        return [item for item in entries if run_id is None or item["runId"] == run_id]

    def register(self, payload: dict) -> dict:
        run_id = str(payload.get("runId", ""))
        resource_type = str(payload.get("resourceType", ""))
        resource_id = str(payload.get("resourceId", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not _RUN_ID.fullmatch(run_id):
            raise LedgerError("invalid runId")
        if resource_type not in _RESOURCE_TYPES:
            raise LedgerError("unsupported resourceType")
        if not resource_id:
            raise LedgerError("resourceId is required")
        if not name.startswith("E2E-"):
            raise LedgerError("resource name must use the E2E- ownership prefix")
        now = datetime.now().astimezone().isoformat()
        entry = {
            "ledgerId": f"ledger-{uuid4().hex[:16]}",
            "runId": run_id,
            "caseId": str(payload.get("caseId", "")),
            "resourceType": resource_type,
            "resourceId": resource_id,
            "name": name,
            "accountContext": str(payload.get("accountContext", "personal_e2e_account")),
            "createdAt": now,
            "cleanupStatus": "pending",
            "cleanupEvidence": [],
            "updatedAt": now,
        }
        with self._lock:
            entries = self._load_unlocked()
            if any(item["resourceType"] == resource_type and item["resourceId"] == resource_id for item in entries):
                raise LedgerError("resource already registered")
            entries.append(entry)
            self._save_unlocked(entries)
        return entry

    def record_cleanup(self, ledger_id: str, status: str, evidence: list[str]) -> dict:
        if status not in _CLEANUP_STATES:
            raise LedgerError("invalid cleanup status")
        with self._lock:
            entries = self._load_unlocked()
            entry = next((item for item in entries if item["ledgerId"] == ledger_id), None)
            if entry is None:
                raise LedgerError("ledger entry not found")
            entry["cleanupStatus"] = status
            entry["cleanupEvidence"] = [str(item)[:2000] for item in evidence]
            entry["updatedAt"] = datetime.now().astimezone().isoformat()
            self._save_unlocked(entries)
            return entry

    def summary(self) -> dict:
        entries = self._load()
        pending = [item for item in entries if item["cleanupStatus"] not in {"completed", "not_required"}]
        return {"total": len(entries), "pendingCleanup": len(pending), "zeroResidualProven": bool(entries) and not pending}

    def _load(self) -> list[dict]:
        with self._lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerError("resource ledger is unreadable") from exc
        if not isinstance(payload, list):
            raise LedgerError("resource ledger must contain a JSON array")
        return payload

    def _save_unlocked(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(f".{uuid4().hex}.tmp")
        temp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

