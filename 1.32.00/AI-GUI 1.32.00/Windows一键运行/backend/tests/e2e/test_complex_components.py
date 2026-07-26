from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"""<!doctype html><html><body>
        <select aria-label='Province'><option value='p1'>P1</option></select><select aria-label='City'><option value='c1'>C1</option></select>
        <button aria-label='Open agent'>Open</button><input placeholder='Search agent'><button role='option'>E2E_Agent</button>
        <input aria-label='Start'><input aria-label='End'><button aria-label='Next page' onclick="history.pushState({},'', '/page/2')">Next</button>
        <div data-testid='stat'>Runs: 42</div><button role='tab' aria-selected='false' onclick="this.setAttribute('aria-selected','true')">Runs tab</button>
        <button aria-label='Open upload' onclick="document.querySelector('#file').style.display='block'">Upload</button><input id='file' aria-label='Data file' type='file' style='display:none'>
        <button aria-label='Open image' onclick="document.querySelector('img').style.display='block'">Preview</button><img alt='E2E terrain preview' style='display:none'>
        <div data-testid='scroll' style='height:50px;overflow:auto'><div style='height:500px'>Long content</div></div>
        </body></html>"""
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


@pytest.mark.e2e
def test_nine_generic_component_semantics_collect_evidence(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    source = tmp_path / "E2E_data.json"; source.write_text('{"name":"E2E"}', encoding="utf-8")
    record = {"id": "file-111111111111", "fileName": source.name, "size": source.stat().st_size, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "mimeType": "application/json", "extension": ".json", "validationProfile": "json", "validationStatus": "valid", "validationErrors": [], "expectedResult": "accepted", "path": str(source)}
    components = [
        {"kind":"cascade_select","semanticTarget":"地域级联","locators":[{"role":"combobox","name":"Province"},{"role":"combobox","name":"City"}],"values":["p1","c1"]},
        {"kind":"searchable_select","semanticTarget":"智能体选择","locators":[{"role":"button","name":"Open agent"},{"placeholder":"Search agent"},{"role":"option","name":"E2E_Agent"}],"values":["E2E_Agent"]},
        {"kind":"date_time_range","semanticTarget":"运行时间","locators":[{"role":"textbox","name":"Start"},{"role":"textbox","name":"End"}],"values":["2026-07-01T00:00","2026-07-22T00:00"]},
        {"kind":"pagination","semanticTarget":"运行分页","locators":[{"role":"button","name":"Next page"}],"values":[]},
        {"kind":"statistics_card","semanticTarget":"运行统计","locators":[{"test_id":"stat"}],"values":[],"expectedText":"42"},
        {"kind":"tab","semanticTarget":"运行页签","locators":[{"role":"tab","name":"Runs tab"}],"values":[]},
        {"kind":"upload_dialog","semanticTarget":"数据上传","locators":[{"role":"button","name":"Open upload"},{"label":"Data file"}],"values":[],"fileId":record["id"]},
        {"kind":"image_preview","semanticTarget":"高程预览","locators":[{"role":"button","name":"Open image"},{"role":"img","name":"E2E terrain preview"}],"values":[],"expectedText":"E2E terrain"},
        {"kind":"local_scroll","semanticTarget":"帮助滚动","locators":[{"test_id":"scroll"}],"values":[],"scrollDeltaY":120}
    ]
    plan = ExecutionPlan.model_validate({"name":"components","base_url":url,"steps":[{"action":"navigate","target":"/"}] + [
        {"action":"component","description":item["semanticTarget"],"business_object_name":"E2E_upload" if item["kind"] == "upload_dialog" else None,"component":item} for item in components
    ]})
    try:
        result, run_dir = run_plan(plan, RunnerConfig(artifacts_root=tmp_path / "artifacts", allow_private_network=True, test_files=(record,), business_context={"status":"blocked","completeness":0.5,"blockedItems":["目标站事实待确认"]}))
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert result.status.value == "passed"
    evidence = [step.component_evidence for step in result.steps[1:]]
    assert [item["kind"] for item in evidence] == [item["kind"] for item in components]
    assert all(item["status"] == "complete" for item in evidence)
    assert evidence[6]["fileEvidence"]["contentExposedToModel"] is False
    assert result.business_context_snapshot["status"] == "blocked"
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "复杂组件语义证据" in report and "业务上下文快照" in report
