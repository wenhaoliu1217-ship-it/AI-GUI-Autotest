import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(new URL('../../\u6e90\u7801/package.json', import.meta.url));
const { chromium } = require('playwright');

const username = process.env.JD_TEST_USERNAME;
const password = process.env.JD_TEST_PASSWORD;
const guiUrl = process.env.GUI_URL || 'http://127.0.0.1:8080';
const hasInjectedCredentials = Boolean(username && password);

const root = dirname(fileURLToPath(import.meta.url));
const productRoot = resolve(root, '..', '..');
const packageRoot = join(productRoot, 'Windows\u4e00\u952e\u8fd0\u884c');
const statusPath = join(root, 'login-status.json');
const resultPath = join(root, 'authenticated-readonly-results.json');
const safeLocation = (value) => {
  try {
    const url = new URL(value);
    return `${url.origin}${url.pathname}`;
  } catch {
    return 'unavailable';
  }
};
const saveStatus = (status, detail = {}) => writeFile(
  statusPath,
  JSON.stringify({ updatedAt: new Date().toISOString(), status, ...detail }, null, 2),
  'utf8',
);
const api = async (path, options = {}) => {
  const response = await fetch(`${guiUrl}${path}`, {
    ...options,
    headers: { 'content-type': 'application/json', ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const safeDetail = typeof body.detail === 'string' && body.detail.length < 500 ? `: ${body.detail}` : '';
    throw new Error(`GUI API ${path} failed with HTTP ${response.status}${safeDetail}`);
  }
  return body;
};

let guiProcess = null;
const guiIsHealthy = async () => {
  try {
    const response = await fetch(`${guiUrl}/api/health`);
    return response.ok;
  } catch {
    return false;
  }
};
if (!(await guiIsHealthy())) {
  const runtimeRoot = join(packageRoot, 'runtime');
  guiProcess = spawn(join(runtimeRoot, 'python', 'python.exe'), [
    '-m', 'uvicorn', 'gui_agent.api.server:app', '--host', '127.0.0.1', '--port', '8080',
  ], {
    cwd: packageRoot,
    windowsHide: true,
    stdio: 'ignore',
    env: {
      ...process.env,
      PYTHONHOME: join(runtimeRoot, 'python'),
      PYTHONPATH: join(packageRoot, 'backend', 'src'),
      PLAYWRIGHT_BROWSERS_PATH: join(runtimeRoot, 'ms-playwright'),
      GUI_STATIC_DIR: join(packageRoot, 'dist'),
      GUI_AGENT_ARTIFACTS: join(packageRoot, 'artifacts'),
      GUI_AGENT_DATA: join(packageRoot, 'data'),
      GUI_API_HOST: '127.0.0.1',
      GUI_API_PORT: '8080',
      GUI_RUNNER_MODE: 'container',
      GUI_RUNNER_IMAGE: 'ai-gui-runner:1.30.31',
      GUI_DOCKER_CLI: 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe',
    },
  });
  for (let attempt = 0; attempt < 120 && !(await guiIsHealthy()); attempt += 1) {
    if (guiProcess.exitCode !== null) throw new Error('The 1.30.31 GUI service exited during startup');
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 500));
  }
  if (!(await guiIsHealthy())) throw new Error('The 1.30.31 GUI service did not become healthy');
}
process.on('exit', () => guiProcess?.kill());

const results = {
  version: '1.30.31',
  scope: 'JD authenticated read-only acceptance',
  startedAt: new Date().toISOString(),
  checks: [],
  blockers: [],
};
const check = (name, passed, detail) => results.checks.push({ name, status: passed ? 'passed' : 'failed', detail });

await api('/api/health');
const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({
    name: 'JD authenticated read-only acceptance 1.30.31',
    baseUrl: 'https://home.jd.com/',
    allowedHosts: [
      'jd.com', 'www.jd.com', 'home.jd.com', 'passport.jd.com', 'order.jd.com',
      'search.jd.com', 'item.jd.com', 'pcitem.jd.com', 'help.jd.com',
    ],
    forbiddenActions: [
      'add to cart', 'favorite', 'follow', 'claim coupon', 'write address',
      'write invoice', 'submit order', 'pay', 'cancel order', 'confirm receipt',
      'review', 'send message', 'request after-sale', 'refund',
    ],
    onboardingLevel: 'L1',
  }),
});
const environment = await api(`/api/projects/${encodeURIComponent(project.id)}/environments`, {
  method: 'POST',
  body: JSON.stringify({
    name: 'JD production authenticated read-only',
    variables: {},
    secretRefs: {
      LOGIN_USERNAME: 'JD_TEST_USERNAME',
      LOGIN_PASSWORD: 'JD_TEST_PASSWORD',
    },
    screenshotMaskSelectors: ['body'],
    viewport: { width: 1440, height: 960 },
  }),
});
check('GUI project safety boundary', true, `projectId=${project.id}; environmentId=${environment.id}; screenshotMask=full-page`);

const browser = await chromium.launch({
  headless: false,
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--start-maximized'],
});
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();

