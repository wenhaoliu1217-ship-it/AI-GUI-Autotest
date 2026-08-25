"""Read-only evidence gates for the real Cesium ion site.

This module classifies an already captured native Observation.  It does not
navigate, click, inspect storage state, or infer identity from cookies/tokens.
The result is intentionally conservative: a protected route is only marked
verified when visible page facts contain a Cesium identity and an explicit
authenticated marker, while login/challenge signals always remain blocking.
"""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlparse

from ...domain.results import Observation
from .policy import is_cesium_target


_PROTECTED_ROUTE_PREFIXES = (
    "/stories",
    "/assets",
    "/addasset",
    "/assetdepot",
    "/clips",
    "/tokens",
    "/usage",
    "/account",
)
_AUTHENTICATED_MARKERS = (
    "sign out",
    "log out",
    "退出登录",
    "退出账号",
    "account-button-in-header",
)
_LOGIN_MARKERS = (
    "sign in",
    "login",
    "log in",
    "signin",
    "登录",
    "type=password",
    "password",
)
_CHALLENGE_MARKERS = (
    "verify you are human",
    "security verification",
    "cloudflare",
    "access denied",
    "challenge-error",
    "人机验证",
    "安全验证",
)
_MAP_MARKERS = (
    "ion-map-viewer",
    "cesium-viewer",
    "cesium widget",
    "webgl",
)


def _facts(observation: Observation) -> str:
    return "\n".join((observation.title, *observation.dom_summary, observation.accessibility_summary)).lower()


def _route_matches(path: str, expected_route: str | None) -> bool:
    if not expected_route:
        return True
    expected = expected_route.rstrip("/") or "/"
    current = path.rstrip("/") or "/"
    return current == expected or current.startswith(expected + "/")


def _is_protected_route(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in _PROTECTED_ROUTE_PREFIXES)


def _has_any(facts: str, markers: Iterable[str]) -> bool:
    return any(marker in facts for marker in markers)


def evaluate_site_observation(
    observation: Observation,
    *,
    expected_route: str | None = None,
    require_authenticated: bool = True,
    require_map_surface: bool = False,
    required_map_controls: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a bounded, conservative acceptance result for one observation."""

    parsed = urlparse(observation.url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/") or "/"
    facts = _facts(observation)
    visible_facts = observation.accessibility_summary.lower()
    target_host = is_cesium_target(observation.url) and host == "ion.cesium.com"
    cesium_brand = "cesium ion" in facts or "cesiumion" in facts
    authenticated_marker = _has_any(visible_facts, _AUTHENTICATED_MARKERS)
    login_wall = _has_any(facts, _LOGIN_MARKERS)
    challenge_wall = _has_any(facts, _CHALLENGE_MARKERS)
    protected_route = _is_protected_route(path)
    route_matches = _route_matches(path, expected_route)
    map_surface = _has_any(facts, _MAP_MARKERS)

    map_controls = []
    for line in (*observation.dom_summary, observation.accessibility_summary.splitlines()):
        if isinstance(line, list):
            candidates = line
        else:
            candidates = [line]
        for candidate in candidates:
            lowered = str(candidate).lower()
            if "ion-map-viewer" in lowered or "cesium" in lowered or "measure" in lowered or "area" in lowered:
                map_controls.append(str(candidate)[:300])
    map_controls = list(dict.fromkeys(map_controls))[:20]
    requested_controls = [str(item).strip().lower() for item in required_map_controls if str(item).strip()][:10]
    missing_map_controls = [
        item for item in requested_controls
        if not any(item in control.lower() for control in map_controls)
    ]

    blockers: list[str] = []
    if not target_host:
        blockers.append("目标地址不是 ion.cesium.com")
    if not cesium_brand:
        blockers.append("页面没有可见的 Cesium ion 身份证据")
    if not protected_route:
        blockers.append("当前页面不是已登记的受保护 Cesium 路由")
    if not route_matches:
        blockers.append(f"当前路由与期望路由不一致：{expected_route}")
    if login_wall:
        blockers.append("页面仍显示登录墙或密码输入信号")
    if challenge_wall:
        blockers.append("页面处于人机验证或安全挑战状态")
    if require_authenticated and not authenticated_marker:
        blockers.append("没有观察到明确的已登录标记")
    if require_map_surface and not map_surface:
        blockers.append("没有观察到 Cesium 地图/WebGL 表面")
    if missing_map_controls:
        blockers.append(f"缺少指定地图工具证据：{', '.join(missing_map_controls)}")

    identity_verified = target_host and cesium_brand
    session_verified = identity_verified and protected_route and route_matches and not login_wall and not challenge_wall and (
        authenticated_marker if require_authenticated else True
    )
    map_verified = (not require_map_surface and not requested_controls) or (
        map_surface and not missing_map_controls
    )
    verified = session_verified and map_verified
    return {
        "version": "1.33.00",
        "provider": "native_observation",
        "status": "verified" if verified else "blocked",
        "verified": verified,
        "identity": {
            "host": host,
            "site": "cesium-ion" if identity_verified else "unknown",
            "verified": identity_verified,
        },
        "session": {
            "protectedRoute": path,
            "routeMatches": route_matches,
            "authenticatedMarkerObserved": authenticated_marker,
            "loginWallObserved": login_wall,
            "challengeObserved": challenge_wall,
            "verified": session_verified,
        },
        "map": {
            "surfaceObserved": map_surface,
            "controlsObserved": map_controls,
            "requiredControls": requested_controls,
            "missingControls": missing_map_controls,
            "verified": map_verified,
        },
        "evidence": {
            "path": path,
            "title": observation.title[:300],
            "pageReadyState": observation.page_health.ready_state if observation.page_health else None,
            "visibleTextLength": observation.page_health.visible_text_length if observation.page_health else None,
        },
        "blockers": blockers,
    }
