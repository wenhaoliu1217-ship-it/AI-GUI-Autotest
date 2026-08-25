"""通用网页验证基线。

这里故意只描述网页语义和可验证结果，不保存 Cesium、Canvas 或企业站点
专用选择器。两个页面都是本地可重复夹具：一个覆盖普通表单/表格流程，
另一个覆盖 Shadow DOM 里的控件，作为 1.33.00 通用能力的最低回归门槛。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from playwright.sync_api import sync_playwright

from ..demo.server import DemoServer, find_available_port
from ..version import APP_VERSION


def _base_payload() -> dict:
    sites = [
        {
            "id": "generic-crm",
            "name": "云衡 CRM 本地夹具",
            "path": "/",
            "surface": "普通表单、选择框、表格和动态状态",
            "tasks": [
                {
                    "id": "G01",
                    "title": "登录后创建客户",
                    "goal": "使用演示账号登录，创建一个客户并确认列表数量变化。",
                    "expected": "登录层消失，客户列表出现新客户，统计数字同步更新。",
                    "effect": "isolated_local_write",
                    "status": "unverified",
                },
                {
                    "id": "G02",
                    "title": "读取客户列表状态",
                    "goal": "读取客户列表中的客户名称、负责人和服务状态，不修改数据。",
                    "expected": "能够从可见表格和状态标签中得到结构化结果。",
                    "effect": "read_only",
                    "status": "unverified",
                },
            ],
        },
        {
            "id": "generic-shadow",
            "name": "Shadow DOM 资产夹具",
            "path": "/shadow.html",
            "surface": "开放 Shadow DOM、插槽、表单控件和缺失资源诊断",
            "tasks": [
                {
                    "id": "G03",
                    "title": "定位 Shadow DOM 搜索框",
                    "goal": "在资产工具中找到搜索框并输入一个资产关键词。",
                    "expected": "通过可访问名称、标签或 Shadow DOM 路径定位到搜索框。",
                    "effect": "session_only",
                    "status": "unverified",
                },
                {
                    "id": "G04",
                    "title": "执行 Shadow DOM 操作",
                    "goal": "点击创建 Story 操作并记录可复现的定位依据。",
                    "expected": "动作目标属于资产工具内部控件，报告不依赖专项引擎专用类名。",
                    "effect": "session_only",
                    "status": "unverified",
                },
            ],
        },
    ]
    tasks = [task for site in sites for task in site["tasks"]]
    return {
        "suite": "generic-web",
        "version": APP_VERSION,
        "targetKind": "generic-web",
        "truthPolicy": "未执行真实浏览器回归前只标记为未验证，不把合同检查当作通过。",
        "compatibilityBoundary": {
            "genericLayerMayAssume": [
                "可观察的 DOM/ARIA/文本语义",
                "页面 URL、标题和加载状态",
                "可复现的动作前后证据",
            ],
            "genericLayerMustNotAssume": [
                "Cesium 路径、按钮类名或全局对象",
                "固定网站元素 ID",
                "某一种渲染引擎的内部状态",
            ],
        },
        "summary": {
            "siteCount": len(sites),
            "taskCount": len(tasks),
            "verified": 0,
            "unverified": len(tasks),
        },
        "sites": sites,
    }


def _load_latest_run(artifacts_root: Path | str | None) -> dict | None:
    if artifacts_root is None:
        return None
    path = Path(artifacts_root) / "generic-web" / "latest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def validation_payload(artifacts_root: Path | str | None = None) -> dict:
    """Return the generic contract and, when requested, the last real run."""

    payload = _base_payload()
    latest = _load_latest_run(artifacts_root)
    if not latest:
        return payload

    status_by_task = {item["taskId"]: item["status"] for item in latest.get("tasks", [])}
    for site in payload["sites"]:
        for task in site["tasks"]:
            task["status"] = status_by_task.get(task["id"], task["status"])
    verified = sum(task["status"] == "passed" for site in payload["sites"] for task in site["tasks"])
    payload["summary"] = {
        **payload["summary"],
        "verified": verified,
        "unverified": payload["summary"]["taskCount"] - verified,
    }
    payload["lastRun"] = latest.get("run")
    return payload


def _task(task_id: str, site_id: str, status: str, facts: list[str], action_count: int, error: str | None = None) -> dict:
    result = {
        "taskId": task_id,
        "siteId": site_id,
        "status": status,
        "facts": facts[:12],
        "actionCount": action_count,
    }
    if error:
        result["errorClass"] = "GenericWebRegressionError"
        result["error"] = error[:500]
    return result


def _write_regression_evidence(artifacts_root: Path | str | None, evidence: dict) -> str | None:
    if artifacts_root is None:
        return None
    root = Path(artifacts_root).resolve() / "generic-web"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = root / f"regression-{stamp}.json"
    latest = root / "latest.json"
    encoded = json.dumps(evidence, ensure_ascii=False, indent=2)
    timestamped.write_text(encoded, encoding="utf-8")
    latest.write_text(encoded, encoding="utf-8")
    return str(timestamped.relative_to(Path(artifacts_root).resolve())).replace("\\", "/")


def run_regression(artifacts_root: Path | str | None = None) -> dict:
    """Execute the two local non-Cesium sites and return persisted evidence."""

    started_at = datetime.now(timezone.utc)
    started = monotonic()
    tasks: list[dict] = []
    with DemoServer(port=find_available_port()) as demo:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                crm = browser.new_page()
                crm.goto(f"{demo.url}/", wait_until="domcontentloaded")
                actions = 0
                try:
                    crm.get_by_label("用户名").fill("admin")
                    crm.get_by_label("密码").fill("admin123")
                    crm.get_by_role("button", name="登录").click()
                    actions += 3
                    crm.locator("#loginLayer").wait_for(state="hidden")
                    name = "E2E_通用回归客户"
                    crm.get_by_label("客户名称").fill(name)
                    crm.get_by_label("负责人").select_option("emp2")
                    crm.get_by_role("button", name="新建客户").click()
                    actions += 3
                    assert crm.get_by_text(name, exact=True).count() == 1
                    assert int(crm.locator("#customerCount").inner_text()) == 3
                    tasks.append(_task("G01", "generic-crm", "passed", ["登录层已消失", "客户总数由 2 更新为 3", "新客户出现在列表"], actions))
                except Exception as exc:
                    tasks.append(_task("G01", "generic-crm", "failed", [], actions, str(exc)))

                try:
                    rows = crm.locator(".customer-row")
                    names = rows.all_inner_texts()
                    assert len(names) == 3
                    assert all("服务中" in row for row in names)
                    tasks.append(_task("G02", "generic-crm", "passed", [f"读取 {len(names)} 条客户记录", "每条记录均带服务中状态", "名称和负责人来自可见表格"], 1))
                except Exception as exc:
                    tasks.append(_task("G02", "generic-crm", "failed", [], 1, str(exc)))

                shadow = browser.new_page()
                shadow.goto(f"{demo.url}/shadow.html", wait_until="domcontentloaded")
                try:
                    search = shadow.get_by_label("搜索资产")
                    search.fill("E2E_ASSET")
                    assert search.input_value() == "E2E_ASSET"
                    tasks.append(_task("G03", "generic-shadow", "passed", ["通过 Shadow DOM 可访问标签找到搜索框", "输入值未依赖固定外部页面 ID"], 1))
                except Exception as exc:
                    tasks.append(_task("G03", "generic-shadow", "failed", [], 1, str(exc)))

                try:
                    shadow.get_by_role("button", name="Create story").click()
                    assert shadow.get_by_test_id("story-status").inner_text() == "Story 已创建"
                    tasks.append(_task("G04", "generic-shadow", "passed", ["通过 Shadow DOM 内部语义按钮执行创建", "状态区域报告 Story 已创建"], 1))
                except Exception as exc:
                    tasks.append(_task("G04", "generic-shadow", "failed", [], 1, str(exc)))
            finally:
                browser.close()

    ended_at = datetime.now(timezone.utc)
    passed = sum(item["status"] == "passed" for item in tasks)
    run = {
        "status": "passed" if passed == len(tasks) else "failed",
        "startedAt": started_at.isoformat(),
        "endedAt": ended_at.isoformat(),
        "durationMs": round((monotonic() - started) * 1000),
        "siteCount": 2,
        "taskCount": len(tasks),
        "passed": passed,
        "failed": len(tasks) - passed,
    }
    evidence = {"schemaVersion": "1", "run": run, "tasks": tasks}
    evidence_path = _write_regression_evidence(artifacts_root, evidence)
    if evidence_path:
        run["evidencePath"] = evidence_path
        evidence["run"] = run
        _write_regression_evidence(artifacts_root, evidence)

    payload = _base_payload()
    status_by_task = {item["taskId"]: item["status"] for item in tasks}
    for site in payload["sites"]:
        for task in site["tasks"]:
            task["status"] = status_by_task.get(task["id"], "unverified")
    payload["summary"] = {
        **payload["summary"],
        "verified": passed,
        "unverified": len(tasks) - passed,
    }
    payload["lastRun"] = run
    payload["evidence"] = tasks
    return payload
