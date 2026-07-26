from gui_agent.domain.models import ActionType, ExecutionMode, Locator, RelativePosition, StabilityLevel, Step, TestPlan as ExecutionPlan
from gui_agent.execution.compiler import compile_test


def test_compiler_uses_semantic_locators_and_secret_refs() -> None:
    plan = ExecutionPlan(
        name="登录",
        base_url="https://example.com",
        steps=[
            Step(action=ActionType.FILL, locator=Locator(label="密码"), value_from_secret="PASSWORD"),
            Step(action=ActionType.CLICK, locator=Locator(role="button", name="登录")),
        ],
    )
    source, generated = compile_test(plan)
    assert "getByLabel" in source and "getByRole" in source
    assert "process.env.PASSWORD" in source
    assert generated.stability_level == "A"
    assert generated.ci_eligible is True


def test_compiler_keeps_placeholder_and_scoped_business_identity() -> None:
    plan = ExecutionPlan(
        name="作用域定位",
        base_url="https://example.com",
        steps=[
            Step(action=ActionType.FILL, locator=Locator(placeholder="请输入租户"), value="qa"),
            Step(
                action=ActionType.CLICK,
                locator=Locator(
                    role="button", name="删除",
                    scope={
                        "kind": "row",
                        "locator": {"attribute": {"name": "data-object-id", "value": "agent-42"}},
                        "identity": "E2E_Agent_42",
                    },
                ),
            ),
        ],
    )

    source, _ = compile_test(plan)

    assert 'getByPlaceholder("请输入租户")' in source
    assert '[data-object-id=\\"agent-42\\"]' in source
    assert 'filter({ hasText: "E2E_Agent_42" }).getByRole("button", { name: "删除" })' in source


def test_visual_compiler_keeps_relative_coordinates_only() -> None:
    plan = ExecutionPlan(
        name="Canvas 选择",
        base_url="https://example.com",
        steps=[Step(
            action=ActionType.VISUAL_CLICK,
            execution_mode=ExecutionMode.VISUAL,
            stability_level=StabilityLevel.C,
            locator=Locator(css="canvas"),
            visual_target="目标 A",
            relative_position=RelativePosition(xRatio=0.25, yRatio=0.75),
        )],
    )
    source, generated = compile_test(plan)
    assert "visualBox.width * 0.25" in source
    assert "visualBox.height * 0.75" in source
    assert generated.supported_replay_modes == ["adaptive"]
    assert generated.ci_eligible is False


def test_d_level_step_is_manual_and_skips_the_generated_test() -> None:
    plan = ExecutionPlan(
        name="硬件认证",
        base_url="https://example.com",
        steps=[Step(
            action=ActionType.CLICK,
            locator=Locator(role="button", name="使用安全密钥"),
            description="触摸硬件安全密钥",
            stability_level=StabilityLevel.D,
            stability_reason="需要人工操作硬件",
        )],
    )

    source, generated = compile_test(plan)

    assert "test.skip(true" in source
    assert "// MANUAL [D]: 触摸硬件安全密钥" in source
    assert ".click()" not in source
    assert generated.manual_steps == ["触摸硬件安全密钥"]
    assert generated.supported_replay_modes == []
    assert generated.ci_eligible is False


def test_compiler_generates_manifest_upload_and_run_scoped_download() -> None:
    plan = ExecutionPlan.model_validate({
        "name": "File flow", "base_url": "https://example.com", "steps": [
            {"action": "upload", "locator": {"label": "Agent JSON"}, "file_id": "file-0123456789ab", "business_object_name": "E2E_Agent"},
            {"action": "download", "locator": {"role": "link", "name": "Export"}, "business_object_name": "E2E_Run", "download_validation": {"extension": ".json", "format": "json", "requiredJsonKeys": ["runId"]}},
        ],
    })

    source, _ = compile_test(plan)

    assert "process.env.TEST_FILE_FILE_0123456789AB" in source
    assert ".setInputFiles(uploadPath1)" in source
    assert "testInfo.outputPath('downloads'" in source
    assert "createHash('sha256')" in source
    assert "toHaveProperty(\"runId\")" in source
