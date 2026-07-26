"""Compile an approved plan to maintainable Playwright TypeScript."""

from __future__ import annotations

import json

from ..domain.models import ActionType, Locator, Step, TestPlan
from ..domain.results import GeneratedTest


_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def compile_test(plan: TestPlan, source_path: str = "generated-test.spec.ts") -> tuple[str, GeneratedTest]:
    levels = [step.stability_level.value for step in plan.steps]
    overall = max(levels, key=lambda level: _RANK[level], default="A")
    has_file_steps = any(
        step.action in {ActionType.UPLOAD, ActionType.DOWNLOAD}
        or (step.action == ActionType.COMPONENT and step.component and step.component.kind == "upload_dialog")
        for step in plan.steps
    )
    lines = ["import { test, expect } from '@playwright/test';"]
    if has_file_steps:
        lines.extend(["import { readFileSync } from 'node:fs';", "import { createHash } from 'node:crypto';"])
    lines.extend(["", f"test({json.dumps(plan.name, ensure_ascii=False)}, async ({{ page }}, testInfo) => {{"])
    manual_steps = [step.description or f"步骤 {index}" for index, step in enumerate(plan.steps, start=1) if step.stability_level.value == "D"]
    if manual_steps:
        lines.append(f"  test.skip(true, {json.dumps('包含 D 级人工步骤，禁止作为自动化测试执行', ensure_ascii=False)});")
    for index, step in enumerate(plan.steps, start=1):
        lines.append(f"  await test.step({json.dumps(step.description or f'步骤 {index}', ensure_ascii=False)}, async () => {{")
        lines.extend(f"    {line}" for line in _compile_step(step, plan.base_url, index))
        lines.append("  });")
    for assertion_index, assertion in enumerate(plan.assertions, start=1):
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
        elif assertion.type.value.startswith("canvas_"):
            lines.extend(f"  {line}" for line in _compile_canvas_assertion(assertion, assertion_index))
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


def _compile_canvas_assertion(assertion, index: int) -> list[str]:
    variable = f"canvasState{index}"
    lines = [
        f"const {variable} = await page.evaluate(async (globalName) => {{",
        "  const bridge = (window as any)[globalName];",
        "  if (!bridge || typeof bridge.getSceneState !== 'function') throw new Error('App Bridge semantic state unavailable');",
        "  await bridge.waitForSceneReady();",
        "  return bridge.getSceneState();",
        "}, process.env.APP_BRIDGE_GLOBAL || '__WEB_AI_TEST__');",
    ]
    kind = assertion.type.value
    if kind == "canvas_layer_visible":
        expected = json.dumps(assertion.expected, ensure_ascii=False)
        lines.extend([f"const canvasLayer{index} = {variable}.layers.find((item: any) => item.id === {expected} || item.name === {expected});", f"expect(canvasLayer{index}?.visible ?? canvasLayer{index}?.show).toBe(true);"])
    elif kind == "canvas_camera_equals":
        expected = json.loads(assertion.expected or "{}")
        for key, value in expected.items():
            if isinstance(value, (int, float)):
                lines.append(f"expect(Math.abs(Number({variable}.camera[{json.dumps(key)}]) - {value})).toBeLessThanOrEqual({assertion.tolerance});")
            else:
                lines.append(f"expect({variable}.camera[{json.dumps(key)}]).toEqual({json.dumps(value, ensure_ascii=False)});")
    elif kind == "canvas_selected_entity":
        lines.append(f"expect({variable}.selectedEntityId ?? {variable}.selectedTargetId).toBe({json.dumps(assertion.expected, ensure_ascii=False)});")
    elif kind == "canvas_tiles_loaded":
        lines.append(f"expect({variable}.tilesLoaded).toBe(true);")
    elif kind == "canvas_webgl_no_error":
        lines.append(f"expect({variable}.webglError ?? null).toBeNull();")
    else:
        key = {
            "canvas_entity_count": "entityCount", "canvas_path_point_count": "pathPoints",
            "canvas_poi_count": "pois", "canvas_fence_count": "fences", "canvas_drawing_count": "drawings",
        }[kind]
        expression = f"{variable}.{key}.length" if key != "entityCount" else f"{variable}.entityCount"
        lines.append(f"expect({expression}).toBe({assertion.count});")
    return lines


