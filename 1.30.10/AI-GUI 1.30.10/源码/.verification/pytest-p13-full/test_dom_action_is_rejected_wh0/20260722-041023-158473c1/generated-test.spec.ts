import { test, expect } from '@playwright/test';

test("Occlusion check", async ({ page }, testInfo) => {
  await test.step("步骤 1", async () => {
    await page.goto("http://127.0.0.1:64877/");
  });
  await test.step("步骤 2", async () => {
    await page.locator("#target").click();
  });
});
