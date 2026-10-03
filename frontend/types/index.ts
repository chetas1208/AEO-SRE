// Hand-written mirror of the A12 API contract (UI.md §51 + extras). Wire format is snake_case; the API layer
// camelizes keys (see utils/camelize.ts). String unions mirror backend/app/domain/enums.py.
// If /openapi.json is reachable, `pnpm gen:api` writes types/api.generated.ts for cross-checking.
export * from './agentmatch'
export * from './campaign'

export type Severity = 'critical' | 'high' | 'medium' | 'low'
export type IncidentState =
  | 'detected' | 'triaged' | 'investigating' | 'evidence_ready' | 'root_cause_proposed'
  | 'root_cause_confirmed' | 'intervention_proposed' | 'awaiting_approval' | 'approved' | 'executing'
  | 'executed' | 'awaiting_verification' | 'verified' | 'rewarded' | 'closed' | 'dismissed' | 'failed'
export type ActionType =
  | 'observe' | 'update_existing_page' | 'create_faq' | 'create_canonical_page'
  | 'create_comparison_content' | 'publisher_outreach' | 'structured_data'
export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'modified' | 'expired'
export type ExperimentStatus =
  | 'proposed' | 'approved' | 'executing' | 'executed' | 'awaiting_verification' | 'verified'
  | 'rewarded' | 'rejected' | 'failed'
export type EvidenceType = 'profound' | 'owned' | 'competitor' | 'external' | 'inference'
export type EvidenceStatus = 'live' | 'changed' | 'stale' | 'unavailable' | 'failed'
export type EdgeType =
  | 'cites' | 'supports' | 'contradicts' | 'changed_before' | 'associated_with'
  | 'contains_claim' | 'competes_with' | 'triggered'
export type HypothesisStatus = 'proposed' | 'confirmed' | 'rejected'
export type StepStatus = 'pending' | 'running' | 'success' | 'warning' | 'failed' | 'waiting'
export type SelectionBasis = 'cold_start_prior' | 'learned_policy' | 'rule_fallback' | 'manual_override'
export type Risk = 'low' | 'medium' | 'high'
export type CapabilityState = 'healthy' | 'degraded' | 'unavailable'

export interface MetricDelta {
  label: string
  key?: string
  before?: number | null
  after?: number | null
  value?: number | null
  delta?: number | null
  deltaPct?: number | null
  unit?: string | null
  /** Provided by backend: whether the change is good for the brand. Frontend never infers it. */
  favorable?: boolean | null
  trend?: number[] | null
  observedAt?: string | null
}

export type IncidentStatus =
  | 'detected' | 'investigating' | 'needs_review' | 'ready_for_action' | 'executing'
  | 'awaiting_measurement' | 'verified' | 'resolved' | 'dismissed' | 'failed'

export interface IncidentSummary {
  id: string
  number?: number | null
  title: string
  category?: string | null
  severity: Severity
  state: IncidentState
  /** UI lifecycle status computed by the backend (UI.md §26). */
  status?: IncidentStatus | string | null
  priority?: number | null
  detectedAt: string
  topic?: string | null
  primaryDelta?: MetricDelta | null
  trend?: number[] | null
  confidence?: number | null
  summary?: string | null
  investigationStatus?: string | null
  contextLabel?: string | null
}

export interface IncidentCounts {
  bySeverity: Record<string, number>
  byStatus: Record<string, number>
  byState: Record<string, number>
}

export interface IncidentList {
  items: IncidentSummary[]
  total: number
  counts: IncidentCounts
}

export interface IncidentDetail extends IncidentSummary {
  firstObservedAt?: string | null
  promptClusterId?: string | null
  metrics: MetricDelta[]
  priorityBreakdown?: PriorityBreakdown | null
  primaryCta?: BackendCta | null
  allowedActions?: Array<{ action: string; enabled: boolean; reason?: string | null }>
  affectedPromptCount?: number | null
  context?: Record<string, unknown> | null
  experimentId?: string | null
  expectedOutcome?: ExpectedOutcome | null
  evidenceCount?: number | null
  explanation?: IncidentExplanation | null
}

export interface IncidentExplanation {
  whatChanged: string
  whyItMatters: string
  leadingHypothesis?: string | null
  hypothesisStatus?: string | null
  confidence?: number | null
  supportingEvidence?: string[]
  counterevidence?: string[]
  recommendedAction?: string | null
  policyScores?: Array<{ action: string; policyScore?: number | null; label?: string; selected?: boolean }>
  expectedMetric?: string | null
  verificationWindow?: string | null
  note?: string
  timings?: Record<string, number | null>
  independenceNote?: string | null
}

