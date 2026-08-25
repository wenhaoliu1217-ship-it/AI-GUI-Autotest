from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import ActionType, Locator, Step, TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan


class PageServer:
    def __init__(self, body: str) -> None:
        self.body = body.encode("utf-8")

    def __enter__(self):
        body = self.body

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


GENERIC_PAGE = """<!doctype html><html><body>
<main><h1>Generic Web Contract</h1><p id="status">ready</p>
<button id="complete">Complete</button>
<script>document.querySelector('#complete').addEventListener('click', () => {
  document.querySelector('#status').textContent = 'done';
});</script></main></body></html>"""

SECOND_GENERIC_PAGE = """<!doctype html><html><body>
<section><h1>Ordinary Dashboard Contract</h1><p id="status">idle</p>
<button id="complete">Mark ready</button>
<script>document.querySelector('#complete').addEventListener('click', () => {
  document.querySelector('#status').textContent = 'ready';
});</script></section></body></html>"""


@pytest.mark.parametrize("page_body", [GENERIC_PAGE, SECOND_GENERIC_PAGE], ids=["generic-web", "ordinary-dashboard"])
@pytest.mark.e2e
def test_generic_runner_keeps_native_actions_and_records_real_browsergym_evidence(
    tmp_path: Path, page_body: str,
) -> None:
    runtime_python = os.getenv("GUI_AGENT_OPENSOURCE_RUNTIME_PYTHON", "").strip()
    if not runtime_python or not Path(runtime_python).is_file():
        pytest.skip("GUI_AGENT_OPENSOURCE_RUNTIME_PYTHON is not configured")

    with PageServer(page_body) as server:
        plan = ExecutionPlan(
            name="Generic web upstream evaluation",
            base_url=server.url,
            steps=[
                Step(action=ActionType.NAVIGATE, target="/"),
                Step(action=ActionType.CLICK, locator=Locator(css="#complete")),
            ],
        )
        result, run_dir = run_plan(
            plan,
            RunnerConfig(
                artifacts_root=tmp_path / "artifacts",
                allowed_hosts=("127.0.0.1",),
                allow_private_network=True,
                open_source_evaluation_provider="browsergym",
            ),
        )

    assert result.status.value == "passed"
    assert result.open_source_runtime_evaluation is not None
    assert result.open_source_runtime_evaluation["status"] == "ready"
    assert result.open_source_runtime_evaluation["upstreamRuntimeStarted"] is True
    assert result.open_source_runtime_evaluation["actionPolicy"] == "local_fixture_only"
    assert (run_dir / "evaluations" / "open-source-runtime.json").is_file()
    assert "开源上游评测证据" in (run_dir / "report.md").read_text(encoding="utf-8")
