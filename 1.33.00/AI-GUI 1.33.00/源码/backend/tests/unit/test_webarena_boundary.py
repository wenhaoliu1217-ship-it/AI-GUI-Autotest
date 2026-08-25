from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from fastapi.testclient import TestClient

from gui_agent.api import server
from gui_agent.opensource.webarena import (
    WebArenaConfigError,
    import_webarena_task_config,
    ingest_webarena_trajectory,
    probe_webarena_sites,
    validate_webarena_self_hosted_config,
)


def _health_server(status_code: int) -> tuple[ThreadingHTTPServer, type[BaseHTTPRequestHandler]]:
    class Handler(BaseHTTPRequestHandler):
        methods: list[str] = []

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler hook
            self.__class__.methods.append(self.command)
            self.send_response(status_code)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"health")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server, Handler


def _configuration() -> dict:
    return {
        "readOnly": True,
        "allowExternalWrites": False,
        "sites": [
            {
                "id": "crm",
                "name": "CRM fixture",
                "baseUrl": "http://crm.local",
                "healthPath": "/health",
                "allowedHosts": ["crm.local", "api.crm.local"],
            },
            {
                "id": "shop",
                "name": "Shop fixture",
                "baseUrl": "http://shop.local",
            },
        ],
        "tasks": [
            {"id": "crm.search", "siteId": "crm", "intent": "Find a customer", "evaluator": "crm_readonly"},
            {"id": "shop.lookup", "siteId": "shop", "intent": "Read an order", "evaluator": "shop_readonly"},
        ],
    }


def test_self_hosted_configuration_is_validated_without_network_access() -> None:
    result = validate_webarena_self_hosted_config(_configuration())

    assert result["status"] == "config_ready"
    assert result["runtimeStatus"] == "self_hosted_sites_required"
    assert result["runtimeReady"] is False
    assert result["siteCount"] == 2
    assert result["taskCount"] == 2
    assert result["actionPolicy"] == "trajectory_evaluation_only"
    assert "apiKey" not in str(result)


def test_self_hosted_configuration_rejects_credentials_and_writes() -> None:
    unsafe = _configuration()
    unsafe["allowExternalWrites"] = True
    try:
        validate_webarena_self_hosted_config(unsafe)
    except WebArenaConfigError as exc:
        assert "external writes" in str(exc)
    else:
        raise AssertionError("write-enabled WebArena configuration must be rejected")

    credentials = _configuration()
    credentials["sites"][0]["baseUrl"] = "http://user:password@crm.local"
    try:
        validate_webarena_self_hosted_config(credentials)
    except WebArenaConfigError as exc:
        assert "credentials" in str(exc)
    else:
        raise AssertionError("credential-bearing site URL must be rejected")


def test_trajectory_is_bound_to_config_without_starting_a_browser() -> None:
    result = ingest_webarena_trajectory(
        _configuration(),
        {
            "task_id": "crm.search",
            "site": "crm",
            "trajectory": [
                {"step": 0, "action": "goto('/search')", "observation": {"url": "http://crm.local/search"}},
                {"step": 1, "action": "read", "reward": 1, "terminated": True},
            ],
            "evaluator": {"success": True, "passed": True},
        },
    )

    assert result["adapter"] == "webarena"
    assert result["configuration"]["task"]["siteId"] == "crm"
    assert result["runtimeReady"] is False
    assert result["actionPolicy"] == "trajectory_evaluation_only"


def test_webarena_boundary_api_rejects_unknown_task_and_accepts_valid_trajectory() -> None:
    client = TestClient(server.app)
    configuration = _configuration()
    configured = client.post(
        "/api/opensource/evaluation/webarena/configuration",
        json={"configuration": configuration},
    )
    assert configured.status_code == 200
    assert configured.json()["siteCount"] == 2

    missing = client.post(
        "/api/opensource/evaluation/webarena/trajectory",
        json={"configuration": configuration, "payload": {"task_id": "missing.task", "trajectory": []}},
    )
    assert missing.status_code == 422

    valid = client.post(
        "/api/opensource/evaluation/webarena/trajectory",
        json={
            "configuration": configuration,
            "payload": {"task_id": "shop.lookup", "trajectory": [{"step": 0, "terminated": True}]},
        },
    )
    assert valid.status_code == 200
    assert valid.json()["configuration"]["task"]["id"] == "shop.lookup"


