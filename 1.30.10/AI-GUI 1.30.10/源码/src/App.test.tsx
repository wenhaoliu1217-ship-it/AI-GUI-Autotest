import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { api } from './services/api';

const plan = {
  name: '管理员登录验收',
  base_url: 'http://127.0.0.1:8765',
  role: '测试工程师',
  preconditions: [],
  steps: [
    { action: 'navigate', target: '/', description: '打开目标网站' },
    { action: 'click', locator: { role: 'button', name: '登录' }, description: '点击登录' }
  ],
  assertions: [{ type: 'visible', locator: { text: '客户管理' }, description: '确认客户管理可见' }]
};

const backendRun = {
  run_id: '20260719-120000-abcd1234',
  plan_name: plan.name,
  role: plan.role,
  base_url_summary: plan.base_url,
  status: 'passed',
  started_at: '2026-07-19T12:00:00+08:00',
  ended_at: '2026-07-19T12:00:01+08:00',
  steps: [{
    index: 1,
    action: 'navigate',
    description: '打开目标网站',
    target_summary: '打开目标网站 -> /',
    status: 'passed',
    started_at: '2026-07-19T12:00:00+08:00',
    ended_at: '2026-07-19T12:00:01+08:00',
    screenshot: 'screenshots/step-1-after.png',
    execution_mode: 'locator',
    stability_level: 'A',
    stability_reason: '确定性 Playwright 动作',
    before: { url: 'about:blank', title: '', screenshot: 'screenshots/step-1-before.png', dom_summary: [], accessibility_summary: '', console_errors: [], page_errors: [], failed_requests: [] },
    after: { url: plan.base_url, title: '演示站', screenshot: 'screenshots/step-1-after.png', dom_summary: ['button | text=登录'], accessibility_summary: '- button "登录"', console_errors: [], page_errors: [], failed_requests: [] }
  }],
  assertions: [{ index: 1, type: 'visible', description: '确认客户管理可见', status: 'passed', actual_summary: 'visible' }],
  reproduction_steps: ['打开目标网站 -> /'],
  cause_hints: [],
  artifact_base_url: '/api/artifacts/20260719-120000-abcd1234',
  runner_isolation: {
    mode: 'spawn_process', process_id: 4321, windows_job_assigned: true,
    memory_limit_mb: 2048, network_policy: 'playwright_request_guard', forced_termination: false
  }
};

const project = {
  id: 'project-1', name: '企业测试站', baseUrl: 'https://example.com', allowedHosts: ['example.com'], forbiddenActions: ['支付'], allowPrivateNetwork: false, onboardingLevel: 'L0',
  businessContext: { description: '', terminology: {}, objectTypes: [], stateModels: {}, exampleGoals: [], operatingBoundaries: [], allowedActions: [], bridgeCapabilities: [], bridgeSemanticTargets: {} },
  limits: { maxSteps: 50, timeoutSeconds: 600, maxModelCalls: 20 }, createdAt: '2026-07-20T00:00:00Z', updatedAt: '2026-07-20T00:00:00Z'
};

const compatibilityReport = {
  projectId: 'project-1', generatedAt: '2026-07-20T00:02:00Z', onboardingLevel: 'L0', recommendedOnboardingLevel: 'L2',
  requestedUrl: 'https://example.com', finalUrl: 'https://example.com/dashboard', title: '企业工作台', status: 'attention',
  pageSummary: { buttons: 5, links: 3, inputs: 2, selects: 0, textareas: 0, canvases: 0, webglRegions: 0, iframes: 0, crossOriginIframes: 0, fileInputs: 0, shadowRoots: 0, contentEditors: 0, unlabeledControls: 1, duplicateIds: 0, loadingSignals: 1 },
  candidateLocators: { testIds: 0, labels: 2, roles: 3, ariaNames: 2, namedControls: 9 },
  capabilities: ['标准 DOM', '主要导航只读遍历'], thirdPartyHosts: [], consoleErrors: [], failedRequests: [],
  blockedAreas: [], recommendations: ['为无可访问名称的关键控件补充 aria-label'],
  suggestedScenarios: ['验证主要导航入口“客户管理”可见且可访问'],
  scannedPages: [{ url: 'https://example.com/dashboard', title: '企业工作台', pageType: '导航/工作台', summary: {}, candidateLocators: {}, headings: ['工作台'], redirectChain: ['https://example.com'] }],
  navigationEntries: ['客户管理', '操作记录'], authenticationSignals: ['扫描已加载保存的登录态，未发现公开登录表单'],
  asyncPatterns: ['观察到 2 个 Fetch/XHR 资源，页面存在异步数据加载'], stableAreas: ['9 个具名 DOM 控件可稳定定位'],
  visualAreas: [], adaptiveAreas: ['1 个控件缺少可访问名称'], manualAreas: [],
  recommendedConfig: { allowedHosts: ['example.com'], ignoreRules: [], viewport: { width: 1440, height: 960 }, limits: project.limits },
  sampleScenarioId: 'scenario-scan', sampleScenarioCreated: true
};

let historyRuns: typeof backendRun[] = [];
let savedEnvironments: any[] = [];
let savedScenarios: any[] = [];
let savedTestFiles: any[] = [];
let startRunResponse: typeof backendRun & { completion_reason?: string } = backendRun;
let detailRunResponse: any = backendRun;
let reviewState: any = {
  available: true,
  steps: plan.steps.map((step, index) => ({ sourceIndex: index + 1, retained: true, step })),
  history: []
};

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
}

