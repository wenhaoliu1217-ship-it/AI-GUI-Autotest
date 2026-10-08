from .generic_planner import plan_from_draft


def test_initial_navigation_phrases_are_covered_without_warning() -> None:
    target = "https://ion.cesium.com/stories/example"

    for flow in (
        "导航到目标 URL",
        "打开地址 https://ion.cesium.com/stories/example",
    ):
        result = plan_from_draft(
            name="Cesium 登录接管回归",
            target_url=target,
            flow=flow,
            expectation="确认看到“Stories”",
        )

        assert result.warnings == []
        assert result.plan.steps[0].target == "/"
        assert len(result.plan.steps) == 1


def test_read_only_and_generic_access_clauses_do_not_block_fast_path() -> None:
    result = plan_from_draft(
        name="Cesium 只读访问验证",
        target_url="https://ion.cesium.com/stories/example",
        flow="确认已登录账号可以访问该页面；确认看到“Edit story”；全程只读，不修改任何内容",
        expectation="确认看到“Edit story”",
    )

    assert result.warnings == []
    assert len(result.plan.steps) == 1
    assert len(result.plan.assertions) == 1
    assert result.plan.assertions[0].locator is not None
    assert result.plan.assertions[0].locator.text == "Edit story"
