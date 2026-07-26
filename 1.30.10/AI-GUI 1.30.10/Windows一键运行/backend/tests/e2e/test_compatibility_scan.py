import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from gui_agent.demo.server import DemoServer, find_available_port
from gui_agent.onboarding.models import ProjectConfig
from gui_agent.onboarding.scanner import scan_project


class ProbeServer:
    posts = 0

    def __enter__(self):
        body = b"""<!doctype html><html><head><title>Probe App</title></head><body>
        <nav><button aria-controls='detail'>Open details</button><button aria-controls='save'>Save</button></nav>
        <main><div role='tablist'><button role='tab' onclick="document.querySelector('#panel').textContent='Tab opened'">Overview</button></div>
        <section id='panel'>Initial</section><div id='detail' role='dialog' hidden>Details dialog</div>
        <script>
        document.querySelector('[aria-controls=detail]').onclick=()=>document.querySelector('#detail').hidden=false;
        document.querySelector('[aria-controls=save]').onclick=()=>fetch('/save',{method:'POST'});
        </script></body></html>"""
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def do_POST(self):
                owner.posts += 1; self.send_response(204); self.end_headers()

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_args):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


@pytest.mark.e2e
def test_read_only_scan_profiles_login_and_spa_without_submitting() -> None:
    with DemoServer(port=find_available_port()) as demo:
        project = ProjectConfig(
            id="project-scan", name="历史 CRM", baseUrl=demo.url,
            allowedHosts=["127.0.0.1"], forbiddenActions=["删除数据"],
            allowPrivateNetwork=True, onboardingLevel="L0",
        )
        report = scan_project(project, headless=True, timeout_ms=10_000)

    assert report.scanned_pages
    assert report.scanned_pages[0]["pageType"] == "登录页"
    assert report.recommended_onboarding_level == "L1"
    assert any("登录" in item for item in report.authentication_signals)
    assert report.candidate_locators["labels"] >= 2
    assert report.stable_areas
    assert report.suggested_scenarios
    assert report.scan_mode == "read_only"
    assert report.app_map["version"] == "1"
    assert report.app_map["pages"][0]["id"] == "page-1"
    assert "inputs" in report.app_map["pages"][0]
    assert not any(token in " ".join(report.failed_requests).lower() for token in ("delete", "publish", "checkout"))


@pytest.mark.e2e
def test_low_risk_scan_opens_safe_ui_and_never_clicks_save() -> None:
    with ProbeServer() as server:
        project = ProjectConfig(
            id="project-probe", name="Probe", baseUrl=server.url,
            allowedHosts=["127.0.0.1"], allowPrivateNetwork=True,
        )
        report = scan_project(project, headless=True, timeout_ms=10_000, scan_mode="low_risk")

    probes = report.app_map["pages"][0]["probes"]
    assert report.scan_mode == "low_risk"
    assert any(item.get("name") == "Open details" and item.get("map", {}).get("dialogs") for item in probes)
    assert any(item.get("name") == "Overview" for item in probes)
    assert not any(item.get("name") == "Save" for item in probes)
    assert server.posts == 0
