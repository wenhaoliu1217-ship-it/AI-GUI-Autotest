"""Product-owned, deterministic evaluation fixtures for open-source contracts.

These fixtures intentionally model the persisted shapes emitted by BrowserGym
and AgentLab.  They validate the Jingcai OPC boundary, report rendering, and
safety policy without importing or starting either upstream runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from .adapters import (
    normalize_agentlab_experiment,
    normalize_browser_use_agent_result,
    normalize_browsergym_trajectory,
    normalize_playwright_cli_trace,
    normalize_ui_tars_action_result,
    normalize_webarena_trajectory,
)


EvaluationProvider = Literal["browsergym", "agentlab", "webarena", "browser-use", "ui-tars", "playwright-cli"]
_FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "benchmarks" / "opensource" / "evaluation-fixtures"
_FIXTURES: dict[EvaluationProvider, tuple[str, str]] = {
    "browsergym": ("browsergym-contract-fixture.json", "BrowserGym 任务轨迹契约夹具"),
    "agentlab": ("agentlab-contract-fixture.json", "AgentLab 实验轨迹契约夹具"),
    "webarena": ("webarena-contract-fixture.json", "WebArena contract fixture"),
    "browser-use": ("browser-use-agent-fixture.json", "Browser-use agent state fixture"),
    "ui-tars": ("ui-tars-action-fixture.json", "UI-TARS visual action fixture"),
    "playwright-cli": ("playwright-cli-trace-fixture.json", "Playwright CLI trace fixture"),
}


def evaluation_fixture_catalog_payload() -> list[dict[str, Any]]:
    """Return fixture availability without probing or starting upstream code."""

    return [
        {
            "provider": provider,
            "name": name,
            "fixtureId": f"{provider}-contract-fixture",
            "available": (_FIXTURE_ROOT / filename).is_file(),
            "source": "product_owned_deterministic_fixture",
            "upstreamRuntimeRequired": False,
            "actionPolicy": "contract_validation_only",
        }
        for provider, (filename, name) in _FIXTURES.items()
    ]


def run_evaluation_fixture(provider: EvaluationProvider) -> dict[str, Any]:
    """Load and normalize one deterministic fixture; never run an upstream process."""

    filename, _ = _FIXTURES[provider]
    fixture_path = _FIXTURE_ROOT / filename
    if not fixture_path.is_file():
        raise FileNotFoundError(f"评测契约夹具不存在：{filename}")
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if provider == "browsergym":
        normalized = normalize_browsergym_trajectory(payload)
    elif provider == "agentlab":
        normalized = normalize_agentlab_experiment(payload)
    elif provider == "browser-use":
        normalized = normalize_browser_use_agent_result(payload)
    elif provider == "ui-tars":
        normalized = normalize_ui_tars_action_result(payload)
    elif provider == "playwright-cli":
        normalized = normalize_playwright_cli_trace(payload)
    else:
        normalized = normalize_webarena_trajectory(payload)
    normalized["fixture"] = {
        "id": f"{provider}-contract-fixture",
        "source": "product_owned_deterministic_fixture",
        "upstreamRuntimeStarted": False,
        "actionPolicy": "contract_validation_only",
    }
    return normalized
