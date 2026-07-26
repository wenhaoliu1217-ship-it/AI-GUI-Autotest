"""Canonical commerce state machines and bounded read-only evidence polling."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from time import monotonic, sleep
from urllib.parse import quote, urljoin

from .models import CommerceStateProbe


STATE_TRANSITIONS = {
    "order": {
        "draft": {"pending_payment", "cancelled"},
        "pending_payment": {"paid", "cancelled", "closed"},
        "paid": {"fulfilling", "cancelling", "refund_pending"},
        "fulfilling": {"shipped", "cancelled"},
        "shipped": {"completed", "return_pending"},
        "cancelling": {"cancelled"},
        "refund_pending": {"refunded"},
        "return_pending": {"refunded", "completed"},
        "completed": set(), "cancelled": set(), "closed": set(), "refunded": set(),
    },
    "inventory": {
        "available": {"reserved", "unavailable"},
        "reserved": {"released", "deducted"},
        "released": {"available"},
        "deducted": set(), "unavailable": {"available"},
    },
    "payment": {
        "initiated": {"pending", "cancelled"},
        "pending": {"succeeded", "failed", "cancelled", "timed_out"},
        "succeeded": {"refunding", "refunded"},
        "refunding": {"refunded", "refund_failed"},
        "failed": set(), "cancelled": set(), "timed_out": set(),
        "refunded": set(), "refund_failed": {"refunding"},
    },
    "refund": {
        "requested": {"approved", "rejected", "cancelled"},
        "approved": {"processing"},
        "processing": {"succeeded", "failed"},
        "failed": {"processing"},
        "rejected": set(), "cancelled": set(), "succeeded": set(),
    },
}


class CommerceStateError(RuntimeError):
    pass


def poll_commerce_state(
    request_context,
    probe: CommerceStateProbe,
    *,
    base_url: str,
    run_id: str,
    target_ref: str | None,
    policy,
) -> dict:
    template = probe.url.replace("${RUN_ID}", quote(run_id, safe=""))
    template = template.replace("${TARGET_REF}", quote(target_ref or "", safe=""))
    url = template if "://" in template else urljoin(base_url.rstrip("/") + "/", template.lstrip("/"))
    policy.check_url(url)
    deadline = monotonic() + probe.timeout_ms / 1000
    observations: list[dict] = []
    previous: str | None = None
    while True:
        response = request_context.get(url, fail_on_status_code=False, timeout=probe.interval_ms + 5_000)
        body = response.body()
        if response.status >= 400:
            raise CommerceStateError(f"后台状态探针返回 HTTP {response.status}")
        try:
            payload = json.loads(body)
            state = str(_json_path(payload, probe.json_path))
        except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise CommerceStateError(f"后台状态证据无法读取 {probe.json_path}") from exc
        if state != previous:
            if previous is not None:
                allowed = STATE_TRANSITIONS[probe.domain].get(previous)
                if allowed is None or state not in allowed:
                    raise CommerceStateError(
                        f"{probe.domain} 状态非法跃迁：{previous} -> {state}"
                    )
            observations.append({
                "state": state,
                "observedAt": datetime.now(timezone.utc).isoformat(),
                "httpStatus": response.status,
                "responseSha256": hashlib.sha256(body).hexdigest(),
            })
            previous = state
        if state == probe.expected_state:
            return {
                "domain": probe.domain,
                "expectedState": probe.expected_state,
                "finalState": state,
                "consistent": True,
                "probeUrlSha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
                "observations": observations,
            }
        if monotonic() >= deadline:
            raise CommerceStateError(
                f"{probe.domain} 状态在时限内未达到 {probe.expected_state}，最终为 {state}"
            )
        sleep(probe.interval_ms / 1000)


def observe_commerce_state(
    request_context,
    probe: CommerceStateProbe,
    *,
    base_url: str,
    run_id: str,
    target_ref: str | None,
    policy,
) -> dict:
    """Read one state without waiting; used to prove whether replay is safe."""
    template = probe.url.replace("${RUN_ID}", quote(run_id, safe=""))
    template = template.replace("${TARGET_REF}", quote(target_ref or "", safe=""))
    url = template if "://" in template else urljoin(base_url.rstrip("/") + "/", template.lstrip("/"))
    policy.check_url(url)
    response = request_context.get(url, fail_on_status_code=False, timeout=probe.interval_ms + 5_000)
    body = response.body()
    if response.status >= 400:
        raise CommerceStateError(f"后台状态探针返回 HTTP {response.status}")
    try:
        payload = json.loads(body)
        state = str(_json_path(payload, probe.json_path))
    except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise CommerceStateError(f"后台状态证据无法读取 {probe.json_path}") from exc
    return {
        "verified": True,
        "domain": probe.domain,
        "state": state,
        "httpStatus": response.status,
        "responseSha256": hashlib.sha256(body).hexdigest(),
        "probeUrlSha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
        "observedAt": datetime.now(timezone.utc).isoformat(),
    }


def _json_path(payload, path: str):
    current = payload
    for segment in path.split("."):
        current = current[int(segment)] if segment.isdigit() else current[segment]
    return current
