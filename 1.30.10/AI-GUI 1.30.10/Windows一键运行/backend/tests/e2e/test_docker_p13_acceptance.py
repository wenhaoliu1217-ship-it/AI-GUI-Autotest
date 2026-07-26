from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import TestPlan as ExecutionPlan
from gui_agent.execution.orchestrator import RunOrchestrator
from gui_agent.execution.runner import RunnerConfig


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"<html><body><h1>Evidence acceptance</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


@pytest.mark.e2e
@pytest.mark.skipif(os.getenv("GUI_DOCKER_ACCEPTANCE") != "1", reason="invoked explicitly for Docker release acceptance")
def test_docker_standard_evidence_package(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://host.docker.internal:{server.server_port}"
    plan = ExecutionPlan.model_validate({
        "name": "P1-3 Docker evidence",
        "base_url": url,
        "steps": [{"action": "navigate", "target": "/"}],
    })
    config = RunnerConfig(
        artifacts_root=tmp_path / "artifacts",
        allowed_hosts=("host.docker.internal",),
        allow_private_network=True,
        max_duration_seconds=60,
        project_id="project-p13",
        environment_id="qa",
        project_snapshot={"id": "project-p13", "accountIds": ["qa-user"]},
        environment_snapshot={"id": "qa", "variableNames": ["BASE_PATH"], "secretAliases": ["LOGIN_PASSWORD"]},
        app_map_snapshot={"projectId": "project-p13", "pages": [{"id": "home"}]},
    )
    try:
        result = RunOrchestrator(runner_mode="container").run_blocking(plan, config)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["status"] == "passed", result
    assert result["runner_isolation"]["mode"] == "docker_container"
    assert result["evidence_completeness"] >= 0.95
    run_dir = config.artifacts_root / result["run_id"]
    manifest = json.loads((run_dir / result["evidence_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["runId"] == result["run_id"]
    assert manifest["missingCount"] == 0
    assert all(item["runId"] == result["run_id"] for item in manifest["items"])
    package_text = "\n".join(path.read_text(encoding="utf-8") for path in (run_dir / "evidence").glob("*.json"))
    assert "LOGIN_PASSWORD" in package_text
    assert "CANARY_SECRET_TARGET" not in package_text
