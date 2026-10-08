from types import SimpleNamespace

from gui_agent.site_capabilities.intranet_intent import compile_intranet_intent


def _scenario(goal: str, test_data=None):
    return SimpleNamespace(name="runtime task", goal=goal, test_data=test_data or {})


def test_dynamic_scenario_name_is_a_parameter_not_an_allowlisted_script() -> None:
    test_d = compile_intranet_intent(_scenario("找到 test D，进入想定"))
    arbitrary = compile_intranet_intent(_scenario("找到 蓝军临时想定-09，进入想定并启动"))

    assert test_d.target_name == "test D"
    assert test_d.object_kind == "scenario"
    assert test_d.safe_for_local_routing is True
    assert arbitrary.target_name == "蓝军临时想定-09"
    assert arbitrary.object_kind == "scenario"
    assert arbitrary.safe_for_local_routing is True


def test_labeled_name_stops_before_following_operation() -> None:
    intent = compile_intranet_intent(_scenario("打开想定名称为 新测试01 并启动仿真"))

    assert intent.target_name == "新测试01"
    assert intent.operations == ("open", "start")


def test_current_test_data_can_supply_a_new_runtime_name() -> None:
    intent = compile_intranet_intent(
        _scenario("打开指定想定并保存", {"scenarioName": "本轮随机名称-20260905"})
    )

    assert intent.target_name == "本轮随机名称-20260905"
    assert intent.safe_for_local_routing is True


def test_delete_is_never_eligible_for_local_routing() -> None:
    intent = compile_intranet_intent(_scenario("找到 test_D 并删除该想定"))

    assert intent.delete_requested is True
    assert intent.safe_for_local_routing is False
