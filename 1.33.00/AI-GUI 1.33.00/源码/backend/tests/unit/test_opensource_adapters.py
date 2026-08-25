from __future__ import annotations

from pathlib import Path

from gui_agent.opensource.adapters import (
    adapter_catalog_payload,
    normalize_agentlab_experiment,
    normalize_browser_use_agent_result,
    normalize_browsergym_trajectory,
    normalize_openadapt_checkpoint,
    normalize_playwright_mcp_observation,
    normalize_playwright_mcp_tool_result,
    normalize_playwright_cli_trace,
    normalize_stagehand_candidate_result,
    normalize_ui_tars_action_result,
    normalize_webarena_trajectory,
)


def test_playwright_mcp_observation_is_normalized_without_an_upstream_page_object() -> None:
    observation = normalize_playwright_mcp_observation({
        "structuredContent": {
            "url": "https://example.test/dashboard",
            "title": "Dashboard",
            "snapshot": '- button "登录"\n- textbox "搜索"',
            "domSummary": ["button | text=登录", "input | placeholder=搜索"],
        }
    })

    assert observation.url == "https://example.test/dashboard"
    assert observation.title == "Dashboard"
    assert 'button "登录"' in observation.accessibility_summary
    assert observation.dom_summary == ["button | text=登录", "input | placeholder=搜索"]


def test_playwright_mcp_tool_result_is_bounded_and_marks_errors() -> None:
    result = normalize_playwright_mcp_tool_result({
        "isError": True,
        "error": "拒绝执行付款动作",
        "text": "拒绝执行付款动作",
        "structuredContent": {"reason": "forbidden"},
    })

    assert result == {
        "adapter": "playwright-mcp",
        "ok": False,
        "text": "拒绝执行付款动作",
        "structuredContent": {"reason": "forbidden"},
        "error": "拒绝执行付款动作",
    }


def test_stagehand_candidate_result_is_normalized_without_executing_actions() -> None:
    result = normalize_stagehand_candidate_result({
        "data": {
            "candidates": [{
                "action": "click",
                "description": "打开客户列表",
                "selector": "[data-testid=customers]",
                "confidence": 0.91,
            }],
            "extraction": {"count": 3, "privateValue": "must remain bounded"},
        }
    })

    assert result["adapter"] == "stagehand"
    assert result["status"] == "candidate_only"
    assert result["candidates"][0]["action"] == "click"
    assert result["candidates"][0]["locator"] == "[data-testid=customers]"
    assert result["evidence"]["actionPolicy"] == "candidate_only_no_execution"
    assert result["evidence"]["hasExtraction"] is True


def test_openadapt_checkpoint_normalization_excludes_resume_tokens() -> None:
    result = normalize_openadapt_checkpoint({
        "checkpoint": {"checkpoint_id": "cp-7", "state": "paused", "current_url": "https://example.test/", "step_index": 4},
        "evidence": {"outcome": "paused", "resumeToken": "secret-token"},
        "artifacts": ["trace.json", "checkpoint.json"],
    })

    assert result["status"] == "checkpoint_ready"
    assert result["checkpoint"] == {
        "id": "cp-7", "status": "paused", "currentUrl": "https://example.test/", "stepIndex": 4
    }
    assert result["artifactNames"] == ["trace.json", "checkpoint.json"]
    assert result["actionPolicy"] == "checkpoint_only_no_execution"
    assert "resumeToken" not in result["evidence"]


def test_browser_use_agent_state_is_bounded_and_redacts_sensitive_state() -> None:
    result = normalize_browser_use_agent_result({
        "goal": "检查客户列表是否存在",
        "current_url": "https://example.test/customers",
        "status": "done",
        "api_key": "should-not-leak",
        "storage_state": {"cookies": [{"value": "should-not-leak"}]},
        "storageState": {"cookies": [{"value": "should-not-leak"}]},
        "steps": [
            {
                "step": 0,
                "action": {"type": "observe", "headers": {"authorization": "should-not-leak"}},
                "url": "https://example.test/customers",
                "result": {"visible": True},
                "done": False,
            },
            {
                "step": 1,
                "model_output": {"action": {"type": "click", "target": "客户列表"}},
                "result": {"rows": 3},
                "done": True,
            },
        ],
        "final_result": {"success": True, "summary": "发现 3 条记录"},
    })

    assert result["adapter"] == "browser-use"
    assert result["status"] == "agent_state_ready"
    assert result["summary"] == {"stepCount": 2, "done": True, "success": True}
    assert result["steps"][1]["action"] == {"type": "click", "target": "客户列表"}
    assert result["actionPolicy"] == "agent_state_preview_only_no_execution"
    assert "should-not-leak" not in str(result)
    assert "[REDACTED]" in str(result)


