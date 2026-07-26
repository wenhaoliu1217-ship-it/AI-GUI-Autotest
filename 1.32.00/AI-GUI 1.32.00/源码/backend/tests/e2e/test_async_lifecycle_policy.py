from __future__ import annotations

import base64
import hashlib
import json
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan


class WebSocketHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            request += self.request.recv(4096)
        headers = request.decode("latin-1").split("\r\n")
        key = next(line.split(":", 1)[1].strip() for line in headers if line.lower().startswith("sec-websocket-key:"))
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.request.sendall(
            ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode()
        )
        payload = json.dumps({"businessId": "E2E_job_1", "state": "running", "token": "must-not-appear"}).encode()
        self.request.sendall(bytes([0x81, len(payload)]) + payload)


class HttpHandler(BaseHTTPRequestHandler):
    ws_port = 0

    def do_GET(self) -> None:
        body = f"""<!doctype html><html><body>
        <div id='object'>E2E_job_1</div><div data-testid='state'>queued</div>
        <button aria-label='delete E2E job' onclick="document.querySelector('#object').remove()">delete</button>
        <script>
        const socket = new WebSocket('ws://127.0.0.1:{self.ws_port}/events');
        socket.onmessage = (event) => {{
          const message = JSON.parse(event.data);
          document.querySelector('[data-testid=state]').textContent = message.state;
          setTimeout(() => document.querySelector('[data-testid=state]').textContent = 'done', 250);
        }};
        </script></body></html>""".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


@pytest.mark.e2e
def test_websocket_state_machine_and_reverse_cleanup(tmp_path: Path) -> None:
    websocket = socketserver.ThreadingTCPServer(("127.0.0.1", 0), WebSocketHandler)
    HttpHandler.ws_port = websocket.server_address[1]
    http = ThreadingHTTPServer(("127.0.0.1", 0), HttpHandler)
    threads = [Thread(target=websocket.serve_forever, daemon=True), Thread(target=http.serve_forever, daemon=True)]
    for thread in threads:
        thread.start()
    url = f"http://127.0.0.1:{http.server_port}"
    plan = ExecutionPlan.model_validate({
        "name": "async lifecycle", "base_url": url, "steps": [
            {"action": "navigate", "target": "/"},
            {"action": "wait_for_state", "locator": {"test_id": "state"}, "state_machine_id": "job", "business_object_id": "E2E_job_1"},
        ],
    })
    machine = {
        "id": "job", "states": ["queued", "running", "done", "failed"],
        "terminalStates": ["done"], "failureStates": ["failed"],
        "transitions": {"queued": ["running", "failed"], "running": ["done", "failed"]},
        "pollingIntervalMs": 100, "timeoutMs": 5000,
    }
    cleanup_step = {
        "action": "click", "locator": {"role": "button", "name": "delete E2E job"},
        "description": "delete E2E_job_1", "action_category": "delete", "object_type": "job",
        "business_object_name": "E2E_job_1", "business_object_id": "E2E_job_1", "cleanup_required": True,
    }
    try:
        result, run_dir = run_plan(plan, RunnerConfig(
            artifacts_root=tmp_path / "artifacts", allow_private_network=True,
            async_state_machines=(machine,),
            side_effect_policies=({"id": "delete", "actionCategory": "delete", "objectType": "job", "namePattern": "^E2E_", "decision": "confirm", "rollbackRule": "verify absent"},),
            business_objects=({"key": "job", "objectType": "job", "name": "E2E_job_1", "dependencies": [], "reuse": False, "cleanupStep": cleanup_step, "verificationLocator": {"text": "E2E_job_1"}, "manualFallback": "remove E2E_job_1"},),
            confirmation_callback=lambda *_: True,
        ))
    finally:
        http.shutdown(); websocket.shutdown(); http.server_close(); websocket.server_close()
        for thread in threads:
            thread.join(timeout=2)

    assert result.status.value == "passed"
    assert result.steps[1].async_evidence["classification"] == "success_terminal"
    assert any(item["kind"] == "frame" and item["businessFields"]["businessId"] == "E2E_job_1" for item in result.websocket_timeline)
    assert "must-not-appear" not in json.dumps(result.websocket_timeline)
    assert result.cleanup_report["status"] == "passed"
    assert result.cleanup_report["objects"][0]["status"] == "cleaned"
    assert (run_dir / "cleanup-report.json").is_file()
    assert "异步与 WebSocket 证据" in (run_dir / "report.md").read_text(encoding="utf-8")
