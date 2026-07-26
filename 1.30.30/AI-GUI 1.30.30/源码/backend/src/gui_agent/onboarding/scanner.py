"""Read-only compatibility scanning for an application and its safe navigation links."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urldefrag, urlparse

from playwright.sync_api import BrowserContext, Page, sync_playwright

from ..security.policy import DomainPolicy, SecurityError, guard_playwright_route
from .models import CompatibilityReport, ProjectConfig


_DANGEROUS_NAVIGATION = (
    "logout", "signout", "delete", "remove", "destroy", "pay", "checkout", "publish", "invite",
    "退出", "注销", "删除", "移除", "支付", "结算", "发布", "邀请",
)


def _unique(values: Iterable[str], limit: int = 100) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))[:limit]


def _is_safe_navigation(label: str, url: str, policy: DomainPolicy) -> bool:
    lowered = f"{label} {url}".lower()
    if any(token in lowered for token in _DANGEROUS_NAVIGATION):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    try:
        policy.check_url(url)
    except Exception:
        return False
    return True


def _inspect_page(page: Page, url: str, timeout_ms: int) -> tuple[dict, list[str]]:
    redirect_chain: list[str] = []
    response = page.goto(url, wait_until="commit")
    if response is not None:
        request = response.request
        while request is not None:
            redirect_chain.append(request.url)
            request = request.redirected_from
        redirect_chain.reverse()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 8_000))
    except Exception:
        pass
    facts = page.evaluate(
        """
        () => {
          const all = Array.from(document.querySelectorAll('*'));
          const controls = Array.from(document.querySelectorAll('button,a[href],input,select,textarea,[role="button"],[contenteditable="true"]'));
          const resources = performance.getEntriesByType('resource').map(entry => ({ name: entry.name, type: entry.initiatorType || '' }));
          const text = selector => Array.from(document.querySelectorAll(selector))
            .map(el => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ')).filter(Boolean).slice(0, 12);
          const nameOf = el => (el.getAttribute('aria-label') || el.getAttribute('title') ||
            (el.labels && el.labels[0] && el.labels[0].innerText) || el.innerText || el.textContent || el.getAttribute('name') || '').trim().replace(/\\s+/g, ' ');
          const nav = Array.from(document.querySelectorAll('nav a[href],header a[href],aside a[href],[role="navigation"] a[href]'))
            .map(el => ({ label: nameOf(el).slice(0, 120), href: new URL(el.getAttribute('href'), location.href).href }));
          const navLabels = Array.from(document.querySelectorAll('nav a,nav button,header a,header button,aside a,aside button,[role="navigation"] a,[role="navigation"] button'))
            .map(nameOf).filter(Boolean).slice(0, 30);
          const passwordInputs = document.querySelectorAll('input[type="password"]').length;
          const oneTimeInputs = document.querySelectorAll('input[autocomplete="one-time-code"],input[name*="otp" i],input[id*="otp" i]').length;
          const pageText = (document.body?.innerText || '').slice(0, 30000);
          const captcha = document.querySelectorAll('iframe[src*="recaptcha" i],iframe[src*="hcaptcha" i],[class*="captcha" i],[id*="captcha" i]').length > 0 || /验证码|captcha|人机验证/i.test(pageText);
          const mfa = oneTimeInputs > 0 || /双重验证|两步验证|多因素|动态口令|验证器|one[- ]time code|two[- ]factor|multi[- ]factor/i.test(pageText);
          const unlabeledControls = controls.filter(el => {
            if (el.matches('a[href]')) return !nameOf(el);
            if (el.matches('input[type="hidden"],input[type="submit"],input[type="button"]')) return false;
            return !nameOf(el) && !el.getAttribute('aria-labelledby');
          }).length;
          const duplicateIds = Object.values(all.reduce((acc, el) => {
            if (el.id) acc[el.id] = (acc[el.id] || 0) + 1;
            return acc;
          }, {})).filter(count => count > 1).length;
          const canvases = Array.from(document.querySelectorAll('canvas'));
          const scriptText = Array.from(document.scripts).map(el => `${el.src} ${el.textContent || ''}`).join(' ').slice(0, 200000);
          const webgl = canvases.length > 0 && /webgl|cesium|three(?:\\.min)?\\.js|babylon|mapbox|deck\\.gl/i.test(scriptText + ' ' + document.documentElement.innerHTML.slice(0, 100000));
          const iframeHosts = Array.from(document.querySelectorAll('iframe[src]')).map(el => {
            try { return new URL(el.src, location.href).hostname; } catch { return ''; }
          }).filter(Boolean);
          const loadingSignals = document.querySelectorAll('[aria-busy="true"],[class*="skeleton" i],[class*="spinner" i],[class*="loading" i]').length;
          const framework = /__NEXT_DATA__/.test(document.documentElement.innerHTML) ? 'Next.js' :
            document.querySelector('[data-reactroot],#root') ? 'React/SPA candidate' :
            document.querySelector('[data-v-app],#app') ? 'Vue/SPA candidate' :
            /angular/i.test(scriptText) ? 'Angular/SPA candidate' : '';
          return {
            title: document.title || '', finalUrl: location.href,
            summary: {
              buttons: document.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]').length,
              links: document.querySelectorAll('a[href]').length,
              inputs: document.querySelectorAll('input').length,
              selects: document.querySelectorAll('select').length,
              textareas: document.querySelectorAll('textarea').length,
              canvases: canvases.length,
              webglRegions: webgl ? canvases.length : 0,
              iframes: document.querySelectorAll('iframe').length,
              crossOriginIframes: iframeHosts.filter(host => host !== location.hostname).length,
              fileInputs: document.querySelectorAll('input[type="file"]').length,
              shadowRoots: all.filter(el => el.shadowRoot).length,
              contentEditors: document.querySelectorAll('[contenteditable="true"],.monaco-editor,.CodeMirror,.ProseMirror').length,
              unlabeledControls,
              duplicateIds,
              loadingSignals
            },
            locators: {
              testIds: document.querySelectorAll('[data-testid],[data-test],[data-qa]').length,
              labels: document.querySelectorAll('label').length,
              roles: document.querySelectorAll('[role]').length,
              ariaNames: document.querySelectorAll('[aria-label],[aria-labelledby]').length,
              namedControls: controls.filter(nameOf).length
            },
            resources, headings: text('h1,h2,h3'), nav, navLabels,
            auth: { passwordInputs, loginDetected: passwordInputs > 0 || /登录|sign in|log in/i.test(pageText), captcha, mfa, oneTimeInputs },
            iframeHosts, framework,
            asyncPatterns: {
              fetch: resources.filter(item => ['fetch', 'xmlhttprequest'].includes(item.type)).length,
              loadingSignals
            }
          };
        }
        """
    )
    return facts, _unique(redirect_chain)


def _classify_page(facts: dict) -> str:
    summary = facts["summary"]
    if facts["auth"]["loginDetected"]:
        return "登录页"
    if summary["webglRegions"] or summary["canvases"]:
        return "Canvas/WebGL 混合页"
    if summary["contentEditors"]:
        return "复杂编辑页"
    if summary["inputs"] + summary["selects"] + summary["textareas"] >= 4:
        return "表单页"
    if summary["links"] + summary["buttons"] >= 6:
        return "导航/工作台"
    return "内容页"


def scan_project(
    project: ProjectConfig,
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    storage_state: dict | None = None,
) -> CompatibilityReport:
    policy = DomainPolicy(
        project.base_url,
        project.allowed_hosts,
        allow_private_network=project.allow_private_network,
    )
    policy.check_url(project.base_url)
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    http_errors: list[str] = []
    profiles: list[dict] = []
    facts_by_url: list[dict] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1440, "height": 960}, storage_state=storage_state)
        context.route(
            "**/*",
            lambda route: guard_playwright_route(
                route,
                policy,
                lambda message, url, resource_type: failed_requests.append(
                    f"BLOCKED {resource_type} {url[:250]} ({message[:180]})"
                ),
            ),
        )
        pending = [project.base_url]
        visited: set[str] = set()
        try:
            while pending and len(visited) < 6:
                requested = urldefrag(pending.pop(0))[0].rstrip("/") or project.base_url
                if requested in visited:
                    continue
                visited.add(requested)
                page = context.new_page()
                page.set_default_navigation_timeout(timeout_ms)
                page.on("console", lambda message: console_errors.append(message.text[:500]) if message.type == "error" else None)
                page.on("pageerror", lambda error: page_errors.append(str(error)[:500]))
                page.on("requestfailed", lambda request: failed_requests.append(f"{request.method} {request.url[:350]}"))
                page.on("response", lambda response: http_errors.append(f"HTTP {response.status} {response.url[:350]}") if response.status >= 400 else None)
                try:
                    policy.clear_rejection()
                    facts, redirect_chain = _inspect_page(page, requested, timeout_ms)
                    policy.check_url(facts["finalUrl"])
                    facts_by_url.append(facts)
                    profile = {
                        "url": facts["finalUrl"], "title": facts["title"], "pageType": _classify_page(facts),
                        "summary": facts["summary"], "candidateLocators": facts["locators"],
                        "headings": facts["headings"][:8], "redirectChain": redirect_chain,
                    }
                    profiles.append(profile)
                    for entry in facts["nav"]:
                        target = urldefrag(entry["href"])[0]
                        if _is_safe_navigation(entry["label"], target, policy) and target.rstrip("/") not in visited and target not in pending:
                            pending.append(target)
                except Exception as exc:
                    rejection = policy.consume_rejection()
                    if rejection and requested == project.base_url:
                        raise SecurityError(rejection) from exc
                    if requested == project.base_url:
                        raise
                    failed_requests.append(f"GET {requested[:350]} ({(rejection or str(exc))[:160]})")
                finally:
                    page.close()
        finally:
            context.close()
            browser.close()

    root = facts_by_url[0]
    summaries = [facts["summary"] for facts in facts_by_url]
    locators = [facts["locators"] for facts in facts_by_url]
    total_summary = {key: sum(item.get(key, 0) for item in summaries) for key in summaries[0]}
    total_locators = {key: sum(item.get(key, 0) for item in locators) for key in locators[0]}
    resources = [item["name"] for facts in facts_by_url for item in facts["resources"]]
    base_host = urlparse(project.base_url).hostname or ""
    third_party = sorted({urlparse(url).hostname for url in resources if urlparse(url).hostname and urlparse(url).hostname != base_host})
    navigation_entries = _unique(label for facts in facts_by_url for label in facts["navLabels"])

    capabilities = ["标准 DOM", "ARIA/Accessibility 候选", "主要导航只读遍历"]
    recommendations: list[str] = []
    blocked: list[str] = []
    stable: list[str] = []
    visual: list[str] = []
    adaptive: list[str] = []
    manual: list[str] = []
    auth_signals: list[str] = []
    async_patterns: list[str] = []

    if total_locators["namedControls"]:
        stable.append(f"{total_locators['namedControls']} 个具名 DOM 控件可优先使用角色、标签或文本定位")
    if total_locators["testIds"]:
        stable.append(f"{total_locators['testIds']} 个控件提供 data-testid/data-test/data-qa")
    else:
        recommendations.append("关键业务控件尚未发现测试标识；可选补充 data-testid，但不阻止 L0/L1 黑盒测试")
    if total_summary["unlabeledControls"]:
        adaptive.append(f"{total_summary['unlabeledControls']} 个控件缺少可访问名称，定位稳定性较低")
        recommendations.append("为无可访问名称的关键控件补充 label、aria-label 或 aria-labelledby")
    if total_summary["duplicateIds"]:
        adaptive.append(f"发现 {total_summary['duplicateIds']} 组重复 DOM id")
    if total_summary["canvases"]:
        capabilities.append("Canvas/WebGL")
        visual.append(f"{total_summary['canvases']} 个 Canvas 区域需要截图视觉 fallback 或相对几何操作")
        blocked.append("Canvas/WebGL 内部对象无法通过标准 DOM 枚举")
        recommendations.append("Canvas 仍可用 L0/L1 视觉能力探索；如需确定性状态验证，可选接入只在测试环境启用的 App Bridge")
    if total_summary["webglRegions"]:
        capabilities.append("WebGL 框架候选")
    if total_summary["iframes"]:
        capabilities.append("Iframe")
    if total_summary["crossOriginIframes"]:
        adaptive.append(f"{total_summary['crossOriginIframes']} 个跨域 Iframe 需要单独允许域名和 Frame 定位")
        blocked.append("跨域 Iframe 内部结构受同源边界限制")
    if total_summary["shadowRoots"]:
        capabilities.append("Shadow DOM")
        adaptive.append(f"{total_summary['shadowRoots']} 个开放 Shadow Root 需要组件级定位")
    if total_summary["contentEditors"]:
        capabilities.append("复杂编辑器")
        adaptive.append(f"{total_summary['contentEditors']} 个 contenteditable/代码编辑器需要专用交互策略")
    if total_summary["fileInputs"]:
        capabilities.append("文件上传")
        recommendations.append("文件上传仅使用用户授权的固定测试文件，并在执行前确认目标目录")

    auth = [facts["auth"] for facts in facts_by_url]
    if any(item["loginDetected"] for item in auth):
        auth_signals.append("检测到登录入口或密码输入框")
        if storage_state:
            auth_signals.append("已加载保存的登录态，但页面仍出现登录信号，需检查会话是否失效")
            blocked.append("保存的登录态可能未生效或已被服务端拒绝")
        else:
            recommendations.append("建议上传 storageState 或使用交互登录录制后重新扫描")
    elif storage_state:
        auth_signals.append("扫描已加载保存的登录态，未发现公开登录表单")
    else:
        auth_signals.append("未发现明确登录表单；本次按公开页面状态扫描")
    if any(item["captcha"] for item in auth):
        auth_signals.append("检测到验证码/人机验证候选")
        manual.append("验证码必须由人工处理，系统不得绕过")
    if any(item["mfa"] for item in auth):
        auth_signals.append("检测到 MFA/一次性口令候选")
        manual.append("MFA、短信、扫码或硬件密钥步骤需要人工完成")

    frameworks = _unique(facts["framework"] for facts in facts_by_url)
    if frameworks:
        capabilities.extend(frameworks)
    fetch_count = sum(facts["asyncPatterns"]["fetch"] for facts in facts_by_url)
    loading_count = sum(facts["asyncPatterns"]["loadingSignals"] for facts in facts_by_url)
    if fetch_count:
        async_patterns.append(f"观察到 {fetch_count} 个 Fetch/XHR 资源，页面存在异步数据加载")
    if loading_count:
        async_patterns.append(f"观察到 {loading_count} 个 loading/skeleton/aria-busy 信号")
    if navigation_entries:
        stable.append(f"识别 {len(navigation_entries)} 个主要导航入口；仅遍历具有安全 GET 地址的入口")
    if len(profiles) == 1 and navigation_entries:
        adaptive.append("主要导航使用无 href 的脚本按钮；只读扫描已记录入口名称但未点击，避免触发写操作")

    recommended_level = "L0"
    if any(item["loginDetected"] for item in auth) or storage_state:
        recommended_level = "L1"
    if total_summary["unlabeledControls"] or total_summary["shadowRoots"] or total_summary["contentEditors"]:
        recommended_level = "L2"
    if total_summary["canvases"] or total_summary["webglRegions"]:
        recommended_level = "L3"

    analytics_hosts = [host for host in third_party if any(token in host for token in ("analytics", "telemetry", "sentry", "clarity", "google-analytics"))]
    ignore_rules = [f"**://{host}/**" for host in analytics_hosts]
    visible_target = root["headings"][0] if root["headings"] else root["title"] or project.name
    suggested = [f"确认看到“{visible_target}”"]
    suggested.extend(f"验证主要导航入口“{label}”可见且可访问" for label in navigation_entries[:4])
    suggested.extend(f"验证业务区域“{heading}”可见" for heading in root["headings"][:3])
    if any(item["loginDetected"] for item in auth):
        suggested.insert(0, "使用授权测试账号完成登录并确认进入首个业务页面")

    recommendations.extend(f"评估第三方域名 {host} 是否需要加入网络忽略规则，而不是自动加入访问白名单" for host in analytics_hosts)
    status = "attention" if blocked or manual or console_errors or page_errors or failed_requests or http_errors else "compatible"
    return CompatibilityReport(
        projectId=project.id,
        onboardingLevel=project.onboarding_level,
        recommendedOnboardingLevel=recommended_level,
        requestedUrl=project.base_url,
        finalUrl=root["finalUrl"],
        title=root["title"],
        status=status,
        pageSummary=total_summary,
        candidateLocators=total_locators,
        capabilities=_unique(capabilities),
        thirdPartyHosts=third_party,
        consoleErrors=_unique([*console_errors, *page_errors], 20),
        failedRequests=_unique([*failed_requests, *http_errors], 20),
        blockedAreas=_unique(blocked),
        recommendations=_unique(recommendations),
        suggestedScenarios=_unique(suggested, 8),
        scannedPages=profiles,
        navigationEntries=navigation_entries,
        authenticationSignals=_unique(auth_signals),
        asyncPatterns=_unique(async_patterns),
        stableAreas=_unique(stable),
        visualAreas=_unique(visual),
        adaptiveAreas=_unique(adaptive),
        manualAreas=_unique(manual),
        recommendedConfig={
            "allowedHosts": project.allowed_hosts,
            "ignoreRules": ignore_rules,
            "viewport": {"width": 1440, "height": 960},
            "limits": project.limits.model_dump(mode="json", by_alias=True),
        },
    )
