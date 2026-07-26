from __future__ import annotations

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
        body = b"""<html><body><div data-testid='stat'>Runs: 42</div>
        <button role='tab' aria-selected='false' onclick="this.setAttribute('aria-selected','true')">Runs tab</button>
        <div data-testid='scroll' style='height:40px;overflow:auto'><div style='height:400px'>content</div></div></body></html>"""
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


@pytest.mark.e2e
@pytest.mark.skipif(os.getenv("GUI_DOCKER_ACCEPTANCE") != "1", reason="invoked explicitly for Docker release acceptance")
def test_docker_project_component_adapters_and_context_snapshot(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f"http://host.docker.internal:{server.server_port}"
    components = [
        ("run.stat", {"kind":"statistics_card","semanticTarget":"运行统计","locators":[{"test_id":"stat"}],"values":[],"expectedText":"42"}),
        ("run.tab", {"kind":"tab","semanticTarget":"运行页签","locators":[{"role":"tab","name":"Runs tab"}],"values":[]}),
        ("help.scroll", {"kind":"local_scroll","semanticTarget":"帮助滚动","locators":[{"test_id":"scroll"}],"values":[],"scrollDeltaY":120}),
    ]
    plan = ExecutionPlan.model_validate({"name":"P1-1 Docker","base_url":url,"steps":[{"action":"navigate","target":"/"}] + [
        {"action":"component","component_adapter_id":adapter_id,"component":action} for adapter_id, action in components
    ]})
    adapters = tuple({"id": adapter_id, "module": "help" if adapter_id.startswith("help") else "run", "page": "验收页", "action": action, "status": "configured", "source": "本地 Docker 验收页面", "blockedReason": ""} for adapter_id, action in components)
    config = RunnerConfig(
        artifacts_root=tmp_path / "artifacts", allowed_hosts=("host.docker.internal",), allow_private_network=True,
        max_duration_seconds=60, project_id="project-p11", component_adapters=adapters,
        business_context={"status":"blocked","confirmedCount":3,"totalCount":4,"completeness":0.75,"blockedItems":["企业目标站选择器待确认"]},
    )
    try:
        result = RunOrchestrator(runner_mode="container").run_blocking(plan, config)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert result["status"] == "passed", result
    assert result["runner_isolation"]["mode"] == "docker_container"
    assert [item["component_evidence"]["kind"] for item in result["steps"][1:]] == ["statistics_card", "tab", "local_scroll"]
    assert result["business_context_snapshot"]["blockedItems"] == ["企业目标站选择器待确认"]
