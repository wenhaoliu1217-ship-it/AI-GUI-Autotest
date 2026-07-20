"""连接 Web GUI 与真实 Playwright 执行器的 HTTP API。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, ValidationError

from ..domain.models import Step, TestPlan
from ..execution import RunOrchestrator, RunnerConfig, run_plan
from ..execution.review import RunReviewError, apply_path_review, load_path_review, save_generated_source
from ..onboarding import (
    AuditRecord,
    EnvironmentConfig,
    LoginRecordingManager,
    ProjectConfig,
    ProjectLimits,
    ProjectStore,
    ScenarioConfig,
    SessionStateError,
    scan_project,
    validate_storage_state,
)
from ..planning import AgentScenario, AIAgentPlanner, OpenAIVisualAdapter, PlanningError, plan_from_draft
from ..planning.ai_provider import AIProviderError, AISettings, plan_with_ai, test_connection
from ..security.policy import DomainPolicy, SecurityError, resolve_env_placeholder


ARTIFACTS_ROOT = Path(os.getenv("GUI_AGENT_ARTIFACTS", "artifacts")).resolve()
ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
DATA_ROOT = Path(os.getenv("GUI_AGENT_DATA", "data")).resolve()
PROJECT_STORE = ProjectStore(DATA_ROOT / "projects")
LOGIN_RECORDINGS = LoginRecordingManager()
RUN_ORCHESTRATOR = RunOrchestrator()

app = FastAPI(title="京彩OPC AI GUI 执行服务", version="0.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "GUI_ALLOWED_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173",
        ).split(",")
        if origin.strip()
    ],
    allow_origin_regex=r"http://(?:127\.0\.0\.1|localhost):\d+",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class DraftRequest(BaseModel):
    name: str
    targetUrl: str
    flow: str
    role: str | None = None
    preconditions: str | None = None
    expectation: str | None = None
    testData: dict = Field(default_factory=dict)
    forbiddenActions: list[str] = Field(default_factory=list)
    projectId: str | None = None
    environmentId: str | None = None


class AISettingsRequest(BaseModel):
    protocol: str
    baseUrl: str
    model: str
    apiKey: SecretStr
    inputCostPerMillion: float | None = Field(default=None, ge=0)
    outputCostPerMillion: float | None = Field(default=None, ge=0)

    def to_settings(self) -> AISettings:
        if self.protocol not in {"responses", "chat_completions"}:
            raise AIProviderError("不支持的 API 协议")
        return AISettings(
            protocol=self.protocol,  # type: ignore[arg-type]
            base_url=self.baseUrl,
            model=self.model,
            api_key=self.apiKey,
            input_cost_per_million=self.inputCostPerMillion,
            output_cost_per_million=self.outputCostPerMillion,
        )


class AITestRequest(BaseModel):
    settings: AISettingsRequest


class AIPlanRequest(BaseModel):
    draft: DraftRequest
    settings: AISettingsRequest
    projectId: str | None = None
    environmentId: str | None = None


class PlanRequest(BaseModel):
    plan: dict


class RunRequest(BaseModel):
    plan: dict
    headless: bool = True
    timeoutMs: int = Field(default=30_000, ge=1_000, le=120_000)
    projectId: str | None = None
    environmentId: str | None = None
    scenarioId: str | None = None
    asyncExecution: bool = False


class AgentScenarioRequest(BaseModel):
    name: str
    goal: str
    preconditions: str = ""
    testData: dict = Field(default_factory=dict)
    expectedResults: list[str] = Field(default_factory=list)
    forbiddenActions: list[str] = Field(default_factory=list)


class AgentRunRequest(BaseModel):
    plan: dict
    scenario: AgentScenarioRequest
    settings: AISettingsRequest
    headless: bool = True
    timeoutMs: int = Field(default=30_000, ge=1_000, le=120_000)
    projectId: str | None = None
    environmentId: str | None = None
    scenarioId: str | None = None
    enableVisualFallback: bool = False


class ReplayRequest(BaseModel):
    mode: str = "stable"
    headless: bool = True


class FindingReviewRequest(BaseModel):
    status: str
    title: str | None = Field(default=None, min_length=1, max_length=200)
    severity: str | None = None
    expectedResult: str | None = Field(default=None, max_length=2_000)


class ReviewedStepRequest(BaseModel):
    sourceIndex: int = Field(ge=1)
    retained: bool
    step: Step


class RunPathReviewRequest(BaseModel):
    steps: list[ReviewedStepRequest] = Field(min_length=1, max_length=100)


class GeneratedTestUpdateRequest(BaseModel):
    source: str = Field(min_length=1, max_length=500_000)


class RunDeleteRequest(BaseModel):
    runIds: list[str] = Field(min_length=1, max_length=500)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    baseUrl: str
    allowedHosts: list[str] = Field(default_factory=list)
    forbiddenActions: list[str] = Field(default_factory=list)
    onboardingLevel: str = "L0"
    limits: ProjectLimits = Field(default_factory=ProjectLimits)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    baseUrl: str | None = None
    allowedHosts: list[str] | None = None
    forbiddenActions: list[str] | None = None
    onboardingLevel: str | None = None
    limits: ProjectLimits | None = None


class EnvironmentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    variables: dict[str, str] = Field(default_factory=dict)
    secretRefs: dict[str, str] = Field(default_factory=dict)
    ignoreRules: list[str] = Field(default_factory=list)
    viewport: dict = Field(default_factory=lambda: {"width": 1440, "height": 960})
    deviceScaleFactor: float = 1.0
    appBridge: dict = Field(default_factory=dict)
    artifactRetentionDays: int = 30


class ScenarioCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    preconditions: list[str] = Field(default_factory=list)
    goal: str = Field(min_length=1)
    testData: dict = Field(default_factory=dict)
    expectedResults: list[str] = Field(default_factory=list)
    forbiddenActions: list[str] = Field(default_factory=list)


class ScanRequest(BaseModel):
    headless: bool = True
    timeoutMs: int = Field(default=30_000, ge=5_000, le=120_000)


class SessionImportRequest(BaseModel):
    storageState: dict


class SessionRecordingRequest(BaseModel):
    timeoutSeconds: int = Field(default=600, ge=30, le=1800)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": "real",
        "engine": "playwright-chromium",
        "planner": "deterministic-rules + fixed-ai-plan + stepwise-agent",
        "aiConfigStorage": "request-memory-only",
        "artifacts": str(ARTIFACTS_ROOT),
        "projectStorage": str(PROJECT_STORE.root),
    }


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [item.model_dump(mode="json", by_alias=True) for item in PROJECT_STORE.list()]


@app.post("/api/projects")
def create_project(payload: ProjectCreateRequest) -> dict:
    if payload.onboardingLevel not in {"L0", "L1", "L2", "L3"}:
        raise HTTPException(status_code=422, detail="接入级别必须为 L0、L1、L2 或 L3")
    try:
        project = ProjectConfig(
            id=f"project-{uuid4().hex[:10]}",
            name=payload.name,
            baseUrl=payload.baseUrl,
            allowedHosts=payload.allowedHosts,
            forbiddenActions=payload.forbiddenActions,
            onboardingLevel=payload.onboardingLevel,
            limits=payload.limits,
        )
        PROJECT_STORE.save(project)
        PROJECT_STORE.audit(AuditRecord(action="create", objectType="project", objectId=project.id, projectId=project.id, changedFields=["all"]))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return project.model_dump(mode="json", by_alias=True)


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdateRequest) -> dict:
    current = PROJECT_STORE.get(project_id)
    if current is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    changes = payload.model_dump(exclude_none=True)
    if changes.get("onboardingLevel") not in {None, "L0", "L1", "L2", "L3"}:
        raise HTTPException(status_code=422, detail="接入级别必须为 L0、L1、L2 或 L3")
    try:
        updated = ProjectConfig.model_validate({
            **current.model_dump(mode="json", by_alias=True),
            **changes,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        PROJECT_STORE.save(updated)
        PROJECT_STORE.audit(AuditRecord(action="update", objectType="project", objectId=project_id, projectId=project_id, changedFields=sorted(changes)))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return updated.model_dump(mode="json", by_alias=True)


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict:
    if PROJECT_STORE.get(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    PROJECT_STORE.audit(AuditRecord(action="delete", objectType="project", objectId=project_id, projectId=project_id, changedFields=[]))
    PROJECT_STORE.delete_project(project_id)
    return {"deleted": True, "id": project_id}


@app.get("/api/projects/{project_id}/audit")
def get_project_audit(project_id: str) -> list[dict]:
    return [item.model_dump(mode="json", by_alias=True) for item in PROJECT_STORE.list_audit(project_id)]


@app.get("/api/projects/{project_id}/environments")
def list_environments(project_id: str) -> list[dict]:
    if PROJECT_STORE.get(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return [item.model_dump(mode="json", by_alias=True) for item in PROJECT_STORE.list_environments(project_id)]


@app.post("/api/projects/{project_id}/environments")
def create_environment(project_id: str, payload: EnvironmentCreateRequest) -> dict:
    if PROJECT_STORE.get(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        item = EnvironmentConfig(id=f"environment-{uuid4().hex[:10]}", projectId=project_id, **payload.model_dump())
        PROJECT_STORE.save_environment(item)
        PROJECT_STORE.audit(AuditRecord(action="create", objectType="environment", objectId=item.id, projectId=project_id, changedFields=["all"]))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_message(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return item.model_dump(mode="json", by_alias=True)


@app.put("/api/projects/{project_id}/environments/{environment_id}")
def update_environment(project_id: str, environment_id: str, payload: EnvironmentCreateRequest) -> dict:
    if PROJECT_STORE.get(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        current = PROJECT_STORE.get_environment(project_id, environment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if current is None:
        raise HTTPException(status_code=404, detail="测试环境不存在")
    try:
        updated = EnvironmentConfig(
            id=current.id,
            projectId=project_id,
            createdAt=current.created_at,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            **payload.model_dump(),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_message(exc)) from exc
    changed_fields = [
        alias for field, alias in (
            ("name", "name"), ("variables", "variables"), ("secret_refs", "secretRefs"),
            ("ignore_rules", "ignoreRules"), ("viewport", "viewport"),
            ("device_scale_factor", "deviceScaleFactor"), ("app_bridge", "appBridge"),
            ("artifact_retention_days", "artifactRetentionDays"),
        ) if getattr(current, field) != getattr(updated, field)
    ]
    PROJECT_STORE.save_environment(updated)
    PROJECT_STORE.audit(AuditRecord(
        action="update", objectType="environment", objectId=environment_id,
        projectId=project_id, changedFields=changed_fields,
    ))
    return updated.model_dump(mode="json", by_alias=True)


@app.get("/api/projects/{project_id}/scenarios")
def list_scenarios(project_id: str) -> list[dict]:
    if PROJECT_STORE.get(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return [item.model_dump(mode="json", by_alias=True) for item in PROJECT_STORE.list_scenarios(project_id)]


@app.post("/api/projects/{project_id}/scenarios")
def create_scenario(project_id: str, payload: ScenarioCreateRequest) -> dict:
    if PROJECT_STORE.get(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        item = ScenarioConfig(id=f"scenario-{uuid4().hex[:10]}", projectId=project_id, **payload.model_dump())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc
    PROJECT_STORE.save_scenario(item)
    PROJECT_STORE.audit(AuditRecord(action="create", objectType="scenario", objectId=item.id, projectId=project_id, changedFields=["all"]))
    return item.model_dump(mode="json", by_alias=True)


@app.put("/api/projects/{project_id}/scenarios/{scenario_id}")
def update_scenario(project_id: str, scenario_id: str, payload: ScenarioCreateRequest) -> dict:
    if PROJECT_STORE.get(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        current = PROJECT_STORE.get_scenario(project_id, scenario_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if current is None:
        raise HTTPException(status_code=404, detail="场景不存在")
    values = payload.model_dump()
    try:
        updated = ScenarioConfig(
            id=current.id,
            projectId=project_id,
            createdAt=current.created_at,
            updatedAt=datetime.now(timezone.utc).isoformat(),
            **values,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc
    changed_fields = [
        alias for field, alias in (
            ("name", "name"), ("preconditions", "preconditions"), ("goal", "goal"),
            ("test_data", "testData"), ("expected_results", "expectedResults"),
            ("forbidden_actions", "forbiddenActions"),
        ) if getattr(current, field) != getattr(updated, field)
    ]
    PROJECT_STORE.save_scenario(updated)
    PROJECT_STORE.audit(AuditRecord(
        action="update", objectType="scenario", objectId=scenario_id,
        projectId=project_id, changedFields=changed_fields,
    ))
    return updated.model_dump(mode="json", by_alias=True)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    try:
        project = PROJECT_STORE.get(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project.model_dump(mode="json", by_alias=True)


@app.post("/api/projects/{project_id}/session")
def import_project_session(project_id: str, payload: SessionImportRequest) -> dict:
    project = PROJECT_STORE.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        metadata = validate_storage_state(project, payload.storageState)
        PROJECT_STORE.save_session(project, payload.storageState, metadata)
    except SessionStateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return metadata.model_dump(mode="json", by_alias=True)


@app.get("/api/projects/{project_id}/session")
def get_project_session(project_id: str) -> dict:
    project = PROJECT_STORE.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        state = PROJECT_STORE.load_session(project_id)
        saved = PROJECT_STORE.get_session_metadata(project_id)
        if state is None or saved is None:
            raise HTTPException(status_code=404, detail="该项目尚未导入登录态")
        current = validate_storage_state(project, state)
        current.imported_at = saved.imported_at
    except SessionStateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return current.model_dump(mode="json", by_alias=True)


@app.post("/api/projects/{project_id}/session-recordings")
async def start_session_recording(project_id: str, payload: SessionRecordingRequest) -> dict:
    project = PROJECT_STORE.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        recording = await run_in_threadpool(LOGIN_RECORDINGS.start, project, PROJECT_STORE, payload.timeoutSeconds)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": recording.id, "projectId": project_id, "status": recording.status}


@app.post("/api/projects/{project_id}/session-recordings/{recording_id}/complete")
async def complete_session_recording(project_id: str, recording_id: str) -> dict:
    try:
        recording = await run_in_threadpool(LOGIN_RECORDINGS.complete, recording_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if recording.project_id != project_id:
        raise HTTPException(status_code=404, detail="登录录制不属于该项目")
    PROJECT_STORE.audit(AuditRecord(action="record", objectType="session", objectId=recording_id, projectId=project_id, changedFields=["storageState"]))
    return {"id": recording.id, "projectId": project_id, "status": recording.status, "session": recording.result}


@app.delete("/api/projects/{project_id}/session-recordings/{recording_id}")
async def cancel_session_recording(project_id: str, recording_id: str) -> dict:
    try:
        recording = await run_in_threadpool(LOGIN_RECORDINGS.stop, recording_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if recording.project_id != project_id:
        raise HTTPException(status_code=404, detail="登录录制不属于该项目")
    return {"id": recording.id, "status": recording.status}


@app.post("/api/projects/{project_id}/scan")
async def scan_project_compatibility(project_id: str, payload: ScanRequest) -> dict:
    project = PROJECT_STORE.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        storage_state = PROJECT_STORE.load_session(project_id)
        if storage_state:
            session = validate_storage_state(project, storage_state)
            if session.expiry_status == "expired":
                raise SessionStateError("项目登录态已过期，请重新导入后再扫描")
        report = await run_in_threadpool(
            scan_project,
            project,
            headless=payload.headless,
            timeout_ms=payload.timeoutMs,
            storage_state=storage_state,
        )
        scenarios = PROJECT_STORE.list_scenarios(project_id)
        if scenarios:
            sample = scenarios[0]
        else:
            safe_goal = next(
                (item for item in report.suggested_scenarios if item.startswith("确认看到“")),
                f"确认看到“{report.title or project.name}”",
            )
            sample = ScenarioConfig(
                id=f"scenario-{uuid4().hex[:10]}",
                projectId=project_id,
                name=f"{project.name} 扫描示例",
                preconditions=["目标测试环境可访问", "仅执行只读或低风险验证"],
                goal=safe_goal,
                testData={},
                expectedResults=[safe_goal],
                forbiddenActions=list(dict.fromkeys([*project.forbidden_actions, "删除数据", "支付", "发布内容", "发送邀请"])),
            )
            PROJECT_STORE.save_scenario(sample)
            PROJECT_STORE.audit(AuditRecord(
                action="create", objectType="scenario", objectId=sample.id,
                projectId=project_id, changedFields=["all", "generatedByCompatibilityScan"],
            ))
            report.sample_scenario_created = True
        report.sample_scenario_id = sample.id
        PROJECT_STORE.save_report(report)
        PROJECT_STORE.audit(AuditRecord(
            action="scan", objectType="compatibility", objectId=project_id,
            projectId=project_id, changedFields=["report", "recommendedConfig", "sampleScenario"],
        ))
    except (SecurityError, SessionStateError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"兼容性扫描失败：{exc}") from exc
    return report.model_dump(mode="json", by_alias=True)


@app.get("/api/projects/{project_id}/compatibility")
def get_project_compatibility(project_id: str) -> dict:
    try:
        report = PROJECT_STORE.get_report(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if report is None:
        raise HTTPException(status_code=404, detail="该项目尚未生成兼容性报告")
    return report.model_dump(mode="json", by_alias=True)


@app.post("/api/ai/test")
async def test_ai(payload: AITestRequest) -> dict:
    try:
        return await run_in_threadpool(test_connection, payload.settings.to_settings())
    except AIProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/ai/plans/generate")
async def generate_ai_plan(payload: AIPlanRequest) -> dict:
    draft = payload.draft
    target_url = _resolve_draft_target(draft.targetUrl, payload.projectId, payload.environmentId)
    try:
        result = await run_in_threadpool(
            plan_with_ai,
            settings=payload.settings.to_settings(),
            name=draft.name,
            target_url=target_url,
            flow=draft.flow,
            role=draft.role,
            preconditions=draft.preconditions,
            expectation=draft.expectation,
            test_data=draft.testData,
            forbidden_actions=draft.forbiddenActions,
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "plan": result.plan.model_dump(mode="json", exclude_none=True),
        "warnings": [],
        "planner": f"ai:{result.protocol}:{result.model}",
        "elapsedMs": result.elapsed_ms,
    }


@app.post("/api/plans/generate")
def generate_plan(payload: DraftRequest) -> dict:
    target_url = _resolve_draft_target(payload.targetUrl, payload.projectId, payload.environmentId)
    try:
        result = plan_from_draft(
            name=payload.name,
            target_url=target_url,
            flow=payload.flow,
            role=payload.role,
            preconditions=payload.preconditions,
            expectation=payload.expectation,
        )
    except PlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "plan": result.plan.model_dump(mode="json", exclude_none=True),
        "warnings": result.warnings,
        "planner": result.mode,
    }


@app.post("/api/plans/validate")
def validate_plan(payload: PlanRequest) -> dict:
    try:
        plan = TestPlan.model_validate(payload.plan)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    return {"valid": True, "plan": plan.model_dump(mode="json", exclude_none=True)}


@app.post("/api/runs")
async def execute_run(payload: RunRequest) -> dict:
    try:
        plan = TestPlan.model_validate(payload.plan)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    project = None
    environment = None
    storage_state = None
    if payload.environmentId and not payload.projectId:
        raise HTTPException(status_code=422, detail="使用测试环境时必须同时指定所属项目")
    if payload.projectId:
        project = PROJECT_STORE.get(payload.projectId)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        environment = _environment_for_run(project, payload.environmentId)
        try:
            resolved_base_url = resolve_env_placeholder(plan.base_url, environment.variables if environment else None)
            DomainPolicy(project.base_url, project.allowed_hosts).check_url(resolved_base_url)
        except SecurityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if len(plan.steps) > project.limits.max_steps:
            raise HTTPException(status_code=422, detail=f"计划步骤超过项目上限 {project.limits.max_steps}")
        if payload.timeoutMs > project.limits.timeout_seconds * 1000:
            raise HTTPException(status_code=422, detail="单步超时超过项目运行上限")
        try:
            storage_state = PROJECT_STORE.load_session(project.id)
            if storage_state and validate_storage_state(project, storage_state).expiry_status == "expired":
                raise HTTPException(status_code=422, detail="项目登录态已过期，请重新导入")
        except SessionStateError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _check_environment_secret_refs(plan, environment)
    scenario = _scenario_for_run(project, payload.scenarioId)
    forbidden = tuple(dict.fromkeys([
        *(project.forbidden_actions if project else []),
        *(scenario.forbidden_actions if scenario else []),
    ]))
    _check_plan_forbidden_actions(plan, forbidden)
    config = RunnerConfig(
        artifacts_root=ARTIFACTS_ROOT,
        headless=payload.headless,
        timeout_ms=payload.timeoutMs,
        allowed_hosts=tuple(project.allowed_hosts) if project else (),
        storage_state=storage_state,
        onboarding_level=project.onboarding_level if project else None,
        max_duration_seconds=project.limits.timeout_seconds if project else 600,
        project_id=project.id if project else None,
        environment_id=environment.id if environment else None,
        environment_updated_at=environment.updated_at if environment else None,
        environment_variables=tuple(environment.variables.items()) if environment else (),
        secret_refs=tuple(environment.secret_refs.items()) if environment else (),
        ignore_rules=tuple(environment.ignore_rules) if environment else (),
        viewport=(environment.viewport.width, environment.viewport.height) if environment else (1440, 960),
        device_scale_factor=environment.device_scale_factor if environment else 1.0,
        artifact_retention_days=environment.artifact_retention_days if environment else 30,
        scenario_id=scenario.id if scenario else None,
        scenario_updated_at=scenario.updated_at if scenario else None,
        forbidden_actions=forbidden,
        scenario_goal=scenario.goal if scenario else plan.name,
    )
    if payload.asyncExecution:
        return _run_payload(RUN_ORCHESTRATOR.start(plan, config))
    try:
        result, _ = await run_in_threadpool(
            run_plan,
            plan,
            config,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"执行器启动失败：{exc}") from exc
    return _run_payload(result.model_dump(mode="json"))


@app.post("/api/agent-runs")
def execute_agent_run(payload: AgentRunRequest) -> dict:
    try:
        plan = TestPlan.model_validate(payload.plan)
        settings = payload.settings.to_settings().validated()
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except AIProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    project = None
    environment = None
    storage_state = None
    if payload.environmentId and not payload.projectId:
        raise HTTPException(status_code=422, detail="使用测试环境时必须同时指定所属项目")
    if payload.projectId:
        project = PROJECT_STORE.get(payload.projectId)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        environment = _environment_for_run(project, payload.environmentId)
        try:
            resolved_base_url = resolve_env_placeholder(plan.base_url, environment.variables if environment else None)
            DomainPolicy(project.base_url, project.allowed_hosts).check_url(resolved_base_url)
            storage_state = PROJECT_STORE.load_session(project.id)
            if storage_state and validate_storage_state(project, storage_state).expiry_status == "expired":
                raise HTTPException(status_code=422, detail="项目登录态已过期，请重新导入")
        except (SecurityError, SessionStateError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _check_environment_secret_refs(plan, environment)

    saved_scenario = _scenario_for_run(project, payload.scenarioId)
    scenario = AgentScenario(
        name=saved_scenario.name if saved_scenario else payload.scenario.name,
        goal=saved_scenario.goal if saved_scenario else payload.scenario.goal,
        preconditions="\n".join(saved_scenario.preconditions) if saved_scenario else payload.scenario.preconditions,
        test_data=saved_scenario.test_data if saved_scenario else payload.scenario.testData,
        expected_results=saved_scenario.expected_results if saved_scenario else payload.scenario.expectedResults,
        forbidden_actions=saved_scenario.forbidden_actions if saved_scenario else payload.scenario.forbiddenActions,
    )
    forbidden = tuple(dict.fromkeys([
        *(project.forbidden_actions if project else []),
        *scenario.forbidden_actions,
    ]))
    limits = project.limits if project else ProjectLimits()
    try:
        planner_base_url = resolve_env_placeholder(plan.base_url, environment.variables if environment else None)
    except SecurityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    planner = AIAgentPlanner(settings, scenario, planner_base_url, visual_enabled=payload.enableVisualFallback)
    config = RunnerConfig(
        artifacts_root=ARTIFACTS_ROOT,
        headless=payload.headless,
        timeout_ms=payload.timeoutMs,
        allowed_hosts=tuple(project.allowed_hosts) if project else (),
        storage_state=storage_state,
        onboarding_level=project.onboarding_level if project else None,
        max_duration_seconds=limits.timeout_seconds,
        agent_planner=planner,
        max_model_calls=limits.max_model_calls,
        max_steps=limits.max_steps,
        no_progress_limit=3,
        forbidden_actions=forbidden,
        visual_adapter=OpenAIVisualAdapter(settings) if payload.enableVisualFallback else None,
        project_id=project.id if project else None,
        environment_id=environment.id if environment else None,
        environment_updated_at=environment.updated_at if environment else None,
        environment_variables=tuple(environment.variables.items()) if environment else (),
        secret_refs=tuple(environment.secret_refs.items()) if environment else (),
        ignore_rules=tuple(environment.ignore_rules) if environment else (),
        viewport=(environment.viewport.width, environment.viewport.height) if environment else (1440, 960),
        device_scale_factor=environment.device_scale_factor if environment else 1.0,
        artifact_retention_days=environment.artifact_retention_days if environment else 30,
        scenario_id=saved_scenario.id if saved_scenario else None,
        scenario_updated_at=saved_scenario.updated_at if saved_scenario else None,
        scenario_goal=scenario.goal,
    )
    return _run_payload(RUN_ORCHESTRATOR.start(plan, config))


def _scenario_for_run(project: ProjectConfig | None, scenario_id: str | None) -> ScenarioConfig | None:
    if not scenario_id:
        return None
    if project is None:
        raise HTTPException(status_code=422, detail="使用已保存场景时必须同时指定所属项目")
    try:
        scenario = PROJECT_STORE.get_scenario(project.id, scenario_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if scenario is None:
        raise HTTPException(status_code=404, detail="场景不存在或不属于当前项目")
    return scenario


def _environment_for_run(project: ProjectConfig | None, environment_id: str | None) -> EnvironmentConfig | None:
    if not environment_id:
        return None
    if project is None:
        raise HTTPException(status_code=422, detail="使用测试环境时必须同时指定所属项目")
    try:
        environment = PROJECT_STORE.get_environment(project.id, environment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if environment is None:
        raise HTTPException(status_code=404, detail="测试环境不存在或不属于当前项目")
    return environment


def _resolve_draft_target(target_url: str, project_id: str | None, environment_id: str | None) -> str:
    if environment_id and not project_id:
        raise HTTPException(status_code=422, detail="指定运行环境时必须同时指定项目")
    project = PROJECT_STORE.get(project_id) if project_id else None
    if project_id and project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    environment = _environment_for_run(project, environment_id)
    try:
        return resolve_env_placeholder(target_url, environment.variables if environment else None)
    except SecurityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _check_environment_secret_refs(plan: TestPlan, environment: EnvironmentConfig | None) -> None:
    if environment is None:
        return
    missing: list[str] = []
    for step in plan.steps:
        if not step.value_from_secret:
            continue
        system_name = environment.secret_refs.get(step.value_from_secret, step.value_from_secret)
        if os.environ.get(system_name) is None:
            missing.append(f"{step.value_from_secret} -> {system_name}")
    if missing:
        raise HTTPException(status_code=422, detail=f"测试环境缺少运行时密钥：{'；'.join(dict.fromkeys(missing))}")


def _validation_message(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors(include_context=False)[:8]:
        message = str(error.get("msg", "配置不合法"))
        messages.append(message.removeprefix("Value error, "))
    return "；".join(dict.fromkeys(messages))


def _check_plan_forbidden_actions(plan: TestPlan, forbidden_actions: tuple[str, ...]) -> None:
    for step_index, step in enumerate(plan.steps, start=1):
        serialized = json.dumps(step.model_dump(mode="json", exclude_none=True), ensure_ascii=False).lower()
        blocked = next((item for item in forbidden_actions if item.strip().lower() in serialized), None)
        if blocked:
            raise HTTPException(status_code=422, detail=f"计划第 {step_index} 步命中禁止动作：{blocked}")


@app.post("/api/runs/{run_id}/replay")
async def replay_run(run_id: str, payload: ReplayRequest) -> dict:
    if payload.mode not in {"stable", "adaptive"}:
        raise HTTPException(status_code=422, detail="回放模式必须为 stable 或 adaptive")
    run_dir = _safe_run_dir(run_id)
    reviewed_plan_path = run_dir / "reviewed-plan.json"
    plan_path = reviewed_plan_path if reviewed_plan_path.is_file() else run_dir / "plan.json"
    run_path = run_dir / "run.json"
    if not plan_path.is_file() or not run_path.is_file():
        raise HTTPException(status_code=404, detail="运行计划不存在，无法回放")
    plan = TestPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    previous = json.loads(run_path.read_text(encoding="utf-8"))
    generated = previous.get("generated_test") or {}
    supported = generated.get("supported_replay_modes", [])
    if payload.mode not in supported:
        raise HTTPException(status_code=422, detail=f"当前测试稳定性 {generated.get('stability_level', 'D')} 不支持 {payload.mode} 回放")
    replay_project = PROJECT_STORE.get(previous["project_id"]) if previous.get("project_id") else None
    replay_environment = _environment_for_run(replay_project, previous.get("environment_id"))
    replay_storage_state = None
    if replay_project:
        try:
            replay_storage_state = PROJECT_STORE.load_session(replay_project.id)
            if replay_storage_state and validate_storage_state(replay_project, replay_storage_state).expiry_status == "expired":
                raise HTTPException(status_code=422, detail="项目登录态已过期，请重新导入")
        except SessionStateError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    _check_environment_secret_refs(plan, replay_environment)
    result, _ = await run_in_threadpool(
        run_plan,
        plan,
        RunnerConfig(
            artifacts_root=ARTIFACTS_ROOT,
            headless=payload.headless,
            replay_mode=payload.mode,
            project_id=replay_project.id if replay_project else None,
            allowed_hosts=tuple(replay_project.allowed_hosts) if replay_project else (),
            storage_state=replay_storage_state,
            onboarding_level=replay_project.onboarding_level if replay_project else previous.get("onboarding_level"),
            max_duration_seconds=replay_project.limits.timeout_seconds if replay_project else 600,
            environment_id=replay_environment.id if replay_environment else None,
            environment_updated_at=replay_environment.updated_at if replay_environment else None,
            environment_variables=tuple(replay_environment.variables.items()) if replay_environment else (),
            secret_refs=tuple(replay_environment.secret_refs.items()) if replay_environment else (),
            ignore_rules=tuple(replay_environment.ignore_rules) if replay_environment else (),
            viewport=(replay_environment.viewport.width, replay_environment.viewport.height) if replay_environment else (1440, 960),
            device_scale_factor=replay_environment.device_scale_factor if replay_environment else 1.0,
            artifact_retention_days=replay_environment.artifact_retention_days if replay_environment else 30,
            scenario_id=previous.get("scenario_id"),
            scenario_updated_at=previous.get("scenario_updated_at"),
            scenario_goal=previous.get("scenario_goal") or plan.name,
        ),
    )
    return _run_payload(result.model_dump(mode="json"))


@app.patch("/api/runs/{run_id}/findings/{finding_id}")
def review_finding(run_id: str, finding_id: str, payload: FindingReviewRequest) -> dict:
    if payload.status not in {"confirmed", "rejected", "pending_review"}:
        raise HTTPException(status_code=422, detail="审核状态非法")
    if payload.severity is not None and payload.severity not in {"Blocker", "High", "Medium", "Low"}:
        raise HTTPException(status_code=422, detail="严重程度非法")
    run_path = _safe_run_dir(run_id) / "run.json"
    if not run_path.is_file():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    data = json.loads(run_path.read_text(encoding="utf-8"))
    finding = next((item for item in data.get("findings", []) if item.get("id") == finding_id), None)
    if finding is None:
        raise HTTPException(status_code=404, detail="问题不存在")
    changed = {"review_status": payload.status}
    if payload.title is not None:
        changed["title"] = payload.title
    if payload.severity is not None:
        changed["severity"] = payload.severity
    if payload.expectedResult is not None:
        changed["expected_result"] = payload.expectedResult
    changes = {
        key: {"before": finding.get(key), "after": value}
        for key, value in changed.items()
        if finding.get(key) != value
    }
    finding.setdefault("review_history", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": "local-user",
        "changedFields": sorted(changes),
        "changes": changes,
        "previousStatus": finding.get("review_status"),
        "newStatus": payload.status,
    })
    finding.update(changed)
    temporary = run_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(run_path)
    return finding


@app.get("/api/runs/{run_id}/review")
def get_run_review(run_id: str) -> dict:
    run_dir = _safe_run_dir(run_id)
    if not (run_dir / "run.json").is_file():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    try:
        return load_path_review(run_dir)
    except RunReviewError as exc:
        if not (run_dir / "plan.json").is_file():
            return {"available": False, "steps": [], "history": [], "reason": str(exc)}
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/runs/{run_id}/review")
def save_run_review(run_id: str, payload: RunPathReviewRequest) -> dict:
    run_dir = _safe_run_dir(run_id)
    try:
        return apply_path_review(
            run_dir,
            [(item.sourceIndex, item.retained, item.step) for item in payload.steps],
        )
    except RunReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/generated-test")
def download_generated_test(run_id: str) -> FileResponse:
    target = _safe_run_dir(run_id) / "generated-test.spec.ts"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="尚未生成测试文件")
    return FileResponse(target, media_type="text/typescript", filename=f"{run_id}.spec.ts")


@app.patch("/api/runs/{run_id}/generated-test")
def update_generated_test(run_id: str, payload: GeneratedTestUpdateRequest) -> dict:
    try:
        return save_generated_source(_safe_run_dir(run_id), payload.source)
    except RunReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/report.json")
def download_run_report_json(run_id: str) -> Response:
    _safe_run_dir(run_id)
    payload = RUN_ORCHESTRATOR.read(run_id, ARTIFACTS_ROOT)
    if payload is None:
        raise HTTPException(status_code=404, detail="运行报告不存在")
    content = json.dumps(_run_payload(payload), ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{run_id}-report.json"'},
    )


@app.get("/api/runs/{run_id}/report.html")
def download_run_report_html(run_id: str) -> FileResponse:
    target = _safe_run_dir(run_id) / "report.html"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="HTML 执行证据不存在")
    return FileResponse(target, media_type="text/html", filename=f"{run_id}-evidence.html")


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return [_run_payload(item) for item in RUN_ORCHESTRATOR.list(ARTIFACTS_ROOT)]


@app.post("/api/runs/delete")
def delete_runs(payload: RunDeleteRequest) -> dict:
    run_ids = list(dict.fromkeys(payload.runIds))
    run_dirs: list[Path] = []
    for run_id in run_ids:
        run_dir = _safe_run_dir(run_id)
        if not (run_dir / "run.json").is_file():
            raise HTTPException(status_code=404, detail=f"运行记录不存在：{run_id}")
        run_dirs.append(run_dir)
    for run_dir in run_dirs:
        shutil.rmtree(run_dir)
    return {"deleted": run_ids, "count": len(run_ids)}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    _safe_run_dir(run_id)
    payload = RUN_ORCHESTRATOR.read(run_id, ARTIFACTS_ROOT)
    if payload is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return _run_payload(payload)


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    _safe_run_dir(run_id)
    try:
        return _run_payload(RUN_ORCHESTRATOR.cancel(run_id, ARTIFACTS_ROOT))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="运行记录不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/artifacts/{run_id}/{artifact_path:path}")
def get_artifact(run_id: str, artifact_path: str) -> FileResponse:
    run_dir = _safe_run_dir(run_id)
    target = (run_dir / artifact_path).resolve()
    if run_dir not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="证据文件不存在")
    return FileResponse(target)


def _safe_run_dir(run_id: str) -> Path:
    if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in run_id):
        raise HTTPException(status_code=400, detail="运行编号非法")
    run_dir = (ARTIFACTS_ROOT / run_id).resolve()
    if ARTIFACTS_ROOT not in run_dir.parents:
        raise HTTPException(status_code=400, detail="运行编号非法")
    return run_dir


def _run_payload(payload: dict) -> dict:
    payload = dict(payload)
    run_id = payload.get("run_id", "")
    payload["artifact_base_url"] = f"/api/artifacts/{run_id}"
    status = str(payload.get("status") or "incomplete")
    assertions = payload.get("assertions") if isinstance(payload.get("assertions"), list) else []
    passed_assertions = sum(item.get("status") == "passed" for item in assertions if isinstance(item, dict))
    total_assertions = len(assertions)
    payload.setdefault("scenario_goal", payload.get("plan_name") or "未命名场景")
    if not payload.get("goal_status"):
        payload["goal_status"] = (
            "achieved" if status == "passed" else
            "in_progress" if status in {"queued", "running"} else
            "incomplete" if status in {"cancelled", "system_error", "error", "incomplete"} else
            "not_achieved"
        )
    if not payload.get("goal_summary"):
        assertion_summary = f"断言通过 {passed_assertions}/{total_assertions}" if total_assertions else "无收尾断言"
        payload["goal_summary"] = f"{assertion_summary}；结束原因 {payload.get('completion_reason', status)}"
    try:
        started = datetime.fromisoformat(str(payload.get("started_at")))
        ended = datetime.fromisoformat(str(payload.get("ended_at")))
        payload["duration_ms"] = max(0, round((ended - started).total_seconds() * 1000))
    except (TypeError, ValueError):
        payload["duration_ms"] = 0
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    review_statuses = [item.get("review_status", "pending_review") for item in findings if isinstance(item, dict)]
    pending = review_statuses.count("pending_review")
    confirmed = review_statuses.count("confirmed")
    rejected = review_statuses.count("rejected")
    disposition = (
        "pending_confirmation" if pending else
        "issues_found" if confirmed else
        "all_rejected" if rejected else
        "no_findings"
    )
    payload["review_summary"] = {
        "disposition": disposition,
        "pending": pending,
        "confirmed": confirmed,
        "rejected": rejected,
        "total": len(review_statuses),
    }
    return payload


def main() -> None:
    uvicorn.run(
        "gui_agent.api.server:app",
        host=os.getenv("GUI_API_HOST", "127.0.0.1"),
        port=int(os.getenv("GUI_API_PORT", "8787")),
        reload=False,
    )


if __name__ == "__main__":
    main()


STATIC_DIR = os.getenv("GUI_STATIC_DIR")
if STATIC_DIR and Path(STATIC_DIR).is_dir():
    # API 路由先注册，最后挂载静态 GUI；同一个端口即可完成真实一键运行。
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")
