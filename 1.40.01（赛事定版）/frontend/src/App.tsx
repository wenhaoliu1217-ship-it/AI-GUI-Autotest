import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ChevronDown, ChevronRight, ClipboardCheck, Copy, Download, Eye, EyeOff, FileText, History, KeyRound, Play, RefreshCw, Save, ScanSearch, ShieldAlert, ShieldCheck, SquarePen, StopCircle, Trash2 } from 'lucide-react';
import { api } from './services/api';
import type { AISettings, ActionType, CompatibilityReport, EnvironmentConfig, EnvironmentDraft, Finding, GeneratedTest, Locator, PlanAssertion, PlanStep, ProjectConfig, ProjectDraft, Report, ReviewedStep, RunPathReview, ScenarioConfig, ScenarioDraft, SessionMetadata, SessionRecording, TestCaseDraft, TestPlan, TestRun } from './services/types';

type Page = 'start' | 'compose' | 'settings' | 'overview' | 'projects' | 'ai' | 'new' | 'run' | 'history' | 'report';
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
  pending_review: '待审核',
  stopped: '已停止',
  skipped: '已跳过',
  pending_confirmation: '等待确认',
  waiting_for_clarification: '等待你处理（未结束）',
  issues_found: '发现问题',
  incomplete: '未完成',
  system_error: '系统错误',
  cancelled: '已取消'
} as const;

const activeRunStatuses = new Set(['queued', 'running', 'pending_confirmation', 'waiting_for_clarification']);

function displayTimestamp(value?: string) {
  if (!value) return '时间未记录';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? '时间未记录'
    : parsed.toLocaleString('zh-CN', { hour12: false });
}

const recordingStatusText: Record<SessionRecording['status'], string> = {
  starting: '正在启动',
  recording: '等待登录',
  saving: '正在保存',
  completed: '已完成',
  cancelled: '已取消',
  error: '发生错误'
};

const reviewDispositionText: Record<TestRun['reviewSummary']['disposition'], string> = {
  pending_confirmation: '审核：待确认',
  issues_found: '审核：已确认问题',
  all_rejected: '审核：已全部驳回',
  no_findings: '审核：无发现'
};

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
  role: '测试工程师',
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

type ModelProviderPreset = {
  id: string;
  label: string;
  protocol: AISettings['protocol'];
  baseUrl: string;
  models: { id: string; label: string }[];
};

const MODEL_PROVIDER_PRESETS: ModelProviderPreset[] = [
  {
    id: 'openai',
    label: 'ChatGPT / OpenAI',
    protocol: 'responses',
    baseUrl: 'https://api.openai.com/v1',
    models: [
      { id: 'gpt-5.6-sol', label: 'GPT-5.6 Sol · 高能力' },
      { id: 'gpt-5.6-terra', label: 'GPT-5.6 Terra · 均衡' },
      { id: 'gpt-5.6-luna', label: 'GPT-5.6 Luna · 快速经济' }
    ]
  },
  {
    id: 'kimi',
    label: 'Kimi',
    protocol: 'chat_completions',
    baseUrl: 'https://api.moonshot.cn/v1',
    models: [
      { id: 'kimi-k3', label: 'Kimi K3 · 旗舰推理' },
      { id: 'kimi-k2.6', label: 'Kimi K2.6 · 多模态 Agent' },
      { id: 'kimi-k2.5', label: 'Kimi K2.5 · 多模态' }
    ]
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    protocol: 'chat_completions',
    baseUrl: 'https://api.deepseek.com',
    models: [
      { id: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro · 高能力' },
      { id: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash · 快速' }
    ]
  }
];

function ModelPresetPicker({
  settings,
  onSelect
}: {
  settings: AISettings;
  onSelect: (preset: ModelProviderPreset, model: ModelProviderPreset['models'][number]) => void;
}) {
  const selectedProvider = MODEL_PROVIDER_PRESETS.find((provider) =>
    provider.models.some((model) => model.id === settings.model)
  );
  const [open, setOpen] = useState(false);
  const [activeProviderId, setActiveProviderId] = useState(selectedProvider?.id || MODEL_PROVIDER_PRESETS[0].id);
  const activeProvider = MODEL_PROVIDER_PRESETS.find((provider) => provider.id === activeProviderId) || MODEL_PROVIDER_PRESETS[0];

  return <div
    className="model-preset-picker"
    onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
    }}
    onKeyDown={(event) => {
      if (event.key === 'Escape') setOpen(false);
    }}
  >
    <button
      type="button"
      className="model-preset-trigger"
      aria-label="选择模型预设"
      aria-haspopup="menu"
      aria-expanded={open}
      onClick={() => {
        setActiveProviderId(selectedProvider?.id || activeProviderId);
        setOpen((value) => !value);
      }}
    >
      <span>{selectedProvider?.label || '选择预设'}</span>
      <ChevronDown size={16} />
    </button>
    {open && <div className="model-preset-menu" role="menu" aria-label="模型厂商和版本">
      <div className="model-provider-list">
        {MODEL_PROVIDER_PRESETS.map((provider) => <button
          type="button"
          role="menuitem"
          aria-haspopup="menu"
          className={provider.id === activeProvider.id ? 'active' : ''}
          key={provider.id}
          onMouseEnter={() => setActiveProviderId(provider.id)}
          onFocus={() => setActiveProviderId(provider.id)}
        >
          <span>{provider.label}</span>
          <ChevronRight size={15} />
        </button>)}
      </div>
      <div className="model-version-list" role="menu" aria-label={`${activeProvider.label} 模型版本`}>
        <strong>{activeProvider.label}</strong>
        {activeProvider.models.map((model) => <button
          type="button"
          role="menuitem"
          className={settings.model === model.id ? 'active' : ''}
          key={model.id}
          onClick={() => {
            onSelect(activeProvider, model);
            setOpen(false);
          }}
        >
          <span>{model.label}</span>
          <small>{model.id}</small>
        </button>)}
      </div>
      <p className="model-preset-note">选择后自动填写协议和URL，仍可手动修改。</p>
    </div>}
  </div>;
}

const initialProject: ProjectDraft = {
  name: '', baseUrl: '', allowedHosts: [], forbiddenActions: [], allowPrivateNetwork: false, onboardingLevel: 'L0',
  businessContext: { description: '', terminology: {}, objectTypes: [], stateModels: {}, exampleGoals: [], operatingBoundaries: [], allowedActions: [], bridgeCapabilities: [], bridgeSemanticTargets: {} },
  limits: { maxSteps: 50, timeoutSeconds: 600, maxModelCalls: 20 }
};

