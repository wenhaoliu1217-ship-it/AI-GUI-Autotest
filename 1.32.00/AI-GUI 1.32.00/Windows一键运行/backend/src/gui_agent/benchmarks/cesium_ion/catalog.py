"""Authoritative, non-optimistic C01-C60 Cesium ion scenario catalog."""

from __future__ import annotations

from collections import Counter
from datetime import date


SCENARIOS = (
    ("C01", "P0", "正确登录", "进入受保护路由且账号上下文正确"),
    ("C02", "P0", "错误登录", "错误提示明确且不建立会话"),
    ("C03", "P1", "OAuth 登录取消/回调", "state 与 PKCE 正确且无重复提交"),
    ("C04", "P0", "会话过期与退出", "退出后受保护数据不可继续访问"),
    ("C05", "P0", "全局导航巡检", "主入口路由、标题和选中态正确"),
    ("C06", "P1", "帮助和外链", "外链域名、新窗口和返回行为正确"),
    ("C07", "P0", "My Assets 搜索/过滤", "UI 与 API 过滤结果一致"),
    ("C08", "P0", "排序/分页", "查询参数、列状态与 Link 分页一致"),
    ("C09", "P1", "行选择与全选", "选择作用域正确且不跨页误选"),
    ("C10", "P0", "上传合法 GLB", "处理状态 COMPLETE 且详情预览正确"),
    ("C11", "P0", "上传合法 3D Tiles ZIP", "tileset ready 且关键资源无失败"),
    ("C12", "P0", "上传 GeoJSON/KML/CZML", "资产类型、实体、范围和时间正确"),
    ("C13", "P1", "上传影像", "资产为 IMAGERY 且范围和图层正确"),
    ("C14", "P1", "上传地形", "资产为 TERRAIN 且高程/provider 正确"),
    ("C15", "P1", "上传点云", "资产为 3DTILES 且点云可见"),
    ("C16", "P1", "多文件/目录/sidecar", "上传集合完整且资源引用无 404"),
    ("C17", "P0", "非法/损坏文件", "ERROR/DATA_ERROR 可诊断且不误报成功"),
    ("C18", "P1", "上传断网、取消与重试", "状态、残留和恢复均正确"),
    ("C19", "P1", "并发上传", "任务和进度按业务 ID 隔离"),
    ("C20", "P0", "异步状态恢复", "刷新或重启后按 task/asset ID 继续"),
    ("C21", "P1", "S3 导入", "凭据脱敏、Preview 和导入结果正确"),
    ("C22", "P1", "Azure 三类认证", "成功、过期和权限不足分支正确"),
    ("C23", "P2", "Sketchfab/Bentley 导入", "授权、取消和资产映射正确"),
    ("C24", "P0", "资产详情元数据", "列表、详情和 API 元数据一致"),
    ("C25", "P0", "Cesium Viewer 加载", "scene/tiles ready 且无 WebGL 致命错误"),
    ("C26", "P1", "相机/Home/全屏", "状态变化可观测且能退出全屏"),
    ("C27", "P1", "时间轴播放", "CZML 时钟、播放和暂停状态一致"),
    ("C28", "P1", "Attribution 与代码复制", "归属正确、代码有效且 token 脱敏"),
    ("C29", "P1", "位置/方向/比例编辑", "保存后 Viewer 与 API 一致"),
    ("C30", "P0", "标签创建与应用", "标签计数和资产/Story 关联一致"),
    ("C31", "P0", "Archive 与下载", "ZIP 文件名、大小、SHA-256 和内容正确"),
    ("C32", "P1", "S3 Export", "任务完成且对象清单和头信息正确"),
    ("C33", "P0", "删除 E2E 资产取消/确认", "仅删除双重校验后的目标资产 ID"),
    ("C34", "P0", "阻止删除非 E2E 资产", "策略门拒绝且零副作用"),
    ("C35", "P0", "Asset Depot 浏览/详情", "类型、提供商、许可和归属正确"),
    ("C36", "P1", "Add to my assets", "新引用可定位、可预览且可清理"),
    ("C37", "P1", "创建 3D Tiles Clip", "AOI、配额、状态和输出资产正确"),
    ("C38", "P1", "创建影像/地形 Clip", "输出类型、范围和预览正确"),
    ("C39", "P1", "Clip 非法 AOI/配额不足", "明确阻断且不错误占用配额"),
    ("C40", "P0", "创建最小权限 token", "scopes、URL 和 assets 限制正确"),
    ("C41", "P0", "token API 正/负授权", "目标资产允许且非目标资产拒绝"),
    ("C42", "P0", "token 全链路脱敏", "全部证据中的明文密钥数量为零"),
    ("C43", "P1", "token 轮换/撤销", "新 token 有效且旧 token 失效"),
    ("C44", "P0", "Usage 范围与 token 过滤", "图表、数据接口和选项一致"),
    ("C45", "P1", "Usage 延迟与配额", "用量在约定统计窗口内最终一致"),
    ("C46", "P0", "Story 创建、编辑和预览", "资产、幻灯片、相机和时间一致"),
    ("C47", "P1", "Story 自动保存与重进", "无数据丢失且无重复草稿"),
    ("C48", "P1", "Story 分享与撤销", "访问边界正确且默认不公开"),
    ("C49", "P0", "Shadow DOM 全量扫描", "真实可见控件无系统性漏检"),
    ("C50", "P0", "Console/Network/WebGL 异常归档", "失败分类和证据完整"),
    ("C51", "P1", "Account 字段校验和取消", "校验可验证且不提交高风险变更"),
    ("C52", "P1", "Authorized Apps 只读/撤销 E2E", "Last Used 正确且撤销后访问失败"),
    ("C53", "P1", "OAuth App + PKCE", "redirect、state 和 challenge 正确"),
    ("C54", "P1", "Team Owner/Member 权限", "账单和成员权限严格隔离"),
    ("C55", "P1", "Team 邀请/切换/离开", "团队上下文和数据严格隔离"),
    ("C56", "P0", "Billing/License 只读巡检", "不产生套餐、支付或许可副作用"),
    ("C57", "P1", "API 429/5xx/超时恢复", "退避、失败分类和幂等正确"),
    ("C58", "P1", "浏览器刷新/崩溃续跑", "不重复创建且不遗漏清理"),
    ("C59", "P1", "不同视口与键盘", "布局、焦点、表格和 Viewer 可用"),
    ("C60", "P0", "全闭环清理", "资产、token、Story、Clip 和标签零残留"),
)


