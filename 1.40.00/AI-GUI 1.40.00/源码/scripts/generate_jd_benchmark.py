"""从最终指导书基线生成 J01-J65 的结构化、默认阻塞场景。"""

from __future__ import annotations

import json
from pathlib import Path


CASES = [
    ("J01", "P0", "密码/二维码登录", "会话建立并返回原页面"),
    ("J02", "P0", "登录失败/过期", "无会话，错误明确"),
    ("J03", "P1", "验证码人工接管", "不绕过，完成后续跑"),
    ("J04", "P0", "首页导航与帮助", "路由、外链和选中态正确"),
    ("J05", "P0", "精确搜索", "关键词和结果相关"),
    ("J06", "P1", "联想、纠错和无结果", "状态和建议正确"),
    ("J07", "P0", "品牌/价格/库存筛选", "条件和结果一致"),
    ("J08", "P0", "综合/销量/价格排序", "顺序及参数正确"),
    ("J09", "P1", "模式切换和分页", "状态保留、边界正确"),
    ("J10", "P0", "广告与自然结果区分", "广告标识存在"),
    ("J11", "P0", "按 skuId 打开详情", "SKU 唯一且一致"),
    ("J12", "P0", "SKU 属性切换", "价格/库存/图片联动"),
    ("J13", "P0", "配送地区切换", "库存/运费/时效联动"),
    ("J14", "P0", "数量边界", "最小、最大和非法值正确"),
    ("J15", "P1", "服务和保障选择", "服务费单独计入"),
    ("J16", "P1", "评价/详情/售后页签", "内容可访问、无关键错误"),
    ("J17", "P0", "空购物车", "空态和金额为零"),
    ("J18", "P0", "加入单 SKU", "目标行、数量、价格正确"),
    ("J19", "P0", "重复加入同 SKU", "合并规则正确"),
    ("J20", "P1", "多店/多仓购物车", "分组和运费正确"),
    ("J21", "P0", "购物车数量修改", "小计、合计和库存同步"),
    ("J22", "P0", "单选/分组/全选", "选择作用域正确"),
    ("J23", "P0", "删除取消/确认", "只处理目标 cartLineId"),
    ("J24", "P1", "失效商品与移入关注", "状态和清理正确"),
    ("J25", "P0", "结算商品快照", "SKU、数量、服务一致"),
    ("J26", "P0", "地址新增/选择/校验", "仅测试数据，字段正确"),
    ("J27", "P1", "配送与自提", "时效、运费和能力匹配"),
    ("J28", "P0", "发票字段校验", "类型、抬头、税号正确"),
    ("J29", "P0", "满减边界", "门槛前后相差一分正确"),
    ("J30", "P0", "券叠加/互斥", "最优规则和手选正确"),
    ("J31", "P0", "优惠分摊和舍入", "行合计等于订单优惠"),
    ("J32", "P0", "最终金额公式", "所有金额项精确一致"),
    ("J33", "P0", "无货/库存不足", "下单被阻断且原因明确"),
    ("J34", "P1", "两会话最后库存", "无超卖，失败方明确"),
    ("J35", "P0", "提交订单幂等", "仅一个有效 orderId"),
    ("J36", "P0", "支付成功", "支付/订单/账务一致"),
    ("J37", "P0", "支付失败/取消", "订单可重试或关闭正确"),
    ("J38", "P0", "支付超时", "库存和权益按规则释放"),
    ("J39", "P0", "重复支付回调", "不重复扣款/记账"),
    ("J40", "P1", "支付成功但页面超时", "后台状态优先且可恢复"),
    ("J41", "P0", "订单列表和详情", "orderId、快照、金额一致"),
    ("J42", "P1", "订单时间/状态筛选", "结果和分页正确"),
    ("J43", "P0", "未支付订单取消", "状态关闭、库存释放"),
    ("J44", "P1", "已支付取消", "审核、退款和权益返还"),
    ("J45", "P1", "发货和物流推进", "节点有序、状态一致"),
    ("J46", "P1", "拆单/多包裹", "子单、金额和物流正确"),
    ("J47", "P1", "确认收货", "高风险确认、状态正确"),
    ("J48", "P0", "售后入口和资格", "可售后范围正确"),
    ("J49", "P0", "整单退款", "金额、库存和权益正确"),
    ("J50", "P0", "部分商品退款", "分摊和剩余订单正确"),
    ("J51", "P1", "退货/换货/维修", "状态机和逆向物流正确"),
    ("J52", "P0", "重复退款回调", "仅一次退款入账"),
    ("J53", "P1", "售后驳回/补资料/撤销", "分支和原因明确"),
    ("J54", "P1", "优惠券各状态", "可用/已用/过期/回收站正确"),
    ("J55", "P1", "发票申请与下载", "状态、金额、文件正确"),
    ("J56", "P1", "收藏/关注/足迹", "只操作 E2E 数据并清理"),
    ("J57", "P1", "评价表单校验", "正式站不提交，沙箱可清理"),
    ("J58", "P1", "客服入口", "正式站不发送消息"),
    ("J59", "P0", "隐私全链路脱敏", "明文个人/支付数据为 0"),
    ("J60", "P0", "正式站危险动作阻断", "不下单、不支付、不售后"),
    ("J61", "P1", "会话刷新/崩溃恢复", "不重复写入"),
    ("J62", "P1", "429/5xx/断网恢复", "退避、幂等和分类正确"),
    ("J63", "P1", "不同视口和键盘访问", "布局、焦点、操作可用"),
    ("J64", "P0", "Console/Network 证据", "关键错误不遗漏"),
    ("J65", "P0", "全闭环清理", "购物车、订单、券、库存零残留"),
]

