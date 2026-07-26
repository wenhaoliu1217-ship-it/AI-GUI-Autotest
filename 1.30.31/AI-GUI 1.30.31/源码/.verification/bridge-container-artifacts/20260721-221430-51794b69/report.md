# 测试运行报告：real container bridge verification

- 运行 ID：`20260721-221430-51794b69`
- 总体状态：**通过**
- 基础地址：http://host.docker.internal:60937
- 开始：2026-07-21T14:14:31.796742+00:00
- 结束：2026-07-21T14:14:34.244042+00:00
- 耗时：2447 ms
- 退出码：0
- Runner 隔离：docker_container ｜ Windows Job：未绑定 ｜ 内存上限：2048 MB ｜ 强制终止：否

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
    "tilesLoaded": true,
    "loading": false
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
    "tilesLoaded": true,
    "loading": false
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
| 1 | navigate | navigate -> / | 通过 | 466 |  |
| 2 | bridge_click | Select entity Alpha through Bridge @  | 通过 | 381 | Select entity Alpha through Bridge |
