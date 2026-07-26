import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, Bot, ChevronRight, ClipboardCheck, Copy, Download, Eye, EyeOff, FileText, Globe2, History, KeyRound, LogIn, Monitor, Play, RefreshCw, Save, ScanSearch, Settings2, ShieldAlert, ShieldCheck, SquarePen, StopCircle, Trash2 } from 'lucide-react';
import { api } from './services/api';
import type { AcceptanceBatch, AIConnectionResult, AISettings, ActionType, CesiumAcceptanceSuite, CompatibilityReport, EnvironmentConfig, EnvironmentDraft, FileAsset, Finding, GAEAcceptanceBatch, GAEAcceptanceCatalog, GAEL4RunResult, GAEL4Workflow, GeneratedTest, Locator, PlanAssertion, PlanStep, ProjectConfig, ProjectDraft, Report, ReviewedStep, RunPathReview, ScenarioConfig, ScenarioDraft, SessionMetadata, TestCaseDraft, TestPlan, TestRun } from './services/types';

type Page = 'start' | 'compose' | 'settings' | 'overview' | 'projects' | 'ai' | 'new' | 'run' | 'history' | 'report' | 'acceptance' | 'spatial-acceptance' | 'simulation-acceptance';
type Connection = 'checking' | 'connected' | 'disconnected';
type AIConnection = 'untested' | 'testing' | 'connected' | 'failed';
type PlannerMode = 'rules' | 'ai';
type ExecutionStrategy = 'fixed' | 'agent';
type ApprovalMode = 'ask' | 'delegate' | 'full';

const statusText = {
  queued: '排队中',
  passed: '成功',
  failed: '失败',
  error: '错误',
  running: '运行中',
  waiting_for_clarification: '等待澄清',
  pending_review: '待审核',
  stopped: '已停止',
  skipped: '已跳过',
  pending_confirmation: '等待确认',
  issues_found: '发现问题',
  incomplete: '未完成',
  system_error: '系统错误',
  cancelled: '已取消'
} as const;

const activeRunStatuses = new Set(['queued', 'running', 'waiting_for_clarification', 'pending_confirmation']);
const recoverableRunStatuses = new Set(['passed', 'failed', 'error', 'issues_found', 'incomplete', 'system_error', 'cancelled', 'stopped']);

export function displayedRunStatus(run: Pick<TestRun, 'status' | 'executionStatus' | 'completionReason'>): TestRun['status'] {
  if (run.completionReason === 'model_error' || run.executionStatus === 'system_error') return 'system_error';
  return run.status;
}

export function runCanRecover(run: Pick<TestRun, 'status' | 'executionStatus' | 'completionReason'>): boolean {
  return recoverableRunStatuses.has(displayedRunStatus(run));
}

export function isModelRecoveryWait(run: Pick<TestRun, 'completionReason'>): boolean {
  return run.completionReason === 'agent_waiting_for_model_recovery';
}

export function shouldAnalyzeScopeAfterLogin(verified: boolean, connection: AIConnection): boolean {
  return verified && connection === 'connected';
}

function resultClassificationText(value: TestRun['resultClassification']): string {
  return {
    agent_passed: 'Agent 通过', agent_failed: 'Agent 失败',
    fixed_passed: '固定计划通过', fixed_failed: '固定计划失败',
    fallback_passed: 'fallback 通过', unverified: '未验证',
  }[value];
}

export function resolveTargetHost(targetUrl: string, projectBaseUrl?: string): string {
  const candidate = /^https?:\/\//i.test(targetUrl.trim()) ? targetUrl.trim() : projectBaseUrl?.trim();
  if (!candidate) {
    throw new Error('目标网站使用环境占位符时必须先选择已保存项目。');
  }
  try {
    const parsed = new URL(candidate);
    if (!parsed.hostname) throw new Error('missing hostname');
    return parsed.hostname;
  } catch {
    throw new Error('目标网站地址必须是完整的 http:// 或 https:// URL。');
  }
}

function isolationText(
  isolation: NonNullable<TestRun['runnerIsolation']>,
  includeTermination = false
) {
  if (isolation.mode === 'docker_container') {
    const network = isolation.containerPrivateNetworkAllowed ? '显式私网例外' : '默认私网阻断';
    const termination = includeTermination ? ` · 强停 ${isolation.forcedTermination ? '是' : '否'}` : '';
    return `容器隔离 · ${isolation.image || 'Runner 镜像'} · 根目录${isolation.rootFilesystemReadOnly ? '只读' : '状态未知'} · ${network} · ${isolation.memoryLimitMb || '-'} MB${termination}`;
  }
  if (includeTermination) {
    return `隔离 ${isolation.mode} · Job ${isolation.windowsJobAssigned ? '已绑定' : '未绑定'} · 强停 ${isolation.forcedTermination ? '是' : '否'}`;
  }
  return `隔离进程 · Job ${isolation.windowsJobAssigned ? '已绑定' : '未绑定'} · ${isolation.memoryLimitMb || '-'} MB`;
}

const initialDraft: TestCaseDraft = {
  name: '客户管理访问验收',
  targetUrl: 'http://127.0.0.1:8765',
  flow: '确认当前测试账号可以进入“客户管理”页面',
  role: '网站使用者',
  preconditions: '目标网站已启动；仅使用授权的测试环境和脱敏账号。',
  expectation: '确认看到“客户管理”',
  testData: {},
  forbiddenActions: []
};

const initialAISettings: AISettings = {
  protocol: 'responses',
  baseUrl: 'https://api.openai.com/v1',
  model: 'gpt-5.6-terra',
  apiKey: ''
};

const initialProject: ProjectDraft = {
  name: '', baseUrl: '', allowedHosts: [], forbiddenActions: [], allowPrivateNetwork: false, onboardingLevel: 'L0',
  businessContext: { description: '', terminology: {}, objectTypes: [], stateModels: {}, exampleGoals: [], operatingBoundaries: [], allowedActions: [], bridgeCapabilities: [], bridgeSemanticTargets: {} },
  commerceProfile: { enabled: false, environment: 'production_readonly', accountRef: null, productionReversibleWriteAuthorized: false, sandboxDriver: false, fixedProductRef: null, fixedAddressRef: null, writtenAuthorizationRef: null, automaticCancellationVerified: false, e2eResourcePrefix: 'E2E_', piiMaskSelectors: [] },
  limits: { maxSteps: 50, timeoutSeconds: 600, maxModelCalls: 20 }
};

const absoluteForbiddenActions = ['实际支付', '代输密码', '代输验证码', '修改账号安全设置', '删除非测试数据'];
const jdLoginHosts = ['jd.com', 'www.jd.com', 'passport.jd.com', 'plogin.m.jd.com', 'm.jd.com', 'qr.m.jd.com'];
const trackingQueryKeys = new Set(['fbclid', 'gclid', 'dclid', 'msclkid', 'mc_cid', 'mc_eid', 'yclid', 'igshid', 'vero_conv', 'vero_id', 'wickedid']);