export interface PriorityComponent {
  key: string
  label: string
  value?: number | null
  weight?: number | null
  source?: string | null
  note?: string | null
}

export interface PriorityBreakdown {
  score?: number | null
  components: PriorityComponent[]
  method?: string | null
  note?: string | null
}

export interface BackendCta {
  key: string
  label: string
  enabled: boolean
  reason?: string | null
  interventionId?: string | null
  targetTab?: string | null
}

export interface ExpectedOutcome {
  available?: boolean
  reason?: string | null
  metric?: string | null
  low?: number | null
  high?: number | null
  unit?: string | null
  n: number
  insufficient?: boolean
}

export interface Hypothesis {
  id: string
  title: string
  summary?: string | null
  rationale?: string | null
  status?: HypothesisStatus
  confidence: number
  evidenceIds: string[]
  contradictingEvidenceIds?: string[]
  producedBy?: string | null
}

export interface EvidenceItem {
  id: string
  type: EvidenceType
  title: string
  source?: string | null
  domain?: string | null
  url?: string | null
  observedAt?: string | null
  retrievedAt?: string | null
  excerpt?: string | null
  contentHash?: string | null
  retrievalMethod?: string | null
  supportScore?: number | null
  contradictionScore?: number | null
  insufficientScore?: number | null
  freshnessRisk?: number | null
  confidence?: number | null
  status: EvidenceStatus
  error?: string | null
}

export interface GraphNode {
  id: string
  type: string
  role?: string | null
  title: string
  source?: string | null
  url?: string | null
  observedAt?: string | null
  confidence?: number | null
  excerpt?: string | null
  status?: string | null
  level?: number | null
  /** explicit depth layer for the 3D scene (graph lineage adapter); overrides the type-based depth */
  depth?: number | null
}

export interface GraphEdge {
  id?: string
  src: string
  dst: string
  edgeType: EdgeType | string
  confidence?: number | null
  conflict?: boolean
  backEdge?: boolean
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  warnings?: string[]
}

export interface PromptRow {
  id: string
  text: string
  intent?: string | null
  volume?: number | null
  visibility?: number | null
  visibilityBefore?: number | null
  competitor?: string | null
  competitorVisibility?: number | null
  topic?: string | null
  engines?: string[]
  change?: number | null
  unit?: string | null
  persona?: string | null
  region?: string | null
  citations?: string[]
  observedAt?: string | null
}

export interface PromptSet {
  items: PromptRow[]
  total: number
  unavailableReason?: string | null
}

export interface ProposedChange {
  target?: string | null
  resource?: string | null
  files?: string[] // paths; the backend sends file objects, normalize.ts keeps only the path
  diff?: string | null
  summary?: string | null
  [k: string]: unknown
}

/** Structured intervention package issued by the (default) manual executor. Exact text, never paraphrased. */
export interface PackageChange {
  path: string
  targetUrl?: string | null
  changeType?: string | null
  proposedText: string
  currentContent?: string | null
  currentContentKnown?: boolean
  diff?: string | null
}
export interface InterventionPackage {
  executor?: string
  action?: string
  title?: string | null
  summary?: string | null
  target?: { url?: string | null; paths?: string[] } | null
  changes: PackageChange[]
  diff?: string | null
  manualTask?: { title?: string; body?: string; recipient?: string | null; targetUrl?: string | null; checklist?: string[] } | null
  steps: string[]
  why?: string | null
  evidenceSummary: Array<{ title?: string | null; url?: string | null; type?: string | null; status?: string | null }>
  risk?: string | null
  rollback?: string | null
  observationWindow?: { delayHours?: number | null; durationDays?: number | null; note?: string | null } | null
  approvedBy?: string | null
  modifiedByHuman?: boolean
  requiresHumanStep?: boolean
  notes?: string[]
}