READ_ONLY = ({f"J{i:02d}" for i in range(1, 18)} - {"J13"}) | {"J41", "J42", "J54", "J58", "J59", "J60", "J63", "J64"}
REVERSIBLE = {"J18", "J19", "J20", "J21", "J22", "J23", "J24", "J56"}
RESTORABLE_PREFERENCE = {"J13"}
MANUAL_TAKEOVER = {"J01", "J03"}
MULTI_SESSION = {"J34", "J61"}
MONEY = {"J12", "J13", "J15", "J18", "J20", "J21", "J25", "J27", "J29", "J30", "J31", "J32", "J36", "J41", "J44", "J46", "J49", "J50", "J55"}


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "backend" / "benchmarks" / "jd" / "scenarios"
    root.mkdir(parents=True, exist_ok=True)
    for case_id, priority, title, acceptance in CASES:
        risk = "read" if case_id in READ_ONLY else "reversible_write" if case_id in REVERSIBLE | RESTORABLE_PREFERENCE else "high_risk_write"
        allowed_environments = ["production_readonly", "isolated_transaction"] if case_id in READ_ONLY | RESTORABLE_PREFERENCE else ["isolated_transaction"]
        required_capabilities = ["jd_page_adapter", "pii_redaction", "network_evidence"]
        if case_id in MANUAL_TAKEOVER:
            required_capabilities.append("manual_takeover_resume")
        if case_id in MULTI_SESSION:
            required_capabilities.append("multi_session_barrier")
        if case_id in MONEY:
            required_capabilities.append("decimal_money_evidence")
        if case_id in RESTORABLE_PREFERENCE:
            required_capabilities.extend(["preference_snapshot", "cleanup_verification"])
        elif risk != "read":
            required_capabilities.extend(["e2e_resource_ledger", "side_effect_confirmation", "cleanup_verification"])
        if risk == "high_risk_write":
            required_capabilities.extend(["idempotency_key", "isolated_transaction_sandbox"])
        payload = {
            "id": case_id,
            "priority": priority,
            "title": title,
            "role": "dedicated_buyer_or_required_authorized_role",
            "repeatCount": 5,
            "allowedEnvironments": allowed_environments,
            "riskLevel": risk,
            "formalSitePolicy": "read_only" if risk == "read" else "allowed_with_restore" if case_id in RESTORABLE_PREFERENCE else "forbidden",
            "preconditions": [
                "使用专用测试账号或游客会话，不记录真实个人信息",
                "固定数据、角色、环境和动作授权均已登记",
            ] + (["记录原配送地区并预先登记恢复动作"] if case_id in RESTORABLE_PREFERENCE else ["写场景只使用 E2E 资源并预先登记清理动作"] if risk != "read" else []),
            "requiredBusinessIds": ["skuId", "cartLineId", "orderId", "paymentId", "refundId"],
            "acceptanceAssertions": [acceptance, "页面事实与脱敏网络／后台状态证据一致"],
            "evidenceRequirements": [
                "runId/caseId/stepId/role/environment",
                "脱敏路由、定位器、前后截图、Console/Network",
                "业务 ID 仅保存 SHA-256 和脱敏后缀",
                "写场景保存状态时间线、幂等键结果和资源台账",
            ],
            "cleanup": "无需持久资源清理" if risk == "read" else "恢复原配送地区" if case_id in RESTORABLE_PREFERENCE else "逆序清理全部 E2E 资源并验证后台不可查询或恢复基线",
            "requiredCapabilities": required_capabilities,
            "blockedDependencies": [
                "京东目标环境与账号授权",
                "稳定页面 Adapter／业务 ID 属性",
            ] + ([] if risk == "read" or case_id in RESTORABLE_PREFERENCE else ["隔离交易环境、固定数据和清理接口"]),
            "bindingStatus": "blocked",
            "verificationStatus": "unverified",
        }
        (root / f"{case_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    manifest = {
        "profile": "jd-commerce-1.30.31",
        "scenarioCount": len(CASES),
        "repeatCount": 5,
        "plannedAttempts": len(CASES) * 5,
        "releaseStatus": "unverified",
        "scenarios": [case_id for case_id, *_ in CASES],
    }
    (root.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
