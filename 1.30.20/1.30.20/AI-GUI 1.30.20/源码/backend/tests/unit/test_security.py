import pytest

from gui_agent.security.policy import DomainPolicy, SecurityError, guard_playwright_route, guard_playwright_websocket, resolve_env_placeholder, resolve_secret
from gui_agent.security.redaction import REDACTION_MASK, Redactor


def test_domain_policy_rejects_cross_domain() -> None:
    policy = DomainPolicy("https://demo.example.com", resolver=lambda _: {"93.184.216.34"})
    with pytest.raises(SecurityError):
        policy.check_url("https://other.example.com/admin")


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "100.64.1.2", "169.254.10.20", "::1", "fe80::1", "fd00::1"])
def test_domain_policy_blocks_restricted_networks_by_default(address: str) -> None:
    base_url = f"http://[{address}]" if ":" in address else f"http://{address}"
    policy = DomainPolicy(base_url)

    with pytest.raises(SecurityError, match="默认禁止访问"):
        policy.check_url(f"{base_url}/health")


def test_domain_policy_allows_explicit_controlled_private_network() -> None:
    policy = DomainPolicy("http://127.0.0.1", allow_private_network=True)

    policy.check_url("http://127.0.0.1:8080/health")


def test_domain_policy_allows_enterprise_proxy_benchmark_mapping() -> None:
    policy = DomainPolicy("https://example.com", resolver=lambda _: {"198.18.0.36"})

    policy.check_url("https://example.com")


def test_domain_policy_rejects_dns_scope_rebinding_even_with_private_exception() -> None:
    answers = iter([{"93.184.216.34"}, {"127.0.0.1"}])
    policy = DomainPolicy(
        "https://demo.example.com",
        allow_private_network=True,
        resolver=lambda _: next(answers),
    )
    policy.check_url("https://demo.example.com")

    with pytest.raises(SecurityError, match="DNS 重绑定"):
        policy.check_url("https://demo.example.com/dashboard")


def test_domain_policy_rejects_non_http_and_credentials() -> None:
    policy = DomainPolicy("https://demo.example.com", resolver=lambda _: {"93.184.216.34"})

    with pytest.raises(SecurityError, match="http/https"):
        policy.check_url("file:///etc/passwd")
    with pytest.raises(SecurityError, match="用户名或密码"):
        policy.check_url("https://user:password@demo.example.com")


def test_browser_route_blocks_private_subresource_and_records_reason() -> None:
    class Request:
        url = "http://127.0.0.1/internal"
        resource_type = "fetch"

        @staticmethod
        def is_navigation_request() -> bool:
            return False

    class Route:
        request = Request()
        aborted = False

        def continue_(self) -> None:
            raise AssertionError("受限地址不得继续请求")

        def abort(self, reason: str) -> None:
            assert reason == "blockedbyclient"
            self.aborted = True

    route = Route()
    rejections: list[tuple[str, str, str]] = []
    policy = DomainPolicy("https://demo.example.com", resolver=lambda host: {
        "93.184.216.34" if host == "demo.example.com" else "127.0.0.1"
    })

    guard_playwright_route(route, policy, lambda *items: rejections.append(items))

    assert route.aborted is True
    assert "默认禁止访问" in rejections[0][0]
    assert policy.consume_rejection() == rejections[0][0]


def test_browser_websocket_requires_allowed_host_and_network_scope() -> None:
    class WebSocketRoute:
        url = "ws://127.0.0.1/internal"
        closed = False

        def connect_to_server(self) -> None:
            raise AssertionError("受限 WebSocket 不得连接")

        def close(self, *, code: int, reason: str) -> None:
            assert code == 1008
            assert reason == "blocked by network policy"
            self.closed = True

    route = WebSocketRoute()
    rejections: list[tuple[str, str, str]] = []
    policy = DomainPolicy("https://demo.example.com", resolver=lambda host: {
        "93.184.216.34" if host == "demo.example.com" else "127.0.0.1"
    })

    guard_playwright_websocket(route, policy, lambda *items: rejections.append(items))

    assert route.closed is True
    assert rejections[0][2] == "websocket"
    assert "白名单" in rejections[0][0]


def test_redactor_scrubs_nested_values() -> None:
    redactor = Redactor()
    redactor.register("secret-value")
    result = redactor.scrub_mapping(
        {"message": "token=secret-value", "nested": ["secret-value", {"x": "secret-value"}]}
    )
    assert result == {
        "message": f"token={REDACTION_MASK}",
        "nested": [REDACTION_MASK, {"x": REDACTION_MASK}],
    }
    assert b"secret-value" not in redactor.scrub_bytes(b"token=secret-value")


def test_environment_values_and_secret_aliases_resolve_per_run(monkeypatch) -> None:
    monkeypatch.setenv("QA_LOGIN_PASSWORD", "runtime-only-secret")
    redactor = Redactor()

    assert resolve_env_placeholder("${TEST_BASE_URL}", {"TEST_BASE_URL": "https://qa.example.com"}) == "https://qa.example.com"
    assert resolve_secret("LOGIN_PASSWORD", redactor, {"LOGIN_PASSWORD": "QA_LOGIN_PASSWORD"}) == "runtime-only-secret"
    assert redactor.scrub("password=runtime-only-secret") == f"password={REDACTION_MASK}"


def test_redactor_scrubs_unregistered_token_and_cloud_credential_shapes() -> None:
    redactor = Redactor()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjZXNpdW0tdGVzdCJ9.signature12345"
    payload = {
        "authorization": f"Bearer {jwt}",
        "url": "https://example.test/blob?sv=1&sig=secret-signature&se=tomorrow",
        "client_secret": "must-not-leak",
        "note": f"token={jwt}",
        "input_tokens": 42,
    }

    scrubbed = redactor.scrub_mapping(payload)
    serialized = str(scrubbed)

    assert jwt not in serialized and "must-not-leak" not in serialized and "secret-signature" not in serialized
    assert scrubbed["authorization"] == REDACTION_MASK
    assert scrubbed["input_tokens"] == 42
    assert b"signature12345" not in redactor.scrub_bytes(jwt.encode())
