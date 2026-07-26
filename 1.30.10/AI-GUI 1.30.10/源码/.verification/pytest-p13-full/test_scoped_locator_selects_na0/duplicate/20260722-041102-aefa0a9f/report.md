# 测试运行报告：Duplicate

- 运行 ID：`20260722-041102-aefa0a9f`
- 总体状态：**错误**
- 基础地址：http://127.0.0.1:65009
- 开始：2026-07-22T04:11:02.850951+08:00
- 结束：2026-07-22T04:11:04.759120+08:00
- 耗时：1908 ms
- 退出码：1

## 证据完整度

- 完整度：**100.0%**
- 清单：[`evidence/evidence-manifest.json`](evidence/evidence-manifest.json)
- 缺失项：0

## 步骤

| # | 动作 | 目标 | 状态 | 耗时(ms) | 说明 |
|---|------|------|------|----------|------|
| 1 | navigate | navigate -> / | 通过 | 461 |  |
| 2 | click | click @ role=button[name=Delete] | 错误 | 358 | 动作目标必须唯一，实际匹配 2 个：role=button[name=Delete] |

## 失败信息

- 失败步骤：第 2 步
- 复现步骤：
  1. navigate -> /
  2. click @ role=button[name=Delete]

## 可能原因（启发式提示，非确定性诊断）

- **[unknown]** 发生未分类异常，请结合错误消息、截图和 trace 人工复核。
  - 置信度：low（启发式）
  - 证据：步骤/断言 2；动作目标必须唯一，实际匹配 2 个：role=button[name=Delete]

## 结构化问题（待人工审核）

### 步骤 2 未完成：click

- 分类：unknown
- 严重度／置信度：Medium／medium
- 实际：动作目标必须唯一，实际匹配 2 个：role=button[name=Delete]
- 预期：动作成功并进入可验证的下一状态
- 证据时间线：
  - 2026-07-22T04:11:04.367500+08:00 · before_action；截图 `screenshots/step-2-before.png`
    - 页面：无标题 · http://127.0.0.1:65009/
  - 2026-07-22T04:11:04.563307+08:00 · after_action；截图 `screenshots/step-2-after-failure.png`
    - 页面：无标题 · http://127.0.0.1:65009/
    - 动作目标必须唯一，实际匹配 2 个：role=button[name=Delete]
