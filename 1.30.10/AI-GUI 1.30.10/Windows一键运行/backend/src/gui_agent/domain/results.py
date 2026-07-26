"""运行结果领域模型。

与 models.py（计划输入）分开：这里描述"执行发生了什么"，
是 execution 层的输出、artifacts 层的输入。不依赖 Playwright。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    """步骤/断言/整体运行的统一状态。"""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"      # 非断言失败的异常：定位不到、超时、网络、安全拒绝
    SKIPPED = "skipped"  # 前序失败导致未执行
    QUEUED = "queued"
    RUNNING = "running"
    PENDING_CONFIRMATION = "pending_confirmation"
    ISSUES_FOUND = "issues_found"
    INCOMPLETE = "incomplete"
    SYSTEM_ERROR = "system_error"
    CANCELLED = "cancelled"


class FailureCategory(str, Enum):
    """失败分类，用于区分报告与启发式原因提示。"""

    ASSERTION = "assertion"        # 断言不成立（页面状态与预期不符）
    LOCATOR = "locator"            # 元素定位失败
    TIMEOUT = "timeout"            # 超时
    NAVIGATION = "navigation"      # 导航/网络错误
    SECURITY = "security"          # 安全策略拒绝
    MODEL = "model"                # 模型不可用/输出异常
    UNKNOWN = "unknown"
    ASYNC_STATE = "async_state"
    CLEANUP = "cleanup"


class Observation(BaseModel):
    """A bounded set of browser facts captured at one point in time."""

    url: str = "about:blank"
    title: str = ""
    screenshot: Optional[str] = None
    dom_summary: list[str] = Field(default_factory=list)
    accessibility_summary: str = ""
    console_errors: list[str] = Field(default_factory=list)
    page_errors: list[str] = Field(default_factory=list)
    failed_requests: list[str] = Field(default_factory=list)
    page_issues: list["PageIssue"] = Field(default_factory=list)
    page_health: Optional["PageHealth"] = None
    captured_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


class PageIssue(BaseModel):
    """浏览器启发式发现的可复核页面信号，不直接宣判为产品缺陷。"""

    kind: str
    severity: str = "Medium"
    confidence: str = "medium"
    message: str
    target: str = ""
    details: dict = Field(default_factory=dict)


class PageHealth(BaseModel):
    ready_state: str = ""
    visible_text_length: int = 0
    visible_element_count: int = 0
    interactive_count: int = 0
    visual_surface_count: int = 0


class StepResult(BaseModel):
    """单步执行结果。"""

    index: int
    action: str
    description: Optional[str] = None
    target_summary: str = Field(description="脱敏后的动作目标描述")
    status: Status
    started_at: datetime
    ended_at: datetime
    error_message: Optional[str] = None
    failure_category: Optional[FailureCategory] = None
    screenshot: Optional[str] = Field(default=None, description="相对 run 目录的截图路径")
    execution_mode: str = Field(default="locator", description="locator/visual/app_bridge")
    locator_basis: Optional[str] = None
    stability_level: str = Field(default="A", description="A/B/C/D")
    stability_reason: str = "确定性 Playwright 动作"
    computer_use_triggered: bool = False
    computer_use_reason: Optional[str] = None
    coordinate_source: Optional[str] = None
    app_bridge_result: Optional[dict] = None
    stability_evidence: Optional[dict] = None
    canvas_evidence: Optional[dict] = None
    file_evidence: Optional[dict] = None
    async_evidence: Optional[dict] = None
    side_effect_evidence: Optional[dict] = None
    component_evidence: Optional[dict] = None
    before: Optional[Observation] = None
    after: Optional[Observation] = None
    planner_reason: Optional[str] = None
    progress_assessment: Optional[str] = None

    @property
    def duration_ms(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds() * 1000)


class AssertionResult(BaseModel):
    """断言结果。"""

    index: int
    type: str
    description: Optional[str] = None
    detail: str = Field(description="脱敏后的断言描述")
    status: Status
    expected_summary: Optional[str] = None
    actual_summary: Optional[str] = None
    error_message: Optional[str] = None
    screenshot: Optional[str] = None
    semantic_evidence: Optional[dict] = None


class CauseHint(BaseModel):
    """启发式原因提示。必须明确标注为非确定性建议。"""

    heuristic: bool = True
    category: FailureCategory
    message: str
    evidence: list[str] = Field(default_factory=list, description="支撑该提示的证据来源")
    confidence: str = Field(default="low", description="low/medium：不提供确定性诊断")


class Finding(BaseModel):
    id: str
    title: str
    category: str
    severity: str = "Medium"
    confidence: str = "medium"
    actual_result: str
    expected_result: str
    facts: list[str] = Field(default_factory=list)
    inference: str = ""
    evidence: list[str] = Field(default_factory=list)
    evidence_timeline: list["EvidenceEvent"] = Field(default_factory=list)
    reproduction_steps: list[str] = Field(default_factory=list)
    review_status: str = "pending_review"
    review_history: list[dict] = Field(default_factory=list)


class EvidenceEvent(BaseModel):
    phase: str
    timestamp: datetime
    screenshot: Optional[str] = None
    facts: list[str] = Field(default_factory=list)


class GeneratedTest(BaseModel):
    language: str = "typescript"
    framework_version: str = "playwright"
    source_path: str
    stability_level: str
    supported_replay_modes: list[str] = Field(default_factory=list)
    ci_eligible: bool = False
    ci_recommendation: str
    source: str = ""
    manual_steps: list[str] = Field(default_factory=list)
    source_revision: int = Field(default=1, ge=1)
    source_review_history: list[dict] = Field(default_factory=list)


class ModelCallRecord(BaseModel):
    index: int
    model: str
    protocol: str
    elapsed_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: Optional[float] = None
    decision: str
    reason: str


class RunResult(BaseModel):
    """一次完整运行的结果。序列化为 run.json。"""

    model_config = {"protected_namespaces": ()}

    run_id: str
    plan_name: str
    role: Optional[str] = None
    base_url_summary: str = Field(description="脱敏后的 base_url")
    status: Status
    started_at: datetime
    ended_at: datetime
    steps: list[StepResult] = Field(default_factory=list)
    assertions: list[AssertionResult] = Field(default_factory=list)
    failed_step_index: Optional[int] = None
    reproduction_steps: list[str] = Field(default_factory=list)
    cause_hints: list[CauseHint] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    generated_test: Optional[GeneratedTest] = None
    replay_mode: str = "exploration"
    onboarding_level: Optional[str] = None
    stability_level: str = "A"
    completion_reason: str = "plan_completed"
    project_id: Optional[str] = None
    account_id: Optional[str] = None
    account_role: Optional[str] = None
    environment_id: Optional[str] = None
    environment_updated_at: Optional[str] = None
    artifact_retention_days: int = 30
    scenario_id: Optional[str] = None
    scenario_updated_at: Optional[str] = None
    scenario_goal: str = ""
    goal_status: str = "incomplete"
    goal_summary: str = ""
    model_calls: int = 0
    estimated_cost: Optional[float] = None
    input_tokens: int = 0
    output_tokens: int = 0
    model_call_records: list[ModelCallRecord] = Field(default_factory=list)
    confirmation_history: list[dict] = Field(default_factory=list)
    runner_isolation: Optional[dict[str, Any]] = None
    websocket_timeline: list[dict] = Field(default_factory=list)
    cleanup_report: Optional[dict[str, Any]] = None
    business_context_snapshot: Optional[dict[str, Any]] = None
    project_snapshot: Optional[dict[str, Any]] = None
    environment_snapshot: Optional[dict[str, Any]] = None
    app_map_snapshot: Optional[dict[str, Any]] = None
    evidence_manifest: Optional[dict[str, Any]] = None
    evidence_completeness: float = 0.0
    evidence_manifest_path: Optional[str] = None

    @property
    def duration_ms(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds() * 1000)

    @property
    def exit_code(self) -> int:
        """CI 退出码：通过 0，其余非 0。"""
        return 0 if self.status == Status.PASSED else 1
