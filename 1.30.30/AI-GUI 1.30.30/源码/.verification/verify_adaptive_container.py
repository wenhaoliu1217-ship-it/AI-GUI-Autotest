import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from pydantic import SecretStr

from gui_agent.domain.models import ActionType, Assertion, AssertionType, ExecutionMode, Locator, RelativePosition, StabilityLevel, Step, TestPlan
from gui_agent.execution import RunOrchestrator, RunnerConfig
from gui_agent.planning.ai_provider import AISettings
from gui_agent.planning.replay_planner import AdaptiveReplayPlanner
from gui_agent.planning.visual_adapter import OpenAIVisualAdapter


PAGE = b"""<!doctype html><html><body><h1>Adaptive target</h1><canvas id='map' width='400' height='200'></canvas><p id='state'>Waiting</p><script>document.querySelector('#map').addEventListener('click',()=>document.querySelector('#state').textContent='Selected');</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        suggestion = {
            "target": "Canvas center",
            "action": "click",
            "x_ratio": 0.5,
            "y_ratio": 0.5,
            "end_x_ratio": None,
            "end_y_ratio": None,
            "scroll_delta_y": 600,
            "expected_change": "Selected becomes visible",
            "confidence": 0.99,
            "rationale": "Target is at the visible canvas center",
        }
        payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(suggestion)}]}], "usage": {"input_tokens": 10, "output_tokens": 10}}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
port = server.server_port
base_url = f"http://host.docker.internal:{port}"
plan = TestPlan(
    name="real adaptive container verification",
    base_url=base_url,
    steps=[
        Step(action=ActionType.NAVIGATE, target="/"),
        Step(
            action=ActionType.VISUAL_CLICK,
            locator=Locator(css="#map"),
            execution_mode=ExecutionMode.VISUAL,
            stability_level=StabilityLevel.C,
            visual_target="Canvas center",
            relative_position=RelativePosition(xRatio=0.1, yRatio=0.1),
            visual_expected_change="Selected becomes visible",
        ),
    ],
    assertions=[Assertion(type=AssertionType.VISIBLE, locator=Locator(text="Selected"))],
)
artifacts = Path(".verification/adaptive-container-artifacts").resolve()
orchestrator = RunOrchestrator(runner_mode="container")
config = RunnerConfig(
    artifacts_root=artifacts,
    replay_mode="adaptive",
    allow_private_network=True,
    agent_planner=AdaptiveReplayPlanner(plan),
    visual_adapter=OpenAIVisualAdapter(AISettings(
        protocol="responses",
        base_url=f"{base_url}/v1",
        model="verification-vision",
        api_key=SecretStr("request-only-verification-key"),
    )),
    max_model_calls=8,
    max_steps=4,
    max_duration_seconds=90,
)
try:
    result = orchestrator.run_blocking(plan, config)
    run_id = result["run_id"]
    events = (artifacts / run_id / "events.jsonl").read_text(encoding="utf-8")
    print(json.dumps({
        "run_id": run_id,
        "status": result["status"],
        "completion_reason": result["completion_reason"],
        "isolation": result["runner_isolation"]["mode"],
        "coordinate_source": result["steps"][1]["coordinate_source"],
        "visual_verified": '"type": "visual_action_verified"' in events and '"verified": true' in events,
        "api_key_persisted": "request-only-verification-key" in (artifacts / run_id / "run.json").read_text(encoding="utf-8"),
    }, ensure_ascii=False))
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
