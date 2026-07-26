import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan


class FileServer:
    def __enter__(self):
        body = b"""<!doctype html><html><body>
        <form><label>Agent JSON<input name='agent' type='file'></label></form>
        <a href='/export' download>Export run</a>
        <script>document.querySelector('input').onchange=async(e)=>{
          const form=new FormData(); form.append('agent',e.target.files[0]);
          await fetch('/upload',{method:'POST',body:form});
        };</script></body></html>"""
        export = json.dumps({"runId": "E2E_Run_1", "status": "completed"}).encode()
        owner = self
        owner.upload_bytes = 0

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = export if self.path == "/export" else body
                self.send_response(200)
                self.send_header("Content-Type", "application/json" if self.path == "/export" else "text/html")
                self.send_header("Content-Length", str(len(payload)))
                if self.path == "/export":
                    self.send_header("Content-Disposition", 'attachment; filename="run.json"')
                self.end_headers(); self.wfile.write(payload)

            def do_POST(self):
                owner.upload_bytes = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(owner.upload_bytes)
                payload = b'{"id":"business-agent-7"}'
                self.send_response(201); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.export = export
        return self

    def __exit__(self, *_args):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


@pytest.mark.e2e
def test_registered_upload_and_run_scoped_download_capture_evidence(tmp_path: Path) -> None:
    source = tmp_path / "E2E_agent.json"
    source.write_text('{"name":"E2E_agent"}', encoding="utf-8")
    record = {
        "id": "file-0123456789ab", "projectId": "project-file", "fileName": source.name,
        "size": source.stat().st_size, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "mimeType": "application/json", "extension": ".json", "validationProfile": "json",
        "validationStatus": "valid", "validationErrors": [], "expectedResult": "accepted", "path": str(source),
    }
    with FileServer() as server:
        plan = ExecutionPlan.model_validate({
            "name": "File transfer", "base_url": server.url, "steps": [
                {"action": "navigate", "target": "/"},
                {"action": "upload", "locator": {"label": "Agent JSON"}, "file_id": record["id"], "business_object_name": "E2E_Agent"},
                {"action": "download", "locator": {"role": "link", "name": "Export run"}, "business_object_name": "E2E_Run_1", "download_validation": {"extension": ".json", "format": "json", "requiredJsonKeys": ["runId", "status"]}},
            ],
        })
        result, run_dir = run_plan(plan, RunnerConfig(
            artifacts_root=tmp_path / "artifacts", allow_private_network=True,
            project_id="project-file", environment_id="environment-qa", test_files=(record,),
        ))

    assert result.status.value == "passed"
    assert server.upload_bytes > source.stat().st_size
    upload = result.steps[1].file_evidence
    assert upload["generatedBusinessId"] == "business-agent-7"
    assert upload["contentExposedToModel"] is False
    download = result.steps[2].file_evidence
    assert download["sha256"] == hashlib.sha256(server.export).hexdigest()
    assert download["formatValidation"]["topLevelType"] == "dict"
    assert download["responseStatus"] == 200
    assert download["responseHeaders"]["content-type"] == "application/json"
    assert (run_dir / download["artifact"]).read_bytes() == server.export
    assert "上传／下载证据" in (run_dir / "report.md").read_text(encoding="utf-8")
