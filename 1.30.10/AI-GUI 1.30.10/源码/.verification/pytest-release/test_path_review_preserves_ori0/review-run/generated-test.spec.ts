import { test, expect } from '@playwright/test';

test("审核回归路径", async ({ page }, testInfo) => {
  await test.step("打开审核后的首页", async () => {
    await page.goto("https://example.com/");
  });
  await expect(page).toHaveURL(new RegExp("/"));
});