function normalizeWebsiteUrl(value: string): URL {
  const source = value.trim();
  if (!source) throw new Error('请先输入要测试的网站地址。');
  try {
    const url = new URL(/^https?:\/\//i.test(source) ? source : `https://${source}`);
    if (!url.hostname) throw new Error();
    return url;
  } catch {
    throw new Error('这个网址无法识别，请输入类似 https://www.example.com 的完整地址。');
  }
}

export function normalizeBeginnerTarget(url: URL): { url: URL; changed: boolean } {
  const normalized = new URL(url.href);
  let changed = Boolean(normalized.hash);
  normalized.hash = '';
  [...normalized.searchParams.keys()].forEach((key) => {
    if (key.toLowerCase().startsWith('utm_') || trackingQueryKeys.has(key.toLowerCase())) {
      normalized.searchParams.delete(key);
      changed = true;
    }
  });
  return { url: normalized, changed };
}

export function detectedModules(report: CompatibilityReport | null): string[] {
  if (!report) return ['页面能否正常打开'];
  const summary = report.pageSummary;
  const observedControls = report.scannedPages.flatMap((page) => page.controls || []);
  const observedControlText = observedControls.flatMap((control) => [
    control.name || '',
    control.locator?.accessibleName || '',
    control.locator?.label || '',
    control.locator?.placeholder || '',
    control.locator?.href || ''
  ]).join(' ').toLowerCase();
  const observedText = [
    report.title,
    ...report.navigationEntries,
    ...report.capabilities,
    ...report.suggestedScenarios,
    ...report.scannedPages.flatMap((page) => [
      page.title,
      page.pageType,
      ...page.headings,
      ...(page.controls || []).flatMap((control) => [
        control.name || '',
        control.role || '',
        control.locator?.accessibleName || '',
        control.locator?.label || '',
        control.locator?.placeholder || '',
        control.locator?.href || ''
      ])
    ])
  ].join(' ').toLowerCase();
  const modules = ['页面能否正常打开'];
  if (summary.buttons + summary.links + summary.inputs + summary.selects + summary.textareas > 0) modules.push('页面上实际看到的按钮、输入框和链接');
  if (report.navigationEntries.length || report.scannedPages.length > 1 || report.scannedPages.some((page) => (page.regions || []).some((region) => region.role === 'navigation'))) modules.push('当前网站实际识别到的栏目和页面跳转');
  if (summary.unlabeledControls > 0 || summary.duplicateIds > 0) modules.push('控件是否清楚易用、有没有无法识别的按钮');
  if (report.consoleErrors.length || report.failedRequests.length) modules.push('扫描中已经出现的报错和失败请求');
  if (report.authenticationSignals.some((item) => /登录|账号|密码/.test(item) && !/未发现|没有发现/.test(item))) modules.push('账号登录入口和登录后的功能');
  if (summary.canvases + summary.webglRegions > 0 || report.visualAreas.length) modules.push('当前页面实际存在的画布、三维或可视化区域');
  if (summary.fileInputs > 0 || /(?:^|\s)(?:upload|download)(?:\s|$)|上传文件|选择文件|文件上传|导出(?:文件|数据|报告|结果)|下载(?:文件|数据|报告|结果)/.test(observedControlText)) modules.push('当前网站实际提供的文件上传或下载');
  if (summary.loadingSignals > 0 || report.asyncPatterns.length) modules.push('页面加载、后台任务或长时间等待');
  if (/商品|购物车|结算|订单|商城|sku|product|cart|checkout/.test(observedText)) modules.push('商品、购物车和下单前流程（绝不付款）');
  return [...new Set(modules)];
}

export function websiteRequiresLogin(report: Pick<CompatibilityReport, 'authenticationSignals' | 'blockedAreas'>): boolean {
  return report.authenticationSignals
    .filter((item) => !/未发现|没有发现|未检测到/.test(item))
    .some((item) => /登录墙|登录表单|登录拦截|未登录无法|必须登录|需要登录后|网站仍要求登录|登录(?:态|状态)可能未生效|仍出现登录信号/.test(item))
    || report.blockedAreas.some((item) => /登录(?:态|状态)(?:可能)?未生效|会话.*失效|已被网站拒绝|需要登录|必须登录/.test(item));
}

export function goalRequiresLogin(goal: string): boolean {
  return /购物车|收藏|关注|订单|结算|收货地址|个人中心|我的账户|账号信息|提交订单|付款方式/.test(goal);
}

function acceptanceStatusText(status: string): string {
  return ({
    blocked: '缺少账号、数据或授权',
    observed_read_only: '只查看过，尚未完整测试',
    unverified: '尚未测试',
    passed: '已通过',
    failed: '未通过'
  } as Record<string, string>)[status] || status;
}

function effectLevelText(level: string): string {
  if (level === 'read_only') return '只查看，不修改';
  if (level === 'session_only') return '只影响本次页面';
  if (level === 'isolated_local_write') return '仅下载到隔离目录';
  if (level === 'forbidden') return '系统禁止';
  if (level.startsWith('high_risk') || level.startsWith('sensitive')) return '高风险，每次确认';
  return '会创建可清理的测试数据';
}

export function isLowRiskConfirmation(action: string, target: string, rule: string): boolean {
  const value = `${action} ${target} ${rule}`.toLowerCase();
  if (/pay|支付|付款|密码|验证码|指纹|刷脸|delete|删除|account|账号|security|安全|refund|退款|publish|发布|submit|提交订单|下单/.test(value)) return false;
  return /fill|type|input|填写|输入|click|select|勾选|选择|展开|关闭|navigate|导航|scroll|滚动|filter|筛选/.test(value);
}

const initialScenario: ScenarioDraft = {
  name: '',
  preconditions: [],
  goal: '',
  testData: {},
  expectedResults: [],
  forbiddenActions: [],
  commerceSteps: [],
  executionSteps: []
};

const initialEnvironment: EnvironmentDraft = {
  name: '',
  variables: {},
  secretRefs: {},
  ignoreRules: [],
  screenshotMaskSelectors: [],
  viewport: { width: 1440, height: 960 },
  deviceScaleFactor: 1,
  appBridge: { enabled: false, globalName: '__WEB_AI_TEST__', adapter: 'generic' },
  artifactRetentionDays: 30
};

function StatusBadge({ status }: { status: keyof typeof statusText }) {
  return <span className={`status status-${status}`}>{statusText[status]}</span>;
}

export default function App({
  initialPage = 'start'
}: {
  initialPage?: Page;
}) {
  const [page, setPage] = useState<Page>(initialPage);
  const [cesiumSuite, setCesiumSuite] = useState<CesiumAcceptanceSuite | null>(null);
  const [cesiumPriority, setCesiumPriority] = useState<'all' | 'P0' | 'P1' | 'P2'>('all');
  const [cesiumStatus, setCesiumStatus] = useState<'all' | 'unverified' | 'blocked' | 'observed_read_only' | 'passed' | 'failed'>('all');
  const [websiteInput, setWebsiteInput] = useState('');
  const [fullCheckRequested, setFullCheckRequested] = useState(false);
  const [websiteScope, setWebsiteScope] = useState<string[]>(['页面能否正常打开']);
  const [scopeSource, setScopeSource] = useState<'scan' | 'ai'>('scan');
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>('ask');
  const [settingsReturnPage, setSettingsReturnPage] = useState<Page>('start');
  const autoApprovedConfirmationIds = useRef<Set<string>>(new Set());
  const [draft, setDraft] = useState(initialDraft);
  const [plan, setPlan] = useState<TestPlan | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [run, setRun] = useState<TestRun | null>(null);
  const [history, setHistory] = useState<TestRun[]>([]);
  const [selectedRunIds, setSelectedRunIds] = useState<Set<string>>(new Set());
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState<Connection>('checking');
  const [message, setMessage] = useState('正在连接真实执行服务…');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [aiSettings, setAISettings] = useState<AISettings>(initialAISettings);
  const [aiConnection, setAIConnection] = useState<AIConnection>('untested');
  const [aiCapabilities, setAICapabilities] = useState<AIConnectionResult['capabilities'] | null>(null);
  const [aiMessage, setAIMessage] = useState('尚未测试模型连接。配置只保留在当前页面内存中。');
  const [showKey, setShowKey] = useState(false);
  const [plannerMode, setPlannerMode] = useState<PlannerMode>('rules');
  const [executionStrategy, setExecutionStrategy] = useState<ExecutionStrategy>('agent');
  const [clarificationAnswer, setClarificationAnswer] = useState('');
  const [visualFallbackEnabled, setVisualFallbackEnabled] = useState(false);
  const [domModelAuthorized, setDomModelAuthorized] = useState(false);
  const [screenshotModelAuthorized, setScreenshotModelAuthorized] = useState(false);
  const [projects, setProjects] = useState<ProjectConfig[]>([]);
  const [projectDraft, setProjectDraft] = useState<ProjectDraft>(initialProject);
  const [projectTerminology, setProjectTerminology] = useState('{}');
  const [projectStateModels, setProjectStateModels] = useState('{}');
  const [projectBridgeTargets, setProjectBridgeTargets] = useState('{}');
  const [selectedProject, setSelectedProject] = useState<ProjectConfig | null>(null);
  const [fileAssets, setFileAssets] = useState<FileAsset[]>([]);
  const [environments, setEnvironments] = useState<EnvironmentConfig[]>([]);
  const [selectedEnvironment, setSelectedEnvironment] = useState<EnvironmentConfig | null>(null);
  const [environmentDraft, setEnvironmentDraft] = useState<EnvironmentDraft>(initialEnvironment);
  const [environmentVariables, setEnvironmentVariables] = useState('{}');
  const [environmentSecretRefs, setEnvironmentSecretRefs] = useState('{}');
  const [scenarios, setScenarios] = useState<ScenarioConfig[]>([]);
  const [selectedScenario, setSelectedScenario] = useState<ScenarioConfig | null>(null);
  const [scenarioDraft, setScenarioDraft] = useState<ScenarioDraft>(initialScenario);
  const [scenarioTestData, setScenarioTestData] = useState('{}');
  const [compatibility, setCompatibility] = useState<CompatibilityReport | null>(null);
  const [session, setSession] = useState<SessionMetadata | null>(null);
  const [sessionUpload, setSessionUpload] = useState<Record<string, unknown> | null>(null);
  const [sessionFileName, setSessionFileName] = useState('');
  const [recordingId, setRecordingId] = useState<string | null>(null);
  const [loginCheckFailed, setLoginCheckFailed] = useState(false);
  const [acceptanceBatches, setAcceptanceBatches] = useState<AcceptanceBatch[]>([]);
  const [acceptanceBatch, setAcceptanceBatch] = useState<AcceptanceBatch | null>(null);
  const [gaeCatalog, setGAECatalog] = useState<GAEAcceptanceCatalog | null>(null);
  const [gaeWorkflow, setGAEWorkflow] = useState<GAEL4Workflow | null>(null);
  const [gaeBatches, setGAEBatches] = useState<GAEAcceptanceBatch[]>([]);
  const [gaeBatch, setGAEBatch] = useState<GAEAcceptanceBatch | null>(null);
  const [gaeL4Result, setGAEL4Result] = useState<GAEL4RunResult | null>(null);
  const [gaeScenarioBindings, setGAEScenarioBindings] = useState('{}');
  const [gaeL4Bindings, setGAEL4Bindings] = useState('{}');

  const goToAISettings = () => {
    setSettingsReturnPage(page === 'compose' || page === 'run' ? page : 'start');
    setPage('ai');
  };

  useEffect(() => {
    let active = true;
    api.health()
      .then(async (health) => {
        if (!active) return;
        setConnection('connected');
        setMessage(`已连接 ${health.engine} · ${health.planner}`);
        const [runs, savedProjects] = await Promise.all([api.getHistory(), api.getProjects()]);
        if (active) { setHistory(runs); setProjects(savedProjects); }
      })
      .catch((error: Error) => {
        if (!active) return;
        setConnection('disconnected');
        setMessage(error.message);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!run || !activeRunStatuses.has(run.status)) return;
    let active = true;
    const timer = window.setInterval(async () => {
      try {
        const nextRun = await api.getRun(run.id);
        if (!active) return;
        setRun(nextRun);
        if (!activeRunStatuses.has(nextRun.status)) {
          window.clearInterval(timer);
          setHistory(await api.getHistory());
          setMessage(`真实执行结束：${statusText[displayedRunStatus(nextRun)]}`);
        }
      } catch (error) {
        if (active) setMessage(error instanceof Error ? error.message : String(error));
      }
    }, 800);
    return () => { active = false; window.clearInterval(timer); };
  }, [run?.id, run?.status]);

  useEffect(() => {
    const pending = run?.pendingConfirmation;
    if (!pending || autoApprovedConfirmationIds.current.has(pending.id)) return;
    const shouldApprove = approvalMode === 'full'
      || (approvalMode === 'delegate' && isLowRiskConfirmation(pending.action, pending.target, pending.rule));
    if (!shouldApprove) return;
    autoApprovedConfirmationIds.current.add(pending.id);
    api.decideConfirmation(run.id, pending.id, 'approved')
      .then((nextRun) => {
        setRun(nextRun);
        setMessage(approvalMode === 'full' ? '已按“完全访问权限”自动批准该动作。' : '已替你批准同类低风险动作。');
      })
      .catch((error: Error) => setMessage(error.message));
  }, [approvalMode, run?.id, run?.pendingConfirmation?.id]);

  const metrics = useMemo(() => {
    const total = history.length;
    const failed = history.filter((item) => item.status !== 'passed').length;
    const passRate = total ? Math.round(((total - failed) / total) * 100) : 0;
    return { totalCases: total, recentRuns: total, passRate, failed };
  }, [history]);

  const execute = async (task: () => Promise<void>) => {
    setBusy(true);
    setMessage(connection === 'connected' ? '正在处理真实请求…' : message);
    try {
      await task();
    } catch (error) {
      const rawMessage = error instanceof Error ? error.message : String(error);
      setMessage(/Page\.goto|net::ERR_(?:CONNECTION|NETWORK|TIMED_OUT)/i.test(rawMessage)
        ? '网站连接暂时中断，系统已经自动重试但仍未打开。请检查本机网络，稍后再点一次“开始”。'
        : rawMessage);
      if (connection !== 'connected') setConnection('disconnected');
    } finally {
      setBusy(false);
    }
  };

  const generatePlan = () => execute(async () => {
    if (plannerMode === 'ai' && aiConnection !== 'connected') {
      throw new Error('请先在“AI 模型设置”中测试连接成功，再使用 AI 生成计划。');
    }
    if (!draft.targetUrl.trim() || !draft.flow.trim() || !draft.expectation.trim()) {
      throw new Error('请补齐目标网站地址、测试目标和预期结果后再生成计划。');
    }
    if (/\b(?:TBD|TODO)\b|待补充|待填写|\{\{[^}]+\}\}/i.test(JSON.stringify(draft))) {
      throw new Error('场景仍包含待补充的关键数据，请明确填写后再生成；系统不会猜测账号、金额或业务值。');
    }
    const effectiveDraft = {
      ...draft,
      name: draft.name.trim() || draft.flow.trim().slice(0, 80)
    };
    const generated = plannerMode === 'ai'
      ? await api.generateAIPlan(effectiveDraft, aiSettings, selectedProject?.id, selectedEnvironment?.id, selectedScenario?.id)
      : await api.generatePlan(effectiveDraft, selectedProject?.id, selectedEnvironment?.id, selectedScenario?.id);
    setDraft(effectiveDraft);
    setPlan(generated.plan);
    setWarnings(generated.warnings);
    setReviewed(false);
    setMessage(generated.warnings.length
      ? `计划已生成，但有 ${generated.warnings.length} 项需要人工补充，未审核前不能执行。`
      : `${plannerMode === 'ai' ? 'AI' : '规则'}计划已生成，请审核每个动作、定位器和断言。`);
  });

  const updateAISettings = (next: AISettings) => {
    setAISettings(next);
    setAIConnection('untested');
    setAICapabilities(null);
    setVisualFallbackEnabled(false);
    setAIMessage('配置已修改，请重新测试连接。');
  };

  const testAIConnection = () => execute(async () => {
    setAIConnection('testing');
    setAIMessage('正在验证模型连接、结构化输出和多轮上下文……');
    try {
      const result = await api.testAI(aiSettings);
      if (result.capabilities.schema !== 'passed' || result.capabilities.agentDecision !== 'passed' || result.capabilities.multiTurn !== 'passed') {
        throw new Error('模型能力探针未通过，不能用于逐步 Agent。');
      }
      setAICapabilities(result.capabilities);
      setAIConnection('connected');
      setAIMessage(`能力探针通过：${result.verifiedModelId}；真实 Agent 决策通过；多轮上下文通过；看图能力${result.capabilities.vision === 'passed' ? '通过' : `未通过（${result.visionDetail || '模型没有正确识别合成测试图片'}）`}；耗时 ${result.elapsedMs}ms。`);
      setMessage('AI 已通过真实 Agent 决策探针，可以启动逐步测试。');
    } catch (error) {
      setAIConnection('failed');
      setAIMessage(error instanceof Error ? error.message : String(error));
      throw error;
    }
  });

  const reviewPlan = () => execute(async () => {
    if (!plan) return;
    setPlan(await api.reviewPlan(plan));
    setReviewed(true);
    setMessage('计划 Schema 校验通过，允许启动真实浏览器测试。');
  });

  const startRun = () => execute(async () => {
    if (executionStrategy === 'agent' && aiConnection !== 'connected') {
      throw new Error('逐步 Agent 探索需要先连接 AI 模型。');
    }
    if (executionStrategy === 'agent' && (!draft.targetUrl.trim() || !draft.flow.trim())) {
      throw new Error('逐步 Agent 探索需要目标网站地址和当前测试目标。');
    }
    if (executionStrategy === 'agent' && !domModelAuthorized) {
      throw new Error('必须为当前目标网站单独授权模型接收脱敏 DOM。');
    }
    if (executionStrategy === 'agent' && visualFallbackEnabled && !screenshotModelAuthorized) {
      throw new Error('启用视觉 fallback 前必须单独授权截图传模。');
    }
    if (executionStrategy === 'agent' && visualFallbackEnabled && aiCapabilities?.vision !== 'passed') {
      throw new Error('当前模型尚未通过视觉能力探针，不能启用截图视觉 fallback。');
    }
    if (executionStrategy === 'fixed' && (!plan || !reviewed)) {
      throw new Error('固定计划执行前必须生成并审核计划。');
    }
    const nextRun = executionStrategy === 'agent'
      ? await api.startAgentRun(draft, aiSettings, {
          siteHost: resolveTargetHost(draft.targetUrl, selectedProject?.baseUrl),
          allowDom: domModelAuthorized,
          allowScreenshots: screenshotModelAuthorized
        }, selectedProject?.id, selectedScenario?.id, selectedEnvironment?.id, visualFallbackEnabled, approvalMode)
      : await api.startRun(plan!, selectedProject?.id, selectedScenario?.id, selectedEnvironment?.id);
    setRun(nextRun);
    setHistory(await api.getHistory());
    setReport(null);
    setPage('run');
    setMessage(`运行已进入后台：${statusText[nextRun.status]}，页面将持续加载真实步骤。`);
  });

  const cancelRun = () => execute(async () => {
    if (!run || !activeRunStatuses.has(run.status)) return;
    const nextRun = await api.cancelRun(run.id);
    setRun(nextRun);
    setMessage('已请求停止执行；当前浏览器动作结束后将安全关闭并保存已有证据。');
  });

  const decideConfirmation = (decision: 'approved' | 'rejected') => execute(async () => {
    if (!run?.pendingConfirmation) return;
    const lowRisk = isLowRiskConfirmation(run.pendingConfirmation.action, run.pendingConfirmation.target, run.pendingConfirmation.rule);
    const nextRun = await api.decideConfirmation(run.id, run.pendingConfirmation.id, decision);
    setRun(nextRun);
    setMessage(decision === 'approved'
      ? `${lowRisk ? '该操作' : '该高风险动作'}已获单次批准，运行继续。`
      : `${lowRisk ? '该操作' : '该高风险动作'}已拒绝，不会执行。`);
  });

  const answerClarification = (answer = clarificationAnswer) => execute(async () => {
    if (!run?.pendingClarification || !answer.trim()) return;
    const isFollowUp = run.pendingClarification.round === 0;
    const nextRun = await api.answerClarification(run.id, run.pendingClarification.id, answer.trim());
    setRun(nextRun);
    setClarificationAnswer('');
    setMessage(isFollowUp ? '新要求已发送，AI 将在当前页面继续。' : '补充信息已提交，AI 将在当前页面继续。');
  });

  const openHistory = () => execute(async () => {
    setHistory(await api.getHistory());
    setPage('history');
    setMessage('真实运行历史已加载。');
  });

  const toggleRunSelection = (runId: string) => {
    setSelectedRunIds((current) => {
      const next = new Set(current);
      if (next.has(runId)) next.delete(runId); else next.add(runId);
      return next;
    });
  };

  const toggleAllRuns = () => {
    setSelectedRunIds((current) => current.size === history.length
      ? new Set()
      : new Set(history.map((item) => item.id)));
  };

  const deleteSelectedRuns = () => execute(async () => {
    const runIds = [...selectedRunIds];
    if (!runIds.length) return;
    if (!window.confirm(`确定永久删除选中的 ${runIds.length} 份报告及其截图、Trace 和生成代码吗？此操作无法撤销。`)) return;
    const result = await api.deleteRuns(runIds);
    const nextHistory = await api.getHistory();
    setHistory(nextHistory);
    setSelectedRunIds(new Set());
    if (run && runIds.includes(run.id)) setRun(null);
    if (report && runIds.includes(report.run.id)) setReport(null);
    setMessage(`已删除 ${result.count} 份运行报告及其工件。`);
  });

  const cleanupExpiredRuns = () => execute(async () => {
    const result = await api.cleanupRuns();
    setHistory(await api.getHistory());
    setSelectedRunIds(new Set());
    if (run && result.deleted.includes(run.id)) setRun(null);
    if (report && result.deleted.includes(report.run.id)) setReport(null);
    setMessage(result.count
      ? `保留策略已执行，清理 ${result.count} 份到期运行工件。`
      : '保留策略已执行，没有到期运行工件。');
  });

  const openReport = (runId?: string) => execute(async () => {
    const id = runId || run?.id || history[0]?.id;
    if (!id) {
      setReport(null);
      setPage('report');
      return;
    }
    setReport(await api.getReport(id));
    setPage('report');
    setMessage('真实测试报告与截图证据已加载。');
  });

  const reviewFinding = (findingId: string, payload: { status: 'pending_review' | 'confirmed' | 'rejected'; title: string; severity: Finding['severity']; expectedResult: string }) => execute(async () => {
    if (!report) return;
    setReport(await api.reviewFinding(report.run.id, findingId, payload));
    setMessage(payload.status === 'confirmed' ? '问题修改已保存并确认。' : payload.status === 'rejected' ? '问题修改已保存并驳回。' : '问题修改已保存，仍待审核。');
  });

  const savePathReview = (steps: ReviewedStep[]) => execute(async () => {
    if (!report) return;
    setReport(await api.saveRunReview(report.run.id, steps));
    setMessage('审核路径已保存，Playwright 测试已按保留步骤重新编译。');
  });

  const saveGeneratedSource = (source: string) => execute(async () => {
    if (!report) return;
    setReport(await api.saveGeneratedTestSource(report.run.id, source));
    setMessage('Playwright TypeScript 已保存为新修订，下载文件已同步。');
  });

  const replayRun = (mode: 'stable' | 'adaptive') => execute(async () => {
    if (!report) return;
    if (mode === 'adaptive' && !aiSettings.apiKey.trim()) throw new Error('自适应回放需要在 AI 设置中填写本次 API Key');
    const nextRun = await api.replay(report.run.id, mode, mode === 'adaptive' ? aiSettings : undefined);
    setRun(nextRun);
    setHistory(await api.getHistory());
    setPage('run');
    setMessage(`${mode === 'stable' ? '稳定' : '自适应'}回放完成：${statusText[nextRun.status]}`);
  });

  const returnToRunScope = () => execute(async () => {
    if (!run) return;
    const project = projects.find((item) => item.id === run.projectId);
    if (!project) {
      setRun(null);
      setWebsiteInput('');
      setPage('start');
      setMessage('这条历史测试缺少可恢复的网站配置，请重新输入网址开始。');
      return;
    }
    setSelectedProject(project);
    setSelectedEnvironment(null);
    setSelectedScenario(null);
    setProjectDraft({
      name: project.name, baseUrl: project.baseUrl, allowedHosts: project.allowedHosts,
      allowPrivateNetwork: project.allowPrivateNetwork, forbiddenActions: project.forbiddenActions,
      onboardingLevel: project.onboardingLevel, limits: project.limits,
      businessContext: project.businessContext, commerceProfile: project.commerceProfile,
    });
    const report = await api.getCompatibility(project.id);
    setCompatibility(report);
    setWebsiteScope(detectedModules(report));
    setScopeSource('scan');
    setDraft({
      ...initialDraft,
      name: run.caseName,
      targetUrl: project.baseUrl,
      flow: run.scenarioGoal,
      expectation: '',
      preconditions: '用户已授权检查当前网站；需要登录时由用户本人完成；系统绝不代替用户付款。',
    });
    try { setSession(await api.getSession(project.id)); } catch { setSession(null); }
    setDomModelAuthorized(true);
    setScreenshotModelAuthorized(false);
    setExecutionStrategy('agent');
    setPlannerMode('ai');
    setPage('compose');
    setMessage(`已恢复“${project.name}”的网站范围和原测试要求。`);
  });

  const beginWebsite = () => execute(async () => {
    const localNormalized = normalizeBeginnerTarget(normalizeWebsiteUrl(websiteInput));
    let resolved = { url: localNormalized.url.href, changed: localNormalized.changed, redirectChain: [localNormalized.url.href] };
    try { resolved = await api.resolveWebsite(localNormalized.url.href); } catch { /* 扫描仍可使用用户输入继续 */ }
    const url = new URL(resolved.url);
    setWebsiteInput(url.href);
    setFullCheckRequested(false);
    setLoginCheckFailed(false);
    setApprovalMode('ask');
    autoApprovedConfirmationIds.current.clear();
    setMessage('AI 正在安全读取网站结构，不会点击、填写或提交任何内容…');

    let project = projects.find((item) => {
      try { return new URL(item.baseUrl).hostname === url.hostname; } catch { return false; }
    });
    if (!project) {
      const isCommerce = /(^|\.)jd\.com$/i.test(url.hostname);
      const redirectHosts = resolved.redirectChain.map((item) => {
        try { return new URL(item).hostname; } catch { return ''; }
      }).filter(Boolean);
      project = await api.createProject({
        ...initialProject,
        name: url.hostname,
        baseUrl: url.href,
        allowedHosts: [...new Set([url.hostname, ...redirectHosts, ...(isCommerce ? jdLoginHosts : [])])],
        forbiddenActions: absoluteForbiddenActions,
        onboardingLevel: 'L0',
        businessContext: {
          ...initialProject.businessContext,
          description: '由普通用户通过网址接入的网站。系统自动识别可测试能力，技术配置保留在高级设置中。',
          operatingBoundaries: ['只操作已获授权的网站和测试数据', '需要登录时由用户本人登录', '付款步骤必须由用户本人接管'],
          allowedActions: ['浏览页面', '填写测试数据', '执行低风险可逆操作', '创建未支付订单'],
        },
        commerceProfile: isCommerce ? { ...initialProject.commerceProfile, enabled: true } : initialProject.commerceProfile
      });
      setProjects(await api.getProjects());
    }

    setSelectedProject(project);
    setProjectDraft({
      name: project.name, baseUrl: project.baseUrl, allowedHosts: project.allowedHosts,
      allowPrivateNetwork: project.allowPrivateNetwork, forbiddenActions: project.forbiddenActions,
      onboardingLevel: project.onboardingLevel, limits: project.limits,
      businessContext: project.businessContext, commerceProfile: project.commerceProfile
    });
    const report = await api.scanProject(project.id);
    setCompatibility(report);
    const scanScope = detectedModules(report);
    setWebsiteScope(scanScope);
    setScopeSource('scan');
    setDraft({
      ...initialDraft,
      name: `检查 ${url.hostname}`,
      targetUrl: url.href,
      flow: '',
      expectation: '',
      preconditions: '用户已授权检查当前网站；需要登录时由用户本人完成；系统绝不代替用户付款。'
    });
    setDomModelAuthorized(true);
    setScreenshotModelAuthorized(false);
    setExecutionStrategy('agent');
    setPlannerMode('ai');
    setPage('compose');
    await new Promise<void>((resolve) => window.setTimeout(resolve, 0));

    let savedSession: SessionMetadata | null = null;
    try { savedSession = await api.getSession(project.id); setSession(savedSession); } catch { setSession(null); }
    if (websiteRequiresLogin(report) && !savedSession) {
      const recording = await api.startSessionRecording(project.id, Math.min(project.limits.timeoutSeconds, 1800));
      setRecordingId(recording.id);
      setMessage(`检测到这个网站需要登录，已使用${recording.browserName || '独立测试浏览器'}打开登录窗口。请正常登录，完成后回到这里点击“我已登录”。`);
    } else {
      if (aiConnection === 'connected') {
        try {
          const analysis = await api.analyzeWebsiteScope(project.id, aiSettings);
          setWebsiteScope(analysis.items);
          setScopeSource('ai');
          setMessage(`${resolved.changed ? '已自动跟随跳转并移除通用广告跟踪参数。' : ''}AI 已根据当前网站的真实扫描结果重新分析，可以检查 ${analysis.items.length} 类内容。`);
        } catch (error) {
          setMessage(`网站已完成真实扫描；AI 深度分析暂时失败，当前先显示扫描确认的 ${scanScope.length} 类内容：${error instanceof Error ? error.message : String(error)}`);
        }
      } else {
        setMessage(`${resolved.changed ? '已自动跟随跳转并移除通用广告跟踪参数。' : ''}已根据当前网站的真实扫描识别 ${scanScope.length} 类内容；连接 AI 后会进一步分析。`);
      }
    }
  });

  const confirmFullCheckScope = () => {
    const commerceSafety = selectedProject?.commerceProfile.enabled
      ? '。可以检查到创建未支付订单之前；如果具备正式站授权和固定测试数据，可以创建未支付订单，但绝不实际付款'
      : '';
    setDraft({ ...draft, flow: `全面检查这个网站：${websiteScope.join('；')}${commerceSafety}。`, expectation: `完成已确认范围的检查，用普通中文总结结果${selectedProject?.commerceProfile.enabled ? '；付款保持未完成' : ''}。` });
    setFullCheckRequested(false);
    window.setTimeout(() => document.getElementById('test-goal')?.focus(), 0);
    setMessage('检查范围已放入聊天框。你可以继续修改，确认后点击“开始测试”。');
  };

  const launchBeginnerRun = () => execute(async () => {
    if (!draft.flow.trim()) throw new Error('请在聊天框里告诉 AI 你想测试什么。');
    if (aiConnection !== 'connected') throw new Error('内置AI算力有限，请在高级设置中更换AI服务。');
    if (goalRequiresLogin(draft.flow) && !session && selectedProject) {
      const recording = await api.startSessionRecording(selectedProject.id, Math.min(selectedProject.limits.timeoutSeconds, 1800));
      setRecordingId(recording.id);
      setLoginCheckFailed(false);
      setMessage(`你要求测试的内容需要登录，已使用${recording.browserName || '独立测试浏览器'}打开登录窗口。请正常登录，完成后回到这里点击“我已登录”。`);
      return;
    }
    const nextRun = await api.startAgentRun({
      ...draft,
      name: draft.name.trim() || draft.flow.trim().slice(0, 80),
      expectation: draft.expectation || '完成用户提出的检查，并说明是否符合预期。'
    }, aiSettings, {
      siteHost: resolveTargetHost(draft.targetUrl, selectedProject?.baseUrl),
      allowDom: true,
      allowScreenshots: screenshotModelAuthorized
    }, selectedProject?.id, undefined, selectedEnvironment?.id, visualFallbackEnabled, approvalMode);
    setRun(nextRun);
    setHistory(await api.getHistory());
    setPage('run');
    setMessage('AI 已开始操作网站；左侧画面会随每一步更新。');
  });

  const saveProject = () => execute(async () => {
    const updating = Boolean(selectedProject);
    const stateModels = parseStateModels(projectStateModels);
    const payload = {
      ...projectDraft,
      businessContext: {
        ...projectDraft.businessContext,
        terminology: parseStringMap(projectTerminology, '业务术语'),
        stateModels,
        bridgeSemanticTargets: parseStringMap(projectBridgeTargets, 'Bridge 语义目标')
      }
    };
    const project = selectedProject
      ? await api.updateProject(selectedProject.id, payload)
      : await api.createProject(payload);
    setProjects(await api.getProjects());
    setSelectedProject(project);
    if (!updating) {
      setEnvironments([]);
      setSelectedEnvironment(null);
      setEnvironmentDraft(initialEnvironment);
      setEnvironmentVariables('{}');
      setEnvironmentSecretRefs('{}');
      setScenarios([]);
      setSelectedScenario(null);
      setScenarioDraft(initialScenario);
      setScenarioTestData('{}');
      setCompatibility(null);
      setSession(null);
    }
    setMessage(`项目“${project.name}”已${updating ? '更新' : '保存'}，可启动只读兼容性扫描。`);
  });

  const scanSelectedProject = () => execute(async () => {
    if (!selectedProject) return;
    const report = await api.scanProject(selectedProject.id);
    setCompatibility(report);
    setScenarios(await api.getScenarios(selectedProject.id));
    setMessage(`真实只读扫描完成：建议 ${report.recommendedOnboardingLevel}${report.sampleScenarioCreated ? '，已自动创建可编辑示例场景' : ''}。扫描未点击、填写或提交页面。`);
  });

  const chooseProject = (project: ProjectConfig) => execute(async () => {
    setSelectedProject(project);
    setProjectDraft({
      name: project.name, baseUrl: project.baseUrl, allowedHosts: project.allowedHosts,
      allowPrivateNetwork: project.allowPrivateNetwork,
      forbiddenActions: project.forbiddenActions, onboardingLevel: project.onboardingLevel,
      limits: project.limits, businessContext: project.businessContext, commerceProfile: project.commerceProfile
    });
    setProjectTerminology(JSON.stringify(project.businessContext.terminology, null, 2));
    setProjectStateModels(JSON.stringify(project.businessContext.stateModels, null, 2));
    setProjectBridgeTargets(JSON.stringify(project.businessContext.bridgeSemanticTargets, null, 2));
    setCompatibility(null);
    setSession(null);
    setSelectedScenario(null);
    setScenarioDraft(initialScenario);
    setScenarioTestData('{}');
    const [savedEnvironments, savedScenarios, savedFileAssets] = await Promise.all([api.getEnvironments(project.id), api.getScenarios(project.id), api.getFileAssets(project.id)]);
    setEnvironments(savedEnvironments);
    setFileAssets(savedFileAssets);
    setScenarios(savedScenarios);
    setSelectedEnvironment(null);
    setEnvironmentDraft(initialEnvironment);
    setEnvironmentVariables('{}');
    setEnvironmentSecretRefs('{}');
    try { setCompatibility(await api.getCompatibility(project.id)); } catch { /* 尚未扫描 */ }
    try { setSession(await api.getSession(project.id)); } catch { /* 尚未导入登录态 */ }
    setPage('projects');
  });

  const uploadFileAsset = (file?: File) => execute(async () => {
    if (!selectedProject || !file) return;
    const saved = await api.uploadFileAsset(selectedProject.id, file);
    setFileAssets(await api.getFileAssets(selectedProject.id));
    setMessage(`固定测试文件已登记：${saved.filename}，计划只能使用 ${saved.ref} 引用。`);
  });

  const loadEnvironment = (environment: EnvironmentConfig) => {
    setSelectedEnvironment(environment);
    setEnvironmentDraft({
      name: environment.name,
      variables: environment.variables,
      secretRefs: environment.secretRefs,
      ignoreRules: environment.ignoreRules,
      screenshotMaskSelectors: environment.screenshotMaskSelectors || [],
      viewport: environment.viewport,
      deviceScaleFactor: environment.deviceScaleFactor,
      appBridge: environment.appBridge,
      artifactRetentionDays: environment.artifactRetentionDays
    });
    setEnvironmentVariables(JSON.stringify(environment.variables, null, 2));
    setEnvironmentSecretRefs(JSON.stringify(environment.secretRefs, null, 2));
    setPlan(null);
    setReviewed(false);
  };

  const resetEnvironment = () => {
    setSelectedEnvironment(null);
    setEnvironmentDraft(initialEnvironment);
    setEnvironmentVariables('{}');
    setEnvironmentSecretRefs('{}');
    setPlan(null);
    setReviewed(false);
  };

  const parseStringMap = (source: string, label: string) => {
    try {
      const parsed = JSON.parse(source || '{}') as unknown;
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error();
      const entries = Object.entries(parsed as Record<string, unknown>);
      if (entries.some(([, value]) => typeof value !== 'string')) throw new Error();
      return Object.fromEntries(entries) as Record<string, string>;
    } catch {
      throw new Error(`${label}必须是值均为字符串的 JSON 对象。`);
    }
  };

  const openAcceptance = () => execute(async () => {
    const batches = await api.getAcceptanceBatches();
    setAcceptanceBatches(batches);
    setAcceptanceBatch((current) => batches.find((item) => item.id === current?.id) || batches[0] || null);
    setPage('acceptance');
  });

  const openSpatialAcceptance = () => execute(async () => {
    const suite = await api.getCesiumAcceptance();
    setCesiumSuite(suite);
    setPage('spatial-acceptance');
    setMessage(`三维与复杂网站专项基线已载入：共 ${suite.summary.total} 项，真实完整通过 ${suite.summary.passed} 项。`);
  });

  const openSimulationAcceptance = () => execute(async () => {
    const [catalog, workflow, batches] = await Promise.all([
      api.getGAEAcceptanceCatalog(), api.getGAEL4Workflow(), api.getGAEAcceptanceBatches()
    ]);
    setGAECatalog(catalog);
    setGAEWorkflow(workflow);
    setGAEBatches(batches);
    setGAEBatch((current) => batches.find((item) => item.batchId === current?.batchId) || batches[0] || null);
    setPage('simulation-acceptance');
    setMessage(`仿真业务验收底账已载入：共 ${catalog.scenarioCount} 项，每项重复 ${catalog.repeatCount} 次；当前 ${catalog.blockedCount} 项仍缺少真实验证条件。`);
  });

  const startGAEAcceptance = (dryRun: boolean) => execute(async () => {
    if (!dryRun && (!selectedProject || !selectedEnvironment)) throw new Error('真实全面验收需要先在“网站与登录配置”中选择网站和运行环境。');
    const scenarioBindings = dryRun ? {} : JSON.parse(gaeScenarioBindings || '{}') as Record<string, unknown>;
    const batch = await api.startGAEAcceptanceBatch({
      dryRun, projectId: selectedProject?.id, environmentId: selectedEnvironment?.id, scenarioBindings
    });
    setGAEBatch(batch);
    setGAEBatches((items) => [batch, ...items.filter((item) => item.batchId !== batch.batchId)]);
    setMessage(dryRun ? '已启动 30 项 × 5 次的合同检查；它不会访问目标网站，也不算真实通过。' : '已启动仿真业务真实全面验收，网站操作窗口将保持可见。');
  });

  const controlGAEAcceptance = (action: 'cancel' | 'resume' | 'retry-failed') => execute(async () => {
    if (!gaeBatch) return;
    const batch = await api.controlGAEAcceptanceBatch(gaeBatch.batchId, action);
    setGAEBatch(batch);
    setGAEBatches((items) => items.map((item) => item.batchId === batch.batchId ? batch : item));
  });

  const startGAEL4 = (dryRun: boolean) => execute(async () => {
    if (!dryRun && (!selectedProject || !selectedEnvironment)) throw new Error('真实完整流程需要先在“网站与登录配置”中选择网站和运行环境。');
    const stageBindings = dryRun ? {} : JSON.parse(gaeL4Bindings || '{}') as Record<string, unknown>;
    const result = await api.startGAEL4Run({ dryRun, projectId: selectedProject?.id, environmentId: selectedEnvironment?.id, stageBindings });
    setGAEL4Result(result);
    setMessage(dryRun ? '完整流程合同检查已完成；未访问目标网站，结果仍是未验证。' : `完整流程执行结束：${result.status}`);
  });

  const visibleCesiumCases = (cesiumSuite?.cases || []).filter((item) =>
    (cesiumPriority === 'all' || item.priority === cesiumPriority)
    && (cesiumStatus === 'all' || item.execution.status === cesiumStatus)
  );

  const startAcceptance = () => execute(async () => {
    const batch = await api.startAcceptanceBatch();
    setAcceptanceBatch(batch);
    setAcceptanceBatches((items) => [batch, ...items.filter((item) => item.id !== batch.id)]);
    setMessage(`验收批次已建立：${batch.plannedAttempts} 次计划尝试，当前结论 ${batch.verificationStatus}。`);
  });

  const controlAcceptance = (action: 'cancel' | 'resume' | 'retry-failed') => execute(async () => {
    if (!acceptanceBatch) return;
    const batch = await api.controlAcceptanceBatch(acceptanceBatch.id, action);
    setAcceptanceBatch(batch);
    setAcceptanceBatches((items) => items.map((item) => item.id === batch.id ? batch : item));
    setMessage(`验收批次操作完成：${action}。`);
  });

  const parseStateModels = (source: string) => {
    try {
      const parsed = JSON.parse(source || '{}') as unknown;
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error();
      const entries = Object.entries(parsed as Record<string, unknown>);
      if (entries.some(([, value]) => !Array.isArray(value) || value.some((item) => typeof item !== 'string'))) throw new Error();
      return Object.fromEntries(entries) as Record<string, string[]>;
    } catch {
      throw new Error('业务状态模型必须是值均为字符串数组的 JSON 对象。');
    }
  };

  const saveEnvironment = () => execute(async () => {
    if (!selectedProject) throw new Error('请先选择一个已接入项目。');
    if (!environmentDraft.name.trim()) throw new Error('请填写测试环境名称。');
    const payload = {
      ...environmentDraft,
      variables: parseStringMap(environmentVariables, '普通环境变量'),
      secretRefs: parseStringMap(environmentSecretRefs, '密钥引用')
    };
    const saved = selectedEnvironment
      ? await api.updateEnvironment(selectedProject.id, selectedEnvironment.id, payload)
      : await api.createEnvironment(selectedProject.id, payload);
    setEnvironments(await api.getEnvironments(selectedProject.id));
    loadEnvironment(saved);
    setMessage(`测试环境“${saved.name}”已${selectedEnvironment ? '更新' : '保存'}并选为当前运行环境。`);
  });

  const loadScenario = (scenario: ScenarioConfig) => {
    setSelectedScenario(scenario);
    setScenarioDraft({
      name: scenario.name,
      preconditions: scenario.preconditions,
      goal: scenario.goal,
      testData: scenario.testData,
      expectedResults: scenario.expectedResults,
      forbiddenActions: scenario.forbiddenActions,
      commerceSteps: scenario.commerceSteps || [],
      executionSteps: scenario.executionSteps || []
    });
    setScenarioTestData(JSON.stringify(scenario.testData, null, 2));
    setDraft({
      ...draft,
      name: scenario.name,
      targetUrl: selectedProject?.baseUrl || draft.targetUrl,
      preconditions: scenario.preconditions.join('；'),
      flow: scenario.goal,
      expectation: scenario.expectedResults.join('；'),
      testData: scenario.testData,
      forbiddenActions: scenario.forbiddenActions
    });
    setPlan(null);
    setReviewed(false);
    setWarnings([]);
  };

  const resetScenario = () => {
    setSelectedScenario(null);
    setScenarioDraft(initialScenario);
    setScenarioTestData('{}');
  };

  const updateScenarioBackedDraft = (next: TestCaseDraft) => {
    setDraft(next);
    setPlan(null);
    setReviewed(false);
  };

  const saveScenario = () => execute(async () => {
    if (!selectedProject) throw new Error('请先选择一个已接入项目。');
    if (!scenarioDraft.name.trim() || !scenarioDraft.goal.trim() || !scenarioDraft.preconditions.length || !scenarioDraft.expectedResults.length) {
      throw new Error('请补齐场景名称、前置条件、测试目标和至少一项预期结果。');
    }
    let testData: ScenarioDraft['testData'];
    try {
      const parsed = JSON.parse(scenarioTestData || '{}') as unknown;
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error();
      testData = parsed as ScenarioDraft['testData'];
    } catch {
      throw new Error('测试数据必须是一个有效的 JSON 对象。');
    }
    const commerceSteps = plan
      ? plan.steps.flatMap((step, index) => step.commerce ? [{ stepIndex: index + 1, commerce: step.commerce }] : [])
      : scenarioDraft.commerceSteps;
    const executionSteps = plan
      ? plan.steps.flatMap((step, index) => step.browserTarget || step.action === 'human_takeover' ? [{
          stepIndex: index + 1,
          browserTarget: step.browserTarget || { page: 'current' as const, waitTimeoutMs: 10000 },
          action: step.action === 'human_takeover' ? 'human_takeover' as const : undefined,
          takeoverReason: step.takeoverReason,
          takeoverResumeLocator: step.takeoverResumeLocator
        }] : [])
      : scenarioDraft.executionSteps;
    const payload = { ...scenarioDraft, testData, commerceSteps, executionSteps };
    const saved = selectedScenario
      ? await api.updateScenario(selectedProject.id, selectedScenario.id, payload)
      : await api.createScenario(selectedProject.id, payload);
    setScenarios(await api.getScenarios(selectedProject.id));
    loadScenario(saved);
    setMessage(`场景“${saved.name}”已${selectedScenario ? '更新' : '保存'}并载入测试。`);
  });

  const loadSessionFile = async (file?: File) => {
    setSessionUpload(null);
    setSessionFileName('');
    if (!file) return;
    try {
      const parsed = JSON.parse(await readTextFile(file)) as Record<string, unknown>;
      if (!Array.isArray(parsed.cookies) || !Array.isArray(parsed.origins)) throw new Error('文件不是有效的 Playwright storageState');
      setSessionUpload(parsed);
      setSessionFileName(file.name);
      setMessage('登录态文件已在当前页面内存中读取，点击“加密导入”后才会发送到本机服务。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '无法读取登录态文件');
    }
  };

  const importProjectSession = () => execute(async () => {
    if (!selectedProject || !sessionUpload) return;
    const metadata = await api.importSession(selectedProject.id, sessionUpload);
    setSession(metadata);
    setSessionUpload(null);
    setSessionFileName('');
    setMessage(`L1 登录态已使用 ${metadata.encryption} 加密保存；Cookie 内容不会返回前端或写入报告。`);
  });

  const startLoginRecording = () => execute(async () => {
    if (!selectedProject) return;
    const recording = await api.startSessionRecording(selectedProject.id, Math.min(selectedProject.limits.timeoutSeconds, 1800));
    setRecordingId(recording.id);
    setLoginCheckFailed(false);
    setMessage(`${recording.browserName || '独立测试浏览器'}登录窗口已打开。完成登录后返回此页并点击“完成录制”。`);
  });

  const completeLoginRecording = () => execute(async () => {
    if (!selectedProject || !recordingId) return;
    const recording = await api.completeSessionRecording(selectedProject.id, recordingId);
    if (recording.session) setSession(recording.session);
    setRecordingId(null);
    const report = await api.scanProject(selectedProject.id);
    setCompatibility(report);
    const scanScope = detectedModules(report);
    setWebsiteScope(scanScope);
    setScopeSource('scan');
    const verified = Boolean(recording.session) && !websiteRequiresLogin(report);
    setLoginCheckFailed(!verified);
    if (shouldAnalyzeScopeAfterLogin(verified, aiConnection)) {
      try {
        const analysis = await api.analyzeWebsiteScope(selectedProject.id, aiSettings);
        setWebsiteScope(analysis.items);
        setScopeSource('ai');
        setMessage(`登录成功。AI 已重新查看登录后的页面，可以检查 ${analysis.items.length} 类内容。现在请告诉 AI 你想测试什么。`);
      } catch (error) {
        setMessage(`登录成功；登录后页面已重新扫描，但 AI 分析暂时失败，先显示系统确认的 ${scanScope.length} 类内容：${error instanceof Error ? error.message : String(error)}`);
      }
    } else {
      setMessage(verified
        ? '登录成功，AI 已确认可以继续。现在请告诉 AI 你想测试什么。'
        : '系统仍然看到登录入口，暂时不能判定登录成功。请重新登录，或确认账号是否被网站拦截。');
    }
  });

  const cancelLoginRecording = () => execute(async () => {
    if (!selectedProject || !recordingId) return;
    await api.cancelSessionRecording(selectedProject.id, recordingId);
    setRecordingId(null);
    setMessage('登录录制已取消，未保存会话。');
  });

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">京彩OPC<br /><span>AI 网站测试助手 · 1.32.00</span></div>
        <nav aria-label="主要导航">
          <button className={page === 'start' || page === 'compose' || page === 'run' ? 'active' : ''} onClick={() => setPage('start')}><Globe2 size={18} />开始测试</button>
          <button className={page === 'history' || page === 'report' ? 'active' : ''} onClick={openHistory}><History size={18} />测试记录</button>
          <button className={page === 'settings' || ['projects', 'ai', 'new', 'overview', 'acceptance', 'spatial-acceptance', 'simulation-acceptance'].includes(page) ? 'active' : ''} onClick={() => setPage('settings')}><Settings2 size={18} />高级设置</button>
        </nav>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <p className="eyebrow">AI 网站测试助手</p>
            <h1>{pageTitle(page)}</h1>
          </div>
          <div className={`service-state service-${connection}`}>
            {connection === 'connected' ? <ShieldCheck size={18} /> : <ShieldAlert size={18} />}
            <span>{connection === 'checking' ? '正在检查执行服务' : connection === 'connected' ? '真实执行服务已连接' : '执行服务未连接'}</span>
          </div>
        </header>

        <div className={`notice notice-${connection}`} role={connection === 'disconnected' ? 'alert' : 'status'}>{message}</div>

        {page === 'start' && (
          <section className="content beginner-start">
            <div className="start-hero">
              <div className="start-copy">
                <p className="eyebrow">不需要懂项目、环境或测试脚本</p>
                <h2>输入网址，让 AI 帮你测试</h2>
                <p>AI 会先安全查看网站，再用普通中文告诉你它能检查什么。扫描阶段不会点击、填写或提交内容。</p>
              </div>
              <div className="url-entry-card">
                <label htmlFor="website-url">要测试的网站地址</label>
                <div className="url-entry-row">
                  <Globe2 size={21} />
                  <input id="website-url" value={websiteInput} onChange={(event) => setWebsiteInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') beginWebsite(); }} placeholder="例如：https://www.example.com" autoFocus />
                  <button className="primary" onClick={beginWebsite} disabled={busy || connection !== 'connected'}>开始 <ChevronRight size={18} /></button>
                </div>
                <p className="safety-line"><ShieldCheck size={17} />仅测试你有权使用的网站；密码、验证码与付款始终由你本人完成。</p>
              </div>
            </div>
            {history.length > 0 && <div className="panel recent-simple"><h3>最近测试</h3><div className="recent-site-list">{history.slice(0, 3).map((item) => <button key={item.id} onClick={() => { setRun(item); setPage('run'); }}><span>{item.caseName}</span><small>{new Date(item.startedAt).toLocaleString('zh-CN', { hour12: false })}</small><ChevronRight size={17} /></button>)}</div></div>}
          </section>
        )}

        {page === 'compose' && (
          <section className="content compose-layout">
            <div className="site-preview panel">
              <div className="panel-title"><div><p className="eyebrow">已识别网站</p><h2>{compatibility?.title || selectedProject?.name || '当前网站'}</h2></div><Monitor size={22} /></div>
              <div className="recognized-url">{draft.targetUrl}</div>
              {recordingId || loginCheckFailed ? <div className="login-guide">
                <LogIn size={30} />
                <h3>{loginCheckFailed ? '还没有确认登录成功' : '请先在弹出的窗口中登录'}</h3>
                <p>{loginCheckFailed ? '网站仍显示登录入口。你可以重新登录，系统会再次判断。' : '像平常一样登录。系统不会读取或保存你的密码；登录完成后回到这里。'}</p>
                {recordingId ? <><button className="primary" onClick={completeLoginRecording} disabled={busy}>我已登录</button><button onClick={cancelLoginRecording} disabled={busy}>取消登录</button></> : <button className="primary" onClick={startLoginRecording} disabled={busy}>重新打开登录窗口</button>}
              </div> : <>
                <div className="capability-summary"><h3>{scopeSource === 'ai' ? 'AI 根据这个网站的扫描结果发现这些内容' : '系统从这个网站实际识别到这些内容'}</h3><ul>{websiteScope.map((item) => <li key={item}><ShieldCheck size={17} />{item}</li>)}</ul></div>
                <div className="toolbar login-choice"><button onClick={startLoginRecording} disabled={busy}><LogIn size={17} />{session ? '重新登录或更换账号' : '登录账号后再测试'}</button><small>{session ? (session.expiryStatus === 'expired' || (compatibility && websiteRequiresLogin(compatibility)) ? '保存的登录状态可能已经失效，请重新登录后再测试账号功能。' : '当前已保存这个网站的登录状态；需要换账号或登录失效时再点这里。') : '当前没有保存登录状态；只有测试账号功能时才需要登录。'}</small></div>
                <p className="preview-note">真正开始后，这里会显示 AI 正在操作的网站画面。</p>
              </>}
            </div>

            <div className="ai-compose panel">
              <div className="assistant-message"><Bot size={20} /><div><strong>你想怎么测试？</strong><p>你可以直接说一件事，也可以让我按刚才识别到的内容全面检查。</p></div></div>
              {!fullCheckRequested ? <div className="test-choice-list">
                <button className="choice-card" onClick={() => document.getElementById('test-goal')?.focus()}><span><strong>告诉 AI 你想测试什么</strong><small>例如：搜索一个商品，加入购物车，但不要付款</small></span><ChevronRight size={19} /></button>
                <button className="choice-card" onClick={() => setFullCheckRequested(true)}><span><strong>让 AI 全面检查这个网站</strong><small>先确认检查范围，再开始运行</small></span><ChevronRight size={19} /></button>
              </div> : <div className="full-check-confirm">
                <strong>AI 将检查以下内容</strong>
                <ul>{websiteScope.map((item) => <li key={item}>{item}</li>)}</ul>
                <p>{selectedProject?.commerceProfile.enabled
                  ? '涉及写入会按你的批准方式处理；可以检查到创建未支付订单之前，付款步骤一定停止并请你接管。本项目验收不会实际付款。'
                  : '涉及写入会按你的批准方式处理；高风险或不可逆操作会停止并请你确认。'}</p>
                <button className="primary" onClick={confirmFullCheckScope}>确认这个范围</button>
                <button onClick={() => setFullCheckRequested(false)}>返回</button>
              </div>}
              <div className="chat-composer">
                <textarea id="test-goal" value={draft.flow} onChange={(event) => setDraft({ ...draft, flow: event.target.value })} placeholder="告诉 AI 你想测试什么……" disabled={Boolean(recordingId)} />
                {aiConnection !== 'connected' && <p className="ai-limited-note">内置AI算力有限，请在高级设置中<button className="text-link" onClick={goToAISettings}>更换AI服务</button>。</p>}
                <div className="composer-actions">
                  <label>AI 操作前
                    <select value={approvalMode} onChange={(event) => {
                      const mode = event.target.value as ApprovalMode;
                      if (mode === 'full' && !window.confirm('完全访问权限会自动批准所有可执行动作。付款、密码、账号安全修改和删除非测试数据仍会被系统禁止。确定启用吗？')) return;
                      setApprovalMode(mode);
                    }}>
                      <option value="ask">请求批准 · 每次写入都问我</option>
                      <option value="delegate">替我审批 · 低风险自动处理</option>
                      <option value="full">完全访问权限 · 自动批准</option>
                    </select>
                  </label>
                  <button className="primary" onClick={launchBeginnerRun} disabled={busy || Boolean(recordingId) || !draft.flow.trim()}>开始测试 <Play size={17} /></button>
                </div>
                {approvalMode === 'full' && <div className="full-access-warning" role="alert"><ShieldAlert size={18} /><span>完全访问权限已开启：系统会自动批准允许范围内的动作。付款及绝对禁止动作仍不会执行。</span></div>}
              </div>
            </div>
          </section>
        )}

        {page === 'settings' && (
          <section className="content settings-hub">
            <div className="panel settings-intro"><p className="eyebrow">普通测试通常不需要修改这里</p><h2>高级设置</h2><p>这里完整保留 AI 服务、网站登录、专业场景和专项验收等配置。普通测试可以直接返回开始页面。</p></div>
            <div className="settings-grid">
              <button onClick={goToAISettings}><KeyRound size={22} /><span><strong>更换 AI 服务</strong><small>填写自己的 AI 服务地址、模型和密钥</small></span><ChevronRight size={18} /></button>
              <button onClick={() => setPage('projects')}><ScanSearch size={22} /><span><strong>网站与登录配置</strong><small>查看系统内部保存的网站、环境和登录状态</small></span><ChevronRight size={18} /></button>
              <button onClick={() => setPage('new')}><SquarePen size={22} /><span><strong>传统测试编辑器</strong><small>为专业人员保留固定计划与场景编辑</small></span><ChevronRight size={18} /></button>
              <button onClick={openAcceptance}><ClipboardCheck size={22} /><span><strong>完整电商验收</strong><small>内部保留全部 65 项检查，普通结果按业务模块汇总</small></span><ChevronRight size={18} /></button>
              <button onClick={openSpatialAcceptance}><Globe2 size={22} /><span><strong>三维与复杂网站验收</strong><small>检查文件、三维画面、异步任务、账号权限与清理闭环</small></span><ChevronRight size={18} /></button>
              <button onClick={openSimulationAcceptance}><Activity size={22} /><span><strong>仿真业务完整验收</strong><small>检查建模、环境数据、想定、运行、下载和清理的完整业务流程</small></span><ChevronRight size={18} /></button>
              <button onClick={() => setPage('overview')}><Activity size={22} /><span><strong>运行总览</strong><small>查看技术统计和执行服务状态</small></span><ChevronRight size={18} /></button>
            </div>
          </section>
        )}

        {page === 'overview' && (
          <section className="content">
            <div className="metric-grid">
              <Metric label="真实运行记录" value={metrics.totalCases} />
              <Metric label="最近运行次数" value={metrics.recentRuns} />
              <Metric label="测试通过率" value={`${metrics.passRate}%`} />
              <Metric label="失败 / 错误" value={metrics.failed} tone="danger" />
            </div>
            <div className="panel split">
              <div className="min-width-zero">
                <h2>最近真实运行</h2>
                <RunTable runs={history} onReport={openReport} />
              </div>
              <div className="quick-create">
                <h2>创建真实测试</h2>
                <p>生成受约束计划，经人工审核后由 Playwright 打开目标网站执行；未连接服务时不会产生结果。</p>
                <button className="primary" onClick={() => setPage('new')}><SquarePen size={18} />新建测试</button>
              </div>
            </div>
          </section>
        )}

        {page === 'projects' && (
          <section className="content onboarding-layout">
            <div className="panel onboarding-form">
              <div className="panel-title"><div><p className="eyebrow">企业项目配置 / L0-L3</p><h2>{selectedProject ? '编辑项目配置' : '新项目接入'}</h2></div><ScanSearch size={22} /></div>
              <p className="security-note"><ShieldCheck size={18} />兼容性扫描只打开页面并读取结构、控制台和网络状态，不点击、不填写、不提交，也不采集密码与 Cookie。</p>
              <div className="form-grid project-form-grid">
                <label>项目名称<input value={projectDraft.name} onChange={(event) => setProjectDraft({ ...projectDraft, name: event.target.value })} placeholder="例如：客户管理后台" /></label>
                <label>Base URL<input value={projectDraft.baseUrl} onChange={(event) => setProjectDraft({ ...projectDraft, baseUrl: event.target.value })} placeholder="https://test.example.com" spellCheck={false} /></label>
                <label>接入级别<select value={projectDraft.onboardingLevel} onChange={(event) => setProjectDraft({ ...projectDraft, onboardingLevel: event.target.value as ProjectDraft['onboardingLevel'] })}><option value="L0">L0 · 黑盒探索</option><option value="L1">L1 · 登录态与环境配置</option><option value="L2">L2 · 可测试 DOM 增强</option><option value="L3">L3 · Canvas App Bridge</option></select></label>
                <label className="wide">允许域名（每行或逗号分隔）<textarea value={projectDraft.allowedHosts.join('\n')} onChange={(event) => setProjectDraft({ ...projectDraft, allowedHosts: splitEntries(event.target.value) })} placeholder="test.example.com" /></label>
                <label className="checkline wide"><input type="checkbox" checked={projectDraft.allowPrivateNetwork} onChange={(event) => setProjectDraft({ ...projectDraft, allowPrivateNetwork: event.target.checked })} />允许访问受控私网／本机目标</label>
                <label>最大步骤<input type="number" min="1" max="100" value={projectDraft.limits.maxSteps} onChange={(event) => setProjectDraft({ ...projectDraft, limits: { ...projectDraft.limits, maxSteps: Number(event.target.value) } })} /></label>
                <label>运行上限（秒）<input type="number" min="30" max="3600" value={projectDraft.limits.timeoutSeconds} onChange={(event) => setProjectDraft({ ...projectDraft, limits: { ...projectDraft.limits, timeoutSeconds: Number(event.target.value) } })} /></label>
                <label>模型调用上限<input type="number" min="0" max="100" value={projectDraft.limits.maxModelCalls} onChange={(event) => setProjectDraft({ ...projectDraft, limits: { ...projectDraft.limits, maxModelCalls: Number(event.target.value) } })} /></label>
                <label className="wide">禁止动作（每行一项）<textarea value={projectDraft.forbiddenActions.join('\n')} onChange={(event) => setProjectDraft({ ...projectDraft, forbiddenActions: splitEntries(event.target.value) })} /></label>
              </div>
              <details className="configuration-details">
                <summary>项目业务上下文包</summary>
                <div className="form-grid project-context-grid">
                  <label className="wide">业务范围说明<textarea value={projectDraft.businessContext.description} onChange={(event) => setProjectDraft({ ...projectDraft, businessContext: { ...projectDraft.businessContext, description: event.target.value } })} placeholder="说明系统处理的业务、主要用户和测试边界" /></label>
                  <label className="wide">业务术语（JSON）<textarea value={projectTerminology} onChange={(event) => setProjectTerminology(event.target.value)} spellCheck={false} placeholder={'{"挂载点":"仿真对象可连接的接口"}'} /></label>
                  <label>业务对象（每行一项）<textarea value={projectDraft.businessContext.objectTypes.join('\n')} onChange={(event) => setProjectDraft({ ...projectDraft, businessContext: { ...projectDraft.businessContext, objectTypes: splitEntries(event.target.value) } })} /></label>
                  <label>状态模型（JSON）<textarea value={projectStateModels} onChange={(event) => setProjectStateModels(event.target.value)} spellCheck={false} placeholder={'{"任务":["草稿","运行中","已完成"]}'} /></label>
                  <label>示例目标（每行一项）<textarea value={projectDraft.businessContext.exampleGoals.join('\n')} onChange={(event) => setProjectDraft({ ...projectDraft, businessContext: { ...projectDraft.businessContext, exampleGoals: splitEntries(event.target.value) } })} /></label>
                  <label>操作边界（每行一项）<textarea value={projectDraft.businessContext.operatingBoundaries.join('\n')} onChange={(event) => setProjectDraft({ ...projectDraft, businessContext: { ...projectDraft.businessContext, operatingBoundaries: splitEntries(event.target.value) } })} placeholder="例如：只允许操作测试租户中的数据" /></label>
                  <label>允许操作（每行一项）<textarea value={projectDraft.businessContext.allowedActions.join('\n')} onChange={(event) => setProjectDraft({ ...projectDraft, businessContext: { ...projectDraft.businessContext, allowedActions: splitEntries(event.target.value) } })} placeholder="例如：查询客户、启动仿真" /></label>
                  <label>Bridge 能力（每行一项）<textarea value={projectDraft.businessContext.bridgeCapabilities.join('\n')} onChange={(event) => setProjectDraft({ ...projectDraft, businessContext: { ...projectDraft.businessContext, bridgeCapabilities: splitEntries(event.target.value) } })} placeholder="例如：等待场景就绪、读取选中对象" /></label>
                  <label className="wide">Bridge 语义目标（JSON）<textarea value={projectBridgeTargets} onChange={(event) => setProjectBridgeTargets(event.target.value)} spellCheck={false} placeholder={'{"agent.primary":"主仿真 Agent"}'} /></label>
                </div>
              </details>
              <details className="configuration-details">
                <summary>电商交易安全配置</summary>
                <div className="form-grid project-context-grid">
                  <label className="checkline wide"><input type="checkbox" checked={projectDraft.commerceProfile.enabled} onChange={(event) => setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, enabled: event.target.checked } })} />启用电商动作前门禁与隐私遮罩</label>
                  <label>环境层级<select value={projectDraft.commerceProfile.environment} onChange={(event) => {
                    const environment = event.target.value as ProjectDraft['commerceProfile']['environment'];
                    setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, environment, sandboxDriver: environment === 'production_readonly' ? false : projectDraft.commerceProfile.sandboxDriver } });
                  }}><option value="production_readonly">正式站 · 只读／受控可逆</option><option value="isolated_transaction">隔离交易环境</option></select></label>
                  <label>专用账号密钥别名<input value={projectDraft.commerceProfile.accountRef || ''} onChange={(event) => setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, accountRef: event.target.value.trim().toUpperCase() || null } })} placeholder="JD_BUYER_ACCOUNT" spellCheck={false} /></label>
                  <label>E2E 资源前缀<input value={projectDraft.commerceProfile.e2eResourcePrefix} onChange={(event) => setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, e2eResourcePrefix: event.target.value } })} placeholder="E2E_" spellCheck={false} /></label>
                  <label className="checkline"><input type="checkbox" checked={projectDraft.commerceProfile.productionReversibleWriteAuthorized} onChange={(event) => setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, productionReversibleWriteAuthorized: event.target.checked } })} />正式站可逆写已书面授权</label>
                  <label className="checkline"><input type="checkbox" checked={projectDraft.commerceProfile.sandboxDriver} disabled={projectDraft.commerceProfile.environment === 'production_readonly'} onChange={(event) => setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, sandboxDriver: event.target.checked } })} />支付／退款沙箱驱动可用</label>
                  <label>固定测试商品引用<input value={projectDraft.commerceProfile.fixedProductRef || ''} onChange={(event) => setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, fixedProductRef: event.target.value.trim() || null } })} placeholder="public-sku:TEST_SKU" spellCheck={false} /></label>
                  <label>固定测试地址密钥别名<input value={projectDraft.commerceProfile.fixedAddressRef || ''} onChange={(event) => setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, fixedAddressRef: event.target.value.trim().toUpperCase() || null } })} placeholder="JD_TEST_ADDRESS" spellCheck={false} /></label>
                  <label>提交订单书面授权编号<input value={projectDraft.commerceProfile.writtenAuthorizationRef || ''} onChange={(event) => setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, writtenAuthorizationRef: event.target.value.trim() || null } })} placeholder="AUTH-2026-001" spellCheck={false} /></label>
                  <label className="checkline"><input type="checkbox" checked={projectDraft.commerceProfile.automaticCancellationVerified} onChange={(event) => setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, automaticCancellationVerified: event.target.checked } })} />自动取消与零残留已实测</label>
                  <label className="wide">PII 截图遮罩选择器（每行一项）<textarea value={projectDraft.commerceProfile.piiMaskSelectors.join('\n')} onChange={(event) => setProjectDraft({ ...projectDraft, commerceProfile: { ...projectDraft.commerceProfile, piiMaskSelectors: splitEntries(event.target.value) } })} placeholder={'[data-testid="address"]\n[data-testid="mobile"]'} spellCheck={false} /></label>
                </div>
                <p className="security-note"><ShieldAlert size={18} />真实支付始终禁止。正式站仅在专用账号、固定商品与地址、书面授权、自动取消清理全部具备时允许提交未支付订单，否则停在提交前。</p>
              </details>
              <div className="toolbar"><button className="primary" onClick={saveProject} disabled={busy || !projectDraft.name.trim() || !projectDraft.baseUrl.trim()}><ShieldCheck size={18} />{selectedProject ? '保存项目修改' : '保存项目配置'}</button>{selectedProject && <button onClick={() => {
                setSelectedProject(null); setProjectDraft(initialProject); setCompatibility(null); setSession(null);
                setProjectTerminology('{}'); setProjectStateModels('{}'); setProjectBridgeTargets('{}');
                setEnvironments([]); setSelectedEnvironment(null); setScenarios([]); setSelectedScenario(null);
              }}><SquarePen size={18} />新建项目</button>}</div>
              <p className="format-hint">L1 支持导入 storageState 或在受控浏览器中交互登录，两种方式均由 Windows 当前用户 DPAPI 加密保存。</p>
            </div>
            <div className="panel project-list-panel">
              <h2>已接入项目</h2>
              {projects.length ? <div className="project-list">{projects.map((project) => <button className={selectedProject?.id === project.id ? 'project-card active' : 'project-card'} key={project.id} onClick={() => chooseProject(project)}><strong>{project.name}</strong><span>{project.baseUrl}</span><small>{project.onboardingLevel} · {project.allowedHosts.length} 个允许域名 · {project.allowPrivateNetwork ? '受控私网已允许' : '仅公网'}{project.commerceProfile.enabled ? ` · ${project.commerceProfile.environment === 'production_readonly' ? '电商正式站只读' : '隔离交易'}` : ''}</small></button>)}</div> : <div className="empty compact">尚无项目配置。</div>}
              {selectedProject && <div className="scan-actions">
                <p><strong>当前项目：</strong>{selectedProject.name}</p>
                <details className="configuration-details">
                  <summary>固定测试文件 · {fileAssets.length} 项</summary>
                  <label className="session-upload">登记固定 E2E 文件（最大 20 MB）
                    <input type="file" onChange={(event) => uploadFileAsset(event.target.files?.[0])} />
                  </label>
                  {fileAssets.length ? <div className="session-meta">{fileAssets.map((asset) => <span key={asset.sha256} title={asset.ref}>{asset.filename} · {asset.bytes} B · {asset.sha256.slice(0, 12)}...</span>)}</div> : <p className="muted">尚未登记固定测试文件。</p>}
                </details>
                <div className="session-box">
                  <div className="session-heading"><strong>L1 登录态</strong>{session && <span className={`session-state session-${session.expiryStatus}`}>{sessionStatusText(session.expiryStatus)}</span>}</div>
                  {session ? <div className="session-meta"><span>{session.cookieCount} 个 Cookie</span><span>{session.originCount} 个 Origin</span><span>{session.encryption}</span><span>{session.domains.join('、') || '未记录域名'}</span></div> : <p className="muted">尚未导入。扫描和执行将使用公开页面状态。</p>}
                  <label className="session-upload">选择 Playwright storageState JSON
                    <input type="file" accept="application/json,.json" onChange={(event) => loadSessionFile(event.target.files?.[0])} />
                  </label>
                  {sessionFileName && <p className="selected-file">待导入：{sessionFileName}</p>}
                  <button onClick={importProjectSession} disabled={busy || !sessionUpload}><ShieldCheck size={18} />加密导入登录态</button>
                  <div className="recording-actions">
                    {!recordingId ? <button onClick={startLoginRecording} disabled={busy}><Play size={18} />交互登录录制</button> : <><button className="primary" onClick={completeLoginRecording} disabled={busy}><ClipboardCheck size={18} />完成录制</button><button onClick={cancelLoginRecording} disabled={busy}><StopCircle size={18} />取消</button></>}
                  </div>
                </div>
                <button className="primary" onClick={scanSelectedProject} disabled={busy}><ScanSearch size={18} />启动真实只读扫描</button>
                <button onClick={() => { setDraft({ ...draft, targetUrl: selectedProject.baseUrl }); setSelectedScenario(null); setPage('new'); }}><SquarePen size={18} />用该地址新建测试</button>
              </div>}
            </div>
            {compatibility && <CompatibilityView report={compatibility} />}
            {selectedProject && <div className="panel environment-workbench compatibility-panel">
              <div className="panel-title"><div><p className="eyebrow">FR-01 / 项目运行环境</p><h2>测试环境配置</h2></div><ShieldCheck size={22} /></div>
              <p className="security-note"><ShieldCheck size={18} />密钥引用只保存操作系统环境变量名称；密码、Token、Cookie 和密钥值不会写入项目 JSON。</p>
              <div className="environment-selector">
                <label>已保存环境<select value={selectedEnvironment?.id || ''} onChange={(event) => {
                  const environment = environments.find((item) => item.id === event.target.value);
                  if (environment) loadEnvironment(environment); else resetEnvironment();
                }}><option value="">新建环境</option>{environments.map((environment) => <option value={environment.id} key={environment.id}>{environment.name}</option>)}</select></label>
                <button onClick={resetEnvironment}><SquarePen size={17} />新建环境</button>
              </div>
              <div className="form-grid environment-form-grid">
                <label>环境名称<input value={environmentDraft.name} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, name: event.target.value })} placeholder="例如：预发布环境" /></label>
                <label>Viewport 宽度<input type="number" min="320" max="3840" value={environmentDraft.viewport.width} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, viewport: { ...environmentDraft.viewport, width: Number(event.target.value) } })} /></label>
                <label>Viewport 高度<input type="number" min="320" max="2160" value={environmentDraft.viewport.height} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, viewport: { ...environmentDraft.viewport, height: Number(event.target.value) } })} /></label>
                <label className="wide">普通环境变量（JSON）<textarea className="environment-json-editor" value={environmentVariables} onChange={(event) => setEnvironmentVariables(event.target.value)} spellCheck={false} /></label>
                <label className="wide">密钥引用（JSON）<textarea className="environment-json-editor" value={environmentSecretRefs} onChange={(event) => setEnvironmentSecretRefs(event.target.value)} spellCheck={false} /></label>
                <label className="wide">网络忽略规则（每行一项）<textarea value={environmentDraft.ignoreRules.join('\n')} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, ignoreRules: splitEntries(event.target.value) })} /></label>
                <label className="wide">截图隐私遮罩 CSS 选择器（每行一项）<textarea value={environmentDraft.screenshotMaskSelectors.join('\n')} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, screenshotMaskSelectors: splitEntries(event.target.value) })} placeholder="例如：.customer-name" /></label>
                <label>设备缩放<input type="number" min="0.5" max="3" step="0.25" value={environmentDraft.deviceScaleFactor} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, deviceScaleFactor: Number(event.target.value) })} /></label>
                <label>工件保留天数<input type="number" min="1" max="365" value={environmentDraft.artifactRetentionDays} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, artifactRetentionDays: Number(event.target.value) })} /></label>
                <label>页面语义适配器<select value={environmentDraft.appBridge.adapter} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, appBridge: { ...environmentDraft.appBridge, adapter: event.target.value as EnvironmentDraft['appBridge']['adapter'] } })}><option value="generic">普通网页</option><option value="cesium">通用三维网页</option><option value="gaealavic_cesium">仿真业务三维网页</option></select></label>
                <label className="checkline environment-bridge-toggle"><input type="checkbox" checked={environmentDraft.appBridge.enabled} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, appBridge: { ...environmentDraft.appBridge, enabled: event.target.checked } })} />启用 App Bridge</label>
                <label className="wide">Bridge 全局名称<input value={environmentDraft.appBridge.globalName} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, appBridge: { ...environmentDraft.appBridge, globalName: event.target.value } })} spellCheck={false} /></label>
                <div className="toolbar wide"><a className="evidence-link" href="/api/bridge/cesium-reference" download><Download size={17} />通用三维页面参考适配器</a><a className="evidence-link" href="/api/bridge/gaealavic-cesium-adapter" download><Download size={17} />仿真业务页面参考适配器</a></div>
              </div>
              <div className="toolbar"><button className="primary" onClick={saveEnvironment} disabled={busy}><Save size={18} />{selectedEnvironment ? '保存环境修改' : '保存新环境'}</button>{selectedEnvironment && <span className="saved-environment-state">当前运行环境：{selectedEnvironment.name}</span>}</div>
            </div>}
          </section>
        )}

        {page === 'ai' && (
          <section className="content">
            <div className="panel ai-settings-panel">
              <div className="panel-title">
                <div>
                  <p className="eyebrow">仅当前会话 / 不落盘</p>
                  <h2>更换 AI 服务</h2>
                </div>
                <span className={`ai-state ai-state-${aiConnection}`}>
                  {aiConnection === 'connected' ? '已连接' : aiConnection === 'testing' ? '测试中' : aiConnection === 'failed' ? '连接失败' : '未测试'}
                </span>
              </div>
              <p className="security-note"><ShieldCheck size={18} />Key 只发送到本机后端并用于当次模型请求，不写入文件、浏览器存储、运行历史或测试报告。刷新页面后自动清除。</p>
              <div className="form-grid ai-form">
                <label>API 协议
                  <select value={aiSettings.protocol} onChange={(event) => updateAISettings({ ...aiSettings, protocol: event.target.value as AISettings['protocol'] })}>
                    <option value="responses">OpenAI Responses API</option>
                    <option value="chat_completions">兼容 Chat Completions</option>
                  </select>
                </label>
                <label>模型名称
                  <input value={aiSettings.model} onChange={(event) => updateAISettings({ ...aiSettings, model: event.target.value })} placeholder="例如 gpt-5.6-terra" spellCheck={false} />
                </label>
                <label className="wide">API Base URL
                  <input value={aiSettings.baseUrl} onChange={(event) => updateAISettings({ ...aiSettings, baseUrl: event.target.value })} placeholder="https://api.openai.com/v1" spellCheck={false} />
                </label>
                <label className="wide">API Key
                  <div className="secret-field">
                    <input type={showKey ? 'text' : 'password'} value={aiSettings.apiKey} onChange={(event) => updateAISettings({ ...aiSettings, apiKey: event.target.value })} placeholder="请填写重新生成的 Key" autoComplete="new-password" spellCheck={false} />
                    <button type="button" className="icon-button" aria-label={showKey ? '隐藏 API Key' : '显示 API Key'} onClick={() => setShowKey((value) => !value)}>{showKey ? <EyeOff size={18} /> : <Eye size={18} />}</button>
                  </div>
                </label>
                <label>输入单价／百万 Token
                  <input type="number" min="0" step="0.01" value={aiSettings.inputCostPerMillion ?? ''} onChange={(event) => updateAISettings({ ...aiSettings, inputCostPerMillion: event.target.value === '' ? undefined : Number(event.target.value) })} placeholder="可选" />
                </label>
                <label>输出单价／百万 Token
                  <input type="number" min="0" step="0.01" value={aiSettings.outputCostPerMillion ?? ''} onChange={(event) => updateAISettings({ ...aiSettings, outputCostPerMillion: event.target.value === '' ? undefined : Number(event.target.value) })} placeholder="可选" />
                </label>
              </div>
              <div className={`ai-message ai-message-${aiConnection}`} role={aiConnection === 'failed' ? 'alert' : 'status'}>{aiMessage}</div>
              <div className="toolbar">
                <button className="primary" onClick={testAIConnection} disabled={busy || !aiSettings.apiKey || !aiSettings.model || !aiSettings.baseUrl}><KeyRound size={18} />运行模型能力探针</button>
                <button onClick={() => { updateAISettings({ ...aiSettings, apiKey: '' }); setShowKey(false); }}>清除密钥</button>
                <button onClick={() => { setPlannerMode('ai'); setWebsiteInput(''); setRun(null); setFullCheckRequested(false); setPage('start'); }} disabled={aiConnection !== 'connected'}><SquarePen size={18} />使用 AI 新建测试</button>
                <button onClick={() => setPage(settingsReturnPage)}><ChevronRight size={18} />返回刚才的测试</button>
              </div>
              <p className="format-hint">能力探针会验证基础连接、结构化输出和多轮上下文。视觉能力需要单独实测通过后才可启用截图 fallback。</p>
            </div>
          </section>
        )}

        {page === 'new' && (
          <section className="content">
            <details className="panel scenario-workbench optional-workbench">
              <summary>可复用场景库（可选）</summary>
              <div className="panel-title">
                <div><p className="eyebrow">FR-03 / 持久化场景</p><h2>自然语言测试场景</h2></div>
                <FileText size={22} />
              </div>
              {selectedProject ? <>
                <div className="scenario-selector">
                  <label>已保存场景
                    <select value={selectedScenario?.id || ''} onChange={(event) => {
                      const scenario = scenarios.find((item) => item.id === event.target.value);
                      if (scenario) loadScenario(scenario); else resetScenario();
                    }}>
                      <option value="">新建场景</option>
                      {scenarios.map((scenario) => <option value={scenario.id} key={scenario.id}>{scenario.name}</option>)}
                    </select>
                  </label>
                  <button onClick={resetScenario}><SquarePen size={17} />新建场景</button>
                  <span>{selectedProject.name}</span>
                </div>
                <div className="form-grid scenario-form-grid">
                  <label>场景名称<input value={scenarioDraft.name} onChange={(event) => setScenarioDraft({ ...scenarioDraft, name: event.target.value })} placeholder="例如：普通用户加入购物车" /></label>
                  <label className="wide">测试目标<textarea value={scenarioDraft.goal} onChange={(event) => setScenarioDraft({ ...scenarioDraft, goal: event.target.value })} placeholder="搜索指定商品，打开有库存的结果并加入购物车" /></label>
                  <label>前置条件（每行一项）<textarea value={scenarioDraft.preconditions.join('\n')} onChange={(event) => setScenarioDraft({ ...scenarioDraft, preconditions: splitEntries(event.target.value) })} /></label>
                  <label>预期结果（每行一项）<textarea value={scenarioDraft.expectedResults.join('\n')} onChange={(event) => setScenarioDraft({ ...scenarioDraft, expectedResults: splitEntries(event.target.value) })} /></label>
                  <label>禁止操作（每行一项）<textarea value={scenarioDraft.forbiddenActions.join('\n')} onChange={(event) => setScenarioDraft({ ...scenarioDraft, forbiddenActions: splitEntries(event.target.value) })} /></label>
                  <label className="wide">测试数据（JSON）<textarea className="scenario-data-editor" value={scenarioTestData} onChange={(event) => setScenarioTestData(event.target.value)} spellCheck={false} /></label>
                </div>
                <div className="toolbar">
                  <button className="primary" onClick={saveScenario} disabled={busy}><Save size={18} />{selectedScenario ? '保存场景修改' : '保存新场景'}</button>
                  {selectedScenario && <span className="saved-scenario-state">已载入：{selectedScenario.name}</span>}
                </div>
              </> : <div className="empty compact">请先在“项目接入”中创建或选择项目，再保存可复用场景。</div>}
            </details>
            {selectedProject && <div className="run-environment-bar">
              <label>运行环境<select value={selectedEnvironment?.id || ''} onChange={(event) => {
                const environment = environments.find((item) => item.id === event.target.value);
                if (environment) loadEnvironment(environment); else resetEnvironment();
              }}><option value="">项目默认配置</option>{environments.map((environment) => <option value={environment.id} key={environment.id}>{environment.name}</option>)}</select></label>
              <span>{selectedEnvironment ? `${selectedEnvironment.viewport.width}×${selectedEnvironment.viewport.height} · ${selectedEnvironment.artifactRetentionDays} 天` : '1440×960 · 30 天'}</span>
            </div>}
            <div className="planner-switch" role="group" aria-label="计划生成方式">
              <button className={plannerMode === 'rules' ? 'active' : ''} onClick={() => setPlannerMode('rules')}>本地规则规划</button>
              <button className={plannerMode === 'ai' ? 'active' : ''} onClick={() => setPlannerMode('ai')} disabled={aiConnection !== 'connected'}>AI 模型规划{aiConnection === 'connected' ? ` · ${aiSettings.model}` : ' · 请先连接'}</button>
              <button className="settings-link" onClick={() => setPage('ai')}><KeyRound size={16} />配置 AI</button>
            </div>
            <div className="planner-switch" role="group" aria-label="执行策略">
              <button className={executionStrategy === 'fixed' ? 'active' : ''} onClick={() => setExecutionStrategy('fixed')}>固定计划执行</button>
              <button className={executionStrategy === 'agent' ? 'active' : ''} onClick={() => setExecutionStrategy('agent')}>逐步 Agent 探索</button>
            </div>
            {executionStrategy === 'agent' && <label className="checkline visual-fallback-toggle"><input type="checkbox" checked={visualFallbackEnabled} disabled={aiCapabilities?.vision !== 'passed'} onChange={(event) => setVisualFallbackEnabled(event.target.checked)} />启用截图视觉 fallback（需视觉探针通过并授权截图传模）</label>}
            {executionStrategy === 'agent' && <div className="model-data-authorization">
              <label className="checkline"><input type="checkbox" checked={domModelAuthorized} onChange={(event) => setDomModelAuthorized(event.target.checked)} />授权当前网站的脱敏 DOM 发送给模型</label>
              <label className="checkline"><input type="checkbox" checked={screenshotModelAuthorized} onChange={(event) => setScreenshotModelAuthorized(event.target.checked)} />授权当前网站的脱敏截图发送给模型</label>
            </div>}
            <div className="form-grid goal-first-form">
              <label>目标网站地址<input value={draft.targetUrl} onChange={(event) => updateScenarioBackedDraft({ ...draft, targetUrl: event.target.value })} /></label>
              <label className="wide">当前测试目标<textarea value={draft.flow} onChange={(event) => updateScenarioBackedDraft({ ...draft, flow: event.target.value })} placeholder="例如：为普通用户找到一个有库存的商品并加入购物车" /></label>
              <label className="wide">期望结果<textarea value={draft.expectation} onChange={(event) => updateScenarioBackedDraft({ ...draft, expectation: event.target.value })} /></label>
            </div>
            <details className="configuration-details test-advanced-settings">
              <summary>高级设置</summary>
              <div className="form-grid">
                <label>测试名称<input value={draft.name} onChange={(event) => updateScenarioBackedDraft({ ...draft, name: event.target.value })} placeholder="留空时使用测试目标" /></label>
                <label>执行角色<input value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value })} /></label>
                <label className="wide">前置条件<textarea value={draft.preconditions} onChange={(event) => updateScenarioBackedDraft({ ...draft, preconditions: event.target.value })} /></label>
              </div>
            </details>
            <div className="toolbar">
              <button className="primary" onClick={generatePlan} disabled={busy || connection !== 'connected'}><ClipboardCheck size={18} />{plannerMode === 'ai' ? 'AI 生成测试计划' : '生成规则测试计划'}</button>
              <button onClick={reviewPlan} disabled={!plan || busy || warnings.length > 0}>审核并校验计划</button>
              <button onClick={startRun} disabled={busy || (executionStrategy === 'agent' ? aiConnection !== 'connected' : !reviewed || !plan)}><Play size={18} />{executionStrategy === 'agent' ? '启动逐步 Agent 探索' : '启动真实浏览器测试'}</button>
            </div>
            {warnings.length > 0 && <div className="warning-box" role="alert"><strong>计划尚不能审核：</strong><ul>{warnings.map((item) => <li key={item}>{item}</li>)}</ul><p>请把这些描述改写成明确动作后重新生成。</p></div>}
            <PlanEditor plan={plan} fileAssets={fileAssets} onChange={(next) => { setPlan(next); setReviewed(false); }} reviewed={reviewed} />
          </section>
        )}

        {page === 'run' && (
          <section className="content">
            <div className="panel">
              <div className="panel-title">
                <div><p className="eyebrow">网站实时操作</p><h2>{run?.caseName ?? '尚未启动测试'}</h2></div>
                {run ? <StatusBadge status={displayedRunStatus(run)} /> : <StatusBadge status="pending_review" />}
              </div>
              {run && <div className="live-workbench">
                <div className="live-screen">
                  <div className="live-screen-bar"><span><i />实时画面（每一步更新）</span><small>{run.steps[run.steps.length - 1]?.after?.title || run.steps[run.steps.length - 1]?.before?.title || '正在等待网站画面'}</small></div>
                  {(() => {
                    const last = run.steps[run.steps.length - 1];
                    const screenshot = last?.after?.screenshot || last?.before?.screenshot || last?.evidence;
                    return screenshot ? <img src={screenshot} alt="AI 正在操作的网站画面" /> : <div className="screen-placeholder"><Monitor size={44} /><p>网站窗口启动后，画面会显示在这里</p></div>;
                  })()}
                </div>
                <div className="run-chat">
                  <div className="chat-feed" aria-live="polite">
                    <div className="assistant-message"><Bot size={20} /><div><strong>AI 正在测试</strong><p>{run.scenarioGoal || '正在理解你的要求…'}</p></div></div>
                    {run.steps.slice(-6).map((step) => <div className="chat-step" key={step.id}><span className={`step-dot step-${step.result}`} /><div><strong>{step.action}</strong><p>{step.progressAssessment || step.plannerReason || step.target}</p></div></div>)}
                    {run.status === 'passed' && <div className="assistant-message result-good"><ShieldCheck size={20} /><div><strong>检查完成</strong><p>{run.goalSummary || '这次检查已完成，没有发现阻止目标完成的问题。'}</p></div></div>}
                    {['failed', 'error', 'issues_found', 'incomplete', 'system_error'].includes(displayedRunStatus(run)) && <div className="assistant-message result-attention"><ShieldAlert size={20} /><div><strong>{displayedRunStatus(run) === 'system_error' ? (run.completionReason === 'model_error' ? 'AI 在测试中遇到问题' : '测试没有成功启动') : '检查发现问题'}</strong><p>{displayedRunStatus(run) === 'system_error' ? '测试没有完整执行。你可以修改要求后重新开始，本次不算测试完成。' : (run.goalSummary || run.completionReason)}</p></div></div>}
                  </div>
                  <div className="approval-dock">
                    <label>AI 操作前<select value={approvalMode} onChange={(event) => {
                      const mode = event.target.value as ApprovalMode;
                      if (mode === 'full' && !window.confirm('完全访问权限会自动批准所有可执行动作。付款、密码、账号安全修改和删除非测试数据仍会被系统禁止。确定启用吗？')) return;
                      setApprovalMode(mode);
                    }}><option value="ask">请求批准 · 每次写入都问我</option><option value="delegate">替我审批 · 低风险自动处理</option><option value="full">完全访问权限 · 自动批准</option></select></label>
                    {approvalMode === 'full' && <div className="full-access-warning" role="alert"><ShieldAlert size={17} />完全访问权限已开启；绝对禁止动作仍会拦截。</div>}
                  </div>
                </div>
              </div>}
              {run?.pendingClarification && <div className="clarification-box" role="alert" aria-label="Agent 等待澄清">
                <div><h3>{isModelRecoveryWait(run) ? 'AI 服务暂时不可用' : run.pendingClarification.round === 0 ? '当前任务已完成' : `需要补充信息（第 ${run.pendingClarification.round} / 3 轮）`}</h3><p>{run.pendingClarification.question}</p></div>
                <label>{isModelRecoveryWait(run) ? '恢复当前任务' : run.pendingClarification.round === 0 ? '下一项测试要求' : '回答'}<textarea value={clarificationAnswer} onChange={(event) => setClarificationAnswer(event.target.value)} placeholder={isModelRecoveryWait(run) ? '发送“重试”继续当前任务' : run.pendingClarification.round === 0 ? '例如：继续检查页面里的另一个功能……' : undefined} autoFocus /></label>
                <div className="toolbar"><button className="primary" onClick={() => answerClarification()} disabled={busy || !clarificationAnswer.trim()}>{run.pendingClarification.round === 0 ? '发送并继续' : '提交并继续'}</button>{run.pendingClarification.round === 0 && <button onClick={() => answerClarification('结束本次测试')} disabled={busy}>结束本次测试</button>}<button onClick={cancelRun} disabled={busy}>停止整个测试</button></div>
              </div>}
              {run && run.clarificationHistory.length > 0 && <details className="review-history"><summary>对话记录（{run.clarificationHistory.length}）</summary><ol>{run.clarificationHistory.map((item, index) => <li key={item.id || index}>{item.round === 0 ? '继续测试' : `第 ${item.round} 轮补充`}：{item.question} → {item.answer}</li>)}</ol></details>}
              {run?.pendingConfirmation && <div className="confirmation-box" role="alert" aria-label={run.pendingConfirmation.action === 'human_takeover' ? '登录确认' : isLowRiskConfirmation(run.pendingConfirmation.action, run.pendingConfirmation.target, run.pendingConfirmation.rule) ? '操作确认' : '高风险动作确认'}>
                <div><ShieldAlert size={22} /><div><h3>{run.pendingConfirmation.action === 'human_takeover' ? '请先完成网站登录' : 'AI 请求你的批准'}</h3><p>{run.pendingConfirmation.action === 'human_takeover' ? '请在刚打开的测试浏览器中由你本人完成登录。登录成功后回到这里继续检测，AI 不会读取或代填密码、验证码。' : isLowRiskConfirmation(run.pendingConfirmation.action, run.pendingConfirmation.target, run.pendingConfirmation.rule) ? `AI 准备执行“${run.pendingConfirmation.action}”。这是本次测试需要的页面操作，请确认是否继续。` : `AI 准备执行“${run.pendingConfirmation.action}”。这可能影响网站中的业务数据，请谨慎确认。`}</p></div></div>
                <dl><div><dt>目标</dt><dd>{run.pendingConfirmation.target}</dd></div><div><dt>请求时间</dt><dd>{new Date(run.pendingConfirmation.requestedAt).toLocaleString('zh-CN', { hour12: false })}</dd></div></dl>
                <div className="toolbar"><button className="primary" onClick={() => decideConfirmation('approved')} disabled={busy}><ShieldCheck size={18} />{run.pendingConfirmation.action === 'human_takeover' ? '我已登录，继续检测' : '单次批准'}</button><button className="danger-button" onClick={() => decideConfirmation('rejected')} disabled={busy}><ShieldAlert size={18} />{run.pendingConfirmation.action === 'human_takeover' ? '停止本次测试' : '拒绝动作'}</button></div>
              </div>}
              {run && run.confirmationHistory.length > 0 && <details className="review-history"><summary>操作确认记录（{run.confirmationHistory.length}）</summary><ol>{run.confirmationHistory.map((item) => <li key={item.id}>步骤 #{item.stepIndex} · {item.action} · {item.decision === 'approved' ? '已批准' : '已拒绝'} · {item.actor} · {new Date(item.decidedAt).toLocaleString('zh-CN', { hour12: false })}</li>)}</ol></details>}
              {run && <details className="technical-details"><summary>查看技术详情</summary><div className="run-classification"><span>已完成步骤 {run.steps.length}</span><span>完成原因 {run.completionReason}</span><span>环境 {run.environmentId || '系统默认'}</span><span>证据保留 {run.artifactRetentionDays} 天</span>{run.runnerIsolation && <span>{isolationText(run.runnerIsolation)}</span>}<span>模型调用 {run.modelCalls}</span><span>Token {run.inputTokens + run.outputTokens}</span>{run.estimatedCost !== undefined && <span>估算成本 {run.estimatedCost}</span>}</div><CommerceRunSummary run={run} /><AgentDecisionList run={run} /><RunSteps run={run} /></details>}
              {run && runCanRecover(run) && <div className="run-recovery-composer">
                <label>{displayedRunStatus(run) === 'system_error' ? '修改要求后开始新的测试' : '这次会话已经结束；如需继续，请开始新的测试'}<textarea value={draft.flow} onChange={(event) => setDraft({ ...draft, flow: event.target.value })} placeholder="告诉 AI 新的测试要求……" /></label>
                <div className="toolbar"><button className="primary" onClick={launchBeginnerRun} disabled={busy || !draft.flow.trim()}><Play size={17} />开始新的测试</button><button onClick={returnToRunScope} disabled={busy}>返回检查范围</button><button onClick={() => { setRun(null); setPage('start'); }} disabled={busy}>更换网站</button></div>
              </div>}
              <div className="toolbar">
                {run && activeRunStatuses.has(run.status) && <button onClick={cancelRun} disabled={busy}><StopCircle size={18} />终止执行</button>}
                <button onClick={() => openReport(run?.id)} disabled={!run}><FileText size={18} />打开报告详情</button>
              </div>
            </div>
          </section>
        )}

        {page === 'history' && (
          <section className="content panel">
            <div className="panel-title"><h2>真实运行历史</h2><span className="selection-count">已选择 {selectedRunIds.size} / {history.length}</span></div>
            <div className="history-actions">
              <label className="checkline"><input type="checkbox" aria-label="全选报告" checked={history.length > 0 && selectedRunIds.size === history.length} onChange={toggleAllRuns} disabled={!history.length} />全选</label>
              <button onClick={() => setSelectedRunIds(new Set())} disabled={!selectedRunIds.size}>取消选择</button>
              <button onClick={cleanupExpiredRuns} disabled={busy}><RefreshCw size={18} />执行保留策略</button>
              <a className="evidence-link" href={api.deletionAuditUrl} download><Download size={18} />下载删除审计</a>
              <button className="danger-button" onClick={deleteSelectedRuns} disabled={busy || !selectedRunIds.size}><Trash2 size={18} />删除选中</button>
            </div>
            <RunTable runs={history} onReport={openReport} selectedRunIds={selectedRunIds} onToggleSelection={toggleRunSelection} />
          </section>
        )}

        {page === 'report' && (
          <section className="content">
            <ReportView report={report} onReview={reviewFinding} onSavePath={savePathReview} onSaveSource={saveGeneratedSource} onReplay={replayRun} />
          </section>
        )}

        {page === 'acceptance' && (
          <AcceptanceConsole
            batches={acceptanceBatches}
            batch={acceptanceBatch}
            busy={busy}
            onSelect={setAcceptanceBatch}
            onStart={startAcceptance}
            onRefresh={openAcceptance}
            onControl={controlAcceptance}
          />
        )}

        {page === 'spatial-acceptance' && (
          <section className="content">
            <div className="panel-title"><div><p className="eyebrow">内部专项标准 · 每项需重复验证 5 次</p><h2>三维与复杂网站完整验收</h2></div><button onClick={openSpatialAcceptance} disabled={busy}><RefreshCw size={18} />刷新</button></div>
            {cesiumSuite ? <>
              <div className="metric-grid">
                <Metric label="应检查项目" value={cesiumSuite.summary.total} />
                <Metric label="真实完整通过" value={cesiumSuite.summary.passed} />
                <Metric label="缺少条件" value={cesiumSuite.summary.byStatus.blocked || 0} tone="danger" />
                <Metric label="尚待完整验证" value={(cesiumSuite.summary.byStatus.unverified || 0) + (cesiumSuite.summary.byStatus.observed_read_only || 0)} />
              </div>
              <div className="panel">
                <p className="security-note"><ShieldAlert size={18} />这里显示的是内部验收底账。“看见页面”“正在加载”“只查看过”都不算通过；当前不会虚报完成。</p>
                <div className="scan-meta"><span><strong>专项目标：</strong>文件上传、三维画面、长任务、权限与清理</span><span><strong>固定测试文件：</strong>{cesiumSuite.testData.manifestStatus === 'blocked' ? '尚未提供完整数据包' : cesiumSuite.testData.manifestStatus}</span><span><strong>待清理测试资源：</strong>{cesiumSuite.resourceLedger.pendingCleanup}</span></div>
                <div className="toolbar">
                  <label>重要程度<select value={cesiumPriority} onChange={(event) => setCesiumPriority(event.target.value as typeof cesiumPriority)}><option value="all">全部</option><option value="P0">必须通过</option><option value="P1">重要</option><option value="P2">补充</option></select></label>
                  <label>当前状态<select value={cesiumStatus} onChange={(event) => setCesiumStatus(event.target.value as typeof cesiumStatus)}><option value="all">全部</option><option value="unverified">尚未测试</option><option value="blocked">缺少条件</option><option value="observed_read_only">只查看过</option><option value="passed">已通过</option><option value="failed">未通过</option></select></label>
                </div>
                <div className="table-wrap"><table><thead><tr><th>内部编号</th><th>要检查什么</th><th>会不会改动网站</th><th>已完整验证</th><th>当前状态</th></tr></thead><tbody>{visibleCesiumCases.map((item) => <tr key={item.id}><td><strong>{item.id}</strong></td><td><strong>{item.title}</strong><br /><span className="muted">合格标准：{item.exactExpected}</span></td><td>{effectLevelText(item.effectLevel)}</td><td>{item.execution.repetitionsCompleted}/{item.execution.requiredRepetitions} 次</td><td><strong>{acceptanceStatusText(item.execution.status)}</strong><br /><span className="muted">{item.execution.reason}</span></td></tr>)}</tbody></table></div>
              </div>
            </> : <div className="empty">正在读取专项验收底账。</div>}
          </section>
        )}

        {page === 'simulation-acceptance' && (
          <section className="content acceptance-layout">
            <div className="panel-title"><div><p className="eyebrow">内部专项标准 · 普通测试不会显示本页</p><h2>仿真业务完整验收</h2></div><button onClick={openSimulationAcceptance} disabled={busy}><RefreshCw size={18} />刷新</button></div>
            {gaeCatalog && gaeWorkflow ? <>
              <div className="metric-grid">
                <Metric label="应检查的业务项目" value={gaeCatalog.scenarioCount} />
                <Metric label="每项重复检查" value={`${gaeCatalog.repeatCount} 次`} />
                <Metric label="计划检查总次数" value={gaeCatalog.plannedRuns} />
                <Metric label="缺少真实验证条件" value={gaeCatalog.blockedCount} tone="danger" />
              </div>
              <div className="panel">
                <p className="security-note"><ShieldAlert size={18} />旧版只能确认“检查合同能被系统读取和调度”，没有成功访问企业目标站。因此当前 30 项仍全部标为缺少真实验证条件，不会虚报通过。</p>
                <div className="toolbar">
                  <button className="primary" onClick={() => startGAEAcceptance(true)} disabled={busy || Boolean(gaeBatch && ['queued', 'running', 'cancelling'].includes(gaeBatch.status))}><Play size={18} />检查 30×5 调度合同</button>
                  <button onClick={() => startGAEAcceptance(false)} disabled={busy || !selectedProject || !selectedEnvironment || Boolean(gaeBatch && ['queued', 'running', 'cancelling'].includes(gaeBatch.status))}><Play size={18} />开始真实全面验收</button>
                  {gaeBatch && <><button onClick={() => controlGAEAcceptance('cancel')} disabled={busy || !['queued', 'running', 'cancelling'].includes(gaeBatch.status)}><StopCircle size={18} />停止</button><button onClick={() => controlGAEAcceptance('resume')} disabled={busy || !['cancelled', 'failed'].includes(gaeBatch.status)}><RefreshCw size={18} />继续</button><button onClick={() => controlGAEAcceptance('retry-failed')} disabled={busy || ['queued', 'running', 'cancelling'].includes(gaeBatch.status)}><RefreshCw size={18} />重试未完成项</button></>}
                </div>
                <p className="muted">“检查调度合同”不会打开目标网站，只验证系统确实保留全部 30 项、每项 5 次；只有“真实全面验收”才可能产生真实通过结果。</p>
                {gaeBatch && <div className="scan-meta"><span><strong>当前批次：</strong>{gaeBatch.dryRun ? '仅合同检查／未实测' : '真实网站执行'}</span><span><strong>进度：</strong>{gaeBatch.completedRuns}/{gaeBatch.plannedRuns}</span><span><strong>状态：</strong>{gaeBatch.status}</span>{gaeBatch.currentScenarioId && <span><strong>正在检查：</strong>{gaeBatch.currentScenarioId} · 第 {gaeBatch.currentRepeat} 次</span>}{gaeBatch.summaryAvailable && <><a className="evidence-link" href={`/api/acceptance/gaealavic/batches/${gaeBatch.batchId}/summary.json`} download><Download size={17} />下载汇总</a><a className="evidence-link" href={`/api/acceptance/gaealavic/batches/${gaeBatch.batchId}/report.md`} download><FileText size={17} />下载报告</a></>}</div>}
              </div>
              <div className="panel acceptance-scenarios">
                <h2>按业务内容查看 30 项检查</h2>
                <p className="muted">这里优先说清楚“要检查什么”，内部编号不作为用户理解业务的前提。</p>
                <table><thead><tr><th>业务范围</th><th>要检查什么</th><th>所需账号</th><th>当前状态</th><th>还缺什么</th></tr></thead><tbody>{gaeCatalog.scenarios.map((item) => <tr key={item.id}><td>{simulationCategoryName(item.category)}</td><td><strong>{item.name}</strong></td><td>{simulationRoleName(item.accountRole)}</td><td>{item.bindingStatus === 'ready' ? '已补齐执行条件' : '缺少真实验证条件'}</td><td>{item.blockedDependencies.join('；') || '-'}</td></tr>)}</tbody></table>
              </div>
              <div className="panel">
                <div className="panel-title"><div><h2>从登录到清理的一次完整流程</h2><p className="muted">按顺序串联建模、环境数据、想定、运行、结果下载和测试数据清理。</p></div><span>{gaeWorkflow.bindingStatus === 'ready' ? '可执行' : '尚缺真实条件'}</span></div>
                <ol className="workflow-list">{gaeWorkflow.stages.map((stage) => <li key={stage.id}><strong>{stage.goal}</strong><span>完成后应留下：{stage.requiredOutputs.map(simulationOutputName).join('、')}</span></li>)}</ol>
                <div className="toolbar"><button className="primary" onClick={() => startGAEL4(true)} disabled={busy}><Play size={18} />检查完整流程合同</button><button onClick={() => startGAEL4(false)} disabled={busy || !selectedProject || !selectedEnvironment}><Play size={18} />执行真实完整流程</button>{gaeL4Result && <><a className="evidence-link" href={gaeL4Result.reportUrls.json} download><Download size={17} />结果数据</a><a className="evidence-link" href={gaeL4Result.reportUrls.markdown} download><FileText size={17} />文字报告</a></>}</div>
                {gaeL4Result && <p className="security-note"><ShieldCheck size={18} />本次状态：{gaeL4Result.verificationStatus === 'dry_run_only' ? '只检查合同，未访问目标网站' : gaeL4Result.status}；清理：{gaeL4Result.cleanupSuccess ? '已完成' : '需要人工检查'}。</p>}
              </div>
              <details className="panel technical-details"><summary>专业人员：补充真实执行绑定</summary><p className="muted">普通用户不需要填写。只有掌握目标站页面、账号权限、固定测试文件及书面授权的人员，才能在这里补充经过审核的动作和判断依据。</p><label className="wide">30 项执行绑定（JSON）<textarea className="environment-json-editor" value={gaeScenarioBindings} onChange={(event) => setGAEScenarioBindings(event.target.value)} spellCheck={false} /></label><label className="wide">完整流程阶段绑定（JSON）<textarea className="environment-json-editor" value={gaeL4Bindings} onChange={(event) => setGAEL4Bindings(event.target.value)} spellCheck={false} /></label></details>
            </> : <div className="empty">正在读取仿真业务验收底账。</div>}
          </section>
        )}
      </main>
    </div>
  );
}

function pageTitle(page: Page) {
  return { start: '开始测试', compose: '告诉 AI 你想测试什么', settings: '高级设置', overview: '运行总览', projects: '网站与内部配置', ai: '更换 AI 服务', new: '传统测试编辑器', run: 'AI 正在测试', history: '测试记录', report: '测试结果', acceptance: '完整电商验收', 'spatial-acceptance': '三维与复杂网站验收', 'simulation-acceptance': '仿真业务完整验收' }[page];
}

function simulationCategoryName(category: string) {
  return ({ 登录: '登录与账号', 导航: '页面与菜单', 智能体: '装备与模型', 环境: '环境与地形', 想定: '任务想定', 仿真: '运行与结果', 强化学习: '训练与评估', 帮助: '帮助与说明' } as Record<string, string>)[category] || category;
}

function simulationRoleName(role: string) {
  return ({ 管理员: '管理员账号', 普通测试: '普通测试账号', 只读: '只查看账号', '运维/开发者': '运维或开发账号' } as Record<string, string>)[role] || role;
}

function simulationOutputName(value: string) {
  return ({ accountId: '账号身份', role: '账号权限', sessionId: '登录状态', businessId: '新建数据编号', uploadSha256: '上传文件校验值', geometryEvidence: '地图绘制证据', relationId: '数据关联编号', simulationRunId: '仿真运行编号', confirmationId: '用户确认记录', stateTimeline: '运行状态时间线', websocketOrPollingEvidence: '后台状态变化证据', simulationDataSha256: '仿真数据文件校验值', evaluationDataSha256: '评估数据文件校验值', cleanupReport: '清理报告', manualActions: '待人工处理事项', evidenceManifest: '证据清单', acceptanceSummary: '验收汇总' } as Record<string, string>)[value] || value;
}

function AcceptanceConsole({ batches, batch, busy, onSelect, onStart, onRefresh, onControl }: {
  batches: AcceptanceBatch[]; batch: AcceptanceBatch | null; busy: boolean;
  onSelect: (batch: AcceptanceBatch | null) => void; onStart: () => void; onRefresh: () => void;
  onControl: (action: 'cancel' | 'resume' | 'retry-failed') => void;
}) {
  const scenarios = useMemo(() => {
    if (!batch) return [];
    return Array.from(new Set(batch.attempts.map((item) => item.scenarioId))).map((scenarioId) => {
      const attempts = batch.attempts.filter((item) => item.scenarioId === scenarioId);
      return { scenarioId, title: attempts[0].title, priority: attempts[0].priority, attempts };
    });
  }, [batch]);
  const thresholdNames: Record<string, string> = {
    p0Completion: 'P0 完成率', allScenarioPassRate: '全场景通过率', stableReplayRate: '稳定回放率',
    evidenceCompleteness: '证据完整率', amountAccuracy: '金额准确率', cleanupCompleteness: '清理完整率',
    zeroToleranceIncidents: '零容忍事件'
  };
  return <section className="content acceptance-layout">
    <div className="panel">
      <div className="panel-title"><div><p className="eyebrow">完整检查 / 每项重复验证 5 次</p><h2>电商网站全面验收</h2></div><ClipboardCheck size={22} /></div>
      <div className="toolbar"><button className="primary" onClick={onStart} disabled={busy}><Play size={18} />建立验收批次</button><button onClick={onRefresh} disabled={busy}><RefreshCw size={18} />刷新</button>{batch && <><button onClick={() => onControl('cancel')} disabled={busy || batch.status === 'cancelled' || batch.status === 'blocked'}><StopCircle size={18} />取消</button><button onClick={() => onControl('resume')} disabled={busy || batch.status !== 'cancelled'}><Play size={18} />恢复</button><button onClick={() => onControl('retry-failed')} disabled={busy || batch.summary.counts.failed === 0}><RefreshCw size={18} />重试失败</button><a className="evidence-link" href={api.acceptanceReportUrl(batch.id)} download><Download size={18} />HTML 报告</a></>}</div>
      <label>验收批次<select value={batch?.id || ''} onChange={(event) => onSelect(batches.find((item) => item.id === event.target.value) || null)}><option value="">尚无批次</option>{batches.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.verificationStatus}</option>)}</select></label>
    </div>
    {!batch ? <div className="panel empty">尚未建立京东验收批次。</div> : <>
      <div className="metric-grid">
        <Metric label="检查项目" value={batch.scenarioCount} /><Metric label="每项重复" value={batch.repeatCount} /><Metric label="计划检查" value={batch.plannedAttempts} /><Metric label="已验证" value={batch.summary.verifiedAttempts} tone={batch.summary.verifiedAttempts ? undefined : 'danger'} />
      </div>
      <div className="panel">
        <div className="panel-title"><h2>批次状态</h2><span className={`compatibility-state ${batch.summary.passed ? 'compatible' : 'attention'}`}>{batch.summary.passed ? '通过' : batch.verificationStatus}</span></div>
        <dl className="acceptance-counts">{Object.entries(batch.summary.counts).map(([name, count]) => <div key={name}><dt>{name}</dt><dd>{count}</dd></div>)}</dl>
        <div className="threshold-grid">{Object.entries(batch.summary.thresholds).map(([name, threshold]) => {
          const actual = typeof threshold.actual === 'number' && name !== 'zeroToleranceIncidents' ? `${(threshold.actual * 100).toFixed(2)}%` : String(threshold.actual);
          const required = typeof threshold.required === 'number' && name !== 'zeroToleranceIncidents' ? `${threshold.required * 100}%` : String(threshold.required);
          const passed = acceptanceThresholdPassed(name, threshold);
          return <div className={passed ? 'threshold-pass' : 'threshold-fail'} key={name}><span>{thresholdNames[name] || name}</span><strong>{actual}</strong><small>要求 {required}</small></div>;
        })}</div>
      </div>
      <div className="panel acceptance-scenarios"><h2>按业务内容查看进度</h2><p className="muted">系统内部仍执行全部 65 项固定检查，这里不显示技术编号。</p><table><thead><tr><th>检查内容</th><th>重要程度</th><th>5 次状态</th><th>需要处理</th></tr></thead><tbody>{scenarios.map((scenario) => <tr key={scenario.scenarioId}><td><strong>{acceptanceModuleName(scenario.title)}</strong><span>{scenario.title.replace(/^J\d+\s*[-—·:]?\s*/i, '')}</span></td><td>{scenario.priority === 'P0' ? '必须通过' : '重要'}</td><td><div className="attempt-strip">{scenario.attempts.map((attempt) => <span className={`attempt-${attempt.status}`} title={`第 ${attempt.repeat} 次：${attempt.status}`} key={attempt.id}>{attempt.repeat}</span>)}</div></td><td>{Array.from(new Set(scenario.attempts.flatMap((item) => item.blockedDependencies))).join('；') || '-'}</td></tr>)}</tbody></table></div>
    </>}
  </section>;
}

