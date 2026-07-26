# 测试运行报告：Canvas 自适应回放

- 运行 ID：`20260722-043023-3f7a0d84`
- 总体状态：**通过**
- 基础地址：http://127.0.0.1:61629
- 开始：2026-07-22T04:30:23.286803+08:00
- 结束：2026-07-22T04:30:26.074606+08:00
- 耗时：2787 ms
- 退出码：0

## 证据完整度

- 完整度：**100.0%**
- 清单：[`evidence/evidence-manifest.json`](evidence/evidence-manifest.json)
- 缺失项：0

## Canvas／Bridge 证据

### 步骤 2 · visual_click

- 采集状态：complete
- 语义目标：Canvas 目标
- 坐标来源：region-relative:0.7500,0.5000
- Trace：trace.zip

```json
{
  "mode": "visual",
  "action": "visual_click",
  "semanticTarget": "Canvas 目标",
  "coordinateSource": "region-relative:0.7500,0.5000",
  "beforeScreenshot": "screenshots/step-2-before.png",
  "afterScreenshot": "screenshots/step-2-after.png",
  "traceArtifact": "trace.zip",
  "collectionStatus": "complete",
  "bridgeAvailable": false,
  "bridgeBefore": null,
  "bridgeAfter": null,
  "sceneStateChanged": null,
  "selectedTargetChanged": null,
  "gestureEvidence": null,
  "observationProgressVerified": true
}
```

## 步骤

| # | 动作 | 目标 | 状态 | 耗时(ms) | 说明 |
|---|------|------|------|----------|------|
| 1 | navigate | navigate -> / | 通过 | 471 |  |
| 2 | visual_click | 视觉定位并执行 click：Canvas 目标 @ css=#map | 通过 | 551 | 视觉定位并执行 click：Canvas 目标 |

## 断言

| # | 类型 | 期望 | 实际 | 状态 |
|---|------|------|------|------|
| 1 | visible | None | visible=True | 通过 |
