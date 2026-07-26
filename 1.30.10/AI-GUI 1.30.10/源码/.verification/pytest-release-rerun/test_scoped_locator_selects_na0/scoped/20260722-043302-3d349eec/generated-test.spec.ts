import { test, expect } from '@playwright/test';

test("Scoped", async ({ page }, testInfo) => {
  await test.step("步骤 1", async () => {
    await page.goto("http://127.0.0.1:52833/");
  });
  await test.step("步骤 2", async () => {
    await page.locator("[data-object-id=\"agent-2\"]").filter({ hasText: "E2E_Beta" }).getByRole("button", { name: "Delete" }).click();
  });
});
