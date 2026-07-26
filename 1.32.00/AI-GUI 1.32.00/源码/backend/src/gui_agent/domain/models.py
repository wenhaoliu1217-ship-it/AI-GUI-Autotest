"""受约束的测试计划模型。

设计要点：
- 动作和断言都是封闭枚举（白名单），执行器只认识这些类型。
- 定位器优先确定性策略（role/label/test-id/css/text），不含"让模型自己找"。
- 敏感值只通过 ``value_from_secret`` 引用环境变量名，绝不在计划里写明文。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from ..commerce.models import CommerceStepMetadata


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
    VISUAL_ZOOM = "visual_zoom"
    VISUAL_CLEAR = "visual_clear"
    VISUAL_DRAW_POLYGON = "visual_draw_polygon"
    VISUAL_DRAW_RECTANGLE = "visual_draw_rectangle"
    BRIDGE_CLICK = "bridge_click"
    HUMAN_TAKEOVER = "human_takeover"
    UPLOAD_FILE = "upload_file"
    DOWNLOAD = "download"
    UPLOAD = "upload"
    WAIT_FOR_STATE = "wait_for_state"
    COMPONENT = "component"


class ExecutionMode(str, Enum):
    LOCATOR = "locator"
    VISUAL = "visual"
    APP_BRIDGE = "app_bridge"


class StabilityLevel(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class EffectLevel(str, Enum):
    """目标网站动作的业务影响等级，供专项安全策略在执行前校验。"""

    READ_ONLY = "read_only"
    SESSION_ONLY = "session_only"
    REVERSIBLE_WRITE = "reversible_write"
    REVERSIBLE_QUOTA_WRITE = "reversible_quota_write"
    ISOLATED_LOCAL_WRITE = "isolated_local_write"
    SENSITIVE_REVERSIBLE_WRITE = "sensitive_reversible_write"
    HIGH_RISK_WRITE = "high_risk_write"
    HIGH_RISK_EXTERNAL_WRITE = "high_risk_external_write"
    HIGH_RISK_IRREVERSIBLE = "high_risk_irreversible"
    HIGH_RISK_PUBLIC_WRITE = "high_risk_public_write"
    HIGH_RISK_IDENTITY_WRITE = "high_risk_identity_write"
    FORBIDDEN = "forbidden"


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
    CANVAS_LAYER_VISIBLE = "canvas_layer_visible"
    CANVAS_CAMERA_EQUALS = "canvas_camera_equals"
    CANVAS_ENTITY_COUNT = "canvas_entity_count"
    CANVAS_SELECTED_ENTITY = "canvas_selected_entity"
    CANVAS_PATH_POINT_COUNT = "canvas_path_point_count"
    CANVAS_POI_COUNT = "canvas_poi_count"
    CANVAS_FENCE_COUNT = "canvas_fence_count"
    CANVAS_DRAWING_COUNT = "canvas_drawing_count"
    CANVAS_TILES_LOADED = "canvas_tiles_loaded"
    CANVAS_WEBGL_NO_ERROR = "canvas_webgl_no_error"


class StableAttribute(BaseModel):
    model_config = {"extra": "forbid"}
    name: Literal["data-test", "data-qa", "data-cy", "data-object-id"]
    value: str = Field(min_length=1, max_length=500)


class LocatorScope(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["row", "card", "dialog", "tab_panel", "canvas"]
    locator: "Locator"
    identity: Optional[str] = Field(default=None, max_length=500)


class Locator(BaseModel):
    """确定性定位器。至少提供一种策略，按 role→label→test_id→css→text 优先级使用。"""

    model_config = {"extra": "forbid", "populate_by_name": True}

    role: Optional[str] = Field(default=None, description="ARIA role，如 button")
    name: Optional[str] = Field(default=None, description="role 的可访问名称")
    label: Optional[str] = Field(default=None, description="表单标签文本")
    placeholder: Optional[str] = Field(default=None, description="输入框 placeholder")
    test_id: Optional[str] = Field(default=None, description="data-testid 值")
    attribute_name: Optional[str] = Field(default=None, description="HTML name 属性")
    href: Optional[str] = Field(default=None, description="链接 href 精确值")
    attribute: Optional[StableAttribute] = Field(default=None, description="稳定 data-* 属性")
    css: Optional[str] = Field(default=None, description="稳定 CSS 选择器")
    text: Optional[str] = Field(default=None, description="可见文本")
    exact: bool = Field(default=True, description="文本和可访问名称是否精确匹配")
    shadow_hosts: list[str] = Field(default_factory=list, description="从外到内的开放 Shadow DOM 宿主路径")
    scope: Optional[LocatorScope] = Field(default=None, description="业务容器作用域")

    @model_validator(mode="after")
    def _at_least_one_strategy(self) -> "Locator":
        if not any([self.role, self.label, self.placeholder, self.test_id, self.attribute_name, self.href, self.attribute, self.css, self.text]):
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
        if self.placeholder:
            parts.append(f"placeholder={self.placeholder}")
        if self.test_id:
            parts.append(f"test_id={self.test_id}")
        if self.attribute_name:
            parts.append(f"name={self.attribute_name}")
        if self.href:
            parts.append(f"href={self.href}")
        if self.attribute:
            parts.append(f"{self.attribute.name}={self.attribute.value}")
        if self.css:
            parts.append(f"css={self.css}")
        if self.text:
            parts.append(f"text={self.text}")
        if self.shadow_hosts:
            parts.insert(0, "shadow=" + " > ".join(self.shadow_hosts))
        target = ", ".join(parts)
        if self.scope:
            identity = f" identity={self.scope.identity}" if self.scope.identity else ""
            return f"scope[{self.scope.kind}{identity}: {self.scope.locator.describe()}] -> {target}"
        return target


class DownloadValidation(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}
    extension: Optional[str] = Field(default=None, pattern=r"^\.[A-Za-z0-9]{1,10}$")
    filename_pattern: Optional[str] = Field(default=None, alias="filenamePattern", max_length=200)
    minimum_size: int = Field(default=1, alias="minimumSize", ge=1, le=2 * 1024 * 1024 * 1024)
    sha256: Optional[str] = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    format: Literal["binary", "json", "zip", "text", "csv"] = "binary"
    required_json_keys: list[str] = Field(default_factory=list, alias="requiredJsonKeys", max_length=100)


class ComponentAction(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}
    kind: Literal["cascade_select", "searchable_select", "date_time_range", "pagination", "statistics_card", "tab", "upload_dialog", "image_preview", "local_scroll"]
    semantic_target: str = Field(alias="semanticTarget", min_length=1, max_length=300)
    locators: list[Locator] = Field(min_length=1, max_length=20)
    values: list[str] = Field(default_factory=list, max_length=20)
    expected_text: Optional[str] = Field(default=None, alias="expectedText", max_length=1000)
    file_id: Optional[str] = Field(default=None, alias="fileId", max_length=80)
    scroll_delta_y: int = Field(default=600, alias="scrollDeltaY", ge=-5000, le=5000)

    @model_validator(mode="after")
    def validate_contract(self) -> "ComponentAction":
        minimum = {"cascade_select": (1, 1), "searchable_select": (3, 1), "date_time_range": (2, 2), "pagination": (1, 0), "statistics_card": (1, 0), "tab": (1, 0), "upload_dialog": (2, 0), "image_preview": (2, 0), "local_scroll": (1, 0)}
        locators, values = minimum[self.kind]
        if len(self.locators) < locators or len(self.values) < values:
            raise ValueError(f"复杂组件 {self.kind} 缺少必需定位器或参数")
        if self.kind == "cascade_select" and len(self.locators) != len(self.values):
            raise ValueError("级联下拉的每一级定位器必须对应一个选择值")
        if self.kind == "upload_dialog" and not self.file_id:
            raise ValueError("上传弹窗组件需要已登记 fileId")
        if self.kind in {"statistics_card", "image_preview"} and not self.expected_text:
            raise ValueError(f"复杂组件 {self.kind} 需要 expectedText 作为可验证结果")
        return self


class CommerceScope(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}

    kind: Literal["product_card", "cart_line", "order_row", "after_sale_row"]
    container: Locator
    anchor: Locator
    excluded_markers: list[Locator] = Field(default_factory=list, alias="excludedMarkers", max_length=10)
    max_scroll_attempts: int = Field(default=4, alias="maxScrollAttempts", ge=0, le=12)


class BrowserTarget(BaseModel):
    """Selects a browser page and optional iframe without storing ephemeral page ids."""

    model_config = {"extra": "forbid", "populate_by_name": True}

    page: Literal["current", "newest", "opener"] = "current"
    url_contains: Optional[str] = Field(default=None, alias="urlContains", max_length=500)
    frame_css: Optional[str] = Field(default=None, alias="frameCss", max_length=500)
    wait_timeout_ms: int = Field(default=10_000, alias="waitTimeoutMs", ge=500, le=120_000)


class Step(BaseModel):
    """单个执行步骤。fill 类动作的值要么明文 value，要么 value_from_secret 引用。"""

    model_config = {"extra": "forbid", "populate_by_name": True}

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
    visual_points: list[RelativePosition] = Field(default_factory=list, max_length=100)
    canvas_region_locator: Optional[Locator] = None
    zoom_delta: int = Field(default=-600, ge=-5000, le=5000)
    gesture_finish: Literal["double_click", "enter", "none"] = "double_click"
    visual_expected_change: Optional[str] = None
    bridge_target_id: Optional[str] = None
    computer_use_triggered: bool = False
    computer_use_reason: Optional[str] = None
    scroll_delta_y: int = Field(default=600, ge=-5000, le=5000)
    wait_before_ms: int = Field(default=0, alias="waitBeforeMs", ge=0, le=30_000)
    commerce: Optional[CommerceStepMetadata] = None
    commerce_scope: Optional[CommerceScope] = Field(default=None, alias="commerceScope")
    browser_target: BrowserTarget = Field(default_factory=BrowserTarget, alias="browserTarget")
    takeover_reason: Optional[Literal["captcha", "slider", "qr_login", "risk_control", "payment_auth", "other"]] = Field(
        default=None, alias="takeoverReason"
    )
    takeover_resume_locator: Optional[Locator] = Field(default=None, alias="takeoverResumeLocator")
    file_asset_ref: Optional[str] = Field(default=None, alias="fileAssetRef", pattern=r"^asset:[0-9a-f]{64}$")
    expected_download_sha256: Optional[str] = Field(default=None, alias="expectedDownloadSha256", pattern=r"^[0-9a-f]{64}$")
    file_id: Optional[str] = Field(default=None, description="项目测试文件仓库中的登记 ID")
    expected_file_validity: Literal["valid", "invalid"] = "valid"
    business_object_name: Optional[str] = Field(default=None, max_length=200)
    download_validation: Optional[DownloadValidation] = None
    residual_object_locator: Optional[Locator] = None
    expected_residual_count: int = Field(default=0, ge=0, le=1000)
    state_machine_id: Optional[str] = Field(default=None, max_length=80)
    business_object_id: Optional[str] = Field(default=None, max_length=200)
    action_category: Optional[str] = Field(default=None, max_length=80)
    object_type: Optional[str] = Field(default=None, max_length=120)
    precondition_state: Optional[str] = Field(default=None, max_length=120)
    cleanup_required: bool = False
    effect_kind: Optional[str] = Field(default=None, description="目标站点专项策略中的动作类型")
    effect_level: Optional[EffectLevel] = Field(default=None, description="必须与目标站点专项策略定义一致")
    target_id: Optional[str] = Field(default=None, description="副作用目标的稳定业务 ID")
    resource_name: Optional[str] = Field(default=None, description="副作用目标或新资源的唯一名称")
    account_context: Optional[str] = Field(default=None, description="个人账号或团队上下文")
    cleanup_action: Optional[str] = Field(default=None, description="成功或中断后的清理动作")
    component: Optional[ComponentAction] = None
    component_adapter_id: Optional[str] = Field(default=None, max_length=100)

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
        elif self.action in {ActionType.VISUAL_ZOOM, ActionType.VISUAL_CLEAR, ActionType.VISUAL_DRAW_POLYGON, ActionType.VISUAL_DRAW_RECTANGLE}:
            if self.execution_mode != ExecutionMode.VISUAL or not self.visual_target or not self.canvas_region_locator:
                raise ValueError(f"{self.action.value} 需要 visual 模式、语义目标和 Canvas 区域")
            if self.stability_level not in {StabilityLevel.B, StabilityLevel.C}:
                raise ValueError("Canvas 视觉手势稳定性只能为 B 或 C")
            if self.action == ActionType.VISUAL_ZOOM and not self.relative_position:
                raise ValueError("visual_zoom 需要 Canvas 内相对中心点")
            if self.action == ActionType.VISUAL_CLEAR and not self.locator:
                raise ValueError("visual_clear 需要受约束的清除控件 locator")
            if self.action == ActionType.VISUAL_DRAW_POLYGON and len(self.visual_points) < 3:
                raise ValueError("visual_draw_polygon 至少需要 3 个相对顶点")
            if self.action == ActionType.VISUAL_DRAW_RECTANGLE and len(self.visual_points) != 2:
                raise ValueError("visual_draw_rectangle 必须提供 2 个相对边界点")
        elif self.action == ActionType.BRIDGE_CLICK:
            if self.execution_mode != ExecutionMode.APP_BRIDGE or not self.bridge_target_id:
                raise ValueError("bridge_click 需要 app_bridge 模式和 bridge_target_id")
        elif self.action == ActionType.HUMAN_TAKEOVER:
            if not self.takeover_reason:
                raise ValueError("human_takeover 需要 takeoverReason")
            if not self.browser_target.url_contains and not self.takeover_resume_locator:
                raise ValueError("human_takeover 需要恢复 URL 或恢复状态定位器")
            if self.stability_level != StabilityLevel.D:
                raise ValueError("human_takeover 必须标记为 D 级人工步骤")
        elif self.action == ActionType.UPLOAD_FILE:
            if not self.locator or not self.file_asset_ref:
                raise ValueError("upload_file 需要 locator 和 fileAssetRef")
        elif self.action == ActionType.DOWNLOAD:
            if not self.locator:
                raise ValueError("download 需要 locator")
        elif self.action == ActionType.UPLOAD:
            if not self.locator or not self.file_id:
                raise ValueError("upload 需要文件输入 locator 和已登记 file_id")
            if not self.business_object_name or not self.business_object_name.startswith("E2E_"):
                raise ValueError("upload 的业务对象名称必须使用 E2E_ 前缀")
            if self.expected_file_validity == "invalid" and self.residual_object_locator is None:
                raise ValueError("无效上传必须提供 residual_object_locator 验证业务对象零残留")
        elif self.action == ActionType.WAIT_FOR_STATE:
            if not self.locator or not self.state_machine_id or not self.business_object_id:
                raise ValueError("wait_for_state 需要状态 locator、state_machine_id 和 business_object_id")
        elif self.action == ActionType.COMPONENT:
            if self.component is None or self.locator is not None:
                raise ValueError("component 动作需要复杂组件语义配置，定位器必须封装在 component.locators 中")
        if self.action_category:
            if not self.object_type or not self.business_object_name:
                raise ValueError("副作用动作需要 object_type 和 business_object_name")
            if not self.business_object_name.startswith("E2E_"):
                raise ValueError("副作用业务对象名称必须使用 E2E_ 前缀")
        if self.computer_use_triggered and self.execution_mode != ExecutionMode.VISUAL:
            raise ValueError("Computer Use 只能用于 visual 模式")
        if self.commerce_scope and (self.execution_mode != ExecutionMode.LOCATOR or not self.locator):
            raise ValueError("电商作用域只能用于带 locator 的结构化步骤")
        if self.browser_target.frame_css and self.execution_mode != ExecutionMode.LOCATOR:
            raise ValueError("iframe 内动作当前只允许确定性 locator 模式")
        return self


class Assertion(BaseModel):
    """执行到某步后进行的断言。挂在步骤后统一运行。"""

    model_config = {"extra": "forbid"}

    type: AssertionType
    locator: Optional[Locator] = None
    expected: Optional[str] = Field(default=None, description="期望文本/URL 片段/值")
    count: Optional[int] = Field(default=None, ge=0, description="count_equals 的期望数量")
    description: Optional[str] = None
    tolerance: float = Field(default=0.0001, ge=0, le=1000)

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
        semantic_counts = {AssertionType.CANVAS_ENTITY_COUNT, AssertionType.CANVAS_PATH_POINT_COUNT, AssertionType.CANVAS_POI_COUNT, AssertionType.CANVAS_FENCE_COUNT, AssertionType.CANVAS_DRAWING_COUNT}
        if self.type in semantic_counts and self.count is None:
            raise ValueError(f"{self.type.value} 需要 count")
        if self.type in {AssertionType.CANVAS_LAYER_VISIBLE, AssertionType.CANVAS_CAMERA_EQUALS, AssertionType.CANVAS_SELECTED_ENTITY} and self.expected is None:
            raise ValueError(f"{self.type.value} 需要 expected")
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
