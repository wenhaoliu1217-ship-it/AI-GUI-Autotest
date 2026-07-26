import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from gui_agent.domain.models import ActionType, ExecutionMode, Step, TestPlan
from gui_agent.execution import RunOrchestrator, RunnerConfig


PAGE = b"""<!doctype html><html><body>
<canvas id='map' width='400' height='200'></canvas><p id='state'>Waiting</p>
<script>
let selected = null; const canvas = document.querySelector('#map');
canvas.addEventListener('click', () => { selected = 'entity.alpha'; document.querySelector('#state').textContent = 'Selected'; });
window.CONTAINER_BRIDGE = {
 version:'1',
 getSceneState:()=>({camera:{heading:0},layers:[{id:'base',show:true}],tilesLoaded:true,loading:false}),
 listVisibleTargets:()=>[{id:'entity.alpha',type:'entity',label:'Alpha'}],
 getTargetScreenPosition:()=>{const b=canvas.getBoundingClientRect();return{x:b.x+b.width/2,y:b.y+b.height/2}},
 getSelectedTargetId:()=>selected,
 waitForSceneReady:async()=>({ready:true})
};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_args):
        pass


server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
base_url = f"http://host.docker.internal:{server.server_port}"
plan = TestPlan(
    name="real container bridge verification",
    base_url=base_url,
    steps=[
        Step(action=ActionType.NAVIGATE, target="/"),
        Step(
            action=ActionType.BRIDGE_CLICK,
            execution_mode=ExecutionMode.APP_BRIDGE,
            bridge_target_id="entity.alpha",
            description="Select entity Alpha through Bridge",
        ),
    ],
)
artifacts = Path(".verification/bridge-container-artifacts").resolve()
config = RunnerConfig(
    artifacts_root=artifacts,
    allow_private_network=True,
    app_bridge_enabled=True,
    app_bridge_global_name="CONTAINER_BRIDGE",
    app_bridge_adapter="cesium",
    max_duration_seconds=90,
)
try:
    result = RunOrchestrator(runner_mode="container").run_blocking(plan, config)
    bridge_step = result["steps"][1]
    print(json.dumps({
        "run_id": result["run_id"],
        "status": result["status"],
        "completion_reason": result["completion_reason"],
        "isolation": result["runner_isolation"]["mode"],
        "coordinate_source": bridge_step["coordinate_source"],
        "stability_passed": bridge_step["stability_evidence"]["passed"],
        "semantic_verified": bridge_step["app_bridge_result"]["semanticStateVerified"],
        "selected": bridge_step["app_bridge_result"]["selectedTargetAfter"],
        "canvas_evidence_status": bridge_step["canvas_evidence"]["collectionStatus"],
        "scene_before_recorded": bool(bridge_step["canvas_evidence"]["sceneBefore"]),
        "scene_after_recorded": bool(bridge_step["canvas_evidence"]["sceneAfter"]),
        "screenshots_linked": bool(bridge_step["canvas_evidence"]["beforeScreenshot"] and bridge_step["canvas_evidence"]["afterScreenshot"]),
        "trace_linked": bridge_step["canvas_evidence"]["traceArtifact"] == "trace.zip",
    }, ensure_ascii=False))
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
