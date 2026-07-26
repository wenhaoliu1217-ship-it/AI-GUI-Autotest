import { test, expect } from '@playwright/test';

test("FR-11 container browser smoke", async ({ page }) => {
  await test.step("open public target", async () => {
    await page.goto("http://example.com/");
  });
  await test.step("capture evidence", async () => {
    await page.screenshot({ fullPage: false });
  });
});
