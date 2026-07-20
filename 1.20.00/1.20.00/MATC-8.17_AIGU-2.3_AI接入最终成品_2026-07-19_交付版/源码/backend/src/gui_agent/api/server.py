"""连接 Web GUI 与真实 Playwright 执行器的 HTTP API。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, ValidationError

from ..domain.models import TestPlan
from ..execution import RunnerConfig, run_plan
from ..planning import PlanningError, plan_from_draft
from ..planning.ai_provider import AIProviderError, AISettings, plan_with_ai, test_connection


ARTIFACTS_ROOT = Path(os.getenv("GUI_AGENT_ARTIFACTS", "artifacts")).resolve()
ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="京彩OPC AI GUI 执行服务", version="0.3.0")
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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class DraftRequest(BaseModel):
    name: str
    targetUrl: str
    flow: str
    role: str | None = None
    preconditions: str | None = None
    expectation: str | None = None


class AISettingsRequest(BaseModel):
    protocol: str
    baseUrl: str
    model: str
    apiKey: SecretStr

    def to_settings(self) -> AISettings:
        if self.protocol not in {"responses", "chat_completions"}:
            raise AIProviderError("不支持的 API 协议")
        return AISettings(
            protocol=self.protocol,  # type: ignore[arg-type]
            base_url=self.baseUrl,
            model=self.model,
            api_key=self.apiKey,
        )


class AITestRequest(BaseModel):
    settings: AISettingsRequest


class AIPlanRequest(BaseModel):
    draft: DraftRequest
    settings: AISettingsRequest


class PlanRequest(BaseModel):
    plan: dict


class RunRequest(BaseModel):
    plan: dict
    headless: bool = True
    timeoutMs: int = Field(default=10_000, ge=1_000, le=120_000)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": "real",
        "engine": "playwright-chromium",
        "planner": "deterministic-rules + user-configured-ai",
        "aiConfigStorage": "request-memory-only",
        "artifacts": str(ARTIFACTS_ROOT),
    }


@app.post("/api/ai/test")
async def test_ai(payload: AITestRequest) -> dict:
    try:
        return await run_in_threadpool(test_connection, payload.settings.to_settings())
    except AIProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/ai/plans/generate")
async def generate_ai_plan(payload: AIPlanRequest) -> dict:
    draft = payload.draft
    try:
        result = await run_in_threadpool(
            plan_with_ai,
            settings=payload.settings.to_settings(),
            name=draft.name,
            target_url=draft.targetUrl,
            flow=draft.flow,
            role=draft.role,
            preconditions=draft.preconditions,
            expectation=draft.expectation,
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
    try:
        result = plan_from_draft(
            name=payload.name,
            target_url=payload.targetUrl,
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
    try:
        result, _ = await run_in_threadpool(
            run_plan,
            plan,
            RunnerConfig(
                artifacts_root=ARTIFACTS_ROOT,
                headless=payload.headless,
                timeout_ms=payload.timeoutMs,
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"执行器启动失败：{exc}") from exc
    return _run_payload(result.model_dump(mode="json"))


@app.get("/api/runs")
def list_runs() -> list[dict]:
    runs: list[dict] = []
    for path in sorted(ARTIFACTS_ROOT.glob("*/run.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            runs.append(_run_payload(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    return runs


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    path = _safe_run_dir(run_id) / "run.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return _run_payload(json.loads(path.read_text(encoding="utf-8")))


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
    run_id = payload.get("run_id", "")
    payload["artifact_base_url"] = f"/api/artifacts/{run_id}"
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
