from __future__ import annotations

import base64
import hashlib
import json
import os
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import TestPlan as ExecutionPlan
from gui_agent.execution.orchestrator import RunOrchestrator
from gui_agent.execution.runner import RunnerConfig


class WsHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            request += self.request.recv(4096)
        key = next(line.split(":", 1)[1].strip() for line in request.decode("latin-1").split("\r\n") if line.lower().startswith("sec-websocket-key:"))
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.request.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        payload = json.dumps({"businessId": "E2E_docker_job", "state": "running"}).encode()
        self.request.sendall(bytes([0x81, len(payload)]) + payload)


class HttpHandler(BaseHTTPRequestHandler):
    ws_port = 0

    def do_GET(self) -> None:
        body = f"""<html><body><div id='object'>E2E_docker_job</div><div data-testid='state'>queued</div>
        <button aria-label='remove E2E job' onclick="document.querySelector('#object').remove()">remove</button>
        <script>const s=new WebSocket('ws://host.docker.internal:{self.ws_port}/events');s.onmessage=e=>{{const m=JSON.parse(e.data);document.querySelector('[data-testid=state]').textContent=m.state;setTimeout(()=>document.querySelector('[data-testid=state]').textContent='done',250);}};</script></body></html>""".encode()
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


@pytest.mark.e2e
@pytest.mark.skipif(os.getenv("GUI_DOCKER_ACCEPTANCE") != "1", reason="invoked explicitly for Docker release acceptance")
def test_docker_p03_acceptance(tmp_path: Path) -> None:
    websocket = socketserver.ThreadingTCPServer(("0.0.0.0", 0), WsHandler)
    HttpHandler.ws_port = websocket.server_address[1]
    http = ThreadingHTTPServer(("0.0.0.0", 0), HttpHandler)
    threads = [Thread(target=websocket.serve_forever, daemon=True), Thread(target=http.serve_forever, daemon=True)]
    for thread in threads:
        thread.start()
    url = f"http://host.docker.internal:{http.server_port}"
    plan = ExecutionPlan.model_validate({"name": "P0-3 Docker acceptance", "base_url": url, "steps": [
        {"action": "navigate", "target": "/"},
        {"action": "wait_for_state", "locator": {"test_id": "state"}, "state_machine_id": "job", "business_object_id": "E2E_docker_job"},
    ]})
    config = RunnerConfig(
        artifacts_root=tmp_path / "artifacts", headless=True, allowed_hosts=("host.docker.internal",),
        allow_private_network=True, max_duration_seconds=60,
        async_state_machines=({"id": "job", "states": ["queued", "running", "done", "failed"], "terminalStates": ["done"], "failureStates": ["failed"], "transitions": {"queued": ["running", "failed"], "running": ["done", "failed"]}, "pollingIntervalMs": 100, "timeoutMs": 5000},),
        side_effect_policies=({"id": "cleanup", "actionCategory": "cleanup", "objectType": "job", "namePattern": "^E2E_", "decision": "allow", "rollbackRule": "verify absent"},),
        business_objects=({"key": "job", "objectType": "job", "name": "E2E_docker_job", "dependencies": [], "reuse": False, "cleanupStep": {"action": "click", "locator": {"role": "button", "name": "remove E2E job"}, "description": "remove E2E object", "action_category": "cleanup", "object_type": "job", "business_object_name": "E2E_docker_job", "cleanup_required": True}, "verificationLocator": {"text": "E2E_docker_job"}, "manualFallback": "remove object manually"},),
    )
    try:
        result = RunOrchestrator(runner_mode="container").run_blocking(plan, config)
    finally:
        http.shutdown(); websocket.shutdown(); http.server_close(); websocket.server_close()
        for thread in threads:
            thread.join(timeout=2)

    assert result["status"] == "passed", result
    assert result["runner_isolation"]["mode"] == "docker_container"
    assert result["steps"][1]["async_evidence"]["classification"] == "success_terminal"
    assert result["cleanup_report"]["status"] == "passed"
    assert any(item["kind"] == "frame" for item in result["websocket_timeline"])
