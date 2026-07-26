"""受约束的测试计划模型。

设计要点：
- 动作和断言都是封闭枚举（白名单），执行器只认识这些类型。
- 定位器优先确定性策略（role/label/test-id/css/text），不含"让模型自己找"。
- 敏感值只通过 ``value_from_secret`` 引用环境变量名，绝不在计划里写明文。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ActionType(str, Enum):
    """执行器允许的动作白名单。未列出的动作会在校验期被拒绝。"""

    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    WAIT_FOR = "wait_for"
    SCREENSHOT = "screenshot"
    CLEAR = "clear"
    CHECK = "check"
    UNCHECK = "uncheck"
    HOVER = "hover"
    SCROLL = "scroll"
    BACK = "back"
    RELOAD = "reload"
    PRESS = "press"
    VISUAL_CLICK = "visual_click"
    VISUAL_HOVER = "visual_hover"
    VISUAL_SCROLL = "visual_scroll"
    VISUAL_DRAG = "visual_drag"
    BRIDGE_CLICK = "bridge_click"


class ExecutionMode(str, Enum):
    LOCATOR = "locator"
    VISUAL = "visual"
    APP_BRIDGE = "app_bridge"


class StabilityLevel(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class RelativePosition(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}
    x_ratio: float = Field(alias="xRatio", ge=0, le=1)
    y_ratio: float = Field(alias="yRatio", ge=0, le=1)


class AssertionType(str, Enum):
    """断言白名单，覆盖页面到达、可见性、文本、URL、表单值和数据范围。"""

    PAGE_REACHED = "page_reached"      # 当前 URL 到达某路径
    VISIBLE = "visible"                # 元素可见
    NOT_VISIBLE = "not_visible"        # 元素不可见（权限/数据范围常用）
    TEXT_CONTAINS = "text_contains"    # 元素文本包含
    URL_CONTAINS = "url_contains"      # URL 包含
    VALUE_EQUALS = "value_equals"      # 表单值等于
    COUNT_EQUALS = "count_equals"      # 匹配元素数量等于（数据范围断言）


class Locator(BaseModel):
    """确定性定位器。至少提供一种策略，按 role→label→test_id→css→text 优先级使用。"""

    model_config = {"extra": "forbid"}

    role: Optional[str] = Field(default=None, description="ARIA role，如 button")
    name: Optional[str] = Field(default=None, description="role 的可访问名称")
    label: Optional[str] = Field(default=None, description="表单标签文本")
    test_id: Optional[str] = Field(default=None, description="data-testid 值")
    css: Optional[str] = Field(default=None, description="稳定 CSS 选择器")
    text: Optional[str] = Field(default=None, description="可见文本")

    @model_validator(mode="after")
    def _at_least_one_strategy(self) -> "Locator":
        if not any([self.role, self.label, self.test_id, self.css, self.text]):
            raise ValueError("Locator 至少需要一种定位策略")
        if self.name and not self.role:
            raise ValueError("name 只能与 role 搭配使用")
        return self

    def describe(self) -> str:
        """人类可读的定位描述，用于报告和复现步骤。"""
        parts = []
        if self.role:
            parts.append(f"role={self.role}" + (f"[name={self.name}]" if self.name else ""))
        if self.label:
            parts.append(f"label={self.label}")
        if self.test_id:
            parts.append(f"test_id={self.test_id}")
        if self.css:
            parts.append(f"css={self.css}")
        if self.text:
            parts.append(f"text={self.text}")
        return ", ".join(parts)


class Step(BaseModel):
    """单个执行步骤。fill 类动作的值要么明文 value，要么 value_from_secret 引用。"""

    model_config = {"extra": "forbid"}

    action: ActionType
    target: Optional[str] = Field(default=None, description="navigate 的路径，如 /login")
    locator: Optional[Locator] = None
    value: Optional[str] = Field(default=None, description="明文输入值（非敏感）")
    value_from_secret: Optional[str] = Field(
        default=None, description="敏感值引用的环境变量名，运行时解析，不入报告"
    )
    description: Optional[str] = Field(default=None, description="步骤的人类可读说明")
    execution_mode: ExecutionMode = Field(default=ExecutionMode.LOCATOR)
    stability_level: StabilityLevel = Field(default=StabilityLevel.A)
    stability_reason: str = "确定性 Playwright 动作"
    visual_target: Optional[str] = None
    relative_position: Optional[RelativePosition] = None
    relative_end_position: Optional[RelativePosition] = None
    visual_expected_change: Optional[str] = None
    bridge_target_id: Optional[str] = None
    computer_use_triggered: bool = False
    computer_use_reason: Optional[str] = None
    scroll_delta_y: int = Field(default=600, ge=-5000, le=5000)

    @model_validator(mode="after")
    def _validate_action_shape(self) -> "Step":
        if self.value is not None and self.value_from_secret is not None:
            raise ValueError("value 与 value_from_secret 不能同时提供")

        if self.action == ActionType.NAVIGATE:
            if not self.target:
                raise ValueError("navigate 需要 target")
        elif self.action in (ActionType.CLICK, ActionType.WAIT_FOR, ActionType.CLEAR, ActionType.CHECK, ActionType.UNCHECK, ActionType.HOVER):
            if not self.locator:
                raise ValueError(f"{self.action.value} 需要 locator")
        elif self.action in (ActionType.FILL, ActionType.SELECT):
            if not self.locator:
                raise ValueError(f"{self.action.value} 需要 locator")
            if self.value is None and self.value_from_secret is None:
                raise ValueError(f"{self.action.value} 需要 value 或 value_from_secret")
        elif self.action == ActionType.SCREENSHOT:
            if self.locator or self.value is not None or self.value_from_secret:
                raise ValueError("screenshot 不接受 locator 或 value")
        elif self.action == ActionType.PRESS:
            if self.value is None:
                raise ValueError("press 需要 value 指定按键")
        elif self.action in {ActionType.VISUAL_CLICK, ActionType.VISUAL_HOVER, ActionType.VISUAL_SCROLL, ActionType.VISUAL_DRAG}:
            if self.execution_mode != ExecutionMode.VISUAL or not self.relative_position or not self.visual_target:
                raise ValueError(f"{self.action.value} 需要 visual 模式、语义目标和相对坐标")
            if self.action == ActionType.VISUAL_DRAG and not self.relative_end_position:
                raise ValueError("visual_drag 需要相对终点坐标")
            if self.stability_level not in {StabilityLevel.B, StabilityLevel.C}:
                raise ValueError("视觉动作稳定性只能为 B 或 C")
        elif self.action == ActionType.BRIDGE_CLICK:
            if self.execution_mode != ExecutionMode.APP_BRIDGE or not self.bridge_target_id:
                raise ValueError("bridge_click 需要 app_bridge 模式和 bridge_target_id")
        if self.computer_use_triggered and self.execution_mode != ExecutionMode.VISUAL:
            raise ValueError("Computer Use 只能用于 visual 模式")
        return self


class Assertion(BaseModel):
    """执行到某步后进行的断言。挂在步骤后统一运行。"""

    model_config = {"extra": "forbid"}

    type: AssertionType
    locator: Optional[Locator] = None
    expected: Optional[str] = Field(default=None, description="期望文本/URL 片段/值")
    count: Optional[int] = Field(default=None, ge=0, description="count_equals 的期望数量")
    description: Optional[str] = None

    @model_validator(mode="after")
    def _validate_assertion_shape(self) -> "Assertion":
        needs_locator = {
            AssertionType.VISIBLE,
            AssertionType.NOT_VISIBLE,
            AssertionType.TEXT_CONTAINS,
            AssertionType.VALUE_EQUALS,
            AssertionType.COUNT_EQUALS,
        }
        needs_expected = {
            AssertionType.PAGE_REACHED,
            AssertionType.TEXT_CONTAINS,
            AssertionType.URL_CONTAINS,
            AssertionType.VALUE_EQUALS,
        }
        if self.type in needs_locator and not self.locator:
            raise ValueError(f"断言 {self.type.value} 需要 locator")
        if self.type in needs_expected and self.expected is None:
            raise ValueError(f"断言 {self.type.value} 需要 expected")
        if self.type == AssertionType.COUNT_EQUALS and self.count is None:
            raise ValueError("count_equals 需要 count")
        return self


class Precondition(BaseModel):
    model_config = {"extra": "forbid"}

    description: str


class TestPlan(BaseModel):
    """一条完整的可执行测试计划。这是 planning 层的输出、execution 层的输入。"""

    model_config = {"extra": "forbid"}

    name: str
    base_url: str = Field(description="被测系统根地址，支持 ${ENV_VAR} 占位")
    role: Optional[str] = Field(default=None, description="执行该用例的角色，如 admin/employee")
    preconditions: list[Precondition] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    assertions: list[Assertion] = Field(
        default_factory=list, description="全部步骤执行后运行的收尾断言"
    )
