"""使用 Windows DPAPI 保存 Playwright storageState，不落盘明文。"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from datetime import datetime, timezone
from urllib.parse import urlparse

from .models import ProjectConfig, SessionMetadata, utc_now


MAX_STATE_BYTES = 2 * 1024 * 1024
SESSION_STORAGE_STATE_KEY = "sessionStorage"
MAX_SESSION_STORAGE_ORIGINS = 100
MAX_SESSION_STORAGE_ITEMS = 2_000
KEYRING_SERVICE = "ai-gui-autotest.storage-state"


class SessionStateError(ValueError):
    """登录态格式或安全边界不合法。"""


def _normalized_cookie_domain(value: object) -> str:
    return str(value).strip().lstrip(".").rstrip(".").lower()


def _cookie_domain_is_allowed(domain: str, allowed_hosts: set[str]) -> bool:
    """Cookie 父域可覆盖允许的子域，但不能用相似后缀冒充。"""
    return bool(domain) and any(
        host == domain or host.endswith(f".{domain}")
        for host in allowed_hosts
    )


def filter_storage_state_for_project(project: ProjectConfig, state: dict) -> dict:
    """录制登录态只保留适用于项目主机的 Cookie 和精确 Origin。"""
    if not isinstance(state, dict) or not isinstance(state.get("cookies", []), list) or not isinstance(state.get("origins", []), list):
        raise SessionStateError("storageState 必须包含 cookies 和 origins 数组")
    allowed = {host.strip().rstrip(".").lower() for host in project.allowed_hosts}

    cookies = []
    for cookie in state.get("cookies", []):
        if not isinstance(cookie, dict) or not cookie.get("domain"):
            cookies.append(cookie)
            continue
        domain = _normalized_cookie_domain(cookie["domain"])
        if _cookie_domain_is_allowed(domain, allowed):
            cookies.append(cookie)

    origins = []
    for origin in state.get("origins", []):
        if not isinstance(origin, dict) or not origin.get("origin"):
            origins.append(origin)
            continue
        host = (urlparse(str(origin["origin"])).hostname or "").lower()
        if host in allowed:
            origins.append(origin)

    session_storage = []
    for entry in state.get(SESSION_STORAGE_STATE_KEY, []):
        if not isinstance(entry, dict) or not entry.get("origin"):
            continue
        host = (urlparse(str(entry["origin"])).hostname or "").lower()
        if host in allowed:
            session_storage.append(entry)

    return {
        **state,
        "cookies": cookies,
        "origins": origins,
        SESSION_STORAGE_STATE_KEY: session_storage,
    }


def capture_session_storage_for_project(project: ProjectConfig, pages: list[object]) -> list[dict]:
    """Capture per-tab sessionStorage without exposing values outside encrypted state."""
    allowed = {host.strip().rstrip(".").lower() for host in project.allowed_hosts}
    captured: dict[str, list[dict[str, str]]] = {}
    for page in pages:
        try:
            entry = page.evaluate(
                """() => ({
                    origin: window.location.origin,
                    items: Object.keys(window.sessionStorage).map((name) => ({
                        name,
                        value: window.sessionStorage.getItem(name) ?? "",
                    })),
                })"""
            )
        except Exception:
            continue
        if not isinstance(entry, dict) or not entry.get("origin"):
            continue
        origin = str(entry["origin"])
        host = (urlparse(origin).hostname or "").lower()
        items = entry.get("items", [])
        if host in allowed and isinstance(items, list):
            captured[origin] = items
    return [
        {"origin": origin, "items": items}
        for origin, items in captured.items()
    ]


def playwright_storage_state(state: dict | None) -> dict | None:
    """Return only fields accepted by Playwright's browser.new_context."""
    if state is None:
        return None
    return {
        "cookies": state.get("cookies", []),
        "origins": state.get("origins", []),
    }


def session_storage_init_script(state: dict | None) -> str | None:
    """Build an origin-scoped init script that restores captured sessionStorage."""
    if not state:
        return None
    by_origin = {
        str(entry["origin"]): entry.get("items", [])
        for entry in state.get(SESSION_STORAGE_STATE_KEY, [])
        if isinstance(entry, dict) and entry.get("origin") and isinstance(entry.get("items", []), list)
    }
    if not by_origin:
        return None
    encoded = json.dumps(by_origin, ensure_ascii=True, separators=(",", ":"))
    return f"""(() => {{
        const entries = {encoded}[window.location.origin];
        if (!Array.isArray(entries)) return;
        for (const item of entries) {{
            if (!item || typeof item.name !== "string" || typeof item.value !== "string") continue;
            try {{ window.sessionStorage.setItem(item.name, item.value); }} catch (_) {{}}
        }}
    }})()"""