try {
  await saveStatus('opening_login');
  await page.goto('https://passport.jd.com/new/login.aspx', { waitUntil: 'domcontentloaded', timeout: 120000 });
  const accountTab = page.locator('.login-tab-r, [clstag*="login-tab-r"]').first();
  if (await accountTab.count()) await accountTab.click();
  if (hasInjectedCredentials) {
    await page.locator('#loginname').fill(username);
    await page.locator('#nloginpwd').fill(password);
    await saveStatus('credentials_submitted');
    await page.locator('#loginsubmit').click();
  } else {
    await saveStatus('manual_login_required', {
      reason: 'Enter the dedicated test account in the open JD browser. Credentials are not stored by this harness.',
      location: safeLocation(page.url()),
    });
    console.log('MANUAL_LOGIN_REQUIRED');
  }

  const loginDeadline = Date.now() + 15 * 60 * 1000;
  let authenticated = false;
  let manualNoticeWritten = false;
  while (Date.now() < loginDeadline) {
    const cookies = await context.cookies();
    authenticated = cookies.some((cookie) => ['pt_key', 'thor'].includes(cookie.name) && cookie.value);
    if (authenticated && !page.url().includes('passport.jd.com')) break;
    if (!manualNoticeWritten && Date.now() + 14 * 60 * 1000 < loginDeadline) {
      await page.waitForTimeout(10000);
      continue;
    }
    if (!manualNoticeWritten) {
      manualNoticeWritten = true;
      await saveStatus('manual_verification_required', {
        reason: 'Complete any JD slider, SMS, QR, or device confirmation in the open browser.',
        location: safeLocation(page.url()),
      });
      console.log('MANUAL_VERIFICATION_REQUIRED');
    }
    await page.waitForTimeout(2000);
  }
  if (!authenticated) throw new Error('JD login was not completed within the allowed manual-verification window');

  await saveStatus('authenticated', { location: safeLocation(page.url()) });
  check('JD login', true, 'Authenticated browser state established; no credential values were persisted');

  let storageState = await context.storageState();
  storageState = {
    cookies: storageState.cookies.filter((cookie) => {
      const domain = cookie.domain.replace(/^\./, '').toLowerCase();
      return domain === 'jd.com' || domain.endsWith('.jd.com');
    }),
    origins: storageState.origins.filter((origin) => {
      try {
        const host = new URL(origin.origin).hostname.toLowerCase();
        return host === 'jd.com' || host.endsWith('.jd.com');
      } catch {
        return false;
      }
    }),
  };
  const sessionHosts = new Set(storageState.cookies.map((cookie) => cookie.domain.replace(/^\./, '').toLowerCase()));
  for (const origin of storageState.origins) sessionHosts.add(new URL(origin.origin).hostname.toLowerCase());
  const allowedHosts = [...new Set([...(project.allowedHosts || []), ...sessionHosts])];
  await api(`/api/projects/${encodeURIComponent(project.id)}`, {
    method: 'PUT',
    body: JSON.stringify({ allowedHosts }),
  });
  const session = await api(`/api/projects/${encodeURIComponent(project.id)}/session`, {
    method: 'POST',
    body: JSON.stringify({ storageState }),
  });
  storageState = null;
  check('GUI encrypted session import', session.encryption === 'Windows DPAPI / CurrentUser', `cookies=${session.cookieCount}; encryption=${session.encryption}`);

  const scan = await api(`/api/projects/${encodeURIComponent(project.id)}/scan`, {
    method: 'POST',
    body: JSON.stringify({ headless: true, timeoutMs: 120000 }),
  });
  const scanStayedAuthenticated = !String(scan.finalUrl || '').includes('passport.jd.com');
  check('GUI authenticated compatibility scan', scanStayedAuthenticated, `status=${scan.status}; finalLocation=${safeLocation(scan.finalUrl || '')}`);

  const run = await api('/api/runs', {
    method: 'POST',
    body: JSON.stringify({
      projectId: project.id,
      environmentId: environment.id,
      headless: true,
      timeoutMs: 120000,
      asyncExecution: false,
      plan: {
        name: 'JD account home read-only reachability',
        base_url: 'https://home.jd.com',
        role: 'authenticated test account read-only',
        preconditions: [{ description: 'A DPAPI-encrypted JD session is attached to the project' }],
        steps: [{ action: 'navigate', target: '/' }],
        assertions: [{ type: 'url_contains', expected: 'home.jd.com', description: 'The account home does not redirect to login' }],
      },
    }),
  });
  check('GUI isolated Runner account-home check', run.status === 'passed', `runId=${run.run_id || run.id || 'unknown'}; status=${run.status}; completion=${run.completion_reason || ''}`);
} catch (error) {
  let message = String(error?.message || error);
  if (username) message = message.replaceAll(username, '[REDACTED]');
  if (password) message = message.replaceAll(password, '[REDACTED]');
  results.blockers.push({ stage: 'authenticated-readonly', reason: message });
  await saveStatus('blocked', { reason: message, location: safeLocation(page.url()) });
} finally {
  results.endedAt = new Date().toISOString();
  await writeFile(resultPath, JSON.stringify(results, null, 2), 'utf8');
  await context.close();
  await browser.close();
  if (guiProcess) {
    guiProcess.kill();
    await new Promise((resolveDelay) => guiProcess.once('exit', resolveDelay));
  }
}

console.log(JSON.stringify(results, null, 2));
