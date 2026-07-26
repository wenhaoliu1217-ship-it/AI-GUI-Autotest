from gui_agent.domain.models import ActionType, ExecutionMode, Locator, RelativePosition, StabilityLevel, Step, TestPlan as ExecutionPlan, WaitCondition
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


def test_compiler_supports_shadow_scope_files_download_and_business_polling() -> None:
    scoped_input = Locator(
        placeholder="Search for...",
        shadow_hosts=["ion-assets-page"],
        within=Locator(role="row", name="E2E-run-asset"),
    )
    plan = ExecutionPlan(
        name="Cesium 文件闭环",
        base_url="https://ion.cesium.com",
        steps=[
            Step(
                action=ActionType.UPLOAD_FILES,
                locator=Locator(css="input[type=file]", shadow_hosts=["ion-upload-form"]),
                file_ids=["D01/cesium-e2e-model.glb"],
            ),
            Step(
                action=ActionType.WAIT_UNTIL,
                wait_condition=WaitCondition(source="text", locator=scoped_input, expected="COMPLETE"),
            ),
            Step(
                action=ActionType.DOWNLOAD,
                locator=Locator(role="button", name="Download"),
                download_name_pattern=r".*\.zip",
            ),
        ],
    )

    source, _ = compile_test(plan)

    assert "GUI_TEST_DATA_ROOT" in source and "setInputFiles" in source
    assert ".getByRole(\"row\"" in source and ".locator(\"ion-assets-page\")" in source
    assert ".getByPlaceholder(\"Search for...\"" in source
    assert "expect.poll" in source and "120000" in source
    assert "page.waitForEvent('download')" in source and "suggestedFilename" in source
