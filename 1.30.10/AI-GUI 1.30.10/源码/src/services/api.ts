import type {
  AIConnectionResult,
  AISettings,
  AcceptanceBatch,
  AcceptanceCatalog,
  BusinessContextStatus,
  HealthStatus,
  L4Workflow,
  L4RunResult,
  PlanGeneration,
  ProjectConfig,
  ProjectDraft,
  CompatibilityReport,
  EnvironmentConfig,
  EnvironmentDraft,
  SessionMetadata,
  SessionRecording,
  Report,
  ReviewedStep,
  RunPathReview,
  RunStatus,
  ScenarioConfig,
  ScenarioDraft,
  TestCaseDraft,
  TestPlan,
  TestRun,
  TestFileRecord
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
  execution_mode?: 'locator' | 'visual' | 'app_bridge';
  stability_level?: 'A' | 'B' | 'C' | 'D';
  stability_reason?: string;
  before?: BackendObservation;
  after?: BackendObservation;
  planner_reason?: string;
  progress_assessment?: string;
  computer_use_triggered?: boolean;
  computer_use_reason?: string;
  coordinate_source?: string;
  app_bridge_result?: Record<string, unknown>;
  stability_evidence?: Record<string, unknown>;
  canvas_evidence?: Record<string, unknown>;
  file_evidence?: Record<string, unknown>;
  async_evidence?: Record<string, unknown>;
  side_effect_evidence?: Record<string, unknown>;
  component_evidence?: Record<string, unknown>;
}

interface BackendObservation {
  url: string;
  title: string;
  screenshot?: string;
  dom_summary: string[];
  accessibility_summary: string;
  console_errors: string[];
  page_errors: string[];
  failed_requests: string[];
  page_issues?: Array<{ kind: string; severity: string; confidence: string; message: string; target: string }>;
  page_health?: { ready_state: string; visible_text_length: number; visible_element_count: number; interactive_count: number; visual_surface_count: number };
  captured_at?: string;
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
    semantic_evidence?: Record<string, unknown>;
  }>;
  reproduction_steps: string[];
  cause_hints: Array<{ message: string; confidence: string; category: string }>;
  artifact_base_url: string;
  replay_mode?: 'exploration' | 'stable' | 'adaptive';
  onboarding_level?: 'L0' | 'L1' | 'L2' | 'L3';
  stability_level?: 'A' | 'B' | 'C' | 'D';
  completion_reason?: string;
  project_id?: string;
  environment_id?: string;
  environment_updated_at?: string;
  artifact_retention_days?: number;
  runner_isolation?: {
    mode: string; process_id?: number; windows_job_assigned?: boolean;
    memory_limit_mb?: number; network_policy?: string; forced_termination?: boolean;
    container_name?: string; image?: string; root_filesystem_read_only?: boolean;
    artifact_mount?: string; tmpfs_mb?: number; cpu_limit?: number; pids_limit?: number;
    capabilities_dropped?: string; no_new_privileges?: boolean;
    container_network_mode?: string; container_private_network_allowed?: boolean;
  };
  duration_ms?: number;
  scenario_goal?: string;
  goal_status?: 'in_progress' | 'achieved' | 'not_achieved' | 'incomplete';
  goal_summary?: string;
  review_summary?: {
    disposition: 'pending_confirmation' | 'issues_found' | 'all_rejected' | 'no_findings';
    pending: number; confirmed: number; rejected: number; total: number;
  };
  model_calls?: number;
  estimated_cost?: number;
  input_tokens?: number;
  output_tokens?: number;
  model_call_records?: Array<{
    index: number; model: string; protocol: string; elapsed_ms: number;
    input_tokens: number; output_tokens: number; estimated_cost?: number;
    decision: string; reason: string;
  }>;
  pending_confirmation?: {
    id: string; step_index: number; action: string; target: string; rule: string; requested_at: string;
  };
  confirmation_history?: Array<{
    id: string; step_index: number; action: string; target: string; rule: string;
    requested_at: string; decision: 'approved' | 'rejected'; actor: string; decided_at: string;
  }>;
  websocket_timeline?: Array<Record<string, unknown>>;
  cleanup_report?: Record<string, unknown>;
  business_context_snapshot?: Record<string, unknown>;
  evidence_manifest?: TestRun['evidenceManifest'];
  evidence_completeness?: number;
  evidence_manifest_path?: string;
  findings?: Array<{
    id: string; title: string; category: string; severity: 'Blocker' | 'High' | 'Medium' | 'Low'; confidence: string;
    actual_result: string; expected_result: string; facts: string[]; inference: string; evidence: string[];
    evidence_timeline?: Array<{ phase: string; timestamp: string; screenshot?: string; facts: string[] }>;
    reproduction_steps: string[]; review_status: 'pending_review' | 'confirmed' | 'rejected';
    review_history?: Array<{
      timestamp: string; actor?: string; changedFields?: string[];
      changes?: Record<string, { before: unknown; after: unknown }>;
    }>;
  }>;
  generated_test?: {
    source_path: string; source: string; stability_level: 'A' | 'B' | 'C' | 'D';
    supported_replay_modes: Array<'stable' | 'adaptive'>; ci_eligible: boolean; ci_recommendation: string;
    manual_steps?: string[]; source_revision?: number;
    source_review_history?: Array<{
      timestamp: string; actor: string; action: 'manual_source_edit' | 'regenerated_from_path_review';
      revision: number; beforeSha256: string; afterSha256: string;
    }>;
  };
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

