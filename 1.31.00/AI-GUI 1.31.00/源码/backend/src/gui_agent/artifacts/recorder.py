"""运行产物记录器。

负责一次运行的产物目录与机器可读事件流：

    artifacts/<run-id>/
      run.json        总体结果（RunResult 序列化）
      events.jsonl    逐步事件，每行一个 JSON
      report.html     人类可读报告
      report.md       人类可读报告
      screenshots/    截图

所有写入前都经过 Redactor 脱敏，保证密钥/敏感值不落盘。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain.results import RunResult
from ..security.redaction import Redactor


def new_run_id(now: datetime | None = None) -> str:
    """生成基于时间的运行 ID，Windows 文件名安全。"""
    now = now or datetime.now(timezone.utc)
    return now.strftime("run-%Y%m%d-%H%M%S-%f")


class RunRecorder:
    """管理单次运行的产物目录与事件流。"""

    def __init__(self, run_id: str, artifacts_root: Path, redactor: Redactor) -> None:
        self.run_id = run_id
        self.root = Path(artifacts_root) / run_id
        self.screenshots_dir = self.root / "screenshots"
        self._redactor = redactor
        self._events_path = self.root / "events.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    def event(self, event_type: str, **fields: Any) -> None:
        """追加一条事件到 events.jsonl。字段值经过脱敏。"""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
        }
        for key, value in fields.items():
            record[key] = self._redact_value(value)
        line = json.dumps(record, ensure_ascii=False)
        with self._events_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def screenshot_path(self, name: str) -> Path:
        """返回截图应保存的绝对路径（不创建文件）。"""
        safe = name.replace("/", "_").replace("\\", "_")
        return self.screenshots_dir / safe

    def relative_screenshot(self, path: Path) -> str:
        """把截图绝对路径转成相对 run 目录的路径，供报告引用。"""
        return str(Path(path).relative_to(self.root)).replace("\\", "/")

    def write_run_json(self, result: RunResult) -> Path:
        """写 run.json。整体经过脱敏序列化。"""
        payload = json.loads(result.model_dump_json())
        redacted = self._redact_value(payload)
        path = self.root / "run.json"
        path.write_text(
            json.dumps(redacted, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redactor.redact(value)
        if isinstance(value, dict):
            return {k: self._redact_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_value(v) for v in value]
        return value
