"""Compile guide scenarios into executable plans using reviewed runtime bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import ValidationError

from ..domain.models import Assertion, Step, TestPlan
from ..onboarding.models import EnvironmentConfig, ProjectConfig, TestFileRecord
from .models import BenchmarkScenario


class ScenarioBindingError(ValueError):
    """The scenario is valid, but required target-specific bindings are missing."""

    def __init__(self, blocked_items: list[str]) -> None:
        self.blocked_items = list(dict.fromkeys(blocked_items))
        super().__init__("；".join(self.blocked_items))


@dataclass(frozen=True)
class CompiledScenario:
    scenario: BenchmarkScenario
    plan: TestPlan
    account_id: str
    file_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenarioId": self.scenario.id,
            "bindingStatus": "ready",
            "accountId": self.account_id,
            "fileIds": list(self.file_ids),
            "plan": self.plan.model_dump(mode="json", exclude_none=True),
            "blockedDependencies": [],
        }


def compile_scenario(
    scenario: BenchmarkScenario,
    project: ProjectConfig,
    environment: EnvironmentConfig,
    *,
    account_id: str,
    step_bindings: Mapping[str, list[dict[str, Any]]],
    assertion_bindings: Mapping[str, list[dict[str, Any]]],
    test_files: list[TestFileRecord],
) -> CompiledScenario:
    """Bind every abstract guide action/fact to reviewed executable contracts.

    Bindings are deliberately supplied at run time. This keeps S01-S30 usable for
    different deployments without storing guessed selectors or credentials in the
    benchmark catalog.
    """

    blocked: list[str] = []
    if environment.project_id != project.id:
        blocked.append("所选环境不属于验收项目")
    account = next((item for item in project.account_profiles if item.id == account_id), None)
    if account is None:
        blocked.append(f"账号槽位不存在：{account_id}")
    elif not _role_matches(scenario.account_role, account.role):
        blocked.append(f"账号角色不匹配：场景要求 {scenario.account_role}，当前为 {account.role}")

    raw_steps: list[dict[str, Any]] = []
    for abstract in scenario.steps:
        binding_id = str(abstract.get("id", ""))
        bound = step_bindings.get(binding_id)
        if not bound:
            blocked.append(f"缺少步骤绑定：{scenario.id}/{binding_id}")
        else:
            raw_steps.extend(bound)

    raw_assertions: list[dict[str, Any]] = []
    for abstract in scenario.assertions:
        binding_id = str(abstract.get("id", ""))
        bound = assertion_bindings.get(binding_id)
        if not bound:
            blocked.append(f"缺少断言绑定：{scenario.id}/{binding_id}")
        else:
            raw_assertions.extend(bound)

    if blocked:
        raise ScenarioBindingError(blocked)

    try:
        steps = [Step.model_validate(item) for item in raw_steps]
        assertions = [Assertion.model_validate(item) for item in raw_assertions]
    except ValidationError as exc:
        raise ScenarioBindingError([f"动作或断言绑定不符合 TestPlan 契约：{exc.errors()[0]['msg']}"]) from exc

    file_index = {item.id: item for item in test_files}
    file_ids = sorted({step.file_id for step in steps if step.file_id})
    missing_files = [file_id for file_id in file_ids if file_id not in file_index]
    if missing_files:
        raise ScenarioBindingError([f"项目测试文件不存在：{file_id}" for file_id in missing_files])

    adapter_index = {item.id: item for item in project.component_adapters}
    adapter_errors: list[str] = []
    for step in steps:
        if not step.component_adapter_id:
            continue
        adapter = adapter_index.get(step.component_adapter_id)
        if adapter is None:
            adapter_errors.append(f"复杂组件 Adapter 不存在：{step.component_adapter_id}")
        elif adapter.status != "configured":
            adapter_errors.append(f"复杂组件 Adapter 尚未配置：{step.component_adapter_id}")
        elif adapter.action.model_dump(mode="json", by_alias=True, exclude_none=True) != step.component.model_dump(mode="json", by_alias=True, exclude_none=True):
            adapter_errors.append(f"复杂组件动作与 Adapter 不一致：{step.component_adapter_id}")
    if adapter_errors:
        raise ScenarioBindingError(adapter_errors)

    required_secrets = {step.value_from_secret for step in steps if step.value_from_secret}
    configured_secrets = set(environment.secret_refs) | set(environment.variables)
    missing_secrets = sorted(required_secrets - configured_secrets)
    if missing_secrets:
        raise ScenarioBindingError([f"测试环境缺少密钥引用：{name}" for name in missing_secrets])

    try:
        plan = TestPlan(
            name=f"{scenario.id} {scenario.name}",
            base_url=project.base_url,
            role=scenario.account_role,
            preconditions=[{"description": str(item.get("description", ""))} for item in scenario.prerequisites],
            steps=steps,
            assertions=assertions,
        )
    except ValidationError as exc:
        raise ScenarioBindingError([f"无法生成有效 TestPlan：{exc.errors()[0]['msg']}"]) from exc
    return CompiledScenario(scenario, plan, account_id, tuple(file_ids))


def _role_matches(required: str, actual: str) -> bool:
    normalized_required = required.strip().lower()
    normalized_actual = actual.strip().lower()
    if normalized_required == normalized_actual:
        return True
    aliases = {
        "普通测试": {"tester", "普通测试", "测试"},
        "只读": {"readonly", "read_only", "只读"},
        "管理员": {"admin", "administrator", "管理员"},
        "运维/开发者": {"developer", "operator", "ops", "运维/开发者", "运维", "开发者"},
    }
    return normalized_actual in aliases.get(required, set())
