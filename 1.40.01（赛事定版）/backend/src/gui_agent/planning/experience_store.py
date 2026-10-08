"""Auditable success-only experience memory for the stepwise Agent."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from ..site_capabilities import resolve_site_capability_pack


class SuccessExperienceStore:
    """Persist only fully accepted multimodal trajectories.

    This is retrieval memory, not model-weight training. Failed and incomplete
    runs remain in the normal artifact audit trail and are never written here.
    """

    _lock = Lock()

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def promote(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self._eligible(payload):
            return None
        target_url = str(payload.get("base_url_summary") or "")
        origin = _origin(target_url)
        site_pack = resolve_site_capability_pack(target_url)
        run_id = str(payload.get("run_id") or "").strip()
        if not origin or not run_id:
            return None
        trajectory = [
            {
                "index": item.get("index"),
                "action": item.get("action"),
                "target": item.get("target_summary"),
                "description": item.get("description"),
                "progress": item.get("progress_assessment"),
                "status": item.get("status"),
                "executionMode": item.get("execution_mode"),
                "actionFingerprint": item.get("action_fingerprint"),
                "beforeState": _state_snapshot(item.get("before")),
                "afterState": _state_snapshot(item.get("after")),
            }
            for item in payload.get("steps", [])
            if isinstance(item, dict) and item.get("status") == "passed"
        ]
        record = {
            "schemaVersion": 2,
            "kind": "success_only_multimodal_experience",
            "runId": run_id,
            "origin": origin,
            "sitePack": site_pack.site_id,
            "sitePackVersion": site_pack.version,
            "scenarioGoal": str(payload.get("scenario_goal") or "")[:2_000],
            "promotedAt": datetime.now().astimezone().isoformat(),
            "multimodalDecisionCount": int(payload.get("multimodal_decision_count") or 0),
            "trajectory": trajectory,
            "assertions": [
                {
                    "type": item.get("type"),
                    "description": item.get("description"),
                    "status": item.get("status"),
                }
                for item in payload.get("assertions", [])
                if isinstance(item, dict)
            ],
            "advisoryOnly": True,
        }
        digest = hashlib.sha256(f"{origin}\0{site_pack.version}\0{run_id}".encode("utf-8")).hexdigest()[:20]
        target = self.root / f"{digest}.json"
        temporary = target.with_suffix(".json.tmp")
        with self._lock:
            temporary.write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(target)
        return record

    def retrieve(self, target_url: str, goal: str, *, limit: int = 3) -> list[dict[str, Any]]:
        origin = _origin(target_url)
        site_pack = resolve_site_capability_pack(target_url)
        if not origin or limit <= 0:
            return []
        goal_terms = set(_terms(goal))
        matches: list[tuple[int, float, dict[str, Any]]] = []
        with self._lock:
            paths = list(self.root.glob("*.json"))
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                payload.get("kind") != "success_only_multimodal_experience"
                or payload.get("schemaVersion") != 2
                or payload.get("origin") != origin
                or payload.get("sitePack") != site_pack.site_id
                or payload.get("sitePackVersion") != site_pack.version
            ):
                continue
            overlap = len(goal_terms.intersection(_terms(str(payload.get("scenarioGoal") or ""))))
            matches.append((overlap, path.stat().st_mtime, payload))
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in matches[:limit]]

    @staticmethod
    def _eligible(payload: dict[str, Any]) -> bool:
        if payload.get("status") != "passed" or payload.get("goal_status") != "achieved":
            return False
        if payload.get("multimodal_required") is not True:
            return False
        if int(payload.get("multimodal_decision_count") or 0) <= 0:
            return False
        assertions = payload.get("assertions")
        if not isinstance(assertions, list) or not assertions:
            return False
        if any(not isinstance(item, dict) or item.get("status") != "passed" for item in assertions):
            return False
        evidence = payload.get("evidence_manifest")
        if not isinstance(evidence, dict) or float(evidence.get("completeness") or 0) < 0.98:
            return False
        calls = payload.get("model_call_records")
        decision_calls = [
            item for item in calls or []
            if isinstance(item, dict)
            and item.get("protocol") != "local"
            and item.get("decision") in {"action", "clarification", "complete", "blocked"}
        ]
        return bool(decision_calls) and all(item.get("multimodal") is True for item in decision_calls)


class CrossActionExperienceCache:
    """State-validated, advisory next-action hints from accepted trajectories.

    The cache deliberately does not replay a stored locator or coordinate. A
    hint is eligible only when the fresh observation has the same semantic page
    identity (and, when available, route). The model must still ground the
    action against the current screenshot, DOM, and accessibility tree.
    """

    policy = "state_validated_advisory_v1"

    def __init__(
        self,
        experiences: list[dict[str, Any]] | None = None,
        *,
        max_experiences: int = 3,
    ) -> None:
        self.experiences = [
            item for item in (experiences or [])
            if isinstance(item, dict)
            and item.get("kind") == "success_only_multimodal_experience"
        ][: max(0, max_experiences)]

    def suggest(
        self,
        observation: Any,
        history: list[Any] | None = None,
        *,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Return fresh-grounding hints whose stored pre-state matches now."""
        if limit <= 0:
            return []
        current = _state_snapshot(observation)
        if not current["pageKey"]:
            return []
        failed_targets = {
            _normalise_target(_safe_cache_target(_value(item, "target_summary", "targetSummary", default="")))
            for item in (history or [])
            if _status_value(_value(item, "status", default="")) in {"error", "failed", "incomplete"}
            and _value(item, "before", default=None) is not None
            and _state_snapshot(_value(item, "before", default=None))["pageKey"] == current["pageKey"]
        }
        candidates: list[tuple[int, float, dict[str, Any]]] = []
        invalidated = 0
        for experience in self.experiences:
            for item in experience.get("trajectory", []):
                if not isinstance(item, dict) or item.get("status") not in {None, "passed"}:
                    # Success store trajectories are passed by construction;
                    # tolerate old records which omitted the status field.
                    continue
                before = _state_snapshot(item.get("beforeState"))
                if before["pageKey"] != current["pageKey"]:
                    continue
                if before["route"] and current["route"] and before["route"] != current["route"]:
                    invalidated += 1
                    continue
                target = _safe_cache_target(item.get("target"))
                if _normalise_target(target) in failed_targets:
                    invalidated += 1
                    continue
                stored_signature = before["signature"]
                signature_match = bool(
                    stored_signature and current["signature"]
                    and stored_signature == current["signature"]
                )
                score = 3 if signature_match else 2
                hint = {
                    "sourceRunId": str(experience.get("runId") or ""),
                    "sourceStepIndex": item.get("index"),
                    "stateMatch": "page_key_route_signature" if signature_match else "page_key_route",
                    "confidence": "high" if signature_match else "medium",
                    "action": str(item.get("action") or ""),
                    "target": target,
                    "description": str(item.get("description") or "")[:500],
                    "executionMode": str(item.get("executionMode") or "locator"),
                    "expectedAfterState": _state_summary(item.get("afterState")),
                    "requiresFreshGrounding": True,
                    "advisoryOnly": True,
                }
                candidates.append((score, _experience_time(experience), hint))
        candidates.sort(key=lambda value: (value[0], value[1]), reverse=True)
        selected: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for _, _, hint in candidates:
            key = (hint["action"], _normalise_target(hint["target"]))
            if key in seen:
                continue
            seen.add(key)
            selected.append(hint)
            if len(selected) >= limit:
                break
        return selected

    def context(
        self,
        observation: Any,
        history: list[Any] | None = None,
        *,
        limit: int = 3,
    ) -> dict[str, Any]:
        hints = self.suggest(observation, history, limit=limit)
        return {
            "enabled": True,
            "policy": self.policy,
            "advisoryOnly": True,
            "currentObservationOverrides": True,
            "matchCount": len(hints),
            "hints": hints,
        }