export interface InterventionCandidate {
  id: string
  action: ActionType | string
  title: string
  reason?: string | null
  score: number
  risk: Risk
  selected: boolean
  selectionBasis?: SelectionBasis | null
  policyVersion?: string | null
  policyProbability?: number | null
  coldStart: boolean
  relatedExperiments?: number | null
  proposedChange?: ProposedChange | null
  executor?: string | null
  rollback?: string | null
  observationWindow?: string | null
  executionTarget?: string | null
  experimentId?: string | null
  approvalStatus?: ApprovalStatus | null
  executionStatus?: string | null
  executionReference?: string | null
  dryRun?: boolean | null
  package?: InterventionPackage | null
  manualExecutionPending?: boolean
  deviation?: boolean
  executedBy?: string | null
  executedAt?: string | null
  actualChange?: string | null
  expectedOutcome?: ExpectedOutcome | null
  guard?: ChangeCheck | null
  approvalDigest?: string | null
}

export interface InterventionSet {
  items: InterventionCandidate[]
  policyVersion?: string | null
  coldStart?: boolean | null
  selectionBasis?: SelectionBasis | null
  relatedExperiments?: number | null
  unavailableReason?: string | null
}

export interface IncidentEvent {
  id: string
  incidentId?: string
  timestamp: string
  stage: string
  /** Canonical vocabulary from the server (app/core/event_types.py); unknown stages keep their own name. */
  eventType?: string
  status: StepStatus
  message: string
  metadata?: Record<string, unknown> | null
}

export interface RewardData {
  total?: number | null
  components?: Record<string, number> | null
  weights?: Record<string, number> | null
  computedAt?: string | null
}

export interface ExperimentSummaryRow {
  id: string
  number?: number | null
  code?: string | null
  displayStatus?: string | null
  incidentId?: string | null
  incidentTitle?: string | null
  action: string
  actionTitle?: string | null
  startedAt?: string | null
  status: ExperimentStatus
  executor?: string | null
  dryRun?: boolean | null
  before?: number | null
  after?: number | null
  unit?: string | null
  metricLabel?: string | null
  reward?: number | null
  policyVersion?: string | null
  source?: string | null
  /** Server-provided outcome label (favorable|unfavorable|neutral|inconclusive); null = not assessed. */
  outcomeLabel?: string | null
  inconclusiveReason?: string | null
}

export interface ExperimentSpecInfo {
  ifAction?: string | null
  becauseRootCause?: string | null
  thenMetric?: string | null
  direction?: string | null
  windowHours?: number | null
  delayHours?: number | null
  statement?: string | null
  observe?: boolean | null
  declaredAt?: string | null
}

export interface OutcomeRecord {
  label: string
  observeOutcome?: string | null
  rewardTotal?: number | null
  components: Record<string, number>
  confounders: Array<{ kind: string; detail?: string | null; hard?: boolean | null }>
  causalConfidence?: string | null
  causalStatement?: string | null
  learningApplied: boolean
  inconclusiveReason?: string | null
  observedAt?: string | null
  evaluatedAt?: string | null
}

export interface OverrideInfo {
  overridden: boolean
  policyAction?: string | null
  executedAction?: string | null
  reason?: string | null
  by?: string | null
}

export interface VerificationInfoView {
  executedAt?: string | null
  eligibleAt?: string | null
  windowEnd?: string | null
  delayHours?: number | null
  isOpen?: boolean | null
  rules: string[]
}

export interface ExperimentCounts {
  running?: number
  awaitingMeasurement?: number
  verified?: number
}

export interface ExperimentList {
  items: ExperimentSummaryRow[]
  counts: ExperimentCounts | null
  unavailableReason?: string | null
}

export interface TimelineEntry {
  at?: string | null
  event: string
  actor?: string | null
}

export interface ExperimentDetail extends ExperimentSummaryRow {
  rationale?: string | null
  selectionBasis?: SelectionBasis | null
  coldStart?: boolean | null
  policyProbability?: number | null
  relatedExperiments?: number | null
  contextVector?: Record<string, number> | null
  alternatives?: Array<{ action: string; title?: string | null; score: number }>
  evidenceSnapshot?: Array<{ id?: string; title?: string; url?: string | null; excerpt?: string | null }> | null
  proposedChange?: ProposedChange | null
  executor?: string | null
  executionReference?: string | null
  dryRun?: boolean | null
  deviation?: boolean
  actualChange?: string | null
  executedBy?: string | null
  executionNote?: string | null
  package?: InterventionPackage | null
  approval?: { status?: ApprovalStatus; decidedBy?: string | null; decidedAt?: string | null; note?: string | null } | null
  beforeMetrics?: MetricDelta[] | Record<string, number> | null
  afterMetrics?: MetricDelta[] | Record<string, number> | null
  spec?: ExperimentSpecInfo | null
  declaredMetrics?: { primary?: string | null; secondary: string[] } | null
  outcome?: OutcomeRecord | null
  override?: OverrideInfo | null
  verification?: VerificationInfoView | null
  rewardData?: RewardData | null
  verificationWindowStart?: string | null
  verificationWindowEnd?: string | null
  executedAt?: string | null
  evaluatedAt?: string | null
  protection?: ExperimentProtection | null
  timeline?: TimelineEntry[]
  awaitingReward?: boolean
}

