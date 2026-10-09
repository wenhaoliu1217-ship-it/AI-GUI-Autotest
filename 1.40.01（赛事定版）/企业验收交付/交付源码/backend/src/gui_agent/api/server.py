"""连接 Web GUI 与真实 Playwright 执行器的 HTTP API。"""

from __future__ import annotations

import json
import os
import shutil
import html
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, ValidationError

from ..acceptance import (
    AcceptanceBatchManager,
    CompiledScenario,
    L4Orchestrator,
    ScenarioBindingError,
    compile_scenario,
    dry_run_executor,
    assert_control_plane_delivery,
    evaluate_control_plane_preflight,
    load_scenarios,
)
from ..commerce import (
    AcceptanceBatchError, AcceptanceBatchStore, CallbackObservation, CommerceActionRequest, CommerceAssuranceError, InventoryRaceEvidence,
    evaluate_callback_idempotency, evaluate_commerce_action, evaluate_inventory_race,
)
from ..benchmarks.cesium_ion import acceptance_payload, cesium_coverage_payload, site_map_payload
from ..benchmarks.cesium_ion.ledger import LedgerError, ResourceLedger
from ..benchmarks.cesium_ion.policy import CesiumPolicyError, is_cesium_target, policy_payload, validate_cesium_plan
from ..benchmarks.cesium_ion.test_data import readiness_payload
from ..domain.models import EffectLevel, Step, TestPlan
from ..artifacts import ArtifactLifecycle, ArtifactLifecycleError, FileAssetError, FileAssetStore
from ..execution import ActiveRunConflict, RunOrchestrator, RunnerConfig
from ..execution.container_runtime import DEFAULT_RUNNER_IMAGE, docker_engine_ready, docker_image_available, resolve_docker_cli
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
from ..onboarding.models import AccountProfile, AsyncStateMachine, BusinessObjectLifecycle, ComponentAdapter, SideEffectPolicy
from ..planning import AdaptiveReplayPlanner, AgentScenario, AIAgentPlanner, ExperienceJournal, OpenAIVisualAdapter, PlanningError, SuccessExperienceStore, plan_from_draft
from ..planning.task_authorization import derive_task_authorization
from ..planning.ai_provider import AIProviderError, AISettings, analyze_website_scope, plan_with_ai, probe_capabilities, test_connection
from ..playwright_runtime import BrowserRuntimeUnavailable
from ..playwright_runtime import normalize_browser_name
from ..platform_support import (
    detect_host_platform,
    playwright_browser_ready,
    resolve_playwright_browser_root,
)
from ..onboarding.url_resolution import resolve_public_url
from ..security.policy import DomainPolicy, SecurityError, resolve_env_placeholder
from ..site_capabilities import resolve_site_capability_pack
from ..version import API_CONTRACT_VERSION, APP_VERSION, FRONTEND_BUNDLE_VERSION


ARTIFACTS_ROOT = Path(os.getenv("GUI_AGENT_ARTIFACTS", "artifacts")).resolve()
ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
DATA_ROOT = Path(os.getenv("GUI_AGENT_DATA", "data")).resolve()
PROJECT_STORE = ProjectStore(DATA_ROOT / "projects")
SUCCESS_EXPERIENCE_STORE = SuccessExperienceStore(DATA_ROOT / "success-experiences")
try:
    EXPERIENCE_JOURNAL: ExperienceJournal | None = ExperienceJournal(
        DATA_ROOT / "experience-journal"
    )
except OSError:
    # Diagnostics must not prevent the API from starting in a read-only host
    # environment; normal project runs use the writable .local data root.
    EXPERIENCE_JOURNAL = None
FILE_ASSET_STORE = FileAssetStore(DATA_ROOT / "file-assets")
CESIUM_LEDGER = ResourceLedger(DATA_ROOT / "benchmarks" / "cesium-ion" / "resource-ledger.json")
LOGIN_RECORDINGS = LoginRecordingManager()
RUN_ORCHESTRATOR = RunOrchestrator()
# Older runs predate ExperienceJournal. Import their last persisted state once
# at startup so historical evidence is searchable without changing success
# promotion rules.
STARTUP_BACKFILLED_EXPERIENCE_RUNS = (
    EXPERIENCE_JOURNAL.backfill_from_artifacts(ARTIFACTS_ROOT)
    if EXPERIENCE_JOURNAL is not None
    else []
)
# A service restart empties the in-memory job registry. Reconcile persisted
# active runs before serving the UI so interrupted records remain visible and
# recoverable instead of looking permanently active or disappearing.
STARTUP_RECONCILED_RUNS = RUN_ORCHESTRATOR.reconcile(ARTIFACTS_ROOT)
JD_BENCHMARK_ROOT = Path(__file__).resolve().parents[3] / "benchmarks" / "jd"
JD_ACCEPTANCE_STORE = AcceptanceBatchStore(DATA_ROOT / "jd-acceptance", JD_BENCHMARK_ROOT / "scenarios")
GAE_BENCHMARK_ROOT = Path(__file__).resolve().parents[3] / "benchmarks" / "gaealavic"
GAE_ACCEPTANCE_BATCHES = AcceptanceBatchManager(DATA_ROOT / "gaealavic-acceptance-batches")
GAE_L4_RUNS_ROOT = (DATA_ROOT / "gaealavic-l4-runs").resolve()
GAE_L4_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
assert_control_plane_delivery(GAE_BENCHMARK_ROOT)


def _frontend_contract_status() -> dict[str, object]:
    default_static_dir = Path(__file__).resolve().parents[4] / "frontend-dist"
    static_dir = Path(os.getenv("GUI_STATIC_DIR", str(default_static_dir)))
    manifest_path = static_dir / "bundle-manifest.json"
    bundle_contract: str | None = None
    manifest_verified = False
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            bundle_contract = str(manifest.get("apiContractVersion") or "") or None
            manifest_verified = True
        except (OSError, ValueError, TypeError):
            manifest_verified = False
    compatible = manifest_verified and bundle_contract == API_CONTRACT_VERSION
    return {
        "apiContractVersion": API_CONTRACT_VERSION,
        "frontendApiContractVersion": bundle_contract,
        "frontendManifestVerified": manifest_verified,
        "contractCompatible": compatible,
    }


def _runtime_readiness() -> dict[str, object]:
    """Expose the real execution prerequisites separately from API health."""
    host_platform = detect_host_platform()
    docker_cli = resolve_docker_cli()
    docker_ready = docker_engine_ready(docker_cli, timeout=1.5) if docker_cli else False
    runner_mode = os.getenv("GUI_RUNNER_MODE", "container")
    image_ready = docker_image_available(DEFAULT_RUNNER_IMAGE, docker_cli, timeout=1.5) if docker_ready else False
    requested_browser = os.getenv("GUI_BROWSER", "chromium")
    try:
        browser_name = normalize_browser_name(requested_browser)
        browser_name_error = None
    except BrowserRuntimeUnavailable as exc:
        browser_name = requested_browser.strip().lower() or "chromium"
        browser_name_error = str(exc)
    browser_root = resolve_playwright_browser_root(
        Path(__file__).resolve().parents[4] / "runtime" / "ms-playwright"
    )
    browser_installed = playwright_browser_ready(browser_root, browser_name)
    browser_ready = browser_installed and (runner_mode != "container" or browser_name == "chromium")
    runtime_python = Path(sys.executable).is_file()
    if runner_mode == "container":
        runner_available = bool(docker_ready and image_ready and browser_ready and runtime_python)
        runner_reason = (
            "ready" if runner_available else
            "docker_cli_missing" if not docker_cli else
            "docker_engine_not_ready" if not docker_ready else
            "runner_image_missing" if not image_ready else
            "unsupported_browser" if browser_name_error else
            "browser_not_certified_in_runner" if not browser_ready else
            "playwright_browser_missing" if not browser_installed else
            "python_runtime_missing"
        )
    else:
        runner_available = bool(browser_ready and runtime_python)
        runner_reason = "ready" if runner_available else "playwright_browser_missing_or_python_runtime_missing"
    return {
        "runnerMode": runner_mode,
        "hostPlatform": host_platform.to_dict(),
        "browserName": browser_name,
        "browserInstalled": browser_installed,
        "browserReason": browser_name_error or ("runner_image_only_certifies_chromium" if runner_mode == "container" and browser_name != "chromium" else "ready" if browser_installed else "missing"),
        "runnerAvailable": runner_available,
        "runnerReason": runner_reason,
        "dockerCliPath": docker_cli,
        "dockerEngineReady": docker_ready,
        "runnerImage": DEFAULT_RUNNER_IMAGE,
        "runnerImageReady": image_ready,
        "runtimePythonReady": runtime_python,
        "runtimePythonPath": str(Path(sys.executable).resolve()),
        "playwrightBrowserReady": browser_ready,
        "playwrightBrowsersPath": str(browser_root) if browser_root else None,
    }

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


