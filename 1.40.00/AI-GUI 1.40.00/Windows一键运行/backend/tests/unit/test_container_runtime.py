import json
from pathlib import Path

from pydantic import SecretStr

from gui_agent.domain.models import ActionType, Step, TestPlan as ExecutionPlan
from gui_agent.execution.container_runtime import build_container_spec, build_docker_command, _stage_file_assets
from gui_agent.execution.runner import RunnerConfig
from gui_agent.planning.agent_planner import AgentScenario, AIAgentPlanner
from gui_agent.planning.replay_planner import AdaptiveReplayPlanner
from gui_agent.planning.ai_provider import AISettings


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        name="container probe",
        base_url="https://example.com",
        steps=[Step(action=ActionType.NAVIGATE, target="/")],
    )


def test_container_spec_only_includes_referenced_environment_secrets(monkeypatch) -> None:
    monkeypatch.setenv("QA_PASSWORD", "allowed-secret")
    monkeypatch.setenv("HOST_PRIVATE_TOKEN", "must-not-cross-boundary")
    config = RunnerConfig(secret_refs=(("LOGIN_PASSWORD", "QA_PASSWORD"),))

    spec = build_container_spec(_plan(), config)
    serialized = json.dumps(spec)

    assert spec["environment"] == {"QA_PASSWORD": "allowed-secret"}
    assert "must-not-cross-boundary" not in serialized
    assert "artifacts_root" not in spec["config"]


def test_container_spec_preserves_project_business_context_for_agent() -> None:
    planner = AIAgentPlanner(
        AISettings(
            protocol="responses", base_url="https://api.openai.com/v1",
            model="test-model", api_key=SecretStr("context-key"),
        ),
        AgentScenario(
            name="业务目标", goal="处理任务池",
            business_context={"terminology": {"任务池": "待处理任务集合"}},
        ),
        "https://example.com",
    )

    spec = build_container_spec(_plan(), RunnerConfig(agent_planner=planner))

    assert spec["agent_planner"]["scenario"]["business_context"] == {
        "terminology": {"任务池": "待处理任务集合"}
    }


def test_container_spec_preserves_adaptive_replay_plan_without_recorded_controller_state() -> None:
    spec = build_container_spec(_plan(), RunnerConfig(agent_planner=AdaptiveReplayPlanner(_plan())))

    assert spec["agent_planner"]["kind"] == "adaptive_replay"
    assert spec["agent_planner"]["plan"]["steps"][0]["action"] == "navigate"


def test_container_spec_preserves_universal_runtime_contracts() -> None:
    config = RunnerConfig(
        async_state_machines=({"id": "job", "states": ["done"], "terminalStates": ["done"]},),
        side_effect_policies=({"id": "delete", "decision": "confirm"},),
        component_adapters=({"id": "table.filter", "status": "configured"},),
        business_objects=({"key": "job", "cleanupStep": {"action": "click"}},),
        project_snapshot={"id": "project-1"},
        environment_snapshot={"environmentId": "env-1"},
    )
    spec = build_container_spec(_plan(), config)
    assert spec["config"]["async_state_machines"][0]["id"] == "job"
    assert spec["config"]["side_effect_policies"][0]["id"] == "delete"
    assert spec["config"]["component_adapters"][0]["id"] == "table.filter"
    assert spec["config"]["business_objects"][0]["key"] == "job"
    assert spec["config"]["project_snapshot"] == {"id": "project-1"}


def test_docker_command_enforces_container_boundaries(tmp_path: Path) -> None:
    config = RunnerConfig(
        artifacts_root=tmp_path,
        isolation_memory_limit_mb=1536,
        allow_private_network=False,
    )

    command = build_docker_command(
        "docker", "ai-gui-runner:1.30.00", tmp_path / "run-1", "run-1", config
    )
    rendered = " ".join(str(item) for item in command)

    assert "--read-only" in command
    assert "--rm" in command
    assert "--memory 1536m" in rendered
    assert "--cpus 2" in rendered
    assert "--pids-limit 256" in rendered
    assert "--cap-drop ALL" in rendered
    assert "--security-opt no-new-privileges:true" in rendered
    assert "GUI_ALLOW_PRIVATE_NETWORK=0" in command
    assert "type=bind" in rendered and "dst=/work/artifacts/run-1" in rendered
    assert rendered.count("--tmpfs") == 3


def test_docker_command_records_explicit_private_network_exception(tmp_path: Path) -> None:
    config = RunnerConfig(artifacts_root=tmp_path, allow_private_network=True)

    command = build_docker_command(
        "docker", "ai-gui-runner:1.30.00", tmp_path / "run-2", "run-2", config
    )

    assert "GUI_ALLOW_PRIVATE_NETWORK=1" in command


def test_container_stages_only_referenced_hash_without_host_path(tmp_path: Path) -> None:
    import hashlib

    source = tmp_path / "host" / "private-name.txt"
    source.parent.mkdir()
    source.write_bytes(b"fixed input")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    staged = _stage_file_assets(
        RunnerConfig(file_assets=((f"asset:{digest}", str(source)),)), run_dir, "run-1"
    )
    assert staged.file_assets == ((f"asset:{digest}", f"/work/artifacts/run-1/_inputs/{digest}"),)
    assert (run_dir / "_inputs" / digest).read_bytes() == b"fixed input"
    assert "private-name" not in str(staged.file_assets)
