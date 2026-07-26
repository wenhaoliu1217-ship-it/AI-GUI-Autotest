"""连接 Web GUI 与真实 Playwright 执行器的 HTTP API。"""

from __future__ import annotations

import json
import os
import shutil
import html
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, ValidationError

from ..commerce import (
    AcceptanceBatchError, AcceptanceBatchStore, CallbackObservation, CommerceActionRequest, CommerceAssuranceError, InventoryRaceEvidence,
    evaluate_callback_idempotency, evaluate_commerce_action, evaluate_inventory_race,
)
from ..domain.models import Step, TestPlan
from ..artifacts import ArtifactLifecycle, ArtifactLifecycleError, FileAssetError, FileAssetStore
from ..execution import RunOrchestrator, RunnerConfig
from ..execution.confirmation import confirmation_match
from ..execution.review import RunReviewError, apply_path_review, load_path_review, save_generated_source
from ..onboarding import (
    AuditRecord,
    BusinessContext,
    CommerceProfile,
    EnvironmentConfig,
    LoginRecordingManager,
    ProjectConfig,
    ProjectLimits,
    ProjectStore,
    ScenarioCommerceStep,
    ScenarioConfig,
    ScenarioExecutionStep,
    SessionStateError,
    scan_project,
    validate_storage_state,
)
from ..planning import AdaptiveReplayPlanner, AgentScenario, AIAgentPlanner, OpenAIVisualAdapter, PlanningError, plan_from_draft
from ..planning.ai_provider import AIProviderError, AISettings, plan_with_ai, test_connection
from ..security.policy import DomainPolicy, SecurityError, resolve_env_placeholder
from ..version import APP_VERSION


ARTIFACTS_ROOT = Path(os.getenv("GUI_AGENT_ARTIFACTS", "artifacts")).resolve()
ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
DATA_ROOT = Path(os.getenv("GUI_AGENT_DATA", "data")).resolve()
PROJECT_STORE = ProjectStore(DATA_ROOT / "projects")
FILE_ASSET_STORE = FileAssetStore(DATA_ROOT / "file-assets")
LOGIN_RECORDINGS = LoginRecordingManager()
RUN_ORCHESTRATOR = RunOrchestrator()
JD_BENCHMARK_ROOT = Path(__file__).resolve().parents[3] / "benchmarks" / "jd"
JD_ACCEPTANCE_STORE = AcceptanceBatchStore(DATA_ROOT / "jd-acceptance", JD_BENCHMARK_ROOT / "scenarios")

app = FastAPI(title="京彩OPC AI GUI 执行服务", version=APP_VERSION)
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
    scenarioId: str | None = None


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
    scenarioId: str | None = None


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
    settings: AISettingsRequest | None = None


class ConfirmationDecisionRequest(BaseModel):
    confirmationId: str = Field(min_length=1, max_length=100)
    decision: str
    actor: str = Field(default="local_user", min_length=1, max_length=120)


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
    actor: str = Field(default="local_user", min_length=1, max_length=120)


class RunCleanupRequest(BaseModel):
    actor: str = Field(default="system", min_length=1, max_length=120)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    baseUrl: str
    allowedHosts: list[str] = Field(default_factory=list)
    forbiddenActions: list[str] = Field(default_factory=list)
    allowPrivateNetwork: bool = False
    businessContext: BusinessContext = Field(default_factory=BusinessContext)
    commerceProfile: CommerceProfile = Field(default_factory=CommerceProfile)
    onboardingLevel: str = "L0"
    limits: ProjectLimits = Field(default_factory=ProjectLimits)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    baseUrl: str | None = None
    allowedHosts: list[str] | None = None
    forbiddenActions: list[str] | None = None
    allowPrivateNetwork: bool | None = None
    businessContext: BusinessContext | None = None
    commerceProfile: CommerceProfile | None = None
    onboardingLevel: str | None = None
    limits: ProjectLimits | None = None


class EnvironmentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    variables: dict[str, str] = Field(default_factory=dict)
    secretRefs: dict[str, str] = Field(default_factory=dict)
    ignoreRules: list[str] = Field(default_factory=list)
    screenshotMaskSelectors: list[str] = Field(default_factory=list)
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
    commerceSteps: list[ScenarioCommerceStep] = Field(default_factory=list)
    executionSteps: list[ScenarioExecutionStep] = Field(default_factory=list)


class ScanRequest(BaseModel):
    headless: bool = True
    timeoutMs: int = Field(default=30_000, ge=5_000, le=120_000)


class SessionImportRequest(BaseModel):
    storageState: dict


class SessionRecordingRequest(BaseModel):
    timeoutSeconds: int = Field(default=600, ge=30, le=1800)


class InventoryRaceRequest(BaseModel):
    environment: str
    evidence: InventoryRaceEvidence


class CallbackIdempotencyRequest(BaseModel):
    environment: str
    observations: list[CallbackObservation] = Field(min_length=2, max_length=20)


class AcceptanceAttemptRequest(BaseModel):
    status: str
    runId: str | None = None
    evidenceCompleteness: float | None = Field(default=None, ge=0, le=1)
    stableReplay: bool | None = None
    amountAccurate: bool | None = None
    cleanupComplete: bool | None = None
    zeroToleranceIncidents: dict[str, int] = Field(default_factory=dict)
    blockedDependencies: list[str] = Field(default_factory=list)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "appVersion": APP_VERSION,
        "mode": "real",
        "engine": "playwright-chromium",
        "planner": "deterministic-rules + fixed-ai-plan + stepwise-agent",
        "aiConfigStorage": "request-memory-only",
        "artifacts": str(ARTIFACTS_ROOT),
        "projectStorage": str(PROJECT_STORE.root),
    }


@app.post("/api/commerce/policy/evaluate")
def evaluate_commerce_policy(payload: CommerceActionRequest) -> dict:
    """在任何电商副作用动作执行前返回机器可读门禁结论。"""
    return evaluate_commerce_action(payload).model_dump(mode="json", by_alias=True)


@app.post("/api/commerce/assurance/inventory-race/evaluate")
def evaluate_inventory_race_api(payload: InventoryRaceRequest) -> dict:
    if payload.environment != "isolated_transaction":
        raise HTTPException(status_code=422, detail="双会话库存验证只允许隔离交易环境")
    try:
        return evaluate_inventory_race(payload.evidence)
    except CommerceAssuranceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/commerce/assurance/callback-idempotency/evaluate")
def evaluate_callback_idempotency_api(payload: CallbackIdempotencyRequest) -> dict:
    if payload.environment != "isolated_transaction":
        raise HTTPException(status_code=422, detail="支付／退款回调验证只允许隔离交易环境")
    try:
        return evaluate_callback_idempotency(payload.observations)
    except CommerceAssuranceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/benchmarks/jd/manifest")
def get_jd_benchmark_manifest() -> dict:
    target = JD_BENCHMARK_ROOT / "manifest.json"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="京东场景清单尚未生成")
    return json.loads(target.read_text(encoding="utf-8"))


@app.get("/api/benchmarks/jd/scenarios")
def list_jd_benchmark_scenarios() -> list[dict]:
    scenario_root = JD_BENCHMARK_ROOT / "scenarios"
    if not scenario_root.is_dir():
        raise HTTPException(status_code=404, detail="京东场景目录尚未生成")
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(scenario_root.glob("J*.json"))
    ]


@app.get("/api/acceptance/jd/batches")
def list_jd_acceptance_batches() -> list[dict]:
    return JD_ACCEPTANCE_STORE.list()


@app.post("/api/acceptance/jd/batches")
def start_jd_acceptance_batch() -> dict:
    try:
        return JD_ACCEPTANCE_STORE.start()
    except AcceptanceBatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/acceptance/jd/batches/{batch_id}")
def get_jd_acceptance_batch(batch_id: str) -> dict:
    try:
        return JD_ACCEPTANCE_STORE.get(batch_id)
    except AcceptanceBatchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/acceptance/jd/batches/{batch_id}/{action}")
