"""领域模型层：用例、步骤、断言、计划、运行结果。

本层不依赖 Playwright、FastAPI 或任何模型 SDK，只描述"是什么"，
不描述"怎么执行"。执行语义由 execution/assertions 层实现。
"""

from gui_agent.domain.models import (
    ActionType,
    AssertionType,
    Locator,
    Step,
    Assertion,
    TestPlan,
    Precondition,
)
from gui_agent.domain.results import (
    Status,
    FailureCategory,
    StepResult,
    AssertionResult,
    CauseHint,
    RunResult,
)

__all__ = [
    "ActionType",
    "AssertionType",
    "Locator",
    "Step",
    "Assertion",
    "TestPlan",
    "Precondition",
    "Status",
    "FailureCategory",
    "StepResult",
    "AssertionResult",
    "CauseHint",
    "RunResult",
]
