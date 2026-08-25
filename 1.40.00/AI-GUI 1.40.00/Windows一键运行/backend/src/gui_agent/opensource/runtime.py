"""Isolated upstream runtime boundary for optional GUI-Agent providers.

The product process never imports BrowserGym or AgentLab.  Runtime probes and
the small local contract run are delegated to an explicitly configured Python
interpreter in a child process.  This keeps optional research dependencies out
of the shipped application and makes the runtime claim evidence-based.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal


RuntimeProvider = Literal["browsergym", "agentlab"]
RUNTIME_PYTHON_ENV = "GUI_AGENT_OPENSOURCE_RUNTIME_PYTHON"
_PROBE_TIMEOUT_SECONDS = 15
_RUN_TIMEOUT_SECONDS = 90


class OpenSourceRuntimeError(RuntimeError):
    """An optional upstream provider could not be probed or executed."""


def _configured_python() -> tuple[Path | None, str | None]:
    raw = os.getenv(RUNTIME_PYTHON_ENV, "").strip()
    if not raw:
        return None, None
    path = Path(raw).expanduser()
    if not path.is_file():
        return None, raw
    return path, raw


def _provider_probe_code(provider: RuntimeProvider) -> str:
    if provider == "browsergym":
        return (
            "import browsergym.core, json; "
            "print(json.dumps({'module':'browsergym.core',"
            "'version':getattr(browsergym.core, '__version__', None)}, ensure_ascii=False))"
        )
    return (
        "from agentlab.experiments.loop import StepInfo; import importlib.metadata, json; "
        "print(json.dumps({'module':'agentlab.experiments.loop',"
        "'version':importlib.metadata.version('agentlab'),"
        "'stepInfoFields':list(StepInfo.__dataclass_fields__)}, ensure_ascii=False))"
    )


def _child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    return environment


def _parse_child_json(stdout: str, provider: RuntimeProvider) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if not line.strip():
            continue
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            return result
    raise OpenSourceRuntimeError(f"{provider} 子进程未返回 JSON 运行时证据")


def _probe_provider(provider: RuntimeProvider, python_path: Path | None, raw_path: str | None) -> dict[str, Any]:
    base = {
        "provider": provider,
        "module": "browsergym.core" if provider == "browsergym" else "agentlab.experiments.loop",
        "configuredPython": raw_path,
        "pythonPath": str(python_path) if python_path else None,
        "executionBoundary": "isolated_child_process",
    }
    if not raw_path:
        return {
            **base,
            "available": False,
            "runtimeReady": False,
            "status": "not_configured",
            "reasons": [f"请配置 {RUNTIME_PYTHON_ENV} 指向隔离环境 Python"],
        }
    if python_path is None:
        return {
            **base,
            "available": False,
            "runtimeReady": False,
            "status": "invalid_python_path",
            "reasons": ["配置的 Python 路径不存在或不是文件"],
        }
    try:
        completed = subprocess.run(
            [str(python_path), "-c", _provider_probe_code(provider)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_SECONDS,
            env=_child_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            **base,
            "available": False,
            "runtimeReady": False,
            "status": "probe_failed",
            "reasons": [f"子进程探针失败：{type(exc).__name__}"],
        }
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        return {
            **base,
            "available": False,
            "runtimeReady": False,
            "status": "probe_failed",
            "reasons": [detail[0][:500] if detail else f"子进程退出码 {completed.returncode}"],
        }
    try:
        evidence = _parse_child_json(completed.stdout, provider)
    except OpenSourceRuntimeError as exc:
        return {
            **base,
            "available": False,
            "runtimeReady": False,
            "status": "probe_failed",
            "reasons": [str(exc)],
        }
    return {
        **base,
        "available": True,
        "runtimeReady": True,
        "status": "runtime_ready",
        "version": evidence.get("version"),
        "probeEvidence": evidence,
        "reasons": ["上游模块导入探针通过；真实动作仍需显式运行 provider"],
    }


def open_source_runtime_status() -> dict[str, Any]:
    """Return evidence-backed availability for the configured upstream runtime."""

    python_path, raw_path = _configured_python()
    providers = [_probe_provider(provider, python_path, raw_path) for provider in ("browsergym", "agentlab")]
    return {
        "schemaVersion": "1",
        "source": "isolated_upstream_runtime_probe",
        "configurationEnv": RUNTIME_PYTHON_ENV,
        "configuredPython": raw_path,
        "executionBoundary": "isolated_child_process",
        "providers": providers,
        "summary": {
            "configured": bool(raw_path),
            "runtimeReady": sum(bool(item["runtimeReady"]) for item in providers),
            "providerCount": len(providers),
        },
    }


def run_open_source_runtime(provider: RuntimeProvider) -> dict[str, Any]:
    """Run one product-owned local contract through an upstream child runtime."""

    python_path, raw_path = _configured_python()
    status = _probe_provider(provider, python_path, raw_path)
    if not status["runtimeReady"] or python_path is None:
        reason = "; ".join(status.get("reasons", [])) or "上游运行时未就绪"
        raise OpenSourceRuntimeError(reason)

    runner = Path(__file__).with_name("runtime_runner.py")
    if not runner.is_file():
        raise OpenSourceRuntimeError("缺少隔离运行时 runner")
    try:
        completed = subprocess.run(
            [str(python_path), str(runner), provider],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_RUN_TIMEOUT_SECONDS,
            env=_child_environment(),
            cwd=str(runner.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise OpenSourceRuntimeError(f"{provider} 隔离运行超时") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpenSourceRuntimeError(f"{provider} 隔离运行启动失败：{type(exc).__name__}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        raise OpenSourceRuntimeError(detail[0][:1_000] if detail else f"{provider} 子进程退出码 {completed.returncode}")

    payload = _parse_child_json(completed.stdout, provider)
    from .adapters import normalize_agentlab_experiment, normalize_browsergym_trajectory

    normalized = (
        normalize_browsergym_trajectory(payload)
        if provider == "browsergym"
        else normalize_agentlab_experiment(payload)
    )
    normalized["runtimeEvidence"] = {
        "source": "upstream_child_process",
        "upstreamRuntimeStarted": True,
        "executionBoundary": "isolated_child_process",
        "pythonPath": str(python_path),
        "module": status["module"],
        "version": status.get("version"),
        "actionPolicy": "local_fixture_only",
    }
    return normalized
