from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from gui_agent.domain.models import ActionType, Assertion, AssertionType, Locator, Step, TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"<html><body><button onclick=\"this.textContent='confirmed'\">delete record</button></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


@pytest.mark.e2e
@pytest.mark.parametrize(("approved", "expected"), [(True, "passed"), (False, "cancelled")])
def test_dangerous_fixed_action_waits_for_confirmation(tmp_path, approved: bool, expected: str) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    requests: list[tuple[int, str]] = []
    plan = ExecutionPlan(
        name="危险动作确认",
        base_url=url,
        steps=[
            Step(action=ActionType.NAVIGATE, target="/"),
            Step(action=ActionType.CLICK, locator=Locator(role="button", name="delete record"), description="delete record"),
        ],
        assertions=[Assertion(type=AssertionType.VISIBLE, locator=Locator(text="confirmed"))],
    )
    try:
        result, _ = run_plan(plan, RunnerConfig(
            artifacts_root=tmp_path / "artifacts",
            headless=True,
            allow_private_network=True,
            confirmation_callback=lambda _step, index, rule: requests.append((index, rule)) or approved,
        ))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.status.value == expected
    assert requests == [(2, "delete")]
    if approved:
        assert result.assertions[0].status.value == "passed"
    else:
        assert result.steps[-1].status.value == "skipped"
        assert result.completion_reason == "dangerous_action_rejected"
