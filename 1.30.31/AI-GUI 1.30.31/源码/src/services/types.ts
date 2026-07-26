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

export type ActionType = 'navigate' | 'click' | 'fill' | 'select' | 'wait_for' | 'screenshot' | 'clear' | 'check' | 'uncheck' | 'hover' | 'scroll' | 'back' | 'reload' | 'press' | 'visual_click' | 'visual_hover' | 'visual_scroll' | 'visual_drag' | 'bridge_click' | 'human_takeover' | 'upload_file' | 'download';
export type AssertionType = 'page_reached' | 'visible' | 'not_visible' | 'text_contains' | 'url_contains' | 'value_equals' | 'count_equals';

export interface Locator {
  role?: string;
  name?: string;
  label?: string;
  test_id?: string;
  css?: string;
  text?: string;
}

export type CommerceAction = 'browse' | 'search' | 'filter' | 'sort' | 'paginate' | 'view_product' | 'view_account_structure' | 'view_help' | 'change_region' | 'add_cart' | 'remove_cart' | 'favorite' | 'unfavorite' | 'follow' | 'unfollow' | 'claim_coupon' | 'write_address' | 'write_invoice_profile' | 'submit_order' | 'pay' | 'cancel_order' | 'confirm_receipt' | 'request_after_sale' | 'refund' | 'review' | 'send_message' | 'download_invoice' | 'merchant_mutation';
export type CommerceTargetKind = 'skuId' | 'spuId' | 'cartLineId' | 'orderId' | 'paymentId' | 'refundId' | 'afterSaleId' | 'shipmentId' | 'invoiceId';

