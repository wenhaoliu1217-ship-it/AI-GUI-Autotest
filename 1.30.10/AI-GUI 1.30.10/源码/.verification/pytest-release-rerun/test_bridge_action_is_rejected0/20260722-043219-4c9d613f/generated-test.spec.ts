import { test, expect } from '@playwright/test';

test("Bridge semantic click", async ({ page }, testInfo) => {
  await test.step("步骤 1", async () => {
    await page.goto("http://127.0.0.1:52733/");
  });
  await test.step("Select entity Alpha", async () => {
    const bridgeGlobalName = process.env.APP_BRIDGE_GLOBAL || '__WEB_AI_TEST__';
    const bridgeResult = await page.evaluate(async (args) => {
      const { id, globalName } = args;
      const bridge = (window as any)[globalName];
      const required = ['getSceneState', 'listVisibleTargets', 'getTargetScreenPosition', 'getSelectedTargetId', 'waitForSceneReady'];
      if (!bridge || String(bridge.version || '') !== '1' || required.some((name) => typeof bridge[name] !== 'function')) throw new Error('Canvas App Bridge v1 contract is unavailable');
      await bridge.waitForSceneReady();
      const before = await bridge.getSceneState();
      const targets = await bridge.listVisibleTargets();
      if (!targets.some((target) => (target.id || target.targetId) === id)) throw new Error(`Bridge target is not visible: ${id}`);
      const position = await bridge.getTargetScreenPosition(id);
      return { position, before };
    }, { id: "entity.alpha", globalName: bridgeGlobalName });
    const bridgePosition = bridgeResult.position;
    await page.mouse.click(bridgePosition.x, bridgePosition.y);
    const bridgeVerification = await page.evaluate(async (args) => {
      const bridge = (window as any)[args.globalName];
      await bridge.waitForSceneReady();
      return { selectedTargetId: await bridge.getSelectedTargetId(), after: await bridge.getSceneState() };
    }, { id: "entity.alpha", globalName: bridgeGlobalName });
    if (bridgeVerification.selectedTargetId !== "entity.alpha") throw new Error('Bridge semantic selection verification failed');
  });
});
