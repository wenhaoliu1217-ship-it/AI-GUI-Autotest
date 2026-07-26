import { test, expect } from '@playwright/test';

test("Visual action with Bridge evidence", async ({ page }, testInfo) => {
  await test.step("步骤 1", async () => {
    await page.goto("http://127.0.0.1:52761/");
  });
  await test.step("步骤 2", async () => {
    const visualBox = await page.locator("#map").boundingBox();
    if (!visualBox) throw new Error('Visual region is not visible');
    await page.mouse.click(visualBox.x + visualBox.width * 0.5, visualBox.y + visualBox.height * 0.5);
  });
});