READ_ONLY = {
    "C05", "C06", "C07", "C08", "C09", "C24", "C25", "C26", "C27", "C28",
    "C34", "C35", "C44", "C49", "C50", "C51", "C56", "C59",
}
HIGH_RISK = {"C04", "C32", "C33", "C37", "C38", "C40", "C43", "C48", "C52", "C53", "C54", "C55", "C60"}
FORBIDDEN = set()
OBSERVATION_NOTES = {
    "C05": "protected navigation and primary routes were inventoried",
    "C06": "help and external-link targets were inventoried without opening third-party pages",
    "C07": "asset search and type filtering were exercised once and restored",
    "C08": "name sorting and the single-page disabled pagination state were exercised once",
    "C09": "one existing row was selected and unselected without invoking a write action",
    "C24": "the public Cesium OSM Buildings detail metadata was inspected",
    "C25": "the Viewer open-shadow canvas was present at 464x250; full readiness was not proven",
    "C26": "the Viewer Home control was exercised once; full-screen behavior was not exercised",
    "C28": "attribution and generated code were inspected; clipboard copy was not exercised",
    "C35": "Asset Depot inventory and detail structure were inspected without adding an asset",
    "C44": "Usage scope filtering was exercised once and restored without reading token values",
    "C49": "open Shadow DOM hosts and controls were recursively inventoried",
    "C51": "Account form names and control types were inspected without reading field values",
    "C56": "Billing and License routes were inventoried without changing plan or payment state",
}
OBSERVED_READ_ONLY = set(OBSERVATION_NOTES)

REQUIREMENTS = {
    "C01": ["isolated_e2e_identity"], "C02": ["isolated_e2e_identity"],
    "C03": ["oauth_test_tenant", "human_takeover"], "C04": ["isolated_e2e_identity", "session_recovery"],
    **{f"C{i:02d}": ["isolated_e2e_identity", "versioned_test_data", "write_authorization"] for i in range(10, 21)},
    "C21": ["temporary_s3_credentials", "dedicated_bucket_prefix"],
    "C22": ["azure_test_tenant", "three_credential_sets"],
    "C23": ["sketchfab_or_bentley_test_identity", "human_takeover"],
    "C29": ["owned_e2e_asset", "write_authorization"],
    "C30": ["owned_e2e_asset", "write_authorization"],
    "C31": ["owned_e2e_asset", "archive_quota"],
    "C32": ["temporary_s3_credentials", "explicit_confirmation"],
    "C33": ["owned_e2e_asset", "explicit_confirmation"],
    "C36": ["approved_depot_asset", "write_authorization"],
    "C37": ["clip_quota", "explicit_confirmation"], "C38": ["clip_quota", "explicit_confirmation"],
    "C39": ["clip_test_context"],
    "C40": ["owned_e2e_asset", "explicit_confirmation"], "C41": ["e2e_access_token"],
    "C43": ["e2e_access_token", "explicit_confirmation"],
    "C45": ["write_scenario_usage", "statistics_delay_window"],
    "C46": ["owned_e2e_asset", "write_authorization"], "C47": ["owned_e2e_story"],
    "C48": ["owned_e2e_story", "explicit_confirmation"],
    "C52": ["owned_e2e_oauth_app", "explicit_confirmation"],
    "C53": ["oauth_test_tenant", "explicit_confirmation"],
    "C54": ["team_owner_and_member_identities", "team_sandbox"],
    "C55": ["team_owner_and_member_identities", "team_sandbox", "explicit_confirmation"],
    "C57": ["fault_injection_proxy"], "C58": ["persistent_resource_ledger"],
    "C60": ["resource_ledger", "explicit_confirmation"],
}


