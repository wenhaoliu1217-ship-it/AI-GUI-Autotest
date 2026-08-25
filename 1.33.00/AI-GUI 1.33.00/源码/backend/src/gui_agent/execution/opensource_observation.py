"""Optional read-only observation providers used as runner evidence."""

from __future__ import annotations

from typing import Any

from ..opensource import (
    normalize_openadapt_checkpoint,
    normalize_stagehand_candidate_result,
    observe_playwright_mcp_url,
    run_open_source_runtime,
)
from ..domain.results import Observation
from .checkpoint import observation_fingerprint


def capture_open_source_observation(
    provider: str,
    url: str,
    allowed_hosts: tuple[str, ...],
    native_observation: Observation | None = None,
    step_index: int = 0,
) -> dict[str, Any] | None:
    """Capture supplemental evidence without granting the provider action rights.

    The native Playwright observer remains the execution source of truth.  An
    optional provider can only add a bounded, JSON-safe observation record;
    provider startup or navigation failures are reported as evidence rather
    than failing the user's existing run.
    """

    if provider == "native":
        return None
    if provider == "stagehand":
        if native_observation is None:
            return {
                "provider": provider,
                "status": "unavailable",
                "errorClass": "NativeObservationRequired",
                "error": "Stagehand compatibility evidence requires a native page observation",
            }
        candidates = [
            {
                "action": "inspect",
                "description": line,
                "locator": {"semantic": line},
                "confidence": "observed",
            }
            for line in native_observation.dom_summary[:20]
            if any(line.lower().startswith(prefix) for prefix in ("button", "a", "input", "select", "textarea", "role="))
        ]
        normalized = normalize_stagehand_candidate_result({"candidates": candidates})
        return {
            "provider": provider,
            "status": "ready",
            "compatibilityMode": "native_observation_bridge",
            "observation": {
                "url": native_observation.url,
                "title": native_observation.title,
                "accessibility_summary": native_observation.accessibility_summary[:12_000],
            },
            "candidateResult": normalized,
            "runtimeEvidence": {
                "source": "native_observation_compatibility_bridge",
                "upstreamRuntimeStarted": False,
                "actionPolicy": "candidate_only_no_execution",
            },
            "toolResult": {"ok": True, "candidateCount": len(normalized["candidates"])},
        }
    if provider == "openadapt":
        if native_observation is None:
            return {
                "provider": provider,
                "status": "unavailable",
                "errorClass": "NativeObservationRequired",
                "error": "OpenAdapt compatibility evidence requires a native page observation",
            }
        normalized = normalize_openadapt_checkpoint({
            "checkpoint": {
                "id": f"native-observation-{step_index}",
                "status": "observed",
                "currentUrl": native_observation.url,
                "stepIndex": step_index,
            },
            "evidence": {
                "verified": True,
                "outcome": "native_observation_snapshot",
                "pageFingerprint": observation_fingerprint(native_observation),
            },
        })
        return {
            "provider": provider,
            "status": "ready",
            "compatibilityMode": "native_observation_bridge",
            "observation": {"url": native_observation.url, "title": native_observation.title},
            "checkpointResult": normalized,
            "runtimeEvidence": {
                "source": "native_observation_compatibility_bridge",
                "upstreamRuntimeStarted": False,
                "actionPolicy": "checkpoint_only_no_execution",
            },
            "toolResult": {"ok": True, "checkpointId": normalized["checkpoint"]["id"]},
        }
    if provider != "playwright-mcp":
        return {
            "provider": provider,
            "status": "unsupported",
            "errorClass": "UnsupportedObservationProvider",
            "error": f"不支持的开源观察提供器：{provider}"[:1_000],
        }
    try:
        result = observe_playwright_mcp_url(url, allowed_hosts)
    except Exception as exc:  # provider failure must not disable native execution
        return {
            "provider": provider,
            "status": "unavailable",
            "errorClass": type(exc).__name__,
            "error": str(exc)[:1_000],
        }
    observation = result.get("observation") if isinstance(result, dict) else None
    runtime_evidence = result.get("runtimeEvidence") if isinstance(result, dict) else None
    tool_result = result.get("toolResult") if isinstance(result, dict) else None
    return {
        "provider": provider,
        "status": "ready",
        "observation": observation if isinstance(observation, dict) else {},
        "runtimeEvidence": runtime_evidence if isinstance(runtime_evidence, dict) else {},
        "toolResult": tool_result if isinstance(tool_result, dict) else {},
    }


