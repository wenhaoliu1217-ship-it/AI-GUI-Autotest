import json
from pathlib import Path

import pytest

from gui_agent.acceptance import ScenarioBindingError, compile_scenario, load_scenarios
from gui_agent.domain.models import TestPlan as PlanModel
from gui_agent.onboarding.models import AccountProfile, BusinessContext, ComponentAdapter, EnvironmentConfig, ProjectConfig


CATALOG = Path(__file__).resolve().parents[2] / "benchmarks" / "gaealavic" / "scenarios"
ADAPTER_TEMPLATE = Path(__file__).resolve().parents[2] / "examples" / "gaealavic-project-adapter.template.json"


def project() -> ProjectConfig:
    return ProjectConfig(
        id="project-gae",
        name="GAEALaViC 企业验收",
        baseUrl="http://192.168.31.218:7991",
        allowPrivateNetwork=True,
        accountProfiles=[
            AccountProfile(id="tester", name="普通测试", role="tester"),
            AccountProfile(id="readonly", name="只读", role="readonly"),
            AccountProfile(id="admin", name="管理员", role="admin"),
            AccountProfile(id="developer", name="运维开发", role="developer"),
        ],
    )


def environment() -> EnvironmentConfig:
    return EnvironmentConfig(
        id="environment-gae",
        projectId="project-gae",
        name="企业验收",
        secretRefs={"TENANT_CODE": "TENANT_CODE", "LOGIN_USERNAME": "LOGIN_USERNAME", "LOGIN_PASSWORD": "INTRANET_TEST001_PASSWORD"},
    )


def test_all_30_scenarios_compile_to_valid_test_plans_when_runtime_bindings_are_complete() -> None:
    account_ids = {"普通测试": "tester", "只读": "readonly", "管理员": "admin", "运维/开发者": "developer"}
    for scenario in load_scenarios(CATALOG):
        step_bindings = {
            item["id"]: [{"action": "click", "locator": {"role": "button", "name": item["businessAction"]}, "description": item["businessAction"]}]
            for item in scenario.steps
        }
        assertion_bindings = {
            item["id"]: [{"type": "visible", "locator": {"text": item["businessFact"]}, "description": item["businessFact"]}]
            for item in scenario.assertions
        }
        compiled = compile_scenario(
            scenario, project(), environment(), account_id=account_ids[scenario.account_role],
            step_bindings=step_bindings, assertion_bindings=assertion_bindings, test_files=[],
        )

        assert PlanModel.model_validate(compiled.as_dict()["plan"])
        assert compiled.scenario.id == scenario.id


def test_missing_runtime_binding_reports_exact_scenario_step() -> None:
    scenario = load_scenarios(CATALOG)[0]
    with pytest.raises(ScenarioBindingError) as error:
        compile_scenario(
            scenario, project(), environment(), account_id="tester",
            step_bindings={}, assertion_bindings={}, test_files=[],
        )

    assert "缺少步骤绑定：S01/step-1" in error.value.blocked_items
    assert "缺少断言绑定：S01/assertion-1" in error.value.blocked_items


def test_missing_secret_and_file_are_runtime_blockers() -> None:
    scenario = load_scenarios(CATALOG)[0]
    with pytest.raises(ScenarioBindingError) as error:
        compile_scenario(
            scenario,
            project(),
            EnvironmentConfig(id="environment-gae", projectId="project-gae", name="空环境"),
            account_id="tester",
            step_bindings={"step-1": [{"action": "fill", "locator": {"label": "密码"}, "value_from_secret": "LOGIN_PASSWORD"}]},
            assertion_bindings={
                "assertion-1": [{"type": "visible", "locator": {"text": "首页"}}],
                "assertion-2": [{"type": "visible", "locator": {"text": "账号"}}],
            },
            test_files=[],
        )

    assert error.value.blocked_items == ["测试环境缺少密钥引用：LOGIN_PASSWORD"]


def test_guide_adapter_template_is_valid_and_covers_confirmed_target_features() -> None:
    payload = json.loads(ADAPTER_TEMPLATE.read_text(encoding="utf-8"))
    context = BusinessContext.model_validate(payload["businessContext"])
    adapters = [ComponentAdapter.model_validate(item) for item in payload["componentAdapters"]]
    adapter_ids = {item.id for item in adapters}

    assert len(payload["agentDetailTabs"]) == 17
    assert payload["hgtValidation"]["sampleDimensions"] == [1201, 3601]
    assert payload["knownRoutes"]["run"] == "/#/DoEDetails"
    assert "容器重启" in payload["forbiddenOperations"]
    assert {"account.standard_mode", "account.developer_mode", "account.layer_switch", "account.satellite_layer", "account.runtime_status", "account.version"} <= adapter_ids
    assert {"agent.modify", "agent.preview", "agent.batch", "agent.export"} <= adapter_ids
    assert {"environment.upload", "environment.gis_import", "environment.thumbnail", "environment.thumbnail_remove"} <= adapter_ids
    assert {"elevation.upload", "elevation.preview", "run.filter", "run.time_range", "run.batch_delete", "run.export"} <= adapter_ids
    assert {"rl.start", "rl.stop", "rl.result", "rl.delete", "help.manuals", "help.scroll", "help.workflow_image"} <= adapter_ids
    assert "容器重启和运维操作禁止自动执行" in context.operating_boundaries
