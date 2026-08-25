import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App, {
  acceptanceThresholdPassed,
  detectedModules,
  extractWebsiteRequest,
  goalRequiresLogin,
  isModelRecoveryWait,
  isLowRiskConfirmation,
  normalizeBeginnerTarget,
  resolveTargetHost,
  websiteRequiresLogin, displayedRunStatus, runCanRecover, shouldAnalyzeScopeAfterLogin
} from './App';
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
    recovery_evidence: {
      policy: 'bounded_read_retry_proof_first_side_effect_recovery', sideEffect: false,
      outcome: 'known', decision: 'succeeded_after_retry', retried: true,
      attempts: [
        { attempt: 1, outcome: 'failed', failureClass: 'http_429', httpStatus: 429, backoffMs: 250 },
        { attempt: 2, outcome: 'succeeded' }
      ]
    },
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
  commerceProfile: { enabled: false, environment: 'production_readonly', accountRef: null, productionReversibleWriteAuthorized: false, sandboxDriver: false, e2eResourcePrefix: 'E2E_', piiMaskSelectors: [] },
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
let savedProjects: any[] = [];
let savedSessionResponse: any = null;
let startRunResponse: typeof backendRun & { completion_reason?: string } = backendRun;
let detailRunResponse: any = backendRun;
let reviewState: any = {
  available: true,
  steps: plan.steps.map((step, index) => ({ sourceIndex: index + 1, retained: true, step })),
  history: []
};
let reviewUnavailable = false;
let acceptanceBatches: any[] = [];
let modelProfiles: any[] = [];
let activeModelProfileId: string | null = null;

function acceptanceBatch() {
  const attempts = Array.from({ length: 65 }, (_, index) => Array.from({ length: 5 }, (_, repeat) => ({
    id: `J${String(index + 1).padStart(2, '0')}#${repeat + 1}`,
    scenarioId: `J${String(index + 1).padStart(2, '0')}`, priority: index < 40 ? 'P0' : 'P1',
    title: `场景 ${index + 1}`, repeat: repeat + 1, status: 'blocked', verificationStatus: 'unverified',
    blockedDependencies: ['京东目标环境与账号授权'], updatedAt: '2026-07-23T00:00:00Z', zeroToleranceIncidents: {}
  }))).flat();
  return {
    id: 'jd-acceptance-test', profile: 'jd-commerce-1.30.31', status: 'blocked', verificationStatus: 'unverified',
    createdAt: '2026-07-23T00:00:00Z', updatedAt: '2026-07-23T00:00:00Z', repeatCount: 5,
    scenarioCount: 65, plannedAttempts: 325, cancelRequested: false, attempts,
    summary: { counts: { queued: 0, running: 0, passed: 0, failed: 0, blocked: 325, cancelled: 0 }, verifiedAttempts: 0, passed: false,
      thresholds: { p0Completion: { actual: 0, required: 1 }, allScenarioPassRate: { actual: 0, required: 0.95 }, stableReplayRate: { actual: 0, required: 0.95 }, evidenceCompleteness: { actual: 0, required: 0.98 }, amountAccuracy: { actual: false, required: true }, cleanupCompleteness: { actual: false, required: true }, zeroToleranceIncidents: { actual: 0, required: 0 } } }
  };
}

function cesiumAcceptanceSuite() {
  return {
    suite: 'cesium-ion', version: '1.33.00', target: 'https://ion.cesium.com', inspectedAt: '2026-07-22',
    truthPolicy: 'loading and observed do not count as passed', thresholds: {},
    summary: { total: 60, passed: 0, byPriority: { P0: 25, P1: 33, P2: 2 }, byStatus: { blocked: 41, observed_read_only: 14, unverified: 5 } },
    resourceLedger: { total: 0, pendingCleanup: 0, zeroResidualProven: false },
    testData: { manifestStatus: 'blocked', reason: 'missing fixed data', required: [] },
    cases: [{
      id: 'C10', version: '1.33.00', priority: 'P0', title: '上传合法 GLB', businessGoal: '处理完成',
      exactExpected: '处理状态 COMPLETE 且预览正确', effectLevel: 'reversible_write',
      execution: { status: 'blocked', repetitionsCompleted: 0, requiredRepetitions: 5, reason: 'missing: versioned_test_data' }
    }]
  };
}

function gaeAcceptanceCatalog() {
  return {
    schemaVersion: '1', scenarioCount: 30, repeatCount: 5, plannedRuns: 150,
    runtimeBindingSupported: true, readyCount: 0, blockedCount: 30,
    blockedDependencies: ['目标网站当前无法连接'],
    scenarios: Array.from({ length: 30 }, (_, index) => ({
      id: `S${String(index + 1).padStart(2, '0')}`, name: `仿真业务检查 ${index + 1}`,
      category: index < 5 ? '登录' : '仿真', accountRole: '普通测试', goal: '完成检查',
      bindingStatus: 'blocked', blockedDependencies: ['目标网站当前无法连接']
    }))
  };
}

function genericWebBenchmark() {
  return {
    suite: 'generic-web', version: '1.33.00', targetKind: 'generic-web',
    truthPolicy: '未执行真实浏览器回归前只标记为未验证',
    compatibilityBoundary: {
      genericLayerMayAssume: ['可观察的 DOM/ARIA/文本语义'],
      genericLayerMustNotAssume: ['固定网站元素 ID', '某一种渲染引擎的内部状态']
    },
    summary: { siteCount: 2, taskCount: 4, verified: 0, unverified: 4 },
    sites: [
      { id: 'generic-crm', name: '云衡 CRM 本地夹具', path: '/', surface: '普通表单和表格', tasks: [{ id: 'G01', title: '登录后创建客户', goal: '登录并创建客户', expected: '列表数量更新', effect: 'isolated_local_write', status: 'unverified' }] },
      { id: 'generic-shadow', name: 'Shadow DOM 资产夹具', path: '/shadow.html', surface: '开放 Shadow DOM', tasks: [{ id: 'G03', title: '定位 Shadow DOM 搜索框', goal: '输入资产关键词', expected: '定位到搜索框', effect: 'session_only', status: 'unverified' }]
      }
    ]
  };
}