function acceptanceModuleName(title: string) {
  if (/登录|会话|账号/.test(title)) return '登录与账号';
  if (/搜索|筛选|商品|详情/.test(title)) return '查找与查看商品';
  if (/收藏|购物车/.test(title)) return '收藏与购物车';
  if (/订单|结算|地址|优惠|发票/.test(title)) return '提交订单前流程';
  if (/支付|退款/.test(title)) return '付款安全与人工接管';
  if (/清理|恢复|重试|幂等/.test(title)) return '异常恢复与数据清理';
  if (/隐私|脱敏|安全|越权/.test(title)) return '隐私与安全';
  return '网站基础功能';
}

export function acceptanceThresholdPassed(
  name: string,
  threshold: { actual: number | boolean; required: number | boolean }
) {
  if (name === 'zeroToleranceIncidents') return threshold.actual === threshold.required;
  return typeof threshold.required === 'number'
    ? Number(threshold.actual) >= threshold.required
    : threshold.actual === threshold.required;
}

function splitEntries(value: string) {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
}

function readTextFile(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(new Error('无法读取登录态文件'));
    reader.readAsText(file, 'utf-8');
  });
}

function sessionStatusText(status: SessionMetadata['expiryStatus']) {
  return { active: '有效', warning: '部分过期', expired: '已过期', unknown: '有效期未知' }[status];
}

