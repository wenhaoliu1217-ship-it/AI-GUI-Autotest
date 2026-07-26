export type RunStatus = 'queued' | 'running' | 'pending_confirmation' | 'passed' | 'issues_found' | 'incomplete' | 'system_error' | 'cancelled' | 'failed' | 'error' | 'pending_review' | 'stopped' | 'skipped';

export interface TestCaseDraft {
  name: string;
  targetUrl: string;
  flow: string;
  role: string;
  preconditions: string;
  expectation: string;
  testData: Record<string, string | number | boolean | null>;
  forbiddenActions: string[];
}

export type ActionType = 'navigate' | 'click' | 'fill' | 'select' | 'wait_for' | 'wait_for_state' | 'component' | 'screenshot' | 'clear' | 'check' | 'uncheck' | 'hover' | 'scroll' | 'back' | 'reload' | 'press' | 'visual_click' | 'visual_hover' | 'visual_scroll' | 'visual_drag' | 'visual_zoom' | 'visual_clear' | 'visual_draw_polygon' | 'visual_draw_rectangle' | 'bridge_click' | 'upload' | 'download';
export type AssertionType = 'page_reached' | 'visible' | 'not_visible' | 'text_contains' | 'url_contains' | 'value_equals' | 'count_equals' | 'canvas_layer_visible' | 'canvas_camera_equals' | 'canvas_entity_count' | 'canvas_selected_entity' | 'canvas_path_point_count' | 'canvas_poi_count' | 'canvas_fence_count' | 'canvas_drawing_count' | 'canvas_tiles_loaded' | 'canvas_webgl_no_error';

export interface Locator {
  role?: string;
  name?: string;
  label?: string;
  placeholder?: string;
  test_id?: string;
  attribute?: { name: 'data-test' | 'data-qa' | 'data-cy' | 'data-object-id'; value: string };
  css?: string;
  text?: string;
  scope?: {
    kind: 'row' | 'card' | 'dialog' | 'tab_panel' | 'canvas';
    locator: Locator;
    identity?: string;
  };
}

export interface PlanStep {
  action: ActionType;
  target?: string;
  locator?: Locator;
  value?: string;
  value_from_secret?: string;
  description?: string;
  execution_mode?: 'locator' | 'visual' | 'app_bridge';
  stability_level?: 'A' | 'B' | 'C' | 'D';
  stability_reason?: string;
  visual_target?: string;
  relative_position?: { xRatio?: number; yRatio?: number; x_ratio?: number; y_ratio?: number };
  relative_end_position?: { xRatio?: number; yRatio?: number; x_ratio?: number; y_ratio?: number };
  visual_points?: Array<{ xRatio?: number; yRatio?: number; x_ratio?: number; y_ratio?: number }>;
  canvas_region_locator?: Locator;
  zoom_delta?: number;
  gesture_finish?: 'double_click' | 'enter' | 'none';
  bridge_target_id?: string;
  computer_use_triggered?: boolean;
  computer_use_reason?: string;
  scroll_delta_y?: number;
  file_id?: string;
  expected_file_validity?: 'valid' | 'invalid';
  business_object_name?: string;
  download_validation?: {
    extension?: string;
    filenamePattern?: string;
    minimumSize?: number;
    sha256?: string;
    format: 'binary' | 'json' | 'zip' | 'text' | 'csv';
    requiredJsonKeys?: string[];
  };
  residual_object_locator?: Locator;
  expected_residual_count?: number;
  state_machine_id?: string;
  business_object_id?: string;
  action_category?: string;
  object_type?: string;
  precondition_state?: string;
  cleanup_required?: boolean;
  component?: ComponentAction;
  component_adapter_id?: string;
}

export interface ComponentAction {
  kind: 'cascade_select' | 'searchable_select' | 'date_time_range' | 'pagination' | 'statistics_card' | 'tab' | 'upload_dialog' | 'image_preview' | 'local_scroll';
  semanticTarget: string;
  locators: Locator[];
  values: string[];
  expectedText?: string;
  fileId?: string;
  scrollDeltaY?: number;
}

