import { useEffect, useMemo, useState } from 'react';
import { Activity, ClipboardCheck, Eye, EyeOff, FileText, History, KeyRound, Play, ShieldAlert, ShieldCheck, SquarePen, StopCircle } from 'lucide-react';
import { api } from './services/api';
import type { AISettings, ActionType, Locator, PlanAssertion, PlanStep, Report, TestCaseDraft, TestPlan, TestRun } from './services/types';

type Page = 'overview' | 'ai' | 'new' | 'run' | 'history' | 'report';
type Connection = 'checking' | 'connected' | 'disconnected';
type AIConnection = 'untested' | 'testing' | 'connected' | 'failed';
type PlannerMode = 'rules' | 'ai';

const statusText = {
  passed: '成功',
  failed: '失败',
  error: '错误',
  running: '运行中',
  pending_review: '待审核',
  stopped: '已停止',
  skipped: '已跳过'
} as const;

const initialDraft: TestCaseDraft = {
  name: '管理员登录验收',
  targetUrl: 'http://127.0.0.1:8765',
  flow: '在“用户名”输入“admin”；在“密码”输入“admin123”；点击“登录”；确认看到“客户管理”；截图',
  role: '测试工程师',
  preconditions: '目标网站已启动；仅使用授权的测试环境和脱敏账号。',
  expectation: '确认看到“客户管理”'
};

const initialAISettings: AISettings = {
  protocol: 'responses',
  baseUrl: 'https://api.openai.com/v1',
  model: 'gpt-5.6-terra',
  apiKey: ''
};

function StatusBadge({ status }: { status: keyof typeof statusText }) {
  return <span className={`status status-${status}`}>{statusText[status]}</span>;
}

