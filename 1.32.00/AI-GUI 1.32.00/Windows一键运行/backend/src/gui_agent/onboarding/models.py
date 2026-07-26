"""企业项目接入的数据模型。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..commerce.models import CommerceEnvironment, CommerceStepMetadata
from ..domain.models import BrowserTarget, ComponentAction, Locator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ProjectLimits(ApiModel):
    max_steps: int = Field(default=50, alias="maxSteps", ge=1, le=100)
    timeout_seconds: int = Field(default=600, alias="timeoutSeconds", ge=30, le=3600)
    max_model_calls: int = Field(default=20, alias="maxModelCalls", ge=0, le=100)


class BusinessContext(ApiModel):
    description: str = Field(default="", max_length=4000)
    terminology: dict[str, str] = Field(default_factory=dict, max_length=100)
    object_types: list[str] = Field(default_factory=list, alias="objectTypes", max_length=100)
    state_models: dict[str, list[str]] = Field(default_factory=dict, alias="stateModels", max_length=100)
    example_goals: list[str] = Field(default_factory=list, alias="exampleGoals", max_length=50)
    operating_boundaries: list[str] = Field(default_factory=list, alias="operatingBoundaries", max_length=50)
    allowed_actions: list[str] = Field(default_factory=list, alias="allowedActions", max_length=100)
    bridge_capabilities: list[str] = Field(default_factory=list, alias="bridgeCapabilities", max_length=100)
    bridge_semantic_targets: dict[str, str] = Field(default_factory=dict, alias="bridgeSemanticTargets", max_length=200)
    facts: list["BusinessFact"] = Field(default_factory=list, max_length=300)
    object_relations: list["ObjectRelation"] = Field(default_factory=list, alias="objectRelations", max_length=300)
    missing_facts: list[str] = Field(default_factory=list, alias="missingFacts", max_length=200)
    source_revision: str = Field(default="", alias="sourceRevision", max_length=200)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("terminology")
    @classmethod
    def normalize_terminology(cls, values: dict[str, str]) -> dict[str, str]:
        return {
            str(key).strip(): str(value).strip()
            for key, value in values.items()
            if str(key).strip() and str(value).strip()
        }

    @field_validator("object_types", "example_goals", "operating_boundaries", "allowed_actions", "bridge_capabilities")
    @classmethod
    def normalize_context_entries(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))

    @field_validator("state_models")
    @classmethod
    def normalize_state_models(cls, values: dict[str, list[str]]) -> dict[str, list[str]]:
        normalized: dict[str, list[str]] = {}
        for raw_name, raw_states in values.items():
            name = str(raw_name).strip()
            states = list(dict.fromkeys(str(item).strip() for item in raw_states if str(item).strip()))
            if name and states:
                normalized[name] = states
        return normalized

    @field_validator("bridge_semantic_targets")
    @classmethod
    def normalize_bridge_semantic_targets(cls, values: dict[str, str]) -> dict[str, str]:
        return {
            str(key).strip(): str(value).strip()
            for key, value in values.items()
            if str(key).strip() and str(value).strip()
        }


class BusinessFact(ApiModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    category: Literal["term", "object", "state", "operation", "permission", "bridge", "constraint"]
    statement: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="", max_length=1000)
    status: Literal["confirmed", "blocked"] = "blocked"

    @model_validator(mode="after")
    def require_source(self) -> "BusinessFact":
        if self.status == "confirmed" and not self.source.strip():
            raise ValueError("已确认业务事实必须填写来源")
        return self


class ObjectRelation(ApiModel):
    source_object: str = Field(alias="sourceObject", min_length=1, max_length=160)
    relation: str = Field(min_length=1, max_length=160)
    target_object: str = Field(alias="targetObject", min_length=1, max_length=160)
    source: str = Field(default="", max_length=1000)
    status: Literal["confirmed", "blocked"] = "blocked"


class ComponentAdapter(ApiModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    module: Literal["login", "navigation", "agent", "environment_asset", "elevation", "scenario", "run", "reinforcement_learning", "help"]
    page: str = Field(min_length=1, max_length=300)
    action: ComponentAction
    status: Literal["configured", "blocked"] = "blocked"
    source: str = Field(default="", max_length=1000)
    blocked_reason: str = Field(default="", alias="blockedReason", max_length=1000)

    @model_validator(mode="after")
    def validate_status(self) -> "ComponentAdapter":
        if self.status == "configured" and not self.source.strip():
            raise ValueError("已配置组件适配必须填写目标站确认来源")
        if self.status == "blocked" and not self.blocked_reason.strip():
            raise ValueError("blocked 组件适配必须填写缺失原因")
        return self


class TestFileRecord(ApiModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^file-[a-f0-9]{12}$")
    project_id: str = Field(alias="projectId")
    file_name: str = Field(alias="fileName", min_length=1, max_length=255)
    size: int = Field(ge=0, le=2 * 1024 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str = Field(alias="mimeType", max_length=200)
    extension: str = Field(max_length=16)
    validation_profile: str = Field(alias="validationProfile", max_length=40)
    validation_status: Literal["valid", "invalid"] = Field(alias="validationStatus")
    validation_errors: list[str] = Field(default_factory=list, alias="validationErrors")
    expected_result: str = Field(default="accepted", alias="expectedResult", max_length=500)
    created_at: str = Field(default_factory=utc_now, alias="createdAt")


class AccountProfile(ApiModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)
    login_method: Literal["interactive", "credentials"] = Field(default="interactive", alias="loginMethod")
    credential_refs: dict[str, str] = Field(default_factory=dict, alias="credentialRefs", max_length=10)
    permissions: list[str] = Field(default_factory=list, max_length=100)


class AsyncStateMachine(ApiModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    states: list[str] = Field(min_length=1, max_length=100)
    terminal_states: list[str] = Field(alias="terminalStates", min_length=1, max_length=50)
    failure_states: list[str] = Field(default_factory=list, alias="failureStates", max_length=50)
    transitions: dict[str, list[str]] = Field(default_factory=dict, max_length=100)
    polling_interval_ms: int = Field(default=1000, alias="pollingIntervalMs", ge=100, le=60_000)
    timeout_ms: int = Field(default=120_000, alias="timeoutMs", ge=1000, le=3_600_000)

    @model_validator(mode="after")
    def validate_states(self) -> "AsyncStateMachine":
        known = set(self.states)
        referenced = set(self.terminal_states) | set(self.failure_states)
        for source, targets in self.transitions.items():
            referenced.add(source)
            referenced.update(targets)
        unknown = sorted(referenced - known)
        if unknown:
            raise ValueError(f"状态机引用了未声明状态：{unknown[0]}")
        return self


class SideEffectPolicy(ApiModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    action_category: str = Field(alias="actionCategory", min_length=1, max_length=80)
    object_type: str = Field(alias="objectType", min_length=1, max_length=120)
    name_pattern: str = Field(default=r"^E2E_", alias="namePattern", min_length=1, max_length=200)
    environment_id: str | None = Field(default=None, alias="environmentId", max_length=120)
    role: str | None = Field(default=None, max_length=120)
    precondition_state: str | None = Field(default=None, alias="preconditionState", max_length=120)
    decision: Literal["allow", "confirm", "conditional", "forbid"]
    rollback_rule: str = Field(default="", alias="rollbackRule", max_length=1000)


class CommerceProfile(ApiModel):
    enabled: bool = False
    environment: CommerceEnvironment = CommerceEnvironment.PRODUCTION_READONLY
    account_ref: str | None = Field(default=None, alias="accountRef")
    production_reversible_write_authorized: bool = Field(
        default=False, alias="productionReversibleWriteAuthorized"
    )
    sandbox_driver: bool = Field(default=False, alias="sandboxDriver")
    fixed_product_ref: str | None = Field(default=None, alias="fixedProductRef", max_length=120)
    fixed_address_ref: str | None = Field(default=None, alias="fixedAddressRef", max_length=120)
    written_authorization_ref: str | None = Field(default=None, alias="writtenAuthorizationRef", max_length=160)
    automatic_cancellation_verified: bool = Field(default=False, alias="automaticCancellationVerified")
    e2e_resource_prefix: str = Field(default="E2E_", alias="e2eResourcePrefix")
    pii_mask_selectors: list[str] = Field(default_factory=list, alias="piiMaskSelectors", max_length=100)

    @field_validator("account_ref")
    @classmethod
    def validate_account_ref(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", normalized):
            raise ValueError("电商账号只能保存大写环境密钥别名")
        return normalized

    @field_validator("e2e_resource_prefix")
    @classmethod
    def validate_e2e_prefix(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"E2E_[A-Za-z0-9_-]{0,24}", normalized):
            raise ValueError("E2E 资源前缀必须以 E2E_ 开头且只含字母、数字、下划线或连字符")
        return normalized

    @field_validator("pii_mask_selectors")
    @classmethod
    def normalize_mask_selectors(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))

    @model_validator(mode="after")
    def validate_environment_controls(self) -> "CommerceProfile":
        if self.environment == CommerceEnvironment.PRODUCTION_READONLY and self.sandbox_driver:
            raise ValueError("正式站项目不能启用支付／退款沙箱驱动器")
        return self

class ProjectConfig(ApiModel):
    id: str
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(alias="baseUrl")
    allowed_hosts: list[str] = Field(default_factory=list, alias="allowedHosts")
    forbidden_actions: list[str] = Field(default_factory=list, alias="forbiddenActions")
    allow_private_network: bool = Field(default=False, alias="allowPrivateNetwork")
    business_context: BusinessContext = Field(default_factory=BusinessContext, alias="businessContext")
    async_state_machines: list[AsyncStateMachine] = Field(default_factory=list, alias="asyncStateMachines", max_length=50)
    side_effect_policies: list[SideEffectPolicy] = Field(default_factory=list, alias="sideEffectPolicies", max_length=100)
    component_adapters: list[ComponentAdapter] = Field(default_factory=list, alias="componentAdapters", max_length=300)
    account_profiles: list[AccountProfile] = Field(
        default_factory=lambda: [AccountProfile(id="default", name="默认测试账号", role="tester")],
        alias="accountProfiles", max_length=50,
    )
    commerce_profile: CommerceProfile = Field(default_factory=CommerceProfile, alias="commerceProfile")
    onboarding_level: Literal["L0", "L1", "L2", "L3"] = Field(default="L0", alias="onboardingLevel")
    limits: ProjectLimits = Field(default_factory=ProjectLimits)
    created_at: str = Field(default_factory=utc_now, alias="createdAt")
    updated_at: str = Field(default_factory=utc_now, alias="updatedAt")

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Base URL 必须是完整的 http/https 地址")
        return value.strip().rstrip("/")

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            host = urlparse(value).hostname if "://" in value else value.strip().split(":", 1)[0]
            if host and host.lower() not in normalized:
                normalized.append(host.lower())
        return normalized

    @model_validator(mode="after")
    def include_base_host(self) -> "ProjectConfig":
        host = (urlparse(self.base_url).hostname or "").lower()
        if host and host not in self.allowed_hosts:
            self.allowed_hosts.insert(0, host)
        if host == "ion.cesium.com" and "api.cesium.com" not in self.allowed_hosts:
            self.allowed_hosts.append("api.cesium.com")
        return self


class CompatibilityReport(ApiModel):
    project_id: str = Field(alias="projectId")
    generated_at: str = Field(default_factory=utc_now, alias="generatedAt")
    onboarding_level: Literal["L0", "L1", "L2", "L3"] = Field(alias="onboardingLevel")
    requested_url: str = Field(alias="requestedUrl")
    final_url: str = Field(alias="finalUrl")
    title: str = ""
    status: Literal["compatible", "attention"]
    page_summary: dict[str, int] = Field(alias="pageSummary")
    candidate_locators: dict[str, int] = Field(alias="candidateLocators")
    capabilities: list[str]
    third_party_hosts: list[str] = Field(alias="thirdPartyHosts")
    console_errors: list[str] = Field(alias="consoleErrors")
    failed_requests: list[str] = Field(alias="failedRequests")
    blocked_areas: list[str] = Field(alias="blockedAreas")
    recommendations: list[str]
    suggested_scenarios: list[str] = Field(alias="suggestedScenarios")
    recommended_onboarding_level: Literal["L0", "L1", "L2", "L3"] = Field(default="L0", alias="recommendedOnboardingLevel")
    scanned_pages: list[dict[str, Any]] = Field(default_factory=list, alias="scannedPages")
    navigation_entries: list[str] = Field(default_factory=list, alias="navigationEntries")
    authentication_signals: list[str] = Field(default_factory=list, alias="authenticationSignals")
    async_patterns: list[str] = Field(default_factory=list, alias="asyncPatterns")
    stable_areas: list[str] = Field(default_factory=list, alias="stableAreas")
    visual_areas: list[str] = Field(default_factory=list, alias="visualAreas")
    adaptive_areas: list[str] = Field(default_factory=list, alias="adaptiveAreas")
    manual_areas: list[str] = Field(default_factory=list, alias="manualAreas")
    recommended_config: dict[str, Any] = Field(default_factory=dict, alias="recommendedConfig")
    sample_scenario_id: str | None = Field(default=None, alias="sampleScenarioId")
    sample_scenario_created: bool = Field(default=False, alias="sampleScenarioCreated")


class SessionMetadata(ApiModel):
    project_id: str = Field(alias="projectId")
    imported_at: str = Field(alias="importedAt")
    cookie_count: int = Field(alias="cookieCount")
    origin_count: int = Field(alias="originCount")
    domains: list[str]
    expires_at: str | None = Field(default=None, alias="expiresAt")
    expiry_status: Literal["active", "warning", "expired", "unknown"] = Field(alias="expiryStatus")
    expired_cookie_count: int = Field(default=0, alias="expiredCookieCount")
    encryption: str


class ViewportConfig(ApiModel):
    width: int = Field(default=1440, ge=320, le=3840)
    height: int = Field(default=960, ge=320, le=2160)


class AppBridgeConfig(ApiModel):
    enabled: bool = False
    global_name: str = Field(default="__WEB_AI_TEST__", alias="globalName")
    adapter: Literal["generic", "cesium", "gaealavic_cesium"] = "generic"

    @field_validator("global_name")
    @classmethod
    def validate_global_name(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", normalized):
            raise ValueError("Bridge 全局名称必须是单个安全 JavaScript 标识符")
        return normalized


class EnvironmentConfig(ApiModel):
    """Non-secret environment data plus references to OS-managed secrets."""

    id: str
    project_id: str = Field(alias="projectId")
    name: str = Field(min_length=1, max_length=120)
    variables: dict[str, str] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict, alias="secretRefs")
    ignore_rules: list[str] = Field(default_factory=list, alias="ignoreRules")
    screenshot_mask_selectors: list[str] = Field(
        default_factory=list, alias="screenshotMaskSelectors"
    )
    viewport: ViewportConfig = Field(default_factory=ViewportConfig)
    device_scale_factor: float = Field(default=1.0, alias="deviceScaleFactor", ge=0.5, le=3.0)
    app_bridge: AppBridgeConfig = Field(default_factory=AppBridgeConfig, alias="appBridge")
    artifact_retention_days: int = Field(default=30, alias="artifactRetentionDays", ge=1, le=365)
    created_at: str = Field(default_factory=utc_now, alias="createdAt")
    updated_at: str = Field(default_factory=utc_now, alias="updatedAt")

    @field_validator("variables", "secret_refs")
    @classmethod
    def validate_keys(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_key, raw_value in values.items():
            key = raw_key.strip()
            if not key or not key.replace("_", "A").isalnum() or key[0].isdigit():
                raise ValueError(f"环境变量名非法：{key}")
            normalized[key] = raw_value.strip()
        return normalized

    @field_validator("variables")
    @classmethod
    def reject_sensitive_plain_variables(cls, values: dict[str, str]) -> dict[str, str]:
        sensitive = re.compile(r"password|passwd|token|api[_ -]?key|secret|cookie|密码|密钥", re.IGNORECASE)
        blocked = next((key for key in values if sensitive.search(key)), None)
        if blocked:
            raise ValueError(f"敏感环境变量 {blocked} 必须改用 secretRefs")
        return values

    @field_validator("secret_refs")
    @classmethod
    def validate_secret_targets(cls, values: dict[str, str]) -> dict[str, str]:
        for alias, target in values.items():
            if not target or not target.replace("_", "A").isalnum() or target[0].isdigit():
                raise ValueError(f"密钥引用 {alias} 的系统环境变量名非法")
        return values

    @field_validator("ignore_rules", "screenshot_mask_selectors")
    @classmethod
    def normalize_ignore_rules(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))[:100]

    @model_validator(mode="after")
    def validate_variable_namespaces(self) -> "EnvironmentConfig":
        overlap = set(self.variables) & set(self.secret_refs)
        if overlap:
            raise ValueError(f"普通变量与密钥引用不能重名：{sorted(overlap)[0]}")
        return self


class ScenarioCommerceStep(ApiModel):
    step_index: int = Field(alias="stepIndex", ge=1, le=100)
    commerce: CommerceStepMetadata


class ScenarioExecutionStep(ApiModel):
    step_index: int = Field(alias="stepIndex", ge=1, le=100)
    browser_target: BrowserTarget = Field(alias="browserTarget")
    action: Literal["human_takeover"] | None = None
    takeover_reason: Literal["captcha", "slider", "qr_login", "risk_control", "payment_auth", "other"] | None = Field(default=None, alias="takeoverReason")
    takeover_resume_locator: Locator | None = Field(default=None, alias="takeoverResumeLocator")


class BusinessObjectLifecycle(ApiModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    object_type: str = Field(alias="objectType", min_length=1, max_length=120)
    name: str = Field(min_length=5, max_length=200, pattern=r"^E2E_.+")
    business_id: str | None = Field(default=None, alias="businessId", max_length=200)
    dependencies: list[str] = Field(default_factory=list, max_length=50)
    reuse: bool = False
    cleanup_step: dict[str, Any] = Field(alias="cleanupStep")
    verification_locator: dict[str, Any] | None = Field(default=None, alias="verificationLocator")
    manual_fallback: str = Field(default="", alias="manualFallback", max_length=1000)


class ScenarioConfig(ApiModel):
    id: str
    project_id: str = Field(alias="projectId")
    name: str = Field(min_length=1, max_length=160)
    preconditions: list[str] = Field(min_length=1, max_length=20)
    goal: str = Field(min_length=1)
    test_data: dict[str, Any] = Field(default_factory=dict, alias="testData", max_length=50)
    expected_results: list[str] = Field(alias="expectedResults", min_length=1, max_length=20)
    forbidden_actions: list[str] = Field(default_factory=list, alias="forbiddenActions", max_length=20)
    commerce_steps: list[ScenarioCommerceStep] = Field(default_factory=list, alias="commerceSteps", max_length=100)
    execution_steps: list[ScenarioExecutionStep] = Field(default_factory=list, alias="executionSteps", max_length=100)
    business_objects: list[BusinessObjectLifecycle] = Field(default_factory=list, alias="businessObjects", max_length=100)
    created_at: str = Field(default_factory=utc_now, alias="createdAt")
    updated_at: str = Field(default_factory=utc_now, alias="updatedAt")

    @field_validator("name", "goal")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("场景名称和测试目标不能为空")
        return normalized

    @field_validator("preconditions", "expected_results", "forbidden_actions")
    @classmethod
    def normalize_entries(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))

    @field_validator("test_data")
    @classmethod
    def validate_test_data(cls, values: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        secret_reference = re.compile(r"^<secret:[A-Z][A-Z0-9_]*>$")
        sensitive_key = re.compile(r"password|passwd|token|api[_ -]?key|secret|密码|密钥", re.IGNORECASE)
        for raw_key, value in values.items():
            key = str(raw_key).strip()
            if not key:
                raise ValueError("测试数据字段名不能为空")
            if isinstance(value, (dict, list)):
                raise ValueError(f"测试数据 {key} 只允许字符串、数字、布尔值或 null")
            if sensitive_key.search(key) and value not in (None, "") and not (
                isinstance(value, str) and secret_reference.fullmatch(value.strip())
            ):
                raise ValueError(f"敏感测试数据 {key} 必须使用 <secret:ENV_NAME> 引用")
            normalized[key] = value.strip() if isinstance(value, str) else value
        return normalized

    @model_validator(mode="after")
    def validate_required_lists(self) -> "ScenarioConfig":
        if not self.preconditions:
            raise ValueError("请至少填写一项前置条件；没有前置条件时请明确填写“无”")
        if not self.expected_results:
            raise ValueError("请至少填写一项预期结果")
        indexes = [item.step_index for item in self.commerce_steps]
        if len(indexes) != len(set(indexes)):
            raise ValueError("场景电商步骤不能重复声明同一步骤号")
        execution_indexes = [item.step_index for item in self.execution_steps]
        if len(execution_indexes) != len(set(execution_indexes)):
            raise ValueError("场景浏览器上下文步骤不能重复声明同一步骤号")
        graph = {item.key: item.dependencies for item in self.business_objects}
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("业务对象依赖存在环")
            if key in visited:
                return
            visiting.add(key)
            for dependency in graph.get(key, []):
                if dependency not in graph:
                    raise ValueError(f"业务对象依赖不存在：{dependency}")
                visit(dependency)
            visiting.remove(key)
            visited.add(key)
        for key in graph:
            visit(key)
        return self


class AuditRecord(ApiModel):
    timestamp: str = Field(default_factory=utc_now)
    action: str
    object_type: str = Field(alias="objectType")
    object_id: str = Field(alias="objectId")
    project_id: str = Field(alias="projectId")
    changed_fields: list[str] = Field(default_factory=list, alias="changedFields")