function CompatibilityView({ report }: { report: CompatibilityReport }) {
  const metrics = { ...report.pageSummary, ...report.candidateLocators };
  const config = report.recommendedConfig;
  return <div className="panel compatibility-panel">
    <div className="panel-title"><div><p className="eyebrow">{new Date(report.generatedAt).toLocaleString('zh-CN', { hour12: false })}</p><h2>兼容性扫描报告</h2></div><span className={`compatibility-state ${report.status}`}>{report.status === 'compatible' ? '基础兼容' : '需关注'}</span></div>
    <div className="scan-meta"><span><strong>页面：</strong>{report.title || '未设置标题'}</span><span><strong>最终地址：</strong>{report.finalUrl}</span><span aria-label="当前接入级别"><strong>当前级别：</strong>{report.onboardingLevel}</span><span aria-label="建议接入级别"><strong>建议级别：</strong>{report.recommendedOnboardingLevel}</span></div>
    {report.sampleScenarioId && <p className="scan-scenario-state" aria-label="扫描示例场景">{report.sampleScenarioCreated ? '已自动创建' : '已关联'}可编辑示例场景：<code>{report.sampleScenarioId}</code></p>}
    <div className="scan-metrics">{Object.entries(metrics).map(([key, value]) => <div key={key}><span>{scanMetricLabel(key)}</span><strong>{value}</strong></div>)}</div>
    <div className="compatibility-columns">
      <ReportList title="稳定可测区域" items={report.stableAreas} empty="未识别到足够稳定的结构化区域" />
      <ReportList title="视觉 fallback 区域" items={report.visualAreas} empty="未发现必须依赖视觉的区域" />
      <ReportList title="自适应区域" items={report.adaptiveAreas} empty="未发现需要自适应定位的区域" />
      <ReportList title="人工处理区域" items={report.manualAreas} empty="未发现验证码、MFA 等人工步骤" />
      <ReportList title="认证与会话信号" items={report.authenticationSignals} />
      <ReportList title="异步加载模式" items={report.asyncPatterns} empty="未观察到明确异步加载信号" />
      <ReportList title="主要导航入口" items={report.navigationEntries} empty="未识别到主要导航入口" />
      <ReportList title="已扫描页面" items={report.scannedPages.map((page) => `${page.pageType} · ${page.title || '未设置标题'} · ${page.url}`)} />
      <ReportList title="检测到的能力" items={report.capabilities} />
      <ReportList title="不可直接覆盖区域" items={report.blockedAreas} empty="标准 DOM 范围内未发现阻塞区域" />
      <ReportList title="接入建议" items={report.recommendations} />
      <ReportList title="建议首批场景" items={report.suggestedScenarios} />
      <ReportList title="第三方域名" items={report.thirdPartyHosts} empty="未发现" />
      <ReportList title="控制台 / 网络异常" items={[...report.consoleErrors, ...report.failedRequests]} empty="未发现" />
    </div>
    <div className="recommended-config" role="region" aria-label="扫描建议配置">
      <h3>扫描建议配置</h3>
      <dl><div><dt>允许域名</dt><dd>{config.allowedHosts.join('、') || '仅 Base URL'}</dd></div><div><dt>网络忽略</dt><dd>{config.ignoreRules.join('、') || '无'}</dd></div><div><dt>Viewport</dt><dd>{config.viewport.width}×{config.viewport.height}</dd></div><div><dt>运行限制</dt><dd>{config.limits.maxSteps} 步 / {config.limits.timeoutSeconds} 秒 / {config.limits.maxModelCalls} 次模型调用</dd></div></dl>
    </div>
  </div>;
}

