"""Discover and report the locally archived GUI-Agent reference projects.

This module intentionally does not import any upstream project. The reference
repositories have incompatible dependency graphs and several are benchmark or
desktop-environment projects rather than production runners. The catalog is
the safe boundary used by the GUI while individual adapters are evaluated.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OpenSourceProject:
    project_id: str
    name: str
    repository: str
    license: str
    commit: str
    directory: str
    capabilities: tuple[str, ...]
    entrypoints: tuple[str, ...]
    adaptation: str
    integration_status: str
    license_status: str
    priority: str


PROJECTS: tuple[OpenSourceProject, ...] = (
    OpenSourceProject(
        "playwright-mcp", "Playwright MCP", "microsoft/playwright-mcp", "Apache-2.0", "42e792a40faa", "playwright-mcp",
        ("结构化可访问性观察", "stdio MCP 浏览器工具", "确定性 Playwright 动作"),
        ("cli.js", "index.js", "server.json"),
        "可选 MCP sidecar；结果归一化后进入 Observation/Evidence，继续经过安全门禁。",
        "adapter_ready",
        "approved",
        "P0",
    ),
    OpenSourceProject(
        "playwright-cli", "Playwright CLI", "microsoft/playwright-cli", "Apache-2.0", "eee5a185c98e", "playwright-cli",
        ("录制", "选择器检查", "调试轨迹", "coding-agent CLI"),
        ("skills/", "scripts/", "package.json"),
        "开发/诊断工具；未来可导入轨迹，不替换生产 Runner。",
        "adapter_ready",
        "approved",
        "P2",
    ),
    OpenSourceProject(
        "stagehand", "Stagehand", "browserbase/stagehand", "MIT", "1d49a95c0c23", "stagehand",
        ("observe", "act", "extract", "动作缓存", "结构化模型输出"),
        ("packages/core/lib/inference.ts", "packages/core/lib/v3/agent", "packages/core/package.json"),
        "Node sidecar；只输出候选动作/提取结果，由 Python 安全层执行和验证。",
        "adapter_ready",
        "approved",
        "P0",
    ),
    OpenSourceProject(
        "browser-use", "Browser-use", "browser-use/browser-use", "MIT", "c561b1f514f1", "browser-use",
        ("自然语言 Agent", "动态动作注册", "逐步浏览器状态", "下载管理"),
        ("browser_use/agent/service.py", "browser_use/browser/session.py", "pyproject.toml"),
        "可选 Python sidecar/探针；需隔离依赖和动作权限，不能直接替换当前 Runner。",
        "adapter_ready",
        "approved",
        "P1",
    ),
    OpenSourceProject(
        "openadapt", "OpenAdapt", "OpenAdaptAI/OpenAdapt", "MIT", "f8369b25a132", "openadapt",
        ("录制", "编译", "确定性回放", "lint/certify", "暂停/恢复", "受控修复"),
        ("openadapt/cli.py", "openadapt-flow", "pyproject.toml"),
        "优先适配工作流包、检查点、暂停/恢复和证据，不复制外部引擎。",
        "adapter_ready",
        "approved",
        "P0",
    ),
    OpenSourceProject(
        "browsergym", "BrowserGym", "ServiceNow/BrowserGym", "Apache-2.0", "9e779f087de9", "browsergym",
        ("Gym 环境", "任务注册", "reset/step", "观察", "任务验证"),
        ("browsergym/core/src/browsergym/core/env.py", "task.py", "registration.py"),
        "作为可复现 Web 任务和评测 provider，映射当前 benchmark/report。",
        "adapter_ready",
        "needs_manual_license_review",
        "P1",
    ),
    OpenSourceProject(
        "agentlab", "AgentLab", "ServiceNow/AgentLab", "Apache-2.0", "cbc35a9bc0fa", "agentlab",
        ("Agent 参数", "可重复模式", "实验管理", "轨迹分析", "结果归档"),
        ("src/agentlab/agents/generic_agent/generic_agent.py", "src/agentlab/analyze/", "pyproject.toml"),
        "适配实验元数据、轨迹分析和决策质量指标，不直接引入其 Agent。",
        "adapter_ready",
        "needs_manual_license_review",
        "P1",
    ),
    OpenSourceProject(
        "webarena", "WebArena", "web-arena-x/webarena", "Apache-2.0", "dce04686a562", "webarena",
        ("自托管网站环境", "Playwright Script action", "轨迹保存", "evaluator"),
        ("browser_env/envs.py", "browser_env/trajectory.py", "agent/agent.py"),
        "适配任务、轨迹和 evaluator schema；需要自托管站点，不作为默认本地依赖。",
        "adapter_ready",
        "approved",
        "P1",
    ),
    OpenSourceProject(
        "osworld", "OSWorld", "xlang-ai/OSWorld", "Apache-2.0", "091f5ef1d554", "osworld",
        ("VM 桌面环境", "截图/无障碍树", "坐标动作", "reset/step/evaluate"),
        ("desktop_env/desktop_env.py", "mm_agents/agent.py", "lib_run_single.py"),
        "仅作为未来桌面视觉兜底和跨应用评测 provider，不进入 Web Runner 默认链路。",
        "research_complete",
        "approved",
        "P2",
    ),
    OpenSourceProject(
        "ui-tars", "UI-TARS", "bytedance/UI-TARS", "Apache-2.0", "582f3a7ea5d2", "ui-tars",
        ("视觉模型动作解析", "坐标输出", "截图尺度校正"),
        ("codes/ui_tars/action_parser.py", "README_coordinates.md"),
        "将 action parser 封装为 VisualAdapter 候选解析器，需要独立模型服务和截图授权。",
        "adapter_ready",
        "approved",
        "P2",
    ),
    OpenSourceProject(
        "testzeus-hercules", "TestZeus Hercules", "test-zeus-ai/testzeus-hercules", "AGPL-3.0", "fa2b469e1a6a", "testzeus-hercules",
        ("Gherkin 生成", "导航 Agent", "MCP 运行入口", "报告流程"),
        ("testzeus_hercules/mcp_server.py", "core/runner.py", "core/agents/"),
        "仅作流程和接口研究；许可证评估完成前不复制、不链接为产品依赖。",
        "research_complete",
        "blocked_by_license",
        "P2",
    ),
)


def _reference_root() -> Path | None:
    configured = os.getenv("GUI_AGENT_OPEN_SOURCE_ROOT", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        return path if path.is_dir() else None

    module_path = Path(__file__).resolve()
    for ancestor in (module_path.parent, *module_path.parents):
        candidate = ancestor / "开源GUI-Agent参考项目"
        if candidate.is_dir():
            return candidate
    return None


def _command_version(command: str) -> dict[str, Any]:
    resolved = shutil.which(command)
    if not resolved:
        return {"available": False, "command": command, "version": None}
    try:
        result = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        output = (result.stdout or result.stderr).strip().splitlines()
        return {
            "available": result.returncode == 0,
            "command": command,
            "path": resolved,
            "version": output[0][:160] if output else None,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "command": command, "version": None, "errorClass": type(exc).__name__}


def _docker_status() -> dict[str, Any]:
    """Report Docker CLI and daemon readiness without starting containers."""

    resolved = shutil.which("docker")
    if not resolved:
        return {
            "available": False,
            "daemonReady": False,
            "command": "docker",
            "path": None,
            "status": "command_unavailable",
            "reason": "Docker CLI was not found",
        }
    try:
        result = subprocess.run(
            [resolved, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        server_version = (result.stdout or "").strip().splitlines()
        if result.returncode == 0 and server_version:
            return {
                "available": True,
                "daemonReady": True,
                "command": "docker",
                "path": resolved,
                "serverVersion": server_version[0][:160],
                "status": "ready",
            }
        detail = (result.stderr or result.stdout or "Docker daemon is not available").strip().splitlines()
        return {
            "available": True,
            "daemonReady": False,
            "command": "docker",
            "path": resolved,
            "status": "daemon_unavailable",
            "reason": detail[-1][:500] if detail else "Docker daemon is not available",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": True,
            "daemonReady": False,
            "command": "docker",
            "path": resolved,
            "status": "probe_failed",
            "reason": type(exc).__name__,
        }


def _license_file(path: Path) -> str | None:
    for candidate in path.iterdir() if path.is_dir() else ():
        if candidate.is_file() and candidate.name.upper().startswith("LICENSE"):
            return candidate.name
    return None


def _project_payload(project: OpenSourceProject, root: Path | None, runtime: dict[str, Any]) -> dict[str, Any]:
    path = root / project.directory if root else None
    exists = bool(path and path.is_dir())
    git_exists = bool(path and (path / ".git").exists())
    license_file = _license_file(path) if path else None
    runtime_ready = True
    if project.project_id in {"playwright-mcp", "playwright-cli", "stagehand"}:
        runtime_ready = runtime["node"]["available"]
    elif project.project_id in {"browser-use", "openadapt", "browsergym", "agentlab", "osworld", "testzeus-hercules"}:
        runtime_ready = runtime["python"]["available"]
    elif project.project_id == "webarena":
        runtime_ready = (
            runtime["python"]["available"]
            and runtime.get("docker", {}).get("daemonReady", False)
        )
    return {
        "id": project.project_id,
        "name": project.name,
        "repository": project.repository,
        "license": project.license,
        "commit": project.commit,
        "directory": project.directory,
        "referenceAvailable": exists,
        "gitCheckout": git_exists,
        "licenseFile": license_file,
        "capabilities": list(project.capabilities),
        "entrypoints": list(project.entrypoints),
        "adaptation": project.adaptation,
        "integrationStatus": project.integration_status,
        "licenseStatus": project.license_status,
        "priority": project.priority,
        "runtimeReady": runtime_ready,
    }


def catalog_payload() -> dict[str, Any]:
    from .adapters import adapter_catalog_payload
    from .integration import execution_profiles_payload

    root = _reference_root()
    runtime = {
        "node": _command_version("node"),
        "python": {"available": True, "path": sys.executable, "version": sys.version.splitlines()[0]},
        "docker": _docker_status(),
    }
    projects = [_project_payload(project, root, runtime) for project in PROJECTS]
    available = sum(item["referenceAvailable"] for item in projects)
    adapters = adapter_catalog_payload(root, runtime)
    return {
        "schemaVersion": "1",
        "referenceRoot": str(root) if root else None,
        "source": "local-reference-catalog",
        "projectCount": len(projects),
        "summary": {
            "available": available,
            "missing": len(projects) - available,
            "researched": sum(item["integrationStatus"] in {"research_complete", "adapter_ready"} for item in projects),
            "adapterReady": sum(item["integrationStatus"] == "adapter_ready" for item in projects),
            "licenseReviewRequired": sum(item["licenseStatus"] == "needs_manual_license_review" for item in projects),
            "blockedByLicense": sum(item["licenseStatus"] == "blocked_by_license" for item in projects),
        },
        "runtime": runtime,
        "adapters": adapters,
        "executionProfiles": execution_profiles_payload(),
        "projects": projects,
    }