def _effect(case_id: str) -> str:
    if case_id in FORBIDDEN:
        return "forbidden"
    if case_id in HIGH_RISK:
        return "high_risk_write"
    if case_id in READ_ONLY:
        return "read_only"
    return "reversible_write"


def _role(case_id: str) -> str:
    if case_id in {"C54", "C55"}:
        return "team_owner_and_member"
    if case_id in {"C02", "C03"}:
        return "unauthenticated_or_oauth_test_user"
    return "personal_e2e_account"


def _steps(case_id: str, title: str) -> list[str]:
    return [
        f"预检 {case_id} 所需身份、配额、固定数据和目标所有权",
        f"通过 Cesium ion Dashboard 执行“{title}”的主流程和规定负向分支",
        "按业务 ID 关联 UI、REST/网络、文件或 Viewer 状态证据",
        "执行场景断言；任何环境阻塞、加载中或未知状态均不得记为通过",
        "按资源台账执行清理并验证零非目标副作用",
    ]


def scenario_catalog() -> list[dict]:
    result = []
    for case_id, priority, title, expected in SCENARIOS:
        requirements = REQUIREMENTS.get(case_id, [])
        status = "observed_read_only" if case_id in OBSERVED_READ_ONLY else ("blocked" if requirements else "unverified")
        result.append({
            "id": case_id,
            "version": "1.32.00",
            "priority": priority,
            "title": title,
            "businessGoal": expected,
            "role": _role(case_id),
            "preconditions": requirements or ["authenticated_session_or_case_specific_public_state"],
            "fixedData": ["case-owned IDs from the run ledger; never infer IDs from row order"],
            "steps": _steps(case_id, title),
            "primaryLocator": "data-testid/business ID, then role + accessible name inside the recorded shadow path",
            "backupLocator": "independent label/placeholder/href or row-scoped stable attribute",
            "exactExpected": expected,
            "effectLevel": _effect(case_id),
            "timeouts": {"startupMs": 30_000, "noProgressMs": 120_000, "maximumMs": 7_200_000, "maxRetries": 2},
            "cleanup": "delete or detach only ledger-owned E2E resources; assert UI/API absence",
            "rollback": "stop new writes, preserve the ledger, and resume cleanup by recorded business ID",
            "evidence": ["before/after screenshot", "DOM/ARIA/Shadow summary", "console/network", "business status timeline", "cleanup result"],
            "execution": {
                "status": status,
                "repetitionsCompleted": 0,
                "requiredRepetitions": 5,
                "reason": (
                    f"2026-07-22 read-only observation only: {OBSERVATION_NOTES[case_id]}; "
                    "no fixed scenario has completed five product-run repetitions"
                    if status == "observed_read_only"
                    else ("missing: " + ", ".join(requirements) if requirements else "not executed by the 1.32.00 product runner")
                ),
            },
        })
    return result


def acceptance_payload() -> dict:
    cases = scenario_catalog()
    status_counts = Counter(item["execution"]["status"] for item in cases)
    priority_counts = Counter(item["priority"] for item in cases)
    return {
        "suite": "cesium-ion",
        "version": "1.32.00",
        "target": "https://ion.cesium.com",
        "inspectedAt": date(2026, 7, 22).isoformat(),
        "truthPolicy": "loading, dry-run, blocked, observed, or environment-unavailable never count as passed",
        "thresholds": {"repetitions": 5, "p0CompletionPercent": 100, "allCompletionPercent": 95, "evidencePercent": 98, "cleanupPercent": 100, "plaintextSecrets": 0},
        "summary": {"total": len(cases), "byStatus": dict(status_counts), "byPriority": dict(priority_counts), "passed": 0},
        "cases": cases,
    }

