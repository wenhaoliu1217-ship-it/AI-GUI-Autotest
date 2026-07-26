"""运行产物目录、结构化事件和人类可读报告。"""

from __future__ import annotations

import html
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain.results import RunResult, Status
from ..security.redaction import Redactor
from .report import render_html, render_markdown


class ArtifactManager:
    """管理一次运行的全部证据，所有文本写入前经过脱敏。"""

    def __init__(
        self,
        root: str | Path,
        run_id: str,
        redactor: Redactor,
        screenshot_mask_selectors: tuple[str, ...] = (),
    ) -> None:
        self.run_dir = Path(root) / run_id
        self.screenshots_dir = self.run_dir / "screenshots"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"
        self.redactor = redactor
        self.screenshot_mask_selectors = screenshot_mask_selectors

    def event(self, event_type: str, **payload: Any) -> None:
        record = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "type": event_type,
            **payload,
        }
        scrubbed = self.redactor.scrub_mapping(record)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(scrubbed, ensure_ascii=False, default=str) + "\n")

    def screenshot_path(self, name: str) -> tuple[Path, str]:
        safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
        relative = f"screenshots/{safe_name}.png"
        return self.run_dir / relative, relative

    def download_path(self, suggested_name: str) -> tuple[Path, str]:
        name = Path(suggested_name).name
        if not name or name != suggested_name:
            raise ValueError("下载文件名非法")
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        candidate = Path(safe_name)
        suffix = 1
        relative = f"downloads/{safe_name}"
        while (self.run_dir / relative).exists():
            suffix += 1
            relative = f"downloads/{candidate.stem}-{suffix}{candidate.suffix}"
        target = (self.run_dir / relative).resolve()
        if self.run_dir.resolve() not in target.parents:
            raise ValueError("下载路径越界")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target, relative

    def write_json(self, relative: str, payload: dict) -> str:
        target = self.run_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.redactor.scrub_mapping(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return relative.replace("\\", "/")

    def write_text(self, relative: str, value: str) -> str:
        target = self.run_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.redactor.scrub(value).encode("utf-8"))
        return relative.replace("\\", "/")

    @property
    def trace_path(self) -> Path:
        return self.run_dir / "trace.zip"

    def redact_trace(self) -> None:
        """重写 Playwright trace，移除动作参数和 DOM 快照中的密钥明文。"""
        source = self.trace_path
        if not source.exists():
            return
        temporary = source.with_suffix(".redacted.zip")
        with zipfile.ZipFile(source, "r") as reader, zipfile.ZipFile(temporary, "w") as writer:
            for entry in reader.infolist():
                writer.writestr(entry, self.redactor.scrub_bytes(reader.read(entry.filename)))
        temporary.replace(source)

    def finalize(self, result: RunResult) -> None:
        raw = result.model_dump(mode="json")
        scrubbed = self.redactor.scrub_mapping(raw)
        (self.run_dir / "run.json").write_text(
            json.dumps(scrubbed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.run_dir / "report.md").write_text(
            render_markdown(result), encoding="utf-8"
        )
        (self.run_dir / "report.html").write_text(
            render_html(result), encoding="utf-8"
        )

    def _markdown_report(self, result: RunResult) -> str:
        status = "通过" if result.status == Status.PASSED else "失败"
        lines = [
            f"# {self.redactor.scrub(result.plan_name)}",
            "",
            f"- 运行编号：`{result.run_id}`",
            f"- 结果：**{status}**",
            f"- 角色：`{result.role or '-'}`",
            f"- 耗时：`{result.duration_ms} ms`",
            "",
            "## 执行步骤",
            "",
        ]
        for step in result.steps:
            icon = "PASS" if step.status == Status.PASSED else "FAIL"
            lines.append(
                f"- [{icon}] {step.index}. {self.redactor.scrub(step.description or step.action)} "
                f"({step.duration_ms} ms)"
            )
            if step.error_message:
                lines.append(f"  - 错误：{self.redactor.scrub(step.error_message)}")
            if step.before:
                lines.append(
                    f"  - 动作前事实：{self.redactor.scrub(step.before.title or '无标题')} ｜ "
                    f"{self.redactor.scrub(step.before.url)}"
                )
            if step.after:
                runtime_errors = (
                    step.after.console_errors + step.after.page_errors + step.after.failed_requests
                )
                lines.append(
                    f"  - 动作后事实：{self.redactor.scrub(step.after.title or '无标题')} ｜ "
                    f"{self.redactor.scrub(step.after.url)} ｜ 运行时异常 {len(runtime_errors)} 条"
                )
        if result.assertions:
            lines.extend(["", "## 断言", ""])
            for assertion in result.assertions:
                icon = "PASS" if assertion.status == Status.PASSED else "FAIL"
                lines.append(f"- [{icon}] {self.redactor.scrub(assertion.description or assertion.type)}")
        if result.cause_hints:
            lines.extend(["", "## 可能原因（启发式建议）", ""])
            for hint in result.cause_hints:
                lines.append(f"- {self.redactor.scrub(hint.message)}（置信度：{hint.confidence}）")
        return "\n".join(lines) + "\n"

    def _html_report(self, result: RunResult) -> str:
        passed = result.status == Status.PASSED
        status_cn = "测试通过" if passed else "测试失败"
        status_class = "passed" if passed else "failed"
        step_rows = []
        for step in result.steps:
            error = self.redactor.scrub(step.error_message or "")
            evidence = (
                f'<a href="{html.escape(step.screenshot)}">查看截图</a>'
                if step.screenshot else "-"
            )
            before_link = (
                f'<a href="{html.escape(step.before.screenshot)}">动作前</a>'
                if step.before and step.before.screenshot else "-"
            )
            observation = "-"
            if step.after:
                observed_errors = step.after.console_errors + step.after.page_errors + step.after.failed_requests
                error_list = "".join(
                    f"<li>{html.escape(self.redactor.scrub(item))}</li>" for item in observed_errors
                )
                observation = (
                    f"<div><strong>{html.escape(self.redactor.scrub(step.after.title or '无标题'))}</strong></div>"
                    f"<div class='url'>{html.escape(self.redactor.scrub(step.after.url))}</div>"
                    f"<div>DOM 摘要 {len(step.after.dom_summary)} 项；运行时异常 {len(observed_errors)} 条</div>"
                    + (f"<ul class='runtime-errors'>{error_list}</ul>" if error_list else "")
                )
            step_rows.append(
                "<tr>"
                f"<td>{step.index}</td>"
                f"<td>{html.escape(self.redactor.scrub(step.description or step.action))}</td>"
                f'<td><span class="status {step.status.value}">{step.status.value.upper()}</span></td>'
                f"<td>{step.duration_ms} ms</td>"
                f"<td>{html.escape(error) if error else observation}</td>"
                f"<td>{before_link} · {evidence}</td>"
                "</tr>"
            )
        assertion_rows = []
        for assertion in result.assertions:
            assertion_rows.append(
                "<tr>"
                f"<td>{assertion.index}</td>"
                f"<td>{html.escape(self.redactor.scrub(assertion.description or assertion.type))}</td>"
                f'<td><span class="status {assertion.status.value}">{assertion.status.value.upper()}</span></td>'
                f"<td>{html.escape(self.redactor.scrub(assertion.actual_summary or '-'))}</td>"
                "</tr>"
            )
        hints = "".join(
            f"<li><strong>{html.escape(h.category.value)}</strong> "
            f"{html.escape(self.redactor.scrub(h.message))} "
            f"<small>置信度 {html.escape(h.confidence)}</small></li>"
            for h in result.cause_hints
        ) or "<li>本次运行没有失败原因提示。</li>"
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(self.redactor.scrub(result.plan_name))} - 测试报告</title>
<style>
:root{{--ink:#20242a;--muted:#66707c;--line:#dfe3e7;--paper:#fff;--bg:#f4f6f7;--green:#157f52;--red:#c43b3b;--amber:#a16410;--nav:#202a2e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif;letter-spacing:0}}
header{{background:var(--nav);color:#fff;padding:28px max(24px,calc((100% - 1120px)/2))}}header p{{margin:6px 0 0;color:#c5d0d3}}
main{{max-width:1120px;margin:0 auto;padding:24px}}h1{{font-size:24px;margin:0}}h2{{font-size:17px;margin:0 0 14px}}
.summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid var(--line);background:var(--paper);margin-bottom:20px}}
.metric{{padding:18px;border-right:1px solid var(--line)}}.metric:last-child{{border:0}}.metric small{{display:block;color:var(--muted);margin-bottom:4px}}.metric strong{{font-size:18px}}
section{{background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:20px;margin-bottom:16px;overflow:auto}}
table{{width:100%;border-collapse:collapse;min-width:720px}}th,td{{padding:11px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}th{{font-size:12px;color:var(--muted);font-weight:600;background:#fafbfb}}
.url{{max-width:360px;overflow-wrap:anywhere;color:var(--muted)}}.runtime-errors{{margin-top:6px;color:var(--red)}}
.status{{display:inline-block;font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px}}.status.passed{{background:#e4f4ec;color:var(--green)}}.status.failed,.status.error{{background:#fbe8e8;color:var(--red)}}
.result{{color:var(--green)}}.result.failed{{color:var(--red)}}ul{{margin:0;padding-left:20px}}a{{color:#176b87}}footer{{color:var(--muted);padding:8px 0 24px}}
@media(max-width:720px){{.summary{{grid-template-columns:1fr 1fr}}.metric:nth-child(2){{border-right:0}}header{{padding:22px 18px}}main{{padding:14px}}}}
</style></head><body>
<header><h1>GUI Agent 测试报告</h1><p>{html.escape(self.redactor.scrub(result.plan_name))}</p></header>
<main>
<div class="summary">
 <div class="metric"><small>运行结果</small><strong class="result {status_class}">{status_cn}</strong></div>
 <div class="metric"><small>执行角色</small><strong>{html.escape(result.role or '-')}</strong></div>
 <div class="metric"><small>总耗时</small><strong>{result.duration_ms} ms</strong></div>
 <div class="metric"><small>运行编号</small><strong style="font-size:12px">{html.escape(result.run_id)}</strong></div>
</div>
<section><h2>执行步骤</h2><table><thead><tr><th>#</th><th>动作</th><th>状态</th><th>耗时</th><th>观察事实 / 错误</th><th>截图</th></tr></thead><tbody>{''.join(step_rows)}</tbody></table></section>
<section><h2>结果断言</h2><table><thead><tr><th>#</th><th>检查项</th><th>状态</th><th>实际结果</th></tr></thead><tbody>{''.join(assertion_rows) or '<tr><td colspan="4">无收尾断言</td></tr>'}</tbody></table></section>
<section><h2>可能原因</h2><ul>{hints}</ul><p style="color:var(--muted);margin-bottom:0">原因提示为启发式建议，需要开发人员结合证据复核。</p></section>
<footer>GUI Agent v0.1 · 结构化证据报告</footer>
</main></body></html>"""