def validate_storage_state(project: ProjectConfig, state: dict) -> SessionMetadata:
    if not isinstance(state, dict) or not isinstance(state.get("cookies", []), list) or not isinstance(state.get("origins", []), list):
        raise SessionStateError("storageState 必须包含 cookies 和 origins 数组")
    encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_STATE_BYTES:
        raise SessionStateError("storageState 超过 2 MB 安全上限")
    cookies = state.get("cookies", [])
    origins = state.get("origins", [])
    session_storage = state.get(SESSION_STORAGE_STATE_KEY, [])
    if not isinstance(session_storage, list):
        raise SessionStateError("storageState 中的 sessionStorage 必须是数组")
    if len(cookies) > 500 or len(origins) > 100 or len(session_storage) > MAX_SESSION_STORAGE_ORIGINS:
        raise SessionStateError("storageState 中的 Cookie 或 Origin 数量超过安全上限")

    allowed = {host.strip().rstrip(".").lower() for host in project.allowed_hosts}
    domains: set[str] = set()
    expirations: list[float] = []
    session_cookie_count = 0
    expired_cookie_count = 0
    now = datetime.now(timezone.utc).timestamp()
    for cookie in cookies:
        if not isinstance(cookie, dict) or not cookie.get("name") or not cookie.get("domain"):
            raise SessionStateError("storageState 包含无效 Cookie")
        domain = _normalized_cookie_domain(cookie["domain"])
        if not _cookie_domain_is_allowed(domain, allowed):
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

    session_storage_item_count = 0
    session_storage_origins: set[str] = set()
    for entry in session_storage:
        if not isinstance(entry, dict) or not entry.get("origin") or not isinstance(entry.get("items"), list):
            raise SessionStateError("storageState 包含无效 sessionStorage Origin")
        origin = str(entry["origin"])
        host = (urlparse(origin).hostname or "").lower()
        if host not in allowed:
            raise SessionStateError(f"sessionStorage Origin 域名不在项目允许列表：{host or '空'}")
        session_storage_origins.add(origin)
        domains.add(host)
        for item in entry["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("value"), str):
                raise SessionStateError("storageState 包含无效 sessionStorage 条目")
            session_storage_item_count += 1
            if session_storage_item_count > MAX_SESSION_STORAGE_ITEMS:
                raise SessionStateError("storageState 中的 sessionStorage 条目超过安全上限")

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
        sessionStorageOriginCount=len(session_storage_origins),
        sessionStorageItemCount=session_storage_item_count,
        domains=sorted(domains),
        expiresAt=expires_at,
        expiryStatus=expiry_status,
        expiredCookieCount=expired_cookie_count,
        encryption=_encryption_description(),
    )


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect(project_id: str, state: dict) -> bytes:
    plain = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if os.name == "nt":
        return _crypt(plain, project_id.encode("utf-8"), decrypt=False)
    return _protect_with_keyring(project_id, plain)


def unprotect(project_id: str, encrypted: bytes) -> dict:
    if os.name == "nt":
        plain = _crypt(encrypted, project_id.encode("utf-8"), decrypt=True)
    else:
        plain = _unprotect_with_keyring(project_id, encrypted)
    value = json.loads(plain.decode("utf-8"))
    if not isinstance(value, dict):
        raise SessionStateError("解密后的登录态格式无效")
    return value


def _encryption_description() -> str:
    if os.name == "nt":
        return "Windows DPAPI / CurrentUser"
    uname = getattr(os, "uname", None)
    system = str(uname().sysname).lower() if uname else "unix"
    return f"{system} OS keyring + Fernet"


def storage_state_filename() -> str:
    """Keep the legacy DPAPI filename on Windows and use a neutral name elsewhere."""
    return "storage-state.dpapi" if os.name == "nt" else "storage-state.secure"


def _keyring_backend():
    try:
        import keyring
    except ImportError as exc:
        raise SessionStateError(
            "当前系统缺少跨平台登录态依赖，请安装 backend/requirements-platform.txt"
        ) from exc
    try:
        backend = keyring.get_keyring()
        backend_name = f"{backend.__class__.__module__}.{backend.__class__.__name__}".lower()
        if "fail" in backend_name or "plaintext" in backend_name:
            raise SessionStateError(
                "未检测到可用的系统钥匙串；请启用 Linux Secret Service 或 macOS Keychain"
            )
        return keyring
    except SessionStateError:
        raise
    except Exception as exc:
        raise SessionStateError(f"系统钥匙串不可用：{exc}") from exc


def _fernet(key: bytes | None = None):
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise SessionStateError(
            "当前系统缺少 Fernet 依赖，请安装 backend/requirements-platform.txt"
        ) from exc
    return Fernet(key) if key is not None else Fernet


def _keyring_key(project_id: str) -> bytes:
    keyring = _keyring_backend()
    try:
        encoded = keyring.get_password(KEYRING_SERVICE, project_id)
        if encoded:
            return str(encoded).encode("ascii")
        key = _fernet().generate_key()
        keyring.set_password(KEYRING_SERVICE, project_id, key.decode("ascii"))
        return key
    except SessionStateError:
        raise
    except Exception as exc:
        raise SessionStateError(f"系统钥匙串读写失败：{exc}") from exc


def _protect_with_keyring(project_id: str, plain: bytes) -> bytes:
    try:
        cipher = _fernet(_keyring_key(project_id))
        return b"F1:" + cipher.encrypt(plain)
    except SessionStateError:
        raise
    except Exception as exc:
        raise SessionStateError(f"跨平台登录态加密失败：{exc}") from exc


def _unprotect_with_keyring(project_id: str, encrypted: bytes) -> bytes:
    if not encrypted.startswith(b"F1:"):
        raise SessionStateError("跨平台登录态格式不受支持")
    try:
        cipher = _fernet(_keyring_key(project_id))
        return cipher.decrypt(encrypted[3:])
    except SessionStateError:
        raise
    except Exception as exc:
        raise SessionStateError(f"跨平台登录态解密失败：{exc}") from exc


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
