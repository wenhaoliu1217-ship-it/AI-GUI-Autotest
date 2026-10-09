"""Host-side Docker launcher for one isolated Runner container."""

from __future__ import annotations

import json
import hashlib
import os
import queue
import shutil
import subprocess
import math
from dataclasses import fields, replace
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from ..domain.models import TestPlan
from ..planning.agent_planner import AIAgentPlanner
from ..planning.replay_planner import AdaptiveReplayPlanner
from ..planning.visual_adapter import OpenAIVisualAdapter
from ..platform_support import resolve_docker_executable, subprocess_creationflags
from .runner import RunnerConfig


DEFAULT_RUNNER_IMAGE = "ai-gui-runner:1.32.00"
DEFAULT_RUNNER_CPUS = "2"
DEFAULT_RUNNER_PIDS = "256"
RUNNER_TMPFS_TMP_MB = 512
RUNNER_TMPFS_HOME_MB = 128
RUNNER_TMPFS_RUN_MB = 4
RUNNER_TMPFS_TOTAL_MB = RUNNER_TMPFS_TMP_MB + RUNNER_TMPFS_HOME_MB + RUNNER_TMPFS_RUN_MB
RUNNER_PACKAGE_TARGET = "/usr/local/lib/python3.12/site-packages/gui_agent"
HEADED_WORKER_COMMAND = (
    "Xvfb :99 -screen 0 1440x960x24 -nolisten tcp -ac >/tmp/xvfb.log 2>&1 & "
    "for attempt in 1 2 3 4 5 6 7 8 9 10; do "
    "[ -S /tmp/.X11-unix/X99 ] && break; sleep 0.1; done; "
    "[ -S /tmp/.X11-unix/X99 ] || { cat /tmp/xvfb.log >&2; exit 1; }; "
    "export DISPLAY=:99; exec python -m gui_agent.execution.container_worker"
)


def resolve_docker_cli() -> str | None:
    """Resolve Docker consistently for both the launcher and an in-process run.

    Docker Desktop on Windows commonly installs its CLI outside PATH. Keeping
    this lookup in the execution layer prevents a service started by another
    process from passing health checks and then failing only when a run starts.
    """
    return resolve_docker_executable()


def docker_engine_ready(docker: str | None = None, timeout: float = 3.0) -> bool:
    """Return whether Docker Engine responds without raising or hanging."""
    executable = docker or resolve_docker_cli()
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, "info", "--format", "{{.ServerVersion}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            creationflags=subprocess_creationflags(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def docker_image_available(image: str = DEFAULT_RUNNER_IMAGE, docker: str | None = None, timeout: float = 3.0) -> bool:
    executable = docker or resolve_docker_cli()
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            creationflags=subprocess_creationflags(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


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
        "manual_login_surface",
        "agent_planner", "visual_adapter", "success_experience_root",
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
            "successful_experiences": config.agent_planner.successful_experiences,
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
    package_source = Path(__file__).resolve().parents[1]
    command = [
        docker, "run", "--rm", "-i", "--name", f"ai-gui-{run_id.lower()}",
        "--label", f"ai-gui.delivery={os.getenv('GUI_DELIVERY_ID', 'unassigned')}",
        "--network", "bridge",
        "--read-only",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,noexec,size={RUNNER_TMPFS_TMP_MB}m",
        "--tmpfs", f"/home/runner:rw,nosuid,nodev,size={RUNNER_TMPFS_HOME_MB}m,uid=10001,gid=10001",
        "--tmpfs", f"/run:rw,nosuid,nodev,noexec,size={RUNNER_TMPFS_RUN_MB}m",
        "--mount", f"type=bind,src={run_dir},dst=/work/artifacts/{run_id}",
        "--mount", f"type=bind,src={package_source},dst={RUNNER_PACKAGE_TARGET},readonly",
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
        "--env", f"GUI_BROWSER={config.browser_name or os.getenv('GUI_BROWSER', 'chromium')}",
        "--shm-size", "512m",
        image,
    ]
    if not config.headless:
        command.extend(["sh", "-lc", HEADED_WORKER_COMMAND])
    return command


class DockerRunHandle:
    def __init__(
        self,
        plan: TestPlan,
        config: RunnerConfig,
        run_id: str,
        *,
        image: str | None = None,
    ) -> None:
        docker = resolve_docker_cli()
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
            creationflags=subprocess_creationflags(),
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

    def graceful_stop(self, timeout: float = 5.0) -> bool:
        """Ask Docker to stop the container before resorting to a kill.

        The worker receives the cancellation message first. This second
        signal gives PID 1 and any browser children a bounded opportunity to
        flush their mounted artifacts and exit cleanly.
        """
        if not self.is_alive():
            return True
        docker = resolve_docker_cli()
        if docker:
            seconds = max(1, int(math.ceil(timeout)))
            try:
                subprocess.run(
                    [docker, "stop", "--time", str(seconds), self.container_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=seconds + 5,
                    check=False,
                    creationflags=subprocess_creationflags(),
                )
            except (OSError, subprocess.SubprocessError):
                pass
        self.wait(max(0.1, timeout + 1.0))
        return not self.is_alive()

    def terminate(self) -> None:
        """Final forced termination; call only after graceful_stop()."""
        docker = resolve_docker_cli()
        if docker:
            try:
                subprocess.run(
                    [docker, "kill", self.container_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                    creationflags=subprocess_creationflags(),
                )
            except (OSError, subprocess.SubprocessError):
                pass
        if self.process.poll() is None:
            try:
                self.process.kill()
            except OSError:
                pass

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
        # Preserve the registered extension so sites that validate uploads by
        # filename (for example Cesium ion's GeoJSON form) recognize the type.
        target = target_root / f"{digest}{source.suffix}"
        shutil.copy2(source, target)
        target.chmod(0o444)
        staged.append((asset_ref, f"/work/artifacts/{run_id}/_inputs/{digest}{source.suffix}"))
    return replace(config, file_assets=tuple(staged))
