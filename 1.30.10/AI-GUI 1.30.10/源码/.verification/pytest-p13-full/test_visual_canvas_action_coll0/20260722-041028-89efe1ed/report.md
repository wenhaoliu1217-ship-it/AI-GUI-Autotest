# 测试运行报告：Visual action with Bridge evidence

- 运行 ID：`20260722-041028-89efe1ed`
- 总体状态：**通过**
- 基础地址：http://127.0.0.1:64885
- 开始：2026-07-22T04:10:28.133248+08:00
- 结束：2026-07-22T04:10:30.335027+08:00
- 耗时：2201 ms
- 退出码：0

## 证据完整度

- 完整度：**100.0%**
- 清单：[`evidence/evidence-manifest.json`](evidence/evidence-manifest.json)
- 缺失项：0

## Canvas／Bridge 证据

### 步骤 2 · visual_click

- 采集状态：complete
- 语义目标：entity.alpha
- 坐标来源：region-relative:0.5000,0.5000
- Trace：trace.zip

```json
{
  "mode": "visual",
  "action": "visual_click",
  "semanticTarget": "entity.alpha",
  "coordinateSource": "region-relative:0.5000,0.5000",
  "beforeScreenshot": "screenshots/step-2-before.png",
  "afterScreenshot": "screenshots/step-2-after.png",
  "traceArtifact": "trace.zip",
  "collectionStatus": "complete",
  "bridgeAvailable": true,
  "bridgeBefore": {
    "phase": "before",
    "adapter": "generic",
    "globalName": "CUSTOM_TEST_BRIDGE",
    "version": "1",
    "capabilities": [
      "getSceneState",
      "listVisibleTargets",
      "getTargetScreenPosition",
      "getSelectedTargetId",
      "waitForSceneReady"
    ],
    "sceneReady": true,
    "sceneState": {
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
    "selectedTargetId": null
  },
  "bridgeAfter": {
    "phase": "after",
    "adapter": "generic",
    "globalName": "CUSTOM_TEST_BRIDGE",
    "version": "1",
    "capabilities": [
      "getSceneState",
      "listVisibleTargets",
      "getTargetScreenPosition",
      "getSelectedTargetId",
      "waitForSceneReady"
    ],
    "sceneReady": true,
    "sceneState": {
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
    "selectedTargetId": "entity.alpha"
  },
  "sceneStateChanged": false,
  "selectedTargetChanged": true,
  "gestureEvidence": null
}
```

## 步骤

| # | 动作 | 目标 | 状态 | 耗时(ms) | 说明 |
|---|------|------|------|----------|------|
| 1 | navigate | navigate -> / | 通过 | 546 |  |
| 2 | visual_click | visual_click @ css=#map | 通过 | 553 |  |