function ReportList({ title, items, empty = '无' }: { title: string; items: string[]; empty?: string }) {
  return <section><h3>{title}</h3>{items.length ? <ul>{items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul> : <p className="muted">{empty}</p>}</section>;
}

function scanMetricLabel(key: string) {
  return ({ buttons: '按钮', links: '链接', inputs: '输入框', selects: '下拉框', textareas: '文本域', canvases: 'Canvas', webglRegions: 'WebGL', iframes: 'Iframe', crossOriginIframes: '跨域 Iframe', fileInputs: '文件输入', shadowRoots: 'Shadow Root', contentEditors: '复杂编辑器', unlabeledControls: '无名称控件', duplicateIds: '重复 ID', loadingSignals: '加载信号', testIds: '测试标识', labels: '标签', roles: 'ARIA 角色', ariaNames: 'ARIA 名称', namedControls: '可命名控件' } as Record<string, string>)[key] || key;
}

function Metric({ label, value, tone }: { label: string; value: string | number; tone?: 'danger' }) {
  return <div className={`metric ${tone ?? ''}`}><span>{label}</span><strong>{value}</strong></div>;
}

const actionLabels: Record<ActionType, string> = {
  navigate: '打开地址', click: '点击', fill: '输入', select: '选择', wait_for: '等待元素', screenshot: '截图检查点',
  clear: '清空', check: '勾选', uncheck: '取消勾选', hover: '悬停', scroll: '滚动', back: '后退', reload: '刷新', press: '按键', visual_click: '视觉点击', visual_hover: '视觉悬停', visual_scroll: '视觉滚动', visual_drag: '视觉拖拽', bridge_click: 'Bridge 点击', human_takeover: '人工接管', upload_file: '上传固定文件', download: '下载并校验'
};

const editableActionLabels: Partial<Record<ActionType, string>> = {
  navigate: '打开地址', click: '点击', fill: '输入', select: '选择', wait_for: '等待元素', screenshot: '截图检查点', human_takeover: '人工接管', upload_file: '上传固定文件', download: '下载并校验'
};

const commerceActionOptions: Array<[NonNullable<PlanStep['commerce']>['action'], string]> = [
  ['browse', '浏览'], ['search', '搜索'], ['filter', '筛选'], ['sort', '排序'], ['paginate', '翻页'],
  ['view_product', '查看商品'], ['view_account_structure', '查看账户结构'], ['view_help', '查看帮助'],
  ['change_region', '切换配送地区'], ['add_cart', '加入购物车'], ['remove_cart', '移出购物车'],
  ['favorite', '收藏'], ['unfavorite', '取消收藏'], ['follow', '关注'], ['unfollow', '取消关注'],
  ['claim_coupon', '领券'], ['write_address', '写入地址'], ['write_invoice_profile', '写入发票资料'],
  ['submit_order', '提交订单'], ['pay', '支付'], ['cancel_order', '取消订单'],
  ['confirm_receipt', '确认收货'], ['request_after_sale', '申请售后'], ['refund', '退款'],
  ['review', '评价'], ['send_message', '发送消息'], ['download_invoice', '下载发票'],
  ['merchant_mutation', '商家后台写入']
];

const commerceTargetKinds: Array<[NonNullable<PlanStep['commerce']>['targetKind'], string]> = [
  ['skuId', 'SKU'], ['spuId', 'SPU'], ['cartLineId', '购物车行'], ['orderId', '订单'],
  ['paymentId', '支付'], ['refundId', '退款'], ['afterSaleId', '售后'],
  ['shipmentId', '物流'], ['invoiceId', '发票']
];

type EditableLocatorKey = 'label' | 'test_id' | 'role' | 'text' | 'css';

function locatorEntry(locator?: Locator): [EditableLocatorKey, string] {
  const order: EditableLocatorKey[] = ['label', 'test_id', 'role', 'text', 'css'];
  const key = order.find((item) => locator?.[item]) || 'text';
  return [key, locator?.[key] || ''];
}

function PlanEditor({ plan, fileAssets, onChange, reviewed }: { plan: TestPlan | null; fileAssets: FileAsset[]; onChange: (plan: TestPlan) => void; reviewed: boolean }) {
  if (!plan) return <div className="empty">连接真实执行服务并生成计划后，这里会显示实际可执行的动作与断言。</div>;
  const updateStep = (index: number, next: PlanStep) => onChange({ ...plan, steps: plan.steps.map((item, i) => i === index ? next : item) });
  const updateAssertion = (index: number, next: PlanAssertion) => onChange({ ...plan, assertions: plan.assertions.map((item, i) => i === index ? next : item) });
  return (
    <div className="panel plan-panel">
      <div className="panel-title"><h2>可审核执行计划</h2><StatusBadge status={reviewed ? 'passed' : 'pending_review'} /></div>
      <div className="plan-meta"><span>目标：{plan.base_url}</span><span>步骤：{plan.steps.length}</span><span>断言：{plan.assertions.length}</span></div>
      <div className="plan-list">
        {plan.steps.map((step, index) => {
          const [strategy, locatorValue] = locatorEntry(step.locator);
          return (
            <div className="plan-row" key={`${index}-${step.action}`}>
              <strong>#{index + 1}</strong>
              <select aria-label={`步骤 ${index + 1} 动作`} value={step.action} onChange={(event) => {
                const action = event.target.value as ActionType;
                updateStep(index, action === 'human_takeover' ? {
                  ...step, action, locator: undefined, stability_level: 'D', stability_reason: '受保护交互必须人工接管',
                  takeoverReason: step.takeoverReason || 'other', browserTarget: step.browserTarget || { page: 'current', waitTimeoutMs: 120000 }
                } : { ...step, action });
              }}>
                {Object.entries(editableActionLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
              <input aria-label={`步骤 ${index + 1} 说明`} value={step.description || ''} placeholder="步骤说明" onChange={(event) => updateStep(index, { ...step, description: event.target.value })} />
              {step.action === 'navigate' ? (
                <input aria-label={`步骤 ${index + 1} 路径`} value={step.target || ''} placeholder="/path 或完整地址" onChange={(event) => updateStep(index, { ...step, target: event.target.value })} />
              ) : step.action === 'screenshot' ? <span className="plan-note">执行后保存页面截图</span> : step.action === 'human_takeover' ? <span className="plan-note">用户完成受保护交互后验证恢复条件</span> : (
                <div className="locator-editor">
                  <select aria-label={`步骤 ${index + 1} 定位方式`} value={strategy} onChange={(event) => updateStep(index, { ...step, locator: { [event.target.value]: locatorValue } })}>
                    <option value="label">表单标签</option><option value="test_id">测试 ID</option><option value="role">ARIA 角色</option><option value="text">可见文本</option><option value="css">CSS</option>
                  </select>
                  <input aria-label={`步骤 ${index + 1} 定位值`} value={locatorValue} placeholder="定位值" onChange={(event) => updateStep(index, { ...step, locator: { [strategy]: event.target.value } })} />
                </div>
              )}
              {(step.action === 'fill' || step.action === 'select') && <input aria-label={`步骤 ${index + 1} 输入值`} value={step.value || ''} placeholder="输入 / 选择值" onChange={(event) => updateStep(index, { ...step, value: event.target.value })} />}
              {step.action === 'upload_file' && <label>固定文件<select aria-label={`步骤 ${index + 1} 固定文件`} value={step.fileAssetRef || ''} onChange={(event) => updateStep(index, { ...step, fileAssetRef: event.target.value || undefined })}><option value="">选择已登记文件</option>{fileAssets.map((asset) => <option value={asset.ref} key={asset.sha256}>{asset.filename} · {asset.sha256.slice(0, 12)}...</option>)}</select></label>}
              {step.action === 'download' && <label>期望 SHA-256（可选）<input aria-label={`步骤 ${index + 1} 下载 SHA-256`} value={step.expectedDownloadSha256 || ''} onChange={(event) => updateStep(index, { ...step, expectedDownloadSha256: event.target.value.toLowerCase() || undefined })} /></label>}
              <details className="commerce-step-editor">
                <summary>浏览器上下文 · {step.browserTarget?.page === 'newest' ? '最新窗口' : step.browserTarget?.page === 'opener' ? '打开者窗口' : '当前窗口'}</summary>
                <div className="commerce-step-grid">
                  <label>目标窗口<select aria-label={`步骤 ${index + 1} 目标窗口`} value={step.browserTarget?.page || 'current'} onChange={(event) => updateStep(index, { ...step, browserTarget: { page: event.target.value as NonNullable<PlanStep['browserTarget']>['page'], waitTimeoutMs: step.browserTarget?.waitTimeoutMs || 10000, urlContains: step.browserTarget?.urlContains, frameCss: step.browserTarget?.frameCss } })}><option value="current">当前窗口</option><option value="newest">最新弹窗／标签页</option><option value="opener">返回打开者窗口</option></select></label>
                  <label>等待时限（毫秒）<input type="number" min="500" max="120000" aria-label={`步骤 ${index + 1} 上下文等待时限`} value={step.browserTarget?.waitTimeoutMs || 10000} onChange={(event) => updateStep(index, { ...step, browserTarget: { page: step.browserTarget?.page || 'current', waitTimeoutMs: Number(event.target.value), urlContains: step.browserTarget?.urlContains, frameCss: step.browserTarget?.frameCss } })} /></label>
                  <label className="wide">URL 恢复条件<input aria-label={`步骤 ${index + 1} URL 恢复条件`} value={step.browserTarget?.urlContains || ''} placeholder="例如 /order/ 或 /account" onChange={(event) => updateStep(index, { ...step, browserTarget: { page: step.browserTarget?.page || 'current', waitTimeoutMs: step.browserTarget?.waitTimeoutMs || 10000, urlContains: event.target.value || undefined, frameCss: step.browserTarget?.frameCss } })} /></label>
                  <label className="wide">iframe CSS（可选）<input aria-label={`步骤 ${index + 1} iframe CSS`} value={step.browserTarget?.frameCss || ''} placeholder="例如 iframe[data-payment]" onChange={(event) => updateStep(index, { ...step, browserTarget: { page: step.browserTarget?.page || 'current', waitTimeoutMs: step.browserTarget?.waitTimeoutMs || 10000, urlContains: step.browserTarget?.urlContains, frameCss: event.target.value || undefined } })} /></label>
                  {step.action === 'human_takeover' && <>
                    <label>接管原因<select aria-label={`步骤 ${index + 1} 接管原因`} value={step.takeoverReason || 'other'} onChange={(event) => updateStep(index, { ...step, takeoverReason: event.target.value as NonNullable<PlanStep['takeoverReason']> })}><option value="captcha">验证码</option><option value="slider">滑块</option><option value="qr_login">扫码登录</option><option value="risk_control">风控验证</option><option value="payment_auth">支付认证</option><option value="other">其他受保护交互</option></select></label>
                    <label>恢复元素文本<input aria-label={`步骤 ${index + 1} 人工恢复元素`} value={step.takeoverResumeLocator?.text || ''} placeholder="可见的登录后／完成后文本" onChange={(event) => updateStep(index, { ...step, takeoverResumeLocator: event.target.value ? { text: event.target.value } : undefined })} /></label>
                  </>}
                </div>
              </details>
              <details className="commerce-step-editor">
                <summary>电商安全语义{step.commerce ? ` · ${step.commerce.action}` : ' · 未启用'}</summary>
                <label className="checkline commerce-enabled"><input type="checkbox" aria-label={`步骤 ${index + 1} 启用电商安全语义`} checked={Boolean(step.commerce)} onChange={(event) => updateStep(index, event.target.checked ? { ...step, commerce: { action: 'browse', e2eOwned: false, ledgerOperation: 'none' } } : { ...step, commerce: undefined })} />此步骤属于电商动作</label>
                {step.commerce && <div className="commerce-step-grid">
                  <label>电商动作<select aria-label={`步骤 ${index + 1} 电商动作`} value={step.commerce.action} onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, action: event.target.value as NonNullable<PlanStep['commerce']>['action'] } })}>{commerceActionOptions.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
                  <label>业务对象<select aria-label={`步骤 ${index + 1} 业务对象`} value={step.commerce.targetKind || ''} onChange={(event) => {
                    const targetKind = event.target.value as NonNullable<PlanStep['commerce']>['targetKind'] | '';
                    updateStep(index, { ...step, commerce: { ...step.commerce!, targetKind: targetKind || undefined, targetRef: targetKind ? step.commerce!.targetRef : undefined } });
                  }}><option value="">无业务对象</option>{commerceTargetKinds.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
                  <label className="wide">业务引用<input aria-label={`步骤 ${index + 1} 业务引用`} value={step.commerce.targetRef || ''} disabled={!step.commerce.targetKind} placeholder="resource:E2E_ORDER_1 / secret:KEY / public-sku:SKU_1" onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, targetRef: event.target.value || undefined } })} /></label>
                  <label>执行前状态<input aria-label={`步骤 ${index + 1} 执行前状态`} value={step.commerce.beforeState || ''} placeholder="例如 absent / pending" onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, beforeState: event.target.value || undefined } })} /></label>
                  <label>幂等键引用<input aria-label={`步骤 ${index + 1} 幂等键引用`} value={step.commerce.idempotencyKeyRef || ''} placeholder="secret:E2E_IDEMPOTENCY_KEY" onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, idempotencyKeyRef: event.target.value || undefined } })} /></label>
                  <label className="wide">清理／恢复动作<input aria-label={`步骤 ${index + 1} 清理动作`} value={step.commerce.cleanupAction || ''} placeholder="描述清理或恢复动作" onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, cleanupAction: event.target.value || undefined } })} /></label>
                  <label className="checkline"><input type="checkbox" aria-label={`步骤 ${index + 1} E2E 资源归属`} checked={step.commerce.e2eOwned} onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, e2eOwned: event.target.checked } })} />仅操作 E2E 所属资源</label>
                  <label>资源台账<select aria-label={`步骤 ${index + 1} 资源台账`} value={step.commerce.ledgerOperation} onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, ledgerOperation: event.target.value as NonNullable<PlanStep['commerce']>['ledgerOperation'] } })}><option value="none">不登记</option><option value="register">登记待清理资源</option><option value="cleanup">标记资源已清理</option></select></label>
                  <label className="checkline wide"><input type="checkbox" aria-label={`步骤 ${index + 1} 启用后台状态探针`} checked={Boolean(step.commerce.stateProbe)} onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, stateProbe: event.target.checked ? { domain: 'order', url: '/api/e2e/runs/${RUN_ID}/state', jsonPath: 'state', expectedState: 'completed', timeoutMs: 15000, intervalMs: 500 } : undefined } })} />用只读 API 验证后台最终一致</label>
                  {step.commerce.stateProbe && <>
                    <label>状态域<select aria-label={`步骤 ${index + 1} 状态域`} value={step.commerce.stateProbe.domain} onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, stateProbe: { ...step.commerce!.stateProbe!, domain: event.target.value as NonNullable<NonNullable<PlanStep['commerce']>['stateProbe']>['domain'] } } })}><option value="order">订单</option><option value="inventory">库存</option><option value="payment">支付</option><option value="refund">退款</option></select></label>
                    <label>期望状态<input aria-label={`步骤 ${index + 1} 期望后台状态`} value={step.commerce.stateProbe.expectedState} onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, stateProbe: { ...step.commerce!.stateProbe!, expectedState: event.target.value } } })} /></label>
                    <label className="wide">只读探针 URL<input aria-label={`步骤 ${index + 1} 状态探针 URL`} value={step.commerce.stateProbe.url} onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, stateProbe: { ...step.commerce!.stateProbe!, url: event.target.value } } })} /></label>
                    <label>JSON 状态路径<input aria-label={`步骤 ${index + 1} 状态 JSON 路径`} value={step.commerce.stateProbe.jsonPath} onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, stateProbe: { ...step.commerce!.stateProbe!, jsonPath: event.target.value } } })} /></label>
                    <label>轮询间隔（毫秒）<input type="number" min="100" max="10000" aria-label={`步骤 ${index + 1} 状态轮询间隔`} value={step.commerce.stateProbe.intervalMs} onChange={(event) => updateStep(index, { ...step, commerce: { ...step.commerce!, stateProbe: { ...step.commerce!.stateProbe!, intervalMs: Number(event.target.value) } } })} /></label>
                  </>}
                  <label className="checkline wide"><input type="checkbox" aria-label={`步骤 ${index + 1} 启用电商行作用域`} checked={Boolean(step.commerceScope)} onChange={(event) => updateStep(index, event.target.checked ? { ...step, commerceScope: { kind: 'product_card', container: { css: '[data-commerce-item]' }, anchor: { text: '' }, excludedMarkers: [], maxScrollAttempts: 4 } } : { ...step, commerceScope: undefined })} />限定到唯一商品／业务行</label>
                  {step.commerceScope && <>
                    <label>作用域类型<select aria-label={`步骤 ${index + 1} 作用域类型`} value={step.commerceScope.kind} onChange={(event) => updateStep(index, { ...step, commerceScope: { ...step.commerceScope!, kind: event.target.value as NonNullable<PlanStep['commerceScope']>['kind'] } })}><option value="product_card">商品卡片</option><option value="cart_line">购物车行</option><option value="order_row">订单行</option><option value="after_sale_row">售后行</option></select></label>
                    <label>滚动重试<input type="number" min="0" max="12" aria-label={`步骤 ${index + 1} 作用域滚动重试`} value={step.commerceScope.maxScrollAttempts} onChange={(event) => updateStep(index, { ...step, commerceScope: { ...step.commerceScope!, maxScrollAttempts: Number(event.target.value) } })} /></label>
                    <label className="wide">容器 CSS<input aria-label={`步骤 ${index + 1} 作用域容器`} value={step.commerceScope.container.css || ''} placeholder="例如 [data-commerce-item]" onChange={(event) => updateStep(index, { ...step, commerceScope: { ...step.commerceScope!, container: { css: event.target.value } } })} /></label>
                    <label className="wide">业务锚点文本<input aria-label={`步骤 ${index + 1} 作用域锚点`} value={step.commerceScope.anchor.text || ''} placeholder="商品名或脱敏业务标识" onChange={(event) => updateStep(index, { ...step, commerceScope: { ...step.commerceScope!, anchor: { text: event.target.value } } })} /></label>
                    <label className="wide">广告／推荐标记 CSS（每行一项）<textarea aria-label={`步骤 ${index + 1} 作用域排除标记`} value={step.commerceScope.excludedMarkers.map((item) => item.css || '').filter(Boolean).join('\n')} onChange={(event) => updateStep(index, { ...step, commerceScope: { ...step.commerceScope!, excludedMarkers: splitEntries(event.target.value).map((css) => ({ css })) } })} /></label>
                  </>}
                </div>}
              </details>
            </div>
          );
        })}
      </div>
      <h3>收尾断言</h3>
      {plan.assertions.length ? plan.assertions.map((assertion, index) => {
        const [strategy, locatorValue] = locatorEntry(assertion.locator);
        return <div className="assertion-editor" key={`${index}-${assertion.type}`}><strong>A{index + 1}</strong><select value={assertion.type} onChange={(event) => updateAssertion(index, { ...assertion, type: event.target.value as PlanAssertion['type'] })}><option value="visible">元素可见</option><option value="not_visible">元素不可见</option><option value="text_contains">文本包含</option><option value="url_contains">URL 包含</option><option value="page_reached">页面到达</option><option value="value_equals">值相等</option><option value="count_equals">数量相等</option></select><input value={locatorValue || assertion.expected || ''} aria-label={`断言 ${index + 1} 值`} onChange={(event) => updateAssertion(index, assertion.locator ? { ...assertion, locator: { [strategy]: event.target.value } } : { ...assertion, expected: event.target.value })} /></div>;
      }) : <p className="muted">没有可验证断言。建议至少增加一个可见文本或 URL 断言。</p>}
    </div>
  );
}

