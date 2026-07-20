from fastapi.testclient import TestClient

from gui_agent.api.server import app


client = TestClient(app)


def test_health_declares_real_engine() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "real"
    assert response.json()["engine"] == "playwright-chromium"
    assert response.json()["aiConfigStorage"] == "request-memory-only"


def test_planner_returns_422_for_unexecutable_text() -> None:
    response = client.post(
        "/api/plans/generate",
        json={
            "name": "模糊流程",
            "targetUrl": "https://example.com",
            "flow": "随便帮我测试一下",
            "role": "tester",
        },
    )
    assert response.status_code == 422
    assert "没有识别出" in response.json()["detail"]


def test_ai_endpoint_rejects_empty_key_without_echoing_it() -> None:
    response = client.post(
        "/api/ai/test",
        json={
            "settings": {
                "protocol": "responses",
                "baseUrl": "https://api.openai.com/v1",
                "model": "test-model",
                "apiKey": "",
            }
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "请填写新的 API Key"