beforeEach(() => {
  historyRuns = [];
  savedEnvironments = [];
  savedScenarios = [];
  savedTestFiles = [];
  startRunResponse = backendRun;
  detailRunResponse = backendRun;
  reviewState = {
    available: true,
    steps: plan.steps.map((step, index) => ({ sourceIndex: index + 1, retained: true, step })),
    history: []
  };
  vi.stubGlobal('open', vi.fn());
  vi.stubGlobal('confirm', vi.fn(() => true));
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } });
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/api/health')) return jsonResponse({ status: 'ok', mode: 'real', engine: 'playwright-chromium', planner: 'deterministic-rules' });
    if (url.endsWith('/api/projects') && init?.method === 'POST') return jsonResponse({ ...project, ...JSON.parse(String(init.body)) });
    if (url.endsWith('/api/projects/project-1') && init?.method === 'PUT') return jsonResponse({ ...project, ...JSON.parse(String(init.body)), updatedAt: '2026-07-20T00:03:00Z' });
    if (url.includes('/api/projects/project-1/test-files')) {
      if (init?.method === 'POST') {
        const parsed = new URL(url, 'http://localhost');
        const item = {
          id: 'file-0123456789ab', projectId: 'project-1', fileName: parsed.searchParams.get('fileName'),
          size: (init.body as File).size, sha256: 'a'.repeat(64), mimeType: 'application/json', extension: '.json',
          validationProfile: parsed.searchParams.get('validationProfile'), validationStatus: 'valid', validationErrors: [],
          expectedResult: parsed.searchParams.get('expectedResult'), createdAt: '2026-07-22T00:00:00Z'
        };
        savedTestFiles = [item]; return jsonResponse(item);
      }
      if (init?.method === 'DELETE') { savedTestFiles = []; return jsonResponse({ deleted: true, id: 'file-0123456789ab' }); }
      return jsonResponse(savedTestFiles);
    }
    if (url.endsWith('/api/projects/project-1/scan') && init?.method === 'POST') {
      savedScenarios = [{
        id: 'scenario-scan', projectId: 'project-1', name: '企业测试站 扫描示例', preconditions: ['目标测试环境可访问'],
        goal: compatibilityReport.suggestedScenarios[0], testData: {}, expectedResults: ['企业工作台可见'], forbiddenActions: ['支付'],
        createdAt: '2026-07-20T00:02:00Z', updatedAt: '2026-07-20T00:02:00Z'
      }];
      return jsonResponse(compatibilityReport);
    }
    if (url.endsWith('/api/projects')) return jsonResponse([]);
    if (url.includes('/api/projects/') && url.includes('/environments')) {
      if (init?.method === 'POST') {
        const payload = JSON.parse(String(init.body));
        const environment = { ...payload, id: 'environment-1', projectId: 'project-1', createdAt: '2026-07-20T00:00:00Z', updatedAt: '2026-07-20T00:00:00Z' };
        savedEnvironments = [environment, ...savedEnvironments];
        return jsonResponse(environment);
      }
      if (init?.method === 'PUT') {
        const payload = JSON.parse(String(init.body));
        const environment = { ...savedEnvironments[0], ...payload, updatedAt: '2026-07-20T00:01:00Z' };
        savedEnvironments = [environment];
        return jsonResponse(environment);
      }
      return jsonResponse(savedEnvironments);
    }
    if (url.includes('/api/projects/') && url.includes('/scenarios')) {
      if (init?.method === 'POST') {
        const payload = JSON.parse(String(init.body));
        const scenario = { ...payload, id: 'scenario-1', projectId: 'project-1', createdAt: '2026-07-20T00:00:00Z', updatedAt: '2026-07-20T00:00:00Z' };
        savedScenarios = [scenario, ...savedScenarios];
        return jsonResponse(scenario);
      }
      if (init?.method === 'PUT') {
        const payload = JSON.parse(String(init.body));
        const scenario = { ...savedScenarios[0], ...payload, updatedAt: '2026-07-20T00:01:00Z' };
        savedScenarios = [scenario];
        return jsonResponse(scenario);
      }
      return jsonResponse(savedScenarios);
    }
    if (url.includes('/api/projects/') && url.endsWith('/session') && init?.method === 'POST') return jsonResponse({ projectId: 'project-1', importedAt: '2026-07-20T00:00:00Z', cookieCount: 1, originCount: 0, domains: ['example.com'], expiresAt: '2033-05-18T00:00:00Z', expiryStatus: 'active', expiredCookieCount: 0, encryption: 'Windows DPAPI / CurrentUser' });
    if (url.endsWith('/api/ai/test')) return jsonResponse({ connected: true, model: 'gpt-5.6-terra', protocol: 'responses', elapsedMs: 123 });
    if (url.endsWith('/api/ai/plans/generate')) return jsonResponse({ plan, warnings: [], planner: 'ai:responses:gpt-5.6-terra' });
    if (url.endsWith('/api/plans/generate')) return jsonResponse({ plan, warnings: [], planner: 'deterministic-rules' });
    if (url.endsWith('/api/plans/validate')) return jsonResponse({ valid: true, plan });
    if (url.endsWith('/api/runs') && init?.method === 'POST') return jsonResponse(startRunResponse);
    if (url.endsWith('/api/agent-runs') && init?.method === 'POST') return jsonResponse(startRunResponse);
    if (url.endsWith('/cancel') && init?.method === 'POST') return jsonResponse({ ...startRunResponse, completion_reason: 'cancellation_requested' });
    if (url.endsWith('/confirmation') && init?.method === 'POST') {
      const payload = JSON.parse(String(init.body));
      return jsonResponse({
        ...startRunResponse,
        status: payload.decision === 'approved' ? 'running' : 'cancelled',
        pending_confirmation: null,
        confirmation_history: [{
          ...(startRunResponse as any).pending_confirmation,
          decision: payload.decision,
          actor: payload.actor,
          decided_at: '2026-07-21T04:00:01Z'
        }]
      });
    }
    if (url.includes('/findings/') && init?.method === 'PATCH') {
      const payload = JSON.parse(String(init.body));
      const finding = detailRunResponse.findings[0];
      Object.assign(finding, { title: payload.title, severity: payload.severity, expected_result: payload.expectedResult, review_status: payload.status });
      finding.review_history = [...(finding.review_history || []), { timestamp: '2026-07-20T09:30:00Z', actor: 'local-user', changedFields: ['title', 'severity', 'expected_result', 'review_status'] }];
      detailRunResponse = { ...detailRunResponse, review_summary: { disposition: payload.status === 'confirmed' ? 'issues_found' : payload.status === 'rejected' ? 'all_rejected' : 'pending_confirmation', pending: payload.status === 'pending_review' ? 1 : 0, confirmed: payload.status === 'confirmed' ? 1 : 0, rejected: payload.status === 'rejected' ? 1 : 0, total: 1 } };
      return jsonResponse(finding);
    }
    if (url.endsWith('/review') && init?.method === 'PATCH') {
      const payload = JSON.parse(String(init.body));
      reviewState = { ...reviewState, steps: payload.steps, history: [...reviewState.history, { timestamp: '2026-07-20T09:31:00Z', actor: 'local-user', changes: [{ sourceIndex: 1, action: 'edited' }, { sourceIndex: 2, action: 'removed' }], retainedSourceIndexes: [1] }] };
      detailRunResponse = { ...detailRunResponse, generated_test: { source_path: 'generated-test.spec.ts', source: 'test.step("打开审核后的首页")', stability_level: 'A', supported_replay_modes: ['stable'], ci_eligible: true, ci_recommendation: '可作为 CI 候选' } };
      return jsonResponse(reviewState);
    }
    if (url.endsWith('/review')) return jsonResponse(reviewState);
    if (url.endsWith('/generated-test') && init?.method === 'PATCH') {
      const payload = JSON.parse(String(init.body));
      const previous = detailRunResponse.generated_test;
      detailRunResponse = { ...detailRunResponse, generated_test: { ...previous, source: payload.source, source_revision: (previous.source_revision || 1) + 1, source_review_history: [{ timestamp: '2026-07-20T09:32:00Z', actor: 'local-user', action: 'manual_source_edit', revision: 2, beforeSha256: 'before', afterSha256: 'after' }] } };
      return jsonResponse(detailRunResponse.generated_test);
    }
    if (url.endsWith('/api/runs/delete') && init?.method === 'POST') {
      const { runIds } = JSON.parse(String(init.body)) as { runIds: string[] };
      historyRuns = historyRuns.filter((item) => !runIds.includes(item.run_id));
      return jsonResponse({ deleted: runIds, count: runIds.length, auditId: 'deletion-test' });
    }
    if (url.endsWith('/api/runs/cleanup') && init?.method === 'POST') {
      const deleted = historyRuns.filter((item) => item.run_id.includes('expired')).map((item) => item.run_id);
      historyRuns = historyRuns.filter((item) => !deleted.includes(item.run_id));
      return jsonResponse({ deleted, count: deleted.length, skippedActive: [], auditId: deleted.length ? 'deletion-cleanup' : null });
    }
    if (url.endsWith('/api/runs')) return jsonResponse(historyRuns);
    if (url.includes('/api/runs/')) return jsonResponse(detailRunResponse);
    return jsonResponse({ detail: 'not found' }, 404);
  }));
});

