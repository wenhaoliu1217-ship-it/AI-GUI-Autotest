import type {
  AIConnectionResult,
  AISettings,
  HealthStatus,
  PlanGeneration,
  Report,
  RunStatus,
  TestCaseDraft,
  TestPlan,
  TestRun
} from './types';

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

interface BackendStep {
  index: number;
  action: string;
  description?: string;
  target_summary: string;
  status: RunStatus;
  started_at: string;
  ended_at: string;
  error_message?: string;
  failure_category?: string;
  screenshot?: string;
}

interface BackendRun {
  run_id: string;
  plan_name: string;
  role?: string;
  status: RunStatus;
  started_at: string;
  ended_at: string;
  steps: BackendStep[];
  assertions: Array<{
    index: number;
    type: string;
    description?: string;
    status: RunStatus;
    expected_summary?: string;
    actual_summary?: string;
    error_message?: string;
    screenshot?: string;
  }>;
  reproduction_steps: string[];
  cause_hints: Array<{ message: string; confidence: string; category: string }>;
  artifact_base_url: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: init?.body ? { 'Content-Type': 'application/json', ...init?.headers } : init?.headers
    });
  } catch {
    throw new Error(`无法连接真实执行服务（${API_BASE}）。请先启动后端，当前不会生成模拟结果。`);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: unknown };
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body);
    throw new Error(detail || `请求失败：HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function durationMs(startedAt: string, endedAt: string) {
  return Math.max(0, new Date(endedAt).getTime() - new Date(startedAt).getTime());
}

function artifactUrl(run: BackendRun, relative?: string) {
  if (!relative) return undefined;
  return `${API_BASE}${run.artifact_base_url}/${relative.replace(/^\//, '')}`;
}

function toRun(payload: BackendRun): TestRun {
  const steps = payload.steps.map((step) => ({
    id: `${payload.run_id}-step-${step.index}`,
    order: step.index,
    action: step.description || step.action,
    target: step.target_summary,
    result: step.status,
    durationMs: durationMs(step.started_at, step.ended_at),
    errorType: step.error_message || step.failure_category,
    evidence: artifactUrl(payload, step.screenshot)
  }));
  return {
    id: payload.run_id,
    caseName: payload.plan_name,
    role: payload.role || '未指定',
    startedAt: new Date(payload.started_at).toLocaleString('zh-CN', { hour12: false }),
    status: payload.status,
    steps,
    assertions: payload.assertions.map((item) => ({
      name: item.description || item.type,
      passed: item.status === 'passed',
      message: item.error_message || item.actual_summary || item.expected_summary || item.status
    })),
    reproduction: payload.reproduction_steps,
    heuristicReasons: payload.cause_hints.map((item) => `${item.message}（${item.category}，置信度 ${item.confidence}）`)
  };
}

function toReport(run: TestRun): Report {
  return {
    run,
    assertions: run.assertions,
    failedStep: run.steps.find((step) => step.result !== 'passed'),
    reproduction: run.reproduction,
    heuristicReasons: run.heuristicReasons
  };
}

export const api = {
  baseUrl: API_BASE,
  health: () => request<HealthStatus>('/api/health'),
  generatePlan: (draft: TestCaseDraft) => request<PlanGeneration>('/api/plans/generate', {
    method: 'POST',
    body: JSON.stringify(draft)
  }),
  testAI: (settings: AISettings) => request<AIConnectionResult>('/api/ai/test', {
    method: 'POST',
    body: JSON.stringify({ settings })
  }),
  generateAIPlan: (draft: TestCaseDraft, settings: AISettings) => request<PlanGeneration>('/api/ai/plans/generate', {
    method: 'POST',
    body: JSON.stringify({ draft, settings })
  }),
  async reviewPlan(plan: TestPlan): Promise<TestPlan> {
    const result = await request<{ valid: boolean; plan: TestPlan }>('/api/plans/validate', {
      method: 'POST',
      body: JSON.stringify({ plan })
    });
    return result.plan;
  },
  async startRun(plan: TestPlan): Promise<TestRun> {
    return toRun(await request<BackendRun>('/api/runs', {
      method: 'POST',
      body: JSON.stringify({ plan, headless: true, timeoutMs: 10_000 })
    }));
  },
  async getHistory(): Promise<TestRun[]> {
    return (await request<BackendRun[]>('/api/runs')).map(toRun);
  },
  async getRun(runId: string): Promise<TestRun> {
    return toRun(await request<BackendRun>(`/api/runs/${encodeURIComponent(runId)}`));
  },
  async getReport(runId: string): Promise<Report> {
    return toReport(await api.getRun(runId));
  }
};