def test_ui_tars_action_candidates_normalize_coordinates_without_executing_or_retaining_text() -> None:
    result = normalize_ui_tars_action_result({
        "model_type": "qwen2vl",
        "coordinate_scale": 1000,
        "response": "Thought: 找到客户列表按钮\nAction: click(point='<point>200 300</point>')\n\ntype(content='secret-password')",
    })

    assert result["adapter"] == "ui-tars"
    assert result["status"] == "candidate_actions_ready"
    assert result["summary"] == {
        "actionCount": 2,
        "supportedCount": 2,
        "coordinateActionCount": 1,
        "invalidCoordinateCount": 0,
        "parseErrorCount": 0,
    }
    assert result["actions"][0]["actionType"] == "click"
    assert result["actions"][0]["coordinates"]["start"] == [0.2, 0.3, 0.2, 0.3]
    assert result["actions"][1]["inputs"]["contentRedacted"] is True
    assert "secret-password" not in str(result)
    assert result["actionPolicy"] == "visual_candidate_only_no_execution"


def test_playwright_cli_trace_is_imported_as_bounded_evidence_without_replaying_or_leaking_inputs() -> None:
    result = normalize_playwright_cli_trace({
        "commands": [
            {"kind": "navigate", "command": "browser_navigate https://example.test/customers?token=hidden"},
            {"kind": "fill", "command": "locator('#search').fill('secret-password')", "value": "secret-password"},
            {"kind": "unsafe", "command": "browser_run_code_unsafe(() => page.evaluate('document.title'))"},
        ],
    })

    assert result["adapter"] == "playwright-cli"
    assert result["status"] == "trace_ready"
    assert result["summary"] == {
        "commandCount": 3,
        "navigationCount": 1,
        "writeLikeCount": 1,
        "unsafeCount": 1,
    }
    assert result["commands"][1]["inputRedacted"] is True
    assert result["commands"][2]["unsafe"] is True
    assert result["commands"][0]["url"] == "https://example.test/customers"
    assert "secret-password" not in str(result)
    assert "token=hidden" not in str(result)
    assert result["actionPolicy"] == "trace_import_only_no_execution"


def test_browsergym_trajectory_normalization_keeps_evaluation_facts_without_screenshots() -> None:
    result = normalize_browsergym_trajectory({
        "taskId": "crm.search",
        "instruction": "查找客户",
        "environment": {"taskName": "crm.search", "headless": True, "maxSteps": 8},
        "steps": [
            {
                "step": 0,
                "obs": {
                    "url": "https://example.test/",
                    "screenshot": b"huge-image",
                    "axtree_object": {"role": "button"},
                    "open_pages_urls": ["https://example.test/"],
                },
                "reward": 0,
                "terminated": False,
                "truncated": False,
            },
            {
                "step": 1,
                "action": "click('search')",
                "obs": {"url": "https://example.test/search", "last_action": "click"},
                "reward": 1,
                "raw_reward": 1,
                "terminated": True,
                "truncated": False,
            },
        ],
        "summary": {"cum_reward": 1, "success": True},
        "evaluation": {"score": 1, "passed": True, "privateTrace": "omit"},
    })

    assert result["adapter"] == "browsergym"
    assert result["status"] == "episode_ready"
    assert result["task"]["id"] == "crm.search"
    assert result["summary"] == {
        "stepCount": 2,
        "totalReward": 1,
        "totalRawReward": 1,
        "success": True,
        "terminated": True,
        "truncated": False,
    }
    assert result["steps"][0]["observation"]["hasScreenshot"] is True
    assert "screenshot" not in result["steps"][0]["observation"]
    assert result["evaluation"] == {"passed": True, "score": 1}
    assert result["actionPolicy"] == "trajectory_evaluation_only"


def test_agentlab_experiment_normalization_maps_exp_args_summary_and_step_info() -> None:
    result = normalize_agentlab_experiment({
        "exp_args": {
            "env_args": {"task_name": "webarena.0", "task_seed": 7, "max_steps": 5, "headless": True},
            "agent_args": {"agent_name": "FixtureAgent", "api_key": "must not leak"},
        },
        "summary_info": {
            "n_steps": 2,
            "cum_reward": 1,
            "terminated": True,
            "stats.cum_tokens": 42,
        },
        "steps_info": [
            {"step": 0, "obs": {"url": "https://example.test/"}, "reward": 0},
            {"step": 1, "action": "click('done')", "reward": 1, "terminated": True},
        ],
        "metrics": {"success_rate": 1, "private": {"token": "omit"}},
        "artifacts": ["summary_info.json", "step_1.pkl.gz"],
    })

    assert result["adapter"] == "agentlab"
    assert result["status"] == "experiment_ready"
    assert result["runStatus"] == "done"
    assert result["agent"]["name"] == "FixtureAgent"
    assert result["task"]["id"] == "webarena.0"
    assert result["task"]["seed"] == 7
    assert result["trajectorySummary"]["stepCount"] == 2
    assert result["trajectorySummary"]["totalReward"] == 1
    assert result["metrics"]["stats.cum_tokens"] == 42
    assert result["artifactNames"] == ["summary_info.json", "step_1.pkl.gz"]
    assert result["actionPolicy"] == "experiment_metadata_only"
    assert "api_key" not in str(result)


