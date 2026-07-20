"""安全策略：域名白名单与密钥解析。

- 域名白名单：执行前校验 base_url 与 navigate 目标是否越出允许范围，
  默认拒绝跨域跳转。
- 密钥解析：把 ``value_from_secret`` 引用的环境变量名解析成真实值，
  解析结果同时注册进 Redactor，保证不外泄。
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from .redaction import Redactor


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
    """域名白名单。allowed_hosts 为空表示只允许 base_url 所在主机。"""

    def __init__(self, base_url: str, allowed_hosts: list[str] | None = None) -> None:
        self._base_host = urlparse(base_url).hostname or ""
        self._allowed: set[str] = {self._base_host}
        if allowed_hosts:
            self._allowed.update(h.strip() for h in allowed_hosts if h.strip())

    def check_url(self, url: str) -> None:
        """校验绝对 URL 的主机是否在白名单内。相对路径由调用方拼接后再校验。"""
        host = urlparse(url).hostname
        if host and host not in self._allowed:
            raise SecurityError(
                f"目标主机不在白名单：{host}（允许：{sorted(self._allowed)}）"
            )
