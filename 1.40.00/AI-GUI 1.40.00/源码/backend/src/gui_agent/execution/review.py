"""Persist an auditable, user-approved regression path for a completed run."""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from ..domain.models import ActionType, Step, TestPlan
from .compiler import compile_test


class RunReviewError(ValueError):
    pass


def load_path_review(run_dir: Path) -> dict:
    original = _load_plan(run_dir / "plan.json")
    review_data = _read_json(run_dir / "review.json", default={})
    reviewed_path = run_dir / "reviewed-plan.json"
    reviewed = _load_plan(reviewed_path) if reviewed_path.is_file() else original
    source_indexes = review_data.get("sourceIndexes") or list(range(1, len(original.steps) + 1))
    if len(source_indexes) != len(reviewed.steps):
        raise RunReviewError("审核记录与审核计划不一致")
    approved_by_source = dict(zip(source_indexes, reviewed.steps, strict=True))
    return {
        "available": True,
        "steps": [
            {
                "sourceIndex": index,
                "retained": index in approved_by_source,
                "step": (approved_by_source.get(index) or step).model_dump(mode="json", exclude_none=True),
            }
            for index, step in enumerate(original.steps, start=1)
        ],
        "history": review_data.get("history", []),
    }


def apply_path_review(run_dir: Path, submitted: list[tuple[int, bool, Step]]) -> dict:
    original = _load_plan(run_dir / "plan.json")
    run_path = run_dir / "run.json"
    if not run_path.is_file():
        raise RunReviewError("运行记录不存在")
    run_data = _read_json(run_path)
    if run_data.get("status") in {"queued", "running"}:
        raise RunReviewError("运行尚未结束，不能审核回归路径")

    expected_indexes = set(range(1, len(original.steps) + 1))
    submitted_indexes = [index for index, _, _ in submitted]
    if len(submitted_indexes) != len(set(submitted_indexes)) or set(submitted_indexes) != expected_indexes:
        raise RunReviewError("必须提交完整且不重复的原始步骤编号")
    retained = [(index, step) for index, keep, step in submitted if keep]
    if not retained:
        raise RunReviewError("至少保留一个回归步骤")
    for _, step in retained:
        _validate_sensitive_input(step)

    previous = load_path_review(run_dir)
    previous_by_source = {item["sourceIndex"]: item for item in previous["steps"]}
    changes: list[dict] = []
    for source_index, keep, step in submitted:
        old = previous_by_source[source_index]
        old_step = old["step"]
        new_step = step.model_dump(mode="json", exclude_none=True)
        if old["retained"] and not keep:
            changes.append({"sourceIndex": source_index, "action": "removed", "before": old_step})
        elif not old["retained"] and keep:
            changes.append({"sourceIndex": source_index, "action": "restored", "after": new_step})
        elif keep and old_step != new_step:
            changes.append({"sourceIndex": source_index, "action": "edited", "before": old_step, "after": new_step})

    approved_plan = original.model_copy(update={"steps": [step for _, step in retained]})
    source_indexes = [index for index, _ in retained]
    source, generated = compile_test(approved_plan)
    generated.source_path = "generated-test.spec.ts"
    previous_generated = run_data.get("generated_test") or {}
    previous_source = str(previous_generated.get("source") or "")
    source_history = list(previous_generated.get("source_review_history") or [])
    if previous_source and previous_source != source:
        source_history.append(_source_history_entry(
            action="regenerated_from_path_review",
            before=previous_source,
            after=source,
            revision=int(previous_generated.get("source_revision") or 1) + 1,
        ))
        generated.source_revision = int(previous_generated.get("source_revision") or 1) + 1
    generated.source_review_history = source_history
    generated_payload = generated.model_dump(mode="json")

    history = list(previous["history"])
    if changes:
        history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": "local-user",
            "changes": changes,
            "retainedSourceIndexes": source_indexes,
        })

    _write_json_atomic(run_dir / "reviewed-plan.json", approved_plan.model_dump(mode="json", exclude_none=True))
    _write_json_atomic(run_dir / "review.json", {"sourceIndexes": source_indexes, "history": history})
    (run_dir / "generated-test.spec.ts").write_bytes(source.encode("utf-8"))
    run_data["generated_test"] = generated_payload
    run_data["stability_level"] = generated.stability_level
    run_data["path_review_history"] = history
    _write_json_atomic(run_path, run_data)
    return load_path_review(run_dir)


