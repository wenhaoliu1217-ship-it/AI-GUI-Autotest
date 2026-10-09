from pathlib import Path

from gui_agent.domain.models import ActionType, Step, TestPlan
from gui_agent.execution.container_runtime import (
    HEADED_WORKER_COMMAND,
    RUNNER_TMPFS_HOME_MB,
    RUNNER_TMPFS_RUN_MB,
    RUNNER_TMPFS_TMP_MB,
    RUNNER_TMPFS_TOTAL_MB,
    RUNNER_PACKAGE_TARGET,
    build_docker_command,
)
from gui_agent.execution.runner import RunnerConfig


def test_container_has_bounded_space_for_large_browser_pages() -> None:
    plan = TestPlan(
        name="container limits",
        base_url="https://example.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/", description="Open")],
    )
    command = build_docker_command(
        "docker",
        "runner:test",
        Path("artifacts/run"),
        "run",
        RunnerConfig(),
    )
    joined = " ".join(command)

    assert f"/tmp:rw,nosuid,nodev,noexec,size={RUNNER_TMPFS_TMP_MB}m" in joined
    assert f"/home/runner:rw,nosuid,nodev,size={RUNNER_TMPFS_HOME_MB}m" in joined
    assert f"/run:rw,nosuid,nodev,noexec,size={RUNNER_TMPFS_RUN_MB}m" in joined
    assert RUNNER_TMPFS_TOTAL_MB == 644


def test_headed_container_runs_worker_under_xvfb() -> None:
    command = build_docker_command(
        "docker",
        "runner:test",
        Path("artifacts/run"),
        "run",
        RunnerConfig(headless=False),
    )

    assert command[-4:] == [
        "runner:test",
        "sh",
        "-lc",
        HEADED_WORKER_COMMAND,
    ]


def test_headless_container_uses_image_default_worker_command() -> None:
    command = build_docker_command(
        "docker",
        "runner:test",
        Path("artifacts/run"),
        "run",
        RunnerConfig(headless=True),
    )

    assert command[-1] == "runner:test"


def test_container_executes_current_host_package_source_read_only() -> None:
    run_dir = Path("artifacts/run").resolve()
    command = build_docker_command(
        "docker",
        "runner:test",
        run_dir,
        "run",
        RunnerConfig(),
    )
    mounts = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "--mount"]

    package_mount = next(value for value in mounts if f"dst={RUNNER_PACKAGE_TARGET}" in value)
    assert "type=bind" in package_mount
    assert package_mount.endswith(",readonly")
    assert "src=" in package_mount
