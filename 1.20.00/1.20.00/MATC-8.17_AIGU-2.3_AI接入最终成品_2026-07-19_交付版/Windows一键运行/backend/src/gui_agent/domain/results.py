"""运行结果领域模型。

与 models.py（计划输入）分开：这里描述"执行发生了什么"，
是 execution 层的输出、artifacts 层的输入。不依赖 Playwright。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    """步骤/断言/整体运行的统一状态。"""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"      # 非断言失败的异常：定位不到、超时、网络、安全拒绝
    SKIPPED = "skipped"  # 前序失败导致未执行


class FailureCategory(str, Enum):
    """失败分类，用于区分报告与启发式原因提示。"""

    ASSERTION = "assertion"        # 断言不成立（页面状态与预期不符）
    LOCATOR = "locator"            # 元素定位失败
    TIMEOUT = "timeout"            # 超时
    NAVIGATION = "navigation"      # 导航/网络错误
    SECURITY = "security"          # 安全策略拒绝
    MODEL = "model"                # 模型不可用/输出异常
    UNKNOWN = "unknown"


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


class CauseHint(BaseModel):
    """启发式原因提示。必须明确标注为非确定性建议。"""

    heuristic: bool = True
    category: FailureCategory
    message: str
    evidence: list[str] = Field(default_factory=list, description="支撑该提示的证据来源")
    confidence: str = Field(default="low", description="low/medium：不提供确定性诊断")


class RunResult(BaseModel):
    """一次完整运行的结果。序列化为 run.json。"""

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

    @property
    def duration_ms(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds() * 1000)

    @property
    def exit_code(self) -> int:
        """CI 退出码：通过 0，其余非 0。"""
        return 0 if self.status == Status.PASSED else 1
