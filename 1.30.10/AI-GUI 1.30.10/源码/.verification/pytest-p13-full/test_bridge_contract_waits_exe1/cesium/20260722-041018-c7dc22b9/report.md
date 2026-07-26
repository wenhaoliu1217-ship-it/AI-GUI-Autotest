# 测试运行报告：Bridge semantic click

- 运行 ID：`20260722-041018-c7dc22b9`
- 总体状态：**通过**
- 基础地址：http://127.0.0.1:64827
- 开始：2026-07-22T04:10:18.726078+08:00
- 结束：2026-07-22T04:10:20.813158+08:00
- 耗时：2087 ms
- 退出码：0

## 证据完整度

- 完整度：**100.0%**
- 清单：[`evidence/evidence-manifest.json`](evidence/evidence-manifest.json)
- 缺失项：0

## Canvas／Bridge 证据

### 步骤 2 · bridge_click

- 采集状态：complete
- 语义目标：entity.alpha
- 坐标来源：app_bridge:entity.alpha
- Trace：trace.zip

```json
{
  "mode": "app_bridge",
  "action": "bridge_click",
  "semanticTarget": "entity.alpha",
  "coordinateSource": "app_bridge:entity.alpha",
  "beforeScreenshot": "screenshots/step-2-before.png",
  "afterScreenshot": "screenshots/step-2-after.png",
  "traceArtifact": "trace.zip",
  "collectionStatus": "complete",
  "bridgeAvailable": true,
  "bridgeVersion": "1",
  "bridgeCapabilities": [
    "getSceneState",
    "listVisibleTargets",
    "getTargetScreenPosition",
    "getSelectedTargetId",
    "waitForSceneReady"
  ],
  "sceneBefore": {
    "camera": {
      "heading": 0
    },
    "layers": [
      {
        "id": "base",
        "show": true
      }
    ],
    "loading": false,
    "tilesLoaded": true
  },
  "sceneAfter": {
    "camera": {
      "heading": 0
    },
    "layers": [
      {
        "id": "base",
        "show": true
      }
    ],
    "loading": false,
    "tilesLoaded": true
  },
  "visibleTargets": [
    {
      "id": "entity.alpha",
      "type": "entity",
      "label": "Alpha"
    }
  ],
  "selectedTargetBefore": null,
  "selectedTargetAfter": "entity.alpha",
  "semanticStateVerified": true
}
```

## 步骤

| # | 动作 | 目标 | 状态 | 耗时(ms) | 说明 |
|---|------|------|------|----------|------|
| 1 | navigate | navigate -> / | 通过 | 484 |  |
| 2 | bridge_click | Select entity Alpha @  | 通过 | 438 | Select entity Alpha |