export default function App() {
  const [page, setPage] = useState<Page>('overview');
  const [draft, setDraft] = useState(initialDraft);
  const [plan, setPlan] = useState<TestPlan | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [run, setRun] = useState<TestRun | null>(null);
  const [history, setHistory] = useState<TestRun[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState<Connection>('checking');
  const [message, setMessage] = useState('正在连接真实执行服务…');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [aiSettings, setAISettings] = useState<AISettings>(initialAISettings);
  const [aiConnection, setAIConnection] = useState<AIConnection>('untested');
  const [aiMessage, setAIMessage] = useState('尚未测试模型连接。配置只保留在当前页面内存中。');
  const [showKey, setShowKey] = useState(false);
  const [plannerMode, setPlannerMode] = useState<PlannerMode>('rules');

  useEffect(() => {
    let active = true;
    api.health()
      .then(async (health) => {
        if (!active) return;
        setConnection('connected');
        setMessage(`已连接 ${health.engine} · ${health.planner}`);
        const runs = await api.getHistory();
        if (active) setHistory(runs);
      })
      .catch((error: Error) => {
        if (!active) return;
        setConnection('disconnected');
        setMessage(error.message);
      });
    return () => { active = false; };
  }, []);

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
      setMessage(error instanceof Error ? error.message : String(error));
      if (connection !== 'connected') setConnection('disconnected');
    } finally {
      setBusy(false);
    }
  };

  const generatePlan = () => execute(async () => {
    if (plannerMode === 'ai' && aiConnection !== 'connected') {
      throw new Error('请先在“AI 模型设置”中测试连接成功，再使用 AI 生成计划。');
    }
    const generated = plannerMode === 'ai'
      ? await api.generateAIPlan(draft, aiSettings)
      : await api.generatePlan(draft);
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
      setAIMessage(`连接成功：${result.model}，响应 ${result.elapsedMs}ms。`);
      setMessage('AI 模型连接成功，可以在新建测试中选择 AI 规划。');
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
    const nextRun = await api.startRun(plan);
    setRun(nextRun);
    setHistory(await api.getHistory());
    setReport(null);
    setPage('run');
    setMessage(`真实执行完成：${statusText[nextRun.status]}`);
  });

  const openHistory = () => execute(async () => {
    setHistory(await api.getHistory());
    setPage('history');
    setMessage('真实运行历史已加载。');
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

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">京彩OPC<br /><span>AI GUI 测试管理端</span></div>
        <nav aria-label="主要导航">
          <button className={page === 'overview' ? 'active' : ''} onClick={() => setPage('overview')}><Activity size={18} />总览</button>
          <button className={page === 'ai' ? 'active' : ''} onClick={() => setPage('ai')}><KeyRound size={18} />AI 模型设置</button>
          <button className={page === 'new' ? 'active' : ''} onClick={() => setPage('new')}><SquarePen size={18} />新建测试</button>
          <button className={page === 'run' ? 'active' : ''} onClick={() => setPage('run')}><Play size={18} />执行详情</button>
          <button className={page === 'history' ? 'active' : ''} onClick={openHistory}><History size={18} />历史报告</button>
          <button className={page === 'report' ? 'active' : ''} onClick={() => openReport()}><FileText size={18} />报告详情</button>
        </nav>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <p className="eyebrow">真实执行模式 / Playwright</p>
            <h1>{pageTitle(page)}</h1>
          </div>
          <div className={`service-state service-${connection}`}>
            {connection === 'connected' ? <ShieldCheck size={18} /> : <ShieldAlert size={18} />}
            <span>{connection === 'checking' ? '正在检查执行服务' : connection === 'connected' ? '真实执行服务已连接' : '执行服务未连接'}</span>
          </div>
        </header>

        <div className={`notice notice-${connection}`} role={connection === 'disconnected' ? 'alert' : 'status'}>{message}</div>

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
              </div>
              <div className={`ai-message ai-message-${aiConnection}`} role={aiConnection === 'failed' ? 'alert' : 'status'}>{aiMessage}</div>
              <div className="toolbar">
                <button className="primary" onClick={testAIConnection} disabled={busy || !aiSettings.apiKey || !aiSettings.model || !aiSettings.baseUrl}><KeyRound size={18} />测试模型连接</button>
                <button onClick={() => { updateAISettings({ ...aiSettings, apiKey: '' }); setShowKey(false); }}>清除密钥</button>
                <button onClick={() => { setPlannerMode('ai'); setPage('new'); }} disabled={aiConnection !== 'connected'}><SquarePen size={18} />使用 AI 新建测试</button>
              </div>
              <p className="format-hint">连接测试会产生一次极小的模型请求。OpenAI 使用 Responses API；其他兼容服务可选择 Chat Completions，并填写该服务商提供的 Base URL 与模型名。</p>
            </div>
          </section>
        )}

        {page === 'new' && (
          <section className="content">
            <div className="planner-switch" role="group" aria-label="计划生成方式">
              <button className={plannerMode === 'rules' ? 'active' : ''} onClick={() => setPlannerMode('rules')}>本地规则规划</button>
              <button className={plannerMode === 'ai' ? 'active' : ''} onClick={() => setPlannerMode('ai')} disabled={aiConnection !== 'connected'}>AI 模型规划{aiConnection === 'connected' ? ` · ${aiSettings.model}` : ' · 请先连接'}</button>
              <button className="settings-link" onClick={() => setPage('ai')}><KeyRound size={16} />配置 AI</button>
            </div>
            <div className="form-grid">
              <label>测试名称<input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
              <label>目标网站地址<input value={draft.targetUrl} onChange={(event) => setDraft({ ...draft, targetUrl: event.target.value })} /></label>
              <label>执行角色<input value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value })} /></label>
              <label>前置条件<textarea value={draft.preconditions} onChange={(event) => setDraft({ ...draft, preconditions: event.target.value })} /></label>
              <label className="wide">自然语言业务流程<textarea value={draft.flow} onChange={(event) => setDraft({ ...draft, flow: event.target.value })} /></label>
              <label className="wide">期望结果<textarea value={draft.expectation} onChange={(event) => setDraft({ ...draft, expectation: event.target.value })} /></label>
            </div>
            <p className="format-hint">建议格式：点击“登录”；在“用户名”输入“admin”；确认看到“控制台”。无法确定的描述会被标出，不会自动判定成功。</p>
            <div className="toolbar">
              <button className="primary" onClick={generatePlan} disabled={busy || connection !== 'connected'}><ClipboardCheck size={18} />{plannerMode === 'ai' ? 'AI 生成测试计划' : '生成规则测试计划'}</button>
              <button onClick={reviewPlan} disabled={!plan || busy || warnings.length > 0}>审核并校验计划</button>
              <button onClick={startRun} disabled={!reviewed || !plan || busy}><Play size={18} />启动真实浏览器测试</button>
            </div>
            {warnings.length > 0 && <div className="warning-box" role="alert"><strong>计划尚不能审核：</strong><ul>{warnings.map((item) => <li key={item}>{item}</li>)}</ul><p>请把这些描述改写成明确动作后重新生成。</p></div>}
            <PlanEditor plan={plan} onChange={(next) => { setPlan(next); setReviewed(false); }} reviewed={reviewed} />
          </section>
        )}

        {page === 'run' && (
          <section className="content">
            <div className="panel">
              <div className="panel-title">
                <h2>{run?.id ?? '尚未启动真实执行'}</h2>
                {run ? <StatusBadge status={run.status} /> : <StatusBadge status="pending_review" />}
              </div>
              <RunSteps run={run} />
              <div className="toolbar">
                <button disabled={!run || run.status !== 'running'}><StopCircle size={18} />停止执行</button>
                <button onClick={() => openReport(run?.id)} disabled={!run}><FileText size={18} />打开报告详情</button>
              </div>
            </div>
          </section>
        )}

        {page === 'history' && (
          <section className="content panel">
            <h2>真实运行历史</h2>
            <RunTable runs={history} onReport={openReport} />
          </section>
        )}

        {page === 'report' && (
          <section className="content">
            <ReportView report={report} />
          </section>
        )}
      </main>
    </div>
  );
}

function pageTitle(page: Page) {
  return { overview: '总览页', ai: 'AI 模型设置', new: '新建测试', run: '执行详情', history: '历史报告', report: '报告详情' }[page];
}