def control_jd_acceptance_batch(batch_id: str, action: str) -> dict:
    try:
        if action == "cancel":
            return JD_ACCEPTANCE_STORE.cancel(batch_id)
        if action == "resume":
            return JD_ACCEPTANCE_STORE.resume(batch_id)
        if action == "retry-failed":
            return JD_ACCEPTANCE_STORE.retry_failed(batch_id)
        raise HTTPException(status_code=404, detail="不支持的验收批次操作")
    except AcceptanceBatchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/acceptance/jd/batches/{batch_id}/attempts/{attempt_id}")
def record_jd_acceptance_attempt(batch_id: str, attempt_id: str, payload: AcceptanceAttemptRequest) -> dict:
    try:
        values = payload.model_dump()
        if payload.status in {"passed", "failed"}:
            if not payload.runId:
                raise AcceptanceBatchError("已验证尝试必须关联真实 runId")
            run_path = _safe_run_dir(payload.runId) / "run.json"
            if not run_path.is_file():
                raise AcceptanceBatchError("关联运行报告不存在")
            run = json.loads(run_path.read_text(encoding="utf-8"))
            commerce = run.get("commerce_summary") or {}
            release_gate = commerce.get("releaseGate") or {}
            gate_checks = release_gate.get("checks") or {}
            duplicate = gate_checks.get("duplicateSideEffects") or {}
            privacy = gate_checks.get("privacyLeakage") or {}
            values.update({
                "status": "passed" if run.get("status") == "passed" and release_gate.get("passed") is True else "failed",
                "evidenceCompleteness": (gate_checks.get("evidenceCompleteness") or {}).get("ratio"),
                "stableReplay": run.get("replay_mode") == "stable" and run.get("status") == "passed",
                "amountAccurate": commerce.get("amountAccurate") is True,
                "cleanupComplete": commerce.get("zeroResidual") is True,
                "zeroToleranceIncidents": {
                    "privacyLeak": int(privacy.get("count", 0)),
                    "duplicateOrder": int(duplicate.get("duplicateResourceReferences", 0)),
                    "duplicateCharge": int(duplicate.get("unknownSideEffectOutcomes", 0)),
                },
            })
        return JD_ACCEPTANCE_STORE.record_attempt(batch_id, attempt_id, values)
    except AcceptanceBatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/acceptance/jd/batches/{batch_id}/report.html")
def download_jd_acceptance_report(batch_id: str) -> Response:
    try:
        batch = JD_ACCEPTANCE_STORE.get(batch_id)
    except AcceptanceBatchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    summary = batch["summary"]
    count_rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{count}</td></tr>"
        for name, count in summary["counts"].items()
    )
    threshold_rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{html.escape(str(value['actual']))}</td>"
        f"<td>{html.escape(str(value['required']))}</td><td>{'passed' if _acceptance_threshold_passed(name, value) else 'failed'}</td></tr>"
        for name, value in summary["thresholds"].items()
    )
    rows = "".join(
        f"<tr><td>{html.escape(item['id'])}</td><td>{html.escape(item['title'])}</td><td>{html.escape(item['status'])}</td>"
        f"<td>{html.escape(item['verificationStatus'])}</td><td>{html.escape(', '.join(item.get('blockedDependencies') or []))}</td></tr>"
        for item in batch["attempts"]
    )
    detail_rows = "".join(
        f"<tr><td>{html.escape(item['id'])}</td><td>{html.escape(str(item.get('runId') or ''))}</td>"
        f"<td>{html.escape(str(item.get('evidenceCompleteness')))}</td><td>{html.escape(str(item.get('stableReplay')))}</td>"
        f"<td>{html.escape(str(item.get('amountAccurate')))}</td><td>{html.escape(str(item.get('cleanupComplete')))}</td>"
        f"<td>{html.escape(json.dumps(item.get('zeroToleranceIncidents') or {}, ensure_ascii=False))}</td></tr>"
        for item in batch["attempts"]
    )
    body = f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>京东 65x5 验收报告</title>
