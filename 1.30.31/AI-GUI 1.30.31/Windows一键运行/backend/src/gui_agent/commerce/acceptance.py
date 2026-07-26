"""Persistent, catalog-driven acceptance batches with fixed denominators."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4


class AcceptanceBatchError(ValueError):
    pass


class AcceptanceBatchStore:
    def __init__(self, root: Path, catalog_root: Path, *, repeats: int = 5) -> None:
        self.root = root
        self.catalog_root = catalog_root
        self.repeats = repeats
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def start(self) -> dict:
        scenarios = self._scenarios()
        now = _now()
        batch_id = f"jd-acceptance-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        attempts = []
        for scenario in scenarios:
            blockers = list(scenario.get("blockedDependencies") or [])
            if scenario.get("bindingStatus") == "bound" and not isinstance(scenario.get("executablePlan"), dict):
                blockers.append("场景缺少可执行计划绑定")
            bound = scenario.get("bindingStatus") == "bound" and not blockers
            for repeat in range(1, self.repeats + 1):
                attempts.append({
                    "id": f"{scenario['id']}#{repeat}", "scenarioId": scenario["id"],
                    "priority": scenario["priority"], "title": scenario["title"],
                    "repeat": repeat, "status": "queued" if bound else "blocked",
                    "verificationStatus": "pending" if bound else "unverified",
                    "blockedDependencies": blockers, "runId": None, "updatedAt": now,
                    "evidenceCompleteness": None, "stableReplay": None,
                    "amountAccurate": None, "cleanupComplete": None,
                    "zeroToleranceIncidents": {},
                })
        batch = {
            "id": batch_id, "profile": "jd-commerce-1.30.31", "status": "blocked" if all(a["status"] == "blocked" for a in attempts) else "ready",
            "verificationStatus": "unverified", "createdAt": now, "updatedAt": now,
            "repeatCount": self.repeats, "scenarioCount": len(scenarios),
            "plannedAttempts": len(attempts), "attempts": attempts, "cancelRequested": False,
        }
        return self._save(self._summarize(batch))

    def list(self) -> list[dict]:
        return [self._load(path.stem) for path in sorted(self.root.glob("*.json"), reverse=True)]

    def get(self, batch_id: str) -> dict:
        return self._load(batch_id)

    def cancel(self, batch_id: str) -> dict:
        batch = self._load(batch_id)
        for attempt in batch["attempts"]:
            if attempt["status"] in {"queued", "running"}:
                attempt["status"] = "cancelled"
                attempt["verificationStatus"] = "unverified"
                attempt["updatedAt"] = _now()
        batch.update({"cancelRequested": True, "status": "cancelled", "updatedAt": _now()})
        return self._save(self._summarize(batch))

    def resume(self, batch_id: str) -> dict:
        batch = self._load(batch_id)
        for attempt in batch["attempts"]:
            if attempt["status"] == "cancelled":
                blockers = attempt.get("blockedDependencies") or []
                attempt["status"] = "blocked" if blockers else "queued"
                attempt["updatedAt"] = _now()
        batch.update({"cancelRequested": False, "updatedAt": _now()})
        return self._save(self._summarize(batch))

    def retry_failed(self, batch_id: str) -> dict:
        batch = self._load(batch_id)
        for attempt in batch["attempts"]:
            if attempt["status"] == "failed":
                attempt.update({
                    "status": "blocked" if attempt.get("blockedDependencies") else "queued",
                    "verificationStatus": "unverified", "runId": None,
                    "evidenceCompleteness": None, "stableReplay": None,
                    "amountAccurate": None, "cleanupComplete": None,
                    "zeroToleranceIncidents": {}, "updatedAt": _now(),
                })
        batch["updatedAt"] = _now()
        return self._save(self._summarize(batch))

    def record_attempt(self, batch_id: str, attempt_id: str, result: dict) -> dict:
        batch = self._load(batch_id)
        attempt = next((item for item in batch["attempts"] if item["id"] == attempt_id), None)
        if attempt is None:
            raise AcceptanceBatchError("验收尝试不存在")
        status = result.get("status")
        if status not in {"passed", "failed", "blocked"}:
            raise AcceptanceBatchError("尝试结果只允许 passed、failed 或 blocked")
        incidents = result.get("zeroToleranceIncidents") or {}
        allowed_incidents = {
            "duplicateOrder", "duplicateCharge", "duplicateRefund", "oversell",
            "privacyLeak", "productionTransaction", "nonE2EMutation",
        }
        if any(key not in allowed_incidents or not isinstance(value, int) or value < 0 for key, value in incidents.items()):
            raise AcceptanceBatchError("零容忍事件类别或计数非法")
        attempt.update({
            "status": status,
            "verificationStatus": "verified" if status in {"passed", "failed"} else "unverified",
            "runId": result.get("runId"),
            "evidenceCompleteness": result.get("evidenceCompleteness"),
            "stableReplay": result.get("stableReplay"),
            "amountAccurate": result.get("amountAccurate"),
            "cleanupComplete": result.get("cleanupComplete"),
            "zeroToleranceIncidents": incidents,
            "blockedDependencies": result.get("blockedDependencies") or attempt.get("blockedDependencies") or [],
            "updatedAt": _now(),
        })
        batch["updatedAt"] = _now()
        return self._save(self._summarize(batch))

    def _summarize(self, batch: dict) -> dict:
        attempts = batch["attempts"]
        counts = {name: sum(a["status"] == name for a in attempts) for name in ("queued", "running", "passed", "failed", "blocked", "cancelled")}
        verified = counts["passed"] + counts["failed"]
        p0 = [a for a in attempts if a["priority"] == "P0"]
        evidence = [a["evidenceCompleteness"] for a in attempts if a["evidenceCompleteness"] is not None]
        stable = [a for a in attempts if a["stableReplay"] is not None]
        amounts = [a["amountAccurate"] for a in attempts if a["amountAccurate"] is not None]
        cleanups = [a["cleanupComplete"] for a in attempts if a["cleanupComplete"] is not None]
        zero_incidents = sum(sum(int(value) for value in (a.get("zeroToleranceIncidents") or {}).values()) for a in attempts)
        thresholds = {
            "p0Completion": {"actual": sum(a["status"] == "passed" for a in p0) / len(p0) if p0 else 0, "required": 1.0},
            "allScenarioPassRate": {"actual": counts["passed"] / len(attempts) if attempts else 0, "required": 0.95},
            "stableReplayRate": {"actual": sum(a["stableReplay"] is True for a in stable) / len(stable) if stable else 0, "required": 0.95},
            "evidenceCompleteness": {"actual": sum(evidence) / len(evidence) if evidence else 0, "required": 0.98},
            "amountAccuracy": {"actual": bool(amounts) and all(value is True for value in amounts), "required": True},
            "cleanupCompleteness": {"actual": bool(cleanups) and all(value is True for value in cleanups), "required": True},
            "zeroToleranceIncidents": {"actual": zero_incidents, "required": 0},
        }
        all_terminal = not counts["queued"] and not counts["running"]
        all_verified = verified == len(attempts)
        passed = all_verified and all(
            item["actual"] >= item["required"] if isinstance(item["required"], float)
            else item["actual"] == item["required"] for item in thresholds.values()
        )
        batch["summary"] = {"counts": counts, "verifiedAttempts": verified, "thresholds": thresholds, "passed": passed}
        if batch.get("cancelRequested"):
            batch["status"] = "cancelled"
        elif all_terminal:
            batch["status"] = "completed" if all_verified else "blocked"
        else:
            batch["status"] = "running" if counts["running"] else "ready"
        batch["verificationStatus"] = "verified" if all_verified else "unverified"
        return batch

    def _scenarios(self) -> list[dict]:
        scenarios = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(self.catalog_root.glob("J*.json"))]
        if len(scenarios) != 65 or len({item["id"] for item in scenarios}) != 65:
            raise AcceptanceBatchError("京东验收目录必须包含唯一的 J01～J65")
        return scenarios

    def _path(self, batch_id: str) -> Path:
        if not batch_id.startswith("jd-acceptance-") or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in batch_id):
            raise AcceptanceBatchError("验收批次 ID 非法")
        return self.root / f"{batch_id}.json"

    def _load(self, batch_id: str) -> dict:
        path = self._path(batch_id)
        if not path.is_file():
            raise AcceptanceBatchError("验收批次不存在")
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, batch: dict) -> dict:
        with self._lock:
            self._path(batch["id"]).write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        return batch


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
