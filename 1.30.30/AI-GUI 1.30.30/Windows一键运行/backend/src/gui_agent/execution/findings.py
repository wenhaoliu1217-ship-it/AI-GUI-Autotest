"""Convert deterministic failures and runtime facts into reviewable findings."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from ..domain.results import AssertionResult, EvidenceEvent, Finding, Status, StepResult


def _timeline(step: StepResult, after_facts: list[str] | None = None) -> list[EvidenceEvent]:
    events: list[EvidenceEvent] = []
    if step.before:
        events.append(EvidenceEvent(
            phase="before_action",
            timestamp=step.before.captured_at,
            screenshot=step.before.screenshot,
            facts=[f"页面：{step.before.title or '无标题'} · {step.before.url}"],
        ))
    if step.after:
        facts = [f"页面：{step.after.title or '无标题'} · {step.after.url}"]
        facts.extend(after_facts or [])
        events.append(EvidenceEvent(
            phase="after_action",
            timestamp=step.after.captured_at,
            screenshot=step.after.screenshot,
            facts=facts,
        ))
    return events


def _request_category(item: str) -> str:
    lowered = item.lower()
    if "http 401" in lowered or "http 403" in lowered:
        return "认证／权限请求失败"
    if any(f"http {code}" in lowered for code in range(500, 600)):
        return "服务端请求失败"
    if any(f"http {code}" in lowered for code in range(400, 500)):
        return "客户端请求失败"
    if any(token in lowered for token in ("timeout", "timed out", "connection", "dns", "net::")):
        return "网络／时序请求失败"
    return "请求失败"


def _failure_actual(step: StepResult) -> str:
    if step.failure_category and step.failure_category.value == "timeout":
        return step.error_message or "浏览器动作在限制时间内未完成，页面可能长时间无响应"
    return step.error_message or "动作未成功完成"


def _fact(name: str, value: Any) -> str:
    return f"{name}={json.dumps(value, ensure_ascii=False, sort_keys=True)}"


def _scene_values(evidence: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    scenes: list[tuple[str, dict[str, Any]]] = []
    for name in ("sceneBefore", "sceneAfter"):
        value = evidence.get(name)
        if isinstance(value, dict):
            scenes.append((name, value))
    for bridge_name in ("bridgeBefore", "bridgeAfter"):
        bridge = evidence.get(bridge_name)
        if isinstance(bridge, dict) and isinstance(bridge.get("sceneState"), dict):
            scenes.append((f"{bridge_name}.sceneState", bridge["sceneState"]))
    return scenes


def _visible_target_ids(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        str(item.get("id") or item.get("targetId"))
        for item in value
        if isinstance(item, dict) and (item.get("id") or item.get("targetId"))
    }


def _canvas_finding(
    step: StepResult,
    reproduction: list[str],
    *,
    category: str,
    title: str,
    actual: str,
    expected: str,
    facts: list[str],
    severity: str = "High",
    inference: str = "",
) -> Finding:
    evidence = step.canvas_evidence or {}
    direct_facts = [
        _fact(name, evidence[name])
        for name in (
            "collectionStatus", "failurePhase", "semanticTarget", "visibleTargets",
            "sceneBefore", "sceneAfter", "bridgeBefore", "bridgeAfter",
            "selectedTargetBefore", "selectedTargetAfter", "semanticStateVerified",
            "sceneStateChanged", "selectedTargetChanged",
        )
        if name in evidence
    ]
    screenshots = [
        path for path in (
            evidence.get("beforeScreenshot"),
            evidence.get("afterScreenshot"),
            step.before.screenshot if step.before else None,
            step.after.screenshot if step.after else None,
        ) if isinstance(path, str) and path
    ]
    return Finding(
        id=f"finding-{uuid4().hex[:10]}",
        title=f"步骤 {step.index} {title}",
        category=category,
        severity=severity,
        confidence="high",
        actual_result=actual,
        expected_result=expected,
        facts=list(dict.fromkeys([*direct_facts, *facts])),
        inference=inference,
        evidence=list(dict.fromkeys(screenshots)),
        evidence_timeline=_timeline(step, facts),
        reproduction_steps=reproduction[:step.index],
    )


def _build_canvas_findings(step: StepResult, reproduction: list[str]) -> list[Finding]:
    evidence = step.canvas_evidence
    if not isinstance(evidence, dict):
        return []

    mode = str(evidence.get("mode") or step.execution_mode or "")
    if mode not in {"visual", "app_bridge"}:
        return []

    error = str(evidence.get("error") or step.error_message or "")
    error_lower = error.lower()
    target = evidence.get("semanticTarget")
    visible_targets = evidence.get("visibleTargets")
    scenes = _scene_values(evidence)
    findings: list[Finding] = []

    contract_tokens = (
        "bridge 缺少", "bridge version", "bridge getscenestate", "bridge listvisibletargets",
        "bridge 返回", "bridge 动作后场景状态无效", "app bridge", "应用测试桥接",
    )
    if evidence.get("collectionStatus") == "failed" and any(token in error_lower for token in contract_tokens):
        findings.append(_canvas_finding(
            step, reproduction,
            category="bridge_contract_error",
            title="Bridge 契约不可用",
            actual=error or "Bridge 证据采集失败",
            expected="Bridge v1 五项最小契约可用并返回有效结构",
            facts=[
                _fact("collectionStatus", evidence.get("collectionStatus")),
                _fact("failurePhase", evidence.get("failurePhase")),
                _fact("error", error),
            ],
            inference="需由接入方核对 Bridge 是否注入、版本及五项最小契约实现。",
        ))

    resource_facts: list[str] = []
    resource_tokens = ("resource", "tileset", "瓦片加载失败", "资源加载失败", "http 4", "http 5")
    if any(token in error_lower for token in resource_tokens):
        resource_facts.append(_fact("error", error))
    for scene_name, scene in scenes:
        for key in ("resourceFailures", "resourceErrors", "failedResources", "tileLoadErrors"):
            if scene.get(key):
                resource_facts.append(_fact(f"{scene_name}.{key}", scene[key]))
    if resource_facts:
        findings.append(_canvas_finding(
            step, reproduction,
            category="canvas_resource_failure",
            title="场景资源加载失败",
            actual="Canvas／Bridge 返回了资源加载失败证据",
            expected="场景所需资源成功加载且无资源失败记录",
            facts=resource_facts,
            inference="资源失败的根因可能来自服务、网络、权限或数据，需结合请求日志确认。",
        ))

    scene_not_ready_facts: list[str] = []
    for scene_name, scene in scenes:
        if scene.get("loading") is True:
            scene_not_ready_facts.append(_fact(f"{scene_name}.loading", True))
        if scene.get("tilesLoaded") is False:
            scene_not_ready_facts.append(_fact(f"{scene_name}.tilesLoaded", False))
    if any(token in error_lower for token in ("场景就绪", "等待场景", "scene ready")) and "超时" in error:
        scene_not_ready_facts.append(_fact("error", error))
    if scene_not_ready_facts:
        findings.append(_canvas_finding(
            step, reproduction,
            category="canvas_scene_not_ready",
            title="场景未达到可交互状态",
            actual="动作前后仍检测到场景加载中、瓦片未完成或就绪等待超时",
            expected="场景 loading=false、tilesLoaded=true，且就绪等待在时限内完成",
            facts=list(dict.fromkeys(scene_not_ready_facts)),
            inference="场景未就绪的根因尚未确定，可能需结合资源请求和应用状态进一步确认。",
        ))

    target_ids = _visible_target_ids(visible_targets)
    target_missing_error = "可见目标中不存在" in error
    if target and ((isinstance(visible_targets, list) and str(target) not in target_ids) or target_missing_error):
        findings.append(_canvas_finding(
            step, reproduction,
            category="canvas_target_not_visible",
            title="语义目标不可见",
            actual=f"Bridge 可见目标中不存在 {target}",
            expected=f"语义目标 {target} 出现在 Bridge 可见目标列表中",
            facts=[_fact("semanticTarget", target), _fact("visibleTargets", visible_targets), *([_fact("error", error)] if error else [])],
            inference="目标缺失可能与当前相机、图层、过滤条件或业务数据有关，需结合场景上下文确认。",
        ))

    interaction_tokens = ("未返回有效目标位置", "必须返回坐标对象", "无法确认有效 viewport", "不可交互")
    if any(token in error for token in interaction_tokens):
        findings.append(_canvas_finding(
            step, reproduction,
            category="canvas_target_not_interactive",
            title="语义目标不可交互",
            actual=error,
            expected="目标具备有效屏幕坐标并可执行指定动作",
            facts=[_fact("semanticTarget", target), _fact("error", error)],
            inference="需核对目标坐标映射、viewport 和当前交互状态。",
        ))

    selected_after = evidence.get("selectedTargetAfter")
    mismatch_error = "选中目标不匹配" in error
    if target and ((selected_after is not None and selected_after != target) or mismatch_error):
        findings.append(_canvas_finding(
            step, reproduction,
            category="canvas_selection_mismatch",
            title="动作后选中对象错误",
            actual=f"期望选中 {target}，实际为 {selected_after if selected_after is not None else '未选中'}",
            expected=f"动作后 selectedTargetAfter={target}",
            facts=[
                _fact("semanticTarget", target),
                _fact("selectedTargetBefore", evidence.get("selectedTargetBefore")),
                _fact("selectedTargetAfter", selected_after),
                *([_fact("error", error)] if error else []),
            ],
            inference="坐标命中、场景遮挡或目标映射均可能造成选中偏差，需结合截图和坐标证据确认。",
        ))

    explicit_unchanged = evidence.get("semanticStateVerified") is False
    visual_unchanged = (
        mode == "visual"
        and evidence.get("action") in {"visual_click", "visual_drag"}
        and evidence.get("sceneStateChanged") is False
        and evidence.get("selectedTargetChanged") is False
    )
    if explicit_unchanged or visual_unchanged:
        facts = [
            _fact("semanticStateVerified", evidence.get("semanticStateVerified")),
            _fact("sceneStateChanged", evidence.get("sceneStateChanged")),
            _fact("selectedTargetChanged", evidence.get("selectedTargetChanged")),
        ]
        findings.append(_canvas_finding(
            step, reproduction,
            category="canvas_state_unchanged",
            title="动作后语义状态未变化",
            actual="Bridge／Canvas 动作前后未观察到预期语义状态变化",
            expected="动作后场景状态或选中目标发生与业务动作一致的变化",
            facts=facts,
            inference="动作可能未生效，或当前 Bridge 状态覆盖不足，需结合业务预期人工确认。",
        ))

    return findings


def build_findings(steps: list[StepResult], assertions: list[AssertionResult], reproduction: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for step in steps:
        canvas_findings = _build_canvas_findings(step, reproduction)
        findings.extend(canvas_findings)
        if step.status != Status.PASSED and not canvas_findings:
            facts = [f"步骤 {step.index} 状态为 {step.status.value}"]
            if step.error_message:
                facts.append(step.error_message)
            findings.append(Finding(
                id=f"finding-{uuid4().hex[:10]}",
                title=f"步骤 {step.index} 未完成：{step.description or step.action}",
                category=step.failure_category.value if step.failure_category else "execution",
                severity="High" if step.failure_category and step.failure_category.value == "security" else "Medium",
                actual_result=_failure_actual(step),
                expected_result="动作成功并进入可验证的下一状态",
                facts=facts,
                inference="可能是产品、环境、测试数据、定位或时序问题，需结合证据人工确认。",
                evidence=[path for path in [step.before.screenshot if step.before else None, step.after.screenshot if step.after else None] if path],
                evidence_timeline=_timeline(step, [step.error_message] if step.error_message else []),
                reproduction_steps=reproduction,
            ))
        after = step.after
        if after:
            runtime = after.console_errors + after.page_errors + after.failed_requests
            if runtime:
                classified = [
                    f"{_request_category(item)}：{item}" if item in after.failed_requests else item
                    for item in runtime
                ]
                findings.append(Finding(
                    id=f"finding-{uuid4().hex[:10]}",
                    title=f"步骤 {step.index} 观察到运行时异常",
                    category="runtime",
                    severity="Medium",
                    confidence="high",
                    actual_result=f"采集到 {len(runtime)} 条控制台、页面或网络异常",
                    expected_result="关键页面操作期间不出现未忽略的运行时异常",
                    facts=classified,
                    inference="异常是否影响业务目标尚未确定。",
                    evidence=[path for path in [step.before.screenshot if step.before else None, after.screenshot] if path],
                    evidence_timeline=_timeline(step, classified),
                    reproduction_steps=reproduction[:step.index],
                ))
            before_issue_keys = {
                (issue.kind, issue.target) for issue in (step.before.page_issues if step.before else [])
            }
            for issue in after.page_issues:
                if (issue.kind, issue.target) in before_issue_keys:
                    continue
                facts = [issue.message]
                if issue.target:
                    facts.append(f"目标：{issue.target}")
                findings.append(Finding(
                    id=f"finding-{uuid4().hex[:10]}",
                    title=f"步骤 {step.index} 检测到{issue.message}",
                    category=issue.kind,
                    severity=issue.severity,
                    confidence=issue.confidence,
                    actual_result=issue.message,
                    expected_result="页面关键内容和交互控件完整可见且可操作",
                    facts=facts,
                    inference="该结论来自浏览器布局与命中测试信号，仍需结合业务状态人工确认。",
                    evidence=[path for path in [step.before.screenshot if step.before else None, after.screenshot] if path],
                    evidence_timeline=_timeline(step, facts),
                    reproduction_steps=reproduction[:step.index],
                ))
    trailing_no_progress: list[StepResult] = []
    for candidate in reversed(steps):
        if candidate.progress_assessment != "no_progress":
            break
        trailing_no_progress.append(candidate)
    no_progress = list(reversed(trailing_no_progress))
    if len(no_progress) >= 3:
        findings.append(Finding(
            id=f"finding-{uuid4().hex[:10]}",
            title=f"连续 {len(no_progress)} 个步骤未观察到页面进展",
            category="unresponsive",
            severity="High",
            confidence="high",
            actual_result="页面状态指纹在连续动作后没有发生可验证变化",
            expected_result="动作后页面或应用状态发生与场景目标一致的变化",
            facts=[f"步骤 {step.index}：{step.description or step.action}" for step in no_progress],
            inference="可能是页面无响应、动作未生效或状态变化未被当前观察器覆盖，需人工确认。",
            evidence=[path for step in no_progress for path in [step.after.screenshot if step.after else None] if path],
            evidence_timeline=[event for step in no_progress for event in _timeline(step)],
            reproduction_steps=reproduction,
        ))
    for assertion in assertions:
        if assertion.status != Status.PASSED:
            expected = assertion.expected_summary
            if not expected or expected.strip().lower() in {"none", "null"}:
                expected = assertion.description or assertion.type
            findings.append(Finding(
                id=f"finding-{uuid4().hex[:10]}",
                title=f"预期结果未满足：{assertion.description or assertion.type}",
                category="assertion",
                severity="High",
                confidence="high",
                actual_result=assertion.actual_summary or assertion.error_message or "无法获得实际结果",
                expected_result=expected,
                facts=[f"断言状态为 {assertion.status.value}", assertion.actual_summary or ""],
                inference="明确断言未满足，但仍需排除测试数据和环境问题。",
                evidence=[assertion.screenshot] if assertion.screenshot else [],
                reproduction_steps=reproduction,
            ))
    return findings