export interface PlanAssertion {
  type: AssertionType;
  locator?: Locator;
  expected?: string;
  count?: number;
  description?: string;
  tolerance?: number;
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
  inputCostPerMillion?: number;
  outputCostPerMillion?: number;
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
  before?: StepEvidence;
  after?: StepEvidence;
  executionMode: 'locator' | 'visual' | 'app_bridge';
  stabilityLevel: 'A' | 'B' | 'C' | 'D';
  stabilityReason: string;
  plannerReason?: string;
  progressAssessment?: string;
  computerUseTriggered: boolean;
  computerUseReason?: string;
  coordinateSource?: string;
  appBridgeResult?: Record<string, unknown>;
  stabilityEvidence?: Record<string, unknown>;
  canvasEvidence?: Record<string, unknown>;
  fileEvidence?: Record<string, unknown>;
  asyncEvidence?: Record<string, unknown>;
  sideEffectEvidence?: Record<string, unknown>;
  componentEvidence?: Record<string, unknown>;
}

export interface StepEvidence {
  url: string;
  title: string;
  screenshot?: string;
  domSummary: string[];
  accessibilitySummary: string;
  consoleErrors: string[];
  pageErrors: string[];
  failedRequests: string[];
  pageIssues: Array<{ kind: string; severity: string; confidence: string; message: string; target: string }>;
  pageHealth?: { readyState: string; visibleTextLength: number; visibleElementCount: number; interactiveCount: number; visualSurfaceCount: number };
  capturedAt: string;
}

export interface TestRun {
  id: string;
  caseName: string;
  role: string;
  startedAt: string;
  status: RunStatus;
  executionStatus: RunStatus;
  steps: RunStep[];
  assertions: Array<{ name: string; passed: boolean; message: string; evidence?: string; semanticEvidence?: Record<string, unknown> }>;
  reproduction: string[];
  heuristicReasons: string[];
  findings: Finding[];
  generatedTest?: GeneratedTest;
  replayMode: 'exploration' | 'stable' | 'adaptive';
  onboardingLevel?: 'L0' | 'L1' | 'L2' | 'L3';
  stabilityLevel: 'A' | 'B' | 'C' | 'D';
  completionReason: string;
  projectId?: string;
  environmentId?: string;
  environmentUpdatedAt?: string;
  artifactRetentionDays: number;
  runnerIsolation?: {
    mode: string;
    processId?: number;
    windowsJobAssigned: boolean;
    memoryLimitMb?: number;
    networkPolicy: string;
    forcedTermination: boolean;
    containerName?: string;
    image?: string;
    rootFilesystemReadOnly?: boolean;
    artifactMount?: string;
    tmpfsMb?: number;
    cpuLimit?: number;
    pidsLimit?: number;
    capabilitiesDropped?: string;
    noNewPrivileges?: boolean;
    containerNetworkMode?: string;
    containerPrivateNetworkAllowed?: boolean;
  };
  websocketTimeline: Array<Record<string, unknown>>;
  cleanupReport?: Record<string, unknown>;
  businessContextSnapshot?: Record<string, unknown>;
  evidenceManifest?: {
    schemaVersion: string;
    runId: string;
    applicableCount: number;
    presentCount: number;
    missingCount: number;
    completeness: number;
    items: Array<{
      id: string;
      label: string;
      status: 'present' | 'missing' | 'not_applicable';
      reason: string;
      artifacts: string[];
      runId: string;
      stepIds: string[];
      businessIds: string[];
    }>;
  };
  evidenceCompleteness: number;
  evidenceManifestPath?: string;
  durationMs: number;
  scenarioGoal: string;
  goalStatus: 'in_progress' | 'achieved' | 'not_achieved' | 'incomplete';
  goalSummary: string;
  reviewSummary: {
    disposition: 'pending_confirmation' | 'issues_found' | 'all_rejected' | 'no_findings';
    pending: number;
    confirmed: number;
    rejected: number;
    total: number;
  };
  reportJsonPath: string;
  reportHtmlPath: string;
  modelCalls: number;
  estimatedCost?: number;
  inputTokens: number;
  outputTokens: number;
  modelCallRecords: Array<{
    index: number; model: string; protocol: string; elapsedMs: number;
    inputTokens: number; outputTokens: number; estimatedCost?: number;
    decision: string; reason: string;
  }>;
  pendingConfirmation?: {
    id: string; stepIndex: number; action: string; target: string; rule: string; requestedAt: string;
  };
  confirmationHistory: Array<{
    id: string; stepIndex: number; action: string; target: string; rule: string;
    requestedAt: string; decision: 'approved' | 'rejected'; actor: string; decidedAt: string;
  }>;
}

