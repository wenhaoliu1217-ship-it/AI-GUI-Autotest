from datetime import datetime, timezone

from gui_agent.artifacts.report import render_html, render_markdown
from gui_agent.domain.results import EvidenceEvent, Finding, RunResult, Status


def test_reports_include_structured_finding_evidence_timeline() -> None:
    now = datetime.now(timezone.utc)
    result = RunResult(
        run_id="timeline-report",
        plan_name="证据时间线测试",
        base_url_summary="https://example.com",
        status=Status.ISSUES_FOUND,
        started_at=now,
        ended_at=now,
        findings=[Finding(
            id="finding-1",
            title="提交按钮被遮挡",
            category="element_obscured",
            severity="High",
            confidence="high",
            actual_result="交互控件中心点被遮挡",
            expected_result="提交按钮可点击",
            facts=["覆盖元素：div | text=遮挡层"],
            evidence=["screenshots/after.png"],
            evidence_timeline=[EvidenceEvent(
                phase="after_action",
                timestamp=now,
                screenshot="screenshots/after.png",
                facts=["页面：https://example.com/form"],
            )],
        )],
        confirmation_history=[{
            "id": "confirmation-1", "step_index": 2, "action": "click", "target": "提交按钮",
            "rule": "提交订单", "requested_at": now.isoformat(), "decision": "approved",
            "actor": "tester", "decided_at": now.isoformat(),
        }],
        runner_isolation={
            "mode": "spawn_process", "windows_job_assigned": True,
            "memory_limit_mb": 2048, "forced_termination": False,
        },
    )

    html = render_html(result)
    markdown = render_markdown(result)

    assert "提交按钮被遮挡" in html
    assert "screenshots/after.png" in html
    assert "页面：https://example.com/form" in html
    assert "## 结构化问题（待人工审核）" in markdown
    assert "after_action" in markdown
    assert "危险动作确认记录" in html
    assert "tester" in html
    assert "## 危险动作确认记录" in markdown
    assert "Runner 隔离：spawn_process" in html
    assert "Windows Job：已绑定" in markdown


def test_reports_include_commerce_zero_residual_gate_without_raw_id() -> None:
    now = datetime.now(timezone.utc)
    raw_identifier = "REAL_ORDER_123456789"
    result = RunResult(
        run_id="commerce-report",
        plan_name="电商资源清理",
        base_url_summary="https://example.com",
        status=Status.ERROR,
        started_at=now,
        ended_at=now,
        completion_reason="commerce_cleanup_required",
        commerce_summary={
            "environment": "isolated_transaction",
            "policyEvaluations": [{"action": "submit_order", "allowed": True}],
            "ledgerEntries": [{}],
            "pendingResources": [{
                "reference": {"kind": "orderId", "sha256": "a" * 64, "suffix": "6789"},
                "status": "created",
                "cleanupAction": "cancel sandbox order",
            }],
            "zeroResidual": False,
        },
    )

    html = render_html(result)
    markdown = render_markdown(result)

    assert "电商安全与资源清理" in html
    assert "未通过，必须人工处置" in html
    assert "零残留：**未通过**" in markdown
    assert "aaaaaaaaaaaa..." in markdown
    assert raw_identifier not in html
    assert raw_identifier not in markdown


def test_reports_include_optional_open_source_observation_evidence() -> None:
    now = datetime.now(timezone.utc)
    result = RunResult(
        run_id="open-source-report",
        plan_name="开源观察证据",
        base_url_summary="https://example.test",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
        open_source_observation={
            "provider": "playwright-mcp",
            "status": "ready",
            "evidencePath": "observations/open-source-observation.json",
            "url": "https://example.test/dashboard",
            "title": "客户工作台",
            "toolCount": 23,
            "unsafeTools": ["browser_evaluate"],
            "actionPolicy": "read_only_navigate_snapshot_only",
        },
    )

    html = render_html(result)
    markdown = render_markdown(result)

    assert "开源观察证据" in html
    assert "客户工作台" in html
    assert "observations/open-source-observation.json" in markdown
    assert "browser_evaluate" in markdown
    assert "read_only_navigate_snapshot_only" in markdown


def test_reports_include_isolated_upstream_evaluation_evidence() -> None:
    now = datetime.now(timezone.utc)
    result = RunResult(
        run_id="upstream-evaluation-report",
        plan_name="upstream evaluation",
        base_url_summary="http://127.0.0.1:8765",
        status=Status.PASSED,
        started_at=now,
        ended_at=now,
        open_source_runtime_evaluation={
            "provider": "browsergym",
            "status": "ready",
            "upstreamStatus": "episode_ready",
            "upstreamRuntimeStarted": True,
            "actionPolicy": "local_fixture_only",
            "summary": {"success": True, "totalReward": 1.0},
            "qualityMetrics": {"qualityStatus": "passed", "metricCount": 2},
            "metrics": {"success_rate": 1.0},
            "evidencePath": "evaluations/open-source-runtime.json",
        },
    )

    html = render_html(result)
    markdown = render_markdown(result)

    assert "upstream evaluation" in html
    assert "local_fixture_only" in html
    assert "qualityStatus" in html
    assert "evaluations/open-source-runtime.json" in markdown
    assert "episode_ready" in markdown