def record_open_source_runtime_evaluation(artifacts: Any, config: Any) -> dict[str, Any] | None:
    """Run an explicitly selected upstream provider and retain bounded evidence.

    This is intentionally separate from the native Runner action loop.  A
    provider can add evaluation evidence, but it cannot become the source of
    truth for the product's browser actions or assertions.
    """

    provider = str(getattr(config, "open_source_evaluation_provider", "none") or "none")
    if provider in {"", "none"}:
        return None
    if provider not in {"browsergym", "agentlab"}:
        evidence = {
            "provider": provider,
            "status": "unsupported",
            "errorClass": "UnsupportedEvaluationProvider",
            "error": f"unsupported open-source evaluation provider: {provider}"[:1_000],
            "upstreamRuntimeStarted": False,
            "actionPolicy": "local_fixture_only",
        }
        artifacts.event(
            "opensource_runtime_evaluation_unavailable",
            provider=provider,
            status=evidence["status"],
            error_class=evidence["errorClass"],
            error=evidence["error"],
            upstream_runtime_started=False,
            action_policy=evidence["actionPolicy"],
        )
        return evidence
    try:
        result = run_open_source_runtime(provider)
    except Exception as exc:  # provider failure must not fail the product run
        evidence = {
            "provider": provider,
            "status": "unavailable",
            "errorClass": type(exc).__name__,
            "error": str(exc)[:1_000],
            "upstreamRuntimeStarted": False,
            "actionPolicy": "local_fixture_only",
        }
        artifacts.event(
            "opensource_runtime_evaluation_unavailable",
            provider=provider,
            status=evidence["status"],
            error_class=evidence["errorClass"],
            error=evidence["error"],
            upstream_runtime_started=False,
            action_policy=evidence["actionPolicy"],
        )
        return evidence

    if not isinstance(result, dict):
        result = {"status": "invalid_result", "valueType": type(result).__name__}
    path = artifacts.write_json("evaluations/open-source-runtime.json", result)
    runtime = result.get("runtimeEvidence") if isinstance(result.get("runtimeEvidence"), dict) else {}
    summary = result.get("summary")
    if not isinstance(summary, dict):
        summary = result.get("trajectorySummary")
    if not isinstance(summary, dict):
        summary = result.get("evaluation")
    if not isinstance(summary, dict):
        summary = {}
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    quality_metrics = result.get("qualityMetrics") if isinstance(result.get("qualityMetrics"), dict) else {}
    evidence = {
        "provider": provider,
        "status": "ready",
        "upstreamStatus": result.get("status"),
        "runStatus": result.get("runStatus"),
        "upstreamRuntimeStarted": bool(runtime.get("upstreamRuntimeStarted", True)),
        "actionPolicy": runtime.get("actionPolicy") or result.get("actionPolicy") or "local_fixture_only",
        "summary": summary,
        "metrics": metrics,
        "qualityMetrics": quality_metrics,
        "runtimeEvidence": {
            "source": runtime.get("source", "upstream_child_process"),
            "executionBoundary": runtime.get("executionBoundary", "isolated_child_process"),
            "module": runtime.get("module"),
            "version": runtime.get("version"),
        },
        "evidencePath": path,
    }
    artifacts.event(
        "opensource_runtime_evaluation_captured",
        provider=provider,
        status="ready",
        evidence_path=path,
        upstream_status=evidence["upstreamStatus"],
        run_status=evidence["runStatus"],
        upstream_runtime_started=evidence["upstreamRuntimeStarted"],
        action_policy=evidence["actionPolicy"],
    )
    return evidence
