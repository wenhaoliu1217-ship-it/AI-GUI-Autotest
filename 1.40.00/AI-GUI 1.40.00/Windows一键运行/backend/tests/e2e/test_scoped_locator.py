from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import ActionType, Locator, Step, TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan


class RowServer:
    def __enter__(self):
        body = b"""<!doctype html><html><body>
        <div role='row' data-object-id='agent-1'>E2E_Alpha <button>Delete</button></div>
        <div role='row' data-object-id='agent-2'>E2E_Beta <button onclick="this.dataset.clicked='yes'">Delete</button></div>
        </body></html>"""

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_args):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


@pytest.mark.e2e
def test_scoped_locator_selects_named_business_row_and_unscoped_duplicate_fails(tmp_path: Path) -> None:
    with RowServer() as server:
        scoped = ExecutionPlan(name="Scoped", base_url=server.url, steps=[
            Step(action=ActionType.NAVIGATE, target="/"),
            Step(action=ActionType.CLICK, locator=Locator(
                role="button", name="Delete",
                scope={
                    "kind": "row",
                    "locator": {"attribute": {"name": "data-object-id", "value": "agent-2"}},
                    "identity": "E2E_Beta",
                },
            )),
        ])
        scoped_result, _ = run_plan(scoped, RunnerConfig(
            artifacts_root=tmp_path / "scoped", allow_private_network=True,
            confirmation_callback=lambda *_args: True,
        ))
        duplicate = ExecutionPlan(name="Duplicate", base_url=server.url, steps=[
            Step(action=ActionType.NAVIGATE, target="/"),
            Step(action=ActionType.CLICK, locator=Locator(role="button", name="Delete")),
        ])
        duplicate_result, _ = run_plan(duplicate, RunnerConfig(
            artifacts_root=tmp_path / "duplicate", allow_private_network=True,
            confirmation_callback=lambda *_args: True,
        ))

    assert scoped_result.status.value == "passed"
    assert duplicate_result.status.value == "error"
    assert "动作目标必须唯一，实际匹配 2 个" in duplicate_result.steps[1].error_message
