import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(new URL('../../源码/package.json', import.meta.url));
const { chromium } = require('playwright');

const key = process.env.AI_TEST_KEY;
if (!key) throw new Error('AI_TEST_KEY is required');

const root = fileURLToPath(new URL('.', import.meta.url));
const results = { startedAt: new Date().toISOString(), guiUrl: 'http://127.0.0.1:8080', checks: [], defects: [], blockers: [] };
const add = (name, status, detail, evidence) => results.checks.push({ name, status, detail, evidence });

const browser = await chromium.launch({
  headless: false,
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--start-maximized'],
});
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await context.newPage();

try {
  await page.goto(results.guiUrl, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: /AI 模型设置/ }).click();
  await page.getByLabel('API 协议').selectOption('responses');
  await page.getByLabel('模型名称').fill('gpt-5.6-terra');
  await page.getByLabel('API Base URL').fill('https://api.yunxuan.xyz');
  await page.getByRole('textbox', { name: /API Key/ }).fill(key);
  let aiPayload;
  let aiAttempts = 0;
  for (aiAttempts = 1; aiAttempts <= 3; aiAttempts += 1) {
    const aiResponse = page.waitForResponse((response) => response.url().endsWith('/api/ai/test'), { timeout: 180000 });
    await page.getByRole('button', { name: /测试模型连接/ }).click();
    aiPayload = await (await aiResponse).json();
    if (aiPayload.connected) break;
    if (aiAttempts < 3) await page.waitForTimeout(2000);
  }
  const aiAttemptsUsed = Math.min(aiAttempts, 3);
  await page.screenshot({ path: join(root, '01-ai-connected.png'), fullPage: true });
  add('AI 模型连接', aiPayload.connected ? 'passed' : 'failed', `attempts=${aiAttemptsUsed}; model=${aiPayload.model}; protocol=${aiPayload.protocol}; elapsedMs=${aiPayload.elapsedMs}`, '01-ai-connected.png');
  if (!aiPayload.connected) {
    results.blockers.push({ stage: 'ai-connection', reason: `AI connection failed after ${aiAttemptsUsed} attempts: ${aiPayload.detail || aiPayload.message || 'unknown provider error'}` });
    throw new Error('AI connection unavailable; downstream AI planning was not attempted');
  }

  await page.getByRole('button', { name: /项目接入/ }).click();
  await page.getByLabel('项目名称').fill('京东公开站只读验收');
  await page.getByLabel('Base URL').fill('https://www.jd.com/');
  await page.getByLabel(/允许域名/).fill('www.jd.com\njd.com\nsearch.jd.com\nitem.jd.com\npcitem.jd.com\npassport.jd.com');
  await page.getByLabel(/禁止动作/).fill('登录\n加入购物车\n收藏\n关注\n领券\n写入地址\n写入发票\n提交订单\n支付\n取消订单\n确认收货\n申请售后\n退款\n评价\n发送消息\n商家后台写入');
  await page.getByText('项目业务上下文包', { exact: true }).click();
  await page.getByLabel('业务范围说明').fill('仅测试京东公开页面的浏览、搜索、筛选、排序、分页、商品公开信息、帮助入口和页面兼容性；禁止账户及交易副作用。');
  await page.getByText('电商交易安全配置', { exact: true }).click();
  await page.getByLabel(/启用电商动作前门禁/).check();
  await page.getByLabel('环境层级').selectOption('production_readonly');
  await page.getByLabel(/PII 截图遮罩/).fill('input[type="password"]\n.mobile\n.phone\n.id-card\n.bank-card');
  const createResponse = page.waitForResponse((response) => response.url().endsWith('/api/projects') && response.request().method() === 'POST');
  await page.getByRole('button', { name: /保存项目配置/ }).click();
  const projectPayload = await (await createResponse).json();
  add('正式站只读项目配置', projectPayload.commerceProfile?.environment === 'production_readonly' ? 'passed' : 'failed', `projectId=${projectPayload.id}; allowedHosts=${projectPayload.allowedHosts?.length || 0}`, null);

  const scanButton = page.getByRole('button', { name: /扫描/ });
  const scanResponse = page.waitForResponse((response) => response.url().includes('/scan') && response.request().method() === 'POST', { timeout: 180000 });
  await scanButton.click();
  const scanHttp = await scanResponse;
  const scanPayload = await scanHttp.json();
  await page.screenshot({ path: join(root, '02-jd-compatibility-scan.png'), fullPage: true });
  await writeFile(join(root, 'scan-result.json'), JSON.stringify(scanPayload, null, 2));
  add('京东首页真实兼容扫描', scanHttp.ok() ? 'passed' : 'failed', `status=${scanPayload.status}; title=${scanPayload.title}; pages=${scanPayload.scannedPages?.length || 0}; finalUrl=${scanPayload.finalUrl}`, '02-jd-compatibility-scan.png');

  const newTestButton = page.getByRole('button', { name: /用该地址新建测试/ });
  if (await newTestButton.count()) await newTestButton.click();
  else await page.getByRole('button', { name: /新建测试/ }).first().click();
  await page.getByLabel('目标网站地址').fill('https://www.jd.com/');
  await page.getByLabel(/当前测试目标/).fill('只读打开京东首页；确认页面标题包含“京东”；确认搜索输入框可见；不登录、不填写、不点击任何会产生账户或交易副作用的控件；保存截图。');
  await page.getByLabel(/期望结果/).fill('京东首页成功加载，标题和搜索输入框可见，执行过程中没有账户或交易写操作。');
  await page.getByText('高级设置', { exact: true }).click();
  await page.getByLabel('测试名称').fill('京东首页公开信息只读检查');
  await page.getByLabel('执行角色').fill('未登录访客');
  await page.locator('details.test-advanced-settings textarea').fill('仅访问京东公开正式站；禁止登录、购物车、收藏、下单、支付、售后及个人资料操作。');
  await page.getByRole('button', { name: /AI 模型规划/ }).click();
  let planHttp;
  let planPayload;
  let planAttempts = 0;
  for (planAttempts = 1; planAttempts <= 3; planAttempts += 1) {
    const planResponse = page.waitForResponse((response) => response.url().endsWith('/api/ai/plans/generate'), { timeout: 180000 });
    await page.getByRole('button', { name: /生成.*测试计划/ }).click();
    planHttp = await planResponse;
    planPayload = await planHttp.json();
    if (planHttp.ok()) break;
    if (planAttempts < 3) await page.waitForTimeout(2000);
  }
  const planAttemptsUsed = Math.min(planAttempts, 3);
  await writeFile(join(root, 'ai-plan-result.json'), JSON.stringify(planPayload, null, 2));
  await page.screenshot({ path: join(root, '03-ai-plan.png'), fullPage: true });
  const plan = planPayload.plan || planPayload;
  const planText = JSON.stringify(plan).toLowerCase();
  const unsafeTokens = ['add_cart', 'favorite', 'follow', 'claim_coupon', 'submit_order', 'pay', 'refund', 'request_after_sale', 'send_message', 'write_address', 'write_invoice_profile'];
  const unsafe = unsafeTokens.filter((token) => planText.includes(token));
  add('AI 只读计划生成', planHttp.ok() && unsafe.length === 0 ? 'passed' : 'failed', `attempts=${planAttemptsUsed}; http=${planHttp.status()}; steps=${plan.steps?.length || 0}; assertions=${plan.assertions?.length || 0}; unsafe=${unsafe.join(',') || 'none'}`, '03-ai-plan.png');

  let executablePlanReady = planHttp.ok() && unsafe.length === 0;
  if (!planHttp.ok()) {
    results.blockers.push({ stage: 'ai-plan', reason: `AI plan generation failed after ${planAttemptsUsed} attempts: HTTP ${planHttp.status()}; ${planPayload.detail || 'unknown provider error'}` });
    await page.getByRole('button', { name: /本地规则规划/ }).click();
    const localPlanResponse = page.waitForResponse((response) => response.url().endsWith('/api/plans/generate'), { timeout: 180000 });
    await page.getByRole('button', { name: /生成规则测试计划/ }).click();
    const localPlanHttp = await localPlanResponse;
    const localPlanPayload = await localPlanHttp.json();
    await writeFile(join(root, 'local-plan-unsafe-result.json'), JSON.stringify(localPlanPayload, null, 2));
    await page.screenshot({ path: join(root, '03b-local-plan.png'), fullPage: true });
    const localPlan = localPlanPayload.plan || localPlanPayload;
    const localUnsafeSteps = (localPlan.steps || []).filter((step) => !['navigate', 'screenshot', 'wait_for'].includes(step.action));
    const localWarnings = localPlanPayload.warnings || [];
    add('本地规则原始目标解析', localPlanHttp.ok() && localUnsafeSteps.length === 0 && localWarnings.length === 0 ? 'passed' : 'failed', `http=${localPlanHttp.status()}; warnings=${localWarnings.length}; unsafeActions=${localUnsafeSteps.map((step) => step.action).join(',') || 'none'}`, '03b-local-plan.png');
    if (localUnsafeSteps.length || localWarnings.length) {
      results.defects.push({ stage: 'local-planner-negation', message: `否定句被误解析或未识别：unsafeActions=${localUnsafeSteps.map((step) => step.action).join(',') || 'none'}; warnings=${localWarnings.length}` });
      await page.getByLabel(/当前测试目标/).fill('截图；确认看到“京东”');
      await page.getByLabel(/期望结果/).fill('确认看到“京东”');
      const minimalPlanResponse = page.waitForResponse((response) => response.url().endsWith('/api/plans/generate'), { timeout: 180000 });
      await page.getByRole('button', { name: /生成规则测试计划/ }).click();
      const minimalPlanHttp = await minimalPlanResponse;
      const minimalPlanPayload = await minimalPlanHttp.json();
      await writeFile(join(root, 'local-plan-result.json'), JSON.stringify(minimalPlanPayload, null, 2));
      await page.screenshot({ path: join(root, '03c-local-minimal-plan.png'), fullPage: true });
      const minimalPlan = minimalPlanPayload.plan || minimalPlanPayload;
      const minimalUnsafeSteps = (minimalPlan.steps || []).filter((step) => !['navigate', 'screenshot', 'wait_for'].includes(step.action));
      const minimalWarnings = minimalPlanPayload.warnings || [];
      executablePlanReady = minimalPlanHttp.ok() && minimalUnsafeSteps.length === 0 && minimalWarnings.length === 0 && (minimalPlan.assertions?.length || 0) > 0;
      add('本地规则最小只读降级计划', executablePlanReady ? 'passed' : 'failed', `http=${minimalPlanHttp.status()}; warnings=${minimalWarnings.length}; steps=${minimalPlan.steps?.length || 0}; assertions=${minimalPlan.assertions?.length || 0}; unsafeActions=${minimalUnsafeSteps.map((step) => step.action).join(',') || 'none'}`, '03c-local-minimal-plan.png');
    } else {
      executablePlanReady = true;
    }
  } else if (unsafe.length) {
    results.blockers.push({ stage: 'plan-review', reason: `AI plan contained unsafe actions: ${unsafe.join(', ')}` });
  }

  if (executablePlanReady) {
    const validateResponse = page.waitForResponse((response) => response.url().endsWith('/api/plans/validate'));
    await page.getByRole('button', { name: /审核并校验计划/ }).click();
    const validated = await (await validateResponse).json();
    add('计划人工审核门禁', validated.valid ? 'passed' : 'failed', `valid=${validated.valid}`, null);
    if (validated.valid) {
      const runResponse = page.waitForResponse((response) => response.url().endsWith('/api/runs') && response.request().method() === 'POST', { timeout: 180000 });
      await page.getByRole('button', { name: /启动真实浏览器测试/ }).click();
      const runStart = await (await runResponse).json();
      const runId = runStart.run_id;
      let run = runStart;
      for (let attempt = 0; attempt < 180; attempt += 1) {
        await page.waitForTimeout(1000);
        const response = await page.request.get(`${results.guiUrl}/api/runs/${encodeURIComponent(runId)}`);
        run = await response.json();
        if (!['queued', 'running', 'pending_confirmation'].includes(run.status)) break;
      }
      await writeFile(join(root, 'run-result.json'), JSON.stringify(run, null, 2));
      await page.screenshot({ path: join(root, '04-run-result.png'), fullPage: true });
      add('真实浏览器只读执行', run.status === 'passed' ? 'passed' : 'failed', `runId=${runId}; status=${run.status}; completion=${run.completion_reason || ''}`, '04-run-result.png');
    }
  }
} catch (error) {
  results.defects.push({ stage: 'automation', message: String(error?.stack || error) });
  await page.screenshot({ path: join(root, 'error.png'), fullPage: true }).catch(() => {});
} finally {
  results.endedAt = new Date().toISOString();
  await writeFile(join(root, 'acceptance-results.json'), JSON.stringify(results, null, 2));
  await page.getByRole('button', { name: /清除密钥/ }).click().catch(() => {});
  await context.close();
  await browser.close();
}

console.log(JSON.stringify(results, null, 2));
