"""脱敏：把已解析的密钥值和敏感字段从文本中抹掉。

所有进入日志、事件、报告、截图元数据的文本都应先过 ``Redactor``。
脱敏基于运行时实际解析出的密钥值做字面替换，不依赖正则猜测，避免漏网。
"""

from __future__ import annotations

import hashlib
import os
import re
from urllib.parse import urlsplit

REDACTION_MASK = "***REDACTED***"
_PATH_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])(?:\d{6,}|[A-Fa-f0-9]{16,}|[A-Za-z0-9_-]{32,})(?![A-Za-z0-9])")
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    re.compile(r"(?<!\d)\d{12,19}(?!\d)"),
    re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"),
)
_SENSITIVE_BYTE_PATTERNS = tuple(
    re.compile(pattern.pattern.encode("ascii")) for pattern in _SENSITIVE_TEXT_PATTERNS
)


def summarize_request_url(url: str) -> str:
    """Return a query-free, credential-free request URL plus a correlation hash."""
    digest = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.hostname:
            return f"<non-http-url> [urlSha256={digest}]"
        host = parts.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        default_port = 443 if parts.scheme in {"https", "wss"} else 80
        port = f":{parts.port}" if parts.port and parts.port != default_port else ""
        path = _PATH_IDENTIFIER.sub("{id}", parts.path or "/")[:400]
        return f"{parts.scheme}://{host}{port}{path} [urlSha256={digest}]"
    except (TypeError, ValueError):
        return f"<invalid-url> [urlSha256={digest}]"


class Redactor:
    """按注册的敏感值做字面替换。

    用法：把 ``value_from_secret`` 解析出的真实值注册进来，之后所有输出文本
    调用 :meth:`scrub` 即可确保明文不外泄。
    """

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def register(self, value: str | None) -> None:
        """注册一个需要脱敏的值。空值和过短值忽略，避免误伤正常文本。"""
        if value and len(value) >= 3:
            self._secrets.add(value)

    def register_environment_refs(self, refs: dict[str, str]) -> None:
        """Register available runtime secrets without making missing optional refs fatal."""
        for env_name in refs.values():
            self.register(os.environ.get(env_name))

    def scrub(self, text: str) -> str:
        """把文本中所有已注册的敏感值替换为掩码。"""
        if not text:
            return text
        # 长值先替换，避免短值是长值子串时产生残留
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, REDACTION_MASK)
        for pattern in _SENSITIVE_TEXT_PATTERNS:
            text = pattern.sub(REDACTION_MASK, text)
        return text

    def scrub_bytes(self, data: bytes) -> bytes:
        """对 trace 等二进制容器内的 UTF-8 密钥字面值脱敏。"""
        for secret in sorted(self._secrets, key=len, reverse=True):
            data = data.replace(secret.encode("utf-8"), REDACTION_MASK.encode("utf-8"))
        for pattern in _SENSITIVE_BYTE_PATTERNS:
            data = pattern.sub(REDACTION_MASK.encode("ascii"), data)
        return data

    def scrub_mapping(self, data: dict) -> dict:
        """对字典的字符串值递归脱敏，用于结构化事件。"""
        result: dict = {}
        for key, val in data.items():
            if isinstance(val, str):
                result[key] = self.scrub(val)
            elif isinstance(val, dict):
                result[key] = self.scrub_mapping(val)
            elif isinstance(val, list):
                result[key] = [
                    self.scrub(v) if isinstance(v, str)
                    else self.scrub_mapping(v) if isinstance(v, dict)
                    else v
                    for v in val
                ]
            else:
                result[key] = val
        return result