export interface Capability {
  name: string
  state: CapabilityState
  lastSuccessAt?: string | null
  lastError?: string | null
  detail?: string | null
  label?: string | null
}

export interface SystemHealth {
  status?: string | null
  overall?: CapabilityState | null
  capabilities: Capability[]
  executors: Capability[] // manual always healthy; optional executors are never alarming
  lastIngestionAt?: string | null
  lastInvestigationAt?: string | null
  lastPolicyUpdateAt?: string | null
  nextIngestionAt?: string | null
  policyVersion?: string | null
  modelArtifactVersion?: string | null
}

export interface Organization {
  id: string
  name: string
  domain: string
  competitorDomains?: string[]
  canonicalDomains?: string[]
  personas?: string[]
  topics?: string[]
}

export interface PolicyInfo {
  available?: boolean
  unavailableReason?: string | null
  version?: string | null
  learningMode?: string | null
  coldStart?: boolean | null
  allowedActions?: Record<string, boolean>
  requiresApproval?: boolean | null
  minConfidence?: number | null
  nUpdates?: number | null
}

export interface IntegrationStatus {
  key: string
  label: string
  connected: boolean
  state: CapabilityState
  configuredFields: string[]
  missingFields: string[]
  lastSuccess?: string | null
  lastError?: string | null
  detail?: string | null
  optional?: boolean
  kind?: string
}

export type ErrorKind = 'unavailable' | 'failed'
export interface ApiErrorInfo {
  kind: ErrorKind
  status?: number
  message: string
  detail?: string
  /** Stable server code from error.code (e.g. EXPERIMENT_NOT_VERIFIABLE_YET); falls back to error.type. */
  code?: string
  requestId?: string
  details?: Record<string, unknown> | null
}

// ---- Change Guard (docs/CHANGE_GUARD_SPEC.md). View-model layer: field names follow the spec; utils/normalize.ts is the
// single place that maps the wire format onto these. Null always means "unavailable", never a default.
export type GuardDecision = 'ALLOW' | 'MERGE' | 'DELAY' | 'REQUIRE_REVIEW' | 'BLOCK'
export type GuardSourceMode = 'LIVE' | 'SIMULATED'

export interface GuardFinding {
  type: string | null // contamination | duplicate | canonical_conflict (server vocabulary; rendered humanized)
  severity: string | null
  decision: GuardDecision | null
  reason: string | null
  eligibleAfter: string | null
  targetOverlapPct: number | null
  promptClusterOverlapPct: number | null
  outcomeClass: string | null // DUPLICATE | COMPATIBLE | DEPENDENT | CONFLICTING | UNRELATED
  experimentCode: string | null
  otherChangeId: string | null
  canonicalClaimId: string | null
}

export interface ChangeCheck {
  id: string
  /** Postgres change_set id = ChangeSet node business id in the graph (null when the API does not report it) */
  changeSetId?: string | null
  decision: GuardDecision | null
  agentId: string | null
  agentName: string | null
  sourceMode: GuardSourceMode | null
  targetUrl: string | null
  actionType: string | null
  findings: GuardFinding[] | null
  eligibleAfter: string | null
  semanticCheck: string | null // e.g. ran | degraded | skipped_no_canonical_truth
  checksRun: string[] | null
  checksSkipped: string[] | null
  mergedProposal: string | null
  digest: string | null
  evaluatedAt: string | null
  replayed: boolean | null
  experimentCodes: string[]
}

export interface ChangeCheckList { items: ChangeCheck[]; total: number | null; limit: number | null; offset: number | null }

export interface ExperimentProtection {
  protected: boolean | null
  until: string | null
  targets: string[]
  checksBlockedCount: number | null
  recentChecks: ChangeCheck[]
}

export interface CanonicalClaim {
  id: string
  key: string | null
  statement: string
  entities: string[]
  scope: string | null
  validFrom: string | null
  source: string | null
  status: 'active' | 'retired' | string
  updatedAt: string | null
}
