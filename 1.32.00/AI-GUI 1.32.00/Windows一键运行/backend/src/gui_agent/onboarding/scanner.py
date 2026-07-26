"""Read-only compatibility scanning for an application and its safe navigation links."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urldefrag, urlparse, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Page, sync_playwright

from ..security.redaction import REDACTION_MASK, Redactor
from ..security.policy import DomainPolicy, SecurityError, guard_playwright_route
from .models import CompatibilityReport, ProjectConfig


_DANGEROUS_NAVIGATION = (
    "logout", "signout", "delete", "remove", "destroy", "pay", "checkout", "publish", "invite",
    "退出", "注销", "删除", "移除", "支付", "结算", "发布", "邀请",
)
_SENSITIVE_QUERY_KEYS = {
    "access_token", "authorization", "code", "credential", "key", "password", "secret",
    "session_token", "sig", "signature", "token",
}
_TRANSIENT_NAVIGATION_ERRORS = (
    "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET", "ERR_NETWORK_CHANGED", "ERR_TIMED_OUT",
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


def _safe_url(value: str) -> str:
    """Redact credential-shaped query parameters before a URL reaches scan evidence."""
    try:
        parsed = urlsplit(value)
        query = urlencode([
            (key, REDACTION_MASK if key.lower() in _SENSITIVE_QUERY_KEYS else item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
    except Exception:
        return Redactor().scrub(value)


def _sanitize_scan_facts(facts: dict) -> dict:
    for resource in facts.get("resources", []):
        resource["name"] = _safe_url(str(resource.get("name", "")))
    for entry in facts.get("nav", []):
        entry["href"] = _safe_url(str(entry.get("href", "")))
    return Redactor().scrub_mapping(facts)


def _inspect_page(page: Page, url: str, timeout_ms: int) -> tuple[dict, list[str]]:
    redirect_chain: list[str] = []
    response = None
    for attempt in range(3):
        try:
            response = page.goto(url, wait_until="commit")
            break
        except Exception as exc:
            if attempt == 2 or not any(code in str(exc) for code in _TRANSIENT_NAVIGATION_ERRORS):
                raise
            page.wait_for_timeout(500 * (attempt + 1))
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
          const controlSelector = 'button,a[href],input,select,textarea,[contenteditable="true"],'
            + '[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="textbox"],'
            + '[role="combobox"],[role="switch"],[role="tab"],[role="menuitem"],[role="option"]';
          const records = [];
          const shadowHosts = [];
          const slots = [];
          const seenRoots = new Set();
          const seenElements = new Set();
          const rootPaths = new Map();
          const normalize = value => String(value || '').trim().replace(/\\s+/g, ' ');
          const cssSegment = el => {
            if (el.id) return `#${CSS.escape(el.id)}`;
            for (const attr of ['data-testid', 'data-test', 'data-qa']) {
              if (el.hasAttribute(attr)) return `[${attr}="${CSS.escape(el.getAttribute(attr))}"]`;
            }
            const tag = el.localName || '*';
            const parent = el.parentElement;
            if (!parent) return tag;
            const siblings = Array.from(parent.children).filter(item => item.localName === el.localName);
            return siblings.length > 1 ? `${tag}:nth-of-type(${siblings.indexOf(el) + 1})` : tag;
          };
          const composedParent = el => el.assignedSlot || el.parentElement || el.getRootNode()?.host || null;
          const visible = el => {
            if (!(el instanceof Element) || el.hidden || el.closest('template')) return false;
            for (let current = el; current instanceof Element; current = composedParent(current)) {
              const style = getComputedStyle(current);
              if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || Number(style.opacity) === 0) return false;
              if (current.hidden || current.hasAttribute('inert')) return false;
            }
            return el.getClientRects().length > 0;
          };
          const nameOf = el => normalize(el.getAttribute('aria-label') || el.getAttribute('title') ||
            (el.labels && el.labels[0] && el.labels[0].innerText) || el.innerText || el.textContent || el.getAttribute('name'));
          const roleOf = el => {
            if (el.getAttribute('role')) return el.getAttribute('role');
            if (el.matches('button,input[type="button"],input[type="submit"]')) return 'button';
            if (el.matches('a[href]')) return 'link';
            if (el.matches('input[type="checkbox"]')) return 'checkbox';
            if (el.matches('input[type="radio"]')) return 'radio';
            if (el.matches('select')) return 'combobox';
            if (el.matches('textarea,input:not([type]),input[type="text"],input[type="email"],input[type="password"],input[type="search"]')) return 'textbox';
            return '';
          };
          const visitRoot = (root, hostPath) => {
            if (!root || seenRoots.has(root)) return;
            seenRoots.add(root);
            rootPaths.set(root, hostPath);
            for (const el of Array.from(root.querySelectorAll('*'))) {
              if (!seenElements.has(el)) {
                seenElements.add(el);
                records.push({ el, shadowHosts: hostPath });
              }
              if (el.localName === 'slot') {
                const assigned = el.assignedElements({ flatten: true });
                slots.push({
                  name: el.name || 'default', shadowHosts: hostPath,
                  assignedElements: assigned.length, visible: visible(el)
                });
              }
              if (el.shadowRoot) {
                const path = [...hostPath, cssSegment(el)];
                shadowHosts.push({ tag: el.localName, path, visible: visible(el) });
                visitRoot(el.shadowRoot, path);
              }
            }
          };
          visitRoot(document, []);
          const effectivePath = record => {
            if (record.shadowHosts.length) return record.shadowHosts;
            const slot = record.el.assignedSlot;
            return slot ? (rootPaths.get(slot.getRootNode()) || []) : [];
          };
          const controls = records.filter(record => record.el.matches(controlSelector));
          const controlFacts = controls.slice(0, 1000).map(record => {
            const el = record.el;
            const name = nameOf(el).slice(0, 160);
            const role = roleOf(el);
            const dataTestId = el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-qa') || '';
            return {
              tag: el.localName, role, name, visible: visible(el),
              shadowHosts: effectivePath(record), slot: el.assignedSlot?.name || (el.assignedSlot ? 'default' : ''),
              locator: {
                testId: dataTestId, role: role && name ? role : '', accessibleName: role && name ? name : '',
                label: normalize(el.labels?.[0]?.innerText).slice(0, 160),
                placeholder: normalize(el.getAttribute('placeholder')).slice(0, 160),
                name: normalize(el.getAttribute('name')).slice(0, 160),
                href: el.matches('a[href]') ? new URL(el.getAttribute('href'), location.href).href : '',
                css: cssSegment(el)
              }
            };
          });
          const elements = records.map(record => record.el);
          const resources = performance.getEntriesByType('resource').map(entry => ({ name: entry.name, type: entry.initiatorType || '' }));
          const text = selector => records.filter(record => record.el.matches(selector))
            .map(record => normalize(record.el.innerText || record.el.textContent)).filter(Boolean).slice(0, 12);
          const navRecords = controls.filter(record => record.el.matches('a[href]') && visible(record.el));
          const nav = navRecords.slice(0, 60).map(record => ({
            label: nameOf(record.el).slice(0, 120),
            href: new URL(record.el.getAttribute('href'), location.href).href,
            shadowHosts: effectivePath(record)
          }));
          const isLandmarkControl = record => {
            for (let current = record.el; current instanceof Element; current = composedParent(current)) {
              if (current.matches('nav,header,aside,[role="navigation"]')) return true;
            }
            return false;
          };
          const navLabels = controls.filter(record => visible(record.el) && isLandmarkControl(record))
            .map(record => nameOf(record.el)).filter(Boolean).slice(0, 50);
          const passwordInputs = elements.filter(el => el.matches('input[type="password"]')).length;
          const visiblePasswordInputs = elements.filter(el => el.matches('input[type="password"]') && visible(el)).length;
          const oneTimeInputs = elements.filter(el => el.matches('input[autocomplete="one-time-code"],input[name*="otp" i],input[id*="otp" i]')).length;
          const pageText = [document.body?.innerText || '', ...controlFacts.map(item => item.name)].join(' ').slice(0, 30000);
          const visibleControlNames = controlFacts.filter(item => item.visible).map(item => item.name).filter(Boolean);
          const loginEntryDetected = visibleControlNames.some(name => /请登录|^(?:登录|登录账号|立即登录|sign\\s*in|log\\s*in)$/i.test(name));
          const loginUrlDetected = /(?:^|[/_-])(?:login|signin|sign-in|passport)(?:[/_.-]|$)/i.test(location.pathname + ' ' + location.hostname);
          const loginWallDetected = /(?:请先|需要|必须|尚未|未)登录(?:后|才|以便|即可|$)|(?:sign\\s*in|log\\s*in)\\s+(?:to|before)\\s+(?:continue|access|view)/i.test(pageText);
          const loginFormDetected = visiblePasswordInputs > 0 || loginUrlDetected || loginWallDetected;
          const logoutDetected = visibleControlNames.some(name => /退出登录|退出账号|注销登录|sign\\s*out|log\\s*out/i.test(name));
          const accountAreaDetected = visibleControlNames.some(name => /^(?:我的订单|订单中心|个人中心|账号中心|账户中心|会员中心|个人资料|profile|my\\s+account|account\\s+center|dashboard)$/i.test(name));
          // Public sites often show “我的订单 / My account” before login. It is only
          // positive evidence when the page no longer exposes an explicit login entry.
          const loggedInEvidence = logoutDetected || (accountAreaDetected && !loginEntryDetected);
          const captcha = elements.some(el => el.matches('iframe[src*="recaptcha" i],iframe[src*="hcaptcha" i],[class*="captcha" i],[id*="captcha" i]')) || /验证码|captcha|人机验证/i.test(pageText);
          const mfa = oneTimeInputs > 0 || /双重验证|两步验证|多因素|动态口令|验证器|one[- ]time code|two[- ]factor|multi[- ]factor/i.test(pageText);
          const unlabeledControls = controls.filter(record => {
            const el = record.el;
            if (el.matches('a[href]')) return !nameOf(el);
            if (el.matches('input[type="hidden"],input[type="submit"],input[type="button"]')) return false;
            return !nameOf(el) && !el.getAttribute('aria-labelledby');
          }).length;
          const duplicateIds = Object.values(elements.reduce((acc, el) => {
            if (el.id) acc[el.id] = (acc[el.id] || 0) + 1;
            return acc;
          }, {})).filter(count => count > 1).length;
          const canvases = elements.filter(el => el.matches('canvas'));
          const scriptText = elements.filter(el => el.matches('script')).map(el => `${el.src} ${el.textContent || ''}`).join(' ').slice(0, 200000);
          const webgl = canvases.length > 0 && /webgl|cesium|three(?:\\.min)?\\.js|babylon|mapbox|deck\\.gl/i.test(scriptText + ' ' + document.documentElement.innerHTML.slice(0, 100000));
          const iframeHosts = elements.filter(el => el.matches('iframe[src]')).map(el => {
            try { return new URL(el.src, location.href).hostname; } catch { return ''; }
          }).filter(Boolean);
          const loadingSignals = elements.filter(el => el.matches('[aria-busy="true"],[class*="skeleton" i],[class*="spinner" i],[class*="loading" i]')).length;
          const framework = /__NEXT_DATA__/.test(document.documentElement.innerHTML) ? 'Next.js' :
            document.querySelector('[data-reactroot],#root') ? 'React/SPA candidate' :
            document.querySelector('[data-v-app],#app') ? 'Vue/SPA candidate' :
            /angular/i.test(scriptText) ? 'Angular/SPA candidate' : '';
          const regions = records.filter(record => record.el.matches('header,nav,main,aside,footer,form,[role="region"],[role="navigation"],[role="main"]'))
            .map(record => ({ tag: record.el.localName, role: record.el.getAttribute('role') || '', name: nameOf(record.el).slice(0, 120), shadowHosts: effectivePath(record) }))
            .slice(0, 80);
          const sideEffectPattern = /(?:new|create|add|upload|share|publish|regenerate|delete|remove|upgrade|purchase|invite|新建|创建|添加|上传|分享|发布|重新生成|删除|移除|升级|购买|邀请)/i;
          const sideEffects = controlFacts.filter(item => item.visible && sideEffectPattern.test(item.name)).map(item => ({
            control: item.name, role: item.role, shadowHosts: item.shadowHosts, disposition: 'not-triggered-by-read-only-scan'
          })).slice(0, 80);
          return {
            title: document.title || '', finalUrl: location.href,
            summary: {
              buttons: elements.filter(el => el.matches('button,[role="button"],input[type="button"],input[type="submit"]')).length,
              links: elements.filter(el => el.matches('a[href]')).length,
              inputs: elements.filter(el => el.matches('input')).length,
              selects: elements.filter(el => el.matches('select')).length,
              textareas: elements.filter(el => el.matches('textarea')).length,
              canvases: canvases.length,
              webglRegions: webgl ? canvases.length : 0,
              iframes: elements.filter(el => el.matches('iframe')).length,
              crossOriginIframes: iframeHosts.filter(host => host !== location.hostname).length,
              fileInputs: elements.filter(el => el.matches('input[type="file"]')).length,
              shadowRoots: shadowHosts.length,
              shadowControls: controls.filter(record => effectivePath(record).length > 0).length,
              visibleControls: controls.filter(record => visible(record.el)).length,
              hiddenControls: controls.filter(record => !visible(record.el)).length,
              controlInventoryTotal: controls.length,
              controlInventoryReturned: Math.min(controls.length, 1000),
              slots: slots.length,
              assignedSlotElements: records.filter(record => Boolean(record.el.assignedSlot)).length,
              templates: elements.filter(el => el.matches('template')).length,
              contentEditors: elements.filter(el => el.matches('[contenteditable="true"],.monaco-editor,.CodeMirror,.ProseMirror')).length,
              unlabeledControls,
              duplicateIds,
              loadingSignals
            },
            locators: {
              testIds: elements.filter(el => el.matches('[data-testid],[data-test],[data-qa]')).length,
              labels: elements.filter(el => el.matches('label')).length,
              roles: elements.filter(el => el.matches('[role]')).length,
              ariaNames: elements.filter(el => el.matches('[aria-label],[aria-labelledby]')).length,
              namedControls: controls.filter(record => nameOf(record.el)).length,
              shadowPathCandidates: controls.filter(record => effectivePath(record).length > 0).length,
              slottedControls: controls.filter(record => Boolean(record.el.assignedSlot)).length
            },
            resources, headings: text('h1,h2,h3'), nav, navLabels, regions,
            shadowHosts, slots, controls: controlFacts, sideEffects,
            evidenceHooks: {
              testIds: controls.filter(record => record.el.matches('[data-testid],[data-test],[data-qa]')).length,
              accessibleNames: controls.filter(record => Boolean(roleOf(record.el) && nameOf(record.el))).length,
              shadowPaths: controls.filter(record => effectivePath(record).length > 0).length,
              canvasOrWebgl: canvases.length
            },
            auth: {
              passwordInputs, visiblePasswordInputs, loginEntryDetected, loginFormDetected,
              loginDetected: loginFormDetected, loggedInEvidence, logoutDetected,
              accountAreaDetected, captcha, mfa, oneTimeInputs
            },
            iframeHosts, framework,
            asyncPatterns: {
              fetch: resources.filter(item => ['fetch', 'xmlhttprequest'].includes(item.type)).length,
              loadingSignals
            }
          };
        }
        """
    )
    return _sanitize_scan_facts(facts), _unique(_safe_url(item) for item in redirect_chain)


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
                page.on("requestfailed", lambda request: failed_requests.append(f"{request.method} {_safe_url(request.url)[:350]}"))
                page.on("response", lambda response: http_errors.append(f"HTTP {response.status} {_safe_url(response.url)[:350]}") if response.status >= 400 else None)
                try:
                    policy.clear_rejection()
                    facts, redirect_chain = _inspect_page(page, requested, timeout_ms)
                    policy.check_url(facts["finalUrl"])
                    facts_by_url.append(facts)
                    profile = {
                        "url": facts["finalUrl"], "route": urlparse(facts["finalUrl"]).path or "/",
                        "title": facts["title"], "pageType": _classify_page(facts),
                        "summary": facts["summary"], "candidateLocators": facts["locators"],
                        "headings": facts["headings"][:8], "redirectChain": redirect_chain,
                        "regions": facts["regions"], "shadowHosts": facts["shadowHosts"],
                        "slots": facts["slots"], "controls": facts["controls"],
                        "sideEffects": facts["sideEffects"],
                        "networkContracts": facts["resources"][:100],
                        "evidenceHooks": facts["evidenceHooks"],
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
    # 登录是否成功必须以用户输入的首页为准。只读扫描后续访问的公开导航页
    # 可能带有登录入口，不能反过来把已经登录的首页判成失败。
    root_auth = auth[0]
    blocking_login = root_auth["loginFormDetected"]
    account_evidence = root_auth["loggedInEvidence"]
    public_login_entry = any(item["loginEntryDetected"] for item in auth)
    if storage_state and account_evidence and not blocking_login:
        auth_signals.append("已确认登录成功，并识别到登录后的账号功能")
    elif blocking_login:
        auth_signals.append("检测到仍在显示的登录表单或登录拦截页面")
        if storage_state:
            auth_signals.append("已加载保存的登录状态，但网站仍要求登录，需检查会话是否失效")
            blocked.append("保存的登录状态未生效或已被网站拒绝")
        else:
            recommendations.append("需要测试账号功能时，请使用交互登录窗口正常登录后重新扫描")
    elif storage_state and public_login_entry:
        auth_signals.append("已加载保存的登录状态，但首页仍显示登录入口，暂时无法确认账号已生效")
        blocked.append("保存的登录状态可能未生效：首页仍显示登录入口")
    elif storage_state:
        auth_signals.append("已保存登录状态，且未发现阻断操作的登录页面；可以继续测试账号功能")
    elif public_login_entry:
        auth_signals.append("页面提供可选的登录入口；当前公开内容无需登录也可测试")
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