def _compile_step(step: Step, base_url: str, index: int = 1) -> list[str]:
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
    if step.action == ActionType.UPLOAD:
        target = _locator(step.locator)
        variable = "TEST_FILE_" + "".join(char if char.isalnum() else "_" for char in (step.file_id or "")).upper()
        lines = [
            f"const uploadPath{index} = process.env.{variable};",
            f"if (!uploadPath{index}) throw new Error('Missing registered test file environment: {variable}');",
            f"await {target}.setInputFiles(uploadPath{index});",
        ]
        if step.residual_object_locator is not None:
            lines.append(f"await expect({_locator(step.residual_object_locator)}).toHaveCount({step.expected_residual_count});")
        return lines
    if step.action == ActionType.DOWNLOAD:
        target = _locator(step.locator)
        expected = step.download_validation
        assert expected is not None
        lines = [
            f"const [download{index}] = await Promise.all([page.waitForEvent('download'), {target}.click()]);",
            f"const downloadPath{index} = testInfo.outputPath('downloads', download{index}.suggestedFilename());",
            f"await download{index}.saveAs(downloadPath{index});",
            f"const downloadBytes{index} = readFileSync(downloadPath{index});",
            f"expect(downloadBytes{index}.byteLength).toBeGreaterThanOrEqual({expected.minimum_size});",
            f"const downloadSha256_{index} = createHash('sha256').update(downloadBytes{index}).digest('hex');",
        ]
        if expected.extension:
            escaped_extension = expected.extension.lower().replace(".", "\\.")
            lines.append(f"expect(download{index}.suggestedFilename().toLowerCase()).toMatch(/{escaped_extension}$/);")
        if expected.filename_pattern:
            lines.append(f"expect(download{index}.suggestedFilename()).toMatch(new RegExp({json.dumps(expected.filename_pattern)}));")
        if expected.sha256:
            lines.append(f"expect(downloadSha256_{index}).toBe({json.dumps(expected.sha256.lower())});")
        if expected.format == "json":
            lines.append(f"const downloadJson{index} = JSON.parse(downloadBytes{index}.toString('utf8'));")
            for key in expected.required_json_keys:
                lines.append(f"expect(downloadJson{index}).toHaveProperty({json.dumps(key, ensure_ascii=False)});")
        if expected.format == "zip":
            lines.append(f"expect(downloadBytes{index}.subarray(0, 2).toString('binary')).toBe('PK');")
        if expected.format == "text":
            lines.append(f"expect(() => downloadBytes{index}.toString('utf8')).not.toThrow();")
        return lines
    if step.action == ActionType.WAIT_FOR_STATE:
        target = _locator(step.locator)
        variable = "ASYNC_STATE_MACHINE_" + "".join(
            char if char.isalnum() else "_" for char in (step.state_machine_id or "")
        ).upper()
        return [
            f"const stateMachineRaw{index} = process.env.{variable};",
            f"if (!stateMachineRaw{index}) throw new Error('Missing async state machine environment: {variable}');",
            f"const stateMachine{index} = JSON.parse(stateMachineRaw{index});",
            f"const stateDeadline{index} = Date.now() + Number(stateMachine{index}.timeoutMs || 120000);",
            f"let statePrevious{index}: string | undefined;",
            f"let stateFinal{index} = '';",
            f"while (Date.now() <= stateDeadline{index}) {{",
            f"  const current = (await {target}.innerText()).trim();",
            f"  if (!stateMachine{index}.states.includes(current)) throw new Error(`Unknown async state: ${{current}}`);",
            f"  if (statePrevious{index} && current !== statePrevious{index} && !(stateMachine{index}.transitions[statePrevious{index}] || []).includes(current)) throw new Error(`Invalid async transition: ${{statePrevious{index}}} -> ${{current}}`);",
            f"  if ((stateMachine{index}.failureStates || []).includes(current)) throw new Error(`Async failure state: ${{current}}`);",
            f"  if (stateMachine{index}.terminalStates.includes(current)) {{ stateFinal{index} = current; break; }}",
            f"  statePrevious{index} = current;",
            f"  await page.waitForTimeout(Number(stateMachine{index}.pollingIntervalMs || 1000));",
            "}",
            f"expect(stateMachine{index}.terminalStates).toContain(stateFinal{index});",
        ]
    if step.action == ActionType.COMPONENT:
        component = step.component
        assert component is not None
        targets = [_locator(locator) for locator in component.locators]
        if component.kind == "cascade_select":
            return [f"await {target}.selectOption({json.dumps(value, ensure_ascii=False)});" for target, value in zip(targets, component.values)]
        if component.kind == "searchable_select":
            return [f"await {targets[0]}.click();", f"await {targets[1]}.fill({json.dumps(component.values[0], ensure_ascii=False)});", f"await {targets[2]}.click();"]
        if component.kind == "date_time_range":
            return [f"await {targets[0]}.fill({json.dumps(component.values[0])});", f"await {targets[1]}.fill({json.dumps(component.values[1])});"]
        if component.kind == "pagination":
            return [f"await {targets[0]}.click();"]
        if component.kind == "statistics_card":
            return [f"await expect({targets[0]}).toContainText({json.dumps(component.expected_text, ensure_ascii=False)});"]
        if component.kind == "tab":
            return [f"await {targets[0]}.click();", f"await expect({targets[0]}).not.toHaveAttribute('aria-selected', 'false');"]
        if component.kind == "upload_dialog":
            variable = "TEST_FILE_" + "".join(char if char.isalnum() else "_" for char in (component.file_id or "")).upper()
            return [f"await {targets[0]}.click();", f"const componentUploadPath{index} = process.env.{variable};", f"if (!componentUploadPath{index}) throw new Error('Missing registered test file environment: {variable}');", f"await {targets[1]}.setInputFiles(componentUploadPath{index});"]
        if component.kind == "image_preview":
            return [f"await {targets[0]}.click();", f"await expect({targets[1]}).toBeVisible();", f"const previewIdentity{index} = await {targets[1]}.evaluate((element) => element.getAttribute('alt') || element.getAttribute('aria-label') || element.textContent || '');", f"expect(previewIdentity{index}).toContain({json.dumps(component.expected_text, ensure_ascii=False)});"]
        if component.kind == "local_scroll":
            return [f"await {targets[0]}.evaluate((element, delta) => element.scrollBy(0, delta), {component.scroll_delta_y});"]
    target = _locator(step.locator) if step.locator else "page"
    if step.action in {ActionType.VISUAL_ZOOM, ActionType.VISUAL_CLEAR, ActionType.VISUAL_DRAW_POLYGON, ActionType.VISUAL_DRAW_RECTANGLE}:
        region = _locator(step.canvas_region_locator)
        lines = [f"const canvasBox{index} = await {region}.boundingBox();", f"if (!canvasBox{index}) throw new Error('Canvas region is not visible');"]
        def projected(position) -> str:
            return f"{{ x: canvasBox{index}.x + canvasBox{index}.width * {position.x_ratio}, y: canvasBox{index}.y + canvasBox{index}.height * {position.y_ratio} }}"
        if step.action == ActionType.VISUAL_ZOOM:
            center = projected(step.relative_position)
            return lines + [f"const zoomCenter{index} = {center};", f"await page.mouse.move(zoomCenter{index}.x, zoomCenter{index}.y);", f"await page.mouse.wheel(0, {step.zoom_delta});"]
        if step.action == ActionType.VISUAL_CLEAR:
            return lines + [f"await {target}.click();"]
        points = ", ".join(projected(item) for item in step.visual_points)
        lines.append(f"const canvasPoints{index} = [{points}];")
        if step.action == ActionType.VISUAL_DRAW_RECTANGLE:
            return lines + [f"await page.mouse.move(canvasPoints{index}[0].x, canvasPoints{index}[0].y);", "await page.mouse.down();", f"await page.mouse.move(canvasPoints{index}[1].x, canvasPoints{index}[1].y, {{ steps: 10 }});", "await page.mouse.up();"]
        lines.extend([f"for (const point of canvasPoints{index}) await page.mouse.click(point.x, point.y);"])
        if step.gesture_finish == "double_click":
            lines.append(f"await page.mouse.dblclick(canvasPoints{index}.at(-1)!.x, canvasPoints{index}.at(-1)!.y);")
        elif step.gesture_finish == "enter":
            lines.append("await page.keyboard.press('Enter');")
        return lines
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
    context = "page"
    if locator.scope:
        context = _locator(locator.scope.locator)
        if locator.scope.identity:
            context += f".filter({{ hasText: {json.dumps(locator.scope.identity, ensure_ascii=False)} }})"
    if locator.role:
        option = f", {{ name: {json.dumps(locator.name, ensure_ascii=False)} }}" if locator.name else ""
        return f"{context}.getByRole({json.dumps(locator.role)}{option})"
    if locator.label:
        return f"{context}.getByLabel({json.dumps(locator.label, ensure_ascii=False)})"
    if locator.placeholder:
        return f"{context}.getByPlaceholder({json.dumps(locator.placeholder, ensure_ascii=False)})"
    if locator.test_id:
        return f"{context}.getByTestId({json.dumps(locator.test_id, ensure_ascii=False)})"
    if locator.attribute:
        selector = f'[{locator.attribute.name}="{locator.attribute.value}"]'
        return f"{context}.locator({json.dumps(selector, ensure_ascii=False)})"
    if locator.text:
        return f"{context}.getByText({json.dumps(locator.text, ensure_ascii=False)})"
    return f"{context}.locator({json.dumps(locator.css or '')})"