def _frontend_cache_headers(path: str) -> dict[str, str]:
    if path == "/" or path.endswith(".html") or path.startswith("/api/"):
        return {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
    if path.startswith("/assets/"):
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {}


@app.middleware("http")
async def static_resource_cache_policy(request: Request, call_next):
    """Prevent stale entry HTML while caching content-addressed assets."""
    response = await call_next(request)
    for key, value in _frontend_cache_headers(request.url.path).items():
        response.headers[key] = value
    return response


@app.exception_handler(ActiveRunConflict)
async def active_run_conflict_handler(_request: Request, exc: ActiveRunConflict) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": "active_run_conflict",
            "message": str(exc),
            "runId": exc.run_id,
            "activityKey": exc.activity_key,
        },
    )


def _enforce_cesium_policy(plan: TestPlan, target_url: str) -> None:
    try:
        validate_cesium_plan(plan, target_url, CESIUM_LEDGER.list())
    except (CesiumPolicyError, LedgerError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _classify_default_cesium_agent_plan(
    plan: TestPlan,
    *,
    generated_from_target: bool,
) -> None:
    if (
        is_cesium_target(plan.base_url)
        and generated_from_target
        and len(plan.steps) == 1
        and plan.steps[0].effect_kind is None
    ):
        plan.steps[0].effect_kind = "browse_search_filter_sort"
        plan.steps[0].effect_level = EffectLevel.READ_ONLY


def _cesium_runner_policy(target_url: str) -> tuple[bool, tuple[tuple[str, str, str], ...]]:
    if not is_cesium_target(target_url):
        return False, ()
    try:
        entries = CESIUM_LEDGER.list()
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return True, tuple(
        (str(item.get("resourceId", "")), str(item.get("name", "")), str(item.get("cleanupStatus", "")))
        for item in entries
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


class WebsiteUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4000)


class AIPlanRequest(BaseModel):
    draft: DraftRequest
    settings: AISettingsRequest
    projectId: str | None = None
    environmentId: str | None = None
    scenarioId: str | None = None


class PlanRequest(BaseModel):
    plan: dict


class CesiumLedgerRequest(BaseModel):
    runId: str
    caseId: str
    resourceType: str
    resourceId: str
    name: str
    accountContext: str = "personal_e2e_account"


class CesiumCleanupRequest(BaseModel):
    status: str
    evidence: list[str] = Field(default_factory=list)


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


class ModelDataAuthorizationRequest(BaseModel):
    # authorizedOrigin is authoritative for new clients. siteHost remains for
    # one release so existing packaged frontends fail safely instead of breaking.
    authorizedOrigin: str | None = Field(default=None, max_length=2_048)
    siteHost: str | None = Field(default=None, max_length=253)
    allowDom: bool = False
    allowScreenshots: bool = False
    authorizedBy: str = Field(default="local_user", min_length=1, max_length=120)


class AgentRunRequest(BaseModel):
    plan: dict | None = None
    targetUrl: str | None = None
    scenario: AgentScenarioRequest
    settings: AISettingsRequest
    headless: bool = True
    timeoutMs: int = Field(default=30_000, ge=1_000, le=120_000)
    projectId: str | None = None
    environmentId: str | None = None
    scenarioId: str | None = None
    decisionPolicy: Literal["mandatory_multimodal", "adaptive_multimodal"] = "mandatory_multimodal"
    # Deprecated wire compatibility field. It cannot disable a mandatory
    # multimodal policy and will be removed after packaged clients migrate.
    enableVisualFallback: bool | None = None
    approvalMode: Literal["ask", "delegate", "full"] = "ask"
    modelDataAuthorization: ModelDataAuthorizationRequest


class ReplayRequest(BaseModel):
    mode: str = "stable"
    headless: bool = True
    settings: AISettingsRequest | None = None


class ConfirmationDecisionRequest(BaseModel):
    confirmationId: str = Field(min_length=1, max_length=100)
    decision: str
    actor: str = Field(default="local_user", min_length=1, max_length=120)


class ClarificationAnswerRequest(BaseModel):
    clarificationId: str = Field(min_length=1, max_length=100)
    answer: str = Field(min_length=1, max_length=4000)
    actor: str = Field(default="local_user", min_length=1, max_length=120)


class LoginControlRequest(BaseModel):
    clarificationId: str = Field(min_length=1, max_length=100)
    kind: Literal["frame", "click", "text", "key", "scroll"]
    x: float = Field(default=0, ge=0, le=1)
    y: float = Field(default=0, ge=0, le=1)
    text: SecretStr = Field(default_factory=lambda: SecretStr(""), max_length=2048)
    key: str = Field(default="", max_length=32)
    dy: float = Field(default=0, ge=-800, le=800)


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


class GAEAcceptanceBindingRequest(BaseModel):
    projectId: str = Field(min_length=1, max_length=100)
    environmentId: str = Field(min_length=1, max_length=100)
    accountId: str = Field(min_length=1, max_length=64)
    stepBindings: dict[str, list[dict]] = Field(default_factory=dict)
    assertionBindings: dict[str, list[dict]] = Field(default_factory=dict)


class GAEAcceptanceScenarioBindingRequest(BaseModel):
    accountId: str = Field(min_length=1, max_length=64)
    stepBindings: dict[str, list[dict]] = Field(default_factory=dict)
    assertionBindings: dict[str, list[dict]] = Field(default_factory=dict)


class GAEAcceptanceBatchStartRequest(BaseModel):
    dryRun: bool = True
    projectId: str | None = Field(default=None, max_length=100)
    environmentId: str | None = Field(default=None, max_length=100)
    scenarioBindings: dict[str, GAEAcceptanceScenarioBindingRequest] = Field(default_factory=dict)


class GAEL4StageBindingRequest(BaseModel):
    accountId: str = Field(min_length=1, max_length=64)
    plan: dict
    outputPaths: dict[str, str] = Field(default_factory=dict)
    cleanupPlan: dict | None = None


class GAEL4RunRequest(BaseModel):
    dryRun: bool = True
    projectId: str | None = Field(default=None, max_length=100)
    environmentId: str | None = Field(default=None, max_length=100)
    stageBindings: dict[str, GAEL4StageBindingRequest] = Field(default_factory=dict)


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
    asyncStateMachines: list[AsyncStateMachine] = Field(default_factory=list)
    sideEffectPolicies: list[SideEffectPolicy] = Field(default_factory=list)
    componentAdapters: list[ComponentAdapter] = Field(default_factory=list)
    accountProfiles: list[AccountProfile] = Field(default_factory=list)


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
    asyncStateMachines: list[AsyncStateMachine] | None = None
    sideEffectPolicies: list[SideEffectPolicy] | None = None
    componentAdapters: list[ComponentAdapter] | None = None
    accountProfiles: list[AccountProfile] | None = None


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
    businessObjects: list[BusinessObjectLifecycle] = Field(default_factory=list)


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
    contract = _frontend_contract_status()
    runtime = _runtime_readiness()
    return {
        # Keep API health compatible with existing clients. Runner readiness is
        # reported independently so a healthy UI is never mistaken for a
        # runnable isolated test environment.
        "status": "ok" if contract["contractCompatible"] else "degraded",
        "appVersion": APP_VERSION,
        "frontendBundleVersion": FRONTEND_BUNDLE_VERSION,
        "versionSkew": FRONTEND_BUNDLE_VERSION != APP_VERSION,
        "versionCompatibility": (
            "supported_by_api_contract"
            if contract["contractCompatible"] else "unsupported_or_unverified"
        ),
        **contract,
        "mode": "real",
        "engine": "playwright-chromium",
        "planner": "mandatory-multimodal-stepwise-agent + run-local-recovery-contract + success-only-experience + deterministic-hard-safety",
        "decisionPolicy": "mandatory_multimodal",
        "formFastPath": {
            "enabled": os.getenv("GUI_AGENT_FORM_FAST_PATH", "1") != "0",
            "policy": "model_planned_verified_form_fills_v1",
            "maxFollowups": 3,
            "persistentActionsBatched": False,
        },
        "adaptiveRouter": "run_local_recovery_contract_enabled; cross_action_cache_enabled",
        "crossActionCache": {
            "enabled": True,
            "policy": "state_validated_advisory_v1",
            "successOnly": True,
            "requiresFreshGrounding": True,
        },
        "aiConfigStorage": "request-memory-only",
        "artifacts": str(ARTIFACTS_ROOT),
        "projectStorage": str(PROJECT_STORE.root),
        **runtime,
    }


@app.get("/api/preflight")
def preflight() -> dict:
    report = evaluate_control_plane_preflight(GAE_BENCHMARK_ROOT, readiness_payload())
    return report.model_dump(mode="json", by_alias=True)


@app.get("/api/acceptance/gaealavic/scenarios")
def gae_acceptance_scenarios() -> dict:
    """返回仿真业务固定 30 项验收底账；blocked 仍计入总分母。"""
    scenarios = load_scenarios(GAE_BENCHMARK_ROOT / "scenarios")
    blocked = sorted({dependency for item in scenarios for dependency in item.blocked_dependencies})
    return {
        "schemaVersion": "1",
        "scenarioCount": len(scenarios),
        "repeatCount": 5,
        "plannedRuns": len(scenarios) * 5,
        "runtimeBindingSupported": True,
        "readyCount": sum(item.binding_status == "ready" for item in scenarios),
        "blockedCount": sum(item.binding_status == "blocked" for item in scenarios),
        "blockedDependencies": blocked,
        "scenarios": [item.model_dump(mode="json", by_alias=True) for item in scenarios],
    }


@app.post("/api/acceptance/gaealavic/scenarios/{scenario_id}/bind")
def bind_gae_acceptance_scenario(scenario_id: str, payload: GAEAcceptanceBindingRequest) -> dict:
    scenarios = load_scenarios(GAE_BENCHMARK_ROOT / "scenarios")
    scenario = next((item for item in scenarios if item.id == scenario_id), None)
    if scenario is None:
        raise HTTPException(status_code=404, detail="仿真验收项目不存在")
    project = PROJECT_STORE.get(payload.projectId)
    environment = PROJECT_STORE.get_environment(payload.projectId, payload.environmentId) if project else None
    if project is None or environment is None:
        raise HTTPException(status_code=404, detail="内部网站配置或运行环境不存在")
    try:
        compiled = compile_scenario(
            scenario,
            project,
            environment,
            account_id=payload.accountId,
            step_bindings=payload.stepBindings,
            assertion_bindings=payload.assertionBindings,
            test_files=FILE_ASSET_STORE.list(payload.projectId),
        )
    except ScenarioBindingError as exc:
        return {"scenarioId": scenario.id, "bindingStatus": "blocked", "blockedDependencies": exc.blocked_items, "plan": None}
    return compiled.as_dict()


@app.get("/api/acceptance/gaealavic/batches")
def list_gae_acceptance_batches() -> list[dict]:
    return GAE_ACCEPTANCE_BATCHES.list()


@app.post("/api/acceptance/gaealavic/batches")
def start_gae_acceptance_batch(payload: GAEAcceptanceBatchStartRequest) -> dict:
    scenarios = load_scenarios(GAE_BENCHMARK_ROOT / "scenarios")
    if payload.dryRun:
        compiled = [
            CompiledScenario(
                scenario=item,
                plan=TestPlan(
                    name=f"{item.id} {item.name} 合同演练",
                    base_url="https://example.com",
                    steps=[Step(action="screenshot", description="只检查调度合同，不访问企业目标站")],
                ),
                account_id="contract-only",
                file_ids=(),
            )
            for item in scenarios
        ]
        return GAE_ACCEPTANCE_BATCHES.start(compiled, dry_run_executor, dry_run=True)
    if not payload.projectId or not payload.environmentId:
        raise HTTPException(status_code=422, detail="真实全面验收需要先完成网站、运行环境和账号的内部配置")
    project = PROJECT_STORE.get(payload.projectId)
    environment = PROJECT_STORE.get_environment(payload.projectId, payload.environmentId) if project else None
    if project is None or environment is None:
        raise HTTPException(status_code=404, detail="内部网站配置或运行环境不存在")
    compiled: list[CompiledScenario] = []
    blocked: list[str] = []
    test_files = FILE_ASSET_STORE.list(project.id)
    for scenario in scenarios:
        binding = payload.scenarioBindings.get(scenario.id)
        if binding is None:
            blocked.append(f"{scenario.id} 缺少运行时绑定")
            continue
        try:
            compiled.append(compile_scenario(
                scenario, project, environment, account_id=binding.accountId,
                step_bindings=binding.stepBindings, assertion_bindings=binding.assertionBindings,
                test_files=test_files,
            ))
        except ScenarioBindingError as exc:
            blocked.extend(f"{scenario.id}：{item}" for item in exc.blocked_items)
    if blocked:
        raise HTTPException(status_code=422, detail={"message": "真实全面验收所需条件尚未补齐", "blockedDependencies": blocked})

    def execute(compiled_scenario: CompiledScenario, _repeat: int) -> dict:
        _check_environment_secret_refs(compiled_scenario.plan, environment)
        return RUN_ORCHESTRATOR.run_blocking(compiled_scenario.plan, _gae_runner_config(compiled_scenario, project, environment))

    return GAE_ACCEPTANCE_BATCHES.start(compiled, execute, dry_run=False)


@app.get("/api/acceptance/gaealavic/batches/{batch_id}")
def get_gae_acceptance_batch(batch_id: str) -> dict:
    try:
        payload = GAE_ACCEPTANCE_BATCHES.read(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="仿真业务验收批次不存在")
    return payload


@app.post("/api/acceptance/gaealavic/batches/{batch_id}/{action}")
def control_gae_acceptance_batch(batch_id: str, action: str) -> dict:
    try:
        if action == "cancel":
            return GAE_ACCEPTANCE_BATCHES.cancel(batch_id)
        if action == "resume":
            return GAE_ACCEPTANCE_BATCHES.resume(batch_id)
        if action == "retry-failed":
            return GAE_ACCEPTANCE_BATCHES.retry_failed(batch_id)
        raise HTTPException(status_code=404, detail="不支持的批次操作")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="仿真业务验收批次不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _gae_batch_file(batch_id: str, filename: str) -> Path:
    target = (GAE_ACCEPTANCE_BATCHES.root / batch_id / filename).resolve()
    if GAE_ACCEPTANCE_BATCHES.root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="验收报告尚未生成")
    return target


