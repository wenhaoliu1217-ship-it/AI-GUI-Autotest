"""Business-object dependency ordering and best-effort reverse cleanup."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable


def reverse_cleanup_order(objects: tuple[dict, ...]) -> list[dict]:
    by_key = {item["key"]: item for item in objects}
    visited: set[str] = set()
    ordered: list[dict] = []
    def visit(key: str) -> None:
        if key in visited:
            return
        visited.add(key)
        for dependency in by_key[key].get("dependencies", []):
            visit(dependency)
        ordered.append(by_key[key])
    for key in by_key:
        visit(key)
    return list(reversed(ordered))


def cleanup_business_objects(
    objects: tuple[dict, ...], execute: Callable[[dict], dict], verify: Callable[[dict], bool],
) -> dict[str, Any]:
    started = datetime.now().astimezone().isoformat()
    entries: list[dict[str, Any]] = []
    for item in reverse_cleanup_order(objects):
        entry = {
            "key": item["key"], "objectType": item.get("objectType"), "name": item.get("name"),
            "businessId": item.get("businessId"), "dependencies": item.get("dependencies", []),
            "manualFallback": item.get("manualFallback", ""), "startedAt": datetime.now().astimezone().isoformat(),
        }
        if item.get("reuse"):
            entry.update(status="skipped_reuse", verified=True)
        else:
            try:
                entry["execution"] = execute(item)
                entry["verified"] = verify(item)
                entry["status"] = "cleaned" if entry["verified"] else "residual_found"
                if not entry["verified"]:
                    entry["error"] = "清理动作执行后仍检测到业务对象"
            except Exception as exc:
                entry.update(status="cleanup_failed", verified=False, error=str(exc))
        entry["endedAt"] = datetime.now().astimezone().isoformat()
        entries.append(entry)
    failures = [item for item in entries if item["status"] in {"residual_found", "cleanup_failed"}]
    return {
        "startedAt": started, "endedAt": datetime.now().astimezone().isoformat(),
        "status": "passed" if not failures else "failed", "objects": entries,
        "manualActions": [item["manualFallback"] for item in failures if item.get("manualFallback")],
    }