function openSourceCatalog() {
  const projects = [
    ['playwright-mcp', 'Playwright MCP', 'Apache-2.0', 'P0'],
    ['playwright-cli', 'Playwright CLI', 'Apache-2.0', 'P2'],
    ['stagehand', 'Stagehand', 'MIT', 'P0'],
    ['browser-use', 'Browser-use', 'MIT', 'P1'],
    ['openadapt', 'OpenAdapt', 'MIT', 'P0'],
    ['browsergym', 'BrowserGym', 'Apache-2.0', 'P1'],
    ['agentlab', 'AgentLab', 'Apache-2.0', 'P1'],
    ['webarena', 'WebArena', 'Apache-2.0', 'P1'],
    ['osworld', 'OSWorld', 'Apache-2.0', 'P2'],
    ['ui-tars', 'UI-TARS', 'Apache-2.0', 'P2'],
    ['testzeus-hercules', 'TestZeus Hercules', 'AGPL-3.0', 'P2']
  ].map(([id, name, license, priority]) => ({
    id, name, license, priority, repository: `local/${id}`, commit: 'fixture', directory: id,
    referenceAvailable: true, gitCheckout: true, licenseFile: license === 'AGPL-3.0' ? 'LICENSE' : 'LICENSE',
    capabilities: ['研究能力'], entrypoints: ['README.md'], adaptation: '测试目录',
    integrationStatus: 'research_complete',
    licenseStatus: id === 'testzeus-hercules' ? 'blocked_by_license' : 'approved', runtimeReady: true
  }));
  return {
    schemaVersion: '1', referenceRoot: 'C:/Users/zzzzl/Desktop/京彩OPC/开源GUI-Agent参考项目',
    source: 'local-reference-catalog', projectCount: projects.length,
    summary: { available: 11, missing: 0, researched: 11, adapterReady: 9, licenseReviewRequired: 2, blockedByLicense: 1 },
    runtime: { node: { available: true, command: 'node', path: 'node', version: 'v22.0.0' }, python: { available: true, path: 'python', version: 'Python 3.12' } },
    adapters: {
      contractVersion: '1',
      summary: { contractReady: 3, runtimeReady: 0, blocked: 3 },
      adapters: [
        { id: 'playwright-mcp-adapter', projectId: 'playwright-mcp', name: 'Playwright MCP 观察适配器', contractStatus: 'contract_ready', runtimeStatus: 'not_ready', runtimeReady: false, transport: 'stdio sidecar', entrypoint: 'cli.js', capabilities: ['MCP 页面观察归一化'], normalization: 'Observation + StepResult evidence', configuredCommand: false, reasons: ['未安装上游 playwright-core'], safety: '不启动外部进程' },
        { id: 'stagehand-adapter', projectId: 'stagehand', name: 'Stagehand 候选动作适配器', contractStatus: 'contract_ready', runtimeStatus: 'not_ready', runtimeReady: false, transport: 'stdio sidecar', entrypoint: 'packages/core/lib/inference.ts', capabilities: ['observe 候选动作'], normalization: 'candidate action + extraction evidence', configuredCommand: false, reasons: ['未安装上游 Stagehand 依赖'], safety: '不启动外部进程' },
        { id: 'openadapt-adapter', projectId: 'openadapt', name: 'OpenAdapt 工作流证据适配器', contractStatus: 'contract_ready', runtimeStatus: 'not_ready', runtimeReady: false, transport: 'workflow package', entrypoint: 'openadapt/cli.py', capabilities: ['工作流检查点'], normalization: 'checkpoint + evidence package', configuredCommand: false, reasons: ['未发现外部 openadapt-flow'], safety: '不启动外部进程' }
      ],
      evaluationContracts: [
        { id: 'browsergym-evaluator', projectId: 'browsergym', name: 'BrowserGym 任务轨迹评测契约', contractStatus: 'contract_ready', runtimeStatus: 'not_ready', runtimeReady: false, transport: 'Python evaluation provider', entrypoint: 'browsergym/core/env.py + experiments/loop.py', capabilities: ['task/seed'], normalization: 'task + environment + bounded trajectory + evaluator facts', configuredCommand: false, reasons: ['尚未启动 BrowserGym 上游环境'], safety: '只读' },
        { id: 'agentlab-experiment', projectId: 'agentlab', name: 'AgentLab 实验轨迹评测契约', contractStatus: 'contract_ready', runtimeStatus: 'not_ready', runtimeReady: false, transport: 'Python experiment provider', entrypoint: 'agentlab/experiments/loop.py', capabilities: ['StepInfo'], normalization: 'experiment metadata + trajectory summary + metrics', configuredCommand: false, reasons: ['尚未启动 AgentLab 上游实验'], safety: '只读' },
        { id: 'browser-use-state', projectId: 'browser-use', name: 'Browser-use Agent 状态契约', contractStatus: 'contract_ready', runtimeStatus: 'not_ready', runtimeReady: false, transport: 'Python sidecar evidence import', entrypoint: 'browser_use/agent/service.py + browser_use/browser/session.py', capabilities: ['goal/current URL'], normalization: 'bounded agent state + redacted evidence', configuredCommand: false, reasons: ['Browser-use sidecar 未启用'], safety: '只读预览' },
        { id: 'ui-tars-visual-action', projectId: 'ui-tars', name: 'UI-TARS 视觉动作契约', contractStatus: 'contract_ready', runtimeStatus: 'not_ready', runtimeReady: false, transport: 'visual-model evidence import', entrypoint: 'codes/ui_tars/action_parser.py', capabilities: ['normalized coordinates'], normalization: 'visual action candidate + safe coordinate evidence', configuredCommand: false, reasons: ['视觉模型与截图授权未启用'], safety: '只读预览' },
        { id: 'playwright-cli-trace', projectId: 'playwright-cli', name: 'Playwright CLI 轨迹契约', contractStatus: 'contract_ready', runtimeStatus: 'not_ready', runtimeReady: false, transport: 'CLI trace evidence import', entrypoint: 'skills/ + scripts/ + package.json', capabilities: ['command classification'], normalization: 'trace facts + write/unsafe classification', configuredCommand: false, reasons: ['Playwright CLI 进程未启用'], safety: '只读预览' }
      ]
    },
    executionProfiles: [
      { id: 'native', projectIds: [], name: 'Native guarded Runner', mode: 'product_default', capabilities: ['observation', 'decision_gate', 'native_action_execution'], actionPolicy: 'native_runner_only', status: 'built_in' },
      { id: 'stagehand', projectIds: ['stagehand'], name: 'Stagehand candidate router', mode: 'candidate_to_guarded_action', capabilities: ['observe_candidates', 'semantic_locator_preference', 'extraction_evidence'], actionPolicy: 'candidate_must_pass_product_gate', status: 'product_runtime_integrated' },
      { id: 'browser-use', projectIds: ['browser-use'], name: 'Browser-use state adapter', mode: 'stateful_goal_loop', capabilities: ['goal_state', 'trajectory_tail', 'next_action_boundary', 'sensitive_state_redaction'], actionPolicy: 'one_action_per_observation', status: 'product_runtime_integrated' },
      { id: 'ui-tars', projectIds: ['ui-tars'], name: 'UI-TARS visual action adapter', mode: 'visual_candidate_to_bounded_action', capabilities: ['visual_candidate_contract', 'normalized_coordinates', 'visual_authorization_gate'], actionPolicy: 'visual_action_requires_screenshot_authorization', status: 'product_runtime_integrated' },
      { id: 'playwright-cli', projectIds: ['playwright-cli'], name: 'Playwright CLI trace adapter', mode: 'trace_facts_to_locator_action', capabilities: ['locator_facts', 'command_classification', 'input_value_redaction', 'trace_evidence'], actionPolicy: 'trace_facts_never_replay_external_command', status: 'product_runtime_integrated' },
      { id: 'openadapt', projectIds: ['openadapt'], name: 'OpenAdapt checkpoint adapter', mode: 'checkpointed_workflow', capabilities: ['step_checkpoint', 'pause_resume_evidence', 'reobserve_before_resume'], actionPolicy: 'checkpoint_before_resume_and_revalidate_writes', status: 'product_runtime_integrated' }
    ],
    projects
  };
}

function openSourceObservation() {
  return {
    adapter: 'playwright-mcp',
    runtimeEvidence: { toolCount: 23, unsafeTools: ['browser_evaluate', 'browser_run_code_unsafe'] },
    observation: {
      url: 'http://127.0.0.1:8765/', title: '通用页面夹具', dom_summary: [],
      accessibility_summary: '- heading "通用页面夹具"', console_errors: [], page_errors: [], failed_requests: []
    },
    toolResult: { adapter: 'playwright-mcp', ok: true, text: 'Page Title: 通用页面夹具' }
  };
}

function openSourceEvaluationFixtures() {
  return [
    { provider: 'browsergym', name: 'BrowserGym 任务轨迹契约夹具', fixtureId: 'browsergym-contract-fixture', available: true, source: 'product_owned_deterministic_fixture', upstreamRuntimeRequired: false, actionPolicy: 'contract_validation_only' },
    { provider: 'agentlab', name: 'AgentLab 实验轨迹契约夹具', fixtureId: 'agentlab-contract-fixture', available: true, source: 'product_owned_deterministic_fixture', upstreamRuntimeRequired: false, actionPolicy: 'contract_validation_only' }
  ];
}

function openSourceRuntimeStatus() {
  return {
    schemaVersion: '1', source: 'isolated_upstream_runtime_probe', configurationEnv: 'GUI_AGENT_OPENSOURCE_RUNTIME_PYTHON',
    configuredPython: null, executionBoundary: 'isolated_child_process',
    providers: [
      { provider: 'browsergym', module: 'browsergym.core', configuredPython: null, pythonPath: null, executionBoundary: 'isolated_child_process', available: false, runtimeReady: false, status: 'not_configured', reasons: ['请配置隔离环境 Python'] },
      { provider: 'agentlab', module: 'agentlab.experiments.loop', configuredPython: null, pythonPath: null, executionBoundary: 'isolated_child_process', available: false, runtimeReady: false, status: 'not_configured', reasons: ['请配置隔离环境 Python'] }
      ],
      summary: { configured: false, runtimeReady: 0, providerCount: 2 }
  };
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
}