def _value(item: Any, *names: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        for name in names:
            if name in item:
                return item[name]
        return default
    for name in names:
        if hasattr(item, name):
            return getattr(item, name)
    return default


def _state_snapshot(value: Any) -> dict[str, str]:
    """Keep only stable page identity fields; never persist page source/values."""
    if value is None:
        return {"url": "", "pageKey": "", "route": "", "signature": ""}
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(mode="json", by_alias=True)
        except TypeError:
            value = value.model_dump()
    if not isinstance(value, dict):
        return {"url": "", "pageKey": "", "route": "", "signature": ""}
    semantic = value.get("semantic_summary") or value.get("semanticSummary") or value
    if hasattr(semantic, "model_dump"):
        semantic = semantic.model_dump(mode="json", by_alias=True)
    if not isinstance(semantic, dict):
        semantic = {}
    url = str(value.get("url") or "")[:1_000]
    route = str(semantic.get("route") or "")[:500]
    page_key = str(semantic.get("page_key") or semantic.get("pageKey") or "")[:500]
    signature = str(semantic.get("signature") or "")[:100]
    if not page_key and route:
        page_key = route
    return {"url": url, "pageKey": page_key, "route": route, "signature": signature}


def _state_summary(value: Any) -> dict[str, str]:
    state = _state_snapshot(value)
    return {key: state[key] for key in ("pageKey", "route", "signature") if state[key]}


def _safe_cache_target(value: Any) -> str:
    """Strip ephemeral runtime ids and coordinates before a hint reaches the model."""
    target = str(value or "")[:1_000]
    target = re.sub(r"\bruntime_id=ai_\d+\b", "runtime_id=<fresh>", target)
    target = re.sub(r"\b(?:x|y|left|top)=?-?\d+(?:\.\d+)?\b", "", target, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", target).strip()


def _normalise_target(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _status_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _experience_time(experience: dict[str, Any]) -> float:
    try:
        return datetime.fromisoformat(str(experience.get("promotedAt"))).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


class ExperienceJournal:
    """Append-only host-side journal for every run, including incomplete ones.

    This is deliberately separate from ``SuccessExperienceStore``.  A run can
    be useful for diagnosing a failure or resuming from a checkpoint without
    being safe to present as a verified successful workflow.
    """

    _lock = Lock()

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs_root = self.root / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def record(self, payload: dict[str, Any], *, phase: str = "state") -> dict[str, Any] | None:
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return None
        record = {
            "schemaVersion": 1,
            "kind": "run_experience_snapshot",
            "runId": run_id,
            "phase": phase,
            "recordedAt": datetime.now().astimezone().isoformat(),
            "origin": _origin(str(payload.get("base_url_summary") or "")),
            "planName": str(payload.get("plan_name") or "")[:500],
            "scenarioGoal": str(payload.get("scenario_goal") or "")[:2_000],
            "status": str(payload.get("status") or ""),
            "completionReason": str(payload.get("completion_reason") or "")[:500],
            "goalStatus": str(payload.get("goal_status") or ""),
            "resultClassification": str(payload.get("result_classification") or "")[:200],
            "systemError": str(payload.get("system_error") or "")[:2_000],
            "lastCheckpoint": payload.get("last_checkpoint"),
            "steps": [_step_snapshot(item) for item in payload.get("steps", []) if isinstance(item, dict)],
            "assertions": [
                {
                    "index": item.get("index"),
                    "type": item.get("type"),
                    "description": str(item.get("description") or "")[:500],
                    "status": item.get("status"),
                    "errorMessage": str(item.get("error_message") or "")[:1_000],
                }
                for item in payload.get("assertions", [])
                if isinstance(item, dict)
            ],
            "recovery": {
                "modelCalls": payload.get("model_calls"),
                "modelRecoveryAttempts": payload.get("model_recovery_attempts"),
                "runnerIsolation": payload.get("runner_isolation"),
            },
            "advisoryOnly": True,
        }
        latest = self.runs_root / f"{run_id}.json"
        journal = self.root / "runs.jsonl"
        encoded = json.dumps(record, ensure_ascii=False, indent=2, default=str)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            _write_json_atomic(latest, encoded)
            with journal.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.flush()
                try:
                    import os
                    os.fsync(stream.fileno())
                except OSError:
                    pass
        return record

    def backfill_from_artifacts(
        self, artifacts_root: str | Path, *, limit: int | None = None
    ) -> list[str]:
        """Import existing local run records without duplicating journal entries.

        This is intentionally local-only. It reconstructs the last persisted
        state for runs created before the journal existed; it never promotes a
        run into verified success memory and never sends the data to a model.
        """
        root = Path(artifacts_root)
        if not root.is_dir():
            return []
        with self._lock:
            existing = {path.stem for path in self.runs_root.glob("*.json")}
        candidates = [path for path in root.iterdir() if path.is_dir()]
        candidates.sort(key=lambda path: path.stat().st_mtime)
        imported: list[str] = []
        for run_dir in candidates:
            if limit is not None and len(imported) >= max(0, limit):
                break
            run_id = run_dir.name
            if run_id in existing:
                continue
            state_files = [
                path
                for path in (run_dir / "run.json", run_dir / "run-state.json")
                if path.is_file()
            ]
            if not state_files:
                continue
            state_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            try:
                payload = json.loads(state_files[0].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            payload.setdefault("run_id", run_id)
            if self.record(payload, phase="backfill") is not None:
                imported.append(run_id)
                existing.add(run_id)
        return imported

    def retrieve(self, target_url: str, goal: str, *, limit: int = 5) -> list[dict[str, Any]]:
        origin = _origin(target_url)
        if not origin or limit <= 0:
            return []
        terms = set(_terms(goal))
        matches: list[tuple[int, float, dict[str, Any]]] = []
        with self._lock:
            paths = list(self.runs_root.glob("*.json"))
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("kind") != "run_experience_snapshot" or payload.get("origin") != origin:
                continue
            overlap = len(terms.intersection(_terms(str(payload.get("scenarioGoal") or ""))))
            matches.append((overlap, path.stat().st_mtime, payload))
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in matches[:limit]]


def _step_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    before = item.get("before") if isinstance(item.get("before"), dict) else {}
    after = item.get("after") if isinstance(item.get("after"), dict) else {}
    return {
        "index": item.get("index"),
        "action": item.get("action"),
        "description": str(item.get("description") or "")[:1_000],
        "target": str(item.get("target_summary") or "")[:1_000],
        "status": item.get("status"),
        "errorMessage": str(item.get("error_message") or "")[:1_000],
        "failureCategory": item.get("failure_category"),
        "executionMode": item.get("execution_mode"),
        "screenshot": item.get("screenshot"),
        "beforeState": {
            "url": str(before.get("url") or "")[:1_000],
            "pageKey": ((before.get("semantic_summary") or {}).get("page_key") if isinstance(before.get("semantic_summary"), dict) else None),
            "signature": ((before.get("semantic_summary") or {}).get("signature") if isinstance(before.get("semantic_summary"), dict) else None),
        },
        "afterState": {
            "url": str(after.get("url") or "")[:1_000],
            "pageKey": ((after.get("semantic_summary") or {}).get("page_key") if isinstance(after.get("semantic_summary"), dict) else None),
            "signature": ((after.get("semantic_summary") or {}).get("signature") if isinstance(after.get("semantic_summary"), dict) else None),
        },
        "verification": item.get("verification_evidence"),
        "recovery": item.get("recovery_evidence"),
    }


def _write_json_atomic(target: Path, encoded: str) -> None:
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        try:
            import os
            os.fsync(stream.fileno())
        except OSError:
            pass
    temporary.replace(target)


def _terms(value: str) -> list[str]:
    return [item.lower() for item in value.replace("，", " ").replace("。", " ").split() if item]


def _origin(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    default_port = 80 if scheme == "http" else 443
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{scheme}://{authority}"
