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


def test_canvas_polygon_compiler_emits_relative_vertex_sequence_and_finish() -> None:
    plan = ExecutionPlan(
        name="地图面积测量",
        base_url="https://example.com",
        steps=[Step(
            action=ActionType.VISUAL_DRAW_POLYGON,
            execution_mode=ExecutionMode.VISUAL,
            stability_level=StabilityLevel.C,
            canvas_region_locator=Locator(css="canvas"),
            visual_target="天安门广场边界",
            visual_points=[
                RelativePosition(xRatio=0.2, yRatio=0.2),
                RelativePosition(xRatio=0.8, yRatio=0.2),
                RelativePosition(xRatio=0.8, yRatio=0.8),
                RelativePosition(xRatio=0.2, yRatio=0.8),
            ],
            gesture_finish="double_click",
        )],
    )

    source, generated = compile_test(plan)

    assert "const canvasRegion = activePage.locator(\"canvas\")" in source
    assert "canvasBox.width * 0.2" in source
    assert "canvasBox.height * 0.8" in source
    assert source.count("activePage.mouse.click") == 3
    assert "activePage.mouse.dblclick" in source
    assert generated.supported_replay_modes == ["adaptive"]


def test_canvas_rectangle_compiler_emits_bounded_drag() -> None:
    plan = ExecutionPlan(
        name="地图矩形框选",
        base_url="https://example.com",
        steps=[Step(
            action=ActionType.VISUAL_DRAW_RECTANGLE,
            execution_mode=ExecutionMode.VISUAL,
            stability_level=StabilityLevel.B,
            canvas_region_locator=Locator(css="#map"),
            visual_target="目标区域",
            visual_points=[
                RelativePosition(xRatio=0.1, yRatio=0.2),
                RelativePosition(xRatio=0.9, yRatio=0.8),
            ],
        )],
    )

    source, _ = compile_test(plan)

    assert "activePage.mouse.down()" in source
    assert "steps: 10" in source
    assert "activePage.mouse.up()" in source


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


def test_compiler_preserves_unique_commerce_scope_without_first() -> None:
    plan = ExecutionPlan(
        name="scoped cart action",
        base_url="https://example.com",
        steps=[Step(
            action=ActionType.CLICK,
            locator=Locator(role="button", name="Add to cart"),
            commerceScope={
                "kind": "product_card",
                "container": {"css": "[data-product-card]"},
                "anchor": {"text": "E2E Product A"},
                "excludedMarkers": [{"css": "[data-ad]"}],
            },
        )],
    )

    source, _ = compile_test(plan)

    assert "filter({ has:" in source
    assert "filter({ hasNot:" in source
    assert "await expect(commerceContainer).toHaveCount(1)" in source
    assert "await expect(commerceTarget).toHaveCount(1)" in source
    assert "commerceTarget.click()" in source
    assert ".first()" not in source


def test_compiler_emits_bounded_read_and_proof_first_write_recovery() -> None:
    plan = ExecutionPlan(
        name="safe recovery",
        base_url="https://example.com",
        steps=[
            Step(action="navigate", target="/catalog"),
            Step(
                action="click", locator={"text": "提交订单"},
                commerce={
                    "action": "submit_order", "targetKind": "orderId",
                    "targetRef": "resource:E2E_ORDER", "beforeState": "draft",
                    "idempotencyKeyRef": "secret:E2E_ORDER_KEY", "e2eOwned": True,
                    "stateProbe": {
                        "domain": "order", "url": "/state/${RUN_ID}",
                        "jsonPath": "state", "expectedState": "pending_payment",
                    },
                },
            ),
        ],
    )

    source, _ = compile_test(plan)

    assert "withReadRecovery" in source
    assert "status === 429" in source and "status >= 500" in source
    assert "side_effect_outcome_unknown" in source
    assert "idempotencyKeySha256" in source
    assert "recoveredState !== \"draft\"" in source
