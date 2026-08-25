from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class BenchmarkScenario(BaseModel):
    schema_version: str = Field(alias="schemaVersion")
    id: str = Field(pattern=r"^S(?:0[1-9]|[12][0-9]|30)$")
    name: str
    category: str
    environment_ref: str = Field(alias="environmentRef")
    account_role: str = Field(alias="accountRole")
    prerequisites: list[dict[str, Any]]
    goal: str
    steps: list[dict[str, Any]]
    assertions: list[dict[str, Any]]
    dangerous_policy: dict[str, Any] = Field(alias="dangerousPolicy")
    cleanup: dict[str, Any]
    evidence_requirements: list[str] = Field(alias="evidenceRequirements")
    binding_status: Literal["ready", "blocked"] = Field(alias="bindingStatus")
    blocked_dependencies: list[str] = Field(default_factory=list, alias="blockedDependencies")
    executable_plan: dict[str, Any] | None = Field(default=None, alias="executablePlan")
    l4_stage: str | None = Field(default=None, alias="l4Stage")

    @model_validator(mode="after")
    def validate_binding(self) -> "BenchmarkScenario":
        if self.binding_status == "ready" and self.executable_plan is None:
            raise ValueError("ready 场景必须提供 executablePlan")
        if self.binding_status == "blocked" and not self.blocked_dependencies:
            raise ValueError("blocked 场景必须列出 blockedDependencies")
        if not self.steps or not self.assertions or not self.evidence_requirements:
            raise ValueError("场景必须包含步骤、断言和证据要求")
        return self


class AcceptanceAttempt(BaseModel):
    scenario_id: str = Field(alias="scenarioId")
    repeat: int
    status: str
    goal_status: str = Field(alias="goalStatus")
    run_id: str = Field(alias="runId")
    completion_reason: str = Field(alias="completionReason")
    blocked_dependencies: list[str] = Field(default_factory=list, alias="blockedDependencies")
    stable_candidate: bool = Field(default=False, alias="stableCandidate")
    stable_success: bool = Field(default=False, alias="stableSuccess")
    adaptive_interventions: int = Field(default=0, alias="adaptiveInterventions")
    visual_interventions: int = Field(default=0, alias="visualInterventions")
    evidence_present: int = Field(default=0, alias="evidencePresent")
    evidence_required: int = Field(default=0, alias="evidenceRequired")
    cleanup_cleared: int = Field(default=0, alias="cleanupCleared")
    cleanup_required: int = Field(default=0, alias="cleanupRequired")
    high_risk_misoperations: int = Field(default=0, alias="highRiskMisoperations")
    plaintext_sensitive_leaks: int = Field(default=0, alias="plaintextSensitiveLeaks")
    naked_coordinate_stable_cases: int = Field(default=0, alias="nakedCoordinateStableCases")
    unauthorized_domain_accesses: int = Field(default=0, alias="unauthorizedDomainAccesses")
    evidence_manifest_path: str | None = Field(default=None, alias="evidenceManifestPath")
    business_ids: list[str] = Field(default_factory=list, alias="businessIds")
    failed_step_ids: list[str] = Field(default_factory=list, alias="failedStepIds")