<style>body{{font:14px sans-serif;margin:24px;color:#20242a}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:7px;text-align:left}}th{{background:#f4f6f7}}</style>
<h1>京东 65x5 验收报告</h1><p>批次：{html.escape(batch['id'])}</p><p>固定分母：{batch['plannedAttempts']}；已验证：{summary['verifiedAttempts']}；结论：{html.escape(batch['verificationStatus'])}</p>
<table><thead><tr><th>尝试</th><th>场景</th><th>状态</th><th>验证</th><th>阻塞依赖</th></tr></thead><tbody>{rows}</tbody></table></html>"""
    additions = (
        f"<h2>Counts</h2><table><tbody>{count_rows}</tbody></table>"
        f"<h2>Acceptance thresholds</h2><table><tbody>{threshold_rows}</tbody></table>"
        f"<h2>Attempt evidence</h2><table><thead><tr><th>Attempt</th><th>runId</th><th>evidenceCompleteness</th>"
        f"<th>stableReplay</th><th>amountAccurate</th><th>cleanupComplete</th><th>zeroToleranceIncidents</th>"
        f"</tr></thead><tbody>{detail_rows}</tbody></table>"
    )
    body = body.replace("</html>", additions + "</html>")
    return Response(body, media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{batch_id}.html"'})


def _acceptance_threshold_passed(name: str, threshold: dict) -> bool:
    if name == "zeroToleranceIncidents":
        return threshold["actual"] == threshold["required"]
    required = threshold["required"]
    if isinstance(required, float):
        return threshold["actual"] >= required
    return threshold["actual"] == required


@app.get("/api/bridge/cesium-reference")
def download_cesium_bridge_reference() -> FileResponse:
    target = Path(__file__).resolve().parents[1] / "bridge" / "cesium_reference.js"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Cesium Bridge 参考适配器不存在")
    return FileResponse(
        target,
        media_type="text/javascript",
        filename="cesium-bridge-reference.js",
    )


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [item.model_dump(mode="json", by_alias=True) for item in PROJECT_STORE.list()]


@app.get("/api/projects/{project_id}/file-assets")
def list_file_assets(project_id: str) -> list[dict]:
    if PROJECT_STORE.get(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        return FILE_ASSET_STORE.list(project_id)
    except FileAssetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/file-assets")
async def register_file_asset(project_id: str, request: Request) -> dict:
    if PROJECT_STORE.get(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    filename = request.headers.get("x-file-name", "")
    declared_sha256 = request.headers.get("x-file-sha256", "").lower()
    content = await request.body()
    try:
        record = FILE_ASSET_STORE.register(project_id, filename, content, declared_sha256)
    except FileAssetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    PROJECT_STORE.audit(AuditRecord(
        action="create", objectType="file_asset", objectId=record["sha256"][:16],
        projectId=project_id, changedFields=["sha256", "bytes", "filename"],
    ))
    return record


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
            allowPrivateNetwork=payload.allowPrivateNetwork,
            businessContext=payload.businessContext,
            commerceProfile=payload.commerceProfile,
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
            ("ignore_rules", "ignoreRules"),
            ("screenshot_mask_selectors", "screenshotMaskSelectors"), ("viewport", "viewport"),
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
            ("forbidden_actions", "forbiddenActions"), ("commerce_steps", "commerceSteps"),
            ("execution_steps", "executionSteps"),
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
    project = PROJECT_STORE.get(payload.projectId) if payload.projectId else None
    saved_scenario = _scenario_for_run(project, payload.scenarioId)
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
            business_context=(
                project.business_context.model_dump(mode="json", by_alias=True)
                if project else None
            ),
        )
    except AIProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    generated_plan = _apply_scenario_commerce(result.plan, saved_scenario)
    return {
        "plan": generated_plan.model_dump(mode="json", exclude_none=True),
        "warnings": [],
        "planner": f"ai:{result.protocol}:{result.model}",
        "elapsedMs": result.elapsed_ms,
    }


@app.post("/api/plans/generate")
def generate_plan(payload: DraftRequest) -> dict:
    target_url = _resolve_draft_target(payload.targetUrl, payload.projectId, payload.environmentId)
    project = PROJECT_STORE.get(payload.projectId) if payload.projectId else None
    saved_scenario = _scenario_for_run(project, payload.scenarioId)
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
    generated_plan = _apply_scenario_commerce(result.plan, saved_scenario)
    forbidden = tuple(dict.fromkeys([
        *(project.forbidden_actions if project else []),
        *(saved_scenario.forbidden_actions if saved_scenario else []),
        *_commerce_forbidden_actions(project),
    ]))
    _check_plan_forbidden_actions(generated_plan, forbidden)
    return {
        "plan": generated_plan.model_dump(mode="json", exclude_none=True),
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
            DomainPolicy(
                project.base_url,
                project.allowed_hosts,
                allow_private_network=project.allow_private_network,
            ).check_url(resolved_base_url)
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
    _validate_scenario_commerce(plan, scenario)
    forbidden = tuple(dict.fromkeys([
        *(project.forbidden_actions if project else []),
        *(scenario.forbidden_actions if scenario else []),
        *_commerce_forbidden_actions(project),
    ]))
    _check_plan_forbidden_actions(plan, forbidden)
    if not payload.asyncExecution and any(confirmation_match(step) for step in plan.steps):
        raise HTTPException(status_code=422, detail="危险动作必须使用后台运行并逐步完成人工确认")
    config = RunnerConfig(
        artifacts_root=ARTIFACTS_ROOT,
        headless=payload.headless,
        timeout_ms=payload.timeoutMs,
        allowed_hosts=tuple(project.allowed_hosts) if project else (),
        allow_private_network=project.allow_private_network if project else False,
        storage_state=storage_state,
        onboarding_level=project.onboarding_level if project else None,
        max_duration_seconds=project.limits.timeout_seconds if project else 600,
        project_id=project.id if project else None,
        environment_id=environment.id if environment else None,
        environment_updated_at=environment.updated_at if environment else None,
        environment_variables=tuple(environment.variables.items()) if environment else (),
        secret_refs=tuple(environment.secret_refs.items()) if environment else (),
        ignore_rules=tuple(environment.ignore_rules) if environment else (),
        screenshot_mask_selectors=_commerce_screenshot_masks(project, environment),
        viewport=(environment.viewport.width, environment.viewport.height) if environment else (1440, 960),
        device_scale_factor=environment.device_scale_factor if environment else 1.0,
        app_bridge_enabled=environment.app_bridge.enabled if environment else False,
        app_bridge_global_name=environment.app_bridge.global_name if environment else "__WEB_AI_TEST__",
        app_bridge_adapter=environment.app_bridge.adapter if environment else "generic",
        artifact_retention_days=environment.artifact_retention_days if environment else 30,
        scenario_id=scenario.id if scenario else None,
        scenario_updated_at=scenario.updated_at if scenario else None,
        forbidden_actions=forbidden,
        scenario_goal=scenario.goal if scenario else plan.name,
        file_assets=_file_asset_runner_options(project, plan),
        **_commerce_runner_options(project),
    )
    if payload.asyncExecution:
        return _run_payload(RUN_ORCHESTRATOR.start(plan, config))
    try:
        result = await run_in_threadpool(RUN_ORCHESTRATOR.run_blocking, plan, config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"隔离执行器启动失败：{exc}") from exc
    return _run_payload(result)


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
            DomainPolicy(
                project.base_url,
                project.allowed_hosts,
                allow_private_network=project.allow_private_network,
            ).check_url(resolved_base_url)
            storage_state = PROJECT_STORE.load_session(project.id)
            if storage_state and validate_storage_state(project, storage_state).expiry_status == "expired":
                raise HTTPException(status_code=422, detail="项目登录态已过期，请重新导入")
        except (SecurityError, SessionStateError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _check_environment_secret_refs(plan, environment)

    saved_scenario = _scenario_for_run(project, payload.scenarioId)
    _validate_scenario_commerce(plan, saved_scenario)
    scenario = AgentScenario(
        name=saved_scenario.name if saved_scenario else payload.scenario.name,
        goal=saved_scenario.goal if saved_scenario else payload.scenario.goal,
        preconditions="\n".join(saved_scenario.preconditions) if saved_scenario else payload.scenario.preconditions,
        test_data=saved_scenario.test_data if saved_scenario else payload.scenario.testData,
        expected_results=saved_scenario.expected_results if saved_scenario else payload.scenario.expectedResults,
        forbidden_actions=saved_scenario.forbidden_actions if saved_scenario else payload.scenario.forbiddenActions,
        business_context=(
            {
                **project.business_context.model_dump(mode="json", by_alias=True),
                "commerceProfile": project.commerce_profile.model_dump(mode="json", by_alias=True),
            }
            if project else {}
        ),
        bridge_config=(
            environment.app_bridge.model_dump(mode="json", by_alias=True)
            if environment else {"enabled": False}
        ),
    )
    forbidden = tuple(dict.fromkeys([
        *(project.forbidden_actions if project else []),
        *scenario.forbidden_actions,
        *_commerce_forbidden_actions(project),
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
        allow_private_network=project.allow_private_network if project else False,
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
        screenshot_mask_selectors=_commerce_screenshot_masks(project, environment),
        viewport=(environment.viewport.width, environment.viewport.height) if environment else (1440, 960),
        device_scale_factor=environment.device_scale_factor if environment else 1.0,
        app_bridge_enabled=environment.app_bridge.enabled if environment else False,
        app_bridge_global_name=environment.app_bridge.global_name if environment else "__WEB_AI_TEST__",
        app_bridge_adapter=environment.app_bridge.adapter if environment else "generic",
        artifact_retention_days=environment.artifact_retention_days if environment else 30,
        scenario_id=saved_scenario.id if saved_scenario else None,
        scenario_updated_at=saved_scenario.updated_at if saved_scenario else None,
        scenario_goal=scenario.goal,
        file_assets=_file_asset_runner_options(project, plan),
        **_commerce_runner_options(project),
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


def _apply_scenario_commerce(plan: TestPlan, scenario: ScenarioConfig | None) -> TestPlan:
    if scenario is None or (not scenario.commerce_steps and not scenario.execution_steps):
        return plan
    bindings = {item.step_index: item.commerce for item in scenario.commerce_steps}
    execution_bindings = {item.step_index: item for item in scenario.execution_steps}
    if max([*bindings, *execution_bindings], default=0) > len(plan.steps):
        raise HTTPException(status_code=422, detail="场景电商步骤号超出生成计划范围，请重新维护场景")
    steps = []
    for index, step in enumerate(plan.steps, start=1):
        updates = {}
        if index in bindings:
            updates["commerce"] = bindings[index]
        if index in execution_bindings:
            binding = execution_bindings[index]
            updates.update({
                "browser_target": binding.browser_target,
                "takeover_reason": binding.takeover_reason,
                "takeover_resume_locator": binding.takeover_resume_locator,
            })
            if binding.action == "human_takeover":
                updates.update({
                    "action": "human_takeover", "locator": None,
                    "stability_level": "D", "stability_reason": "受保护交互必须人工接管",
                })
        steps.append(Step.model_validate({**step.model_dump(), **updates}) if updates else step)
    return plan.model_copy(update={"steps": steps})


def _validate_scenario_commerce(plan: TestPlan, scenario: ScenarioConfig | None) -> None:
    if scenario is None:
        return
    for binding in scenario.commerce_steps:
        if binding.step_index > len(plan.steps):
            raise HTTPException(status_code=422, detail="当前计划缺少场景声明的电商步骤，请重新生成并审核")
        actual = plan.steps[binding.step_index - 1].commerce
        if actual != binding.commerce:
            raise HTTPException(status_code=422, detail="当前计划的电商安全语义与已保存场景不一致，请重新生成并审核")
    for binding in scenario.execution_steps:
        if binding.step_index > len(plan.steps):
            raise HTTPException(status_code=422, detail="当前计划缺少场景声明的浏览器上下文步骤，请重新生成并审核")
        actual = plan.steps[binding.step_index - 1]
        if actual.browser_target != binding.browser_target:
            raise HTTPException(status_code=422, detail="当前计划的窗口／iframe 语义与已保存场景不一致，请重新生成并审核")
        if binding.action == "human_takeover" and (
            actual.action.value != "human_takeover"
            or actual.takeover_reason != binding.takeover_reason
            or actual.takeover_resume_locator != binding.takeover_resume_locator
        ):
            raise HTTPException(status_code=422, detail="当前计划的人工接管语义与已保存场景不一致，请重新生成并审核")


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


_PRODUCTION_COMMERCE_HARD_FORBIDDEN = (
    "立即购买", "提交订单", "支付", "白条", "分期", "礼品卡", "领券", "兑换券",
    "新增地址", "编辑地址", "删除地址", "发票抬头", "确认收货", "评价", "晒单",
    "客服消息", "申请售后", "退款", "退货", "换货", "改价", "改库存", "上架", "下架",
)
_PRODUCTION_COMMERCE_REVERSIBLE = (
    "加入购物车", "删除购物车", "移入关注", "收藏", "取消收藏", "关注店铺", "取消关注",
)


def _commerce_forbidden_actions(project: ProjectConfig | None) -> tuple[str, ...]:
    if project is None or not project.commerce_profile.enabled:
        return ()
    profile = project.commerce_profile
    if profile.environment.value != "production_readonly":
        return ()
    values = list(_PRODUCTION_COMMERCE_HARD_FORBIDDEN)
    if not profile.production_reversible_write_authorized:
        values.extend(_PRODUCTION_COMMERCE_REVERSIBLE)
    return tuple(values)


def _commerce_screenshot_masks(
    project: ProjectConfig | None, environment: EnvironmentConfig | None
) -> tuple[str, ...]:
    return tuple(dict.fromkeys([
        *(environment.screenshot_mask_selectors if environment else []),
        *(project.commerce_profile.pii_mask_selectors if project and project.commerce_profile.enabled else []),
    ]))


def _commerce_runner_options(project: ProjectConfig | None) -> dict:
    if project is None:
        return {}
    profile = project.commerce_profile
    return {
        "commerce_enabled": profile.enabled,
        "commerce_environment": profile.environment.value,
        "commerce_account_ref": profile.account_ref,
        "commerce_production_reversible_write_authorized": profile.production_reversible_write_authorized,
        "commerce_sandbox_driver": profile.sandbox_driver,
        "commerce_e2e_resource_prefix": profile.e2e_resource_prefix,
    }


def _file_asset_runner_options(project: ProjectConfig | None, plan: TestPlan) -> tuple[tuple[str, str], ...]:
    refs = list(dict.fromkeys(
        step.file_asset_ref for step in plan.steps if step.file_asset_ref
    ))
    if not refs:
        return ()
    if project is None:
        raise HTTPException(status_code=422, detail="上传文件必须属于已保存项目")
    resolved = []
    for asset_ref in refs:
        try:
            path = FILE_ASSET_STORE.resolve(project.id, asset_ref)
        except FileAssetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        resolved.append((asset_ref, str(path)))
    return tuple(resolved)


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
    if payload.mode == "adaptive" and payload.settings is None:
        raise HTTPException(status_code=422, detail="自适应回放必须显式提供本次视觉模型设置")
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
    replay_scenario = _scenario_for_run(replay_project, previous.get("scenario_id"))
    _validate_scenario_commerce(plan, replay_scenario)
    replay_forbidden = tuple(dict.fromkeys([
        *(replay_project.forbidden_actions if replay_project else []),
        *(replay_scenario.forbidden_actions if replay_scenario else []),
        *_commerce_forbidden_actions(replay_project),
    ]))
    _check_plan_forbidden_actions(plan, replay_forbidden)
    replay_storage_state = None
    if replay_project:
        try:
            replay_storage_state = PROJECT_STORE.load_session(replay_project.id)
            if replay_storage_state and validate_storage_state(replay_project, replay_storage_state).expiry_status == "expired":
                raise HTTPException(status_code=422, detail="项目登录态已过期，请重新导入")
        except SessionStateError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    _check_environment_secret_refs(plan, replay_environment)
    config = RunnerConfig(
            artifacts_root=ARTIFACTS_ROOT,
            headless=payload.headless,
            replay_mode=payload.mode,
            project_id=replay_project.id if replay_project else None,
            allowed_hosts=tuple(replay_project.allowed_hosts) if replay_project else (),
            allow_private_network=replay_project.allow_private_network if replay_project else False,
            storage_state=replay_storage_state,
            onboarding_level=replay_project.onboarding_level if replay_project else previous.get("onboarding_level"),
            max_duration_seconds=replay_project.limits.timeout_seconds if replay_project else 600,
            environment_id=replay_environment.id if replay_environment else None,
            environment_updated_at=replay_environment.updated_at if replay_environment else None,
            environment_variables=tuple(replay_environment.variables.items()) if replay_environment else (),
            secret_refs=tuple(replay_environment.secret_refs.items()) if replay_environment else (),
            ignore_rules=tuple(replay_environment.ignore_rules) if replay_environment else (),
            screenshot_mask_selectors=_commerce_screenshot_masks(replay_project, replay_environment),
            viewport=(replay_environment.viewport.width, replay_environment.viewport.height) if replay_environment else (1440, 960),
            device_scale_factor=replay_environment.device_scale_factor if replay_environment else 1.0,
            app_bridge_enabled=replay_environment.app_bridge.enabled if replay_environment else False,
            app_bridge_global_name=replay_environment.app_bridge.global_name if replay_environment else "__WEB_AI_TEST__",
            app_bridge_adapter=replay_environment.app_bridge.adapter if replay_environment else "generic",
            artifact_retention_days=replay_environment.artifact_retention_days if replay_environment else 30,
            scenario_id=previous.get("scenario_id"),
            scenario_updated_at=previous.get("scenario_updated_at"),
            scenario_goal=previous.get("scenario_goal") or plan.name,
            file_assets=_file_asset_runner_options(replay_project, plan),
            forbidden_actions=replay_forbidden,
            agent_planner=AdaptiveReplayPlanner(plan) if payload.mode == "adaptive" else None,
            visual_adapter=OpenAIVisualAdapter(payload.settings.to_settings()) if payload.mode == "adaptive" and payload.settings else None,
            max_model_calls=max(4, len(plan.steps) * 2 + 1) if payload.mode == "adaptive" else 0,
            max_steps=len(plan.steps) + 1 if payload.mode == "adaptive" else None,
            **_commerce_runner_options(replay_project),
        )
    if any(confirmation_match(step) for step in plan.steps):
        return _run_payload(RUN_ORCHESTRATOR.start(plan, config))
    result = await run_in_threadpool(RUN_ORCHESTRATOR.run_blocking, plan, config)
    return _run_payload(result)


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
    try:
        ArtifactLifecycle(ARTIFACTS_ROOT).cleanup_expired(actor="system:list_runs")
    except ArtifactLifecycleError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [_run_payload(item) for item in RUN_ORCHESTRATOR.list(ARTIFACTS_ROOT)]


@app.post("/api/runs/delete")
def delete_runs(payload: RunDeleteRequest) -> dict:
    try:
        return ArtifactLifecycle(ARTIFACTS_ROOT).delete_runs(
            payload.runIds,
            action="manual_batch_delete",
            actor=payload.actor,
            reason="user requested deletion",
        )
    except ArtifactLifecycleError as exc:
        message = str(exc)
        status = 400 if "非法" in message or "保留目录" in message else 404 if "不存在" in message else 409
        raise HTTPException(status_code=status, detail=message) from exc


@app.post("/api/runs/cleanup")
def cleanup_expired_runs(payload: RunCleanupRequest) -> dict:
    try:
        return ArtifactLifecycle(ARTIFACTS_ROOT).cleanup_expired(actor=payload.actor)
    except ArtifactLifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/runs/deletion-audit")
def download_deletion_audit() -> Response:
    content = json.dumps(ArtifactLifecycle(ARTIFACTS_ROOT).read_audit(), ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="run-deletion-audit.json"'},
    )


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


@app.post("/api/runs/{run_id}/confirmation")
def decide_run_confirmation(run_id: str, payload: ConfirmationDecisionRequest) -> dict:
    _safe_run_dir(run_id)
    try:
        return _run_payload(RUN_ORCHESTRATOR.confirm(
            run_id,
            ARTIFACTS_ROOT,
            payload.confirmationId,
            payload.decision,
            payload.actor,
        ))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="运行记录不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
