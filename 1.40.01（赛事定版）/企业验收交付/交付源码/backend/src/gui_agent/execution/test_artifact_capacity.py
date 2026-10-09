from pathlib import Path
from tempfile import TemporaryDirectory

from gui_agent.execution.orchestrator import _ensure_artifact_capacity


def test_artifact_capacity_preflight_fails_before_runner_launch() -> None:
    with TemporaryDirectory() as directory:
        try:
            _ensure_artifact_capacity(
                Path(directory),
                minimum_free_mb=1_000_000_000,
            )
        except RuntimeError as exc:
            message = str(exc)
            assert "磁盘空间不足" in message
            assert "GUI_AGENT_ARTIFACTS" in message
        else:
            raise AssertionError("an impossible free-space requirement must block launch")
