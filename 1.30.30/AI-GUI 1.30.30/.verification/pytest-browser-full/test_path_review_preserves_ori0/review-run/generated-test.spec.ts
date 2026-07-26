import { test, expect } from '@playwright/test';

test("审核回归路径", async ({ page, context }) => {
  let activePage = page;
  await test.step("打开审核后的首页", async () => {
    await activePage.goto("https://example.com/");
  });
  await expect(page).toHaveURL(new RegExp("/"));
});
