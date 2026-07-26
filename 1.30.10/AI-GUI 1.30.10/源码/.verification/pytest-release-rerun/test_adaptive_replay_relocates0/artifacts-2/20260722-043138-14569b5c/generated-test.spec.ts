import { test, expect } from '@playwright/test';

test("Canvas 自适应回放", async ({ page }, testInfo) => {
  await test.step("步骤 1", async () => {
    await page.goto("http://127.0.0.1:64387/");
  });
  await test.step("视觉定位并执行 click：Canvas 目标", async () => {
    const visualBox = await page.locator("#map").boundingBox();
    if (!visualBox) throw new Error('Visual region is not visible');
    await page.mouse.click(visualBox.x + visualBox.width * 0.75, visualBox.y + visualBox.height * 0.5);
  });
  await expect(page.getByText("Target selected")).toBeVisible();
});