export interface Finding {
  id: string;
  title: string;
  category: string;
  severity: 'Blocker' | 'High' | 'Medium' | 'Low';
  confidence: string;
  actualResult: string;
  expectedResult: string;
  facts: string[];
  inference: string;
  evidence: string[];
  evidenceTimeline: Array<{ phase: string; timestamp: string; screenshot?: string; facts: string[] }>;
  reproductionSteps: string[];
  reviewStatus: 'pending_review' | 'confirmed' | 'rejected';
  reviewHistory: Array<{
    timestamp: string;
    actor?: string;
    changedFields: string[];
    changes?: Record<string, { before: unknown; after: unknown }>;
  }>;
}

export interface ReviewedStep {
  sourceIndex: number;
  retained: boolean;
  step: PlanStep;
}

export interface RunPathReview {
  available: boolean;
  reason?: string;
  steps: ReviewedStep[];
  history: Array<{
    timestamp: string;
    actor: string;
    changes: Array<{ sourceIndex: number; action: 'edited' | 'removed' | 'restored' }>;
    retainedSourceIndexes: number[];
  }>;
}

export interface GeneratedTest {
  sourcePath: string;
  source: string;
  stabilityLevel: 'A' | 'B' | 'C' | 'D';
  supportedReplayModes: Array<'stable' | 'adaptive'>;
  ciEligible: boolean;
  ciRecommendation: string;
  manualSteps: string[];
  sourceRevision: number;
  sourceReviewHistory: Array<{
    timestamp: string;
    actor: string;
    action: 'manual_source_edit' | 'regenerated_from_path_review';
    revision: number;
    beforeSha256: string;
    afterSha256: string;
  }>;
}

export interface Report {
  run: TestRun;
  assertions: TestRun['assertions'];
  failedStep?: RunStep;
  reproduction: string[];
  heuristicReasons: string[];
  pathReview?: RunPathReview;
}

export interface HealthStatus {
  status: 'ok';
  mode: 'real';
  engine: string;
  planner: string;
  aiConfigStorage?: string;
}

export interface ProjectLimits {
  maxSteps: number;
  timeoutSeconds: number;
  maxModelCalls: number;
}

export interface AcceptanceCatalog {
  schemaVersion: string;
  scenarioCount: number;
  repeatCount: number;
  plannedRuns: number;
  readyCount: number;
  blockedCount: number;
  blockedDependencies: string[];
  scenarios: Array<{
    id: string;
    name: string;
    category: string;
    environmentRef: string;
    accountRole: string;
    goal: string;
    bindingStatus: 'ready' | 'blocked';
    blockedDependencies: string[];
    evidenceRequirements: string[];
  }>;
}

export interface AcceptanceBatch {
  schemaVersion: string;
  batchId: string;
  status: 'queued' | 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed';
  dryRun: boolean;
  createdAt: string;
  updatedAt: string;
  plannedRuns: number;
  completedRuns: number;
  currentScenarioId?: string | null;
  currentRepeat?: number | null;
  cancelRequested: boolean;
  summaryAvailable: boolean;
  error?: string;
  attempts: Array<{ scenarioId: string; repeat: number; status: string; runId?: string | null; completionReason?: string | null }>;
}

