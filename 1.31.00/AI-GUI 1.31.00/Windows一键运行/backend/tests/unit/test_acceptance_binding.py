import pytest

from gui_agent.acceptance import ScenarioBindingError, compile_scenario
from gui_agent.acceptance.models import BenchmarkScenario
from gui_agent.domain.models import TestPlan as PlanModel
from gui_agent.onboarding.models import AccountProfile, EnvironmentConfig, ProjectConfig


def scenarios() -> list[BenchmarkScenario]:
    return [BenchmarkScenario.model_validate({
        "schemaVersion": "1.0", "id": f"S{index:02d}", "name": f"通用场景 {index}",
        "category": "cross-site", "environmentRef": "test", "accountRole": "tester",
        "prerequisites": [{"description": "环境可用"}], "goal": f"目标 {index}",
        "steps": [{"id": "step-1", "businessAction": "打开目标"}],
        "assertions": [{"id": "assertion-1", "businessFact": "目标可见"}],
        "dangerousPolicy": {"mode": "confirm"}, "cleanup": {"required": False},
        "evidenceRequirements": ["screenshot"], "bindingStatus": "blocked",
        "blockedDependencies": ["目标站运行时绑定"],
    }) for index in range(1, 31)]


def project() -> ProjectConfig:
    return ProjectConfig(
        id="project-generic",
        name="通用企业验收",
        baseUrl="https://example.com",
        accountProfiles=[
            AccountProfile(id="tester", name="普通测试", role="tester"),
        ],
    )


def environment() -> EnvironmentConfig:
    return EnvironmentConfig(
        id="environment-generic",
        projectId="project-generic",
        name="企业验收",
        secretRefs={"TENANT_CODE": "TENANT_CODE", "LOGIN_USERNAME": "LOGIN_USERNAME", "LOGIN_PASSWORD": "INTRANET_TEST001_PASSWORD"},
    )


def test_all_30_scenarios_compile_to_valid_test_plans_when_runtime_bindings_are_complete() -> None:
    for scenario in scenarios():
        step_bindings = {
            item["id"]: [{"action": "click", "locator": {"role": "button", "name": item["businessAction"]}, "description": item["businessAction"]}]
            for item in scenario.steps
        }
        assertion_bindings = {
            item["id"]: [{"type": "visible", "locator": {"text": item["businessFact"]}, "description": item["businessFact"]}]
            for item in scenario.assertions
        }
        compiled = compile_scenario(
            scenario, project(), environment(), account_id="tester",
            step_bindings=step_bindings, assertion_bindings=assertion_bindings, test_files=[],
        )

        assert PlanModel.model_validate(compiled.as_dict()["plan"])
        assert compiled.scenario.id == scenario.id


def test_missing_runtime_binding_reports_exact_scenario_step() -> None:
    scenario = scenarios()[0]
    with pytest.raises(ScenarioBindingError) as error:
        compile_scenario(
            scenario, project(), environment(), account_id="tester",
            step_bindings={}, assertion_bindings={}, test_files=[],
        )

    assert "缺少步骤绑定：S01/step-1" in error.value.blocked_items
    assert "缺少断言绑定：S01/assertion-1" in error.value.blocked_items


def test_missing_secret_and_file_are_runtime_blockers() -> None:
    scenario = scenarios()[0]
    with pytest.raises(ScenarioBindingError) as error:
        compile_scenario(
            scenario,
            project(),
            EnvironmentConfig(id="environment-generic", projectId="project-generic", name="空环境"),
            account_id="tester",
            step_bindings={"step-1": [{"action": "fill", "locator": {"label": "密码"}, "value_from_secret": "LOGIN_PASSWORD"}]},
            assertion_bindings={
                "assertion-1": [{"type": "visible", "locator": {"text": "首页"}}],
            },
            test_files=[],
        )

    assert error.value.blocked_items == ["测试环境缺少密钥引用：LOGIN_PASSWORD"]


def test_generic_catalog_contains_no_target_specific_constants() -> None:
    serialized = " ".join(item.model_dump_json() for item in scenarios())
    assert "GAEALaViC" not in serialized
    assert "Cesium" not in serialized
    assert "192.168." not in serialized