beforeEach(() => {
  historyRuns = [];
  savedEnvironments = [];
  savedScenarios = [];
  savedProjects = [];
  savedSessionResponse = null;
  startRunResponse = backendRun;
  detailRunResponse = backendRun;
  reviewState = {
    available: true,
    steps: plan.steps.map((step, index) => ({ sourceIndex: index + 1, retained: true, step })),
    history: []
  };
  reviewUnavailable = false;
  acceptanceBatches = [];
  modelProfiles = [];
  activeModelProfileId = null;
  vi.stubGlobal('open', vi.fn());
  vi.stubGlobal('confirm', vi.fn(() => true));
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } });
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/api/health')) return jsonResponse({ status: 'ok', mode: 'real', engine: 'playwright-chromium', planner: 'deterministic-rules' });
    if (url.endsWith('/api/websites/resolve')) return jsonResponse({ url: JSON.parse(String(init?.body)).url, changed: false, redirectChain: [JSON.parse(String(init?.body)).url] });
    if (url.endsWith('/api/benchmarks/cesium-ion')) return jsonResponse(cesiumAcceptanceSuite());
    if (url.endsWith('/api/benchmarks/generic-web/run') && init?.method === 'POST') return jsonResponse({
      ...genericWebBenchmark(),
      summary: { siteCount: 2, taskCount: 4, verified: 4, unverified: 0 },
      lastRun: { status: 'passed', startedAt: '2026-08-05T10:00:00Z', endedAt: '2026-08-05T10:00:02Z', durationMs: 2000, siteCount: 2, taskCount: 4, passed: 4, failed: 0, evidencePath: 'generic-web/regression-test.json' },
      evidence: [
        { taskId: 'G01', siteId: 'generic-crm', status: 'passed', facts: ['客户列表已更新'], actionCount: 6 },
        { taskId: 'G02', siteId: 'generic-crm', status: 'passed', facts: ['读取表格'], actionCount: 1 },
        { taskId: 'G03', siteId: 'generic-shadow', status: 'passed', facts: ['找到搜索框'], actionCount: 1 },
        { taskId: 'G04', siteId: 'generic-shadow', status: 'passed', facts: ['Story 已创建'], actionCount: 1 }
      ]
    });
    if (url.endsWith('/api/benchmarks/generic-web')) return jsonResponse(genericWebBenchmark());
    if (url.endsWith('/api/opensource/catalog')) return jsonResponse(openSourceCatalog());
    if (url.endsWith('/api/opensource/evaluation/fixtures')) return jsonResponse(openSourceEvaluationFixtures());
    if (url.endsWith('/api/opensource/evaluation/runtime')) return jsonResponse(openSourceRuntimeStatus());
    if (url.endsWith('/api/opensource/playwright-mcp/observe')) return jsonResponse(openSourceObservation());
    if (url.endsWith('/api/opensource/evaluation/normalize')) {
      const payload = JSON.parse(String(init?.body));
      return jsonResponse({ adapter: payload.provider, status: payload.provider === 'browsergym' ? 'episode_ready' : 'experiment_ready', actionPolicy: payload.provider === 'browsergym' ? 'trajectory_evaluation_only' : 'experiment_metadata_only', summary: { stepCount: 1 } });
    }
    if (url.endsWith('/api/opensource/evaluation/fixture')) {
      const payload = JSON.parse(String(init?.body));
      return jsonResponse({ adapter: payload.provider, status: payload.provider === 'browsergym' ? 'episode_ready' : 'experiment_ready', actionPolicy: 'contract_validation_only', fixture: { upstreamRuntimeStarted: false } });
    }
    if (url.endsWith('/api/acceptance/gaealavic/scenarios')) return jsonResponse(gaeAcceptanceCatalog());
    if (url.endsWith('/api/acceptance/gaealavic/l4-workflow')) return jsonResponse({ id: 'GAE-L4', name: '完整流程', bindingStatus: 'blocked', stages: [{ id: 'login', goal: '登录授权租户', requiredOutputs: ['accountId'] }, { id: 'cleanup', goal: '清理测试数据', requiredOutputs: ['cleanupReport'] }], successRule: '所有阶段成功并完成清理', blockedDependencies: ['目标网站当前无法连接'] });
    if (url.endsWith('/api/acceptance/gaealavic/batches')) return jsonResponse([]);
    if (url.endsWith('/api/projects') && init?.method === 'POST') return jsonResponse({ ...project, ...JSON.parse(String(init.body)) });
    if (url.endsWith('/api/projects/project-1') && init?.method === 'PUT') return jsonResponse({ ...project, ...JSON.parse(String(init.body)), updatedAt: '2026-07-20T00:03:00Z' });
    if (url.endsWith('/api/projects/project-1/scan') && init?.method === 'POST') {
      savedScenarios = [{
        id: 'scenario-scan', projectId: 'project-1', name: '企业测试站 扫描示例', preconditions: ['目标测试环境可访问'],
        goal: compatibilityReport.suggestedScenarios[0], testData: {}, expectedResults: ['企业工作台可见'], forbiddenActions: ['支付'],
        createdAt: '2026-07-20T00:02:00Z', updatedAt: '2026-07-20T00:02:00Z'
      }];
      return jsonResponse(compatibilityReport);
    }
    if (url.endsWith('/api/projects')) return jsonResponse(savedProjects);
    if (url.endsWith('/api/ai/profiles') && init?.method === 'POST') {
      const payload = JSON.parse(String(init.body));
      const profile = {
        ...payload, apiKey: null, id: 'model-profile-1', keyConfigured: true, isActive: true,
        connectionStatus: 'untested', capabilities: null, verifiedModelId: null, lastTestedAt: null,
        createdAt: '2026-08-06T00:00:00Z', updatedAt: '2026-08-06T00:00:00Z'
      };
      modelProfiles = [profile]; activeModelProfileId = profile.id;
      return jsonResponse(profile);
    }
    if (url.endsWith('/api/ai/profiles')) return jsonResponse({ activeProfileId: activeModelProfileId, profiles: modelProfiles.map((item) => ({ ...item, isActive: item.id === activeModelProfileId })) });
    if (url.endsWith('/api/ai/profiles/model-profile-1/activate')) {
      activeModelProfileId = 'model-profile-1';
      return jsonResponse({ activeProfileId: activeModelProfileId, profiles: modelProfiles.map((item) => ({ ...item, isActive: true })) });
    }
    if (url.endsWith('/api/ai/profiles/model-profile-1/probe')) {
      modelProfiles = modelProfiles.map((item) => ({ ...item, connectionStatus: 'connected', verifiedModelId: 'gpt-5.6-terra', capabilities: { schema: 'passed', agentDecision: 'passed', multiTurn: 'passed', vision: 'passed' }, isActive: true }));
      return jsonResponse({ connected: true, model: 'gpt-5.6-terra', protocol: 'responses', elapsedMs: 123, verifiedModelId: 'gpt-5.6-terra', capabilities: { schema: 'passed', agentDecision: 'passed', multiTurn: 'passed', vision: 'passed' }, visionDetail: '合成红色测试图片识别通过', probeVersion: 'agent-first-v1' });
    }
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
    if (url.includes('/api/projects/') && url.endsWith('/session')) return savedSessionResponse ? jsonResponse(savedSessionResponse) : jsonResponse({ detail: 'not found' }, 404);
    if (url.endsWith('/api/ai/probe')) return jsonResponse({ connected: true, model: 'gpt-5.6-terra', protocol: 'responses', elapsedMs: 123, verifiedModelId: 'gpt-5.6-terra', capabilities: { schema: 'passed', agentDecision: 'passed', multiTurn: 'passed', vision: 'passed' }, visionDetail: '合成红色测试图片识别通过；未发送任何网站截图', probeVersion: 'agent-first-v1' });
    if (url.endsWith('/api/ai/plans/generate')) return jsonResponse({ plan, warnings: [], planner: 'ai:responses:gpt-5.6-terra' });
    if (url.endsWith('/api/plans/generate')) return jsonResponse({ plan, warnings: [], planner: 'deterministic-rules' });
    if (url.endsWith('/api/plans/validate')) {
      const payload = JSON.parse(String(init?.body));
      return jsonResponse({ valid: true, plan: payload.plan });
    }
    if (url.endsWith('/api/acceptance/jd/batches') && init?.method === 'POST') {
      const batch = acceptanceBatch(); acceptanceBatches = [batch]; return jsonResponse(batch);
    }
    if (url.endsWith('/api/acceptance/jd/batches')) return jsonResponse(acceptanceBatches);
    if (url.endsWith('/api/runs') && init?.method === 'POST') return jsonResponse(startRunResponse);
    if (url.endsWith('/api/agent-runs') && init?.method === 'POST') return jsonResponse(startRunResponse);
    if (url.endsWith('/cancel') && init?.method === 'POST') return jsonResponse({ ...startRunResponse, completion_reason: 'cancellation_requested' });
    if (url.endsWith('/clarification') && init?.method === 'POST') {
      const payload = JSON.parse(String(init.body));
      return jsonResponse({
        ...startRunResponse,
        status: payload.answer === '结束本次测试' ? 'passed' : 'running',
        pending_clarification: null,
        clarification_history: payload.answer === '结束本次测试' ? [] : [{
          ...(startRunResponse as any).pending_clarification,
          answer: payload.answer,
          actor: payload.actor,
          answered_at: '2026-07-25T02:00:01Z'
        }]
      });
    }
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
    if (url.endsWith('/review')) return reviewUnavailable
      ? jsonResponse({ detail: '运行记录不存在' }, 404)
      : jsonResponse(reviewState);
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
  async function openAdvancedPage(user: ReturnType<typeof userEvent.setup>, pageName: RegExp | string) {
    await user.click(screen.getByRole('button', { name: '高级设置' }));
    await user.click(screen.getByRole('button', { name: pageName }));
  }

  it('keeps the model center in ordinary navigation and exposes all advanced settings', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    expect(screen.getByPlaceholderText('输入网址，或把网址和测试要求写在一起……')).toBeVisible();
    expect(screen.getByRole('navigation', { name: '主要导航' }).querySelectorAll('button')).toHaveLength(4);
    expect(screen.getByRole('button', { name: '开始测试' })).toBeVisible();
    expect(screen.getByRole('button', { name: '测试记录' })).toBeVisible();
    expect(screen.getByRole('button', { name: '模型中心' })).toBeVisible();
    expect(screen.getByRole('button', { name: '高级设置' })).toBeVisible();
    expect(screen.queryByText(/GAEALaViC|Cesium|京东/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '高级设置' }));
    expect(screen.getByRole('button', { name: /模型中心与高级连接/ })).toBeVisible();
    expect(screen.getByRole('main').querySelectorAll('.settings-grid > button')).toHaveLength(9);
    expect(screen.getByRole('button', { name: /网站与登录配置/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /传统测试编辑器/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /完整电商验收/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /通用网页验证基线/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /三维与复杂网站验收/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /仿真业务完整验收/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /运行总览/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /开源项目适配/ })).toBeVisible();
  });

  it('keeps an optional login entry and clears the unrelated old goal for a new website', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.type(screen.getByPlaceholderText('输入网址，或把网址和测试要求写在一起……'), 'https://example.com');
    await user.click(screen.getByRole('button', { name: '发送并识别网站' }));
    expect(await screen.findByRole('button', { name: '登录账号后再测试' })).toBeVisible();
    expect(screen.getByText('当前没有保存登录状态；只有测试账号功能时才需要登录。')).toBeVisible();
    expect(screen.getByPlaceholderText('告诉 AI 你想测试什么……')).toHaveValue('');
  });

  it('resolves direct and project-backed target hosts without throwing raw URL errors', () => {
    expect(resolveTargetHost('https://shop.example.com/path')).toBe('shop.example.com');
    expect(resolveTargetHost('${TEST_BASE_URL}', 'https://project.example.com')).toBe('project.example.com');
    expect(() => resolveTargetHost('${TEST_BASE_URL}')).toThrow('必须先选择已保存项目');
  });

  it('stops a URL at Chinese task punctuation without dropping the following goal', () => {
    const request = extractWebsiteRequest(
      '测试 https://ion.cesium.com/stories/viewer/?id=46603fed-a31f-477a-a0f8-ff926f733433：在地球仪中搜索并定位天安门'
    );

    expect(request.url).toBe('https://ion.cesium.com/stories/viewer/?id=46603fed-a31f-477a-a0f8-ff926f733433');
    expect(request.goal).toBe('在地球仪中搜索并定位天安门');
  });

  it('removes common tracking parameters without applying a site-specific destination', () => {
    const result = normalizeBeginnerTarget(new URL('https://re.m.jd.com/page/homelike?re_dcp=abc&utm_source=cn.bing.com'));
    expect(result).toEqual({ url: new URL('https://re.m.jd.com/page/homelike?re_dcp=abc'), changed: true });
    expect(normalizeBeginnerTarget(new URL('https://example.com/page')).changed).toBe(false);
  });

  it('derives scope only from values actually observed on the current website', () => {
    const modules = detectedModules({
      ...compatibilityReport,
      pageSummary: { ...compatibilityReport.pageSummary, canvases: 0, webglRegions: 0, fileInputs: 0, loadingSignals: 0 },
      asyncPatterns: [], capabilities: ['标准 DOM'], suggestedScenarios: [], authenticationSignals: [],
      navigationEntries: ['客户管理'], scannedPages: [{ ...compatibilityReport.scannedPages[0], headings: ['工作台'] }]
    } as any);
    expect(modules).toContain('当前网站实际识别到的栏目和页面跳转');
    expect(modules.join(' ')).not.toMatch(/购物车|三维|文件上传/);
  });

  it('recognizes commerce from controls that were actually found on the page', () => {
    const modules = detectedModules({
      ...compatibilityReport,
      navigationEntries: [], capabilities: ['标准 DOM'], suggestedScenarios: [],
      scannedPages: [{
        ...compatibilityReport.scannedPages[0],
        headings: ['首页'],
        controls: [{ name: '我的购物车', role: 'link', locator: { href: 'https://shop.example.com/cart' } }]
      }]
    } as any);
    expect(modules).toContain('商品、购物车和下单前流程（绝不付款）');
  });

  it('does not treat an ordinary app-download link as a file testing capability', () => {
    const modules = detectedModules({
      ...compatibilityReport,
      pageSummary: { ...compatibilityReport.pageSummary, fileInputs: 0 },
      scannedPages: [{
        ...compatibilityReport.scannedPages[0],
        controls: [{ name: '网络举报APP下载', role: 'link', locator: { href: 'https://example.com/app' } }]
      }]
    } as any);
    expect(modules).not.toContain('当前网站实际提供的文件上传或下载');
  });

  it('recognizes a navigation area even when only one page was scanned', () => {
    const modules = detectedModules({
      ...compatibilityReport,
      navigationEntries: [],
      scannedPages: [{
        ...compatibilityReport.scannedPages[0],
        regions: [{ tag: 'nav', role: 'navigation', name: '主要导航' }]
      }]
    } as any);
    expect(modules).toContain('当前网站实际识别到的栏目和页面跳转');
  });

  it('does not mistake a public login entrance for a mandatory login wall', () => {
    expect(websiteRequiresLogin({ ...compatibilityReport, authenticationSignals: ['检测到登录入口或密码输入框'] })).toBe(false);
    expect(websiteRequiresLogin({ ...compatibilityReport, authenticationSignals: ['未发现明确登录表单；本次按公开页面状态扫描'] })).toBe(false);
    expect(websiteRequiresLogin({ ...compatibilityReport, authenticationSignals: ['已确认登录成功，并识别到登录后的账号功能'] })).toBe(false);
    expect(websiteRequiresLogin({ ...compatibilityReport, authenticationSignals: ['已加载保存的登录状态；首页仍保留公共登录入口，但未发现密码框、登录墙或验证拦截，可以继续测试并在进入账号功能时复核'] })).toBe(false);
    expect(websiteRequiresLogin({ ...compatibilityReport, authenticationSignals: ['检测到仍在显示的登录表单或登录拦截页面'], blockedAreas: ['保存的登录状态未生效或已被网站拒绝'] })).toBe(true);
    expect(websiteRequiresLogin({ ...compatibilityReport, authenticationSignals: ['页面处于验证/挑战页，当前登录状态无法确认'] })).toBe(true);
    expect(websiteRequiresLogin({ ...compatibilityReport, blockedAreas: ['该页面必须登录后访问'] })).toBe(true);
  });

  it('keeps retry controls when a model error is wrapped as pending review', () => {
    const failedRun = {
      status: 'pending_review', executionStatus: 'system_error', completionReason: 'model_error'
    } as any;
    expect(displayedRunStatus(failedRun)).toBe('system_error');
    expect(runCanRecover(failedRun)).toBe(true);
  });

  it('does not present an incomplete execution as pending review', () => {
    const incompleteRun = {
      status: 'pending_review', executionStatus: 'incomplete', completionReason: 'agent_blocked'
    } as any;
    expect(displayedRunStatus(incompleteRun)).toBe('incomplete');
    expect(runCanRecover(incompleteRun)).toBe(true);
  });

  it('distinguishes a recoverable model outage from a completed goal', () => {
    expect(isModelRecoveryWait({ completionReason: 'agent_waiting_for_model_recovery' })).toBe(true);
    expect(isModelRecoveryWait({ completionReason: 'agent_goal_completed_waiting_follow_up' })).toBe(false);
    expect(displayedRunStatus({
      status: 'waiting_for_clarification', executionStatus: 'running',
      completionReason: 'agent_goal_completed_waiting_follow_up'
    } as any)).toBe('passed');
    expect(displayedRunStatus({
      status: 'waiting_for_clarification', executionStatus: 'running',
      completionReason: 'waiting_for_clarification', pendingClarification: { round: 0 }
    } as any)).toBe('waiting_for_clarification');
    expect(displayedRunStatus({
      status: 'waiting_for_clarification', executionStatus: 'running',
      completionReason: 'agent_waiting_for_model_recovery', pendingClarification: { round: 0 }
    } as any)).toBe('waiting_for_clarification');
    expect(isModelRecoveryWait({
      completionReason: 'waiting_for_clarification',
      pendingClarification: { round: 0, question: 'AI 服务暂时无法连接，请发送“重试”继续当前任务。' }
    } as any)).toBe(true);
  });

  it('uses ordinary operation wording for low-risk form filling', () => {
    expect(isLowRiskConfirmation('fill', '填写站内搜索词', 'approval-mode:write-action')).toBe(true);
    expect(isLowRiskConfirmation('click', '提交订单', 'commerce:checkout')).toBe(false);
  });

  it('reanalyzes the real page after a confirmed login when AI is connected', () => {
    expect(shouldAnalyzeScopeAfterLogin(true, 'connected')).toBe(true);
    expect(shouldAnalyzeScopeAfterLogin(false, 'connected')).toBe(false);
    expect(shouldAnalyzeScopeAfterLogin(true, 'failed')).toBe(false);
  });

  it('asks for login only when the requested task actually needs account state', () => {
    expect(goalRequiresLogin('把无线鼠标加入购物车并停在提交订单前')).toBe(true);
    expect(goalRequiresLogin('在已有的 Stories 中找到成都并框选后读取面积')).toBe(true);
    expect(goalRequiresLogin('检查首页是否能正常打开')).toBe(false);
  });

  it('fails the zero-tolerance threshold when any incident is recorded', () => {
    expect(acceptanceThresholdPassed('zeroToleranceIncidents', { actual: 0, required: 0 })).toBe(true);
    expect(acceptanceThresholdPassed('zeroToleranceIncidents', { actual: 1, required: 0 })).toBe(false);
    expect(acceptanceThresholdPassed('evidenceCompleteness', { actual: 0.98, required: 0.98 })).toBe(true);
  });

  it('creates a fixed 65 by 5 blocked acceptance batch without claiming verification', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /完整电商验收/);
    expect(await screen.findByText('电商网站全面验收')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /建立验收批次/ }));
    expect((await screen.findAllByText('325')).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('unverified').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('京东目标环境与账号授权')).toHaveLength(65);
    expect(screen.queryByText('verified')).not.toBeInTheDocument();
  });

  it('shows the Cesium-specific baseline only inside advanced settings with plain-language status', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    expect(screen.queryByText('三维与复杂网站完整验收')).not.toBeInTheDocument();
    await openAdvancedPage(user, /三维与复杂网站验收/);
    expect(await screen.findByText('三维与复杂网站完整验收')).toBeInTheDocument();
    expect(screen.getByText('缺少账号、数据或授权')).toBeInTheDocument();
    expect(screen.getByText('会创建可清理的测试数据')).toBeInTheDocument();
    expect(screen.getByText('0/5 次')).toBeInTheDocument();
  });

  it('exposes the two-site generic web baseline separately from Cesium acceptance', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    expect(screen.queryByRole('heading', { name: '通用网页验证基线' })).not.toBeInTheDocument();
    await openAdvancedPage(user, /通用网页验证基线/);
    expect((await screen.findAllByRole('heading', { name: '通用网页验证基线' })).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('两个非 Cesium 本地验证页面')).toBeInTheDocument();
    expect(screen.getByText('云衡 CRM 本地夹具')).toBeInTheDocument();
    expect(screen.getByText('Shadow DOM 资产夹具')).toBeInTheDocument();
    expect(screen.getAllByText('尚未实测').length).toBeGreaterThanOrEqual(2);
  });

  it('runs the two non-Cesium pages from the GUI and shows persisted task evidence', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /通用网页验证基线/);
    await user.click(screen.getByRole('button', { name: '运行两个页面真实回归' }));
    expect(await screen.findByText('最近一次真实回归')).toBeInTheDocument();
    expect(screen.getByText('4/4 通过')).toBeInTheDocument();
    expect(screen.getByText('真实任务事实')).toBeInTheDocument();
    expect(screen.getByText('Story 已创建')).toBeInTheDocument();
    const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/benchmarks/generic-web/run') && init?.method === 'POST');
    expect(call).toBeTruthy();
  });

  it('shows the simulation business baseline only inside advanced settings and keeps blocked truth', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    expect(screen.queryByRole('heading', { name: '仿真业务完整验收' })).not.toBeInTheDocument();
    await openAdvancedPage(user, /仿真业务完整验收/);
    expect((await screen.findAllByRole('heading', { name: '仿真业务完整验收' })).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('30').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/没有成功访问企业目标站/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /检查 30×5 调度合同/ })).toBeInTheDocument();
  });

  it('shows the open-source research catalog and the product runtime integration profiles', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    expect(screen.queryByRole('heading', { name: '开源项目适配' })).not.toBeInTheDocument();
    await openAdvancedPage(user, /开源项目适配/);
    expect((await screen.findAllByRole('heading', { name: '开源项目适配' })).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Playwright MCP')).toBeInTheDocument();
    expect(screen.getByText('TestZeus Hercules')).toBeInTheDocument();
    expect(screen.getAllByText('已研究，待专项适配').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/禁止直接并入/)).toBeInTheDocument();
    expect(screen.getByText(/可执行产品适配 9 个；只读评测契约已在下方列出，P0 运行时探针 0 个可用/)).toBeInTheDocument();
    expect(screen.getByText('已接入 GUI 的 Agent 运行时策略')).toBeInTheDocument();
    expect(screen.getByText(/5 个开源策略已接入/)).toBeInTheDocument();
    expect(screen.getByText('Stagehand candidate router')).toBeInTheDocument();
    expect(screen.getByText('Playwright MCP 观察适配器')).toBeInTheDocument();
    expect(screen.getByText(/未安装上游 playwright-core/)).toBeInTheDocument();
    expect(screen.getByText('开源评测与证据契约')).toBeInTheDocument();
    expect(screen.getByText('BrowserGym 任务轨迹评测契约')).toBeInTheDocument();
    expect(screen.getByText('Browser-use Agent 状态契约')).toBeInTheDocument();
    expect(screen.getByText('UI-TARS 视觉动作契约')).toBeInTheDocument();
    expect(screen.getByText('Playwright CLI 轨迹契约')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '只读归一化' }));
    expect(await screen.findByText(/BrowserGym 评测证据已归一化/)).toBeInTheDocument();
    expect(screen.getAllByText(/trajectory_evaluation_only/).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '运行隔离 fixture' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: '运行隔离 fixture' }));
    expect(await screen.findByText(/BrowserGym 隔离 fixture 通过/)).toBeInTheDocument();
    const request = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/opensource/evaluation/normalize'));
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ provider: 'browsergym', payload: { taskId: 'fixture.task' } });
  });

  it('runs the guarded Playwright MCP read-only observation from the GUI', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /开源项目适配/);
    await user.click(screen.getByRole('button', { name: '运行只读观察' }));
    expect(await screen.findByText(/Playwright MCP 只读观察完成/)).toBeInTheDocument();
    expect(screen.getByText('通用页面夹具')).toBeInTheDocument();
    const request = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/opensource/playwright-mcp/observe'));
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
      url: 'http://127.0.0.1:8765/', allowedHosts: ['127.0.0.1'], allowPrivateNetwork: false
    });
  });

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
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /运行总览/);
    expect(screen.getByText('真实运行记录')).toBeInTheDocument();
    expect(screen.getByText('尚无真实运行记录。Mock 样例已移除。')).toBeInTheDocument();
    expect(screen.queryByText('制造一个失败结果')).not.toBeInTheDocument();
  });

  it('requires a reviewed backend plan before running', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /传统测试编辑器/);
    await user.click(screen.getByRole('button', { name: '固定计划执行' }));
    expect(screen.getByRole('button', { name: /启动真实浏览器测试/ })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    expect(await screen.findByText('可审核执行计划')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /审核并校验计划/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /启动真实浏览器测试/ })).toBeEnabled());
  });

  it('edits structured commerce metadata and sends it with the reviewed plan', async () => {
    const user = userEvent.setup();
    startRunResponse = {
      ...backendRun,
      commerce_summary: {
        environment: 'isolated_transaction',
        policyEvaluations: [{ index: 2, action: 'add_cart', allowed: true, riskLevel: 'reversible_write', reason: 'authorized', missingControls: [] }],
        ledgerEntries: [{}],
        pendingResources: [{ reference: { kind: 'cartLineId', sha256: 'a'.repeat(64), suffix: '***' }, status: 'created', cleanupAction: 'remove cart line' }],
        zeroResidual: false,
        releaseGate: {
          passed: false, policy: 'commerce_release_gate_v1', checks: {
            evidenceCompleteness: { passed: false, ratio: 0.96, minimum: 0.98, passedItems: 24, totalItems: 25, missing: [{ stepIndex: 2, item: 'after_screenshot', passed: false }] },
            privacyLeakage: { passed: true, count: 0, findings: [] },
            zeroResidual: { passed: false, count: 1 },
            duplicateSideEffects: { passed: true, duplicateResourceReferences: 0, unknownSideEffectOutcomes: 0 }
          }
        }
      }
    } as any;
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /传统测试编辑器/);
    await user.click(screen.getByRole('button', { name: '固定计划执行' }));
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await screen.findByText('可审核执行计划');

    await user.click(screen.getAllByText(/电商安全语义/)[1]);
    await user.click(screen.getByLabelText('步骤 2 启用电商安全语义'));
    await user.selectOptions(screen.getByLabelText('步骤 2 电商动作'), 'add_cart');
    await user.selectOptions(screen.getByLabelText('步骤 2 业务对象'), 'cartLineId');
    await user.type(screen.getByLabelText('步骤 2 业务引用'), 'resource:E2E_CART_LINE_1');
    await user.type(screen.getByLabelText('步骤 2 执行前状态'), 'absent');
    await user.type(screen.getByLabelText('步骤 2 清理动作'), 'remove cart line');
    await user.click(screen.getByLabelText('步骤 2 E2E 资源归属'));
    await user.selectOptions(screen.getByLabelText('步骤 2 资源台账'), 'register');
    await user.click(screen.getByLabelText('步骤 2 启用电商行作用域'));
    await user.type(screen.getByLabelText('步骤 2 作用域锚点'), 'E2E Product A');
    fireEvent.change(screen.getByLabelText('步骤 2 作用域排除标记'), {
      target: { value: '[data-ad]' },
    });

    await user.click(screen.getByRole('button', { name: /审核并校验计划/ }));
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));

    const runCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/runs') && init?.method === 'POST');
    const payload = JSON.parse(String(runCall?.[1]?.body));
    expect(payload.plan.steps[1].commerce).toEqual({
      action: 'add_cart',
      targetKind: 'cartLineId',
      targetRef: 'resource:E2E_CART_LINE_1',
      beforeState: 'absent',
      cleanupAction: 'remove cart line',
      e2eOwned: true,
      ledgerOperation: 'register'
    });
    expect(payload.plan.steps[1].commerceScope).toEqual({
      kind: 'product_card',
      container: { css: '[data-commerce-item]' },
      anchor: { text: 'E2E Product A' },
      excludedMarkers: [{ css: '[data-ad]' }],
      maxScrollAttempts: 4
    });
    expect(await screen.findByText('需要人工处置')).toBeInTheDocument();
    expect(screen.getByText(/cartLineId · aaaaaaaaaaaa/)).toBeInTheDocument();
    expect(screen.getByText('96.00%')).toBeInTheDocument();
    expect(screen.getByText('未通过')).toBeInTheDocument();
  });

  it('uses a goal-first default form and keeps effective optional fields in advanced settings', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /传统测试编辑器/);
    await user.click(screen.getByRole('button', { name: '固定计划执行' }));

    expect(screen.getByLabelText('目标网站地址')).toBeVisible();
    expect(screen.getByLabelText('当前测试目标')).toBeVisible();
    expect(screen.getByLabelText('期望结果')).toBeVisible();
    expect(screen.getByLabelText('执行角色')).not.toBeVisible();
    expect(screen.getByText('可复用场景库（可选）')).toBeVisible();
    await user.click(screen.getByText('高级设置', { selector: 'summary' }));
    expect(screen.getByLabelText('执行角色')).toBeVisible();
    expect(screen.getByLabelText('前置条件')).toBeVisible();
  });

  it('stores a model profile and probes it without rendering the API key', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: '模型中心' }));
    await user.type(screen.getByLabelText('档案名称'), '主测试模型');
    await user.type(screen.getByLabelText('模型名称'), 'gpt-5.6-terra');
    const keyInput = screen.getByLabelText('API Key');
    expect(keyInput).toHaveAttribute('type', 'password');
    await user.type(keyInput, 'new-test-key');
    await user.click(screen.getByRole('button', { name: /保存模型档案/ }));
    await user.click(screen.getByRole('button', { name: /验证并设为当前模型/ }));
    expect(await screen.findByText(/能力探针通过：gpt-5.6-terra/)).toBeInTheDocument();
    const calls = vi.mocked(fetch).mock.calls;
    const request = calls.find(([url, init]) => String(url).endsWith('/api/ai/profiles') && init?.method === 'POST');
    expect(request?.[1]?.body).toContain('new-test-key');
    expect(document.body.textContent).not.toContain('new-test-key');
  });

  it('opens a real screenshot artifact from a completed run', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /传统测试编辑器/);
    await user.click(screen.getByRole('button', { name: '固定计划执行' }));
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
    expect(screen.getByText('故障安全恢复 · succeeded_after_retry')).toBeInTheDocument();
  });

  it('keeps a failed run report readable when path review was not generated', async () => {
    reviewUnavailable = true;
    detailRunResponse = {
      ...backendRun,
      status: 'system_error', execution_status: 'system_error',
      completion_reason: 'runner_exception', goal_status: 'incomplete',
      goal_summary: '导航超时，已保留失败截图和错误信息',
    };

    const report = await api.getReport(backendRun.run_id);

    expect(report.run.status).toBe('system_error');
    expect(report.run.goalSummary).toContain('导航超时');
    expect(report.pathReview).toBeUndefined();
  });

  it('restores the original website and goal before retrying a recent failed run', async () => {
    const failedGoal = '检查页面标题和页面状态，确认网站是否正常，只读取';
    const failedRun = {
      ...backendRun,
      status: 'system_error', execution_status: 'system_error',
      completion_reason: 'runner_exception', scenario_goal: failedGoal,
      base_url_summary: 'https://example.com', goal_status: 'incomplete',
    } as any;
    historyRuns = [failedRun];
    detailRunResponse = failedRun;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('最近会话');

    await user.click(screen.getByRole('button', { name: /管理员登录验收/ }));

    expect(screen.getByRole('textbox', { name: /测试中断了/ })).toHaveValue(failedGoal);
  });

  it('shows Docker isolation without mislabeling it as a Windows Job', async () => {
    startRunResponse = {
      ...backendRun,
      runner_isolation: {
        mode: 'docker_container', container_name: 'ai-gui-run-1', image: 'ai-gui-runner:1.30.00',
        root_filesystem_read_only: true, memory_limit_mb: 2048,
        container_private_network_allowed: false,
        network_policy: 'container_egress_firewall+playwright_request_guard', forced_termination: false
      }
    } as any;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /传统测试编辑器/);
    await user.click(screen.getByRole('button', { name: '固定计划执行' }));
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await user.click(await screen.findByRole('button', { name: /审核并校验计划/ }));
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));

    expect(await screen.findByText('容器隔离 · ai-gui-runner:1.30.00 · 根目录只读 · 默认私网阻断 · 2048 MB')).toBeInTheDocument();
    expect(screen.queryByText(/Job 未绑定/)).not.toBeInTheDocument();
  });

  it('starts an asynchronous run and exposes the real cancel request', async () => {
    startRunResponse = { ...backendRun, status: 'queued', steps: [], assertions: [], completion_reason: 'queued' };
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /传统测试编辑器/);
    await user.click(screen.getByRole('button', { name: '固定计划执行' }));
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await user.click(await screen.findByRole('button', { name: /审核并校验计划/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /启动真实浏览器测试/ })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));
    const stop = await screen.findByRole('button', { name: /终止执行/ });
    expect(stop).toBeEnabled();
    await user.click(stop);

    expect(await screen.findByText(/已请求停止执行/)).toBeInTheDocument();
    const startCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/api/runs') && init?.method === 'POST');
    expect(startCall?.[1]?.body).toContain('"asyncExecution":true');
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/cancel'))).toBe(true);
  });

  it('replaces the useless stop button with an editable retry path after a system error', async () => {
    const failedRun = {
      ...backendRun, status: 'system_error', steps: [], assertions: [],
      completion_reason: 'container_runner_exception', system_error: 'browser launch failed',
      checkpoint: {
        version: 1, runId: 'run-checkpoint-1', status: 'system_error',
        currentGoal: '确认订单列表可查询', currentUrl: 'https://example.test/orders',
        currentHost: 'example.test', pageFingerprint: 'fingerprint-1',
        lastSafeStepIndex: 1, safeReadOnlySteps: [{ index: 1, action: 'navigate', safeToSkip: true }],
        pendingWriteRevalidations: [],
        recoveryPolicy: {
          reobserveBeforeResume: true, skipOnlyVerifiedReadOnly: true,
          revalidateWritesBeforeContinue: true, failClosedOnPageMismatch: true
        }
      }
    } as any;
    historyRuns = [failedRun];
    detailRunResponse = failedRun;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(await screen.findByRole('button', { name: /管理员登录验收/ }));

    expect(await screen.findByText('测试没有成功启动')).toBeVisible();
    expect(screen.getByText('本次测试未完成')).toBeVisible();
    expect(screen.queryByText('AI 正在测试')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('如需修改目标，请用普通中文告诉 AI……')).toBeVisible();
    expect(screen.getByRole('button', { name: /从上次安全步骤继续/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /从头重试/ })).toBeVisible();
    expect(screen.queryByRole('button', { name: /终止执行/ })).not.toBeInTheDocument();
  });

  it('reuses a saved login session when retrying a failed account task', async () => {
    const failedRun = {
      ...backendRun,
      run_id: 'failed-account-run',
      plan_name: 'JD retry session',
      status: 'system_error',
      steps: [],
      assertions: [],
      project_id: project.id,
      scenario_goal: '查看我的订单并确认登录后的账号页面',
      completion_reason: 'runner_process_interrupted',
      goal_status: 'incomplete'
    } as any;
    historyRuns = [failedRun];
    detailRunResponse = failedRun;
    savedProjects = [project];
    savedSessionResponse = {
      projectId: project.id,
      importedAt: '2026-08-12T00:00:00Z',
      cookieCount: 3,
      originCount: 1,
      domains: ['example.com'],
      expiresAt: '2033-05-18T00:00:00Z',
      expiryStatus: 'active',
      expiredCookieCount: 0,
      encryption: 'Windows DPAPI / CurrentUser'
    };
    modelProfiles = [{
      id: 'model-profile-1', name: '测试模型', provider: 'openai', protocol: 'responses',
      baseUrl: 'https://api.example.test', model: 'gpt-test', apiKey: null,
      keyConfigured: true, isActive: true, connectionStatus: 'connected',
      verifiedModelId: 'gpt-test',
      capabilities: { schema: 'passed', agentDecision: 'passed', multiTurn: 'passed', vision: 'passed' },
      lastTestedAt: '2026-08-12T00:00:00Z', createdAt: '2026-08-12T00:00:00Z', updatedAt: '2026-08-12T00:00:00Z'
    }];
    activeModelProfileId = 'model-profile-1';
    startRunResponse = {
      ...backendRun,
      run_id: 'retried-account-run',
      plan_name: 'JD retry session',
      project_id: project.id,
      scenario_goal: '查看我的订单并确认登录后的账号页面',
      status: 'running',
      steps: [],
      assertions: []
    } as any;

    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(await screen.findByRole('button', { name: /JD retry session/ }));
    await user.click(screen.getByRole('button', { name: /从头重试/ }));

    expect(await screen.findByText('AI 已开始操作网站；左侧画面会随每一步更新。')).toBeVisible();
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url, init]) =>
      String(url).endsWith('/api/agent-runs') && init?.method === 'POST'
    )).toBe(true));
    expect(vi.mocked(fetch).mock.calls.some(([url, init]) =>
      String(url).includes('/session-recordings') && init?.method === 'POST'
    )).toBe(false);
  });

  it('continues a completed goal in the same run instead of starting a new run', async () => {
    const waitingRun = {
      ...backendRun,
      status: 'waiting_for_clarification',
      completion_reason: 'agent_goal_completed_waiting_follow_up',
      pending_clarification: {
        id: 'clarification-follow-up-1', round: 0,
        question: '当前任务已完成，可以继续告诉 AI 下一项要测试什么，或选择结束本次测试。',
        requested_at: '2026-07-25T02:00:00Z'
      },
      clarification_history: []
    } as any;
    historyRuns = [waitingRun];
    detailRunResponse = waitingRun;
    startRunResponse = waitingRun;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(await screen.findByRole('button', { name: /管理员登录验收/ }));

    expect((await screen.findAllByText('当前任务已完成'))[0]).toBeVisible();
    expect(screen.getByRole('alert', { name: '任务已完成，可继续对话' })).toBeVisible();
    expect(screen.getAllByText('成功')[0]).toBeVisible();
    await user.type(screen.getByLabelText('下一项测试要求'), '继续检查当前页面标题');
    await user.click(screen.getByRole('button', { name: '发送并继续' }));

    await screen.findByText('新要求已发送，AI 将在当前页面继续。');
    const clarificationCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/clarification') && init?.method === 'POST');
    expect(clarificationCall?.[1]?.body).toContain('继续检查当前页面标题');
    expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).endsWith('/api/agent-runs') && init?.method === 'POST')).toHaveLength(0);
  });

  it('never presents a zero-step model outage as a successful completed task', async () => {
    const outageRun = {
      ...backendRun,
      status: 'waiting_for_clarification',
      completion_reason: 'waiting_for_clarification',
      goal_status: 'in_progress',
      goal_summary: '任务尚未完成，等待 AI 服务恢复',
      steps: [],
      model_calls: 0,
      pending_clarification: {
        id: 'clarification-model-outage', round: 0, kind: 'model_recovery',
        question: 'AI 服务暂时无法连接，当前网页、登录状态和已完成结果都已保留。请发送“重试”继续当前任务。',
        requested_at: '2026-08-12T00:40:46+08:00'
      },
      clarification_history: []
    } as any;
    historyRuns = [outageRun];
    detailRunResponse = outageRun;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(await screen.findByRole('button', { name: /管理员登录验收/ }));

    expect(await screen.findByText('等待 AI 恢复')).toBeVisible();
    expect(screen.getByText('任务尚未完成，AI 等待恢复')).toBeVisible();
    expect(screen.getByRole('alert', { name: 'AI 服务暂时不可用' })).toBeVisible();
    expect(screen.getByRole('button', { name: '重试当前任务' })).toBeVisible();
    expect(screen.queryByText('当前任务已完成')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert', { name: '任务已完成，可继续对话' })).not.toBeInTheDocument();
  });

  it('labels session finalization as ending instead of a new follow-up request', async () => {
    const waitingRun = {
      ...backendRun,
      status: 'waiting_for_clarification',
      completion_reason: 'agent_goal_completed_waiting_follow_up',
      pending_clarification: {
        id: 'clarification-follow-up-end', round: 0,
        question: '当前任务已完成，可以继续告诉 AI 下一项要测试什么，或选择结束本次测试。',
        requested_at: '2026-07-25T02:00:00Z'
      },
      clarification_history: []
    } as any;
    historyRuns = [waitingRun];
    detailRunResponse = waitingRun;
    startRunResponse = waitingRun;
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(await screen.findByRole('button', { name: /管理员登录验收/ }));

    await user.click(await screen.findByRole('button', { name: '结束本次测试' }));

    expect(await screen.findByText('正在结束本次测试并整理最终报告…')).toBeVisible();
    const clarificationCall = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/clarification') && init?.method === 'POST');
    expect(clarificationCall?.[1]?.body).toContain('结束本次测试');
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
    await openAdvancedPage(user, /传统测试编辑器/);
    await user.click(screen.getByRole('button', { name: '固定计划执行' }));
    await user.click(screen.getByRole('button', { name: /生成规则测试计划/ }));
    await user.click(await screen.findByRole('button', { name: /审核并校验计划/ }));
    await user.click(screen.getByRole('button', { name: /启动真实浏览器测试/ }));

    expect(await screen.findByRole('alert', { name: '高风险动作确认' })).toHaveTextContent('删除客户');
    await user.click(screen.getByRole('button', { name: /单次批准/ }));

    expect(await screen.findByText(/该高风险动作已获单次批准/)).toBeInTheDocument();
    const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/confirmation') && init?.method === 'POST');
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ confirmationId: 'confirmation-test-1', decision: 'approved', actor: 'local_user' });
  });

  it('starts Agent-first exploration from the natural-language goal without a fixed plan', async () => {
    startRunResponse = { ...backendRun, status: 'queued', steps: [], assertions: [], completion_reason: 'queued' };
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: '模型中心' }));
    await user.type(screen.getByLabelText('档案名称'), 'Agent 模型');
    await user.type(screen.getByLabelText('模型名称'), 'gpt-5.6-terra');
    await user.type(screen.getByLabelText('API Key'), 'agent-test-key');
    await user.click(screen.getByRole('button', { name: /保存模型档案/ }));
    await user.click(screen.getByRole('button', { name: /验证并设为当前模型/ }));
    await screen.findByText(/能力探针通过：gpt-5.6-terra/);
    await user.click(screen.getByRole('button', { name: '开始测试' }));
    expect(await screen.findByRole('heading', { name: /告诉 AI 你要测试哪个网站/ })).toBeInTheDocument();
    await openAdvancedPage(user, /传统测试编辑器/);
    await user.click(screen.getByLabelText(/授权当前网站的脱敏 DOM/));
    await user.click(screen.getByLabelText(/授权当前网站的脱敏截图/));
    const visualFallback = screen.getByLabelText(/启用截图视觉 fallback/);
    expect(visualFallback).not.toBeChecked();
    await user.click(visualFallback);
    await user.click(screen.getByLabelText(/启用 Playwright MCP 补充只读观察/));
    await user.selectOptions(screen.getByLabelText('Agent 开源运行时策略'), 'stagehand');
    await user.click(screen.getByRole('button', { name: /启动逐步 Agent 探索/ }));

    const call = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/api/agent-runs'));
    expect(call?.[1]?.body).toContain('"goal"');
    expect(call?.[1]?.body).not.toContain('agent-test-key');
    const payload = JSON.parse(String(call?.[1]?.body));
    expect(payload).toMatchObject({ targetUrl: plan.base_url, profileId: 'model-profile-1', enableVisualFallback: true, headless: false, openSourceObservationProvider: 'playwright-mcp', openSourceExecutionProvider: 'stagehand' });
    expect(payload).not.toHaveProperty('plan');
  });

  it('saves an enterprise project configuration before scanning', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /网站与登录配置/);
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
    await user.click(screen.getByText('电商交易安全配置'));
    await user.click(screen.getByLabelText('启用电商动作前门禁与隐私遮罩'));
    await user.selectOptions(screen.getByLabelText('环境层级'), 'isolated_transaction');
    await user.type(screen.getByLabelText('专用账号密钥别名'), 'jd_buyer_account');
    await user.click(screen.getByLabelText('支付／退款沙箱驱动可用'));
    fireEvent.change(screen.getByLabelText('PII 截图遮罩选择器（每行一项）'), { target: { value: '[data-testid="mobile"]' } });
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
      },
      commerceProfile: {
        enabled: true, environment: 'isolated_transaction', accountRef: 'JD_BUYER_ACCOUNT',
        sandboxDriver: true, e2eResourcePrefix: 'E2E_', piiMaskSelectors: ['[data-testid="mobile"]']
      },
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
    await openAdvancedPage(user, /网站与登录配置/);
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

  it('creates, edits, selects, and executes a persisted project environment', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await openAdvancedPage(user, /网站与登录配置/);
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
    await user.click(screen.getByRole('button', { name: '固定计划执行' }));
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
    await openAdvancedPage(user, /网站与登录配置/);
    await user.type(screen.getByLabelText('项目名称'), '企业测试站');
    await user.type(screen.getByLabelText('Base URL'), 'https://example.com');
    await user.click(screen.getByRole('button', { name: /保存项目配置/ }));
    await user.click(screen.getByRole('button', { name: /用该地址新建测试/ }));
    await user.click(screen.getByRole('button', { name: '固定计划执行' }));
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
    await openAdvancedPage(user, /网站与登录配置/);
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
    await user.click(screen.getByRole('button', { name: '测试记录' }));
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
    await user.click(screen.getByRole('button', { name: '测试记录' }));

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
      open_source_observation: {
        provider: 'playwright-mcp', status: 'ready', evidencePath: 'observations/open-source-observation.json',
        url: 'http://127.0.0.1:8765/', title: '云衡 CRM · 演示环境', toolCount: 23,
        unsafeTools: ['browser_evaluate'], actionPolicy: 'read_only_navigate_snapshot_only'
      },
      generated_test: { source_path: 'generated-test.spec.ts', source: 'test.skip(true, "包含 D 级人工步骤")', stability_level: 'D', supported_replay_modes: [], ci_eligible: false, ci_recommendation: '包含暂不可自动化步骤，需人工处理', manual_steps: ['触摸硬件安全密钥'] }
    };
    historyRuns = [detailRunResponse];
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('真实执行服务已连接');
    await user.click(screen.getByRole('button', { name: '测试记录' }));
    await user.click(await screen.findByRole('button', { name: '详情' }));

    expect(await screen.findByRole('heading', { name: '回归路径审核' })).toBeInTheDocument();
    expect(screen.getByText('验证登录并创建客户')).toBeInTheDocument();
    expect(screen.getByText('场景目标未完成；断言通过 2/3')).toBeInTheDocument();
    expect(screen.getByText('审核终态 等待确认 1 项')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '视觉 fallback 时间线' })).toHaveTextContent('Canvas 缺少结构化目标');
    expect(screen.getByRole('region', { name: '开源观察证据' })).toHaveTextContent('23');
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