function RunTable({ runs, onReport, selectedRunIds, onToggleSelection }: { runs: TestRun[]; onReport: (runId?: string) => void; selectedRunIds?: Set<string>; onToggleSelection?: (runId: string) => void }) {
  if (!runs.length) return <div className="empty compact">尚无真实运行记录。Mock 样例已移除。</div>;
  const selectable = Boolean(selectedRunIds && onToggleSelection);
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{selectable && <th className="selection-column">选择</th>}<th>运行编号</th><th>用例名称</th><th>执行时间</th><th>执行角色</th><th>执行来源</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{runs.map((item) => <tr className={selectedRunIds?.has(item.id) ? 'selected-row' : ''} key={item.id}>{selectable && <td className="selection-column"><input type="checkbox" aria-label={`选择报告 ${item.id}`} checked={selectedRunIds?.has(item.id) || false} onChange={() => onToggleSelection?.(item.id)} /></td>}<td>{item.id}</td><td>{item.caseName}</td><td>{item.startedAt}</td><td>{item.role}</td><td>{resultClassificationText(item.resultClassification)}</td><td><StatusBadge status={item.status} /></td><td><button onClick={() => onReport(item.id)}>详情</button></td></tr>)}</tbody>
      </table>
    </div>
  );
}

function RunSteps({ run }: { run: TestRun | null }) {
  if (!run) return <div className="empty">审核计划并启动真实浏览器测试后显示步骤状态。</div>;
  return (
    <div className="step-list">
      {run.steps.map((step) => (
        <article className="step-card" key={step.id}>
          <div className="step-copy">
            <div className="step-heading">
              <b>#{step.order}</b>
              <div><strong>{step.action}</strong><span>{step.target}</span><span className="step-classification">{step.executionMode} · 稳定性 {step.stabilityLevel} · {step.stabilityReason}</span>{step.stabilityEvidence?.checked === true && <span className="step-classification">动作前检查：{step.stabilityEvidence.mode === 'app_bridge' ? '场景已就绪' : '可见／可用／稳定／未遮挡'}</span>}</div>
            </div>
            {step.plannerReason && <p className="step-target">决策：{step.plannerReason} · {step.progressAssessment === 'progress' ? '有进展' : step.progressAssessment === 'no_progress' ? '无进展' : '待判断'}</p>}
            {step.errorType && <p className="step-error">{step.errorType}</p>}
            {step.canvasEvidence && <details className="observation-facts">
              <summary>Canvas／Bridge 证据</summary>
              <dl>
                <div><dt>采集状态</dt><dd>{String(step.canvasEvidence.collectionStatus || 'unknown')}</dd></div>
                <div><dt>语义目标</dt><dd>{String(step.canvasEvidence.semanticTarget || '未声明')}</dd></div>
                <div><dt>坐标来源</dt><dd>{String(step.canvasEvidence.coordinateSource || '无坐标')}</dd></div>
                <div><dt>Bridge</dt><dd>{step.canvasEvidence.bridgeAvailable === true ? '已连接' : '未使用'}</dd></div>
                {step.canvasEvidence.selectedTargetAfter != null && <div><dt>动作后选中目标</dt><dd>{String(step.canvasEvidence.selectedTargetAfter)}</dd></div>}
                {step.canvasEvidence.semanticStateVerified != null && <div><dt>语义验证</dt><dd>{step.canvasEvidence.semanticStateVerified === true ? '通过' : '未通过'}</dd></div>}
              </dl>
            </details>}
            {step.browserContextEvidence && <details className="observation-facts">
              <summary>窗口／iframe 证据</summary>
              <dl><div><dt>窗口</dt><dd>{String(step.browserContextEvidence.pageSelection || 'current')} · 共 {String(step.browserContextEvidence.pageCount || 1)} 个页面</dd></div><div><dt>iframe</dt><dd>{step.browserContextEvidence.frame ? '唯一匹配' : '顶层页面'}</dd></div><div><dt>人工接管</dt><dd>{step.browserContextEvidence.humanTakeover === true ? '已完成并验证恢复条件' : '未使用'}</dd></div></dl>
            </details>}
            {step.commerceStateEvidence && <details className="observation-facts">
              <summary>后台状态一致性 · {step.commerceStateEvidence.consistent ? '通过' : '未通过'}</summary>
              <dl><div><dt>状态域</dt><dd>{step.commerceStateEvidence.domain}</dd></div><div><dt>期望／最终</dt><dd>{step.commerceStateEvidence.expectedState} / {step.commerceStateEvidence.finalState}</dd></div><div><dt>状态轨迹</dt><dd>{step.commerceStateEvidence.observations.map((item) => item.state).join(' → ')}</dd></div></dl>
            </details>}
            {step.fileEvidence && <details className="observation-facts"><summary>文件 SHA-256 证据</summary><dl><div><dt>方向</dt><dd>{step.fileEvidence.direction === 'upload' ? '上传' : '下载'}</dd></div><div><dt>大小</dt><dd>{step.fileEvidence.bytes} B</dd></div><div><dt>SHA-256</dt><dd><code>{step.fileEvidence.sha256}</code></dd></div></dl></details>}
            {step.recoveryEvidence && <details className="observation-facts">
              <summary>故障安全恢复 · {step.recoveryEvidence.decision}</summary>
              <dl><div><dt>动作性质</dt><dd>{step.recoveryEvidence.sideEffect ? '副作用动作' : '只读动作'}</dd></div><div><dt>尝试／重试</dt><dd>{step.recoveryEvidence.attempts.length} 次 / {step.recoveryEvidence.retried ? '已重试' : '未重试'}</dd></div><div><dt>结果</dt><dd>{step.recoveryEvidence.outcome}</dd></div><div><dt>未重放原因</dt><dd>{step.recoveryEvidence.noReplayReason || '-'}</dd></div>{step.recoveryEvidence.idempotencyKeySha256 && <div><dt>幂等键哈希</dt><dd><code>{step.recoveryEvidence.idempotencyKeySha256}</code></dd></div>}</dl>
            </details>}
            {step.after && <details className="observation-facts">
              <summary>观察事实</summary>
              <dl>
                <div><dt>页面</dt><dd>{step.after.title || '无标题'} · {step.after.url}</dd></div>
                <div><dt>DOM / Accessibility</dt><dd>{step.after.domSummary.length} 个摘要节点 · {step.after.accessibilitySummary ? '已采集' : '未采集'}</dd></div>
                <div><dt>运行时异常</dt><dd>{step.after.consoleErrors.length + step.after.pageErrors.length + step.after.failedRequests.length} 条</dd></div>
                <div><dt>页面诊断</dt><dd>{step.after.pageIssues.length} 条待复核信号</dd></div>
              </dl>
              {[...step.after.consoleErrors, ...step.after.pageErrors, ...step.after.failedRequests].length > 0 &&
                <ul>{[...step.after.consoleErrors, ...step.after.pageErrors, ...step.after.failedRequests].map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>}
              {step.after.pageIssues.length > 0 && <ul>{step.after.pageIssues.map((item, index) => <li key={`${item.kind}-${index}`}>{item.message}{item.target ? `：${item.target}` : ''}</li>)}</ul>}
            </details>}
          </div>
          <div className="step-meta">
            <StatusBadge status={step.result} />
            <span className="step-duration">{step.durationMs}ms</span>
            {step.before?.screenshot && <a className="evidence-link" href={step.before.screenshot} target="_blank" rel="noreferrer">动作前</a>}
            {step.evidence ? <a className="evidence-link" href={step.evidence} target="_blank" rel="noreferrer">查看截图</a> : <span className="evidence-missing">未采集</span>}
          </div>
        </article>
      ))}
    </div>
  );
}

function CommerceRunSummary({ run }: { run: TestRun }) {
  const summary = run.commerceSummary;
  if (!summary) return null;
  const gate = summary.releaseGate;
  return <section className={`commerce-run-summary ${summary.zeroResidual ? 'commerce-clean' : 'commerce-pending'}`} aria-label="电商安全与资源清理">
    <div className="commerce-summary-heading"><div><ShieldCheck size={20} /><strong>电商安全与资源清理</strong></div><span>{summary.zeroResidual ? '零残留通过' : '需要人工处置'}</span></div>
    <dl><div><dt>运行环境</dt><dd>{summary.environment === 'production_readonly' ? '正式站只读' : '隔离交易'}</dd></div><div><dt>策略评估</dt><dd>{summary.policyEvaluations.length} 次</dd></div><div><dt>资源台账</dt><dd>{summary.ledgerEntries.length} 项</dd></div><div><dt>待清理</dt><dd>{summary.pendingResources.length} 项</dd></div>{gate && <><div><dt>发布门禁</dt><dd>{gate.passed ? '通过' : '未通过'}</dd></div><div><dt>证据完整率</dt><dd>{(gate.checks.evidenceCompleteness.ratio * 100).toFixed(2)}%</dd></div><div><dt>隐私泄漏</dt><dd>{gate.checks.privacyLeakage.count} 项</dd></div><div><dt>重复／结果不明副作用</dt><dd>{gate.checks.duplicateSideEffects.duplicateResourceReferences} / {gate.checks.duplicateSideEffects.unknownSideEffectOutcomes}</dd></div></>}</dl>
    {summary.pendingResources.length > 0 && <div className="commerce-pending-list">{summary.pendingResources.map((item) => <div key={`${item.reference.sha256}-${item.status}`}><code>{item.reference.kind} · {item.reference.sha256.slice(0, 12)}...</code><span>{item.status} · {item.cleanupAction}</span></div>)}</div>}
  </section>;
}

function AgentDecisionList({ run }: { run: TestRun }) {
  if (!run.modelCallRecords.length) return null;
  return <div className="agent-decisions" aria-label="Agent 决策记录">
    {run.modelCallRecords.map((item) => <div key={item.index}>
      <strong>#{item.index} {item.decision}</strong>
      <span>{item.model} · {item.inputTokens + item.outputTokens} Token · {item.elapsedMs}ms</span>
      <p>{item.reason}</p>
    </div>)}
  </div>;
}

function VisualFallbackTimeline({ run }: { run: TestRun }) {
  const visualSteps = run.steps.filter((step) => step.computerUseTriggered);
  if (!visualSteps.length) return null;
  return <section className="visual-timeline" aria-label="视觉 fallback 时间线">
    <h2>视觉 fallback 时间线</h2>
    {visualSteps.map((step) => <article key={step.id}>
      <div><strong>步骤 #{step.order} · {step.action}</strong><span>{step.result === 'passed' ? '已验证' : '未通过'}</span></div>
      <p>{step.computerUseReason || '结构化信息不足，触发视觉定位。'}</p>
      <dl><div><dt>坐标来源</dt><dd>{step.coordinateSource || '未记录'}</dd></div><div><dt>执行模式</dt><dd>{step.executionMode}</dd></div></dl>
      <div className="report-actions">{step.before?.screenshot && <a className="evidence-link" href={step.before.screenshot} target="_blank" rel="noreferrer">动作前截图</a>}{step.evidence && <a className="evidence-link" href={step.evidence} target="_blank" rel="noreferrer">动作后截图</a>}</div>
    </article>)}
  </section>;
}

function FindingReviewCard({ finding, onReview }: {
  finding: Finding;
  onReview: (id: string, payload: { status: 'pending_review' | 'confirmed' | 'rejected'; title: string; severity: Finding['severity']; expectedResult: string }) => void;
}) {
  const [title, setTitle] = useState(finding.title);
  const [severity, setSeverity] = useState<Finding['severity']>(finding.severity);
  const [expectedResult, setExpectedResult] = useState(finding.expectedResult);
  const submit = (status: 'pending_review' | 'confirmed' | 'rejected') => onReview(finding.id, { status, title: title.trim(), severity, expectedResult: expectedResult.trim() });
  return <article className="finding">
    <div className="finding-review-grid">
      <label>问题标题<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} /></label>
      <label>严重程度<select value={severity} onChange={(event) => setSeverity(event.target.value as Finding['severity'])}><option>Blocker</option><option>High</option><option>Medium</option><option>Low</option></select></label>
      <label className="wide">预期结果<textarea value={expectedResult} maxLength={2000} onChange={(event) => setExpectedResult(event.target.value)} /></label>
    </div>
    <div className="finding-heading"><strong>{finding.category}</strong><span>{finding.confidence} · {finding.reviewStatus}</span></div>
    <dl><div><dt>实际结果</dt><dd>{finding.actualResult}</dd></div></dl>
    <h3>观察事实</h3><ul>{finding.facts.filter(Boolean).map((fact) => <li key={fact}>{fact}</li>)}</ul>
    <h3>AI 推断</h3><p>{finding.inference}</p>
    {finding.evidence.map((url, index) => <a className="evidence-link" href={url} target="_blank" rel="noreferrer" key={url}>证据 {index + 1}</a>)}
    {finding.evidenceTimeline.length > 0 && <details className="review-history"><summary>证据时间线（{finding.evidenceTimeline.length}）</summary><ol>{finding.evidenceTimeline.map((event, index) => <li key={`${event.timestamp}-${index}`}><strong>{event.phase === 'before_action' ? '动作前' : '动作后'}</strong> · {new Date(event.timestamp).toLocaleString('zh-CN', { hour12: false })}{event.screenshot && <> · <a className="evidence-link" href={event.screenshot} target="_blank" rel="noreferrer">截图</a></>}<ul>{event.facts.map((fact) => <li key={fact}>{fact}</li>)}</ul></li>)}</ol></details>}
    <div className="finding-actions">
      <button className="primary" onClick={() => submit('confirmed')} disabled={!title.trim()}>保存并确认</button>
      <button onClick={() => submit('pending_review')} disabled={!title.trim()}>保存为待审核</button>
      <button className="secondary" onClick={() => submit('rejected')} disabled={!title.trim()}>保存并驳回</button>
    </div>
    {finding.reviewHistory.length > 0 && <details className="review-history"><summary>问题修改记录（{finding.reviewHistory.length}）</summary><ol>{finding.reviewHistory.map((item, index) => <li key={`${item.timestamp}-${index}`}>{new Date(item.timestamp).toLocaleString('zh-CN', { hour12: false })} · {item.changedFields.join('、') || '状态复核'}</li>)}</ol></details>}
  </article>;
}

