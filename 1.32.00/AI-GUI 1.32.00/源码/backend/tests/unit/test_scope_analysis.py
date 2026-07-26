import json

from pydantic import SecretStr

from gui_agent.planning import ai_provider
from gui_agent.planning.ai_provider import AISettings, analyze_website_scope


def test_scope_analysis_uses_the_current_scan_and_structured_ai_output(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(settings, prompt, **kwargs):
        captured["prompt"] = prompt
        return {"output_text": json.dumps({
            "items": ["检查客户列表入口", "检查页面上的搜索框"],
            "summary": "依据当前企业工作台扫描结果",
        }, ensure_ascii=False)}

    monkeypatch.setattr(ai_provider, "_post", fake_post)
    settings = AISettings(
        protocol="responses",
        base_url="https://api.example.com/v1",
        model="test-model",
        api_key=SecretStr("test-key"),
    )
    report = {
        "title": "企业工作台",
        "finalUrl": "https://example.com/dashboard",
        "pageSummary": {"buttons": 2, "links": 4, "inputs": 1},
        "navigationEntries": ["客户管理", "操作记录"],
        "capabilities": ["标准 DOM"],
        "suggestedScenarios": ["检查客户管理入口"],
        "scannedPages": [{"title": "工作台", "pageType": "导航", "headings": ["客户管理"]}],
    }

    result = analyze_website_scope(settings, report)

    assert result["items"] == ["检查客户列表入口", "检查页面上的搜索框"]
    assert "客户管理" in captured["prompt"]
    assert "京东" not in captured["prompt"]
    assert "Cesium" not in captured["prompt"]