function Metric({ label, value, tone }: { label: string; value: string | number; tone?: 'danger' }) {
  return <div className={`metric ${tone ?? ''}`}><span>{label}</span><strong>{value}</strong></div>;
}

const actionLabels: Record<ActionType, string> = {
  navigate: '打开地址', click: '点击', fill: '输入', select: '选择', wait_for: '等待元素', screenshot: '截图检查点'
};

function locatorEntry(locator?: Locator): [keyof Locator, string] {
  const order: Array<keyof Locator> = ['label', 'test_id', 'role', 'text', 'css'];
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
          const [strategy, locatorValue] = locatorEntry(step.locator);
          return (
            <div className="plan-row" key={`${index}-${step.action}`}>
              <strong>#{index + 1}</strong>
              <select aria-label={`步骤 ${index + 1} 动作`} value={step.action} onChange={(event) => updateStep(index, { ...step, action: event.target.value as ActionType })}>
                {Object.entries(actionLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
              <input aria-label={`步骤 ${index + 1} 说明`} value={step.description || ''} placeholder="步骤说明" onChange={(event) => updateStep(index, { ...step, description: event.target.value })} />
              {step.action === 'navigate' ? (
                <input aria-label={`步骤 ${index + 1} 路径`} value={step.target || ''} placeholder="/path 或完整地址" onChange={(event) => updateStep(index, { ...step, target: event.target.value })} />
              ) : step.action === 'screenshot' ? <span className="plan-note">执行后保存页面截图</span> : (
                <div className="locator-editor">
                  <select aria-label={`步骤 ${index + 1} 定位方式`} value={strategy} onChange={(event) => updateStep(index, { ...step, locator: { [event.target.value]: locatorValue } })}>
                    <option value="label">表单标签</option><option value="test_id">测试 ID</option><option value="role">ARIA 角色</option><option value="text">可见文本</option><option value="css">CSS</option>
                  </select>
                  <input aria-label={`步骤 ${index + 1} 定位值`} value={locatorValue} placeholder="定位值" onChange={(event) => updateStep(index, { ...step, locator: { [strategy]: event.target.value } })} />
                </div>
              )}
              {(step.action === 'fill' || step.action === 'select') && <input aria-label={`步骤 ${index + 1} 输入值`} value={step.value || ''} placeholder="输入 / 选择值" onChange={(event) => updateStep(index, { ...step, value: event.target.value })} />}
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

function RunTable({ runs, onReport }: { runs: TestRun[]; onReport: (runId?: string) => void }) {
  if (!runs.length) return <div className="empty compact">尚无真实运行记录。Mock 样例已移除。</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>运行编号</th><th>用例名称</th><th>执行时间</th><th>执行角色</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{runs.map((item) => <tr key={item.id}><td>{item.id}</td><td>{item.caseName}</td><td>{item.startedAt}</td><td>{item.role}</td><td><StatusBadge status={item.status} /></td><td><button onClick={() => onReport(item.id)}>详情</button></td></tr>)}</tbody>
      </table>
    </div>
  );
}

function RunSteps({ run }: { run: TestRun | null }) {
  if (!run) return <div className="empty">审核计划并启动真实浏览器测试后显示步骤状态。</div>;
  return <div className="step-list">{run.steps.map((step) => <div className="step-card" key={step.id}><b>#{step.order}</b><div><strong>{step.action}</strong><span>{step.target}</span></div><StatusBadge status={step.result} /><span>{step.durationMs}ms</span><span>{step.errorType ?? '无错误'}</span><button disabled={!step.evidence} onClick={() => step.evidence && window.open(step.evidence, '_blank', 'noopener,noreferrer')}>截图</button></div>)}</div>;
}

function ReportView({ report }: { report: Report | null }) {
  if (!report) return <div className="empty">尚无真实报告。请先完成一次浏览器测试。</div>;
  return (
    <div className="report-grid">
      <div className="panel min-width-zero">
        <div className="panel-title"><h2>{report.run.id}</h2><StatusBadge status={report.run.status} /></div>
        <RunSteps run={report.run} />
      </div>
      <div className="panel report-summary">
        <h2>断言结果</h2>
        {report.assertions.length ? report.assertions.map((item) => <p className="assertion" key={item.name}><StatusBadge status={item.passed ? 'passed' : 'failed'} />{item.name}：{item.message}</p>) : <p className="muted">没有断言结果</p>}
        <h2>失败步骤</h2>
        <p>{report.failedStep ? `#${report.failedStep.order} ${report.failedStep.action}：${report.failedStep.errorType || '未通过'}` : '无失败步骤'}</p>
        <h2>复现步骤</h2>
        <ol>{report.reproduction.map((item) => <li key={item}>{item}</li>)}</ol>
        <h2>可能原因（启发式）</h2>
        {report.heuristicReasons.length ? <ul>{report.heuristicReasons.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="muted">本次运行没有原因提示。</p>}
      </div>
    </div>
  );
}