describe('App', () => {
  it('sends selected project and environment when generating an environment-backed plan', async () => {
    await api.generatePlan({
      name: '环境地址规划', targetUrl: '${TEST_BASE_URL}', flow: '确认看到“京彩OPC”',
      role: '测试工程师', preconditions: '测试环境已启动', expectation: '确认看到“京彩OPC”',
      testData: {}, forbiddenActions: []
    }, 'project-1', 'environment-1');

    const request = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/plans/generate'));
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
      targetUrl: '${TEST_BASE_URL}', projectId: 'project-1', environmentId: 'environment-1'
    });
  });

  it('shows real execution mode without fabricated history', async () => {
    render(<App />);
    expect(screen.getByText('真实运行记录')).toBeInTheDocument();
    expect(await screen.findByText('真实执行服务已连接')).toBeInTheDocument();
    expect(screen.getByText('尚无真实运行记录。Mock 样例已移除。')).toBeInTheDocument();
    expect(screen.queryByText('制造一个失败结果')).not.toBeInTheDocument();
  });

  it('requires a reviewed backend plan before running', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getAllByRole('button', { name: /新建测试/ })[0]);
    expect(screen.getByRole('button', { name: /启动真实浏览器测试/ })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    expect(await screen.findByText('可审核执行计划')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /审核并校验计划/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /启动真实浏览器测试/ })).toBeEnabled());
  });

  it('uses a goal-first default form and keeps effective optional fields in advanced settings', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getAllByRole('button', { name: /新建测试/ })[0]);

    expect(screen.getByLabelText('目标网站地址')).toBeVisible();
    expect(screen.getByLabelText('当前测试目标')).toBeVisible();
    expect(screen.getByLabelText('期望结果')).toBeVisible();
    expect(screen.getByLabelText('执行角色')).not.toBeVisible();
    expect(screen.getByText('可复用场景库（可选）')).toBeVisible();
    await user.click(screen.getByText('高级设置'));
    expect(screen.getByLabelText('执行角色')).toBeVisible();
    expect(screen.getByLabelText('前置条件')).toBeVisible();
  });

  it('configures AI in memory and enables AI planning after a real connection test', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: /AI 模型设置/ }));
    const keyInput = screen.getByLabelText('API Key');
    expect(keyInput).toHaveAttribute('type', 'password');
    await user.type(keyInput, 'new-test-key');
    await user.click(screen.getByRole('button', { name: /测试模型连接/ }));
    expect(await screen.findByText(/连接成功：gpt-5.6-terra/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /使用 AI 新建测试/ })).toBeEnabled();
    const calls = vi.mocked(fetch).mock.calls;
    const request = calls.find(([url]) => String(url).endsWith('/api/ai/test'));
    expect(request?.[1]?.body).toContain('new-test-key');
    expect(document.body.textContent).not.toContain('new-test-key');
  });

  it('opens a real screenshot artifact from a completed run', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getAllByRole('button', { name: /新建测试/ })[0]);
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await screen.findByText('可审核执行计划');
    await user.click(screen.getByRole('button', { name: /审核并校验计划/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /启动真实浏览器测试/ })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));
    const screenshot = await screen.findByRole('link', { name: '查看截图' });
    expect(screenshot).toHaveAttribute('href', expect.stringContaining('/api/artifacts/'));
    expect(screenshot).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('link', { name: '动作前' })).toHaveAttribute('href', expect.stringContaining('step-1-before.png'));
    expect(screen.getByText('隔离进程 · Job 已绑定 · 2048 MB')).toBeInTheDocument();
  });

  it('shows Docker isolation without mislabeling it as a Windows Job', async () => {
    startRunResponse = {
      ...backendRun,
      runner_isolation: {
        mode: 'docker_container', container_name: 'ai-gui-run-1', image: 'ai-gui-runner:1.30.10',
        root_filesystem_read_only: true, memory_limit_mb: 2048,
        container_private_network_allowed: false,
        network_policy: 'container_egress_firewall+playwright_request_guard', forced_termination: false
      }
    } as any;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getAllByRole('button', { name: /新建测试/ })[0]);
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await user.click(await screen.findByRole('button', { name: /审核并校验计划/ }));
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));

    expect(await screen.findByText('容器隔离 · ai-gui-runner:1.30.10 · 根目录只读 · 默认私网阻断 · 2048 MB')).toBeInTheDocument();
    expect(screen.queryByText(/Job 未绑定/)).not.toBeInTheDocument();
  });

  it('starts an asynchronous run and exposes the real cancel request', async () => {
    startRunResponse = { ...backendRun, status: 'queued', steps: [], assertions: [], completion_reason: 'queued' };
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getAllByRole('button', { name: /新建测试/ })[0]);
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await user.click(await screen.findByRole('button', { name: /审核并校验计划/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /启动真实浏览器测试/ })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));
    const stop = await screen.findByRole('button', { name: /停止执行/ });
    expect(stop).toBeEnabled();
    await user.click(stop);

    expect(await screen.findByText(/已请求停止执行/)).toBeInTheDocument();
    const startCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/runs') && init?.method === 'POST');
    expect(startCall?.[1]?.body).toContain('"asyncExecution":true');
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/cancel'))).toBe(true);
  });

  it('shows and approves a dangerous action with its single-use confirmation id', async () => {
    startRunResponse = {
      ...backendRun,
      status: 'pending_confirmation',
      steps: [],
      assertions: [],
      completion_reason: 'dangerous_action_pending_confirmation',
      pending_confirmation: {
        id: 'confirmation-test-1', step_index: 2, action: 'click', target: '删除客户',
        rule: '删除', requested_at: '2026-07-21T04:00:00Z'
      },
      confirmation_history: []
    } as any;
    detailRunResponse = startRunResponse;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getAllByRole('button', { name: /新建测试/ })[0]);
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await user.click(await screen.findByRole('button', { name: /审核并校验计划/ }));
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));

    expect(await screen.findByRole('alert', { name: '危险动作确认' })).toHaveTextContent('删除客户');
    await user.click(screen.getByRole('button', { name: /单次批准/ }));

    expect(await screen.findByText(/该危险动作已获单次批准/)).toBeInTheDocument();
    const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/confirmation') && init?.method === 'POST');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ confirmationId: 'confirmation-test-1', decision: 'approved', actor: 'local_user' });
  });

  it('starts explicit stepwise Agent exploration with the reviewed scenario goal', async () => {
    startRunResponse = { ...backendRun, status: 'queued', steps: [], assertions: [], completion_reason: 'queued' };
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: /AI 模型设置/ }));
    await user.type(screen.getByLabelText('API Key'), 'agent-test-key');
    await user.click(screen.getByRole('button', { name: /测试模型连接/ }));
    await screen.findByText(/连接成功：gpt-5.6-terra/);
    await user.click(screen.getByRole('button', { name: /使用 AI 新建测试/ }));
    await user.click(screen.getByRole('button', { name: /AI 生成测试计划/ }));
    await user.click(await screen.findByRole('button', { name: /审核并校验计划/ }));
    expect(screen.queryByLabelText(/启用截图视觉 fallback/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '逐步 Agent 探索' }));
    const visualFallback = screen.getByLabelText(/启用截图视觉 fallback/);
    expect(visualFallback).not.toBeChecked();
    await user.click(visualFallback);
    await user.click(screen.getByRole('button', { name: /启动逐步 Agent 探索/ }));

    const call = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/agent-runs'));
    expect(call?.[1]?.body).toContain('"goal"');
    expect(call?.[1]?.body).toContain(plan.name);
    expect(call?.[1]?.body).toContain('agent-test-key');
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ enableVisualFallback: true });
  });

  it('saves an enterprise project configuration before scanning', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: /项目接入/ }));
    await user.type(screen.getByLabelText('项目名称'), '企业测试站');
    await user.type(screen.getByLabelText('Base URL'), 'https://example.com');
    await user.click(screen.getByLabelText('允许访问受控私网／本机目标'));
    await user.click(screen.getByText('项目业务上下文包'));
    await user.type(screen.getByLabelText('业务范围说明'), '客户运营后台');
    fireEvent.change(screen.getByLabelText('业务术语（JSON）'), { target: { value: '{"客户池":"未分配客户"}' } });
    await user.type(screen.getByLabelText('业务对象（每行一项）'), '客户');
    fireEvent.change(screen.getByLabelText('状态模型（JSON）'), { target: { value: '{"客户":["待分配","跟进中"]}' } });
    await user.type(screen.getByLabelText('操作边界（每行一项）'), '只操作 QA 租户');
    await user.type(screen.getByLabelText('允许操作（每行一项）'), '查询客户');
    await user.type(screen.getByLabelText('Bridge 能力（每行一项）'), '读取选中对象');
    fireEvent.change(screen.getByLabelText('Bridge 语义目标（JSON）'), { target: { value: '{"customer.primary":"主客户对象"}' } });
    await user.click(screen.getByRole('button', { name: /保存项目配置/ }));
    expect(await screen.findByText(/项目“企业测试站”已保存/)).toBeInTheDocument();
    const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/projects') && init?.method === 'POST');
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      baseUrl: 'https://example.com', allowPrivateNetwork: true,
      businessContext: {
        description: '客户运营后台', terminology: { 客户池: '未分配客户' },
        objectTypes: ['客户'], stateModels: { 客户: ['待分配', '跟进中'] },
        operatingBoundaries: ['只操作QA租户'], allowedActions: ['查询客户'],
        bridgeCapabilities: ['读取选中对象'], bridgeSemanticTargets: { 'customer.primary': '主客户对象' }
      }
    });
    await user.clear(screen.getByLabelText('项目名称'));
    await user.type(screen.getByLabelText('项目名称'), '企业测试站更新');
    await user.click(screen.getByRole('button', { name: '保存项目修改' }));
    const updateCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/projects/project-1') && init?.method === 'PUT');
    expect(JSON.parse(String(updateCall?.[1]?.body))).toMatchObject({ name: '企业测试站更新', baseUrl: 'https://example.com', allowPrivateNetwork: true });
  });

  it('shows the deep compatibility profile and refreshes the generated sample scenario', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: /项目接入/ }));
    await user.type(screen.getByLabelText('项目名称'), '企业测试站');
    await user.type(screen.getByLabelText('Base URL'), 'https://example.com');
    await user.click(screen.getByRole('button', { name: /保存项目配置/ }));
    await user.click(screen.getByRole('button', { name: /启动真实只读扫描/ }));

    expect(await screen.findByLabelText('扫描示例场景')).toHaveTextContent('已自动创建可编辑示例场景');
    expect(screen.getByLabelText('建议接入级别')).toHaveTextContent('建议级别：L2');
    expect(screen.getByRole('heading', { name: '稳定可测区域' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '自适应区域' })).toBeInTheDocument();
    expect(screen.getByText(/导航\/工作台 · 企业工作台/)).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '扫描建议配置' })).toHaveTextContent('1440×960');
    expect(savedScenarios).toHaveLength(1);
  });

  it('persists account profiles and sends the selected account for low-risk probing', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: /项目接入/ }));
    await user.type(screen.getByLabelText('项目名称'), '多角色测试站');
    await user.type(screen.getByLabelText('Base URL'), 'https://example.com');
    await user.click(screen.getByText('项目业务上下文包'));
    fireEvent.change(screen.getByLabelText('账号角色矩阵（JSON）'), { target: { value: JSON.stringify([
      { id: 'admin', name: '管理员账号', role: 'admin', loginMethod: 'interactive', credentialRefs: {}, permissions: ['manage'] },
      { id: 'viewer', name: '只读账号', role: 'viewer', loginMethod: 'interactive', credentialRefs: {}, permissions: ['read'] }
    ]) } });
    await user.click(screen.getByRole('button', { name: /保存项目配置/ }));

    const createCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/projects') && init?.method === 'POST');
    expect(JSON.parse(String(createCall?.[1]?.body)).accountProfiles).toMatchObject([{ id: 'admin' }, { id: 'viewer' }]);
    await user.selectOptions(screen.getByLabelText('当前账号'), 'viewer');
    await user.click(screen.getByRole('button', { name: /启动低风险 UI 探测/ }));

    const scanCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/projects/project-1/scan') && init?.method === 'POST');
    expect(JSON.parse(String(scanCall?.[1]?.body))).toMatchObject({ mode: 'low_risk', accountId: 'viewer' });
  });

  it('edits a stable locator with a business-scoped row', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getAllByRole('button', { name: /新建测试/ })[0]);
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await screen.findByText('可审核执行计划');

    await user.selectOptions(screen.getByLabelText('步骤 2 定位方式'), 'data-object-id');
    await user.clear(screen.getByLabelText('步骤 2 定位值'));
    await user.type(screen.getByLabelText('步骤 2 定位值'), 'agent-delete');
    await user.selectOptions(screen.getByLabelText('步骤 2 作用域'), 'row');
    await user.selectOptions(screen.getByLabelText('步骤 2 作用域定位方式'), 'data-object-id');
    await user.type(screen.getByLabelText('步骤 2 作用域定位值'), 'agent-2');
    await user.type(screen.getByLabelText('步骤 2 业务对象身份'), 'E2E_Beta');
    await user.click(screen.getByRole('button', { name: /审核并校验计划/ }));

    const validateCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/plans/validate'));
    const submittedPlan = JSON.parse(String(validateCall?.[1]?.body)).plan;
    expect(submittedPlan.steps[1].locator).toEqual({
      attribute: { name: 'data-object-id', value: 'agent-delete' },
      scope: { kind: 'row', locator: { attribute: { name: 'data-object-id', value: 'agent-2' } }, identity: 'E2E_Beta' }
    });
  });

  it('registers a raw project file and binds it to an upload plan step', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: /项目接入/ }));
    await user.type(screen.getByLabelText('项目名称'), '文件测试站');
    await user.type(screen.getByLabelText('Base URL'), 'https://example.com');
    await user.click(screen.getByRole('button', { name: /保存项目配置/ }));

    const file = new File(['{"name":"E2E_Agent"}'], 'E2E_agent.json', { type: 'application/json' });
    await user.upload(screen.getByLabelText('选择项目测试文件'), file);
    await user.click(screen.getByRole('button', { name: /登记文件并计算哈希/ }));
    expect(await screen.findByText('E2E_agent.json')).toBeInTheDocument();
    const uploadCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).includes('/test-files?') && init?.method === 'POST');
    expect(uploadCall?.[1]?.body).toBe(file);
    expect(String(uploadCall?.[0])).toContain('validationProfile=auto');

    await user.click(screen.getByRole('button', { name: /用该地址新建测试/ }));
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await screen.findByText('可审核执行计划');
    await user.selectOptions(screen.getByLabelText('步骤 2 动作'), 'upload');
    expect(screen.getByLabelText('步骤 2 登记文件')).toHaveValue('file-0123456789ab');
    await user.clear(screen.getByLabelText('步骤 2 文件业务对象'));
    await user.type(screen.getByLabelText('步骤 2 文件业务对象'), 'E2E_Agent');
    await user.click(screen.getByRole('button', { name: /审核并校验计划/ }));

    const validateCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/plans/validate'));
    const submitted = JSON.parse(String(validateCall?.[1]?.body)).plan.steps[1];
    expect(submitted).toMatchObject({ action: 'upload', file_id: 'file-0123456789ab', expected_file_validity: 'valid', business_object_name: 'E2E_Agent' });
  });

  it('creates, edits, selects, and executes a persisted project environment', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: /项目接入/ }));
    await user.type(screen.getByLabelText('项目名称'), '企业测试站');
    await user.type(screen.getByLabelText('Base URL'), 'https://example.com');
    await user.click(screen.getByRole('button', { name: /保存项目配置/ }));

    await user.type(screen.getByLabelText('环境名称'), 'QA 环境');
    fireEvent.change(screen.getByLabelText('普通环境变量（JSON）'), { target: { value: '{"TEST_BASE_URL":"https://example.com","TENANT":"qa"}' } });
    fireEvent.change(screen.getByLabelText('密钥引用（JSON）'), { target: { value: '{"LOGIN_PASSWORD":"QA_LOGIN_PASSWORD"}' } });
    await user.type(screen.getByLabelText(/网络忽略规则/), '**/analytics/**');
    fireEvent.change(screen.getByLabelText(/截图隐私遮罩 CSS 选择器/), { target: { value: '.customer-name\n[data-private=true]' } });
    await user.clear(screen.getByLabelText('Viewport 宽度'));
    await user.type(screen.getByLabelText('Viewport 宽度'), '1280');
    await user.click(screen.getByRole('button', { name: /保存新环境/ }));

    expect(await screen.findByText(/测试环境“QA 环境”已保存并选为当前运行环境/)).toBeInTheDocument();
    const createCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/environments') && init?.method === 'POST');
    expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({
      name: 'QA 环境', variables: { TEST_BASE_URL: 'https://example.com', TENANT: 'qa' },
      secretRefs: { LOGIN_PASSWORD: 'QA_LOGIN_PASSWORD' }, ignoreRules: ['**/analytics/**'],
      screenshotMaskSelectors: ['.customer-name', '[data-private=true]'],
      viewport: { width: 1280, height: 960 }
    });

    await user.clear(screen.getByLabelText('工件保留天数'));
    await user.type(screen.getByLabelText('工件保留天数'), '14');
    await user.click(screen.getByRole('button', { name: /保存环境修改/ }));
    expect(await screen.findByText(/测试环境“QA 环境”已更新并选为当前运行环境/)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([url, init]) => String(url).endsWith('/environments/environment-1') && init?.method === 'PUT')).toBe(true);

    await user.click(screen.getByRole('button', { name: /用该地址新建测试/ }));
    expect(screen.getByLabelText('运行环境')).toHaveValue('environment-1');
    fireEvent.change(screen.getByLabelText('目标网站地址'), { target: { value: '${TEST_BASE_URL}' } });
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await user.click(await screen.findByRole('button', { name: /审核并校验计划/ }));
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));
    const runCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/runs') && init?.method === 'POST');
    expect(JSON.parse(String(runCall?.[1]?.body))).toMatchObject({ projectId: 'project-1', environmentId: 'environment-1' });
  });

  it('creates, edits, reloads, and executes a persisted natural-language scenario', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: /项目接入/ }));
    await user.type(screen.getByLabelText('项目名称'), '企业测试站');
    await user.type(screen.getByLabelText('Base URL'), 'https://example.com');
    await user.click(screen.getByRole('button', { name: /保存项目配置/ }));
    await user.click(screen.getByRole('button', { name: /用该地址新建测试/ }));
    await user.click(screen.getByText('可复用场景库（可选）'));

    await user.type(screen.getByLabelText('场景名称'), '商品检索');
    await user.type(screen.getByLabelText('测试目标'), '点击“商品列表”');
    await user.type(screen.getByLabelText(/前置条件（每行一项）/), '已使用普通用户登录');
    await user.type(screen.getByLabelText(/预期结果（每行一项）/), '确认看到“商品详情”');
    await user.type(screen.getByLabelText(/禁止操作（每行一项）/), '支付');
    fireEvent.change(screen.getByLabelText('测试数据（JSON）'), { target: { value: '{"keyword":"商品 A"}' } });
    await user.click(screen.getByRole('button', { name: /保存新场景/ }));

    expect(await screen.findByText(/场景“商品检索”已保存并载入测试/)).toBeInTheDocument();
    const createCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/scenarios') && init?.method === 'POST');
    expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({
      name: '商品检索', preconditions: ['已使用普通用户登录'], goal: '点击“商品列表”',
      testData: { keyword: '商品 A' }, expectedResults: ['确认看到“商品详情”'], forbiddenActions: ['支付']
    });

    await user.clear(screen.getByLabelText('测试目标'));
    await user.type(screen.getByLabelText('测试目标'), '点击“商品 A”');
    await user.click(screen.getByRole('button', { name: /保存场景修改/ }));
    expect(await screen.findByText(/场景“商品检索”已更新并载入测试/)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([url, init]) => String(url).endsWith('/scenarios/scenario-1') && init?.method === 'PUT')).toBe(true);

    await user.click(screen.getByRole('button', { name: '新建场景' }));
    await user.selectOptions(screen.getByLabelText('已保存场景'), 'scenario-1');
    expect(screen.getByLabelText('测试目标')).toHaveValue('点击“商品 A”');
    await user.clear(screen.getByLabelText('目标网站地址'));
    await user.type(screen.getByLabelText('目标网站地址'), '${TEST_BASE_URL}');

    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await user.click(await screen.findByRole('button', { name: /审核并校验计划/ }));
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));
    const runCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/runs') && init?.method === 'POST');
    expect(JSON.parse(String(runCall?.[1]?.body))).toMatchObject({ projectId: 'project-1', scenarioId: 'scenario-1' });
  });

  it('imports storageState without rendering cookie values', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: /项目接入/ }));
    await user.type(screen.getByLabelText('项目名称'), '企业测试站');
    await user.type(screen.getByLabelText('Base URL'), 'https://example.com');
    await user.click(screen.getByRole('button', { name: /保存项目配置/ }));
    const file = new File([JSON.stringify({ cookies: [{ name: 'session', value: 'private-cookie', domain: 'example.com' }], origins: [] })], 'state.json', { type: 'application/json' });
    await user.upload(screen.getByLabelText(/选择 Playwright storageState JSON/), file);
    await user.click(screen.getByRole('button', { name: /加密导入登录态/ }));
    expect(await screen.findByText('Windows DPAPI / CurrentUser')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('private-cookie');
  });

  it('selects all history reports and deletes their artifact records after confirmation', async () => {
    historyRuns = [
      backendRun,
      { ...backendRun, run_id: '20260719-120100-efgh5678', plan_name: '第二份报告' }
    ];
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: '历史报告' }));
    expect(await screen.findByText('已选择 0 / 2')).toBeInTheDocument();

    await user.click(screen.getByLabelText('全选报告'));
    expect(screen.getByText('已选择 2 / 2')).toBeInTheDocument();
    expect(screen.getByLabelText(`选择报告 ${backendRun.run_id}`)).toBeChecked();
    await user.click(screen.getByRole('button', { name: /删除选中/ }));

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('永久删除选中的 2 份报告'));
    expect(await screen.findByText('已删除 2 份运行报告及其工件。')).toBeInTheDocument();
    expect(screen.getByText('尚无真实运行记录。Mock 样例已移除。')).toBeInTheDocument();
    const deleteCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/runs/delete'));
    expect(deleteCall?.[1]?.body).toContain(backendRun.run_id);
    expect(deleteCall?.[1]?.body).toContain('20260719-120100-efgh5678');
    expect(JSON.parse(String(deleteCall?.[1]?.body))).toMatchObject({ actor: 'local_user' });
  });

  it('runs artifact retention cleanup and exposes the independent deletion audit', async () => {
    historyRuns = [
      { ...backendRun, run_id: 'expired-run' },
      { ...backendRun, run_id: 'current-run', plan_name: '保留中的报告' }
    ];
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: '历史报告' }));

    const audit = await screen.findByRole('link', { name: /下载删除审计/ });
    expect(audit).toHaveAttribute('href', '/api/runs/deletion-audit');
    expect(audit).toHaveAttribute('download');
    await user.click(screen.getByRole('button', { name: /执行保留策略/ }));

    expect(await screen.findByText('保留策略已执行，清理 1 份到期运行工件。')).toBeInTheDocument();
    expect(screen.queryByText('expired-run')).not.toBeInTheDocument();
    expect(screen.getByText('保留中的报告')).toBeInTheDocument();
    const cleanupCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/runs/cleanup'));
    expect(JSON.parse(String(cleanupCall?.[1]?.body))).toEqual({ actor: 'local_user' });
  });

  it('edits findings, approves steps, and versions copied Playwright source', async () => {
    detailRunResponse = {
      ...backendRun,
      scenario_goal: '验证登录并创建客户', goal_status: 'not_achieved', goal_summary: '场景目标未完成；断言通过 2/3', duration_ms: 3414,
      review_summary: { disposition: 'pending_confirmation', pending: 1, confirmed: 0, rejected: 0, total: 1 },
      steps: backendRun.steps.map((step) => ({ ...step, computer_use_triggered: true, computer_use_reason: 'Canvas 缺少结构化目标', coordinate_source: 'canvas-relative:model', execution_mode: 'visual' })),
      findings: [{
        id: 'finding-1', title: '原问题', category: 'expectation_failed', severity: 'Medium', confidence: 'medium',
        actual_result: '未进入控制台', expected_result: '进入首页', facts: ['登录后仍在原页面'], inference: '可能未跳转',
        evidence: [], reproduction_steps: ['打开首页', '点击登录'], review_status: 'pending_review', review_history: []
      }],
      generated_test: { source_path: 'generated-test.spec.ts', source: 'test.skip(true, "包含 D 级人工步骤")', stability_level: 'D', supported_replay_modes: [], ci_eligible: false, ci_recommendation: '包含暂不可自动化步骤，需人工处理', manual_steps: ['触摸硬件安全密钥'] }
    };
    historyRuns = [detailRunResponse];
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: '历史报告' }));
    await user.click(await screen.findByRole('button', { name: '详情' }));

    expect(await screen.findByRole('heading', { name: '回归路径审核' })).toBeInTheDocument();
    expect(screen.getByText('验证登录并创建客户')).toBeInTheDocument();
    expect(screen.getByText('场景目标未完成；断言通过 2/3')).toBeInTheDocument();
    expect(screen.getByText('审核终态 等待确认 1 项')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '视觉 fallback 时间线' })).toHaveTextContent('Canvas 缺少结构化目标');
    expect(screen.getByRole('link', { name: /完整 JSON 报告/ })).toHaveAttribute('href', expect.stringContaining('/report.json'));
    expect(screen.getByRole('link', { name: /HTML 执行证据/ })).toHaveAttribute('href', expect.stringContaining('/report.html'));
    expect(screen.getByText('D 级人工步骤')).toBeInTheDocument();
    expect(screen.getByText('触摸硬件安全密钥')).toBeInTheDocument();
    await user.clear(screen.getByLabelText('问题标题'));
    await user.type(screen.getByLabelText('问题标题'), '登录跳转失败');
    await user.selectOptions(screen.getByLabelText('严重程度'), 'High');
    await user.clear(screen.getByLabelText('预期结果'));
    await user.type(screen.getByLabelText('预期结果'), '进入控制台');
    await user.click(screen.getByRole('button', { name: '保存并确认' }));

    expect(await screen.findByText('问题修改记录（1）')).toBeInTheDocument();
    expect(screen.getByText('审核终态 已确认问题 1 项')).toBeInTheDocument();
    const findingCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).includes('/findings/'));
    expect(JSON.parse(String(findingCall?.[1]?.body))).toMatchObject({ title: '登录跳转失败', severity: 'High', expectedResult: '进入控制台', status: 'confirmed' });

    const descriptions = screen.getAllByLabelText('步骤说明');
    await user.clear(descriptions[0]);
    await user.type(descriptions[0], '打开审核后的首页');
    await user.click(screen.getByLabelText('保留步骤 #2'));
    await user.click(screen.getByRole('button', { name: '保存路径并重新编译' }));

    expect(await screen.findByText('路径修改记录（1）')).toBeInTheDocument();
    const pathCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/review') && init?.method === 'PATCH');
    const pathPayload = JSON.parse(String(pathCall?.[1]?.body));
    expect(pathPayload.steps[0]).toMatchObject({ sourceIndex: 1, retained: true, step: { description: '打开审核后的首页' } });
    expect(pathPayload.steps[1]).toMatchObject({ sourceIndex: 2, retained: false });

    await user.click(screen.getByRole('button', { name: '编辑' }));
    const sourceEditor = screen.getByLabelText('Playwright TypeScript 源码');
    const editedSource = 'import { test } from \'@playwright/test\';\ntest("人工修订", async () => {});\n';
    fireEvent.change(sourceEditor, { target: { value: editedSource } });
    const clipboardSpy = vi.spyOn(navigator.clipboard, 'writeText');
    await user.click(screen.getByRole('button', { name: '复制代码' }));
    expect(clipboardSpy).toHaveBeenCalledWith(editedSource);
    expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '保存修订' }));

    expect(await screen.findByText('修订 2')).toBeInTheDocument();
    expect(screen.getByText('源码修订记录（1）')).toBeInTheDocument();
    const sourceCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/generated-test') && init?.method === 'PATCH');
    expect(JSON.parse(String(sourceCall?.[1]?.body))).toEqual({ source: editedSource });
  });
});
