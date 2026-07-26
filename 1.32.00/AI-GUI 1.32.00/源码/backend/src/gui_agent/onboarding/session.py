"""使用 Windows DPAPI 保存 Playwright storageState，不落盘明文。"""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from datetime import datetime, timezone
from urllib.parse import urlparse

from .models import ProjectConfig, SessionMetadata, utc_now


MAX_STATE_BYTES = 2 * 1024 * 1024


class SessionStateError(ValueError):
    """登录态格式或安全边界不合法。"""


def validate_storage_state(project: ProjectConfig, state: dict) -> SessionMetadata:
    if not isinstance(state, dict) or not isinstance(state.get("cookies", []), list) or not isinstance(state.get("origins", []), list):
        raise SessionStateError("storageState 必须包含 cookies 和 origins 数组")
    encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_STATE_BYTES:
        raise SessionStateError("storageState 超过 2 MB 安全上限")
    cookies = state.get("cookies", [])
    origins = state.get("origins", [])
    if len(cookies) > 500 or len(origins) > 100:
        raise SessionStateError("storageState 中的 Cookie 或 Origin 数量超过安全上限")

    allowed = {host.lower() for host in project.allowed_hosts}
    domains: set[str] = set()
    expirations: list[float] = []
    session_cookie_count = 0
    expired_cookie_count = 0
    now = datetime.now(timezone.utc).timestamp()
    for cookie in cookies:
        if not isinstance(cookie, dict) or not cookie.get("name") or not cookie.get("domain"):
            raise SessionStateError("storageState 包含无效 Cookie")
        domain = str(cookie["domain"]).lstrip(".").lower()
        if domain not in allowed and not any(host.endswith(f".{domain}") for host in allowed):
            raise SessionStateError(f"Cookie 域名不在项目允许列表：{domain}")
        domains.add(domain)
        expires = float(cookie.get("expires", -1) or -1)
        if expires > 0:
            expirations.append(expires)
            if expires <= now:
                expired_cookie_count += 1
        else:
            session_cookie_count += 1

    for origin in origins:
        if not isinstance(origin, dict) or not origin.get("origin"):
            raise SessionStateError("storageState 包含无效 Origin")
        host = (urlparse(str(origin["origin"])).hostname or "").lower()
        if host not in allowed:
            raise SessionStateError(f"Origin 域名不在项目允许列表：{host or '空'}")
        domains.add(host)

    if not expirations:
        expiry_status = "unknown"
        expires_at = None
    elif expired_cookie_count == len(expirations) and session_cookie_count == 0:
        expiry_status = "expired"
        expires_at = datetime.fromtimestamp(max(expirations), timezone.utc).isoformat()
    elif expired_cookie_count:
        expiry_status = "warning"
        expires_at = datetime.fromtimestamp(max(expirations), timezone.utc).isoformat()
    else:
        expiry_status = "active"
        expires_at = datetime.fromtimestamp(max(expirations), timezone.utc).isoformat()

    return SessionMetadata(
        projectId=project.id,
        importedAt=utc_now(),
        cookieCount=len(cookies),
        originCount=len(origins),
        domains=sorted(domains),
        expiresAt=expires_at,
        expiryStatus=expiry_status,
        expiredCookieCount=expired_cookie_count,
        encryption="Windows DPAPI / CurrentUser",
    )


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect(project_id: str, state: dict) -> bytes:
    if os.name != "nt":
        raise SessionStateError("当前版本仅支持在 Windows 上使用 DPAPI 保存登录态")
    plain = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _crypt(plain, project_id.encode("utf-8"), decrypt=False)


def unprotect(project_id: str, encrypted: bytes) -> dict:
    if os.name != "nt":
        raise SessionStateError("当前版本仅支持在 Windows 上使用 DPAPI 读取登录态")
    plain = _crypt(encrypted, project_id.encode("utf-8"), decrypt=True)
    value = json.loads(plain.decode("utf-8"))
    if not isinstance(value, dict):
        raise SessionStateError("解密后的登录态格式无效")
    return value


def _crypt(data: bytes, entropy: bytes, *, decrypt: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(entropy)
    output_blob = _DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if decrypt:
        ok = crypt32.CryptUnprotectData(ctypes.byref(input_blob), None, ctypes.byref(entropy_blob), None, None, flags, ctypes.byref(output_blob))
    else:
        ok = crypt32.CryptProtectData(ctypes.byref(input_blob), None, ctypes.byref(entropy_blob), None, None, flags, ctypes.byref(output_blob))
    _ = input_buffer, entropy_buffer
    if not ok:
        raise SessionStateError(f"Windows DPAPI 操作失败：{ctypes.get_last_error()}")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