def save_generated_source(run_dir: Path, source: str) -> dict:
    run_path = run_dir / "run.json"
    if not run_path.is_file():
        raise RunReviewError("运行记录不存在")
    run_data = _read_json(run_path)
    if run_data.get("status") in {"queued", "running"}:
        raise RunReviewError("运行尚未结束，不能编辑生成测试")
    generated = run_data.get("generated_test")
    if not isinstance(generated, dict):
        raise RunReviewError("该运行尚未生成 Playwright 测试")
    _validate_generated_source(source)
    previous = str(generated.get("source") or "")
    if source == previous:
        return generated
    revision = int(generated.get("source_revision") or 1) + 1
    history = list(generated.get("source_review_history") or [])
    history.append(_source_history_entry(
        action="manual_source_edit",
        before=previous,
        after=source,
        revision=revision,
    ))
    generated["source"] = source
    generated["source_revision"] = revision
    generated["source_review_history"] = history
    source_path = run_dir / "generated-test.spec.ts"
    temporary_source = source_path.with_suffix(".spec.ts.tmp")
    temporary_source.write_bytes(source.encode("utf-8"))
    temporary_source.replace(source_path)
    run_data["generated_test"] = generated
    _write_json_atomic(run_path, run_data)
    return generated


def _validate_sensitive_input(step: Step) -> None:
    if step.action != ActionType.FILL or step.value is None:
        return
    locator_text = " ".join(filter(None, (
        step.locator.label if step.locator else None,
        step.locator.name if step.locator else None,
        step.locator.test_id if step.locator else None,
        step.description,
    ))).lower().replace(" ", "")
    if any(token in locator_text for token in ("密码", "password", "passwd", "token", "apikey", "secret")):
        raise RunReviewError("敏感输入步骤必须使用 value_from_secret，不能保存明文")


def _validate_generated_source(source: str) -> None:
    if "@playwright/test" not in source or not re.search(r"\btest\s*\(", source):
        raise RunReviewError("生成源码必须保留 Playwright test 入口")
    secret_assignment = re.compile(
        r"(?i)(?:password|passwd|token|api[_-]?key|secret)\s*[:=]\s*['\"][^'\"]{3,}['\"]"
    )
    sensitive_fill = re.compile(
        r"(?i)getByLabel\(\s*['\"](?:密码|password|passwd|token|api\s*key|secret)['\"]\s*\)"
        r"\.fill\(\s*['\"][^'\"]+['\"]\s*\)"
    )
    bearer_literal = re.compile(r"(?i)bearer\s+[a-z0-9._-]{8,}")
    if secret_assignment.search(source) or sensitive_fill.search(source) or bearer_literal.search(source):
        raise RunReviewError("生成源码疑似包含明文密码、Token 或 API Key，请改用 process.env 密钥引用")


def _source_history_entry(*, action: str, before: str, after: str, revision: int) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": "local-user",
        "action": action,
        "revision": revision,
        "beforeSha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
        "afterSha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
    }


def _load_plan(path: Path) -> TestPlan:
    if not path.is_file():
        raise RunReviewError("运行计划不存在，无法审核路径")
    try:
        return TestPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise RunReviewError("运行计划损坏，无法审核路径") from exc


def _read_json(path: Path, default: dict | None = None) -> dict:
    if not path.is_file():
        return {} if default is None else default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunReviewError(f"审核工件无法读取：{path.name}") from exc
    if not isinstance(value, dict):
        raise RunReviewError(f"审核工件格式非法：{path.name}")
    return value


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
