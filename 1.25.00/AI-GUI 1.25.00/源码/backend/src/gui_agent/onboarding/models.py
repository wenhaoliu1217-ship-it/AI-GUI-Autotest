"""企业项目接入的数据模型。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ProjectLimits(ApiModel):
    max_steps: int = Field(default=50, alias="maxSteps", ge=1, le=100)
    timeout_seconds: int = Field(default=600, alias="timeoutSeconds", ge=30, le=3600)
    max_model_calls: int = Field(default=20, alias="maxModelCalls", ge=0, le=100)


class ProjectConfig(ApiModel):
    id: str
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(alias="baseUrl")
    allowed_hosts: list[str] = Field(default_factory=list, alias="allowedHosts")
    forbidden_actions: list[str] = Field(default_factory=list, alias="forbiddenActions")
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
    adapter: Literal["generic", "cesium"] = "generic"


class EnvironmentConfig(ApiModel):
    """Non-secret environment data plus references to OS-managed secrets."""

    id: str
    project_id: str = Field(alias="projectId")
    name: str = Field(min_length=1, max_length=120)
    variables: dict[str, str] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict, alias="secretRefs")
    ignore_rules: list[str] = Field(default_factory=list, alias="ignoreRules")
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

    @field_validator("ignore_rules")
    @classmethod
    def normalize_ignore_rules(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))[:100]

    @model_validator(mode="after")
    def validate_variable_namespaces(self) -> "EnvironmentConfig":
        overlap = set(self.variables) & set(self.secret_refs)
        if overlap:
            raise ValueError(f"普通变量与密钥引用不能重名：{sorted(overlap)[0]}")
        return self


class ScenarioConfig(ApiModel):
    id: str
    project_id: str = Field(alias="projectId")
    name: str = Field(min_length=1, max_length=160)
    preconditions: list[str] = Field(min_length=1, max_length=20)
    goal: str = Field(min_length=1)
    test_data: dict[str, Any] = Field(default_factory=dict, alias="testData", max_length=50)
    expected_results: list[str] = Field(alias="expectedResults", min_length=1, max_length=20)
    forbidden_actions: list[str] = Field(default_factory=list, alias="forbiddenActions", max_length=20)
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
        return self


class AuditRecord(ApiModel):
    timestamp: str = Field(default_factory=utc_now)
    action: str
    object_type: str = Field(alias="objectType")
    object_id: str = Field(alias="objectId")
    project_id: str = Field(alias="projectId")
    changed_fields: list[str] = Field(default_factory=list, alias="changedFields")
