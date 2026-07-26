from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"""<html><body><script>
        setTimeout(()=>{const node=document.createElement('div');node.id='ready';node.textContent='ready';document.body.appendChild(node)},250)
        </script></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


@pytest.mark.e2e
def test_wait_for_allows_zero_initial_matches_then_requires_unique_target(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    plan = ExecutionPlan.model_validate({
        "name": "delayed target",
        "base_url": url,
        "steps": [
            {"action": "navigate", "target": "/"},
            {"action": "wait_for", "locator": {"css": "#ready"}},
        ],
        "assertions": [{"type": "visible", "locator": {"css": "#ready"}}],
    })
    try:
        result, _ = run_plan(plan, RunnerConfig(
            artifacts_root=tmp_path / "artifacts",
            allow_private_network=True,
            timeout_ms=5_000,
        ))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.status.value == "passed", result.model_dump(mode="json")
    assert result.steps[1].stability_evidence["passed"] is True