@app.get("/api/acceptance/gaealavic/batches/{batch_id}/summary.json")
def download_gae_acceptance_summary(batch_id: str) -> FileResponse:
    return FileResponse(_gae_batch_file(batch_id, "acceptance-summary.json"), media_type="application/json", filename=f"{batch_id}-summary.json")


@app.get("/api/acceptance/gaealavic/batches/{batch_id}/report.md")
def download_gae_acceptance_report(batch_id: str) -> FileResponse:
    return FileResponse(_gae_batch_file(batch_id, "acceptance-report.md"), media_type="text/markdown", filename=f"{batch_id}-report.md")


@app.get("/api/acceptance/gaealavic/l4-workflow")
def gae_l4_workflow() -> dict:
    target = GAE_BENCHMARK_ROOT / "l4-workflow.json"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="跨模块完整流程合同不存在")
    return json.loads(target.read_text(encoding="utf-8"))


@app.post("/api/acceptance/gaealavic/l4-runs")
def start_gae_l4_run(payload: GAEL4RunRequest) -> dict:
    workflow = gae_l4_workflow()
    temporary = Path(tempfile.mkdtemp(prefix="gae-l4-", dir=GAE_L4_RUNS_ROOT))
    try:
        if payload.dryRun:
            result = L4Orchestrator().run(workflow, temporary, dry_run=True)
        else:
            if not payload.projectId or not payload.environmentId:
                raise HTTPException(status_code=422, detail="真实完整流程需要先完成网站和运行环境配置")
            project = PROJECT_STORE.get(payload.projectId)
            environment = PROJECT_STORE.get_environment(payload.projectId, payload.environmentId) if project else None
            if project is None or environment is None:
                raise HTTPException(status_code=404, detail="内部网站配置或运行环境不存在")
            missing = [stage["id"] for stage in workflow["stages"] if stage["id"] not in payload.stageBindings]
            if missing:
                raise HTTPException(status_code=422, detail=f"完整流程缺少阶段配置：{', '.join(missing)}")
            scenarios = load_scenarios(GAE_BENCHMARK_ROOT / "scenarios")
            executors: dict[str, object] = {}
            cleanup_executors: dict[str, object] = {}
            for stage in workflow["stages"]:
                stage_id = stage["id"]
                binding = payload.stageBindings[stage_id]
                try:
                    plan = TestPlan.model_validate(binding.plan)
                    cleanup_plan = TestPlan.model_validate(binding.cleanupPlan) if binding.cleanupPlan else None
                except ValidationError as exc:
                    raise HTTPException(status_code=422, detail=f"阶段 {stage_id} 的执行内容不合法：{_validation_message(exc)}") from exc
                scenario = next((item for item in scenarios if item.l4_stage == stage_id), scenarios[0])
                compiled = CompiledScenario(scenario=scenario, plan=plan, account_id=binding.accountId, file_ids=tuple(step.file_id for step in plan.steps if step.file_id))
                output_paths = dict(binding.outputPaths)

                def execute_stage(_context, current=compiled, paths=output_paths):
                    _check_environment_secret_refs(current.plan, environment)
                    run = RUN_ORCHESTRATOR.run_blocking(current.plan, _gae_runner_config(current, project, environment))
                    return {"status": "passed" if run.get("status") == "passed" else run.get("status", "failed"), "completionReason": run.get("completion_reason"), "outputs": {name: _gae_dotted_value(run, path) for name, path in paths.items()}}

                executors[stage_id] = execute_stage
                if cleanup_plan:
                    cleanup_compiled = CompiledScenario(scenario=scenario, plan=cleanup_plan, account_id=binding.accountId, file_ids=())

                    def cleanup_stage(_outputs, current=cleanup_compiled):
                        run = RUN_ORCHESTRATOR.run_blocking(current.plan, _gae_runner_config(current, project, environment))
                        return {"status": "deleted" if run.get("status") == "passed" else "failed", "runId": run.get("run_id"), "error": run.get("completion_reason")}

                    cleanup_executors[stage_id] = cleanup_stage
            result = L4Orchestrator().run(workflow, temporary, stage_executors=executors, cleanup_executors=cleanup_executors)
        target = GAE_L4_RUNS_ROOT / result["runId"]
        if target.exists():
            shutil.rmtree(target)
        temporary.replace(target)
        result["reportUrls"] = {
            "json": f"/api/acceptance/gaealavic/l4-runs/{result['runId']}/result.json",
            "markdown": f"/api/acceptance/gaealavic/l4-runs/{result['runId']}/report.md",
        }
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _gae_l4_file(run_id: str, filename: str) -> Path:
    target = (GAE_L4_RUNS_ROOT / run_id / filename).resolve()
    if GAE_L4_RUNS_ROOT not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="完整流程结果不存在")
    return target