def test_agentlab_normalization_exposes_bounded_quality_metrics() -> None:
    result = normalize_agentlab_experiment({
        "exp_args": {"env_args": {"task_name": "quality.task"}},
        "summary_info": {"n_steps": 4, "cum_reward": 0.75, "cum_raw_reward": 1, "terminated": True, "truncated": False},
        "steps_info": [],
        "metrics": {"success_rate": 0.75, "stats.latency_ms": 120},
        "status": "done",
    })

    assert result["qualityMetrics"] == {
        "qualityStatus": "passed",
        "runStatus": "done",
        "terminated": True,
        "truncated": False,
        "stepCount": 4,
        "cumulativeReward": 0.75,
        "cumulativeRawReward": 1,
        "metricCount": 2,
        "errorPresent": False,
    }


def test_webarena_trajectory_normalization_is_read_only_and_bounded() -> None:
    result = normalize_webarena_trajectory({
        "task_id": "crm.search",
        "intent": "Find customer",
        "site": "crm.local",
        "trajectory": [
            {"step": 0, "action": "goto('/search')", "observation": {"url": "http://crm.local/search", "dom": "large"}, "reward": 0},
            {"step": 1, "action": "click('#customer')", "observation": {"url": "http://crm.local/customer/1", "screenshot": "secret-image"}, "reward": 1, "terminated": True},
        ],
        "summary": {"totalReward": 1, "success": True, "terminated": True},
        "evaluator": {"success": True, "passed": True, "privateTrace": "omit"},
    })

    assert result["adapter"] == "webarena"
    assert result["status"] == "trajectory_ready"
    assert result["task"]["id"] == "crm.search"
    assert result["summary"]["success"] is True
    assert result["evaluation"] == {"success": True, "passed": True}
    assert "secret-image" not in str(result)
    assert result["actionPolicy"] == "trajectory_evaluation_only"


def test_playwright_mcp_probe_keeps_contract_ready_separate_from_runtime_ready(tmp_path: Path) -> None:
    project = tmp_path / "playwright-mcp"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "cli.js").write_text("// fixture", encoding="utf-8")
    (project / "package.json").write_text("{}", encoding="utf-8")

    payload = adapter_catalog_payload(tmp_path, {"node": {"available": True}})
    adapter = payload["adapters"][0]

    assert payload["summary"] == {"contractReady": 3, "runtimeReady": 0, "blocked": 3}
    assert adapter["contractStatus"] == "contract_ready"
    assert adapter["runtimeReady"] is False
    assert adapter["reasons"]


def test_evaluation_catalog_includes_new_read_only_adapters() -> None:
    payload = adapter_catalog_payload(None, {"node": {"available": False}})
    contracts = {item["projectId"]: item for item in payload["evaluationContracts"]}

    assert {"browsergym", "agentlab", "webarena", "browser-use", "ui-tars", "playwright-cli"} <= set(contracts)
    for project_id in ("browser-use", "ui-tars", "playwright-cli"):
        assert contracts[project_id]["contractStatus"] == "contract_ready"
        assert contracts[project_id]["runtimeReady"] is False
        assert "only" in contracts[project_id]["safety"].lower()


def test_playwright_mcp_probe_records_runtime_handshake_and_unsafe_tools(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "playwright-mcp"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "cli.js").write_text("// fixture", encoding="utf-8")
    (project / "package.json").write_text("{}", encoding="utf-8")
    node_modules = tmp_path / "node_modules"
    (node_modules / "playwright-core").mkdir(parents=True)

    monkeypatch.setattr("gui_agent.opensource.adapters.shutil.which", lambda command: "node.exe" if command == "node" else None)
    monkeypatch.setattr("gui_agent.opensource.adapters._find_playwright_core", lambda _root: node_modules)
    monkeypatch.setattr("gui_agent.opensource.adapters._mcp_handshake", lambda *_args, **_kwargs: {
        "serverInfo": {"name": "Playwright", "version": "fixture"},
        "protocolVersion": "2025-06-18",
        "toolCount": 2,
        "toolNames": ["browser_snapshot", "browser_run_code_unsafe"],
        "probe": "initialize + tools/list",
    })

    payload = adapter_catalog_payload(tmp_path, {"node": {"available": True}, "python": {"available": True}})
    adapter = payload["adapters"][0]

    assert adapter["runtimeReady"] is True
    assert adapter["runtimeEvidence"]["toolCount"] == 2
    assert adapter["runtimeEvidence"]["unsafeTools"] == ["browser_run_code_unsafe"]
    assert "必须过滤" in adapter["safety"]
