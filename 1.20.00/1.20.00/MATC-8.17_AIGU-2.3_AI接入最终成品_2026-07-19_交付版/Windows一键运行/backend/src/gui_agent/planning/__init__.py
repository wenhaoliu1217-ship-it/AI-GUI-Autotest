"""计划加载与校验层。

职责：把 YAML/JSON 文件加载为 ``TestPlan``，完成 schema 校验（由 Pydantic
保证）与密钥引用预检，并生成供人工审核的摘要。
不通过校验的计划不会进入执行层。
"""

from .loader import PlanLoadError, load_plan, summarize_plan
from .generic_planner import PlanningError, PlanningResult, plan_from_draft

__all__ = [
    "load_plan", "summarize_plan", "PlanLoadError",
    "PlanningError", "PlanningResult", "plan_from_draft",
]
