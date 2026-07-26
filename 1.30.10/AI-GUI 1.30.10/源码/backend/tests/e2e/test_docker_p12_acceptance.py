from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from gui_agent.domain.models import TestPlan as ExecutionPlan
from gui_agent.execution.orchestrator import RunOrchestrator
from gui_agent.execution.runner import RunnerConfig


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"""<html><body><canvas id='map' width='400' height='240' style='border:1px solid'></canvas><script>
        window.GAE_BRIDGE={version:'1',getSceneState:()=>({camera:{longitude:1,latitude:2,height:3,heading:0,pitch:0,roll:0},layers:[{id:'base',name:'Base',visible:true}],entityCount:1,selectedEntityId:'e1',pathPoints:[1,2,3],pois:[1],fences:[1],drawings:[1],tilesLoaded:true,loading:false,webglError:null}),listVisibleTargets:()=>[{id:'e1'}],getTargetScreenPosition:()=>({x:200,y:120}),getSelectedTargetId:()=>'e1',waitForSceneReady:async()=>({ready:true})};
        </script></body></html>"""
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *_args) -> None: pass


@pytest.mark.e2e
@pytest.mark.skipif(os.getenv("GUI_DOCKER_ACCEPTANCE") != "1", reason="invoked explicitly for Docker release acceptance")
def test_docker_canvas_gesture_and_semantic_assertion(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler); thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f"http://host.docker.internal:{server.server_port}"
    plan = ExecutionPlan.model_validate({"name":"P1-2 Docker","base_url":url,"steps":[{"action":"navigate","target":"/"},{"action":"visual_draw_polygon","execution_mode":"visual","stability_level":"C","stability_reason":"运行时相对投影","visual_target":"围栏","canvas_region_locator":{"css":"#map"},"visual_points":[{"xRatio":.2,"yRatio":.2},{"xRatio":.8,"yRatio":.2},{"xRatio":.5,"yRatio":.8}]}],"assertions":[{"type":"canvas_layer_visible","expected":"base"},{"type":"canvas_path_point_count","count":3},{"type":"canvas_tiles_loaded"},{"type":"canvas_webgl_no_error"}]})
    config = RunnerConfig(artifacts_root=tmp_path / "artifacts", allowed_hosts=("host.docker.internal",), allow_private_network=True, max_duration_seconds=60, app_bridge_enabled=True, app_bridge_global_name="GAE_BRIDGE", app_bridge_adapter="gaealavic_cesium")
    try:
        result = RunOrchestrator(runner_mode="container").run_blocking(plan, config)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert result["status"] == "passed", result
    assert result["runner_isolation"]["mode"] == "docker_container"
    assert len(result["steps"][1]["canvas_evidence"]["gestureEvidence"]["points"]) == 3
    assert all(item["semantic_evidence"]["adapter"] == "gaealavic_cesium" for item in result["assertions"])