const locatorReviewActions = new Set<ActionType>(['click', 'fill', 'select', 'wait_for', 'clear', 'check', 'uncheck', 'hover', 'visual_click']);

function PathReviewEditor({ review, onSave }: { review: RunPathReview; onSave: (steps: ReviewedStep[]) => void }) {
  const [steps, setSteps] = useState(review.steps);
  if (!review.available) return <div className="empty compact">{review.reason || '该历史运行没有可审核的原始计划。'}</div>;
  const update = (sourceIndex: number, updater: (item: ReviewedStep) => ReviewedStep) => setSteps((current) => current.map((item) => item.sourceIndex === sourceIndex ? updater(item) : item));
  const retainedCount = steps.filter((item) => item.retained).length;
  return <section className="path-review" aria-label="回归路径审核">
    <div className="panel-title"><h2>回归路径审核</h2><span className="selection-count">保留 {retainedCount} / {steps.length}</span></div>
    <div className="review-step-list">{steps.map((item) => {
      const step = item.step;
      const [strategy, locatorValue] = locatorEntry(step.locator);
      return <article className={`review-step ${item.retained ? '' : 'review-step-removed'}`} key={item.sourceIndex}>
        <div className="review-step-heading">
          <label className="checkline"><input type="checkbox" checked={item.retained} onChange={(event) => update(item.sourceIndex, (current) => ({ ...current, retained: event.target.checked }))} />保留步骤 #{item.sourceIndex}</label>
          <span>{actionLabels[step.action]} · {step.execution_mode || 'locator'} · {step.stability_level || 'A'}</span>
        </div>
        <div className="review-step-fields">
          <label className="wide">步骤说明<input disabled={!item.retained} value={step.description || ''} onChange={(event) => update(item.sourceIndex, (current) => ({ ...current, step: { ...current.step, description: event.target.value } }))} /></label>
          {step.action === 'navigate' && <label className="wide">目标路径<input disabled={!item.retained} value={step.target || ''} onChange={(event) => update(item.sourceIndex, (current) => ({ ...current, step: { ...current.step, target: event.target.value } }))} /></label>}
          {locatorReviewActions.has(step.action) && <>
            <label>定位方式<select disabled={!item.retained} value={strategy} onChange={(event) => update(item.sourceIndex, (current) => ({ ...current, step: { ...current.step, locator: { [event.target.value]: locatorValue } } }))}><option value="label">表单标签</option><option value="test_id">测试 ID</option><option value="role">ARIA 角色</option><option value="text">可见文本</option><option value="css">CSS</option></select></label>
            <label>定位值<input disabled={!item.retained} value={locatorValue} onChange={(event) => update(item.sourceIndex, (current) => ({ ...current, step: { ...current.step, locator: { [strategy]: event.target.value, ...(strategy === 'role' && current.step.locator?.name ? { name: current.step.locator.name } : {}) } } }))} /></label>
            {strategy === 'role' && <label className="wide">可访问名称<input disabled={!item.retained} value={step.locator?.name || ''} onChange={(event) => update(item.sourceIndex, (current) => ({ ...current, step: { ...current.step, locator: { role: current.step.locator?.role || 'button', name: event.target.value || undefined } } }))} /></label>}
          </>}
          {(step.action === 'fill' || step.action === 'select') && <>
            <label>输入值<input disabled={!item.retained || Boolean(step.value_from_secret)} value={step.value || ''} onChange={(event) => update(item.sourceIndex, (current) => ({ ...current, step: { ...current.step, value: event.target.value, value_from_secret: undefined } }))} /></label>
            <label>密钥引用<input disabled={!item.retained} value={step.value_from_secret || ''} placeholder="例如 TEST_PASSWORD" onChange={(event) => update(item.sourceIndex, (current) => ({ ...current, step: { ...current.step, value_from_secret: event.target.value || undefined, value: event.target.value ? undefined : current.step.value } }))} /></label>
          </>}
        </div>
      </article>;
    })}</div>
    <div className="report-actions"><button className="primary" onClick={() => onSave(steps)} disabled={retainedCount === 0}>保存路径并重新编译</button></div>
    {review.history.length > 0 && <details className="review-history"><summary>路径修改记录（{review.history.length}）</summary><ol>{review.history.map((item, index) => <li key={`${item.timestamp}-${index}`}>{new Date(item.timestamp).toLocaleString('zh-CN', { hour12: false })} · {item.changes.map((change) => `#${change.sourceIndex} ${change.action}`).join('，')}</li>)}</ol></details>}
  </section>;
}