export interface L4Workflow {
  id: string;
  name: string;
  bindingStatus: 'ready' | 'blocked';
  stages: Array<{ id: string; goal: string; dependsOn?: string[]; requiredOutputs: string[] }>;
  successRule: string;
  blockedDependencies: string[];
}

export interface L4RunResult {
  runId: string;
  status: 'passed' | 'failed' | 'unverified';
  goalStatus: 'achieved' | 'incomplete';
  verificationStatus: 'executed' | 'dry_run_only';
  cleanupSuccess: boolean;
  failedStage?: string | null;
  stateTimeline: Array<{ at: string; stageId: string; status: string; detail: string }>;
  manualCleanupActions: Array<{ stageId: string; reason: string }>;
  reportUrls: { json: string; markdown: string };
}

export interface BusinessContext {
  description: string;
  terminology: Record<string, string>;
  objectTypes: string[];
  stateModels: Record<string, string[]>;
  exampleGoals: string[];
  operatingBoundaries: string[];
  allowedActions: string[];
  bridgeCapabilities: string[];
  bridgeSemanticTargets: Record<string, string>;
  facts: BusinessFact[];
  objectRelations: ObjectRelation[];
  missingFacts: string[];
  sourceRevision: string;
}

export interface BusinessFact { id: string; category: 'term' | 'object' | 'state' | 'operation' | 'permission' | 'bridge' | 'constraint'; statement: string; source: string; status: 'confirmed' | 'blocked' }
export interface ObjectRelation { sourceObject: string; relation: string; targetObject: string; source: string; status: 'confirmed' | 'blocked' }

export interface AccountProfile {
  id: string;
  name: string;
  role: string;
  loginMethod: 'interactive' | 'credentials';
  credentialRefs: Partial<Record<'tenant' | 'username' | 'password', string>>;
  permissions: string[];
}

export interface ProjectConfig {
  id: string;
  name: string;
  baseUrl: string;
  allowedHosts: string[];
  forbiddenActions: string[];
  allowPrivateNetwork: boolean;
  businessContext: BusinessContext;
  asyncStateMachines: AsyncStateMachine[];
  sideEffectPolicies: SideEffectPolicy[];
  componentAdapters: ComponentAdapter[];
  accountProfiles: AccountProfile[];
  onboardingLevel: 'L0' | 'L1' | 'L2' | 'L3';
  limits: ProjectLimits;
  createdAt: string;
  updatedAt: string;
}

export type ProjectDraft = Omit<ProjectConfig, 'id' | 'createdAt' | 'updatedAt'>;

export interface AsyncStateMachine {
  id: string; name: string; states: string[]; terminalStates: string[]; failureStates: string[];
  transitions: Record<string, string[]>; pollingIntervalMs: number; timeoutMs: number; websocketEvents: string[];
}

export interface SideEffectPolicy {
  id: string; actionCategory: string; objectType: string; namePattern: string;
  environmentId?: string; role?: string; preconditionState?: string;
  decision: 'allow' | 'confirm' | 'conditional' | 'forbid'; rollbackRule: string;
}

export interface ComponentAdapter {
  id: string;
  module: 'login' | 'navigation' | 'agent' | 'environment_asset' | 'elevation' | 'scenario' | 'run' | 'reinforcement_learning' | 'help';
  page: string;
  action: ComponentAction;
  status: 'configured' | 'blocked';
  source: string;
  blockedReason: string;
}

export interface BusinessContextStatus extends BusinessContext {
  componentAdapters: ComponentAdapter[];
  blockedItems: string[];
  status: 'ready' | 'blocked';
  confirmedCount: number;
  totalCount: number;
  completeness: number;
}