@app.get("/api/acceptance/gaealavic/l4-runs/{run_id}/result.json")
def download_gae_l4_result(run_id: str) -> FileResponse:
    return FileResponse(_gae_l4_file(run_id, "l4-result.json"), media_type="application/json", filename=f"{run_id}-result.json")


@app.get("/api/acceptance/gaealavic/l4-runs/{run_id}/report.md")
def download_gae_l4_report(run_id: str) -> FileResponse:
    return FileResponse(_gae_l4_file(run_id, "l4-report.md"), media_type="text/markdown", filename=f"{run_id}-report.md")


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


@app.get("/api/bridge/gaealavic-cesium-adapter")
def download_gaealavic_cesium_adapter() -> FileResponse:
    target = Path(__file__).resolve().parents[1] / "bridge" / "gaealavic_cesium_adapter.js"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="仿真业务三维页面参考适配器不存在")
    return FileResponse(target, media_type="text/javascript", filename="gaealavic-cesium-adapter.js")


@app.get("/api/benchmarks/cesium-ion")
def cesium_acceptance_suite() -> dict:
    payload = acceptance_payload()
    payload["resourceLedger"] = CESIUM_LEDGER.summary()
    payload["testData"] = readiness_payload()
    payload["coverage"] = cesium_coverage_payload(ARTIFACTS_ROOT)
    return payload


@app.get("/api/benchmarks/cesium-ion/coverage")
def cesium_coverage() -> dict:
    """Return evidence-backed business and strict-WebGL acceptance gates."""
    return cesium_coverage_payload(ARTIFACTS_ROOT)


@app.get("/api/benchmarks/cesium-ion/site-map")
def cesium_site_map() -> dict:
    return site_map_payload()


@app.get("/api/benchmarks/cesium-ion/policy")
def cesium_policy() -> dict:
    return policy_payload()


@app.get("/api/benchmarks/cesium-ion/resources")
def cesium_resources(runId: str | None = None) -> dict:
    return {"summary": CESIUM_LEDGER.summary(), "resources": CESIUM_LEDGER.list(runId)}


@app.post("/api/benchmarks/cesium-ion/resources")
def register_cesium_resource(payload: CesiumLedgerRequest) -> dict:
    try:
        return CESIUM_LEDGER.register(payload.model_dump())
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/benchmarks/cesium-ion/resources/{ledger_id}/cleanup")
def record_cesium_cleanup(ledger_id: str, payload: CesiumCleanupRequest) -> dict:
    try:
        return CESIUM_LEDGER.record_cleanup(ledger_id, payload.status, payload.evidence)
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/projects")
def list_projects() -> list[dict]:
    projects = sorted(
        PROJECT_STORE.list(),
        key=_project_selection_quality,
        reverse=True,
    )
    return [item.model_dump(mode="json", by_alias=True) for item in projects]


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
            asyncStateMachines=payload.asyncStateMachines,
            sideEffectPolicies=payload.sideEffectPolicies,
            componentAdapters=payload.componentAdapters,
            accountProfiles=payload.accountProfiles or [AccountProfile(id="default", name="默认测试账号", role="tester")],
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


@app.get("/api/projects/{project_id}/business-context-status")
def get_business_context_status(project_id: str) -> dict:
    project = PROJECT_STORE.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    context = project.business_context
    blocked = list(context.missing_facts)
    blocked.extend(item.statement for item in context.facts if item.status == "blocked")
    blocked.extend(
        f"{item.source_object} {item.relation} {item.target_object}"
        for item in context.object_relations if item.status == "blocked"
    )
    confirmed = sum(item.status == "confirmed" for item in context.facts)
    confirmed += sum(item.status == "confirmed" for item in context.object_relations)
    return {
        "projectId": project_id,
        "status": "blocked" if blocked else "ready",
        "confirmedCount": confirmed,
        "blockedItems": blocked,
        "sourceRevision": context.source_revision,
    }


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


def _reusable_saved_session(project: ProjectConfig, store: ProjectStore):
    """Return validated saved session metadata when it is still usable."""
    try:
        state = store.load_session(project.id)
        saved = store.get_session_metadata(project.id)
        if state is None or saved is None:
            return None
        current = validate_storage_state(project, state)
        current.imported_at = saved.imported_at
        return None if current.expiry_status == "expired" else current
    except (OSError, SessionStateError, ValueError):
        return None


