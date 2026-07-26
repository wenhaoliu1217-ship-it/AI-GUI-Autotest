"""Resolve user-entered public URLs without site-specific redirect rules."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from ..security.policy import DomainPolicy


TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid",
    "yclid", "igshid", "vero_conv", "vero_id", "wickedid",
}


def strip_tracking_parameters(url: str) -> str:
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def resolve_public_url(
    url: str,
    *,
    max_redirects: int = 8,
    requester: Callable[[str], tuple[int, str | None]] | None = None,
) -> dict:
    """Follow ordinary HTTP redirects, checking every hop against the network boundary."""
    original = strip_tracking_parameters(url)
    current = original
    chain = [current]
    request = requester or _request_redirect
    try:
        for _ in range(max_redirects):
            DomainPolicy(current).check_url(current)
            status, location = request(current)
            if status not in {301, 302, 303, 307, 308} or not location:
                return {
                    "url": strip_tracking_parameters(current),
                    "changed": strip_tracking_parameters(current) != url,
                    "redirectChain": chain,
                }
            current = strip_tracking_parameters(urljoin(current, location))
            if current in chain:
                break
            chain.append(current)
    except (httpx.HTTPError, OSError, ValueError):
        pass
    return {"url": original, "changed": original != url, "redirectChain": chain}


def _request_redirect(url: str) -> tuple[int, str | None]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AI-GUI-URL-Resolver/1.32)",
        "Range": "bytes=0-0",
    }
    with httpx.Client(timeout=httpx.Timeout(12.0, connect=6.0), follow_redirects=False) as client:
        response = client.get(url, headers=headers)
    return response.status_code, response.headers.get("location")