export interface TestFileRecord {
  id: string;
  projectId: string;
  fileName: string;
  size: number;
  sha256: string;
  mimeType: string;
  extension: string;
  validationProfile: string;
  validationStatus: 'valid' | 'invalid';
  validationErrors: string[];
  expectedResult: string;
  createdAt: string;
}

export interface EnvironmentConfig {
  id: string;
  projectId: string;
  name: string;
  variables: Record<string, string>;
  secretRefs: Record<string, string>;
  ignoreRules: string[];
  screenshotMaskSelectors: string[];
  viewport: { width: number; height: number };
  deviceScaleFactor: number;
  appBridge: { enabled: boolean; globalName: string; adapter: 'generic' | 'cesium' | 'gaealavic_cesium' };
  artifactRetentionDays: number;
  createdAt: string;
  updatedAt: string;
}

export type EnvironmentDraft = Omit<EnvironmentConfig, 'id' | 'projectId' | 'createdAt' | 'updatedAt'>;

export interface ScenarioConfig {
  id: string;
  projectId: string;
  name: string;
  preconditions: string[];
  goal: string;
  testData: Record<string, string | number | boolean | null>;
  expectedResults: string[];
  forbiddenActions: string[];
  businessObjects: BusinessObjectLifecycle[];
  createdAt: string;
  updatedAt: string;
}

export type ScenarioDraft = Omit<ScenarioConfig, 'id' | 'projectId' | 'createdAt' | 'updatedAt'>;

export interface BusinessObjectLifecycle {
  key: string; objectType: string; name: string; businessId?: string; dependencies: string[];
  reuse: boolean; cleanupStep: PlanStep; verificationLocator?: Locator; manualFallback: string;
}

export interface CompatibilityReport {
  projectId: string;
  generatedAt: string;
  onboardingLevel: 'L0' | 'L1' | 'L2' | 'L3';
  requestedUrl: string;
  finalUrl: string;
  title: string;
  status: 'compatible' | 'attention';
  pageSummary: Record<string, number>;
  candidateLocators: Record<string, number>;
  capabilities: string[];
  thirdPartyHosts: string[];
  consoleErrors: string[];
  failedRequests: string[];
  blockedAreas: string[];
  recommendations: string[];
  suggestedScenarios: string[];
  recommendedOnboardingLevel: 'L0' | 'L1' | 'L2' | 'L3';
  scannedPages: Array<{
    url: string;
    title: string;
    pageType: string;
    summary: Record<string, number>;
    candidateLocators: Record<string, number>;
    headings: string[];
    redirectChain: string[];
  }>;
  navigationEntries: string[];
  authenticationSignals: string[];
  asyncPatterns: string[];
  stableAreas: string[];
  visualAreas: string[];
  adaptiveAreas: string[];
  manualAreas: string[];
  recommendedConfig: {
    allowedHosts: string[];
    ignoreRules: string[];
    viewport: { width: number; height: number };
    limits: ProjectLimits;
  };
  sampleScenarioId?: string | null;
  sampleScenarioCreated: boolean;
  scanMode: 'read_only' | 'low_risk';
  appMap: {
    version: string;
    projectId: string;
    generatedAt: string;
    scanMode: 'read_only' | 'low_risk';
    pages: Array<Record<string, unknown> & { id: string; url: string; title: string; probes: Array<Record<string, unknown>> }>;
  };
}

export interface SessionMetadata {
  projectId: string;
  importedAt: string;
  cookieCount: number;
  originCount: number;
  domains: string[];
  expiresAt?: string | null;
  expiryStatus: 'active' | 'warning' | 'expired' | 'unknown';
  expiredCookieCount: number;
  encryption: string;
  accountId: string;
  accountName: string;
  accountRole: string;
}

export interface SessionRecording {
  id: string;
  projectId: string;
  accountId?: string;
  status: 'starting' | 'recording' | 'saving' | 'completed' | 'cancelled' | 'error';
  session?: SessionMetadata;
}
