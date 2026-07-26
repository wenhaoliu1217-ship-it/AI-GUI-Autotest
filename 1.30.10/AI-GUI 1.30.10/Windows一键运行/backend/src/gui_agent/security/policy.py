"""安全策略：域名白名单与密钥解析。

- 域名白名单：执行前校验 base_url 与 navigate 目标是否越出允许范围，
  默认拒绝跨域跳转。
- 密钥解析：把 ``value_from_secret`` 引用的环境变量名解析成真实值，
  解析结果同时注册进 Redactor，保证不外泄。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from .redaction import Redactor


_RESTRICTED_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


class SecurityError(Exception):
    """安全策略拒绝时抛出。"""


class MissingSecretError(SecurityError):
    """引用的密钥环境变量不存在。"""


def resolve_env_placeholder(value: str, variables: dict[str, str] | None = None) -> str:
    """把 ``${ENV_VAR}`` 占位解析为环境变量值，用于 base_url 等非敏感配置。"""
    if value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
        resolved = (variables or {}).get(env_name)
        if resolved is None:
            resolved = os.environ.get(env_name)
        if resolved is None:
            raise MissingSecretError(f"环境变量未设置：{env_name}")
        return resolved
    return value


def resolve_secret(env_name: str, redactor: Redactor, secret_refs: dict[str, str] | None = None) -> str:
    """解析敏感值引用，并登记到 redactor 以便后续脱敏。"""
    system_name = (secret_refs or {}).get(env_name, env_name)
    value = os.environ.get(system_name)
    if value is None:
        raise MissingSecretError(f"密钥环境变量未设置：{system_name}")
    redactor.register(value)
    return value


class DomainPolicy:
    """域名白名单和目标网络边界。"""

    def __init__(
        self,
        base_url: str,
        allowed_hosts: list[str] | None = None,
        *,
        allow_private_network: bool = False,
        resolver: Callable[[str], set[str]] | None = None,
    ) -> None:
        self._base_host = self._normalize_host(urlparse(base_url).hostname or "")
        self._allowed: set[str] = {self._base_host}
        if allowed_hosts:
            self._allowed.update(self._normalize_host(h) for h in allowed_hosts if h.strip())
        self._allow_private_network = allow_private_network
        self._resolver = resolver or self._resolve
        self._host_scopes: dict[str, str] = {}
        self._last_rejection: str | None = None

    def check_url(self, url: str, *, require_allowed_host: bool = True) -> None:
        """校验协议、主机白名单、地址范围和 DNS 范围切换。"""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise SecurityError("只允许访问 http/https 地址")
        if parsed.username or parsed.password:
            raise SecurityError("目标 URL 不允许包含用户名或密码")
        host = self._normalize_host(parsed.hostname or "")
        if not host:
            raise SecurityError("目标 URL 缺少有效主机")
        if require_allowed_host and host not in self._allowed:
            raise SecurityError(
                f"目标主机不在白名单：{host}（允许：{sorted(self._allowed)}）"
            )
        try:
            addresses = {ipaddress.ip_address(value) for value in self._resolver(host)}
        except (OSError, ValueError) as exc:
            raise SecurityError(f"无法安全解析目标主机：{host}") from exc
        if not addresses:
            raise SecurityError(f"无法安全解析目标主机：{host}")
        scopes = {"restricted" if self._is_restricted_address(address) else "public" for address in addresses}
        if len(scopes) != 1:
            raise SecurityError(f"目标主机同时解析到公网和受限网络地址，已拒绝：{host}")
        scope = next(iter(scopes))
        previous_scope = self._host_scopes.get(host)
        if previous_scope is not None and previous_scope != scope:
            raise SecurityError(f"目标主机解析范围发生变化，疑似 DNS 重绑定：{host}")
        self._host_scopes[host] = scope
        if scope == "restricted" and not self._allow_private_network:
            values = ", ".join(sorted(str(address) for address in addresses))
            raise SecurityError(f"默认禁止访问私网、回环、链路本地或保留地址：{host}（{values}）")

    def remember_rejection(self, message: str) -> None:
        self._last_rejection = message

    def clear_rejection(self) -> None:
        self._last_rejection = None

    def consume_rejection(self) -> str | None:
        message = self._last_rejection
        self._last_rejection = None
        return message

    @staticmethod
    def _normalize_host(host: str) -> str:
        return host.strip().rstrip(".").lower()

    @staticmethod
    def _is_restricted_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return DomainPolicy._is_restricted_address(address.ipv4_mapped)
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            return True
        return any(address.version == network.version and address in network for network in _RESTRICTED_NETWORKS)

    @staticmethod
    def _resolve(host: str) -> set[str]:
        try:
            return {str(ipaddress.ip_address(host))}
        except ValueError:
            return {
                item[4][0]
                for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            }


def guard_playwright_route(route, policy: DomainPolicy, on_rejection=None) -> None:
    """在浏览器实际发出请求前执行网络策略。"""
    parsed = urlparse(route.request.url)
    if parsed.scheme not in {"http", "https"}:
        route.continue_()
        return
    try:
        policy.check_url(
            route.request.url,
            require_allowed_host=route.request.is_navigation_request(),
        )
        route.continue_()
    except SecurityError as exc:
        message = str(exc)
        policy.remember_rejection(message)
        if on_rejection is not None:
            on_rejection(message, route.request.url, route.request.resource_type)
        route.abort("blockedbyclient")


def guard_playwright_websocket(web_socket_route, policy: DomainPolicy, on_rejection=None) -> None:
    """Apply the same DNS/private-network boundary before a WebSocket connects."""
    parsed = urlparse(web_socket_route.url)
    if parsed.scheme not in {"ws", "wss"}:
        web_socket_route.close(code=1008, reason="unsupported protocol")
        return
    checked_url = parsed._replace(scheme="https" if parsed.scheme == "wss" else "http").geturl()
    try:
        policy.check_url(checked_url, require_allowed_host=True)
        web_socket_route.connect_to_server()
    except SecurityError as exc:
        message = str(exc)
        policy.remember_rejection(message)
        if on_rejection is not None:
            on_rejection(message, web_socket_route.url, "websocket")
        web_socket_route.close(code=1008, reason="blocked by network policy")
