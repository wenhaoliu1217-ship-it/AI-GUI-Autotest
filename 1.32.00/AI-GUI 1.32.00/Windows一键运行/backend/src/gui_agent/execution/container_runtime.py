"""Host-side Docker launcher for one isolated Runner container."""

from __future__ import annotations

import json
import hashlib
import os
import queue
import shutil
import subprocess
from dataclasses import fields, replace
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from ..domain.models import TestPlan
from ..planning.agent_planner import AIAgentPlanner
from ..planning.replay_planner import AdaptiveReplayPlanner
from ..planning.visual_adapter import OpenAIVisualAdapter
from .runner import RunnerConfig


DEFAULT_RUNNER_IMAGE = "ai-gui-runner:1.32.00"
DEFAULT_RUNNER_CPUS = "2"
DEFAULT_RUNNER_PIDS = "256"


def _settings_payload(settings) -> dict:
    return {
        "protocol": settings.protocol,
        "base_url": settings.base_url,
        "model": settings.model,
        "api_key": settings.api_key.get_secret_value(),
        "input_cost_per_million": settings.input_cost_per_million,
        "output_cost_per_million": settings.output_cost_per_million,
    }


def build_container_spec(plan: TestPlan, config: RunnerConfig) -> dict:
    excluded = {
        "artifacts_root", "cancel_event", "progress_callback", "confirmation_callback",
        "clarification_callback",
        "agent_planner", "visual_adapter",
    }
    payload: dict[str, Any] = {}
    for item in fields(RunnerConfig):
        if item.name in excluded:
            continue
        value = getattr(config, item.name)
        if isinstance(value, Path):
            value = str(value)
        payload[item.name] = value
    environment: dict[str, str] = {}
    secret_targets = dict(config.secret_refs)
    for step in plan.steps:
        if step.value_from_secret:
            target = secret_targets.get(step.value_from_secret, step.value_from_secret)
            if target in os.environ:
                environment[target] = os.environ[target]
    for _, target in config.secret_refs:
        if target in os.environ:
            environment[target] = os.environ[target]
    pending: list[Any] = [plan.model_dump(mode="json")]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            name = value[2:-1]
            if name in os.environ:
                environment[name] = os.environ[name]
    spec: dict[str, Any] = {
        "plan": plan.model_dump(mode="json"),
        "config": payload,
        "environment": environment,
    }
    if isinstance(config.agent_planner, AIAgentPlanner):
        spec["agent_planner"] = {
            "kind": "ai",
            "settings": _settings_payload(config.agent_planner.settings),
            "scenario": config.agent_planner.scenario.model_dump(mode="json"),
            "base_url": config.agent_planner.base_url,
            "visual_enabled": config.agent_planner.visual_enabled,
        }
    elif isinstance(config.agent_planner, AdaptiveReplayPlanner):
        spec["agent_planner"] = {
            "kind": "adaptive_replay",
            "plan": config.agent_planner.plan.model_dump(mode="json"),
        }
    if isinstance(config.visual_adapter, OpenAIVisualAdapter):
        spec["visual_adapter"] = {
            "settings": _settings_payload(config.visual_adapter.settings),
            "minimum_confidence": config.visual_adapter.minimum_confidence,
        }
    return spec


def build_docker_command(
    docker: str,
    image: str,
    run_dir: Path,
    run_id: str,
    config: RunnerConfig,
) -> list[str]:
    return [
        docker, "run", "--rm", "-i", "--name", f"ai-gui-{run_id.lower()}",
        "--network", "bridge",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=256m",
        "--tmpfs", "/home/runner:rw,nosuid,nodev,size=64m,uid=10001,gid=10001",
        "--tmpfs", "/run:rw,nosuid,nodev,noexec,size=4m",
        "--mount", f"type=bind,src={run_dir},dst=/work/artifacts/{run_id}",
        "--memory", f"{config.isolation_memory_limit_mb}m",
        "--cpus", os.getenv("GUI_RUNNER_CPUS", DEFAULT_RUNNER_CPUS),
        "--pids-limit", os.getenv("GUI_RUNNER_PIDS", DEFAULT_RUNNER_PIDS),
        "--cap-drop", "ALL",
        "--cap-add", "NET_ADMIN",
        "--cap-add", "SETUID",
        "--cap-add", "SETGID",
        "--cap-add", "SETPCAP",
        "--security-opt", "no-new-privileges:true",
        "--env", f"GUI_ALLOW_PRIVATE_NETWORK={int(config.allow_private_network)}",
        "--shm-size", "512m",
        image,
    ]


class DockerRunHandle:
    def __init__(
        self,
        plan: TestPlan,
        config: RunnerConfig,
        run_id: str,
        *,
        image: str | None = None,
    ) -> None:
        docker = os.getenv("GUI_DOCKER_CLI") or shutil.which("docker")
        if not docker:
            candidate = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
            docker = str(candidate) if candidate.is_file() else None
        if not docker:
            raise RuntimeError("Docker CLI 不可用，容器 Runner 拒绝降级")
        self.container_name = f"ai-gui-{run_id.lower()}"
        self.run_dir = (Path(config.artifacts_root).resolve() / run_id)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        effective_config = _stage_file_assets(config, self.run_dir, run_id)
        self.image = image or os.getenv("GUI_RUNNER_IMAGE", DEFAULT_RUNNER_IMAGE)
        self.network_mode = "bridge"
        self.private_network_allowed = bool(config.allow_private_network)
        command = build_docker_command(
            docker, self.image, self.run_dir, run_id, config
        )
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self._messages: queue.Queue = queue.Queue()
        self._stderr: list[str] = []
        self._write_lock = Lock()
        Thread(target=self._read_stdout, daemon=True).start()
        Thread(target=self._read_stderr, daemon=True).start()
        self.send(build_container_spec(plan, effective_config))

    @property
    def pid(self) -> int:
        return self.process.pid

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def send(self, message: dict) -> None:
        if self.process.stdin is None:
            raise RuntimeError("容器 Runner 控制通道已关闭")
        with self._write_lock:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
            self.process.stdin.flush()

    def poll_message(self, timeout: float) -> dict | None:
        try:
            return self._messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def terminate(self) -> None:
        docker = os.getenv("GUI_DOCKER_CLI") or shutil.which("docker") or r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
        subprocess.run(
            [docker, "kill", self.container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if self.process.poll() is None:
            self.process.kill()

    def wait(self, timeout: float) -> int | None:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def error_summary(self) -> str:
        return "".join(self._stderr)[-4000:].strip()

    def _read_stdout(self) -> None:
        if self.process.stdout is None:
            return
        for line in self.process.stdout:
            try:
                self._messages.put(json.loads(line))
            except json.JSONDecodeError:
                self._messages.put({"type": "protocol_error", "error": line.strip()})

    def _read_stderr(self) -> None:
        if self.process.stderr is None:
            return
        for line in self.process.stderr:
            self._stderr.append(line)


def _stage_file_assets(config: RunnerConfig, run_dir: Path, run_id: str) -> RunnerConfig:
    if not config.file_assets:
        return config
    target_root = run_dir / "_inputs"
    target_root.mkdir(parents=True, exist_ok=True)
    staged = []
    for asset_ref, source_text in config.file_assets:
        digest = asset_ref.removeprefix("asset:")
        source = Path(source_text).resolve()
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise RuntimeError("容器暂存前文件资产完整性校验失败")
        target = target_root / digest
        shutil.copy2(source, target)
        target.chmod(0o444)
        staged.append((asset_ref, f"/work/artifacts/{run_id}/_inputs/{digest}"))
    return replace(config, file_assets=tuple(staged))
