# 测试运行报告：Bridge semantic click

- 运行 ID：`20260722-043054-8c09e0d1`
- 总体状态：**错误**
- 基础地址：http://127.0.0.1:58389
- 开始：2026-07-22T04:30:54.239178+08:00
- 结束：2026-07-22T04:30:56.606525+08:00
- 耗时：2367 ms
- 退出码：1

## 证据完整度

- 完整度：**100.0%**
- 清单：[`evidence/evidence-manifest.json`](evidence/evidence-manifest.json)
- 缺失项：0

## Canvas／Bridge 证据

### 步骤 2 · bridge_click

- 采集状态：failed
- 语义目标：entity.alpha
- 坐标来源：无坐标
- Trace：trace.zip

```json
{
  "mode": "app_bridge",
  "action": "bridge_click",
  "semanticTarget": "entity.alpha",
  "beforeScreenshot": "screenshots/step-2-before.png",
  "afterScreenshot": "screenshots/step-2-after-failure.png",
  "traceArtifact": "trace.zip",
  "collectionStatus": "failed",
  "failurePhase": "stability",
  "error": "当前环境未启用 App Bridge，拒绝执行 app_bridge 动作"
}
```

## 步骤

| # | 动作 | 目标 | 状态 | 耗时(ms) | 说明 |
|---|------|------|------|----------|------|
| 1 | navigate | navigate -> / | 通过 | 522 |  |
| 2 | bridge_click | Select entity Alpha @  | 错误 | 420 | 当前环境未启用 App Bridge，拒绝执行 app_bridge 动作 |

## 失败信息

- 失败步骤：第 2 步
- 复现步骤：
  1. navigate -> /
  2. Select entity Alpha @ 

## 可能原因（启发式提示，非确定性诊断）

- **[locator]** 页面元素可能不存在、尚未加载或定位信息已变化，请检查页面和稳定定位属性。
  - 置信度：medium（启发式）
  - 证据：步骤/断言 2；当前环境未启用 App Bridge，拒绝执行 app_bridge 动作

## 结构化问题（待人工审核）

### 步骤 2 Bridge 契约不可用

- 分类：bridge_contract_error
- 严重度／置信度：High／high
- 实际：当前环境未启用 App Bridge，拒绝执行 app_bridge 动作
- 预期：Bridge v1 五项最小契约可用并返回有效结构
- 证据时间线：
  - 2026-07-22T04:30:56.195919+08:00 · before_action；截图 `screenshots/step-2-before.png`
    - 页面：无标题 · http://127.0.0.1:58389/
  - 2026-07-22T04:30:56.425712+08:00 · after_action；截图 `screenshots/step-2-after-failure.png`
    - 页面：无标题 · http://127.0.0.1:58389/
    - collectionStatus="failed"
    - failurePhase="stability"
    - error="当前环境未启用 App Bridge，拒绝执行 app_bridge 动作"