export interface CommerceStepMetadata {
  action: CommerceAction;
  targetKind?: CommerceTargetKind;
  targetRef?: string;
  beforeState?: string;
  cleanupAction?: string;
  idempotencyKeyRef?: string;
  e2eOwned: boolean;
  ledgerOperation: 'none' | 'register' | 'cleanup';
  stateProbe?: {
    domain: 'order' | 'inventory' | 'payment' | 'refund';
    url: string;
    jsonPath: string;
    expectedState: string;
    timeoutMs: number;
    intervalMs: number;
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
  bridge_target_id?: string;
  computer_use_triggered?: boolean;
  computer_use_reason?: string;
  scroll_delta_y?: number;
  commerce?: CommerceStepMetadata;
  commerceScope?: {
    kind: 'product_card' | 'cart_line' | 'order_row' | 'after_sale_row';
    container: Locator;
    anchor: Locator;
    excludedMarkers: Locator[];
    maxScrollAttempts: number;
  };
  browserTarget?: {
    page: 'current' | 'newest' | 'opener';
    urlContains?: string;
    frameCss?: string;
    waitTimeoutMs: number;
  };
  takeoverReason?: 'captcha' | 'slider' | 'qr_login' | 'risk_control' | 'payment_auth' | 'other';
  takeoverResumeLocator?: Locator;
  fileAssetRef?: string;
  expectedDownloadSha256?: string;
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
  browserContextEvidence?: Record<string, unknown>;
  commerceStateEvidence?: {
    domain: string; expectedState: string; finalState: string; consistent: boolean;
    observations: Array<{ state: string; observedAt: string; httpStatus: number; responseSha256: string }>;
  };
  fileEvidence?: { direction: 'upload' | 'download'; sha256: string; bytes: number; assetRef?: string; filename?: string; artifact?: string };
  recoveryEvidence?: {
    policy: string; sideEffect: boolean; outcome: string; decision: string; retried: boolean;
    attempts: Array<{ attempt: number; outcome: string; failureClass?: string; httpStatus?: number; backoffMs?: number }>;
    idempotencyKeySha256?: string; backendProbe?: Record<string, unknown>; noReplayReason?: string;
  };
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
  assertions: Array<{ name: string; passed: boolean; message: string; evidence?: string }>;
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
  commerceSummary?: {
    environment: 'production_readonly' | 'isolated_transaction';
    policyEvaluations: Array<{ index: number; action: string; allowed: boolean; riskLevel: string; reason: string; missingControls: string[] }>;
    ledgerEntries: Array<Record<string, unknown>>;
    pendingResources: Array<{ reference: { kind: string; sha256: string; suffix?: string }; status: string; cleanupAction: string }>;
    zeroResidual: boolean;
    releaseGate?: CommerceReleaseGate;
  };
}

export interface CommerceReleaseGate {
  passed: boolean;
  policy: string;
  checks: {
    evidenceCompleteness: { passed: boolean; ratio: number; minimum: number; passedItems: number; totalItems: number; missing: Array<{ stepIndex: number; item: string; passed: boolean }> };
    privacyLeakage: { passed: boolean; count: number; findings: Array<{ type: string; location: string; valueSha256: string }> };
    zeroResidual: { passed: boolean; count: number };
    duplicateSideEffects: { passed: boolean; duplicateResourceReferences: number; unknownSideEffectOutcomes: number };
  };
}

export interface AcceptanceAttempt {
  id: string; scenarioId: string; priority: 'P0' | 'P1'; title: string; repeat: number;
  status: 'queued' | 'running' | 'passed' | 'failed' | 'blocked' | 'cancelled';
  verificationStatus: 'pending' | 'verified' | 'unverified';
  blockedDependencies: string[]; runId?: string; updatedAt: string;
  evidenceCompleteness?: number; stableReplay?: boolean; amountAccurate?: boolean;
  cleanupComplete?: boolean; zeroToleranceIncidents: Record<string, number>;
}

export interface AcceptanceBatch {
  id: string; profile: string; status: 'ready' | 'running' | 'completed' | 'blocked' | 'cancelled';
  verificationStatus: 'verified' | 'unverified'; createdAt: string; updatedAt: string;
  repeatCount: number; scenarioCount: number; plannedAttempts: number; cancelRequested: boolean;
  attempts: AcceptanceAttempt[];
  summary: {
    counts: Record<'queued' | 'running' | 'passed' | 'failed' | 'blocked' | 'cancelled', number>;
    verifiedAttempts: number; passed: boolean;
    thresholds: Record<string, { actual: number | boolean; required: number | boolean }>;
  };
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
  assertions: Array<{ name: string; passed: boolean; message: string; evidence?: string }>;
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
  appVersion?: string;
}

export interface ProjectLimits {
  maxSteps: number;
  timeoutSeconds: number;
  maxModelCalls: number;
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
}

export interface CommerceProfile {
  enabled: boolean;
  environment: 'production_readonly' | 'isolated_transaction';
  accountRef: string | null;
  productionReversibleWriteAuthorized: boolean;
  sandboxDriver: boolean;
  e2eResourcePrefix: string;
  piiMaskSelectors: string[];
}

export interface ProjectConfig {
  id: string;
  name: string;
  baseUrl: string;
  allowedHosts: string[];
  forbiddenActions: string[];
  allowPrivateNetwork: boolean;
  businessContext: BusinessContext;
  commerceProfile: CommerceProfile;
  onboardingLevel: 'L0' | 'L1' | 'L2' | 'L3';
  limits: ProjectLimits;
  createdAt: string;
  updatedAt: string;
}

export type ProjectDraft = Omit<ProjectConfig, 'id' | 'createdAt' | 'updatedAt'>;

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
  appBridge: { enabled: boolean; globalName: string; adapter: 'generic' | 'cesium' };
  artifactRetentionDays: number;
  createdAt: string;
  updatedAt: string;
}

export type EnvironmentDraft = Omit<EnvironmentConfig, 'id' | 'projectId' | 'createdAt' | 'updatedAt'>;

export interface FileAsset {
  ref: string;
  sha256: string;
  filename: string;
  bytes: number;
  createdAt: string;
}

export interface ScenarioConfig {
  id: string;
  projectId: string;
  name: string;
  preconditions: string[];
  goal: string;
  testData: Record<string, string | number | boolean | null>;
  expectedResults: string[];
  forbiddenActions: string[];
  commerceSteps: Array<{ stepIndex: number; commerce: CommerceStepMetadata }>;
  executionSteps: Array<{
    stepIndex: number;
    browserTarget: NonNullable<PlanStep['browserTarget']>;
    action?: 'human_takeover';
    takeoverReason?: PlanStep['takeoverReason'];
    takeoverResumeLocator?: Locator;
  }>;
  createdAt: string;
  updatedAt: string;
}

export type ScenarioDraft = Omit<ScenarioConfig, 'id' | 'projectId' | 'createdAt' | 'updatedAt'>;

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
}

export interface SessionRecording {
  id: string;
  projectId: string;
  status: 'starting' | 'recording' | 'saving' | 'completed' | 'cancelled' | 'error';
  session?: SessionMetadata;
}
