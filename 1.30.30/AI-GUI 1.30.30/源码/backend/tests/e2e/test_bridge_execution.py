from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import ActionType, ExecutionMode, Locator, RelativePosition, StabilityLevel, Step, TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan


class PageServer:
    def __init__(self, body: str) -> None:
        self.body = body.encode()

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


BRIDGE_PAGE = """<!doctype html><html><body>
<canvas id="map" width="400" height="200" style="border:1px solid"></canvas>
<p id="state">Waiting</p>
<script>
let selected = null;
const canvas = document.querySelector('#map');
canvas.addEventListener('click', () => { selected = 'entity.alpha'; document.querySelector('#state').textContent = 'Selected'; });
window.CUSTOM_TEST_BRIDGE = {
  version: '1',
  getSceneState: () => ({ camera: { heading: 0 }, layers: [{ id: 'base', show: true }], loading: false, tilesLoaded: true }),
  listVisibleTargets: () => [{ id: 'entity.alpha', type: 'entity', label: 'Alpha' }],
  getTargetScreenPosition: () => { const box = canvas.getBoundingClientRect(); return { x: box.x + box.width / 2, y: box.y + box.height / 2 }; },
  getSelectedTargetId: () => selected,
  waitForSceneReady: async () => ({ ready: true }),
};
</script></body></html>"""


def bridge_plan(base_url: str) -> ExecutionPlan:
    return ExecutionPlan(
        name="Bridge semantic click",
        base_url=base_url,
        steps=[
            Step(action=ActionType.NAVIGATE, target="/"),
            Step(
                action=ActionType.BRIDGE_CLICK,
                execution_mode=ExecutionMode.APP_BRIDGE,
                bridge_target_id="entity.alpha",
                description="Select entity Alpha",
            ),
        ],
    )


@pytest.mark.e2e
@pytest.mark.parametrize("adapter_name", ["generic", "cesium"])
def test_bridge_contract_waits_executes_and_verifies_semantic_state(tmp_path: Path, adapter_name: str) -> None:
    with PageServer(BRIDGE_PAGE) as server:
        result, run_dir = run_plan(
            bridge_plan(server.url),
            RunnerConfig(
                artifacts_root=tmp_path / adapter_name,
                allow_private_network=True,
                app_bridge_enabled=True,
                app_bridge_global_name="CUSTOM_TEST_BRIDGE",
                app_bridge_adapter=adapter_name,
            ),
        )

    step = result.steps[1]
    assert result.status.value == "passed"
    assert step.coordinate_source == "app_bridge:entity.alpha"
    assert step.stability_evidence == {
        "checked": True,
        "passed": True,
        "mode": "app_bridge",
        "sceneReady": True,
        "targetId": "entity.alpha",
        "adapter": adapter_name,
    }
    assert step.app_bridge_result["selectedTargetAfter"] == "entity.alpha"
    assert step.app_bridge_result["semanticStateVerified"] is True
    assert step.app_bridge_result["sceneBefore"]["tilesLoaded"] is True
    assert step.canvas_evidence["collectionStatus"] == "complete"
    assert step.canvas_evidence["sceneBefore"]["tilesLoaded"] is True
    assert step.canvas_evidence["sceneAfter"]["tilesLoaded"] is True
    assert step.canvas_evidence["selectedTargetAfter"] == "entity.alpha"
    assert step.canvas_evidence["beforeScreenshot"] and step.canvas_evidence["afterScreenshot"]
    assert result.findings == []
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"type": "action_stability_checked"' in events
    report_md = (run_dir / "report.md").read_text(encoding="utf-8")
    report_html = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "Canvas／Bridge 证据" in report_md and '"selectedTargetAfter": "entity.alpha"' in report_md
    assert "Canvas／Bridge 证据" in report_html and "selectedTargetAfter" in report_html


@pytest.mark.e2e
def test_bridge_action_is_rejected_when_environment_did_not_enable_bridge(tmp_path: Path) -> None:
    with PageServer(BRIDGE_PAGE) as server:
        result, _ = run_plan(
            bridge_plan(server.url),
            RunnerConfig(artifacts_root=tmp_path, allow_private_network=True),
        )

    assert result.status.value == "error"
    assert "未启用 App Bridge" in result.steps[1].error_message
    assert result.steps[1].stability_evidence["passed"] is False
    assert result.steps[1].canvas_evidence["collectionStatus"] == "failed"
    assert result.steps[1].canvas_evidence["failurePhase"] == "stability"


@pytest.mark.e2e
def test_dom_action_is_rejected_when_target_is_occluded(tmp_path: Path) -> None:
    page = """<!doctype html><html><body>
    <button id='target'>Run</button><div style='position:absolute;left:0;top:0;width:300px;height:200px;background:white;z-index:2'></div>
    </body></html>"""
    with PageServer(page) as server:
        plan = ExecutionPlan(
            name="Occlusion check",
            base_url=server.url,
            steps=[
                Step(action=ActionType.NAVIGATE, target="/"),
                Step(action=ActionType.CLICK, locator={"css": "#target"}),
            ],
        )
        result, _ = run_plan(
            plan,
            RunnerConfig(artifacts_root=tmp_path, allow_private_network=True),
        )

    assert result.status.value == "error"
    assert "unoccluded" in result.steps[1].error_message
    assert result.steps[1].stability_evidence["passed"] is False


@pytest.mark.e2e
def test_bridge_rejects_incomplete_v1_contract_before_mouse_action(tmp_path: Path) -> None:
    incomplete = BRIDGE_PAGE.replace("waitForSceneReady: async", "waitForSceneReadyMissing: async")
    with PageServer(incomplete) as server:
        result, _ = run_plan(
            bridge_plan(server.url),
            RunnerConfig(
                artifacts_root=tmp_path,
                allow_private_network=True,
                app_bridge_enabled=True,
                app_bridge_global_name="CUSTOM_TEST_BRIDGE",
            ),
        )

    assert result.status.value == "error"
    assert "waitForSceneReady" in result.steps[1].error_message
    assert [finding.category for finding in result.findings] == ["bridge_contract_error"]
    assert any("waitForSceneReady" in fact for fact in result.findings[0].facts)


@pytest.mark.e2e
def test_visual_canvas_action_collects_bridge_state_before_and_after(tmp_path: Path) -> None:
    with PageServer(BRIDGE_PAGE) as server:
        plan = ExecutionPlan(
            name="Visual action with Bridge evidence",
            base_url=server.url,
            steps=[
                Step(action=ActionType.NAVIGATE, target="/"),
                Step(
                    action=ActionType.VISUAL_CLICK,
                    locator=Locator(css="#map"),
                    execution_mode=ExecutionMode.VISUAL,
                    stability_level=StabilityLevel.C,
                    visual_target="entity.alpha",
                    relative_position=RelativePosition(xRatio=0.5, yRatio=0.5),
                ),
            ],
        )
        result, _ = run_plan(
            plan,
            RunnerConfig(
                artifacts_root=tmp_path,
                allow_private_network=True,
                app_bridge_enabled=True,
                app_bridge_global_name="CUSTOM_TEST_BRIDGE",
            ),
        )

    evidence = result.steps[1].canvas_evidence
    assert result.status.value == "passed"
    assert evidence["mode"] == "visual"
    assert evidence["bridgeAvailable"] is True
    assert evidence["bridgeBefore"]["selectedTargetId"] is None
    assert evidence["bridgeAfter"]["selectedTargetId"] == "entity.alpha"
    assert evidence["selectedTargetChanged"] is True
    assert evidence["beforeScreenshot"] and evidence["afterScreenshot"]
