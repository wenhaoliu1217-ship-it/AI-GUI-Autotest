import { test, expect } from '@playwright/test';

test("Duplicate", async ({ page }, testInfo) => {
  await test.step("步骤 1", async () => {
    await page.goto("http://127.0.0.1:65009/");
  });
  await test.step("步骤 2", async () => {
    await page.getByRole("button", { name: "Delete" }).click();
  });
});