function toEvidence(run: BackendRun, evidence?: BackendObservation) {
  if (!evidence) return undefined;
  return {
    url: evidence.url,
    title: evidence.title,
    screenshot: artifactUrl(run, evidence.screenshot),
    domSummary: evidence.dom_summary,
    accessibilitySummary: evidence.accessibility_summary,
    consoleErrors: evidence.console_errors,
    pageErrors: evidence.page_errors,
    failedRequests: evidence.failed_requests,
    pageIssues: evidence.page_issues || [],
    pageHealth: evidence.page_health ? {
      readyState: evidence.page_health.ready_state,
      visibleTextLength: evidence.page_health.visible_text_length,
      visibleElementCount: evidence.page_health.visible_element_count,
      interactiveCount: evidence.page_health.interactive_count,
      visualSurfaceCount: evidence.page_health.visual_surface_count
    } : undefined,
    capturedAt: evidence.captured_at || ''
  };
}

function toRun(payload: BackendRun): TestRun {
  const reviewSummary = payload.review_summary || { disposition: 'no_findings' as const, pending: 0, confirmed: 0, rejected: 0, total: 0 };
  const displayStatus: RunStatus = payload.status === 'pending_confirmation'
    ? 'pending_confirmation'
    : reviewSummary.disposition === 'pending_confirmation'
    ? 'pending_review'
    : reviewSummary.disposition === 'issues_found' ? 'issues_found' : payload.status;
  const steps = payload.steps.map((step) => ({
    id: `${payload.run_id}-step-${step.index}`,
    order: step.index,
    action: step.description || step.action,
    target: step.target_summary,
    result: step.status,
    durationMs: durationMs(step.started_at, step.ended_at),
    errorType: step.error_message || step.failure_category,
    evidence: artifactUrl(payload, step.screenshot),
    before: toEvidence(payload, step.before),
    after: toEvidence(payload, step.after),
    executionMode: step.execution_mode || 'locator',
    stabilityLevel: step.stability_level || 'A',
    stabilityReason: step.stability_reason || '确定性 Playwright 动作',
    plannerReason: step.planner_reason,
    progressAssessment: step.progress_assessment,
    computerUseTriggered: step.computer_use_triggered || false,
    computerUseReason: step.computer_use_reason,
    coordinateSource: step.coordinate_source,
    appBridgeResult: step.app_bridge_result,
    stabilityEvidence: step.stability_evidence,
    canvasEvidence: step.canvas_evidence,
    fileEvidence: step.file_evidence ? {
      ...step.file_evidence,
      artifact: artifactUrl(payload, typeof step.file_evidence.artifact === 'string' ? step.file_evidence.artifact : undefined)
    } : undefined,
    asyncEvidence: step.async_evidence,
    sideEffectEvidence: step.side_effect_evidence,
    componentEvidence: step.component_evidence
  }));
  return {
    id: payload.run_id,
    caseName: payload.plan_name,
    role: payload.role || '未指定',
    startedAt: new Date(payload.started_at).toLocaleString('zh-CN', { hour12: false }),
    status: displayStatus,
    executionStatus: payload.status,
    steps,
    assertions: payload.assertions.map((item) => ({
      name: item.description || item.type,
      passed: item.status === 'passed',
      message: item.error_message || item.actual_summary || item.expected_summary || item.status,
      evidence: artifactUrl(payload, item.screenshot),
      semanticEvidence: item.semantic_evidence
    })),
    reproduction: payload.reproduction_steps,
    heuristicReasons: payload.cause_hints.map((item) => `${item.message}（${item.category}，置信度 ${item.confidence}）`),
    findings: (payload.findings || []).map((item) => ({
      id: item.id, title: item.title, category: item.category, severity: item.severity, confidence: item.confidence,
      actualResult: item.actual_result, expectedResult: item.expected_result, facts: item.facts, inference: item.inference,
      evidence: item.evidence.map((path) => artifactUrl(payload, path) || ''), reproductionSteps: item.reproduction_steps,
      evidenceTimeline: (item.evidence_timeline || []).map((event) => ({
        phase: event.phase,
        timestamp: event.timestamp,
        screenshot: artifactUrl(payload, event.screenshot),
        facts: event.facts
      })),
      reviewStatus: item.review_status,
      reviewHistory: (item.review_history || []).map((entry) => ({
        timestamp: entry.timestamp,
        actor: entry.actor,
        changedFields: entry.changedFields || [],
        changes: entry.changes
      }))
    })),
    generatedTest: payload.generated_test ? {
      sourcePath: `${API_BASE}/api/runs/${encodeURIComponent(payload.run_id)}/generated-test`,
      source: payload.generated_test.source,
      stabilityLevel: payload.generated_test.stability_level,
      supportedReplayModes: payload.generated_test.supported_replay_modes,
      ciEligible: payload.generated_test.ci_eligible,
      ciRecommendation: payload.generated_test.ci_recommendation,
      manualSteps: payload.generated_test.manual_steps || [],
      sourceRevision: payload.generated_test.source_revision || 1,
      sourceReviewHistory: payload.generated_test.source_review_history || []
    } : undefined,
    replayMode: payload.replay_mode || 'exploration',
    onboardingLevel: payload.onboarding_level,
    stabilityLevel: payload.stability_level || 'A',
    completionReason: payload.completion_reason || 'plan_completed',
    projectId: payload.project_id,
    environmentId: payload.environment_id,
    environmentUpdatedAt: payload.environment_updated_at,
    artifactRetentionDays: payload.artifact_retention_days || 30,
    runnerIsolation: payload.runner_isolation ? {
      mode: payload.runner_isolation.mode,
      processId: payload.runner_isolation.process_id,
      windowsJobAssigned: Boolean(payload.runner_isolation.windows_job_assigned),
      memoryLimitMb: payload.runner_isolation.memory_limit_mb,
      networkPolicy: payload.runner_isolation.network_policy || 'playwright_request_guard',
      forcedTermination: Boolean(payload.runner_isolation.forced_termination),
      containerName: payload.runner_isolation.container_name,
      image: payload.runner_isolation.image,
      rootFilesystemReadOnly: payload.runner_isolation.root_filesystem_read_only,
      artifactMount: payload.runner_isolation.artifact_mount,
      tmpfsMb: payload.runner_isolation.tmpfs_mb,
      cpuLimit: payload.runner_isolation.cpu_limit,
      pidsLimit: payload.runner_isolation.pids_limit,
      capabilitiesDropped: payload.runner_isolation.capabilities_dropped,
      noNewPrivileges: payload.runner_isolation.no_new_privileges,
      containerNetworkMode: payload.runner_isolation.container_network_mode,
      containerPrivateNetworkAllowed: payload.runner_isolation.container_private_network_allowed
    } : undefined,
    websocketTimeline: payload.websocket_timeline || [],
    cleanupReport: payload.cleanup_report,
    businessContextSnapshot: payload.business_context_snapshot,
    evidenceManifest: payload.evidence_manifest,
    evidenceCompleteness: payload.evidence_completeness || 0,
    evidenceManifestPath: artifactUrl(payload, payload.evidence_manifest_path),
    durationMs: payload.duration_ms ?? durationMs(payload.started_at, payload.ended_at),
    scenarioGoal: payload.scenario_goal || payload.plan_name,
    goalStatus: payload.goal_status || (payload.status === 'passed' ? 'achieved' : 'not_achieved'),
    goalSummary: payload.goal_summary || `结束原因 ${payload.completion_reason || payload.status}`,
    reviewSummary,
    reportJsonPath: `${API_BASE}/api/runs/${encodeURIComponent(payload.run_id)}/report.json`,
    reportHtmlPath: `${API_BASE}/api/runs/${encodeURIComponent(payload.run_id)}/report.html`,
    modelCalls: payload.model_calls || 0,
    estimatedCost: payload.estimated_cost,
    inputTokens: payload.input_tokens || 0,
    outputTokens: payload.output_tokens || 0,
    modelCallRecords: (payload.model_call_records || []).map((item) => ({
      index: item.index, model: item.model, protocol: item.protocol, elapsedMs: item.elapsed_ms,
      inputTokens: item.input_tokens, outputTokens: item.output_tokens, estimatedCost: item.estimated_cost,
      decision: item.decision, reason: item.reason
    })),
    pendingConfirmation: payload.pending_confirmation ? {
      id: payload.pending_confirmation.id,
      stepIndex: payload.pending_confirmation.step_index,
      action: payload.pending_confirmation.action,
      target: payload.pending_confirmation.target,
      rule: payload.pending_confirmation.rule,
      requestedAt: payload.pending_confirmation.requested_at
    } : undefined,
    confirmationHistory: (payload.confirmation_history || []).map((item) => ({
      id: item.id, stepIndex: item.step_index, action: item.action, target: item.target,
      rule: item.rule, requestedAt: item.requested_at, decision: item.decision,
      actor: item.actor, decidedAt: item.decided_at
    }))
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
  getAcceptanceCatalog: () => request<AcceptanceCatalog>('/api/acceptance/scenarios'),
  getL4Workflow: () => request<L4Workflow>('/api/acceptance/l4-workflow'),
  getAcceptanceBatches: () => request<AcceptanceBatch[]>('/api/acceptance/batches'),
  getAcceptanceBatch: (batchId: string) => request<AcceptanceBatch>(`/api/acceptance/batches/${encodeURIComponent(batchId)}`),
  startAcceptanceBatch: (payload: { dryRun: boolean; projectId?: string; environmentId?: string; scenarioBindings?: Record<string, unknown> }) => request<AcceptanceBatch>('/api/acceptance/batches', { method: 'POST', body: JSON.stringify(payload) }),
  cancelAcceptanceBatch: (batchId: string) => request<AcceptanceBatch>(`/api/acceptance/batches/${encodeURIComponent(batchId)}/cancel`, { method: 'POST' }),
  resumeAcceptanceBatch: (batchId: string) => request<AcceptanceBatch>(`/api/acceptance/batches/${encodeURIComponent(batchId)}/resume`, { method: 'POST' }),
  retryAcceptanceBatch: (batchId: string) => request<AcceptanceBatch>(`/api/acceptance/batches/${encodeURIComponent(batchId)}/retry-failed`, { method: 'POST' }),
  startL4Run: (payload: { dryRun: boolean; projectId?: string; environmentId?: string; stageBindings?: Record<string, unknown> }) => request<L4RunResult>('/api/acceptance/l4-runs', { method: 'POST', body: JSON.stringify(payload) }),
  getProjects: () => request<ProjectConfig[]>('/api/projects'),
  createProject: (project: ProjectDraft) => request<ProjectConfig>('/api/projects', {
    method: 'POST', body: JSON.stringify(project)
  }),
  updateProject: (projectId: string, project: ProjectDraft) => request<ProjectConfig>(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: 'PUT', body: JSON.stringify(project)
  }),
  scanProject: (projectId: string, mode: 'read_only' | 'low_risk' = 'read_only', accountId = 'default') => request<CompatibilityReport>(`/api/projects/${encodeURIComponent(projectId)}/scan`, {
    method: 'POST', body: JSON.stringify({ headless: true, timeoutMs: 30_000, mode, accountId })
  }),
  getCompatibility: (projectId: string) => request<CompatibilityReport>(`/api/projects/${encodeURIComponent(projectId)}/compatibility`),
  getAppMap: (projectId: string) => request<CompatibilityReport['appMap']>(`/api/projects/${encodeURIComponent(projectId)}/app-map`),
  getBusinessContextStatus: (projectId: string) => request<BusinessContextStatus>(`/api/projects/${encodeURIComponent(projectId)}/business-context-status`),
  getTestFiles: (projectId: string) => request<TestFileRecord[]>(`/api/projects/${encodeURIComponent(projectId)}/test-files`),
  registerTestFile: (projectId: string, file: File, expectedResult: string, validationProfile: string) => request<TestFileRecord>(
    `/api/projects/${encodeURIComponent(projectId)}/test-files?fileName=${encodeURIComponent(file.name)}&expectedResult=${encodeURIComponent(expectedResult)}&validationProfile=${encodeURIComponent(validationProfile)}`,
    { method: 'POST', body: file, headers: { 'Content-Type': file.type || 'application/octet-stream' } }
  ),
  deleteTestFile: (projectId: string, fileId: string) => request<{ deleted: boolean; id: string }>(`/api/projects/${encodeURIComponent(projectId)}/test-files/${encodeURIComponent(fileId)}`, { method: 'DELETE' }),
  getEnvironments: (projectId: string) => request<EnvironmentConfig[]>(`/api/projects/${encodeURIComponent(projectId)}/environments`),
  createEnvironment: (projectId: string, environment: EnvironmentDraft) => request<EnvironmentConfig>(`/api/projects/${encodeURIComponent(projectId)}/environments`, {
    method: 'POST', body: JSON.stringify(environment)
  }),
  updateEnvironment: (projectId: string, environmentId: string, environment: EnvironmentDraft) => request<EnvironmentConfig>(`/api/projects/${encodeURIComponent(projectId)}/environments/${encodeURIComponent(environmentId)}`, {
    method: 'PUT', body: JSON.stringify(environment)
  }),
  getScenarios: (projectId: string) => request<ScenarioConfig[]>(`/api/projects/${encodeURIComponent(projectId)}/scenarios`),
  createScenario: (projectId: string, scenario: ScenarioDraft) => request<ScenarioConfig>(`/api/projects/${encodeURIComponent(projectId)}/scenarios`, {
    method: 'POST', body: JSON.stringify(scenario)
  }),
  updateScenario: (projectId: string, scenarioId: string, scenario: ScenarioDraft) => request<ScenarioConfig>(`/api/projects/${encodeURIComponent(projectId)}/scenarios/${encodeURIComponent(scenarioId)}`, {
    method: 'PUT', body: JSON.stringify(scenario)
  }),
  importSession: (projectId: string, storageState: Record<string, unknown>, accountId = 'default') => request<SessionMetadata>(`/api/projects/${encodeURIComponent(projectId)}/session`, {
    method: 'POST', body: JSON.stringify({ storageState, accountId })
  }),
  getSession: (projectId: string, accountId = 'default') => request<SessionMetadata>(`/api/projects/${encodeURIComponent(projectId)}/session?accountId=${encodeURIComponent(accountId)}`),
  getSessions: (projectId: string) => request<SessionMetadata[]>(`/api/projects/${encodeURIComponent(projectId)}/sessions`),
  deleteSession: (projectId: string, accountId: string) => request<{ deleted: boolean; accountId: string }>(`/api/projects/${encodeURIComponent(projectId)}/session?accountId=${encodeURIComponent(accountId)}`, { method: 'DELETE' }),
  startSessionRecording: (projectId: string, timeoutSeconds: number, accountId = 'default') => request<SessionRecording>(`/api/projects/${encodeURIComponent(projectId)}/session-recordings`, {
    method: 'POST', body: JSON.stringify({ timeoutSeconds, accountId })
  }),
  completeSessionRecording: (projectId: string, recordingId: string) => request<SessionRecording>(`/api/projects/${encodeURIComponent(projectId)}/session-recordings/${encodeURIComponent(recordingId)}/complete`, {
    method: 'POST', body: JSON.stringify({})
  }),
  cancelSessionRecording: (projectId: string, recordingId: string) => request<SessionRecording>(`/api/projects/${encodeURIComponent(projectId)}/session-recordings/${encodeURIComponent(recordingId)}`, {
    method: 'DELETE'
  }),
  generatePlan: (draft: TestCaseDraft, projectId?: string, environmentId?: string) => request<PlanGeneration>('/api/plans/generate', {
    method: 'POST',
    body: JSON.stringify({ ...draft, projectId, environmentId })
  }),
  testAI: (settings: AISettings) => request<AIConnectionResult>('/api/ai/test', {
    method: 'POST',
    body: JSON.stringify({ settings })
  }),
  generateAIPlan: (draft: TestCaseDraft, settings: AISettings, projectId?: string, environmentId?: string) => request<PlanGeneration>('/api/ai/plans/generate', {
    method: 'POST',
    body: JSON.stringify({ draft, settings, projectId, environmentId })
  }),
  async reviewPlan(plan: TestPlan): Promise<TestPlan> {
    const result = await request<{ valid: boolean; plan: TestPlan }>('/api/plans/validate', {
      method: 'POST',
      body: JSON.stringify({ plan })
    });
    return result.plan;
  },
  async startRun(plan: TestPlan, projectId?: string, scenarioId?: string, environmentId?: string, accountId = 'default'): Promise<TestRun> {
    return toRun(await request<BackendRun>('/api/runs', {
      method: 'POST',
      body: JSON.stringify({ plan, headless: true, timeoutMs: 30_000, projectId, scenarioId, environmentId, accountId, asyncExecution: true })
    }));
  },
  async startAgentRun(plan: TestPlan, draft: TestCaseDraft, settings: AISettings, projectId?: string, scenarioId?: string, environmentId?: string, enableVisualFallback = false, accountId = 'default'): Promise<TestRun> {
    return toRun(await request<BackendRun>('/api/agent-runs', {
      method: 'POST',
      body: JSON.stringify({
        plan,
        scenario: {
          name: draft.name,
          goal: draft.flow,
          preconditions: draft.preconditions,
          testData: draft.testData,
          expectedResults: draft.expectation.trim() ? [draft.expectation.trim()] : [],
          forbiddenActions: draft.forbiddenActions
        },
        settings,
        headless: true,
        timeoutMs: 30_000,
        projectId,
        scenarioId,
        environmentId,
        accountId,
        enableVisualFallback
      })
    }));
  },
  async getHistory(): Promise<TestRun[]> {
    return (await request<BackendRun[]>('/api/runs')).map(toRun);
  },
  deleteRuns: (runIds: string[]) => request<{ deleted: string[]; count: number; auditId: string }>('/api/runs/delete', {
    method: 'POST', body: JSON.stringify({ runIds, actor: 'local_user' })
  }),
  cleanupRuns: () => request<{ deleted: string[]; count: number; skippedActive: string[]; auditId: string | null }>('/api/runs/cleanup', {
    method: 'POST', body: JSON.stringify({ actor: 'local_user' })
  }),
  deletionAuditUrl: `${API_BASE}/api/runs/deletion-audit`,
  async getRun(runId: string): Promise<TestRun> {
    return toRun(await request<BackendRun>(`/api/runs/${encodeURIComponent(runId)}`));
  },
  async cancelRun(runId: string): Promise<TestRun> {
    return toRun(await request<BackendRun>(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST', body: JSON.stringify({})
    }));
  },
  async decideConfirmation(runId: string, confirmationId: string, decision: 'approved' | 'rejected'): Promise<TestRun> {
    return toRun(await request<BackendRun>(`/api/runs/${encodeURIComponent(runId)}/confirmation`, {
      method: 'POST', body: JSON.stringify({ confirmationId, decision, actor: 'local_user' })
    }));
  },
  async getReport(runId: string): Promise<Report> {
    const [run, pathReview] = await Promise.all([api.getRun(runId), api.getRunReview(runId)]);
    return { ...toReport(run), pathReview };
  },
  async reviewFinding(runId: string, findingId: string, payload: {
    status: 'pending_review' | 'confirmed' | 'rejected'; title: string; severity: 'Blocker' | 'High' | 'Medium' | 'Low'; expectedResult: string;
  }) {
    await request(`/api/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(findingId)}`, {
      method: 'PATCH', body: JSON.stringify(payload)
    });
    return api.getReport(runId);
  },
  async getRunReview(runId: string): Promise<RunPathReview> {
    return request<RunPathReview>(`/api/runs/${encodeURIComponent(runId)}/review`);
  },
  async saveRunReview(runId: string, steps: ReviewedStep[]): Promise<Report> {
    await request<RunPathReview>(`/api/runs/${encodeURIComponent(runId)}/review`, {
      method: 'PATCH', body: JSON.stringify({ steps })
    });
    return api.getReport(runId);
  },
  async saveGeneratedTestSource(runId: string, source: string): Promise<Report> {
    await request(`/api/runs/${encodeURIComponent(runId)}/generated-test`, {
      method: 'PATCH', body: JSON.stringify({ source })
    });
    return api.getReport(runId);
  },
  async replay(runId: string, mode: 'stable' | 'adaptive', settings?: AISettings): Promise<TestRun> {
    return toRun(await request<BackendRun>(`/api/runs/${encodeURIComponent(runId)}/replay`, {
      method: 'POST', body: JSON.stringify({ mode, headless: true, ...(mode === 'adaptive' ? { settings } : {}) })
    }));
  }
};
