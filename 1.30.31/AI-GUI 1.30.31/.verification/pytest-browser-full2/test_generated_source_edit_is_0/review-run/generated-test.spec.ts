import { test, expect } from '@playwright/test';

test("审核回归路径", async ({ page, context }) => {
  let activePage = page;
  await test.step("打开已审核首页", async () => {
    await activePage.goto("https://example.com/");
  });
  await test.step("点击登录", async () => {
    await activePage.getByRole("button", { name: "登录" }).click();
  });
  await expect(page).toHaveURL(new RegExp("/"));
});
