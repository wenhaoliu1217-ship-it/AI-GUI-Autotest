import { test, expect } from '@playwright/test';

test("real adaptive container verification", async ({ page }) => {
  await test.step("步骤 1", async () => {
    await page.goto("http://host.docker.internal:57981/");
  });
  await test.step("视觉定位并执行 click：Canvas center", async () => {
    const visualBox = await page.locator("#map").boundingBox();
    if (!visualBox) throw new Error('Visual region is not visible');
    await page.mouse.click(visualBox.x + visualBox.width * 0.5, visualBox.y + visualBox.height * 0.5);
  });
  await expect(page.getByText("Selected")).toBeVisible();
});
