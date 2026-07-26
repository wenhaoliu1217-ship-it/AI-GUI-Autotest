from __future__ import annotations

import os
from pathlib import Path

import pytest

from gui_agent.domain.models import TestPlan as ExecutionPlan
from gui_agent.execution import RunnerConfig, run_plan
from gui_agent.onboarding.models import ProjectConfig
from gui_agent.onboarding.scanner import scan_project


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.getenv("GUI_PUBLIC_ACCEPTANCE") != "1", reason="invoked explicitly for ER-07 public-site acceptance"),
]


def test_cesium_official_sandcastle_scan_and_run(tmp_path: Path) -> None:
    url = "https://sandcastle.cesium.com"
    project = ProjectConfig(
        id="er07-cesium",
        name="Cesium Sandcastle",
        baseUrl=url,
        allowedHosts=["sandcastle.cesium.com"],
        onboardingLevel="L0",
    )
    scan = scan_project(project, headless=True, timeout_ms=30_000)
    plan = ExecutionPlan.model_validate({
        "name": "ER-07 Cesium official",
        "base_url": url,
        "steps": [
            {"action": "navigate", "target": "/"},
            {"action": "wait_for", "locator": {"css": "iframe"}, "description": "等待 Cesium 示例 iframe 挂载"},
        ],
        "assertions": [
            {"type": "visible", "locator": {"role": "heading", "name": "Gallery"}},
            {"type": "count_equals", "locator": {"css": "iframe"}, "count": 1},
            {"type": "url_contains", "expected": "sandcastle.cesium.com"},
        ],
    })
    result, run_dir = run_plan(plan, RunnerConfig(
        artifacts_root=tmp_path / "artifacts",
        allowed_hosts=("sandcastle.cesium.com",),
        timeout_ms=30_000,
    ))

    assert scan.app_map["pages"]
    assert "sandcastle.cesium.com" in scan.final_url
    assert "Sandcastle" in scan.title
    assert result.status.value == "passed", result.model_dump(mode="json")
    assert result.evidence_completeness >= 0.95
    assert (run_dir / "evidence" / "evidence-manifest.json").is_file()


def test_safe_public_example_site_run(tmp_path: Path) -> None:
    url = "https://example.com"
    plan = ExecutionPlan.model_validate({
        "name": "ER-07 safe public site",
        "base_url": url,
        "steps": [{"action": "navigate", "target": "/"}],
        "assertions": [
            {"type": "text_contains", "locator": {"css": "h1"}, "expected": "Example Domain"},
            {"type": "url_contains", "expected": "example.com"},
        ],
    })
    result, run_dir = run_plan(plan, RunnerConfig(
        artifacts_root=tmp_path / "artifacts",
        allowed_hosts=("example.com",),
        timeout_ms=30_000,
    ))

    assert result.status.value == "passed", result.model_dump(mode="json")
    assert result.evidence_completeness >= 0.95
    assert (run_dir / "trace.zip").is_file()
