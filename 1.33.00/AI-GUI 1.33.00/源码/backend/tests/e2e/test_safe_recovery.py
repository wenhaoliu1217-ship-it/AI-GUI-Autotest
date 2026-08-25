import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from gui_agent.domain.models import Step, TestPlan as DomainTestPlan
from gui_agent.execution.recovery import execute_with_recovery
from gui_agent.execution.runner import RunnerConfig, _execute_step, run_plan
from gui_agent.security.policy import DomainPolicy
from gui_agent.security.redaction import Redactor


class RecoveryHandler(BaseHTTPRequestHandler):
    counts = {"429": 0, "500": 0, "write": 0}

    def do_GET(self) -> None:
        if self.path == "/flaky-429":
            self.counts["429"] += 1
            if self.counts["429"] == 1:
                return self._send(429, b"rate limited")
            return self._send(200, b"<title>Recovered 429</title>", "text/html")
        if self.path == "/flaky-500":
            self.counts["500"] += 1
            if self.counts["500"] == 1:
                return self._send(503, b"temporary failure")
            return self._send(200, b"<title>Recovered 500</title>", "text/html")
        if self.path == "/side-effect":
            return self._send(
                200,
                b'<button id="submit" onclick="location.href=\'/write\'">Submit</button>',
                "text/html",
            )
        if self.path == "/write":
            self.counts["write"] += 1
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        self._send(404, b"missing")

    def _send(self, status: int, body: bytes, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


@pytest.fixture
def recovery_server():
    RecoveryHandler.counts = {"429": 0, "500": 0, "write": 0}
    server = ThreadingHTTPServer(("127.0.0.1", 0), RecoveryHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.e2e
@pytest.mark.parametrize(("path", "failure_class", "counter"), [
    ("/flaky-429", "http_429", "429"),
    ("/flaky-500", "http_5xx", "500"),
])
def test_real_navigation_recovers_429_and_5xx(
    tmp_path, recovery_server, path, failure_class, counter
) -> None:
    result, _ = run_plan(
        DomainTestPlan(
            name="safe read recovery", base_url=recovery_server,
            steps=[Step(action="navigate", target=path, commerce={"action": "browse"})],
        ),
        RunnerConfig(
            artifacts_root=tmp_path / "artifacts", headless=True,
            allow_private_network=True, timeout_ms=5_000, commerce_enabled=True,
        ),
    )

    assert result.status.value == "passed"
    evidence = result.steps[0].recovery_evidence
    assert evidence and evidence["retried"] is True
    assert evidence["attempts"][0]["failureClass"] == failure_class
    assert RecoveryHandler.counts[counter] == 2
    assert result.commerce_summary["releaseGate"]["passed"] is True


@pytest.mark.e2e
def test_unknown_write_response_is_confirmed_without_second_click(recovery_server) -> None:
    step = Step(
        action="click", locator={"css": "#submit"}, commerce={
            "action": "submit_order",
            "targetKind": "orderId",
            "targetRef": "resource:E2E_RECOVERY_ORDER",
            "beforeState": "draft",
            "idempotencyKeyRef": "secret:E2E_RECOVERY_KEY",
            "e2eOwned": True,
            "stateProbe": {
                "domain": "order", "url": "/state", "jsonPath": "state",
                "expectedState": "pending_payment",
            },
        },
    )
    policy = DomainPolicy(recovery_server, allow_private_network=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{recovery_server}/side-effect")

        def click_with_unknown_response():
            detail = _execute_step(page, step, recovery_server, policy, Redactor())
            page.wait_for_timeout(100)
            raise PlaywrightError("net::ERR_CONNECTION_RESET after request dispatch")

        _, evidence = execute_with_recovery(
            step,
            click_with_unknown_response,
            wait=lambda _milliseconds: None,
            probe=lambda: {"verified": True, "state": "pending_payment"},
        )
        browser.close()

    assert RecoveryHandler.counts["write"] == 1
    assert evidence["decision"] == "original_action_confirmed_applied"
    assert evidence["retried"] is False
