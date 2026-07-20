export type RunStatus = 'passed' | 'failed' | 'error' | 'running' | 'pending_review' | 'stopped' | 'skipped';

export interface TestCaseDraft {
  name: string;
  targetUrl: string;
  flow: string;
  role: string;
  preconditions: string;
  expectation: string;
}

export type ActionType = 'navigate' | 'click' | 'fill' | 'select' | 'wait_for' | 'screenshot';
export type AssertionType = 'page_reached' | 'visible' | 'not_visible' | 'text_contains' | 'url_contains' | 'value_equals' | 'count_equals';

export interface Locator {
  role?: string;
  name?: string;
  label?: string;
  test_id?: string;
  css?: string;
  text?: string;
}

export interface PlanStep {
  action: ActionType;
  target?: string;
  locator?: Locator;
  value?: string;
  value_from_secret?: string;
  description?: string;
}

export interface PlanAssertion {
  type: AssertionType;
  locator?: Locator;
  expected?: string;
  count?: number;
  description?: string;
}

export interface TestPlan {
  name: string;
  base_url: string;
  role?: string;
  preconditions: Array<{ description: string }>;
  steps: PlanStep[];
  assertions: PlanAssertion[];
}

export interface PlanGeneration {
  plan: TestPlan;
  warnings: string[];
  planner: string;
}

export type AIProtocol = 'responses' | 'chat_completions';

export interface AISettings {
  protocol: AIProtocol;
  baseUrl: string;
  model: string;
  apiKey: string;
}

export interface AIConnectionResult {
  connected: true;
  model: string;
  protocol: AIProtocol;
  elapsedMs: number;
}

export interface RunStep {
  id: string;
  order: number;
  action: string;
  target: string;
  result: RunStatus;
  durationMs: number;
  errorType?: string;
  evidence?: string;
}

export interface TestRun {
  id: string;
  caseName: string;
  role: string;
  startedAt: string;
  status: RunStatus;
  steps: RunStep[];
  assertions: Array<{ name: string; passed: boolean; message: string }>;
  reproduction: string[];
  heuristicReasons: string[];
}

export interface Report {
  run: TestRun;
  assertions: Array<{ name: string; passed: boolean; message: string }>;
  failedStep?: RunStep;
  reproduction: string[];
  heuristicReasons: string[];
}

export interface HealthStatus {
  status: 'ok';
  mode: 'real';
  engine: string;
  planner: string;
  aiConfigStorage?: string;
}