const initialScenario: ScenarioDraft = {
  name: '',
  preconditions: [],
  goal: '',
  testData: {},
  expectedResults: [],
  forbiddenActions: []
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

function LoginSurface({ runId, clarificationId }: { runId: string; clarificationId: string }) {
  const [frame, setFrame] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const queue = useRef(Promise.resolve());
  const active = useRef(true);
  const send = (command: Parameters<typeof api.loginControl>[2]) => {
    queue.current = queue.current.then(async () => {
      if (!active.current) return;
      try {
        const result = await api.loginControl(runId, clarificationId, command);
        if (!active.current) return;
        if (result.error) throw new Error(result.error);
        if (result.image) setFrame(`data:image/jpeg;base64,${result.image}`);
        if (result.notice) setNotice(result.notice);
        setError('');
      } catch (err) { if (active.current) setError(err instanceof Error ? err.message : '登录窗口连接失败'); }
    });
    return queue.current;
  };
  useEffect(() => {
    active.current = true;
    let timer: number;
    const poll = async () => {
      await send({ kind: 'frame' });
      if (active.current) timer = window.setTimeout(poll, 800);
    };
    void poll();
    return () => { active.current = false; window.clearTimeout(timer); };
  }, [runId, clarificationId]);
  return <div className="manual-login-surface" role="region" aria-label="登录交互窗口">
    <p>这是测试浏览器的实时交互画面，可点击输入框后直接键入或粘贴。输入只转发给当前网站，不发送给 AI，也不保存为操作记录。</p>
    {error && <p role="alert">{error}</p>}
    {notice && <p className="login-surface-notice" role="status">{notice}</p>}
    {frame ? <img src={frame} alt="可操作的登录页面" tabIndex={0} draggable={false}
      onClick={(event) => {
        event.currentTarget.focus();
        const box = event.currentTarget.getBoundingClientRect();
        void send({ kind: 'click', x: (event.clientX - box.left) / box.width, y: (event.clientY - box.top) / box.height });
      }}
      onKeyDown={(event) => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') return;
        event.preventDefault();
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') void send({ kind: 'key', key: 'Control+A' });
        else if (!event.ctrlKey && !event.metaKey && !event.altKey && event.key.length === 1) void send({ kind: 'text', text: event.key });
        else if (['Tab', 'Enter', 'Backspace', 'Delete', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) void send({ kind: 'key', key: event.shiftKey && event.key === 'Tab' ? 'Shift+Tab' : event.key });
      }}
      onPaste={(event) => { event.preventDefault(); const text = event.clipboardData.getData('text'); if (text) void send({ kind: 'text', text }); }}
      onWheel={(event) => { void send({ kind: 'scroll', dy: Math.max(-800, Math.min(800, event.deltaY)) }); }}
    /> : <p>正在连接隔离浏览器的登录页面…</p>}
  </div>;
}

function ClarificationPanel({ runId, pending, busy, externalLoginWindow, onAnswer, onStop }: {
  runId: string;
  pending: NonNullable<TestRun['pendingClarification']>;
  busy: boolean; externalLoginWindow: boolean; onAnswer: (answer: string) => void; onStop: () => void;
}) {
  const [answer, setAnswer] = useState('');
  const login = pending.question.startsWith('【需要手动登录】');
  const [loginWindowOpen, setLoginWindowOpen] = useState(login && !externalLoginWindow);
  useEffect(() => {
    setLoginWindowOpen(login && !externalLoginWindow);
  }, [login, externalLoginWindow, pending.id]);
  return <div className="confirmation-box" role="alert" aria-label={login ? '等待手动登录' : '等待补充信息'}>
    <div><KeyRound size={22} /><div><h3>{login ? '请完成登录，任务正在等待' : '任务暂停，等待你补充信息'}</h3><p>{pending.question}</p></div></div>
    <p>任务未结束。请勿在这里填写密码、验证码或 API Key；登录信息只在目标网站输入。</p>
    {login && externalLoginWindow && <p><strong>登录页面已在受控 Microsoft Edge 中打开。</strong>请直接在该窗口登录；如果窗口被遮挡，可在任务栏切换到 Edge，或打开下方备用登录窗口。</p>}
    {!login && <label>补充说明<textarea value={answer} onChange={(event) => setAnswer(event.target.value)} /></label>}
    {login && !loginWindowOpen && <div className="toolbar login-launch-toolbar">
      {externalLoginWindow && <button className="primary" disabled={busy} onClick={() => onAnswer('我已在受控 Microsoft Edge 中完成登录，请重新检查页面后继续。')}>我已登录，检查并继续</button>}
      <button onClick={() => setLoginWindowOpen(true)}>{externalLoginWindow ? '打开备用登录窗口' : '打开登录窗口'}</button>
      <button disabled={busy} onClick={onStop}>取消登录并停止</button>
    </div>}
    {login && loginWindowOpen && <div className="manual-login-modal" role="dialog" aria-modal="true" aria-label="登录操作窗口">
      <div className="manual-login-modal-card">
        <div className="manual-login-modal-header">
          <div><KeyRound size={22} /><div><h3>请在此窗口完成登录</h3><p>任务会保持暂停，登录完成前不会超时。</p></div></div>
          <button type="button" aria-label="暂时收起登录窗口" onClick={() => setLoginWindowOpen(false)}>暂时收起</button>
        </div>
        <LoginSurface runId={runId} clarificationId={pending.id} />
        <div className="toolbar manual-login-modal-actions">
          <button className="primary" disabled={busy} onClick={() => onAnswer('我已在测试浏览器完成登录，请重新检查页面后继续。')}>我已登录，检查并继续</button>
          <button disabled={busy} onClick={onStop}>取消登录并停止</button>
        </div>
      </div>
    </div>}
    {!login && <div className="toolbar">
      <button className="primary" disabled={busy || !answer.trim()} onClick={() => onAnswer(answer)}>提交说明并继续</button>
      <button disabled={busy} onClick={onStop}>停止执行</button>
    </div>}
  </div>;
}

function StatusBadge({ status }: { status: keyof typeof statusText }) {
  return <span className={`status status-${status}`}>{statusText[status] ?? '未知状态，请查看运行详情'}</span>;
}

function RecordingDiagnostics({ recording }: { recording: SessionRecording | null }) {
  const diagnostics = recording?.diagnostics;
  const issueCount = diagnostics
    ? diagnostics.failedRequests.length + diagnostics.policyRejections.length + diagnostics.httpErrors.length + diagnostics.consoleErrors + diagnostics.pageErrors
    : 0;
  const issueEvents = diagnostics
    ? [...diagnostics.failedRequests, ...diagnostics.policyRejections, ...diagnostics.httpErrors].slice(-5)
    : [];
  return <section className={`recording-diagnostics${issueCount ? ' recording-diagnostics-warning' : ''}`} aria-label="登录录制诊断" aria-live="polite">
    <div className="recording-diagnostics-heading">
      <strong>登录窗口诊断</strong>
      <span>{recording ? recordingStatusText[recording.status] : '正在读取'}</span>
    </div>
    {!diagnostics ? <p>正在读取受控浏览器状态…</p> : <>
      <dl>
        <div><dt>当前页面</dt><dd><code>{diagnostics.lastUrl || '尚未完成导航'}</code></dd></div>
        <div><dt>网络响应</dt><dd>{diagnostics.recentResponses.length} 个已完成 · {diagnostics.pendingRequests.length} 个待完成</dd></div>
        <div><dt>页面恢复</dt><dd>{diagnostics.reloadCount ? `已重新加载 ${diagnostics.reloadCount} 次` : '尚未重新加载'}</dd></div>
        <div><dt>异常摘要</dt><dd>{issueCount ? `${issueCount} 条，需要检查` : '未发现网络、策略或页面错误'}</dd></div>
      </dl>
      {diagnostics.pendingRequests.length > 0 && <div className="recording-diagnostic-events">
        <strong>待完成请求</strong>
        <ul>{diagnostics.pendingRequests.slice(-5).map((event, index) => <li key={`${event.method}-${event.url}-${index}`}><code>{event.method} {event.url}</code>{event.ageSeconds != null ? ` · ${event.ageSeconds} 秒` : ''}</li>)}</ul>
      </div>}
      {issueEvents.length > 0 && <div className="recording-diagnostic-events recording-diagnostic-errors">
        <strong>最近异常</strong>
        <ul>{issueEvents.map((event, index) => <li key={`${event.method}-${event.url}-${index}`}><code>{event.method} {event.url}</code>{event.status != null ? ` · HTTP ${event.status}` : event.failure ? ` · ${event.failure}` : ''}</li>)}</ul>
      </div>}
      <p className="recording-privacy">诊断仅显示脱敏网址和计数，不采集请求内容、Cookie、邮箱或密码。</p>
    </>}
  </section>;
}

export default function App() {
  const [page, setPage] = useState<Page>('new');
  const [startUrl, setStartUrl] = useState('');
  const [draft, setDraft] = useState(initialDraft);
  const [plan, setPlan] = useState<TestPlan | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [run, setRun] = useState<TestRun | null>(null);
  const [history, setHistory] = useState<TestRun[]>([]);
  const [selectedRunIds, setSelectedRunIds] = useState<Set<string>>(new Set());
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState<Connection>('checking');
  const [health, setHealth] = useState<import('./services/types').HealthStatus | null>(null);
  const [message, setMessage] = useState('正在连接真实执行服务…');
  const [requestFailed, setRequestFailed] = useState(false);
  const [requestElapsed, setRequestElapsed] = useState(0);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [aiSettings, setAISettings] = useState<AISettings>(initialAISettings);
  const [aiConnection, setAIConnection] = useState<AIConnection>('untested');
  const [aiMessage, setAIMessage] = useState('尚未测试模型连接。配置只保留在当前页面内存中。');
  const [showKey, setShowKey] = useState(false);
  const [plannerMode, setPlannerMode] = useState<PlannerMode>('ai');
  const [executionStrategy, setExecutionStrategy] = useState<ExecutionStrategy>('agent');
  const [visualFallbackEnabled, setVisualFallbackEnabled] = useState(false);
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>('ask');
  const [projects, setProjects] = useState<ProjectConfig[]>([]);
  const [projectDraft, setProjectDraft] = useState<ProjectDraft>(initialProject);
  const [projectTerminology, setProjectTerminology] = useState('{}');
  const [projectStateModels, setProjectStateModels] = useState('{}');
  const [projectBridgeTargets, setProjectBridgeTargets] = useState('{}');
  const [selectedProject, setSelectedProject] = useState<ProjectConfig | null>(null);
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
  const [recordingSnapshot, setRecordingSnapshot] = useState<SessionRecording | null>(null);

  useEffect(() => {
    let active = true;
    api.health()
      .then(async (health) => {
        if (!active) return;
        setHealth(health);
        setConnection('connected');
        setMessage(`已连接 ${health.engine} · ${health.planner}`);
        const [runs, savedProjects] = await Promise.all([api.getHistory(), api.getProjects()]);
        if (active) {
          setHistory(runs); setProjects(savedProjects);
          const pendingRun = runs.find((item) => activeRunStatuses.has(item.status));
          if (pendingRun) { setRun(pendingRun); setPage('run'); setMessage('已恢复正在进行的任务；可在此继续处理或停止。'); }
        }
      })
      .catch((error: Error) => {
        if (!active) return;
        setConnection('disconnected');
        setMessage(error.message);
      });
    return () => { active = false; };
  }, []);

  // Docker Desktop may still be starting when the page first loads. Recheck a
  // degraded Runner without requiring the user to refresh the whole UI.
  useEffect(() => {
    if (connection !== 'connected' || health?.runnerAvailable !== false) return;
    let active = true;
    const timer = window.setInterval(async () => {
      try {
        const nextHealth = await api.health();
        if (active) {
          setHealth(nextHealth);
          if (nextHealth.runnerAvailable) setMessage('真实隔离 Runner 已就绪，可以开始测试。');
        }
      } catch { /* The primary connection effect owns disconnected state. */ }
    }, 4000);
    return () => { active = false; window.clearInterval(timer); };
  }, [connection, health?.runnerAvailable]);

  useEffect(() => {
    if (!run || !activeRunStatuses.has(run.status)) return;
    let active = true;
    const timer = window.setInterval(async () => {
      try {
        const nextRun = await api.getRunProgress(run);
        if (!active) return;
        setRun(nextRun);
        if (nextRun.status === 'waiting_for_clarification') {
          setMessage('任务已暂停，等待你处理下方提示；尚未结束，可继续或停止。');
        }
        if (!activeRunStatuses.has(nextRun.status)) {
          window.clearInterval(timer);
          setHistory(await api.getHistory());
          setMessage(`真实执行结束：${statusText[nextRun.status] ?? nextRun.status ?? '状态未知'}`);
        }
      } catch (error) {
        if (active) setMessage(error instanceof Error ? error.message : String(error));
      }
    }, 800);
    return () => { active = false; window.clearInterval(timer); };
  }, [run?.id, run?.status]);

  useEffect(() => {
    if (!recordingId || !selectedProject) return;
    let active = true;
    let timer: number | undefined;
    const refresh = async () => {
      try {
        const nextRecording = await api.getSessionRecording(selectedProject.id, recordingId);
        if (!active) return;
        setRecordingSnapshot(nextRecording);
        if (nextRecording.status === 'recording' || nextRecording.status === 'starting' || nextRecording.status === 'saving') {
          timer = window.setTimeout(refresh, 1000);
        }
      } catch (error) {
        if (active) setMessage(error instanceof Error ? error.message : String(error));
      }
    };
    void refresh();
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, [recordingId, selectedProject?.id]);

  const metrics = useMemo(() => {
    const total = history.length;
    const failed = history.filter((item) => item.status !== 'passed').length;
    const passRate = total ? Math.round(((total - failed) / total) * 100) : 0;
    return { totalCases: total, recentRuns: total, passRate, failed };
  }, [history]);

  const execute = async (task: () => Promise<void>) => {
    const started = Date.now();
    setRequestElapsed(0);
    setRequestFailed(false);
    const progressTimer = window.setInterval(() => setRequestElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    setBusy(true);
    setMessage(connection === 'connected' ? '正在处理真实请求…' : message);
    try {
      await task();
    } catch (error) {
      setRequestFailed(true);
      setMessage(error instanceof Error ? error.message : String(error));
      if (connection !== 'connected') setConnection('disconnected');
    } finally {
      window.clearInterval(progressTimer);
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
      ? await api.generateAIPlan(effectiveDraft, aiSettings, selectedProject?.id, selectedEnvironment?.id)
      : await api.generatePlan(effectiveDraft, selectedProject?.id, selectedEnvironment?.id);
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
    setAIMessage('配置已修改，请重新测试连接。');
  };

  const testAIConnection = () => execute(async () => {
    setAIConnection('testing');
    setAIMessage('正在向模型服务发送最小连接测试……');
    try {
      const result = await api.testAI(aiSettings);
      setAIConnection('connected');
      setPlannerMode('ai');
      setExecutionStrategy('agent');
      setAIMessage(`连接成功：${result.model}，响应 ${result.elapsedMs}ms。`);
      setMessage('AI 模型连接成功；已启用 AI 模型规划和逐步 Agent 探索。');
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
    if (!plan) return;
    if (health?.runnerAvailable === false) {
      throw new Error(`真实隔离 Runner 当前不可用（${health.runnerReason || '未报告原因'}）。请先修复 Docker、Runner 镜像或 Playwright 浏览器状态。`);
    }
    if (executionStrategy === 'agent' && aiConnection !== 'connected') {
      throw new Error('逐步 Agent 探索需要先连接 AI 模型。');
    }
    if (executionStrategy === 'agent' && !visualFallbackEnabled) {
      throw new Error('逐步 Agent 探索必须先授权 AI 接收当前目标站点的脱敏截图、DOM 和无障碍树。');
    }
    const nextRun = executionStrategy === 'agent'
      ? await api.startAgentRun(plan, draft, aiSettings, selectedProject?.id, selectedScenario?.id, selectedEnvironment?.id, visualFallbackEnabled, health?.runnerMode !== 'process')
      : await api.startRun(plan, selectedProject?.id, selectedScenario?.id, selectedEnvironment?.id, health?.runnerMode !== 'process');
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
    const nextRun = await api.decideConfirmation(run.id, run.pendingConfirmation.id, decision);
    setRun(nextRun);
    setMessage(decision === 'approved'
      ? '该动作已获单次批准，运行继续。'
      : '该动作已拒绝；本次不会执行，AI 将换一种方案继续测试。');
  });

  const answerClarification = (answer: string) => execute(async () => {
    if (!run?.pendingClarification || !answer.trim()) return;
    const nextRun = await api.answerClarification(run.id, run.pendingClarification.id, answer.trim());
    setRun(nextRun);
    setMessage('已提交，正在重新检查实际页面；确认后继续执行。');
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
      limits: project.limits, businessContext: project.businessContext
    });
    setProjectTerminology(JSON.stringify(project.businessContext.terminology, null, 2));
    setProjectStateModels(JSON.stringify(project.businessContext.stateModels, null, 2));
    setProjectBridgeTargets(JSON.stringify(project.businessContext.bridgeSemanticTargets, null, 2));
    setCompatibility(null);
    setSession(null);
    setSelectedScenario(null);
    setScenarioDraft(initialScenario);
    setScenarioTestData('{}');
    const [savedEnvironments, savedScenarios] = await Promise.all([api.getEnvironments(project.id), api.getScenarios(project.id)]);
    setEnvironments(savedEnvironments);
    setScenarios(savedScenarios);
    setSelectedEnvironment(null);
    setEnvironmentDraft(initialEnvironment);
    setEnvironmentVariables('{}');
    setEnvironmentSecretRefs('{}');
    try { setCompatibility(await api.getCompatibility(project.id)); } catch { /* 尚未扫描 */ }
    try { setSession(await api.getSession(project.id)); } catch { /* 尚未导入登录态 */ }
    setPage('projects');
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
      forbiddenActions: scenario.forbiddenActions
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

  const beginProjectTest = () => {
    if (!selectedProject) return;
    setDraft({
      ...initialDraft,
      name: '',
      targetUrl: selectedProject.baseUrl,
      flow: '',
      preconditions: '',
      expectation: '',
      testData: {},
      forbiddenActions: []
    });
    setSelectedScenario(null);
    setScenarioDraft(initialScenario);
    setScenarioTestData('{}');
    setPlan(null);
    setReviewed(false);
    setWarnings([]);
    setPage('new');
  };

  const beginFromStart = () => {
    const value = startUrl.trim();
    try {
      const parsed = new URL(value);
      if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error();
    } catch {
      setMessage('请输入完整的 http:// 或 https:// 网站地址。');
      return;
    }
    setDraft({
      ...initialDraft,
      name: '',
      targetUrl: value,
      flow: '',
      preconditions: '',
      expectation: '',
      testData: {},
      forbiddenActions: []
    });
    setPlan(null);
    setReviewed(false);
    setWarnings([]);
    setSelectedScenario(null);
    setPage('compose');
    setMessage('网站地址已载入。请在下一步告诉 AI 要验证的业务目标。');
  };

  const startSimpleTest = () => execute(async () => {
    const flow = draft.flow.trim();
    if (health?.runnerAvailable === false) throw new Error(`真实 Runner 当前不可用（${health.runnerReason || '未报告原因'}）。请先修复执行环境。`);
    if (!flow) throw new Error('请先告诉 AI 你想测试什么。');
    if (!draft.targetUrl.trim()) throw new Error('目标网站地址为空，请返回开始页重新输入。');
    if (aiConnection !== 'connected') throw new Error('自主探索必须先配置并连接 GUI 内置 AI；本地规则规划仅在传统测试编辑器中作为保底手段。');
    if (!visualFallbackEnabled) throw new Error('请先授权 AI 接收当前目标站点的脱敏截图、DOM 和无障碍树。');
    if (/\b(?:TBD|TODO)\b|待补充|待填写|\{\{[^}]+\}\}/i.test(JSON.stringify(draft))) throw new Error('场景仍包含待补充的关键数据，请明确填写后再启动。');
    const effectiveDraft = {
      ...draft,
      name: draft.name.trim() || flow.slice(0, 80),
      expectation: draft.expectation.trim() || '完成目标操作并保存页面、网络和运行结果证据。'
    };
    setDraft(effectiveDraft);
    // The backend already validates a target-only bootstrap contract. Business
    // actions must be planned against the live page, not guessed before launch.
    const nextRun = await api.startAgentRun(null, effectiveDraft, aiSettings, selectedProject?.id, selectedScenario?.id, selectedEnvironment?.id, visualFallbackEnabled, health?.runnerMode !== 'process');
    setPlan(null);
    setWarnings([]);
    setReviewed(false);
    setRun(nextRun);
    setHistory(await api.getHistory());
    setReport(null);
    setPage('run');
    setMessage(`${approvalMode === 'ask' ? '逐次确认写入动作' : approvalMode === 'delegate' ? '按安全策略自动处理低风险动作' : '在系统硬性安全边界内自动处理动作'}；运行已启动。`);
  });

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
    const payload = { ...scenarioDraft, testData };
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
    setRecordingSnapshot(recording);
    setMessage('交互登录浏览器已打开。完成登录后返回此页并点击“完成录制”。');
  });

  const completeLoginRecording = () => execute(async () => {
    if (!selectedProject || !recordingId) return;
    const recording = await api.completeSessionRecording(selectedProject.id, recordingId);
    if (recording.session) setSession(recording.session);
    setRecordingId(null);
    setRecordingSnapshot(null);
    setMessage('登录态已从受控浏览器采集并使用 Windows DPAPI 加密保存。');
  });

  const reloadLoginRecording = () => execute(async () => {
    if (!selectedProject || !recordingId) return;
    const recording = await api.reloadSessionRecording(selectedProject.id, recordingId);
    setRecordingSnapshot(recording);
    setMessage('已让受控浏览器重新加载当前页面；登录地址会保留。');
  });

  const cancelLoginRecording = () => execute(async () => {
    if (!selectedProject || !recordingId) return;
    await api.cancelSessionRecording(selectedProject.id, recordingId);
    setRecordingId(null);
    setRecordingSnapshot(null);
    setMessage('登录录制已取消，未保存会话。');
  });

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">京彩OPC<br /><span>AI 网站测试助手 · 1.32.01</span><span className="brand-compatibility-name">AI Agent 自动测试框架</span></div>
        <nav aria-label="主要导航">
          <button className={['start', 'compose', 'new', 'run'].includes(page) ? 'active' : ''} onClick={() => setPage('new')}><Play size={18} />开始测试</button>
          <button className={['history', 'report'].includes(page) ? 'active' : ''} onClick={openHistory}><History size={18} />测试记录</button>
          <button aria-label="高级设置导航" className={`nav-advanced ${['settings', 'projects', 'ai', 'overview', 'new'].includes(page) ? 'active' : ''}`} onClick={() => setPage('settings')}><KeyRound size={18} /></button>
        </nav>
        <nav className="compat-nav" aria-label="工程入口兼容导航">
          <button onClick={() => setPage('overview')}><Activity size={18} />总览</button>
          <button onClick={() => setPage('projects')}><ScanSearch size={18} />项目接入</button>
          <button onClick={() => setPage('ai')}><KeyRound size={18} />AI 模型设置</button>
          <button onClick={() => setPage('new')}><SquarePen size={18} />新建测试</button>
          <button onClick={() => setPage('run')}><Play size={18} />执行详情</button>
          <button onClick={openHistory}><History size={18} />历史报告</button>
          <button onClick={() => openReport()}><FileText size={18} />报告详情</button>
        </nav>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <p className="eyebrow">AI 网站测试助手</p>
            <h1>{pageTitle(page)}</h1>
          </div>
          <div className={`service-state service-${connection} ${health?.runnerAvailable === false ? 'service-runner-warning' : ''}`}>
            {connection === 'connected' && health?.runnerAvailable !== false ? <ShieldCheck size={18} /> : <ShieldAlert size={18} />}
            <span>{connection === 'checking' ? '正在检查执行服务' : connection !== 'connected' ? '执行服务未连接' : health?.runnerAvailable === false ? '网页服务已连接 · 真实 Runner 不可用' : '真实执行服务已连接'}</span>
          </div>
        </header>

        <div className={`notice notice-${requestFailed ? 'disconnected' : connection}`} role={requestFailed || connection === 'disconnected' ? 'alert' : 'status'}>{message}{busy && <span> · 已等待 {requestElapsed} 秒</span>}</div>
        {page === 'start' && (
          <section className="content beginner-start">
            <div className="start-hero">
              <div className="start-copy">
                <p className="eyebrow">AI 网站测试助手</p>
                <h2>输入网址，让 AI 帮你测试</h2>
                <p>AI 会先安全查看网站，再用普通中文告诉你它能检查什么。扫描阶段不会点击、填写或提交内容。</p>
              </div>
              <div className="url-entry-card">
                <label htmlFor="start-url">要测试的网站地址</label>
                <div className="url-entry-row">
                  <ScanSearch size={20} aria-hidden="true" />
                  <input
                    id="start-url"
                    value={startUrl}
                    onChange={(event) => setStartUrl(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') beginFromStart();
                    }}
                    placeholder="https://example.com"
                    spellCheck={false}
                  />
                  <button className="primary" onClick={beginFromStart} disabled={busy || connection !== 'connected'}><Play size={18} />开始</button>
                </div>
                <p className="safety-line"><ShieldCheck size={16} />仅测试你有权使用的网站；密码、验证码与付款始终由你本人完成。</p>
              </div>
            </div>
            <div className="start-status-summary" aria-label="运行摘要"><span>真实运行记录 <strong>{metrics.totalCases}</strong></span><span>测试通过率 <strong>{metrics.passRate}%</strong></span><span>失败 / 错误 <strong>{metrics.failed}</strong></span></div>
            {history.length === 0 && <p className="muted empty-history-note">尚无真实运行记录。Mock 样例已移除。</p>}
            {history.length > 0 && <section className="recent-simple">
              <div className="panel-title"><h3>最近测试</h3><button className="text-link" onClick={openHistory}>查看全部</button></div>
              <div className="recent-site-list">
                {history.slice(0, 3).map((item) => <button key={item.id} onClick={() => { setRun(item); setPage('run'); }}><span>{item.caseName}</span><small>{new Date(item.startedAt).toLocaleString('zh-CN', { hour12: false })}</small><StatusBadge status={item.status} /><span className="review-state">{reviewDispositionText[item.reviewSummary.disposition]}</span><Play size={17} /></button>)}
              </div>
            </section>}
          </section>
        )}
        {page === 'settings' && (
          <section className="content">
            <div className="settings-intro">
              <p className="eyebrow">普通测试不需要修改这里</p>
              <h2>高级设置</h2>
              <p>“项目、环境、场景”等技术信息仍由系统完整保存。只有需要定制时才进入下面的页面。</p>
            </div>
            <div className="settings-grid">
              <button onClick={() => setPage('ai')}><KeyRound size={22} /><span><strong>更换 AI 服务</strong><small>填写自己的 AI 服务地址、模型和密钥</small></span><Play size={18} /></button>
              <button onClick={() => setPage('projects')}><ScanSearch size={22} /><span><strong>网站与登录配置</strong><small>查看系统内部保存的网站、环境和登录状态</small></span><Play size={18} /></button>
              <button onClick={() => setPage('new')}><SquarePen size={22} /><span><strong>传统测试编辑器</strong><small>为专业人员保留固定计划与场景编辑</small></span><Play size={18} /></button>
              <button onClick={() => setPage('overview')}><Activity size={22} /><span><strong>运行总览</strong><small>查看技术统计和执行服务状态</small></span><Play size={18} /></button>
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
            <div className="advanced-toolbar wide-panel"><span>高级设置</span><button onClick={() => setPage('ai')}><KeyRound size={17} />AI 模型设置</button><button onClick={() => setPage('overview')}><Activity size={17} />运行总览</button></div>
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
              <div className="toolbar"><button className="primary" onClick={saveProject} disabled={busy || !projectDraft.name.trim() || !projectDraft.baseUrl.trim()}><ShieldCheck size={18} />{selectedProject ? '保存项目修改' : '保存项目配置'}</button>{selectedProject && <button onClick={() => {
                setSelectedProject(null); setProjectDraft(initialProject); setCompatibility(null); setSession(null);
                setProjectTerminology('{}'); setProjectStateModels('{}'); setProjectBridgeTargets('{}');
                setEnvironments([]); setSelectedEnvironment(null); setScenarios([]); setSelectedScenario(null);
              }}><SquarePen size={18} />新建项目</button>}</div>
              <p className="format-hint">L1 支持导入 storageState 或在受控浏览器中交互登录，两种方式均由 Windows 当前用户 DPAPI 加密保存。</p>
            </div>
            <div className="panel project-list-panel">
              <h2>已接入项目</h2>
              {projects.length ? <div className="project-list">{projects.map((project) => <button className={selectedProject?.id === project.id ? 'project-card active' : 'project-card'} key={project.id} onClick={() => chooseProject(project)}><strong>{project.name}</strong><span>{project.baseUrl}</span><small>{project.onboardingLevel} · {project.allowedHosts.length} 个允许域名 · {project.allowPrivateNetwork ? '受控私网已允许' : '仅公网'}</small></button>)}</div> : <div className="empty compact">尚无项目配置。</div>}
              {selectedProject && <div className="scan-actions">
                <p><strong>当前项目：</strong>{selectedProject.name}</p>
                <div className="session-box">
                  <div className="session-heading"><strong>L1 登录态</strong>{session && <span className={`session-state session-${session.expiryStatus}`}>{sessionStatusText(session.expiryStatus)}</span>}</div>
                  {session ? <div className="session-meta"><span>{session.cookieCount} 个 Cookie</span><span>{session.originCount} 个 Origin</span><span>{session.encryption}</span><span>{session.domains.join('、') || '未记录域名'}</span></div> : <p className="muted">尚未导入。扫描和执行将使用公开页面状态。</p>}
                  <label className="session-upload">选择 Playwright storageState JSON
                    <input type="file" accept="application/json,.json" onChange={(event) => loadSessionFile(event.target.files?.[0])} />
                  </label>
                  {sessionFileName && <p className="selected-file">待导入：{sessionFileName}</p>}
                  <button onClick={importProjectSession} disabled={busy || !sessionUpload}><ShieldCheck size={18} />加密导入登录态</button>
                  <div className="recording-actions">
                    {!recordingId ? <button onClick={startLoginRecording} disabled={busy}><Play size={18} />交互登录录制</button> : <><button className="primary" onClick={completeLoginRecording} disabled={busy}><ClipboardCheck size={18} />完成录制</button><button onClick={reloadLoginRecording} disabled={busy || recordingSnapshot?.status !== 'recording'}><RefreshCw size={18} />重新加载登录页</button><button onClick={cancelLoginRecording} disabled={busy}><StopCircle size={18} />取消</button></>}
                  </div>
                  {recordingId && <RecordingDiagnostics recording={recordingSnapshot} />}
                </div>
                <button className="primary" onClick={scanSelectedProject} disabled={busy}><ScanSearch size={18} />启动真实只读扫描</button>
                <button onClick={beginProjectTest}><SquarePen size={18} />用该地址新建测试</button>
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
                <label>Bridge 适配器<select value={environmentDraft.appBridge.adapter} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, appBridge: { ...environmentDraft.appBridge, adapter: event.target.value as 'generic' | 'cesium' } })}><option value="generic">Generic</option><option value="cesium">Cesium</option></select></label>
                <label className="checkline environment-bridge-toggle"><input type="checkbox" checked={environmentDraft.appBridge.enabled} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, appBridge: { ...environmentDraft.appBridge, enabled: event.target.checked } })} />启用 App Bridge</label>
                <label className="wide">Bridge 全局名称<input value={environmentDraft.appBridge.globalName} onChange={(event) => setEnvironmentDraft({ ...environmentDraft, appBridge: { ...environmentDraft.appBridge, globalName: event.target.value } })} spellCheck={false} /></label>
                <a className="evidence-link wide" href="/api/bridge/cesium-reference" download><Download size={17} />Cesium Bridge 参考适配器</a>
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
                  <h2>AI 模型设置</h2>
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
                <div className="form-field">
                  <label htmlFor="ai-model-name">模型名称</label>
                  <div className="model-field">
                    <ModelPresetPicker
                      settings={aiSettings}
                      onSelect={(provider, model) => updateAISettings({
                        ...aiSettings,
                        protocol: provider.protocol,
                        model: model.id,
                        baseUrl: provider.baseUrl
                      })}
                    />
                    <input id="ai-model-name" value={aiSettings.model} onChange={(event) => updateAISettings({ ...aiSettings, model: event.target.value })} placeholder="也可手动输入模型 ID" spellCheck={false} />
                  </div>
                </div>
                <div className="wide form-field">
                  <label htmlFor="ai-base-url">API Base URL</label>
                  <input id="ai-base-url" value={aiSettings.baseUrl} onChange={(event) => updateAISettings({ ...aiSettings, baseUrl: event.target.value })} placeholder="https://api.openai.com/v1" spellCheck={false} />
                  <small className="field-hint">选择模型预设时自动切换；兼容网关地址仍可手动输入。</small>
                </div>
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
                <button className="primary" onClick={testAIConnection} disabled={busy || !aiSettings.apiKey || !aiSettings.model || !aiSettings.baseUrl}><KeyRound size={18} />测试模型连接</button>
                <button onClick={() => { updateAISettings({ ...aiSettings, apiKey: '' }); setShowKey(false); }}>清除密钥</button>
                <button onClick={() => { setPlannerMode('ai'); setExecutionStrategy('agent'); setPage('new'); }} disabled={aiConnection !== 'connected'}><SquarePen size={18} />使用 AI 新建测试</button>
              </div>
              <p className="format-hint">连接测试会产生一次极小的模型请求。OpenAI 使用 Responses API；其他兼容服务可选择 Chat Completions，并填写该服务商提供的 Base URL 与模型名。</p>
            </div>
          </section>
        )}

        {page === 'compose' && (
          <section className="content compose-layout">
            <div className="site-preview panel">
              <div className="panel-title"><div><p className="eyebrow">已识别网站</p><h2>{selectedProject?.name || '当前网站'}</h2></div><ScanSearch size={22} /></div>
              <div className="recognized-url">{draft.targetUrl || '尚未填写'}</div>
              {recordingId ? <div className="login-guide"><KeyRound size={30} /><h3>请先在弹出的窗口中登录</h3><p>像平常一样登录。系统不会读取或保存你的密码；登录完成后回到这里。</p><button className="primary" onClick={completeLoginRecording} disabled={busy}>我已登录</button><button onClick={cancelLoginRecording} disabled={busy}>取消登录</button></div> : <>
                <div className="capability-summary"><h3>AI 发现可以检查这些内容</h3><ul><li><ShieldCheck size={17} />页面结构、按钮和表单是否可用</li><li><ShieldCheck size={17} />页面导航、链接和关键业务路径</li><li><ShieldCheck size={17} />页面错误、网络失败和异常反馈</li><li><ShieldCheck size={17} />截图、DOM、无障碍树和运行证据</li></ul></div>
                <p className="preview-note">真正开始后，这里会显示 AI 正在操作的网站画面。</p>
                {selectedProject && !session && <button className="primary login-start-button" onClick={startLoginRecording} disabled={busy}><Play size={17} />打开登录窗口</button>}
              </>}
            </div>
            <div className="ai-compose panel">
              <div className="assistant-message"><Eye size={20} /><div><strong>你想怎么测试？</strong><p>你可以直接说一件事，也可以让我按刚才识别到的内容全面检查。</p></div></div>
              <div className="test-choice-list">
                <button className="choice-card" onClick={() => document.getElementById('test-goal')?.focus()}><span><strong>告诉 AI 你想测试什么</strong><small>例如：搜索一个商品，加入购物车，但不要付款</small></span><Play size={19} /></button>
                <button className="choice-card" onClick={() => updateScenarioBackedDraft({ ...draft, flow: `全面检查这个网站：${draft.targetUrl || '当前网站'}，覆盖主要页面和关键功能；到付款前停止，不实际付款。`, expectation: '完成已确认范围的检查，用普通中文总结结果；付款保持未完成。' })}><span><strong>让 AI 全面检查这个网站</strong><small>先确认检查范围，再开始运行</small></span><Play size={19} /></button>
              </div>
              <div className="chat-composer">
                <textarea id="test-goal" value={draft.flow} onChange={(event) => updateScenarioBackedDraft({ ...draft, flow: event.target.value })} placeholder="告诉 AI 你想测试什么……" disabled={!!recordingId} />
                {aiConnection !== 'connected' && <div className="ai-required-note" role="status"><strong>开始前需要配置 GUI 内置 AI</strong><span>主流程不会静默降级为固定计划；本地规则只保留在传统测试编辑器中作为人工保底。</span><button type="button" onClick={() => setPage('ai')}><KeyRound size={15} />配置 AI</button></div>}
                <label className="checkline model-data-consent"><input type="checkbox" checked={visualFallbackEnabled} onChange={(event) => setVisualFallbackEnabled(event.target.checked)} />授权 AI 接收当前站点的脱敏截图、DOM 和无障碍树（逐步 Agent 必需）</label>
                <div className="composer-actions">
                  <label>AI 操作前<select value={approvalMode} onChange={(event) => setApprovalMode(event.target.value as ApprovalMode)}><option value="ask">请求批准 · 每次写入都问我</option><option value="delegate">替我审批 · 低风险自动处理</option><option value="full">完全访问权限 · 自动批准</option></select></label>
                  <button className="primary" onClick={startSimpleTest} disabled={busy || !!recordingId || connection !== 'connected' || aiConnection !== 'connected' || !visualFallbackEnabled || !draft.flow.trim() || health?.runnerAvailable === false}><Play size={17} />启动 AI 自主探索</button>
                </div>
                {approvalMode === 'full' && <div className="full-access-warning" role="alert"><ShieldAlert size={18} /><span>完全访问权限只影响允许范围内的动作；付款、密码、账号安全修改和删除非测试数据仍由系统硬性禁止。</span></div>}
              </div>
            </div>
          </section>
        )}

        {page === 'new' && (
          <section className="content legacy-compose">
            <div className="compose-layout">
              <aside className="site-preview panel">
                <div className="panel-title"><div><p className="eyebrow">网站识别</p><h2>已识别的网站</h2></div><ScanSearch size={22} /></div>
                <div className="recognized-url"><strong>目标地址</strong><br /><code>{draft.targetUrl || '尚未填写'}</code></div>
                <div className="capability-summary"><h3>当前可用能力</h3><ul><li><ShieldCheck size={18} />读取页面 DOM 与无障碍树</li><li><ShieldCheck size={18} />按步骤保存截图和网络证据</li><li><ShieldCheck size={18} />页面变化后重新观察并恢复</li><li><ShieldCheck size={18} />重要写入动作逐次请求确认</li></ul></div>
                {selectedProject && <div className="recognized-url login-choice"><strong>当前项目</strong><br /><span>{selectedProject.name}</span><small>{session ? '已载入登录态' : '未载入登录态，可在高级设置中登录'}</small></div>}
                {!selectedProject && <div className="preview-note"><Eye size={28} /><p>输入目标后，AI 会在真实浏览器中逐页观察。需要登录或私网访问时，再到高级设置关联项目。</p><button onClick={() => setPage('projects')}><KeyRound size={17} />打开项目接入</button></div>}
              </aside>
              <div className="ai-compose panel">
                <div className="assistant-message"><Eye size={18} /><div><strong>AI 测试工作台</strong><p>请描述希望验证的业务目标，AI 将结合页面观察逐步生成可审核的测试计划。</p></div></div>
                <div className="start-copy">
                  <p className="eyebrow">AI 网站测试助手</p>
                  <h2>告诉 AI 你想测试什么</h2>
                  <p>先描述目标网站和验收目标，AI 会结合当前页面的截图、DOM 与无障碍树逐步探索；每个动作仍会保留可审核的证据和回滚信息。</p>
                </div>
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
            <p className="mode-priority-note"><strong>默认主流程：</strong>AI 模型理解自然语言并由逐步 Agent 根据实时页面自主探索。固定计划只在模型不可用或需要人工精确编排时作为保底。</p>
            <div className="planner-switch" role="group" aria-label="计划生成方式">
              <button className={plannerMode === 'ai' ? 'active' : ''} onClick={() => setPlannerMode('ai')} disabled={aiConnection !== 'connected'}>AI 模型规划{aiConnection === 'connected' ? ` · ${aiSettings.model}` : ' · 请先连接'}</button>
              <button className={plannerMode === 'rules' ? 'active' : ''} onClick={() => { setPlannerMode('rules'); setExecutionStrategy('fixed'); }}>本地规则规划</button>
              <button className="settings-link" onClick={() => setPage('ai')}><KeyRound size={16} />配置 AI</button>
            </div>
            <div className="planner-switch" role="group" aria-label="执行策略">
              <button className={executionStrategy === 'agent' ? 'active' : ''} onClick={() => { setExecutionStrategy('agent'); setPlannerMode('ai'); }} disabled={aiConnection !== 'connected'}>逐步 Agent 探索</button>
              <button className={executionStrategy === 'fixed' ? 'active' : ''} onClick={() => setExecutionStrategy('fixed')}>固定计划执行</button>
            </div>
            {executionStrategy === 'agent' && <label className="checkline visual-fallback-toggle"><input type="checkbox" checked={visualFallbackEnabled} onChange={(event) => setVisualFallbackEnabled(event.target.checked)} />授权 AI 接收当前站点的脱敏截图、DOM 和无障碍树（逐步 Agent 必需）</label>}
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
              {executionStrategy === 'agent' ? <button className="primary" onClick={startSimpleTest} disabled={busy || !!recordingId || connection !== 'connected' || aiConnection !== 'connected' || !visualFallbackEnabled || !draft.flow.trim() || health?.runnerAvailable === false}><Play size={18} />启动逐步 Agent 探索</button> : <>
              <button className="primary" onClick={generatePlan} disabled={busy || connection !== 'connected'}><ClipboardCheck size={18} />{plannerMode === 'ai' ? 'AI 生成测试计划' : '生成规则测试计划'}</button>
              <button onClick={reviewPlan} disabled={!plan || busy || warnings.length > 0}>审核并校验计划</button>
              <button onClick={startRun} disabled={!reviewed || !plan || busy || health?.runnerAvailable === false}><Play size={18} />启动真实浏览器测试</button>
              </>}
            </div>
            {executionStrategy === 'agent' && <p>启动后由当前外部模型根据实时页面逐步规划，无需预生成固定脚本；遇到登录等待人工接管。</p>}
            {executionStrategy === 'fixed' && warnings.length > 0 && <div className="warning-box" role="alert"><strong>计划尚不能审核：</strong><ul>{warnings.map((item) => <li key={item}>{item}</li>)}</ul><p>请把这些描述改写成明确动作后重新生成。</p></div>}
                {executionStrategy === 'fixed' && <PlanEditor plan={plan} onChange={(next) => { setPlan(next); setReviewed(false); }} reviewed={reviewed} />}
              </div>
            </div>
          </section>
        )}

        {page === 'run' && (
          <section className="content">
            <div className="panel">
              <div className="panel-title">
                <div><p className="eyebrow">网站实时操作</p><h2>{run?.caseName ?? '尚未启动真实执行'}</h2></div>
                {run ? <StatusBadge status={run.status} /> : <StatusBadge status="pending_review" />}
              </div>
              {run?.pendingClarification && <ClarificationPanel key={run.pendingClarification.id} runId={run.id} pending={run.pendingClarification} busy={busy} externalLoginWindow={health?.runnerMode === 'process'} onAnswer={answerClarification} onStop={cancelRun} />}
              {!run?.pendingClarification?.question.startsWith('【需要手动登录】') && <LiveWorkbench run={run} />}
              {run && <div className="run-classification"><span>已完成步骤 {run.steps.length}</span><span>完成原因 {run.completionReason}</span><span>环境 {run.environmentId || '项目默认'}</span><span>保留 {run.artifactRetentionDays} 天</span>{run.runnerIsolation && <span>{isolationText(run.runnerIsolation)}</span>}<span>模型调用 {run.modelCalls}</span><span>Token {run.inputTokens + run.outputTokens}</span>{run.estimatedCost !== undefined && <span>估算成本 {run.estimatedCost}</span>}</div>}
              {run && <AgentDecisionList run={run} />}
              {run?.pendingConfirmation && <div className="confirmation-box" role="alert" aria-label="危险动作确认">
                <div><ShieldAlert size={22} /><div><h3>危险动作等待确认</h3><p>步骤 #{run.pendingConfirmation.stepIndex} · {run.pendingConfirmation.action} · 命中规则“{run.pendingConfirmation.rule}”</p></div></div>
                <dl><div><dt>目标</dt><dd>{run.pendingConfirmation.target}</dd></div><div><dt>请求时间</dt><dd>{displayTimestamp(run.pendingConfirmation.requestedAt)}</dd></div></dl>
                <div className="toolbar"><button className="primary" onClick={() => decideConfirmation('approved')} disabled={busy}><ShieldCheck size={18} />单次批准</button><button className="danger-button" onClick={() => decideConfirmation('rejected')} disabled={busy}><ShieldAlert size={18} />拒绝动作</button></div>
              </div>}
              {run && run.confirmationHistory.length > 0 && <details className="review-history"><summary>动作确认记录（{run.confirmationHistory.length}）</summary><ol>{run.confirmationHistory.map((item) => <li key={item.id}>步骤 #{item.stepIndex} · {item.action} · {item.decision === 'approved' ? '已批准' : '已拒绝'} · {item.actor} · {displayTimestamp(item.decidedAt)}</li>)}</ol></details>}
              <RunSteps run={run} />
              <div className="toolbar">
                <button onClick={cancelRun} disabled={!run || !activeRunStatuses.has(run.status) || busy}><StopCircle size={18} />停止执行</button>
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
      </main>
    </div>
  );
}

function pageTitle(page: Page) {
  return {
    start: '开始测试',
    compose: '告诉 AI 你想测试什么',
    settings: '高级设置',
    overview: '总览页',
    projects: '项目接入',
    ai: 'AI 模型设置',
    new: '告诉 AI 你想测试什么',
    run: '执行详情',
    history: '历史报告',
    report: '报告详情'
  }[page];
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
  clear: '清空', check: '勾选', uncheck: '取消勾选', hover: '悬停', scroll: '滚动', back: '后退', reload: '刷新', press: '按键',
  visual_click: '视觉点击', visual_hover: '视觉悬停', visual_scroll: '视觉滚动', visual_drag: '视觉拖拽',
  visual_zoom: '三维视图缩放', visual_clear: '清除画布', visual_draw_polygon: '多点测量/绘制', visual_draw_rectangle: '矩形测量/绘制', bridge_click: 'Bridge 点击',
  upload_files: '上传受控文件', download: '受控下载', wait_until: '等待业务状态'
};

const editableActionLabels: Partial<Record<ActionType, string>> = {
  navigate: '打开地址', click: '点击', fill: '输入', select: '选择', wait_for: '等待元素', screenshot: '截图检查点',
  clear: '清空', check: '勾选', uncheck: '取消勾选', hover: '悬停',
  upload_files: '上传受控文件', download: '受控下载', wait_until: '等待业务状态'
};

const cesiumEffectKinds = [
  'browse_search_filter_sort', 'viewer_camera_clock', 'upload_or_cloud_import', 'archive', 'download',
  's3_export', 'add_to_my_assets', 'create_clip', 'create_token', 'rotate_or_revoke_e2e_token',
  'regenerate_default_token', 'create_story', 'story_annotation_measurement', 'share_story', 'edit_label', 'delete_resource',
  'account_security_change', 'delete_account', 'team_membership_change', 'billing_change',
] as const;

const effectLevels = [
  'read_only', 'session_only', 'reversible_write', 'reversible_quota_write', 'isolated_local_write',
  'sensitive_reversible_write', 'high_risk_write', 'high_risk_external_write', 'high_risk_irreversible',
  'high_risk_public_write', 'high_risk_identity_write', 'forbidden',
] as const;

type LocatorStrategy = 'label' | 'test_id' | 'role' | 'placeholder' | 'attribute_name' | 'href' | 'text' | 'css';

function locatorEntry(locator?: Locator): [LocatorStrategy, string] {
  const order: LocatorStrategy[] = ['test_id', 'role', 'label', 'placeholder', 'attribute_name', 'href', 'text', 'css'];
  const key = order.find((item) => locator?.[item]) || 'text';
  return [key, locator?.[key] || ''];
}

function PlanEditor({ plan, onChange, reviewed }: { plan: TestPlan | null; onChange: (plan: TestPlan) => void; reviewed: boolean }) {
  if (!plan) return <div className="empty">连接真实执行服务并生成计划后，这里会显示实际可执行的动作与断言。</div>;
  const updateStep = (index: number, next: PlanStep) => onChange({ ...plan, steps: plan.steps.map((item, i) => i === index ? next : item) });
  const updateAssertion = (index: number, next: PlanAssertion) => onChange({ ...plan, assertions: plan.assertions.map((item, i) => i === index ? next : item) });
  return (
    <div className="panel plan-panel">
      <div className="panel-title"><h2>可审核执行计划</h2><StatusBadge status={reviewed ? 'passed' : 'pending_review'} /></div>
      <div className="plan-meta"><span>目标：{plan.base_url}</span><span>步骤：{plan.steps.length}</span><span>断言：{plan.assertions.length}</span></div>
      <div className="plan-list">
        {plan.steps.map((step, index) => {
          const waitCondition = step.wait_condition || { source: 'text' as const, expected: '', timeout_ms: 120000, interval_ms: 1000, locator: { text: '' } };
          const [strategy, locatorValue] = locatorEntry(step.action === 'wait_until' ? waitCondition.locator : step.locator);
          const updateLocator = (locator: Locator) => updateStep(index, step.action === 'wait_until'
            ? { ...step, wait_condition: { ...waitCondition, locator } }
            : { ...step, locator });
          return (
            <div className="plan-row" key={`${index}-${step.action}`}>
              <strong>#{index + 1}</strong>
              <select aria-label={`步骤 ${index + 1} 动作`} value={step.action} onChange={(event) => updateStep(index, { ...step, action: event.target.value as ActionType })}>
                {!editableActionLabels[step.action] && <option value={step.action}>{actionLabels[step.action]}</option>}
                {Object.entries(editableActionLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
              <input aria-label={`步骤 ${index + 1} 说明`} value={step.description || ''} placeholder="步骤说明" onChange={(event) => updateStep(index, { ...step, description: event.target.value })} />
              {step.action === 'navigate' ? (
                <input aria-label={`步骤 ${index + 1} 路径`} value={step.target || ''} placeholder="/path 或完整地址" onChange={(event) => updateStep(index, { ...step, target: event.target.value })} />
              ) : step.action === 'screenshot' ? <span className="plan-note">执行后保存页面截图</span> : step.action === 'wait_until' ? (
                <div className="locator-editor">
                  <select aria-label={`步骤 ${index + 1} 状态来源`} value={waitCondition.source} onChange={(event) => {
                    const source = event.target.value as 'text' | 'url' | 'attribute' | 'bridge';
                    updateStep(index, { ...step, wait_condition: { ...waitCondition, source, locator: source === 'url' || source === 'bridge' ? undefined : (waitCondition.locator || { text: '' }), attribute: source === 'attribute' ? (waitCondition.attribute || 'data-status') : undefined } });
                  }}><option value="text">元素文本</option><option value="url">页面 URL</option><option value="attribute">元素属性</option><option value="bridge">App Bridge</option></select>
                  <input aria-label={`步骤 ${index + 1} 期望状态`} value={waitCondition.expected} placeholder="期望包含的状态" onChange={(event) => updateStep(index, { ...step, wait_condition: { ...waitCondition, expected: event.target.value } })} />
                  {(waitCondition.source === 'text' || waitCondition.source === 'attribute') && <>
                    <select aria-label={`步骤 ${index + 1} 状态定位方式`} value={strategy} onChange={(event) => updateLocator({ [event.target.value]: locatorValue })}>
                      <option value="test_id">测试 ID</option><option value="role">ARIA 角色</option><option value="label">表单标签</option><option value="placeholder">Placeholder</option><option value="attribute_name">Name 属性</option><option value="href">Href</option><option value="text">可见文本</option><option value="css">CSS</option>
                    </select>
                    <input aria-label={`步骤 ${index + 1} 状态定位值`} value={locatorValue} placeholder="状态目标" onChange={(event) => updateLocator({ [strategy]: event.target.value })} />
                  </>}
                  {waitCondition.source === 'attribute' && <input aria-label={`步骤 ${index + 1} 状态属性`} value={waitCondition.attribute || ''} placeholder="data-status" onChange={(event) => updateStep(index, { ...step, wait_condition: { ...waitCondition, attribute: event.target.value } })} />}
                  <input type="number" min="1000" aria-label={`步骤 ${index + 1} 状态超时`} value={waitCondition.timeout_ms || 120000} onChange={(event) => updateStep(index, { ...step, wait_condition: { ...waitCondition, timeout_ms: Number(event.target.value) } })} />
                </div>
              ) : (
                <div className="locator-editor">
                  <select aria-label={`步骤 ${index + 1} 定位方式`} value={strategy} onChange={(event) => updateLocator({ [event.target.value]: locatorValue })}>
                    <option value="test_id">测试 ID</option><option value="role">ARIA 角色</option><option value="label">表单标签</option><option value="placeholder">Placeholder</option><option value="attribute_name">Name 属性</option><option value="href">Href</option><option value="text">可见文本</option><option value="css">CSS</option>
                  </select>
                  <input aria-label={`步骤 ${index + 1} 定位值`} value={locatorValue} placeholder="定位值" onChange={(event) => updateLocator({ [strategy]: event.target.value })} />
                </div>
              )}
              {(step.action === 'fill' || step.action === 'select') && <input aria-label={`步骤 ${index + 1} 输入值`} value={step.value || ''} placeholder="输入 / 选择值" onChange={(event) => updateStep(index, { ...step, value: event.target.value })} />}
              {step.action === 'upload_files' && <input aria-label={`步骤 ${index + 1} 文件 ID`} value={(step.file_ids || []).join(', ')} placeholder="D01/cesium-e2e-model.glb" onChange={(event) => updateStep(index, { ...step, file_ids: event.target.value.split(',').map((value) => value.trim()).filter(Boolean) })} />}
              {step.action === 'download' && <input aria-label={`步骤 ${index + 1} 下载文件名规则`} value={step.download_name_pattern || ''} placeholder=".*\\.zip" onChange={(event) => updateStep(index, { ...step, download_name_pattern: event.target.value || undefined })} />}
              <details className="step-policy-editor">
                <summary>副作用与清理</summary>
                <div>
                  <label>策略类型<select aria-label={`步骤 ${index + 1} 副作用类型`} value={step.effect_kind || ''} onChange={(event) => updateStep(index, { ...step, effect_kind: event.target.value || undefined })}><option value="">未声明</option>{cesiumEffectKinds.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
                  <label>风险等级<select aria-label={`步骤 ${index + 1} 副作用等级`} value={step.effect_level || ''} onChange={(event) => updateStep(index, { ...step, effect_level: (event.target.value || undefined) as PlanStep['effect_level'] })}><option value="">未声明</option>{effectLevels.map((value) => <option value={value} key={value}>{value}</option>)}</select></label>
                  <label>账号上下文<input value={step.account_context || ''} onChange={(event) => updateStep(index, { ...step, account_context: event.target.value || undefined })} /></label>
                  <label>目标业务 ID<input value={step.target_id || ''} onChange={(event) => updateStep(index, { ...step, target_id: event.target.value || undefined })} /></label>
                  <label>资源名称<input value={step.resource_name || ''} onChange={(event) => updateStep(index, { ...step, resource_name: event.target.value || undefined })} /></label>
                  <label>清理动作<input value={step.cleanup_action || ''} onChange={(event) => updateStep(index, { ...step, cleanup_action: event.target.value || undefined })} /></label>
                </div>
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
        <thead><tr>{selectable && <th className="selection-column">选择</th>}<th>运行编号</th><th>用例名称</th><th>执行时间</th><th>执行角色</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{runs.map((item) => <tr className={selectedRunIds?.has(item.id) ? 'selected-row' : ''} key={item.id}>{selectable && <td className="selection-column"><input type="checkbox" aria-label={`选择报告 ${item.id}`} checked={selectedRunIds?.has(item.id) || false} onChange={() => onToggleSelection?.(item.id)} /></td>}<td>{item.id}</td><td>{item.caseName}</td><td>{item.startedAt}</td><td>{item.role}</td><td><div className="run-status-stack"><StatusBadge status={item.status} />{item.reviewSummary.total > 0 && <span className="review-state">{reviewDispositionText[item.reviewSummary.disposition]}</span>}</div></td><td><button onClick={() => onReport(item.id)}>详情</button></td></tr>)}</tbody>
      </table>
    </div>
  );
}

function LiveWorkbench({ run }: { run: TestRun | null }) {
  const latest = run?.steps[run.steps.length - 1];
  const screenshot = latest?.after?.screenshot || latest?.before?.screenshot || latest?.evidence;
  return <div className="live-workbench" aria-label="网站实时操作工作台">
    <div className="live-screen">
      <div className="live-screen-bar"><span><i />实时画面（每一步更新）</span><small>{latest?.after?.title || latest?.before?.title || '正在等待网站画面'}</small></div>
      {screenshot ? <img src={screenshot} alt="AI 正在操作的网站画面" /> : <div className="screen-placeholder"><Eye size={44} /><p>{run ? '浏览器已启动，等待第一张证据截图…' : '网站窗口启动后，画面会显示在这里'}</p></div>}
    </div>
    <div className="run-chat" aria-live="polite">
      {!run ? <div className="screen-placeholder"><p>审核计划并启动真实浏览器测试后显示 Agent 步骤。</p></div> : <>
        <div className="assistant-message"><Activity size={20} /><div><strong>AI 正在测试</strong><p>{run.scenarioGoal || '正在理解你的要求…'}</p></div></div>
        {run.steps.slice(-8).map((step) => <div className="chat-step" key={step.id}><span className={`step-dot step-${step.result}`} /><div><strong>{step.action}</strong><p>{step.progressAssessment || step.plannerReason || step.target}</p></div></div>)}
        {run.status === 'passed' && <div className="assistant-message result-good"><ShieldCheck size={20} /><div><strong>检查完成</strong><p>{run.goalSummary || '这次检查已完成。'}</p></div></div>}
        {['failed', 'error', 'issues_found', 'incomplete', 'system_error'].includes(run.status) && <div className="assistant-message result-attention"><ShieldAlert size={20} /><div><strong>检查发现问题</strong><p>{run.goalSummary || run.completionReason}</p></div></div>}
      </>}
    </div>
  </div>;
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
            {step.fileEvidence.length > 0 && <details className="observation-facts">
              <summary>文件证据（{step.fileEvidence.length}）</summary>
              <dl>{step.fileEvidence.map((file) => <div key={`${file.path}-${file.sha256}`}><dt>{file.path}</dt><dd>{file.sizeBytes} bytes · {file.mimeType}<br /><code>SHA-256 {file.sha256}</code>{file.archiveEntries?.length ? <><br />ZIP 条目 {file.archiveEntries.length}</> : null}</dd></div>)}</dl>
            </details>}
            {step.asyncTimeline.length > 0 && <details className="observation-facts">
              <summary>异步状态时间线（{step.asyncTimeline.length}）</summary>
              <ol>{step.asyncTimeline.map((event, index) => <li key={`${event.elapsedMs}-${index}`}>{event.elapsedMs}ms · {event.observed}</li>)}</ol>
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

function AgentDecisionList({ run }: { run: TestRun }) {
  if (!run.modelCallRecords.length) return null;
  const formHits = run.modelCallRecords.filter((item) => item.model === 'AI 表单快速通道').length;
  return <div className="agent-decisions" aria-label="Agent 决策记录">
    {formHits > 0 && <p role="status">AI 表单快速通道命中 {formHits} 次 · 减少 {formHits} 次外部模型请求 · 每步仍独立校验，保存和删除不合并执行</p>}
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

const locatorReviewActions = new Set<ActionType>(['click', 'fill', 'select', 'wait_for', 'clear', 'check', 'uncheck', 'hover', 'visual_click', 'upload_files', 'download']);

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
        <div className="run-classification"><span>原始状态 {statusText[report.run.executionStatus]}</span><span>审核终态 {reviewText}</span><span>接入 {report.run.onboardingLevel || '未关联'}</span><span>环境 {report.run.environmentId || '项目默认'}</span><span>保留 {report.run.artifactRetentionDays} 天</span>{report.run.runnerIsolation && <span>{isolationText(report.run.runnerIsolation, true)}</span>}<span>模式 {report.run.replayMode}</span><span>稳定性 {report.run.stabilityLevel}</span><span>耗时 {report.run.durationMs}ms</span><span>模型调用 {report.run.modelCalls}</span></div>
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