@app.post("/api/projects/{project_id}/session-recordings")
async def start_session_recording(project_id: str, payload: SessionRecordingRequest) -> dict:
    project = PROJECT_STORE.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        recording = await run_in_threadpool(LOGIN_RECORDINGS.start, project, PROJECT_STORE, payload.timeoutSeconds)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _session_recording_response(recording)


def _session_recording_response(recording) -> dict:
    return {
        "id": recording.id,
        "projectId": recording.project_id,
        "status": recording.status,
        "browserName": getattr(recording, "browser_name", None),
        "diagnostics": {
            "lastUrl": getattr(recording, "last_url", None),
            "failedRequests": list(getattr(recording, "failed_requests", [])),
            "policyRejections": [],
            "httpErrors": list(getattr(recording, "http_errors", [])),
            "recentResponses": list(getattr(recording, "recent_responses", [])),
            "pendingRequests": list(getattr(recording, "pending_requests", {}).values())[:20],
            "consoleErrors": getattr(recording, "console_errors", 0),
            "pageErrors": getattr(recording, "page_errors", 0),
            "reloadCount": getattr(recording, "reload_count", 0),
        },
    }


@app.get("/api/projects/{project_id}/session-recordings/{recording_id}")
def get_session_recording(project_id: str, recording_id: str) -> dict:
    project = PROJECT_STORE.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        recording = LOGIN_RECORDINGS.get(recording_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if recording.project_id != project_id:
        raise HTTPException(status_code=404, detail="登录录制不属于该项目")
    return _session_recording_response(recording)


@app.post("/api/projects/{project_id}/session-recordings/{recording_id}/reload")
async def reload_session_recording(project_id: str, recording_id: str) -> dict:
    project = PROJECT_STORE.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        recording = LOGIN_RECORDINGS.get(recording_id)
        if recording.project_id != project_id:
            raise HTTPException(status_code=404, detail="登录录制不属于该项目")
        recording = await run_in_threadpool(LOGIN_RECORDINGS.reload, recording_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _session_recording_response(recording)


@app.post("/api/projects/{project_id}/session-recordings/{recording_id}/complete")
async def complete_session_recording(project_id: str, recording_id: str) -> dict:
    project = PROJECT_STORE.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    recording = None
    try:
        recording = LOGIN_RECORDINGS.get(recording_id)
        if recording.project_id != project_id:
            raise HTTPException(status_code=404, detail="登录录制不属于该项目")
        recording = await run_in_threadpool(LOGIN_RECORDINGS.complete, recording_id)
    except (ValueError, RuntimeError) as exc:
        message = str(exc)
        saved_session = (
            _reusable_saved_session(project, PROJECT_STORE)
            if message == "登录录制不存在" or "登录页面仍在加载" in message
            else None
        )
        if saved_session is None:
            raise HTTPException(status_code=422, detail=message) from exc
        PROJECT_STORE.audit(AuditRecord(
            action="reuse", objectType="session", objectId=recording_id,
            projectId=project_id, changedFields=["existingStorageState"],
        ))
        return {
            "id": recording_id,
            "projectId": project_id,
            "status": "completed",
            "browserName": getattr(recording, "browser_name", None),
            "session": saved_session.model_dump(mode="json", by_alias=True),
            "reusedExistingSession": True,
        }
    PROJECT_STORE.audit(AuditRecord(action="record", objectType="session", objectId=recording_id, projectId=project_id, changedFields=["storageState"]))
    return {
        "id": recording.id,
        "projectId": project_id,
        "status": recording.status,
        "browserName": getattr(recording, "browser_name", None),
        "session": recording.result,
    }


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
    except BrowserRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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


@app.post("/api/ai/probe")
async def probe_ai(payload: AITestRequest) -> dict:
    try:
        result = await run_in_threadpool(probe_capabilities, payload.settings.to_settings())
        capabilities = result.get("capabilities") if isinstance(result, dict) else None
        if isinstance(capabilities, dict) and capabilities.get("vision") != "passed":
            # The UI treats a non-passing vision probe as a hard gate for the
            # multimodal Agent. Surface the precise, sanitized provider reason
            # instead of returning a successful-looking partial probe that the
            # compiled client renders only as "未验证".
            detail = capabilities.get("visionError") or (
                "视觉能力探针未通过：当前模型没有确认探针图片中的红色；"
                "请使用支持图片输入的模型，并确认网关兼容所选协议。"
            )
            raise HTTPException(status_code=422, detail=str(detail))
        return result
    except AIProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/websites/resolve")
async def resolve_website_url(payload: WebsiteUrlRequest) -> dict:
    try:
        parsed = urlparse(payload.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("请输入完整的 http:// 或 https:// 网站地址")
        saved_resolution = _resolve_saved_project_url(payload.url)
        if saved_resolution is not None:
            return saved_resolution
        return await run_in_threadpool(resolve_public_url, payload.url)
    except (SecurityError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/scope-analysis")
async def analyze_project_scope(project_id: str, payload: AITestRequest) -> dict:
    project = PROJECT_STORE.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    report = PROJECT_STORE.get_report(project_id)
    if report is None:
        raise HTTPException(status_code=409, detail="请先完成当前网站的只读扫描")
    try:
        return await run_in_threadpool(
            analyze_website_scope,
            payload.settings.to_settings(),
            report.model_dump(mode="json", by_alias=True),
        )
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
                resolve_site_capability_pack(target_url).effective_business_context(
                    project.business_context.model_dump(mode="json", by_alias=True)
                )
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
    _classify_default_cesium_agent_plan(
        generated_plan,
        generated_from_target=True,
    )
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
    _enforce_cesium_policy(plan, plan.base_url)
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
    resolved_base_url = plan.base_url
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
    _enforce_cesium_policy(plan, resolved_base_url)
    cesium_policy_enabled, cesium_owned_resources = _cesium_runner_policy(resolved_base_url)
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
        cesium_policy_enabled=cesium_policy_enabled,
        cesium_owned_resources=cesium_owned_resources,
        **_universal_runner_options(project, environment, scenario),
        **_commerce_runner_options(project),
    )
    if payload.asyncExecution:
        return _run_payload(RUN_ORCHESTRATOR.start(plan, config))
    try:
        result = await run_in_threadpool(RUN_ORCHESTRATOR.run_blocking, plan, config)
    except ActiveRunConflict:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"隔离执行器启动失败：{exc}") from exc
    return _run_payload(result)


def _effective_project_business_context(project: ProjectConfig | None, target_url: str) -> dict:
    if project is None:
        return {}
    # Stored project context belongs to the user. Site capability packs are
    # passed separately as advisory planner context and must not rewrite it.
    return {
        **project.business_context.model_dump(mode="json", by_alias=True),
        "commerceProfile": project.commerce_profile.model_dump(mode="json", by_alias=True),
    }


def _normalized_origin(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        raise ValueError("授权地址必须是完整的 http:// 或 https:// origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("授权地址端口无效") from exc
    default_port = 80 if scheme == "http" else 443
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{scheme}://{authority}"


def _auto_select_project_for_target(target_url: str) -> ProjectConfig | None:
    """Reuse an explicit saved site authorization for the quick-start form.

    Matching includes scheme, host, and port. Duplicate authorizations prefer
    the uniquely richer onboarding/business contract; equal candidates still
    fail closed instead of relying on recency or filesystem ordering.
    """
    try:
        target_origin = _normalized_origin(target_url)
    except ValueError:
        return None
    matches: list[ProjectConfig] = []
    for project in PROJECT_STORE.list():
        try:
            if _normalized_origin(project.base_url) == target_origin:
                matches.append(project)
        except ValueError:
            continue
    if len(matches) == 1:
        return matches[0]
    private_matches = [project for project in matches if project.allow_private_network]
    if len(private_matches) == 1:
        return private_matches[0]
    eligible = private_matches or matches
    if not eligible:
        return None
    scored = [(_project_selection_quality(project), project) for project in eligible]
    best_score = max(score for score, _project in scored)
    best = [project for score, project in scored if score == best_score]
    if len(best) == 1:
        return best[0]
    return None


def _promote_shallow_project_for_target(
    project: ProjectConfig, target_url: str
) -> ProjectConfig:
    """Replace a stale L0 duplicate with the unique richer exact-origin project."""

    if str(getattr(project, "onboarding_level", "L0")) != "L0":
        return project
    try:
        target_origin = _normalized_origin(target_url)
        if _normalized_origin(project.base_url) != target_origin:
            return project
        stored_projects = PROJECT_STORE.list()
    except (AttributeError, OSError, TypeError, ValueError):
        return project
    candidates: list[ProjectConfig] = []
    for item in stored_projects:
        try:
            same_origin = _normalized_origin(item.base_url) == target_origin
        except (AttributeError, TypeError, ValueError):
            continue
        preserves_private_access = (
            not bool(getattr(project, "allow_private_network", False))
            or bool(getattr(item, "allow_private_network", False))
        )
        if (
            getattr(item, "id", None) != getattr(project, "id", None)
            and same_origin
            and preserves_private_access
        ):
            candidates.append(item)
    if not candidates:
        return project
    selected_score = _project_selection_quality(project)
    scored = [(_project_selection_quality(item), item) for item in candidates]
    best_score = max(score for score, _item in scored)
    best = [item for score, item in scored if score == best_score]
    if len(best) == 1 and best_score > selected_score:
        return best[0]
    return project


def _project_selection_quality(project: Any) -> tuple[int, int, int]:
    """Rank exact-origin projects by durable test knowledge, never recency."""

    onboarding_rank = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}.get(
        str(getattr(project, "onboarding_level", "L0")),
        0,
    )
    context = getattr(project, "business_context", None)
    if hasattr(context, "model_dump"):
        context = context.model_dump(mode="json", by_alias=True)
    if not isinstance(context, dict):
        context = {}
    context_score = 0
    if str(context.get("description") or "").strip():
        context_score += 8
    for key in (
        "terminology",
        "stateModels",
        "objectTypes",
        "operatingBoundaries",
        "allowedActions",
        "facts",
        "objectRelations",
        "bridgeCapabilities",
    ):
        value = context.get(key)
        if isinstance(value, dict):
            context_score += min(len(value), 8)
        elif isinstance(value, list):
            context_score += min(len(value), 8)
    runtime_contract_score = sum(
        min(len(getattr(project, field, ()) or ()), 8)
        for field in (
            "async_state_machines",
            "side_effect_policies",
            "component_adapters",
        )
    )
    return onboarding_rank, context_score, runtime_contract_score


def _resolve_saved_project_url(target_url: str) -> dict | None:
    """Resolve an exact saved origin without applying the public-only probe.

    Private targets are reachable only through a project that has already
    recorded that exact scheme, host, and port plus explicit private-network
    authorization. Ambiguous saved origins deliberately fall back to the
    public resolver, which fails closed for private addresses.
    """
    project = _auto_select_project_for_target(target_url)
    if project is None or not project.allow_private_network:
        return None
    canonical = _canonical_project_target(target_url, project)
    DomainPolicy(
        project.base_url,
        list(project.allowed_hosts),
        allow_private_network=project.allow_private_network,
    ).check_url(canonical)
    return {
        "url": canonical,
        "changed": canonical != target_url,
        "redirectChain": [canonical],
        "authorizationProjectId": project.id,
        "resolutionMode": "saved_project_exact_origin",
    }


def _validate_model_data_authorization(
    target_url: str,
    authorization: ModelDataAuthorizationRequest,
) -> str:
    if not authorization.allowDom:
        raise HTTPException(
            status_code=422,
            detail="必须为当前目标网站单独授权模型接收脱敏 DOM 和无障碍树",
        )
    if not authorization.allowScreenshots:
        raise HTTPException(
            status_code=422,
            detail="必须为当前目标网站单独授权模型接收脱敏截图",
        )
    try:
        target_origin = _normalized_origin(target_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if authorization.authorizedOrigin:
        try:
            authorized_origin = _normalized_origin(authorization.authorizedOrigin)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if authorized_origin != target_origin:
            raise HTTPException(
                status_code=422,
                detail=(
                    "模型数据授权 origin 与实际目标不一致："
                    f"授权 {authorized_origin}，实际 {target_origin}"
                ),
            )
        return target_origin
    legacy_host = (authorization.siteHost or "").strip().lower()
    target_host = (urlparse(target_url).hostname or "").lower()
    if not legacy_host or legacy_host != target_host:
        raise HTTPException(
            status_code=422,
            detail="旧版站点授权与实际目标主机不一致，请重新扫描当前网站",
        )
    return target_origin


@app.post("/api/agent-runs")
def execute_agent_run(payload: AgentRunRequest) -> dict:
    try:
        plan_payload = dict(payload.plan) if payload.plan else {
            "name": payload.scenario.name or payload.scenario.goal[:80],
            "base_url": payload.targetUrl,
            "steps": [{"action": "navigate", "target": "/", "description": "Agent 初始导航契约"}],
            "assertions": [],
        }
        if not plan_payload.get("base_url"):
            raise ValueError("逐步 Agent 需要 targetUrl 或 plan.base_url")
        plan_payload["base_url"] = _resolve_draft_target(
            str(plan_payload["base_url"]), payload.projectId, payload.environmentId
        )
        plan = TestPlan.model_validate(plan_payload)
        _classify_default_cesium_agent_plan(
            plan,
            generated_from_target=payload.plan is None,
        )
        settings = payload.settings.to_settings().validated()
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AIProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    target_url = plan.base_url
    authorized_origin = _validate_model_data_authorization(
        target_url, payload.modelDataAuthorization
    )
    if payload.decisionPolicy == "adaptive_multimodal":
        raise HTTPException(
            status_code=422,
            detail="自适应多模态决策路由尚未部署；当前版本只能使用 mandatory_multimodal",
        )
    if payload.enableVisualFallback is False:
        raise HTTPException(
            status_code=422,
            detail="旧版视觉开关不能关闭 mandatory_multimodal 决策策略",
        )

    project = None
    environment = None
    storage_state = None
    effective_project_id = payload.projectId
    if not effective_project_id:
        project = _auto_select_project_for_target(target_url)
        effective_project_id = project.id if project else None
    if payload.environmentId and not effective_project_id:
        raise HTTPException(status_code=422, detail="使用测试环境时必须同时指定所属项目")
    if effective_project_id:
        project = project or PROJECT_STORE.get(effective_project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        # The compiled client can retain an obsolete L0 project id in memory.
        # Upgrade only an exact-origin shallow duplicate. Project-scoped
        # environment/scenario selections remain authoritative.
        if payload.environmentId is None and payload.scenarioId is None:
            project = _promote_shallow_project_for_target(project, target_url)
            effective_project_id = project.id
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
        # The current run request is authoritative. A selected saved scenario
        # may supply non-permission test data only when this request omitted it;
        # it must never replace the user's latest goal or safety boundaries.
        name=payload.scenario.name or (saved_scenario.name if saved_scenario else ""),
        goal=payload.scenario.goal,
        preconditions=payload.scenario.preconditions,
        test_data=(
            payload.scenario.testData
            if payload.scenario.testData
            else saved_scenario.test_data if saved_scenario else {}
        ),
        expected_results=payload.scenario.expectedResults,
        forbidden_actions=payload.scenario.forbiddenActions,
        business_context=_effective_project_business_context(project, target_url),
        bridge_config=(
            environment.app_bridge.model_dump(mode="json", by_alias=True)
            if environment else {"enabled": False}
        ),
    )
    task_authorization = derive_task_authorization(scenario)
    project_forbidden_actions = list(project.forbidden_actions) if project else []
    if task_authorization.create_allowed:
        project_forbidden_actions = [
            item for item in project_forbidden_actions
            if not _is_legacy_creation_restriction(item)
        ]
    forbidden = tuple(dict.fromkeys([
        *project_forbidden_actions,
        *scenario.forbidden_actions,
        *_commerce_forbidden_actions(project),
    ]))
    limits = project.limits if project else ProjectLimits()
    try:
        planner_base_url = resolve_env_placeholder(plan.base_url, environment.variables if environment else None)
    except SecurityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _enforce_cesium_policy(plan, planner_base_url)
    cesium_policy_enabled, cesium_owned_resources = _cesium_runner_policy(planner_base_url)
    successful_experiences = SUCCESS_EXPERIENCE_STORE.retrieve(
        planner_base_url, scenario.goal
    )
    planner = AIAgentPlanner(
        settings,
        scenario,
        planner_base_url,
        visual_enabled=True,
        successful_experiences=successful_experiences,
    )
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
        decision_policy=payload.decisionPolicy,
        max_model_calls=limits.max_model_calls,
        max_steps=limits.max_steps,
        no_progress_limit=3,
        forbidden_actions=forbidden,
        visual_adapter=OpenAIVisualAdapter(settings),
        success_experience_root=SUCCESS_EXPERIENCE_STORE.root,
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
        approval_mode=payload.approvalMode,
        file_assets=_file_asset_runner_options(project, plan),
        cesium_policy_enabled=cesium_policy_enabled,
        cesium_owned_resources=cesium_owned_resources,
        model_data_authorization={
            "authorizedOrigin": authorized_origin,
            "siteHost": (urlparse(target_url).hostname or "").lower(),
            "allowDom": True,
            "allowScreenshots": bool(payload.modelDataAuthorization.allowScreenshots),
            "authorizedBy": payload.modelDataAuthorization.authorizedBy,
            "authorizedAt": datetime.now(timezone.utc).isoformat(),
        },
        **_universal_runner_options(project, environment, saved_scenario),
        **_commerce_runner_options(project),
    )
    return _run_payload(RUN_ORCHESTRATOR.start(plan, config))


def _environment_snapshot(environment: EnvironmentConfig | None) -> dict:
    if environment is None:
        return {"environmentId": None, "variableNames": [], "secretAliases": []}
    return {
        "environmentId": environment.id,
        "name": environment.name,
        "variableNames": sorted(environment.variables),
        "secretAliases": sorted(environment.secret_refs),
        "updatedAt": environment.updated_at,
    }


def _universal_runner_options(
    project: ProjectConfig | None,
    environment: EnvironmentConfig | None,
    scenario: ScenarioConfig | None,
) -> dict:
    if project is None:
        return {
            "environment_snapshot": _environment_snapshot(environment),
        }
    account = project.account_profiles[0] if project.account_profiles else None
    business_context = project.business_context.model_dump(mode="json", by_alias=True)
    site_defaults = resolve_site_capability_pack(project.base_url).default_side_effect_policies()
    configured_policies = [
        item.model_dump(mode="json", by_alias=True) for item in project.side_effect_policies
    ]
    # Reserved site policies are code-owned. A project saved with an older
    # policy must not silently reintroduce the historical conditional-update
    # rule that blocked legitimate 3D edits. Custom policies remain available
    # for non-reserved IDs.
    site_default_ids = {str(item.get("id")) for item in site_defaults}
    merged_policies = [
        *site_defaults,
        *(item for item in configured_policies if str(item.get("id")) not in site_default_ids),
    ]
    return {
        "async_state_machines": tuple(
            item.model_dump(mode="json", by_alias=True) for item in project.async_state_machines
        ),
        "side_effect_policies": tuple(merged_policies),
        "component_adapters": tuple(
            item.model_dump(mode="json", by_alias=True) for item in project.component_adapters
        ),
        "business_objects": tuple(
            item.model_dump(mode="json", by_alias=True) for item in (scenario.business_objects if scenario else [])
        ),
        "account_id": account.id if account else None,
        "account_role": account.role if account else None,
        "project_snapshot": {
            "id": project.id,
            "name": project.name,
            "baseUrl": project.base_url,
            "allowedHosts": project.allowed_hosts,
            "onboardingLevel": project.onboarding_level,
            "updatedAt": project.updated_at,
        },
        "environment_snapshot": _environment_snapshot(environment),
        "business_context_snapshot": business_context,
        "app_map_snapshot": {
            "componentAdapters": [
                {
                    "id": item.id,
                    "module": item.module,
                    "page": item.page,
                    "status": item.status,
                    "source": item.source,
                    "blockedReason": item.blocked_reason,
                }
                for item in project.component_adapters
            ],
            "sourceRevision": project.business_context.source_revision,
        },
    }


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
        resolved = resolve_env_placeholder(target_url, environment.variables if environment else None)
        return _canonical_project_target(resolved, project)
    except SecurityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _canonical_project_target(target_url: str, project: ProjectConfig | None) -> str:
    """Use the selected project's authority when the UI guessed the wrong scheme."""
    if project is None:
        return target_url
    target = urlparse(target_url)
    configured = urlparse(project.base_url)
    if not target.hostname or not configured.hostname:
        return target_url
    if target.hostname.lower() != configured.hostname.lower():
        return target_url
    try:
        target_port = target.port
        configured_port = configured.port
    except ValueError:
        return target_url
    if target_port != configured_port and (
        target_port is not None or configured_port is not None
    ):
        return target_url
    return target._replace(scheme=configured.scheme, netloc=configured.netloc).geturl()


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


def _gae_runner_config(
    compiled: CompiledScenario,
    project: ProjectConfig,
    environment: EnvironmentConfig,
) -> RunnerConfig:
    """把已审核的仿真场景绑定到 1.32.01 通用执行器，不在基准中固化账号或选择器。"""
    account = next((item for item in project.account_profiles if item.id == compiled.account_id), None)
    if account is None:
        raise HTTPException(status_code=422, detail=f"账号槽位不存在：{compiled.account_id}")
    try:
        storage_state = PROJECT_STORE.load_session(project.id)
        if storage_state and validate_storage_state(project, storage_state).expiry_status == "expired":
            raise HTTPException(status_code=422, detail="网站登录状态已过期，请重新登录")
    except SessionStateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    forbidden = tuple(dict.fromkeys(project.forbidden_actions))
    _check_plan_forbidden_actions(compiled.plan, forbidden)
    universal = _universal_runner_options(project, environment, None)
    universal["account_id"] = compiled.account_id
    universal["account_role"] = account.role
    return RunnerConfig(
        artifacts_root=ARTIFACTS_ROOT,
        headless=False,
        timeout_ms=min(120_000, project.limits.timeout_seconds * 1000),
        allowed_hosts=tuple(project.allowed_hosts),
        allow_private_network=project.allow_private_network,
        storage_state=storage_state,
        onboarding_level=project.onboarding_level,
        max_duration_seconds=project.limits.timeout_seconds,
        project_id=project.id,
        environment_id=environment.id,
        environment_updated_at=environment.updated_at,
        environment_variables=tuple(environment.variables.items()),
        secret_refs=tuple(environment.secret_refs.items()),
        ignore_rules=tuple(environment.ignore_rules),
        screenshot_mask_selectors=tuple(environment.screenshot_mask_selectors),
        viewport=(environment.viewport.width, environment.viewport.height),
        device_scale_factor=environment.device_scale_factor,
        app_bridge_enabled=environment.app_bridge.enabled,
        app_bridge_global_name=environment.app_bridge.global_name,
        app_bridge_adapter=environment.app_bridge.adapter,
        artifact_retention_days=environment.artifact_retention_days,
        scenario_id=compiled.scenario.id,
        forbidden_actions=forbidden,
        scenario_goal=compiled.scenario.goal,
        file_assets=_file_asset_runner_options(project, compiled.plan),
        **universal,
    )


def _gae_dotted_value(payload: dict, path: str):
    current: object = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _validation_message(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors(include_context=False)[:8]:
        message = str(error.get("msg", "配置不合法"))
        messages.append(message.removeprefix("Value error, "))
    return "；".join(dict.fromkeys(messages))


_PRODUCTION_COMMERCE_HARD_FORBIDDEN = (
    "支付", "确认支付", "付款", "白条", "分期", "礼品卡", "领券", "兑换券",
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
    order_controls_ready = all((
        profile.account_ref,
        profile.fixed_product_ref,
        profile.fixed_address_ref,
        profile.production_reversible_write_authorized,
        profile.written_authorization_ref,
        profile.automatic_cancellation_verified,
    ))
    if not order_controls_ready:
        values.extend(("立即购买", "提交订单"))
    if not profile.production_reversible_write_authorized:
        values.extend(_PRODUCTION_COMMERCE_REVERSIBLE)
    return tuple(values)


def _is_legacy_creation_restriction(value: str) -> bool:
    """Let an explicit current-run grant override only old project create bans."""

    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    return any(term in normalized for term in ("创建", "新建", "create", "creation"))


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
        "commerce_fixed_product_ref": profile.fixed_product_ref,
        "commerce_fixed_address_ref": profile.fixed_address_ref,
        "commerce_written_authorization_ref": profile.written_authorization_ref,
        "commerce_automatic_cancellation_verified": profile.automatic_cancellation_verified,
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
    discovered_plan_path = run_dir / "discovered-plan.json"
    plan_path = (
        reviewed_plan_path
        if reviewed_plan_path.is_file()
        else discovered_plan_path
        if discovered_plan_path.is_file()
        else run_dir / "plan.json"
    )
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
    replay_base_url = resolve_env_placeholder(
        plan.base_url,
        replay_environment.variables if replay_environment else None,
    )
    _enforce_cesium_policy(plan, replay_base_url)
    cesium_policy_enabled, cesium_owned_resources = _cesium_runner_policy(replay_base_url)
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
            cesium_policy_enabled=cesium_policy_enabled,
            cesium_owned_resources=cesium_owned_resources,
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
    return [_run_list_payload(item) for item in RUN_ORCHESTRATOR.list(ARTIFACTS_ROOT)]


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


@app.get("/api/runs/{run_id}/progress")
def get_run_progress(
    run_id: str,
    after_step: int = 0,
    after_model_call: int = 0,
    after_confirmation: int = 0,
) -> dict:
    """Return a cursor-based live delta instead of retransmitting all evidence."""

    _safe_run_dir(run_id)
    payload = RUN_ORCHESTRATOR.read(run_id, ARTIFACTS_ROOT)
    if payload is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return _run_progress_payload(
        payload,
        after_step=max(0, after_step),
        after_model_call=max(0, after_model_call),
        after_confirmation=max(0, after_confirmation),
    )


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


@app.post("/api/runs/{run_id}/clarification")
def answer_run_clarification(run_id: str, payload: ClarificationAnswerRequest) -> dict:
    _safe_run_dir(run_id)
    try:
        return _run_payload(RUN_ORCHESTRATOR.answer_clarification(
            run_id,
            ARTIFACTS_ROOT,
            payload.clarificationId,
            payload.answer,
            payload.actor,
        ))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="运行记录不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/login-control")
def run_login_control(run_id: str, payload: LoginControlRequest, request: Request) -> Response:
    # Unlike normal diagnostics, manual input must only come from this GUI origin.
    if request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="登录接管仅允许当前 GUI 页面访问")
    _safe_run_dir(run_id)
    command = payload.model_dump(exclude={"clarificationId", "text"})
    command["text"] = payload.text.get_secret_value()
    try:
        result = RUN_ORCHESTRATOR.login_control(run_id, payload.clarificationId, command)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        command.pop("text", None)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


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
    completion_gate = payload.get("completion_gate")
    if isinstance(completion_gate, dict) and completion_gate.get("goal_status"):
        payload["goal_status"] = completion_gate["goal_status"]
    elif payload.get("goal_status") == "achieved":
        payload["goal_status"] = "incomplete"
        payload["goal_summary"] = "缺少 CompletionGateResult，拒绝展示为 achieved"
    elif not payload.get("goal_status"):
        payload["goal_status"] = (
            "in_progress" if status in {"queued", "running"} else
            "incomplete" if status in {"passed", "cancelled", "system_error", "error", "incomplete"} else
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


def _run_progress_payload(
    payload: dict,
    *,
    after_step: int = 0,
    after_model_call: int = 0,
    after_confirmation: int = 0,
) -> dict:
    """Build a UI-compatible active-run delta with monotonic cursors."""

    item = _run_payload(payload)
    steps = item.get("steps") if isinstance(item.get("steps"), list) else []
    model_calls = (
        item.get("model_call_records")
        if isinstance(item.get("model_call_records"), list)
        else []
    )
    confirmations = (
        item.get("confirmation_history")
        if isinstance(item.get("confirmation_history"), list)
        else []
    )

    def record_index(record: object) -> int:
        if not isinstance(record, dict):
            return 0
        try:
            return int(record.get("index") or 0)
        except (TypeError, ValueError):
            return 0

    keep = {
        "run_id", "plan_name", "role", "status", "started_at", "ended_at",
        "artifact_base_url", "replay_mode", "onboarding_level", "stability_level",
        "completion_reason", "project_id", "environment_id", "environment_updated_at",
        "artifact_retention_days", "runner_isolation", "duration_ms", "scenario_goal",
        "goal_status", "goal_summary", "review_summary", "model_calls",
        "successful_model_calls", "model_recovery_attempts", "estimated_cost",
        "input_tokens", "output_tokens", "pending_confirmation", "pending_clarification",
        "system_error", "result_classification",
    }
    delta = {key: value for key, value in item.items() if key in keep}
    delta.update({
        "steps": [record for record in steps if record_index(record) > after_step],
        "model_call_records": [
            record for record in model_calls if record_index(record) > after_model_call
        ],
        "confirmation_history": confirmations[after_confirmation:],
        # These collections are small during execution and may change without
        # an append-only index. Sending their latest value keeps the delta
        # protocol simple while the large evidence graphs stay cursor-based.
        "assertions": item.get("assertions") if isinstance(item.get("assertions"), list) else [],
        "reproduction_steps": item.get("reproduction_steps") if isinstance(item.get("reproduction_steps"), list) else [],
        "cause_hints": item.get("cause_hints") if isinstance(item.get("cause_hints"), list) else [],
        "findings": item.get("findings") if isinstance(item.get("findings"), list) else [],
        "delta": True,
        "next_step_index": max((record_index(record) for record in steps), default=after_step),
        "next_model_call_index": max(
            (record_index(record) for record in model_calls), default=after_model_call
        ),
        "next_confirmation_offset": len(confirmations),
    })
    return delta


def _run_list_payload(payload: dict) -> dict:
    """Return a UI-compatible history row without loading every run's evidence graph."""
    item = _run_payload(payload)
    status = str(item.get("status") or "incomplete")
    active = status in {
        "queued", "running", "pending_confirmation", "waiting_for_clarification",
    }
    if active:
        return item
    keep = {
        "run_id", "plan_name", "role", "status", "started_at", "ended_at",
        "replay_mode", "onboarding_level", "stability_level", "completion_reason",
        "project_id", "environment_id", "environment_updated_at",
        "artifact_retention_days", "scenario_id", "scenario_goal", "goal_status",
        "goal_summary", "model_calls", "successful_model_calls", "model_recovery_attempts",
        "estimated_cost", "input_tokens", "output_tokens",
        "result_classification", "runner_isolation", "runtime_limit_seconds", "duration_ms", "review_summary",
        "artifact_base_url", "system_error",
    }
    summary = {key: value for key, value in item.items() if key in keep}
    # The current prebuilt UI maps these arrays before rendering a history row.
    # Empty arrays retain compatibility; full data remains available via GET /api/runs/{id}.
    for key in (
        "steps", "assertions", "reproduction_steps", "cause_hints", "findings",
        "model_call_records", "clarification_history", "confirmation_history",
    ):
        summary[key] = []
    summary.update({
        "generated_test": None,
        "commerce_summary": None,
        "pending_confirmation": None,
        "pending_clarification": None,
        "detail_endpoint": f"/api/runs/{item.get('run_id', '')}",
        "summary_only": True,
    })
    return summary


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
