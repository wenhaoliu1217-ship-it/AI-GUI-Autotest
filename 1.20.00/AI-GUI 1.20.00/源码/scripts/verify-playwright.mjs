import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const baseUrl = process.env.AIGU_WEB_URL || 'http://127.0.0.1:4173';
const targetUrl = process.env.AIGU_TARGET_URL || 'http://127.0.0.1:8765';
const out = path.resolve(process.env.AIGU_VERIFY_OUT || '.verification/real-gui');
await mkdir(out, { recursive: true });

async function verify(width, height) {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width, height } });
  const consoleLines = [];
  page.on('console', (msg) => consoleLines.push(`${msg.type()}: ${msg.text()}`));

  await page.goto(baseUrl);
  await page.waitForLoadState('networkidle');

  await visible(page.getByRole('heading', { name: '总览页' }));
  await visible(page.getByText('真实执行服务已连接'));
  await assertNoPageOverflow(page, width);
  await page.screenshot({ path: path.join(out, `overview-${width}.png`), fullPage: true });

  await page.getByRole('navigation').getByRole('button', { name: '新建测试' }).click();
  await visible(page.getByRole('heading', { name: '新建测试' }));
  await page.getByLabel('测试名称').fill(`真实 GUI 验收 ${width}`);
  await page.getByLabel('目标网站地址').fill(targetUrl);
  await page.getByLabel('自然语言业务流程').fill('在“用户名”输入“admin”；在“密码”输入“admin123”；点击“登录”；确认看到“客户管理”；截图');
  await page.getByLabel('期望结果').fill('确认看到“客户管理”');
  await page.getByRole('button', { name: '生成真实测试计划' }).click();
  await visible(page.getByRole('heading', { name: '可审核执行计划' }));
  await page.getByRole('button', { name: '审核并校验计划' }).click();
  await enabled(page.getByRole('button', { name: '启动真实浏览器测试' }));
  await page.getByRole('button', { name: '启动真实浏览器测试' }).click();

  await visible(page.getByRole('heading', { name: '执行详情' }));
  await visible(page.getByText('成功').first());
  const screenshotButton = page.getByRole('button', { name: '截图' }).last();
  await enabled(screenshotButton);
  const [popup] = await Promise.all([page.waitForEvent('popup'), screenshotButton.click()]);
  const response = await popup.waitForNavigation({ waitUntil: 'load' }).catch(() => null);
  if (response && response.headers()['content-type'] !== 'image/png') {
    throw new Error(`screenshot endpoint returned ${response.headers()['content-type']}`);
  }
  if (!popup.url().includes('/api/artifacts/') || !popup.url().endsWith('.png')) {
    throw new Error(`screenshot popup did not open a PNG artifact: ${popup.url()}`);
  }
  await popup.close();
  await assertNoPageOverflow(page, width);
  await page.screenshot({ path: path.join(out, `run-passed-${width}.png`), fullPage: true });

  await page.getByRole('button', { name: '打开报告详情' }).click();
  await visible(page.getByRole('heading', { name: '断言结果' }));
  await visible(page.getByText('确认看到“客户管理”', { exact: false }).first());
  await page.screenshot({ path: path.join(out, `report-${width}.png`), fullPage: true });

  if (consoleLines.some((line) => line.startsWith('error:'))) {
    throw new Error(`browser console errors:\n${consoleLines.join('\n')}`);
  }
  await writeFile(path.join(out, `console-${width}.log`), consoleLines.join('\n') || 'no console messages\n', 'utf8');
  await browser.close();
}

await verify(1440, 1000);
await verify(390, 1000);
console.log(`validated real API + browser execution + assertions + clickable PNG evidence at ${out}`);

async function visible(locator) {
  await locator.waitFor({ state: 'visible', timeout: 15000 });
}

async function enabled(locator) {
  await locator.waitFor({ state: 'visible', timeout: 15000 });
  for (let i = 0; i < 150; i += 1) {
    if (!(await locator.isDisabled())) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('enabled check failed: button stayed disabled');
}

async function assertNoPageOverflow(page, width) {
  const dimensions = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  if (dimensions.scroll > Math.max(dimensions.client, width)) {
    throw new Error(`page overflow: scrollWidth=${dimensions.scroll}, clientWidth=${dimensions.client}`);
  }
}
