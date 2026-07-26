"""Compile an approved plan to maintainable Playwright TypeScript."""

from __future__ import annotations

import hashlib
import json
import re

from ..domain.models import ActionType, Locator, Step, TestPlan
from ..domain.results import GeneratedTest


_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}
_READ_COMMERCE_ACTIONS = {
    "browse", "search", "filter", "sort", "paginate", "view_product",
    "view_account_structure", "view_help", "change_region", "download_invoice",
}
_READONLY_ACTIONS = {
    ActionType.NAVIGATE, ActionType.WAIT_FOR, ActionType.SCREENSHOT,
    ActionType.HOVER, ActionType.SCROLL, ActionType.BACK, ActionType.RELOAD,
    ActionType.DOWNLOAD,
}


def compile_test(plan: TestPlan, source_path: str = "generated-test.spec.ts") -> tuple[str, GeneratedTest]:
    levels = [step.stability_level.value for step in plan.steps]
    overall = max(levels, key=lambda level: _RANK[level], default="A")
    lines = [
        "import { test, expect } from '@playwright/test';",
        "import { createHash } from 'node:crypto';",
        "import { readFile } from 'node:fs/promises';",
        "import path from 'node:path';",
        "",
        "const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));",
        "async function withReadRecovery<T>(operation: () => Promise<T>): Promise<T> {",
        "  for (let attempt = 1; ; attempt += 1) {",
        "    try { return await operation(); } catch (error) {",
        "      const status = Number((error as any)?.httpStatus || 0);",
        "      const message = String(error).toLowerCase();",
        "      const kind = status === 429 ? 'http_429' : status >= 500 && status <= 599 ? 'http_5xx' : /err_(internet_disconnected|network_changed|connection_reset|connection_closed|name_not_resolved)|networkerror|target closed|page crashed/.test(message) ? 'network_or_session' : '';",
        "      if (!kind || attempt >= 3) throw error;",
        "      const backoffMs = 250 * (2 ** (attempt - 1));",
        "      test.info().annotations.push({ type: 'recovery', description: JSON.stringify({ attempt, kind, backoffMs, retried: true }) });",
        "      await sleep(backoffMs);",
        "    }",
        "  }",
        "}",
        "const jsonPath = (value: any, expression: string) => expression.split('.').reduce((current, part) => current[Number.isInteger(Number(part)) ? Number(part) : part], value);",
        "",
        f"test({json.dumps(plan.name, ensure_ascii=False)}, async ({{ page, context }}) => {{",
        "  let activePage = page;",
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
    browser_target = step.browser_target
    lines: list[str] = []
    if browser_target.page == "newest":
        lines.append("activePage = context.pages().at(-1) ?? activePage;")
    elif browser_target.page == "opener":
        lines.extend([
            "const openerPage = await activePage.opener();",
            "if (!openerPage) throw new Error('Expected opener page is unavailable');",
            "activePage = openerPage;",
        ])
    if browser_target.url_contains and step.action != ActionType.HUMAN_TAKEOVER:
        lines.append(
            f"await expect(activePage).toHaveURL(new RegExp({json.dumps(re.escape(browser_target.url_contains))}));"
        )
    locator_root = "activePage"
    if browser_target.frame_css:
        lines.extend([
            f"await expect(activePage.locator({json.dumps(browser_target.frame_css)})).toHaveCount(1);",
            f"const activeFrame = activePage.frameLocator({json.dumps(browser_target.frame_css)});",
        ])
        locator_root = "activeFrame"
    if step.action == ActionType.HUMAN_TAKEOVER:
        lines.append(
            f"// HUMAN TAKEOVER [{step.takeover_reason}]: {step.description or 'complete the protected interaction'}"
        )
        if browser_target.url_contains:
            lines.append(
                f"await expect(activePage).toHaveURL(new RegExp({json.dumps(re.escape(browser_target.url_contains))}));"
            )
        if step.takeover_resume_locator:
            lines.append(f"await expect({_locator(step.takeover_resume_locator, locator_root)}).toBeVisible();")
        return lines
    core = _compile_step_core(step, base_url, locator_root=locator_root)
    if _is_read_only(step):
        core = ["await withReadRecovery(async () => {"] + [f"  {line}" for line in core] + ["});"]
    elif step.commerce is not None:
        core = _compile_side_effect_recovery(step, base_url, core)
    return lines + [re.sub(r"\bpage\b", "activePage", line) for line in core]


def _compile_step_core(step: Step, base_url: str, *, locator_root: str = "page") -> list[str]:
    if step.stability_level.value == "D":
        description = step.description or step.action.value
        return [f"// MANUAL [D]: {description}"]
    if step.action == ActionType.NAVIGATE:
        target = step.target or "/"
        url = target if "://" in target else base_url.rstrip("/") + "/" + target.lstrip("/")
        return [
            f"const response = await page.goto({json.dumps(url)});",
            "if (response && response.status() >= 400) throw Object.assign(new Error(`HTTP ${response.status()}`), { httpStatus: response.status() });",
        ]
    if step.action == ActionType.WAIT_FOR_STATE:
        target = _locator(step.locator, locator_root)
        variable = "ASYNC_STATE_MACHINE_" + "".join(char if char.isalnum() else "_" for char in (step.state_machine_id or "")).upper()
        index = "_" + "".join(char if char.isalnum() else "_" for char in (step.state_machine_id or "state"))
        return [
            f"const stateMachineRaw{index} = process.env.{variable};",
            f"if (!stateMachineRaw{index}) throw new Error('Missing async state machine environment: {variable}');",
            f"const stateMachine{index} = JSON.parse(stateMachineRaw{index});",
            f"const stateDeadline{index} = Date.now() + Number(stateMachine{index}.timeoutMs || 120000);",
            f"let statePrevious{index}: string | undefined;", f"let stateFinal{index} = '';",
            f"while (Date.now() <= stateDeadline{index}) {{",
            f"  const current = (await {target}.innerText()).trim();",
            f"  if (!stateMachine{index}.states.includes(current)) throw new Error(`Unknown async state: ${{current}}`);",
            f"  if (statePrevious{index} && current !== statePrevious{index} && !(stateMachine{index}.transitions[statePrevious{index}] || []).includes(current)) throw new Error(`Invalid async transition: ${{statePrevious{index}}} -> ${{current}}`);",
            f"  if ((stateMachine{index}.failureStates || []).includes(current)) throw new Error(`Async failure state: ${{current}}`);",
            f"  if (stateMachine{index}.terminalStates.includes(current)) {{ stateFinal{index} = current; break; }}",
            f"  statePrevious{index} = current;",
            f"  await page.waitForTimeout(Number(stateMachine{index}.pollingIntervalMs || 1000));", "}",
            f"expect(stateMachine{index}.terminalStates).toContain(stateFinal{index});",
        ]
    if step.action == ActionType.COMPONENT:
        component = step.component
        assert component is not None
        targets = [_locator(locator, locator_root) for locator in component.locators]
        if component.kind == "cascade_select":
            return [f"await {target}.selectOption({json.dumps(value, ensure_ascii=False)});" for target, value in zip(targets, component.values)]
        if component.kind == "searchable_select":
            return [f"await {targets[0]}.click();", f"await {targets[1]}.fill({json.dumps(component.values[0], ensure_ascii=False)});", f"await {targets[2]}.click();"]
        if component.kind == "date_time_range":
            return [f"await {targets[0]}.fill({json.dumps(component.values[0])});", f"await {targets[1]}.fill({json.dumps(component.values[1])});"]
        return [f"await {targets[0]}.click();"]
    if step.action == ActionType.SCREENSHOT:
        wait = [f"await page.waitForTimeout({step.wait_before_ms});"] if step.wait_before_ms else []
        return [*wait, "await page.screenshot({ fullPage: false });"]
    if step.action == ActionType.BACK:
        return ["await page.goBack();"]
    if step.action == ActionType.RELOAD:
        return ["await page.reload();"]
    if step.action == ActionType.UPLOAD_FILE:
        digest = (step.file_asset_ref or "asset:").removeprefix("asset:")
        target = _locator(step.locator, locator_root)
        return [
            "const fileAssetRoot = process.env.GUI_FILE_ASSET_ROOT ?? 'file-assets';",
            f"const uploadPath = path.join(fileAssetRoot, {json.dumps(digest)});",
            f"await {target}.setInputFiles(uploadPath);",
            "const uploadSha256 = createHash('sha256').update(await readFile(uploadPath)).digest('hex');",
            f"expect(uploadSha256).toBe({json.dumps(digest)});",
        ]
    if step.action == ActionType.DOWNLOAD:
        target = _locator(step.locator, locator_root)
        expected = json.dumps(step.expected_download_sha256) if step.expected_download_sha256 else "undefined"
        return [
            f"const [download] = await Promise.all([activePage.waitForEvent('download'), {target}.click()]);",
            "const downloadPath = test.info().outputPath('downloads', download.suggestedFilename());",
            "await download.saveAs(downloadPath);",
            "const downloadSha256 = createHash('sha256').update(await readFile(downloadPath)).digest('hex');",
            f"if ({expected}) expect(downloadSha256).toBe({expected});",
        ]
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
    scope_guards: list[str] = []
    if step.commerce_scope and step.locator:
        scope = step.commerce_scope
        container = _locator(scope.container, locator_root)
        anchor = _locator(scope.anchor, locator_root)
        scoped = f"{container}.filter({{ has: {anchor} }})"
        for marker in scope.excluded_markers:
            scoped += f".filter({{ hasNot: {_locator(marker)} }})"
        scope_guards = [
            f"const commerceContainer = {scoped};",
            "await expect(commerceContainer).toHaveCount(1);",
            f"const commerceTarget = {_locator(step.locator, 'commerceContainer')};",
            "await expect(commerceTarget).toHaveCount(1);",
            "await commerceTarget.scrollIntoViewIfNeeded();",
        ]
        target = "commerceTarget"
    else:
        target = _locator(step.locator, locator_root) if step.locator else "page"
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
        return scope_guards + [f"await {target}.{methods[step.action]};"]
    if step.action in {ActionType.FILL, ActionType.SELECT}:
        value = f"process.env.{step.value_from_secret}" if step.value_from_secret else json.dumps(step.value or "", ensure_ascii=False)
        method = "fill" if step.action == ActionType.FILL else "selectOption"
        return scope_guards + [f"await {target}.{method}({value});"]
    if step.action == ActionType.PRESS:
        return [f"await {target}.press({json.dumps(step.value or '')});"]
    if step.action == ActionType.SCROLL:
        prefix = [f"await {target}.scrollIntoViewIfNeeded();"] if step.locator else []
        return prefix + [f"await page.mouse.wheel(0, {step.scroll_delta_y});"]
    return [f"// Manual step required: {step.action.value}"]


def _is_read_only(step: Step) -> bool:
    if step.commerce is not None:
        return step.commerce.action.value in _READ_COMMERCE_ACTIONS
    return step.action in _READONLY_ACTIONS


def _compile_side_effect_recovery(step: Step, base_url: str, core: list[str]) -> list[str]:
    metadata = step.commerce
    assert metadata is not None
    if not metadata.idempotency_key_ref or metadata.state_probe is None:
        return [
            "try {",
            *[f"  {line}" for line in core],
            "} catch (error) {",
            "  test.info().annotations.push({ type: 'recovery', description: 'side_effect_outcome_unknown:manual_reconciliation_required' });",
            "  throw new Error(`side_effect_outcome_unknown: manual_reconciliation_required; original=${String(error)}`);",
            "}",
        ]
    probe = metadata.state_probe
    template = probe.url.replace("${RUN_ID}", "${process.env.GUI_RUN_ID ?? 'E2E_GENERATED'}")
    template = template.replace("${TARGET_REF}", metadata.target_ref or "")
    probe_url = template if "://" in template else base_url.rstrip("/") + "/" + template.lstrip("/")
    idem_hash = hashlib.sha256(metadata.idempotency_key_ref.encode("utf-8")).hexdigest()
    before = json.dumps(metadata.before_state, ensure_ascii=False)
    expected = json.dumps(probe.expected_state, ensure_ascii=False)
    return [
        "try {",
        *[f"  {line}" for line in core],
        "} catch (error) {",
        f"  const idempotencyKeySha256 = {json.dumps(idem_hash)};",
        f"  const probeResponse = await page.request.get({json.dumps(probe_url)});",
        "  if (!probeResponse.ok()) throw new Error('side_effect_outcome_unknown: backend_probe_failed; manual_reconciliation_required');",
        f"  const recoveredState = String(jsonPath(await probeResponse.json(), {json.dumps(probe.json_path)}));",
        f"  test.info().annotations.push({{ type: 'recovery', description: JSON.stringify({{ outcome: 'side_effect_outcome_unknown', recoveredState, idempotencyKeySha256 }}) }});",
        f"  if (recoveredState === {expected}) return;",
        f"  if (recoveredState !== {before}) throw new Error('side_effect_outcome_unknown: backend_state_ambiguous; manual_reconciliation_required');",
        "  await sleep(250);",
        *[f"  {line}" for line in core],
        "}",
    ]


def _locator(locator: Locator | None, root: str = "page") -> str:
    if locator is None:
        raise ValueError("生成测试时缺少 locator")
    if locator.role:
        option = f", {{ name: {json.dumps(locator.name, ensure_ascii=False)} }}" if locator.name else ""
        return f"{root}.getByRole({json.dumps(locator.role)}{option})"
    if locator.label:
        return f"{root}.getByLabel({json.dumps(locator.label, ensure_ascii=False)})"
    if locator.test_id:
        return f"{root}.getByTestId({json.dumps(locator.test_id, ensure_ascii=False)})"
    if locator.text:
        return f"{root}.getByText({json.dumps(locator.text, ensure_ascii=False)})"
    return f"{root}.locator({json.dumps(locator.css or '')})"
