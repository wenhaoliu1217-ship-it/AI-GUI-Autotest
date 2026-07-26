import { test, expect } from '@playwright/test';

test("FR-11 authorized private container browser", async ({ page }) => {
  await test.step("open authorized target", async () => {
    await page.goto("http://172.17.0.2:18992/");
  });
  await test.step("capture evidence", async () => {
    await page.screenshot({ fullPage: false });
  });
});