function GeneratedTestEditor({ generated, onSave, onReplay }: {
  generated: GeneratedTest;
  onSave: (source: string) => void;
  onReplay: (mode: 'stable' | 'adaptive') => void;
}) {
  const [mode, setMode] = useState<'preview' | 'edit'>('preview');
  const [source, setSource] = useState(generated.source);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const dirty = source !== generated.source;
  const copySource = async () => {
    try {
      await navigator.clipboard.writeText(source);
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
  };
  return <section className="generated-editor" aria-label="Playwright 测试源码">
    <div className="generated-meta"><span>稳定性 {generated.stabilityLevel}</span><span>修订 {generated.sourceRevision}</span><span>{generated.ciRecommendation}</span></div>
    {generated.manualSteps.length > 0 && <div className="manual-steps" role="alert"><strong>D 级人工步骤</strong><ul>{generated.manualSteps.map((item) => <li key={item}>{item}</li>)}</ul></div>}
    <div className="generated-toolbar">
      <div className="source-mode" role="group" aria-label="源码模式"><button className={mode === 'preview' ? 'active' : ''} onClick={() => setMode('preview')}>预览</button><button className={mode === 'edit' ? 'active' : ''} onClick={() => setMode('edit')}>编辑</button></div>
      <button onClick={copySource}><Copy size={17} />{copyState === 'copied' ? '已复制' : copyState === 'failed' ? '复制失败' : '复制代码'}</button>
      <button className="primary" onClick={() => onSave(source)} disabled={!dirty}><Save size={17} />保存修订</button>
      <a className="evidence-link" href={generated.sourcePath}>下载 .spec.ts</a>
    </div>
    {mode === 'edit'
      ? <textarea className="code-editor" aria-label="Playwright TypeScript 源码" value={source} onChange={(event) => setSource(event.target.value)} spellCheck={false} />
      : <pre className="code-preview"><code>{source}</code></pre>}
    {generated.sourceReviewHistory.length > 0 && <details className="review-history"><summary>源码修订记录（{generated.sourceReviewHistory.length}）</summary><ol>{generated.sourceReviewHistory.map((item) => <li key={`${item.revision}-${item.timestamp}`}>修订 {item.revision} · {item.action === 'manual_source_edit' ? '人工编辑' : '路径重新编译'} · {new Date(item.timestamp).toLocaleString('zh-CN', { hour12: false })}</li>)}</ol></details>}
    <div className="report-actions">{generated.supportedReplayModes.map((replayMode) => <button key={replayMode} onClick={() => onReplay(replayMode)}>{replayMode === 'stable' ? '稳定回放' : '显式自适应回放'}</button>)}</div>
  </section>;
}

function ReportView({ report, onReview, onSavePath, onSaveSource, onReplay }: {
  report: Report | null;
  onReview: (id: string, payload: { status: 'pending_review' | 'confirmed' | 'rejected'; title: string; severity: Finding['severity']; expectedResult: string }) => void;
  onSavePath: (steps: ReviewedStep[]) => void;
  onSaveSource: (source: string) => void;
  onReplay: (mode: 'stable' | 'adaptive') => void;
}) {
  if (!report) return <div className="empty">尚无真实报告。请先完成一次浏览器测试。</div>;
  const goalText = { in_progress: '执行中', achieved: '已完成', not_achieved: '未完成', incomplete: '执行不完整' }[report.run.goalStatus];
  const reviewText = {
    pending_confirmation: `等待确认 ${report.run.reviewSummary.pending} 项`,
    issues_found: `已确认问题 ${report.run.reviewSummary.confirmed} 项`,
    all_rejected: `问题已全部驳回 ${report.run.reviewSummary.rejected} 项`,
    no_findings: '无待审核问题'
  }[report.run.reviewSummary.disposition];
  return (
    <div className="report-grid">
      <div className="panel min-width-zero">
        <div className="panel-title"><h2>{report.run.id}</h2><StatusBadge status={report.run.status} /></div>
        <section className={`goal-summary goal-${report.run.goalStatus}`} aria-label="场景目标完成度"><div><span>场景目标</span><strong>{report.run.scenarioGoal}</strong></div><div><span>完成状态</span><strong>{goalText}</strong></div><p>{report.run.goalSummary}</p></section>
        <div className="run-classification"><span>执行来源 {resultClassificationText(report.run.resultClassification)}</span><span>原始状态 {statusText[report.run.executionStatus]}</span><span>审核终态 {reviewText}</span><span>接入 {report.run.onboardingLevel || '未关联'}</span><span>环境 {report.run.environmentId || '项目默认'}</span><span>保留 {report.run.artifactRetentionDays} 天</span>{report.run.runnerIsolation && <span>{isolationText(report.run.runnerIsolation, true)}</span>}<span>模式 {report.run.replayMode}</span><span>稳定性 {report.run.stabilityLevel}</span><span>耗时 {report.run.durationMs}ms</span><span>模型调用 {report.run.modelCalls}</span></div>
        <CommerceRunSummary run={report.run} />
        <AgentDecisionList run={report.run} />
        <VisualFallbackTimeline run={report.run} />
        <RunSteps run={report.run} />
        <h2 className="section-heading">路径审核</h2>
        {report.pathReview && <PathReviewEditor key={`${report.run.id}-${report.pathReview.history.length}`} review={report.pathReview} onSave={onSavePath} />}
        <h2 className="section-heading">问题审核</h2>
        {report.run.findings.length ? report.run.findings.map((finding) => <FindingReviewCard finding={finding} onReview={onReview} key={`${finding.id}-${finding.reviewHistory.length}`} />) : <p className="muted">{report.run.status === 'passed' ? '没有待审核问题，场景按现有确定性检查通过。' : '该历史记录尚未包含结构化问题，请以运行状态、断言和原始证据为准。'}</p>}
        {report.run.generatedTest && <>
          <h2 className="section-heading">生成测试</h2>
          <GeneratedTestEditor key={`${report.run.id}-${report.run.generatedTest.sourceRevision}`} generated={report.run.generatedTest} onSave={onSaveSource} onReplay={onReplay} />
        </>}
      </div>
      <div className="panel report-summary">
        <h2>报告下载</h2>
        <div className="report-downloads"><a className="evidence-link" href={report.run.reportJsonPath} download><Download size={17} />完整 JSON 报告</a><a className="evidence-link" href={report.run.reportHtmlPath} download><FileText size={17} />HTML 执行证据</a></div>
        <h2>断言结果</h2>
        {report.assertions.length ? report.assertions.map((item) => <div className="assertion" key={item.name}><StatusBadge status={item.passed ? 'passed' : 'failed'} /><p>{item.name}：{item.message}</p>{item.evidence && <a className="evidence-link" href={item.evidence} target="_blank" rel="noreferrer">失败截图</a>}</div>) : <p className="muted">没有断言结果</p>}
        <h2>失败步骤</h2>
        <p>{report.failedStep ? `#${report.failedStep.order} ${report.failedStep.action}：${report.failedStep.errorType || '未通过'}` : '无失败步骤'}</p>
        <h2>复现步骤</h2>
        <ol>{report.reproduction.map((item) => <li key={item}>{item}</li>)}</ol>
        <h2>可能原因（启发式推断）</h2>
        {report.heuristicReasons.length ? <ul>{report.heuristicReasons.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="muted">本次运行没有原因提示。</p>}
      </div>
    </div>
  );
}
