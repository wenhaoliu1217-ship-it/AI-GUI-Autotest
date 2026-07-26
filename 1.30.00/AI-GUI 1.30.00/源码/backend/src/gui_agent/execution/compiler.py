"""Compile an approved plan to maintainable Playwright TypeScript."""

from __future__ import annotations

import json

from ..domain.models import ActionType, Locator, Step, TestPlan
from ..domain.results import GeneratedTest


_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def compile_test(plan: TestPlan, source_path: str = "generated-test.spec.ts") -> tuple[str, GeneratedTest]:
    levels = [step.stability_level.value for step in plan.steps]
    overall = max(levels, key=lambda level: _RANK[level], default="A")
    lines = [
        "import { test, expect } from '@playwright/test';",
        "",
        f"test({json.dumps(plan.name, ensure_ascii=False)}, async ({{ page }}) => {{",
    ]
    manual_steps = [step.description or f"步骤 {index}" for index, step in enumerate(plan.steps, start=1) if step.stability_level.value == "D"]
    if manual_steps:
        lines.append(f"  test.skip(true, {json.dumps('包含 D 级人工步骤，禁止作为自动化测试执行', ensure_ascii=False)});")
    for index, step in enumerate(plan.steps, start=1):
        lines.append(f"  await test.step({json.dumps(step.description or f'步骤 {index}', ensure_ascii=False)}, async () => {{")
        lines.extend(f"    {line}" for line in _compile_step(step, plan.base_url))
        lines.append("  });")
    for assertion in plan.assertions:
        target = _locator(assertion.locator) if assertion.locator else None
        if assertion.type.value in {"page_reached", "url_contains"}:
            lines.append(f"  await expect(page).toHaveURL(new RegExp({json.dumps(assertion.expected or '')}));")
        elif assertion.type.value == "visible":
            lines.append(f"  await expect({target}).toBeVisible();")
        elif assertion.type.value == "not_visible":
            lines.append(f"  await expect({target}).toBeHidden();")
        elif assertion.type.value == "text_contains":
            lines.append(f"  await expect({target}).toContainText({json.dumps(assertion.expected or '', ensure_ascii=False)});")
        elif assertion.type.value == "value_equals":
            lines.append(f"  await expect({target}).toHaveValue({json.dumps(assertion.expected or '', ensure_ascii=False)});")
        elif assertion.type.value == "count_equals":
            lines.append(f"  await expect({target}).toHaveCount({assertion.count or 0});")
    lines.append("});")
    source = "\n".join(lines) + "\n"
    modes = ["stable"] if overall in {"A", "B"} else ["adaptive"] if overall == "C" else []
    generated = GeneratedTest(
        source_path=source_path,
        stability_level=overall,
        supported_replay_modes=modes,
        ci_eligible=overall == "A",
        ci_recommendation=(
            "可作为 CI 候选" if overall == "A" else
            "可稳定回放，但应先处理 B 级风险" if overall == "B" else
            "仅允许显式自适应回放，不作为严格 CI 门禁" if overall == "C" else
            "包含暂不可自动化步骤，需人工处理"
        ),
        source=source,
        manual_steps=manual_steps,
    )
    return source, generated


