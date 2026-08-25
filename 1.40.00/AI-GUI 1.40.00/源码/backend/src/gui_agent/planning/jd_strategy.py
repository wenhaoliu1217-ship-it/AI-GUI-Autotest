"""JD web adapter for safe, deterministic entry into product search.

Some JD promotional home pages render a search box whose click and Enter
handlers do not navigate.  Repeating those controls gives the Agent no new
evidence.  This adapter converts the already-authorized product-search intent
into JD's canonical, read-only search URL while leaving product choice and all
cart writes to the normal evidence, confirmation, and commerce-policy gates.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse


_JD_HOST_SUFFIX = ".jd.com"
_SEARCH_DESCRIPTION_PREFIX = "使用京东站内搜索结果页查找商品："
_CATEGORY_SEARCH_DESCRIPTION_PREFIX = "桌面搜索受限后改用京东分类页："
_KNOWN_CATEGORY_URLS = {
    "鼠标": "https://list.jd.com/list.html?cat=670,686,690",
}
_SEARCH_BLOCK_MARKERS = (
    "访问频繁",
    "无法搜索",
    "网络异常导致无法搜索",
    "请稍后再试",
)


def _is_jd_host(host: str | None) -> bool:
    normalized = (host or "").lower().rstrip(".")
    return normalized == "jd.com" or normalized.endswith(_JD_HOST_SUFFIX)


def _extract_product_query(goal: str) -> str | None:
    """Extract the requested product phrase without treating the whole goal as a URL."""

    compact = re.sub(r"\s+", " ", goal).strip()
    quoted = re.search(r"[“\"']([^”\"']{1,40})[”\"']", compact)
    if quoted and any(marker in compact.lower() for marker in ("买", "购买", "搜索", "找", "search")):
        return quoted.group(1).strip()

    marker = re.search(r"(?:想买|购买|买|选购|搜索|查找|找)(?:到|一下)?", compact, re.IGNORECASE)
    if marker is None:
        return None
    fragment = compact[marker.end():]
    fragment = re.split(r"[，,。；;]|(?:给我|并且|并|然后|加入|添加|放入|放到|到购物车)", fragment, maxsplit=1)[0]
    fragment = re.sub(r"^(?:一个|一款|几个|几款|三款|3款|三件|3件)", "", fragment).strip()
    fragment = re.sub(
        r"^(?:(?:预算|价格)?\s*(?:不超过|低于|少于|在)?\s*)?\d+(?:\.\d+)?\s*元(?:以内|以下|之内|左右)?(?:的)?",
        "",
        fragment,
    ).strip()
    fragment = re.sub(r"^(?:性价比高的|性价比较高的|好用的|合适的)", "", fragment).strip()
    fragment = re.sub(r"(?:商品|产品)$", "", fragment).strip()
    if not fragment or len(fragment) > 40:
        return None
    return fragment


class JDDecisionStrategy:
    adapter_name = "jd"

    def matches(self, url: str) -> bool:
        return _is_jd_host(urlparse(url).hostname)

    def pre_model_decision(self, scenario, observation, history, base_url):
        from ..domain.models import Step
        from .agent_planner import AgentDecision

        query = _extract_product_query(scenario.goal)
        parsed = urlparse(observation.url)
        if query is None or not _is_jd_host(parsed.hostname):
            return None
        observation_text = "\n".join((
            observation.title,
            observation.accessibility_summary,
            *observation.dom_summary,
            *observation.failed_requests,
        ))
        desktop_search_blocked = (
            parsed.hostname == "search.jd.com"
            and parsed.path.lower() == "/search"
            and (
                any(marker in observation_text for marker in _SEARCH_BLOCK_MARKERS)
                or any("HTTP 403" in request for request in observation.failed_requests)
            )
        )
        if desktop_search_blocked:
            category_target = _KNOWN_CATEGORY_URLS.get(query)
            already_fell_back = any(
                item.status.value == "passed"
                and item.action == "navigate"
                and item.target_summary.startswith(_CATEGORY_SEARCH_DESCRIPTION_PREFIX)
                for item in history
            )
            if category_target and not already_fell_back:
                return AgentDecision(
                    kind="action",
                    action=Step(
                        action="navigate",
                        target=category_target,
                        description=f"{_CATEGORY_SEARCH_DESCRIPTION_PREFIX}{query}",
                        effect_kind="browse_search_filter_sort",
                        effect_level="read_only",
                    ),
                    reason=(
                        "京东桌面搜索页的事实证据显示搜索接口被网络或频控拦截；"
                        "改用同一京东站点的标准商品分类页继续取得真实商品、价格、榜单和销量证据。"
                        "分类页已通过公开页面验证，这是一次有界只读恢复，不重复刷新，也不执行购物车写入。"
                    ),
                    progress_assessment="progress",
                )
        if parsed.hostname == "search.jd.com" and parsed.path.lower() == "/search":
            return None
        if any(
            item.status.value == "passed"
            and item.action == "navigate"
            and item.target_summary.startswith(_SEARCH_DESCRIPTION_PREFIX)
            for item in history
        ):
            return None
        target = f"https://search.jd.com/Search?keyword={quote(query)}&enc=utf-8"
        return AgentDecision(
            kind="action",
            action=Step(
                action="navigate",
                target=target,
                description=f"{_SEARCH_DESCRIPTION_PREFIX}{query}",
                effect_kind="browse_search_filter_sort",
                effect_level="read_only",
            ),
            reason=(
                "当前京东推广页的搜索控件可能不产生导航；使用同一已授权京东域名下的标准搜索结果页，"
                "先取得真实商品、价格和评价证据。该步骤只读，不加入购物车。"
            ),
            progress_assessment="progress",
        )

    def post_model_decision(self, scenario, observation, history, base_url, decision):
        return decision

    def validate_visual_request(self, request) -> None:
        return None

    def prompt_rules(self) -> str:
        return (
            "目标是京东站点。navigate.target 必须是完整 URL 或站内路径，不能填写自然语言说明；"
            "搜索、分类浏览、筛选和查看商品是只读动作。加入购物车属于可逆写入，必须基于当前商品的名称、价格、"
            "商品链接或商品 ID 定位，并经过产品现有的确认与商务策略门禁；绝不进入提交订单或付款。"
        )
