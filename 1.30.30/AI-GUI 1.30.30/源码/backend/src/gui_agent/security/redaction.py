"""脱敏：把已解析的密钥值和敏感字段从文本中抹掉。

所有进入日志、事件、报告、截图元数据的文本都应先过 ``Redactor``。
脱敏基于运行时实际解析出的密钥值做字面替换，不依赖正则猜测，避免漏网。
"""

from __future__ import annotations

REDACTION_MASK = "***REDACTED***"


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

    def scrub(self, text: str) -> str:
        """把文本中所有已注册的敏感值替换为掩码。"""
        if not text:
            return text
        # 长值先替换，避免短值是长值子串时产生残留
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, REDACTION_MASK)
        return text

    def scrub_bytes(self, data: bytes) -> bytes:
        """对 trace 等二进制容器内的 UTF-8 密钥字面值脱敏。"""
        for secret in sorted(self._secrets, key=len, reverse=True):
            data = data.replace(secret.encode("utf-8"), REDACTION_MASK.encode("utf-8"))
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
