from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan


SCENE = """{
  camera:{longitude:116.3,latitude:39.9,height:1000,heading:0,pitch:-1,roll:0},
  layers:[{id:'base',name:'Base',visible:true}],entityCount:2,selectedEntityId:'entity-1',
  pathPoints:[1,2,3],pois:[1],fences:[1,2],drawings:[{type:'polygon'}],
  tilesLoaded:true,loading:false,webglError:null
}"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = f"""<!doctype html><html><body><button aria-label='Clear drawing' onclick="document.querySelector('#state').textContent='cleared'">Clear</button>
        <canvas id='map' width='400' height='240' style='border:1px solid'></canvas><div id='state'>ready</div>
        <script>
        const canvas=document.querySelector('#map');let selected='entity-1';
        canvas.addEventListener('wheel',e=>{{e.preventDefault();document.querySelector('#state').textContent='zoomed';}});
        canvas.addEventListener('click',()=>document.querySelector('#state').textContent='drawn');
        canvas.addEventListener('mouseup',()=>document.querySelector('#state').textContent='rectangle');
        window.GAE_BRIDGE={{version:'1',getSceneState:()=>({SCENE}),listVisibleTargets:()=>[{{id:'entity-1'}}],getTargetScreenPosition:()=>({{x:200,y:120}}),getSelectedTargetId:()=>selected,waitForSceneReady:async()=>({{ready:true}})}};
        </script></body></html>""".encode()
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


def server():
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=instance.serve_forever, daemon=True); thread.start()
    return instance, thread, f"http://127.0.0.1:{instance.server_port}"


@pytest.mark.e2e
def test_canvas_relative_gestures_record_geometry_and_never_claim_a_stability(tmp_path: Path) -> None:
    instance, thread, url = server()
    plan = ExecutionPlan.model_validate({"name":"gestures","base_url":url,"steps":[{"action":"navigate","target":"/"},
        {"action":"visual_zoom","execution_mode":"visual","stability_level":"C","stability_reason":"运行时相对投影","visual_target":"地图缩放","canvas_region_locator":{"css":"#map"},"relative_position":{"xRatio":.5,"yRatio":.5},"zoom_delta":-300},
        {"action":"visual_draw_polygon","execution_mode":"visual","stability_level":"C","stability_reason":"运行时相对投影","visual_target":"围栏","canvas_region_locator":{"css":"#map"},"visual_points":[{"xRatio":.2,"yRatio":.2},{"xRatio":.8,"yRatio":.2},{"xRatio":.5,"yRatio":.8}]},
        {"action":"visual_draw_rectangle","execution_mode":"visual","stability_level":"C","stability_reason":"运行时相对投影","visual_target":"矩形","canvas_region_locator":{"css":"#map"},"visual_points":[{"xRatio":.25,"yRatio":.25},{"xRatio":.75,"yRatio":.75}]},
        {"action":"visual_clear","execution_mode":"visual","stability_level":"B","stability_reason":"稳定清除控件","visual_target":"清除绘制","canvas_region_locator":{"css":"#map"},"locator":{"role":"button","name":"Clear drawing"}}
    ]})
    try:
        result, _ = run_plan(plan, RunnerConfig(artifacts_root=tmp_path / "artifacts", allow_private_network=True))
    finally:
        instance.shutdown(); instance.server_close(); thread.join(timeout=2)
    assert result.status.value == "passed"
    gestures = [item.canvas_evidence["gestureEvidence"] for item in result.steps[1:]]
    assert [item["action"] for item in gestures] == ["visual_zoom", "visual_draw_polygon", "visual_draw_rectangle", "visual_clear"]
    assert len(gestures[1]["points"]) == 3 and len(gestures[2]["points"]) == 2
    assert all(item["coordinatePolicy"] == "canvas_relative_runtime_projection" for item in gestures)
    assert all(step.stability_level in {"B", "C"} for step in result.steps[1:])


@pytest.mark.e2e
def test_gaealavic_normalized_bridge_semantic_assertions(tmp_path: Path) -> None:
    instance, thread, url = server()
    assertions = [
        {"type":"canvas_layer_visible","expected":"base"}, {"type":"canvas_camera_equals","expected":"{\"longitude\":116.3}","tolerance":.001},
        {"type":"canvas_entity_count","count":2}, {"type":"canvas_selected_entity","expected":"entity-1"},
        {"type":"canvas_path_point_count","count":3}, {"type":"canvas_poi_count","count":1},
        {"type":"canvas_fence_count","count":2}, {"type":"canvas_drawing_count","count":1},
        {"type":"canvas_tiles_loaded"}, {"type":"canvas_webgl_no_error"},
    ]
    plan = ExecutionPlan.model_validate({"name":"semantic","base_url":url,"steps":[{"action":"navigate","target":"/"}],"assertions":assertions})
    try:
        result, run_dir = run_plan(plan, RunnerConfig(
            artifacts_root=tmp_path / "artifacts", allow_private_network=True,
            app_bridge_enabled=True, app_bridge_global_name="GAE_BRIDGE", app_bridge_adapter="gaealavic_cesium",
        ))
    finally:
        instance.shutdown(); instance.server_close(); thread.join(timeout=2)
    assert result.status.value == "passed"
    assert len(result.assertions) == 10 and all(item.semantic_evidence for item in result.assertions)
    assert all(item.semantic_evidence["adapter"] == "gaealavic_cesium" for item in result.assertions)
    assert "Canvas 业务语义证据" in (run_dir / "report.md").read_text(encoding="utf-8")