def _compile_step(step: Step, base_url: str) -> list[str]:
    if step.stability_level.value == "D":
        description = step.description or step.action.value
        return [f"// MANUAL [D]: {description}"]
    if step.action == ActionType.NAVIGATE:
        target = step.target or "/"
        url = target if "://" in target else base_url.rstrip("/") + "/" + target.lstrip("/")
        return [f"await page.goto({json.dumps(url)});"]
    if step.action == ActionType.SCREENSHOT:
        return ["await page.screenshot({ fullPage: false });"]
    if step.action == ActionType.BACK:
        return ["await page.goBack();"]
    if step.action == ActionType.RELOAD:
        return ["await page.reload();"]
    if step.action == ActionType.BRIDGE_CLICK:
        target_id = json.dumps(step.bridge_target_id, ensure_ascii=False)
        return [
            "const bridgeGlobalName = process.env.APP_BRIDGE_GLOBAL || '__WEB_AI_TEST__';",
            f"const bridgeResult = await page.evaluate(async (args) => {{",
            "  const { id, globalName } = args;",
            "  const bridge = (window as any)[globalName];",
            "  const required = ['getSceneState', 'listVisibleTargets', 'getTargetScreenPosition', 'getSelectedTargetId', 'waitForSceneReady'];",
            "  if (!bridge || String(bridge.version || '') !== '1' || required.some((name) => typeof bridge[name] !== 'function')) throw new Error('Canvas App Bridge v1 contract is unavailable');",
            "  await bridge.waitForSceneReady();",
            "  const before = await bridge.getSceneState();",
            "  const targets = await bridge.listVisibleTargets();",
            "  if (!targets.some((target) => (target.id || target.targetId) === id)) throw new Error(`Bridge target is not visible: ${id}`);",
            "  const position = await bridge.getTargetScreenPosition(id);",
            "  return { position, before };",
            f"}}, {{ id: {target_id}, globalName: bridgeGlobalName }});",
            "const bridgePosition = bridgeResult.position;",
            "await page.mouse.click(bridgePosition.x, bridgePosition.y);",
            "const bridgeVerification = await page.evaluate(async (args) => {",
            "  const bridge = (window as any)[args.globalName];",
            "  await bridge.waitForSceneReady();",
            "  return { selectedTargetId: await bridge.getSelectedTargetId(), after: await bridge.getSceneState() };",
            f"}}, {{ id: {target_id}, globalName: bridgeGlobalName }});",
            "if (bridgeVerification.selectedTargetId !== " + target_id + ") throw new Error('Bridge semantic selection verification failed');",
        ]
    target = _locator(step.locator) if step.locator else "page"
    if step.action in {ActionType.VISUAL_CLICK, ActionType.VISUAL_HOVER, ActionType.VISUAL_SCROLL, ActionType.VISUAL_DRAG}:
        position = step.relative_position
        assert position is not None
        box_lines = ([f"const visualBox = await {target}.boundingBox();", "if (!visualBox) throw new Error('Visual region is not visible');"]
                     if step.locator else ["const viewport = page.viewportSize();", "if (!viewport) throw new Error('Viewport is unavailable');", "const visualBox = { x: 0, y: 0, width: viewport.width, height: viewport.height };"])
        point = f"visualBox.x + visualBox.width * {position.x_ratio}, visualBox.y + visualBox.height * {position.y_ratio}"
        if step.action == ActionType.VISUAL_CLICK:
            return box_lines + [f"await page.mouse.click({point});"]
        if step.action == ActionType.VISUAL_HOVER:
            return box_lines + [f"await page.mouse.move({point});"]
        if step.action == ActionType.VISUAL_SCROLL:
            return box_lines + [f"await page.mouse.move({point});", f"await page.mouse.wheel(0, {step.scroll_delta_y});"]
        end = step.relative_end_position
        assert end is not None
        end_point = f"visualBox.x + visualBox.width * {end.x_ratio}, visualBox.y + visualBox.height * {end.y_ratio}"
        return box_lines + [f"await page.mouse.move({point});", "await page.mouse.down();", f"await page.mouse.move({end_point}, {{ steps: 10 }});", "await page.mouse.up();"]
    methods = {
        ActionType.CLICK: "click()", ActionType.CLEAR: "clear()", ActionType.CHECK: "check()",
        ActionType.UNCHECK: "uncheck()", ActionType.HOVER: "hover()",
        ActionType.WAIT_FOR: "waitFor({ state: 'visible' })",
    }
    if step.action in methods:
        return [f"await {target}.{methods[step.action]};"]
    if step.action in {ActionType.FILL, ActionType.SELECT}:
        value = f"process.env.{step.value_from_secret}" if step.value_from_secret else json.dumps(step.value or "", ensure_ascii=False)
        method = "fill" if step.action == ActionType.FILL else "selectOption"
        return [f"await {target}.{method}({value});"]
    if step.action == ActionType.PRESS:
        return [f"await {target}.press({json.dumps(step.value or '')});"]
    if step.action == ActionType.SCROLL:
        prefix = [f"await {target}.scrollIntoViewIfNeeded();"] if step.locator else []
        return prefix + [f"await page.mouse.wheel(0, {step.scroll_delta_y});"]
    return [f"// Manual step required: {step.action.value}"]


def _locator(locator: Locator | None) -> str:
    if locator is None:
        raise ValueError("生成测试时缺少 locator")
    if locator.role:
        option = f", {{ name: {json.dumps(locator.name, ensure_ascii=False)} }}" if locator.name else ""
        return f"page.getByRole({json.dumps(locator.role)}{option})"
    if locator.label:
        return f"page.getByLabel({json.dumps(locator.label, ensure_ascii=False)})"
    if locator.test_id:
        return f"page.getByTestId({json.dumps(locator.test_id, ensure_ascii=False)})"
    if locator.text:
        return f"page.getByText({json.dumps(locator.text, ensure_ascii=False)})"
    return f"page.locator({json.dumps(locator.css or '')})"