def test_original_webarena_config_is_imported_without_secrets_or_evaluator_payloads() -> None:
    result = import_webarena_task_config(
        {
            "tasks": [
                {
                    "task_id": 7,
                    "sites": ["shopping", "shopping_admin"],
                    "start_url": "__SHOPPING_ADMIN__",
                    "intent": "Find the order status",
                    "require_login": True,
                    "storage_state": "private/storage_state.json",
                    "eval": {
                        "eval_types": ["string_match"],
                        "reference_answer": ["shipped"],
                        "reference_url": "http://shopping/order/7",
                        "program_html": ["<html>secret fixture</html>"],
                    },
                }
            ]
        },
        {"shopping": "http://shopping.local", "shopping_admin": "http://admin.shopping.local"},
    )

    assert result["status"] == "config_imported"
    assert result["sourceTaskCount"] == 1
    assert result["importedTaskCount"] == 1
    assert result["secretsDropped"] is True
    task = result["tasks"][0]
    assert task["id"] == "webarena.7"
    assert task["siteId"] == "shopping_admin"
    assert task["startUrlToken"] == "__SHOPPING_ADMIN__"
    assert task["requiresLogin"] is True
    assert task["evaluation"] == {
        "evalTypes": ["string_match"],
        "referenceAnswerPresent": True,
        "referenceUrlPresent": True,
        "programHtmlCount": 1,
    }
    assert "storage_state" not in str(result)
    assert "secret fixture" not in str(result)
    assert result["configuration"]["taskCount"] == 1
    assert result["runtimeReady"] is False


def test_webarena_config_import_api_is_bounded_and_read_only() -> None:
    client = TestClient(server.app)
    response = client.post(
        "/api/opensource/evaluation/webarena/import-config",
        json={
            "records": [
                {
                    "task_id": "demo",
                    "sites": ["__REDDIT__"],
                    "start_url": "__REDDIT__",
                    "intent": "Read one post",
                }
            ],
            "siteBaseUrls": {"__REDDIT__": "http://reddit.local"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configuration"]["sites"][0]["id"] == "reddit"
    assert body["tasks"][0]["actionPolicy"] == "trajectory_evaluation_only"

    unsafe = client.post(
        "/api/opensource/evaluation/webarena/import-config",
        json={
            "records": [{"task_id": "unsafe", "sites": ["__REDDIT__"], "intent": "Read"}],
            "siteBaseUrls": {"__REDDIT__": "http://user:password@reddit.local"},
        },
    )
    assert unsafe.status_code == 422


def test_self_hosted_site_probe_is_get_only_and_classifies_each_site() -> None:
    healthy_server, healthy_handler = _health_server(200)
    failing_server, failing_handler = _health_server(503)
    try:
        configuration = {
            "readOnly": True,
            "allowExternalWrites": False,
            "sites": [
                {
                    "id": "healthy",
                    "name": "Healthy fixture",
                    "baseUrl": f"http://127.0.0.1:{healthy_server.server_port}",
                    "healthPath": "/health",
                },
                {
                    "id": "failing",
                    "name": "Failing fixture",
                    "baseUrl": f"http://127.0.0.1:{failing_server.server_port}",
                    "healthPath": "/health",
                },
            ],
            "tasks": [
                {"id": "healthy.read", "siteId": "healthy", "intent": "Read"},
                {"id": "failing.read", "siteId": "failing", "intent": "Read"},
            ],
        }
        result = probe_webarena_sites(configuration, timeout_ms=1_000)

        assert result["status"] == "site_probe_complete"
        assert result["siteReadiness"] == "sites_not_ready"
        assert result["runtimeReady"] is False
        assert result["executionPolicy"] == "get_only_no_browser_start_no_external_writes"
        assert {item["id"]: item["status"] for item in result["sites"]} == {
            "healthy": "healthy",
            "failing": "http_error",
        }
        assert healthy_handler.methods == ["GET"]
        assert failing_handler.methods == ["GET"]
        api_result = TestClient(server.app).post(
            "/api/opensource/evaluation/webarena/site-health",
            json={"configuration": configuration, "timeoutMs": 1_000},
        )
        assert api_result.status_code == 200
        assert api_result.json()["readySiteCount"] == 1
    finally:
        healthy_server.shutdown()
        failing_server.shutdown()
        healthy_server.server_close()
        failing_server.server_close()
