"""报告生成：把 RunResult 渲染为人类可读的 HTML 与 Markdown。

设计要点：
- 报告内容全部来自已脱敏的 RunResult，不再触碰原始密钥或页面数据。
- "可能原因"区块显式标注为启发式建议，并列出证据与置信度。
- 不引入模板引擎依赖，用标准库字符串拼接，保持 Windows 可移植。
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from ..domain.results import CauseHint, RunResult, Status

_STATUS_LABEL = {
    Status.PASSED: "通过",
    Status.FAILED: "失败",
    Status.ERROR: "错误",
    Status.SKIPPED: "跳过",
}


def _status_text(status: Status) -> str:
    return _STATUS_LABEL.get(status, status.value)


def render_markdown(result: RunResult) -> str:
    """生成 Markdown 报告正文。"""
    lines: list[str] = []
    lines.append(f"# 测试运行报告：{result.plan_name}")
    lines.append("")

    lines.append(f"- 运行 ID：`{result.run_id}`")
    lines.append(f"- 总体状态：**{_status_text(result.status)}**")
    if result.role:
        lines.append(f"- 执行角色：{result.role}")
    lines.append(f"- 基础地址：{result.base_url_summary}")
    lines.append(f"- 开始：{result.started_at.isoformat()}")
    lines.append(f"- 结束：{result.ended_at.isoformat()}")
    lines.append(f"- 耗时：{result.duration_ms} ms")
    lines.append(f"- 退出码：{result.exit_code}")
    if result.runner_isolation:
        isolation = result.runner_isolation
        lines.append(
            f"- Runner 隔离：{isolation.get('mode', '-')} ｜ "
            f"Windows Job：{'已绑定' if isolation.get('windows_job_assigned') else '未绑定'} ｜ "
            f"内存上限：{isolation.get('memory_limit_mb', '-')} MB ｜ "
            f"强制终止：{'是' if isolation.get('forced_termination') else '否'}"
        )
    lines.append("")

    if result.evidence_manifest:
        missing = [
            item for item in result.evidence_manifest.get("items", [])
            if item.get("status") == "missing"
        ]
        lines.extend([
            "## 证据完整度",
            "",
            f"- 完整度：**{result.evidence_completeness * 100:.1f}%**",
            f"- 清单：[`{result.evidence_manifest_path}`]({result.evidence_manifest_path})",
            f"- 缺失项：{len(missing)}",
        ])
        for item in missing:
            lines.append(f"  - {item.get('label', item.get('id', '未知证据'))}：{item.get('reason') or '未生成'}")
        lines.append("")

    canvas_steps = [step for step in result.steps if step.canvas_evidence]
    if canvas_steps:
        lines.append("## Canvas／Bridge 证据")
        lines.append("")
        for step in canvas_steps:
            evidence = step.canvas_evidence or {}
            lines.append(f"### 步骤 {step.index} · {step.action}")
            lines.append("")
            lines.append(f"- 采集状态：{evidence.get('collectionStatus', 'unknown')}")
            lines.append(f"- 语义目标：{evidence.get('semanticTarget') or '未声明'}")
            lines.append(f"- 坐标来源：{evidence.get('coordinateSource') or '无坐标'}")
            lines.append(f"- Trace：{evidence.get('traceArtifact') or '未记录'}")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(evidence, ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")

    file_steps = [step for step in result.steps if step.file_evidence]
    if file_steps:
        lines.append("## 上传／下载证据")
        lines.append("")
        for step in file_steps:
            evidence = step.file_evidence or {}
            lines.append(f"### 步骤 {step.index} · {step.action}")
            lines.append("")
            lines.append(f"- 方向／状态：{evidence.get('direction', '-')}／{evidence.get('status', 'unknown')}")
            lines.append(f"- 文件：{evidence.get('fileName') or evidence.get('fileId') or '未生成'}")
            lines.append(f"- 大小／SHA-256：{evidence.get('size', '-')}／{evidence.get('sha256', '-')}")
            lines.append(f"- 业务对象：{evidence.get('businessObjectName') or '未声明'}")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(evidence, ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")

    lines.append("## 步骤")
    lines.append("")
    lines.append("| # | 动作 | 目标 | 状态 | 耗时(ms) | 说明 |")
    lines.append("|---|------|------|------|----------|------|")
    for s in result.steps:
        note = s.error_message or s.description or ""
        lines.append(
            f"| {s.index} | {s.action} | {s.target_summary} | "
            f"{_status_text(s.status)} | {s.duration_ms} | {note} |"
        )
    lines.append("")

    if result.assertions:
        lines.append("## 断言")
        lines.append("")
        lines.append("| # | 类型 | 期望 | 实际 | 状态 |")
        lines.append("|---|------|------|------|------|")
        for a in result.assertions:
            lines.append(
                f"| {a.index} | {a.type} | {a.expected_summary or ''} | "
                f"{a.actual_summary or ''} | {_status_text(a.status)} |"
            )
            if a.semantic_evidence:
                lines.append(f"  - Canvas 业务语义证据：`{json.dumps(a.semantic_evidence, ensure_ascii=False, sort_keys=True)}`")
        lines.append("")

    if result.status != Status.PASSED:
        lines.append("## 失败信息")
        lines.append("")
        if result.failed_step_index is not None:
            lines.append(f"- 失败步骤：第 {result.failed_step_index} 步")
        if result.reproduction_steps:
            lines.append("- 复现步骤：")
            for i, rs in enumerate(result.reproduction_steps, start=1):
                lines.append(f"  {i}. {rs}")
        lines.append("")

    if result.cause_hints:
        lines.append("## 可能原因（启发式提示，非确定性诊断）")
        lines.append("")
        for h in result.cause_hints:
            lines.append(f"- **[{h.category.value}]** {h.message}")
            lines.append(f"  - 置信度：{h.confidence}（启发式）")
            if h.evidence:
                lines.append(f"  - 证据：{'；'.join(h.evidence)}")
        lines.append("")

    if result.findings:
        lines.append("## 结构化问题（待人工审核）")
        lines.append("")
        for finding in result.findings:
            lines.append(f"### {finding.title}")
            lines.append("")
            lines.append(f"- 分类：{finding.category}")
            lines.append(f"- 严重度／置信度：{finding.severity}／{finding.confidence}")
            lines.append(f"- 实际：{finding.actual_result}")
            lines.append(f"- 预期：{finding.expected_result}")
            if finding.evidence_timeline:
                lines.append("- 证据时间线：")
                for event in finding.evidence_timeline:
                    shot = f"；截图 `{event.screenshot}`" if event.screenshot else ""
                    lines.append(f"  - {event.timestamp.isoformat()} · {event.phase}{shot}")
                    for fact in event.facts:
                        lines.append(f"    - {fact}")
            lines.append("")

    if result.confirmation_history:
        lines.append("## 危险动作确认记录")
        lines.append("")

    async_steps = [step for step in result.steps if step.async_evidence]
    if async_steps or result.websocket_timeline:
        lines.extend(["## 异步与 WebSocket 证据", ""])
        for step in async_steps:
            evidence = step.async_evidence or {}
            lines.append(f"- 步骤 {step.index} · 对象 `{evidence.get('businessObjectId', '-')}` · {evidence.get('classification', '-') } · 终态 `{evidence.get('finalState', '-')}`")
        lines.append(f"- WebSocket 时间线事件：{len(result.websocket_timeline)} 条（脱敏字段级证据）")
        lines.append("")

    if result.cleanup_report:
        lines.extend(["## 业务对象清理", "", f"- 总体状态：{result.cleanup_report.get('status', '-')}"])
        for item in result.cleanup_report.get("objects", []):
            lines.append(f"- `{item.get('key')}` · {item.get('name')} · {item.get('status')} · 验证 {item.get('verified')}")
        for action in result.cleanup_report.get("manualActions", []):
            lines.append(f"- 人工处置：{action}")
        lines.append("")

    component_steps = [step for step in result.steps if step.component_evidence]
    if component_steps:
        lines.extend(["## 复杂组件语义证据", ""])
        for step in component_steps:
            evidence = step.component_evidence or {}
            lines.append(f"- 步骤 {step.index} · `{evidence.get('kind')}` · {evidence.get('semanticTarget')} · {evidence.get('status')}")
        lines.append("")

    if result.business_context_snapshot:
        context = result.business_context_snapshot
        lines.extend(["## 业务上下文快照", "", f"- 状态：{context.get('status', '-')}；完整度：{context.get('completeness', 0)}"])
        for item in context.get("blockedItems", []):
            lines.append(f"- 阻塞项：{item}")
        lines.append("")
        for item in result.confirmation_history:
            lines.append(
                f"- 步骤 {item.get('step_index')} · {item.get('action')} · 规则 {item.get('rule')} · "
                f"{item.get('decision')} · 操作者 {item.get('actor')} · {item.get('decided_at')}"
            )
        lines.append("")

    return "\n".join(lines)


def _hint_html(hint: CauseHint) -> str:
    evidence = (
        f"<div class='evidence'>证据：{html.escape('；'.join(hint.evidence))}</div>"
        if hint.evidence
        else ""
    )
    return (
        f"<li><span class='cat'>[{html.escape(hint.category.value)}]</span> "
        f"{html.escape(hint.message)}"
        f"<div class='conf'>置信度：{html.escape(hint.confidence)}（启发式）</div>"
        f"{evidence}</li>"
    )


def render_html(result: RunResult) -> str:
    """生成自包含 HTML 报告（内联样式，可直接双击打开）。"""
    rows = []
    for s in result.steps:
        note = html.escape(s.error_message or s.description or "")
        shot = (
            f"<a href='{html.escape(s.screenshot)}'>截图</a>"
            if s.screenshot
            else ""
        )
        rows.append(
            f"<tr class='{s.status.value}'><td>{s.index}</td>"
            f"<td>{html.escape(s.action)}</td>"
            f"<td>{html.escape(s.target_summary)}</td>"
            f"<td>{_status_text(s.status)}</td>"
            f"<td>{s.duration_ms}</td><td>{note}</td><td>{shot}</td></tr>"
        )
    steps_table = "\n".join(rows)

    evidence_html = ""
    if result.evidence_manifest:
        missing = [
            item for item in result.evidence_manifest.get("items", [])
            if item.get("status") == "missing"
        ]
        missing_html = "".join(
            f"<li>{html.escape(str(item.get('label', item.get('id', '未知证据'))))}："
            f"{html.escape(str(item.get('reason') or '未生成'))}</li>"
            for item in missing
        ) or "<li>无</li>"
        manifest_link = html.escape(result.evidence_manifest_path or "evidence/evidence-manifest.json")
        evidence_html = (
            "<h2>证据完整度</h2>"
            f"<p><strong>{result.evidence_completeness * 100:.1f}%</strong> · "
            f"<a href='{manifest_link}'>查看机器可读清单</a></p><ul>{missing_html}</ul>"
        )

    canvas_html = ""
    canvas_steps = [step for step in result.steps if step.canvas_evidence]
    if canvas_steps:
        sections = []
        for step in canvas_steps:
            evidence = step.canvas_evidence or {}
            payload = html.escape(json.dumps(evidence, ensure_ascii=False, indent=2))
            sections.append(
                f"<details><summary>步骤 {step.index} · {html.escape(step.action)} · "
                f"{html.escape(str(evidence.get('collectionStatus', 'unknown')))}</summary>"
                f"<pre>{payload}</pre></details>"
            )
        canvas_html = "<h2>Canvas／Bridge 证据</h2>" + "".join(sections)

    file_html = ""
    file_steps = [step for step in result.steps if step.file_evidence]
    if file_steps:
        sections = []
        for step in file_steps:
            evidence = step.file_evidence or {}
            payload = html.escape(json.dumps(evidence, ensure_ascii=False, indent=2))
            artifact = evidence.get("artifact")
            link = f"<p><a href='{html.escape(str(artifact))}'>下载工件</a></p>" if artifact else ""
            sections.append(
                f"<details><summary>步骤 {step.index} · {html.escape(step.action)} · "
                f"{html.escape(str(evidence.get('status', 'unknown')))}</summary>{link}<pre>{payload}</pre></details>"
            )
        file_html = "<h2>上传／下载证据</h2>" + "".join(sections)

    assertions_html = ""
    if result.assertions:
        arows = []
        for a in result.assertions:
            arows.append(
                f"<tr class='{a.status.value}'><td>{a.index}</td>"
                f"<td>{html.escape(a.type)}</td>"
                f"<td>{html.escape(a.expected_summary or '')}</td>"
                f"<td>{html.escape(a.actual_summary or '')}</td>"
                f"<td>{_status_text(a.status)}</td></tr>"
            )
        assertions_html = (
            "<h2>断言</h2><table><thead><tr><th>#</th><th>类型</th>"
            "<th>期望</th><th>实际</th><th>状态</th></tr></thead><tbody>"
            + "\n".join(arows)
            + "</tbody></table>"
        )
        semantic_sections = "".join(
            f"<details><summary>断言 {a.index} Canvas 业务语义证据</summary><pre>{html.escape(json.dumps(a.semantic_evidence, ensure_ascii=False, indent=2))}</pre></details>"
            for a in result.assertions if a.semantic_evidence
        )
        assertions_html += semantic_sections

    failure_html = ""
    if result.status != Status.PASSED:
        repro = "".join(
            f"<li>{html.escape(rs)}</li>" for rs in result.reproduction_steps
        )
        failed = (
            f"<p>失败步骤：第 {result.failed_step_index} 步</p>"
            if result.failed_step_index is not None
            else ""
        )
        failure_html = (
            f"<h2>失败信息</h2>{failed}"
            f"<p>复现步骤：</p><ol>{repro}</ol>"
        )

    hints_html = ""
    if result.cause_hints:
        items = "".join(_hint_html(h) for h in result.cause_hints)
        hints_html = (
            "<h2>可能原因 <small>（启发式提示，非确定性诊断）</small></h2>"
            f"<ul class='hints'>{items}</ul>"
        )

    findings_html = ""
    if result.findings:
        cards = []
        for finding in result.findings:
            facts = "".join(f"<li>{html.escape(fact)}</li>" for fact in finding.facts if fact)
            timeline = []
            for event in finding.evidence_timeline:
                shot = (
                    f" <a href='{html.escape(event.screenshot)}'>截图</a>"
                    if event.screenshot else ""
                )
                event_facts = "".join(f"<li>{html.escape(fact)}</li>" for fact in event.facts)
                timeline.append(
                    f"<li><strong>{html.escape(event.phase)}</strong> · "
                    f"{html.escape(event.timestamp.isoformat())}{shot}<ul>{event_facts}</ul></li>"
                )
            timeline_html = f"<ol>{''.join(timeline)}</ol>" if timeline else "<p>无时间线证据</p>"
            cards.append(
                f"<article class='finding'><h3>{html.escape(finding.title)}</h3>"
                f"<p>{html.escape(finding.category)} · {html.escape(finding.severity)} · "
                f"置信度 {html.escape(finding.confidence)}</p>"
                f"<p><strong>实际：</strong>{html.escape(finding.actual_result)}</p>"
                f"<p><strong>预期：</strong>{html.escape(finding.expected_result)}</p>"
                f"<ul>{facts}</ul><h4>证据时间线</h4>{timeline_html}</article>"
            )
        findings_html = "<h2>结构化问题 <small>（待人工审核）</small></h2>" + "".join(cards)

    confirmations_html = ""
    if result.confirmation_history:
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(str(item.get('step_index', '')))}</td>"
            f"<td>{html.escape(str(item.get('action', '')))}</td>"
            f"<td>{html.escape(str(item.get('rule', '')))}</td>"
            f"<td>{html.escape(str(item.get('decision', '')))}</td>"
            f"<td>{html.escape(str(item.get('actor', '')))}</td>"
            f"<td>{html.escape(str(item.get('decided_at', '')))}</td></tr>"
            for item in result.confirmation_history
        )
        confirmations_html = (
            "<h2>危险动作确认记录</h2><table><thead><tr><th>步骤</th><th>动作</th><th>规则</th>"
            f"<th>决定</th><th>操作者</th><th>时间</th></tr></thead><tbody>{rows}</tbody></table>"
        )

    async_html = ""
    async_steps = [step for step in result.steps if step.async_evidence]
    if async_steps or result.websocket_timeline:
        rows = "".join(
            f"<tr><td>{step.index}</td><td>{html.escape(str((step.async_evidence or {}).get('businessObjectId', '-')))}</td>"
            f"<td>{html.escape(str((step.async_evidence or {}).get('classification', '-')))}</td>"
            f"<td>{html.escape(str((step.async_evidence or {}).get('finalState', '-')))}</td></tr>"
            for step in async_steps
        )
        async_html = f"<h2>异步与 WebSocket 证据</h2><p>WebSocket 脱敏事件 {len(result.websocket_timeline)} 条</p><table><thead><tr><th>步骤</th><th>对象</th><th>分类</th><th>终态</th></tr></thead><tbody>{rows}</tbody></table>"

    cleanup_html = ""
    if result.cleanup_report:
        rows = "".join(
            f"<tr><td>{html.escape(str(item.get('key', '')))}</td><td>{html.escape(str(item.get('name', '')))}</td>"
            f"<td>{html.escape(str(item.get('status', '')))}</td><td>{html.escape(str(item.get('verified', '')))}</td></tr>"
            for item in result.cleanup_report.get("objects", [])
        )
        cleanup_html = f"<h2>业务对象清理</h2><p>总体状态：{html.escape(str(result.cleanup_report.get('status', '-')))}</p><table><thead><tr><th>对象键</th><th>名称</th><th>状态</th><th>验证</th></tr></thead><tbody>{rows}</tbody></table>"

    component_html = ""
    component_steps = [step for step in result.steps if step.component_evidence]
    if component_steps:
        rows = "".join(
            f"<tr><td>{step.index}</td><td>{html.escape(str((step.component_evidence or {}).get('kind', '')))}</td>"
            f"<td>{html.escape(str((step.component_evidence or {}).get('semanticTarget', '')))}</td>"
            f"<td>{html.escape(str((step.component_evidence or {}).get('status', '')))}</td></tr>"
            for step in component_steps
        )
        component_html = f"<h2>复杂组件语义证据</h2><table><thead><tr><th>步骤</th><th>组件</th><th>语义目标</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table>"

    context_html = ""
    if result.business_context_snapshot:
        context = result.business_context_snapshot
        blocked = "".join(f"<li>{html.escape(str(item))}</li>" for item in context.get("blockedItems", []))
        context_html = f"<h2>业务上下文快照</h2><p>状态：{html.escape(str(context.get('status', '-')))} ｜ 完整度：{html.escape(str(context.get('completeness', 0)))}</p><ul>{blocked}</ul>"

    status_cls = result.status.value
    isolation_html = ""
    if result.runner_isolation:
        isolation = result.runner_isolation
        isolation_html = (
            "<div>Runner 隔离："
            f"{html.escape(str(isolation.get('mode', '-')))} ｜ Windows Job："
            f"{'已绑定' if isolation.get('windows_job_assigned') else '未绑定'} ｜ 内存上限："
            f"{html.escape(str(isolation.get('memory_limit_mb', '-')))} MB ｜ 强制终止："
            f"{'是' if isolation.get('forced_termination') else '否'}</div>"
        )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>测试报告 - {html.escape(result.plan_name)}</title>
<style>
  body {{ font-family: "Segoe UI", system-ui, sans-serif; margin: 2rem; color: #1f2328; }}
  h1 {{ font-size: 1.4rem; }}
  .summary {{ background: #f6f8fa; padding: 1rem; border-radius: 8px; }}
  .summary .status {{ font-weight: 700; }}
  .status.passed {{ color: #1a7f37; }}
  .status.failed, .status.error {{ color: #cf222e; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #d0d7de; padding: .4rem .6rem; text-align: left; font-size: .9rem; }}
  th {{ background: #f6f8fa; }}
  tr.failed, tr.error {{ background: #fff0f0; }}
  tr.passed {{ background: #f0fff4; }}
  tr.skipped {{ color: #999; }}
  .hints li {{ margin-bottom: .6rem; }}
  .hints .cat {{ font-weight: 700; color: #9a6700; }}
  .hints .conf, .hints .evidence {{ font-size: .8rem; color: #57606a; }}
  .finding {{ border: 1px solid #d0d7de; padding: .8rem 1rem; margin: .8rem 0; border-radius: 6px; }}
  .finding h3 {{ margin-top: 0; }}
  small {{ color: #57606a; font-weight: 400; }}
</style></head>
<body>
  <h1>测试运行报告：{html.escape(result.plan_name)}</h1>
  <div class="summary">
    <div>运行 ID：<code>{html.escape(result.run_id)}</code></div>
    <div>总体状态：<span class="status {status_cls}">{_status_text(result.status)}</span></div>
    <div>基础地址：{html.escape(result.base_url_summary)}</div>
    <div>耗时：{result.duration_ms} ms ｜ 退出码：{result.exit_code}</div>
    {isolation_html}
  </div>
  {evidence_html}
  <h2>步骤</h2>
  <table><thead><tr><th>#</th><th>动作</th><th>目标</th><th>状态</th>
  <th>耗时(ms)</th><th>说明</th><th>截图</th></tr></thead>
  <tbody>{steps_table}</tbody></table>
  {canvas_html}
  {file_html}
  {async_html}
  {cleanup_html}
  {component_html}
  {context_html}
  {assertions_html}
  {failure_html}
  {hints_html}
  {findings_html}
  {confirmations_html}
</body></html>"""


def write_reports(result: RunResult, run_dir: Path) -> None:
    """把 Markdown 与 HTML 报告写入运行目录。"""
    (run_dir / "report.md").write_text(render_markdown(result), encoding="utf-8")
    (run_dir / "report.html").write_text(render_html(result), encoding="utf-8")
