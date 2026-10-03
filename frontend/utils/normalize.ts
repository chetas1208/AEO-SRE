// Tolerant adapters from camelized API payloads to the typed shapes in ~/types.
// They only rename/unwrap fields; they never invent values.
import type { GContext, GExplanation, GFeature, GHealth, GHighlightPath, GNode, GEdge, GraphView, GraphSourceMode } from '~/types/graph'
import type {
  IntegrationStatus, PromptSet, PriorityBreakdown, Capability, CapabilityState, EvidenceItem, ExperimentDetail, ExperimentList, ExperimentSummaryRow, GraphData,
  Hypothesis, IncidentCounts, IncidentDetail, IncidentEvent, IncidentList, IncidentSummary, InterventionCandidate,
  InterventionPackage, InterventionSet, MetricDelta, ChangeCheck, ChangeCheckList, GuardDecision, GuardFinding, ExperimentProtection, CanonicalClaim, Organization, PolicyInfo, PromptRow, SystemHealth
} from '~/types'

type R = Record<string, any>

const isObj = (v: unknown): v is R => !!v && typeof v === 'object' && !Array.isArray(v)

export function unwrapList<T = R>(raw: unknown, ...keys: string[]): T[] {
  if (Array.isArray(raw)) return raw as T[]
  if (isObj(raw)) {
    for (const k of [...keys, 'items', 'results', 'data']) {
      if (Array.isArray(raw[k])) return raw[k] as T[]
    }
  }
  return []
}

export function normMetric(m: R): MetricDelta {
  return {
    label: m.label ?? m.name ?? m.metric ?? m.key ?? 'Metric',
    key: m.key ?? m.metric ?? undefined,
    before: m.before ?? m.baseline ?? null,
    after: m.after ?? m.current ?? null,
    value: m.value ?? null,
    delta: m.delta ?? null,
    deltaPct: m.deltaPct ?? null,
    unit: m.unit ?? null,
    favorable: m.favorable ?? m.isFavorable ?? null,
    trend: m.trend ?? m.sparkline ?? null,
    observedAt: m.observedAt ?? null
  }
}

export function normIncident(r: R): IncidentSummary {
  return {
    id: String(r.id),
    number: r.number ?? null,
    title: r.title ?? 'Untitled incident',
    category: r.category ?? null,
    severity: r.severity ?? 'low',
    state: r.state ?? 'detected',
    status: r.status ?? null,
    priority: r.priority ?? null,
    detectedAt: r.detectedAt ?? r.createdAt ?? '',
    topic: r.topic ?? null,
    primaryDelta: r.primaryDelta ? normMetric(r.primaryDelta) : null,
    trend: Array.isArray(r.trend) && r.trend.length ? r.trend : null,
    confidence: r.confidence ?? null,
    summary: r.summary ?? null,
    investigationStatus: r.investigationStatus ?? null,
    contextLabel: r.contextLabel ?? null
  }
}

export function normIncidentList(raw: unknown): IncidentList {
  const r: R = isObj(raw) ? raw : {}
  const items = unwrapList(raw, 'incidents').map(normIncident)
  return {
    items,
    total: r.total ?? items.length,
    counts: { bySeverity: r.countsBySeverity ?? {}, byStatus: r.countsByStatus ?? {}, byState: r.countsByState ?? {} }
  }
}

export function normPriority(r: R | null | undefined): PriorityBreakdown | null {
  if (!r || !isObj(r)) return null
  return {
    score: r.score ?? null,
    components: unwrapList<R>(r.components).map((c) => ({
      key: c.key, label: c.label ?? humanize(c.key), value: c.value ?? null, weight: c.weight ?? null, source: c.source ?? null, note: c.note ?? null
    })),
    method: r.method ?? null,
    note: r.note ?? null
  }
}

export function normIncidentDetail(r: R): IncidentDetail {
  const base = normIncident(r)
  return {
    ...base,
    firstObservedAt: r.firstObservedAt ?? null,
    promptClusterId: r.promptClusterId ?? null,
    metrics: unwrapList(r.metrics).map(normMetric),
    priorityBreakdown: normPriority(r.priorityBreakdown),
    primaryCta: r.primaryCta ?? null,
    allowedActions: r.allowedActions ?? [],
    affectedPromptCount: r.affectedPromptCount ?? null,
    context: isObj(r.context) ? r.context : null,
    experimentId: r.experimentId ?? null,
    expectedOutcome: isObj(r.expectedOutcome) ? (r.expectedOutcome as IncidentDetail['expectedOutcome']) : null,
    evidenceCount: r.evidenceCount ?? null,
    explanation: isObj(r.explanation) ? (r.explanation as IncidentDetail['explanation']) : null
  }
}

export function normHypothesis(r: R): Hypothesis {
  return {
    id: String(r.id),
    title: r.title ?? '',
    summary: r.summary ?? null,
    rationale: r.rationale ?? null,
    status: r.status,
    confidence: r.confidence ?? 0,
    evidenceIds: (r.evidenceIds ?? []).map(String),
    contradictingEvidenceIds: (r.contradictingEvidenceIds ?? []).map(String),
    producedBy: r.producedBy ?? null
  }
}

export function normEvidence(r: R): EvidenceItem {
  return {
    id: String(r.id),
    type: r.type ?? 'external',
    title: r.title ?? r.url ?? 'Untitled source',
    source: r.source ?? null,
    domain: r.domain ?? null,
    url: r.url ?? null,
    observedAt: r.observedAt ?? null,
    retrievedAt: r.retrievedAt ?? null,
    excerpt: r.excerpt ?? r.provenance?.extract ?? null,
    contentHash: r.contentHash ?? null,
    retrievalMethod: r.retrievalMethod ?? null,
    supportScore: r.supportScore ?? null,
    contradictionScore: r.contradictionScore ?? null,
    insufficientScore: r.insufficientScore ?? null,
    freshnessRisk: r.freshnessRisk ?? null,
    confidence: r.confidence ?? null,
    status: r.status ?? 'live',
    error: r.error ?? null
  }
}

export function normGraph(raw: unknown): GraphData {
  const r: R = isObj(raw) ? raw : {}
  const nodes = unwrapList<R>(r.nodes).map((n) => ({
    id: String(n.id),
    type: n.nodeClass ?? n.type ?? n.kind ?? 'inference',
    role: n.role ?? null,
    title: n.title ?? n.label ?? String(n.id),
    source: n.source ?? null,
    url: n.url ?? null,
    observedAt: n.observedAt ?? null,
    confidence: n.confidence ?? null,
    excerpt: n.extract ?? n.excerpt ?? null,
    status: n.status ?? null,
    level: n.level ?? null
  }))
  const edges = unwrapList<R>(r.edges).map((e) => ({
    id: e.id ? String(e.id) : undefined,
    src: String(e.source ?? e.src),
    dst: String(e.target ?? e.dst),
    edgeType: e.type ?? e.edgeType ?? 'associated_with',
    confidence: e.confidence ?? null,
    conflict: !!e.conflict,
    backEdge: !!e.backEdge
  }))
  const v: R = isObj(r.validation) ? r.validation : {}
  const warnings: string[] = [...(v.warnings ?? [])]
  if (v.isAcyclic === false) warnings.push('Graph contains cycles; back edges are kept for audit.')
  return { nodes, edges, warnings }
}

export function normPrompt(r: R, i = 0): PromptRow {
  return {
    id: `${i}:${r.prompt ?? r.text ?? ''}`,
    text: r.prompt ?? r.text ?? '',
    intent: r.intent ?? null,
    volume: r.volume ?? null,
    visibility: r.ourVisibility ?? r.visibility ?? null,
    visibilityBefore: r.visibilityBefore ?? null,
    competitor: typeof r.competitor === 'string' ? r.competitor : (r.competitor?.name ?? null),
    competitorVisibility: r.competitorVisibility ?? r.competitor?.share ?? null,
    engines: r.engines ?? [],
    change: r.change ?? null,
    unit: r.unit ?? null,
    persona: r.persona ?? null,
    topic: r.topic ?? null,
    region: r.meta?.region ?? null,
    citations: r.meta?.citations ?? [],
    observedAt: r.meta?.observedAt ?? null
  }
}

export function normPrompts(raw: unknown): PromptSet {
  const r: R = isObj(raw) ? raw : {}
  const items = unwrapList<R>(raw, 'prompts').map(normPrompt)
  return { items, total: r.total ?? items.length, unavailableReason: r.unavailableReason ?? null }
}

export function normPackage(raw: unknown): InterventionPackage | null {
  if (!isObj(raw)) return null
  const win: R | null = isObj(raw.observationWindow) ? raw.observationWindow : null
  return {
    executor: raw.executor, action: raw.action, title: raw.title ?? null, summary: raw.summary ?? null,
    target: isObj(raw.target) ? { url: raw.target.url ?? null, paths: raw.target.paths ?? [] } : null,
    changes: unwrapList<R>(raw.changes).map((c) => ({
      path: String(c.path ?? ''), targetUrl: c.targetUrl ?? null, changeType: c.changeType ?? null, proposedText: c.proposedText ?? '',
      currentContent: c.currentContent ?? null, currentContentKnown: !!c.currentContentKnown, diff: c.diff ?? null
    })),
    diff: raw.diff || null,
    manualTask: isObj(raw.manualTask) ? { title: raw.manualTask.title, body: raw.manualTask.body, recipient: raw.manualTask.recipient ?? null, targetUrl: raw.manualTask.targetUrl ?? null, checklist: raw.manualTask.checklist ?? [] } : null,
    steps: Array.isArray(raw.steps) ? raw.steps : [],
    why: raw.why ?? null,
    evidenceSummary: unwrapList<R>(raw.evidenceSummary),
    risk: raw.risk ?? null, rollback: raw.rollback ?? null,
    observationWindow: win ? { delayHours: win.delayHours ?? null, durationDays: win.durationDays ?? null, note: win.note ?? null } : null,
    approvedBy: raw.approvedBy ?? null, modifiedByHuman: !!raw.modifiedByHuman, requiresHumanStep: raw.requiresHumanStep !== false,
    notes: raw.notes ?? []
  }
}

export function normIntervention(r: R): InterventionCandidate {
  const sb = r.selectionBasis ?? null
  const exec: R = isObj(r.execution) ? r.execution : {}
  const approval: R = isObj(r.approval) ? r.approval : {}
  const change: R = isObj(r.proposedChange) ? r.proposedChange : {}
  const pkg = normPackage(r.package ?? exec.package)
  const win = pkg?.observationWindow
  const hours = r.observationWindowHours
  const actual: R | null = isObj(exec.actualChange) ? exec.actualChange : null
  return {
    id: String(r.id),
    action: r.action ?? 'observe',
    title: r.title ?? r.action ?? '',
    reason: r.reason ?? r.rationale ?? null,
    score: r.score ?? 0,
    risk: r.risk ?? 'low',
    selected: !!r.selected,
    selectionBasis: sb,
    policyVersion: r.policyVersion ?? null,
    policyProbability: r.policyProbability ?? null,
    coldStart: r.coldStart ?? sb === 'cold_start_prior',
    relatedExperiments: r.basedOnExperiments ?? null,
    proposedChange: Object.keys(change).length ? change : null,
    executor: r.executor ?? exec.executor ?? 'manual',
    rollback: r.rollback ?? pkg?.rollback ?? null,
    observationWindow: hours != null ? `${hours}h` : win?.delayHours != null ? `after a ${win.delayHours}h data lag, measured over ${win.durationDays ?? '?'} days` : null,
    executionTarget: r.executionTarget ?? null,
    experimentId: r.experimentId ?? null,
    approvalStatus: r.approvalStatus ?? approval.status ?? null,
    executionStatus: exec.status ?? null,
    executionReference: exec.reference ?? null,
    dryRun: exec.dryRun ?? null,
    package: pkg,
    manualExecutionPending: !!r.manualExecutionPending,
    deviation: !!exec.deviation,
    executedBy: exec.executedBy ?? null,
    executedAt: exec.executedAt ?? null,
    actualChange: actual?.text ?? null,
    expectedOutcome: r.expectedOutcome ?? null,
    guard: isObj(r.changeGuard) ? normChangeCheck(r.changeGuard) : null,
    approvalDigest: r.approvalDigest ?? r.actionDigest ?? approval.actionDigest ?? null
  }
}

export function normInterventions(raw: unknown): InterventionSet {
  const items = unwrapList(raw, 'interventions').map(normIntervention).sort((a, b) => Number(b.selected) - Number(a.selected) || b.score - a.score)
  const r: R = isObj(raw) ? raw : {}
  return {
    items,
    policyVersion: r.policyVersion ?? null,
    coldStart: r.coldStart ?? null,
    selectionBasis: r.selectionBasis ?? null,
    relatedExperiments: null,
    unavailableReason: r.unavailableReason ?? null
  }
}

export function normEvent(r: R): IncidentEvent {
  return {
    id: String(r.id ?? `${r.timestamp ?? r.at}-${r.stage}-${r.message}`),
    incidentId: r.incidentId != null ? String(r.incidentId) : undefined,
    timestamp: r.timestamp ?? r.at ?? '',
    stage: r.stage ?? '',
    eventType: typeof r.eventType === 'string' ? r.eventType : undefined,
    status: r.status ?? 'running',
    message: r.message ?? '',
    metadata: r.metadata ?? null
  }
}

function normMetricList(v: unknown): MetricDelta[] | Record<string, number> | null {
  if (Array.isArray(v)) return v.map(normMetric)
  if (isObj(v)) return v as Record<string, number>
  return null
}

export function normExperimentRow(r: R): ExperimentSummaryRow {
  return {
    id: String(r.id),
    number: r.number ?? null,
    code: r.code ?? null,
    incidentId: r.incidentId != null ? String(r.incidentId) : null,
    incidentTitle: r.incidentTitle ?? null,
    action: r.action ?? '',
    actionTitle: r.actionTitle ?? null,
    startedAt: r.startedAt ?? null,
    status: r.status ?? 'proposed',
    displayStatus: r.displayStatus ?? null,
    before: r.before ?? null,
    after: r.after ?? null,
    unit: null,
    metricLabel: r.beforeAfterLabel ?? null,
    reward: r.reward ?? null,
    policyVersion: r.policyVersion ?? null,
    executor: r.executor ?? null,
    dryRun: r.dryRun ?? null,
    outcomeLabel: r.outcome ?? null,
    inconclusiveReason: r.inconclusiveReason ?? null
  }
}

export function normExperimentList(raw: unknown): ExperimentList {
  const r: R = isObj(raw) ? raw : {}
  const items = unwrapList(raw, 'experiments').map(normExperimentRow)
  const c: R | null = isObj(r.summary) ? r.summary : null
  return {
    items,
    counts: c ? { running: c.running, awaitingMeasurement: c.awaitingMeasurement, verified: c.verified } : null,
    unavailableReason: r.unavailableReason ?? null
  }
}

/** ExperimentDetail is sectioned per UI.md §30: summary / whySelected / contextAtDecision / actionExecuted / ... */
export function normExperimentDetail(r: R): ExperimentDetail {
  const sum: R = r.summary ?? {}
  const why: R = r.whySelected ?? {}
  const act: R = r.actionExecuted ?? {}
  const rw: R | null = isObj(r.reward) ? r.reward : null
  const inc: R | null = isObj(sum.incident) ? sum.incident : null
  const policy: R | null = isObj(r.policy) ? r.policy : null
  const apr: R | null = isObj(r.approval) ? r.approval : null
  const snapshot = r.evidenceSnapshot
  return {
    id: String(r.id),
    number: sum.number ?? null,
    code: r.code ?? null,
    displayStatus: r.displayStatus ?? sum.displayStatus ?? null,
    incidentId: inc?.id ?? null,
    incidentTitle: inc?.title ?? null,
    action: sum.action ?? '',
    actionTitle: sum.actionTitle ?? null,
    startedAt: sum.executedAt ?? sum.createdAt ?? null,
    status: sum.status ?? 'proposed',
    before: null,
    after: null,
    reward: rw?.total ?? null,
    policyVersion: why.policyVersion ?? policy?.version ?? null,
    rationale: why.reason ?? why.rationale ?? null,
    selectionBasis: why.selectionBasis ?? null,
    coldStart: why.coldStart ?? null,
    policyProbability: why.policyProbability ?? null,
    relatedExperiments: policy?.nUpdates ?? null,
    contextVector: r.contextAtDecision?.contextVector ?? null,
    alternatives: Array.isArray(why.alternatives) ? why.alternatives.map((a: R) => ({ action: a.action ?? a.selectedAction ?? '', title: a.title ?? null, score: a.score ?? 0 })) : [],
    evidenceSnapshot: Array.isArray(snapshot) ? snapshot : isObj(snapshot) && Array.isArray(snapshot.items) ? snapshot.items : null,
    proposedChange: act.approvedChange ?? act.proposedChange ?? null,
    executor: act.executor ?? null,
    executionReference: act.reference ?? null,
    dryRun: act.dryRun ?? sum.dryRun ?? null,
    deviation: !!act.execution?.deviation,
    actualChange: isObj(act.execution?.actualChange) ? act.execution.actualChange.text ?? null : null,
    executedBy: act.execution?.executedBy ?? null,
    executionNote: act.execution?.note ?? null,
    package: normPackage(act.execution?.package),
    approval: apr ? { status: apr.status, decidedBy: apr.decidedBy ?? null, decidedAt: apr.decidedAt ?? null, note: apr.note ?? null } : null,
    beforeMetrics: normMetricList(r.beforeMetrics),
    afterMetrics: normMetricList(r.afterMetrics),
    outcomeLabel: isObj(r.outcome) ? r.outcome.label ?? null : null,
    inconclusiveReason: isObj(r.outcome) ? r.outcome.inconclusiveReason ?? null : null,
    spec: isObj(r.spec) ? {
      ifAction: r.spec.ifAction ?? null, becauseRootCause: r.spec.becauseRootCause ?? null, thenMetric: r.spec.thenMetric ?? null,
      direction: r.spec.direction ?? null, windowHours: r.spec.windowHours ?? null, delayHours: r.spec.delayHours ?? null,
      statement: r.spec.statement ?? null, observe: r.spec.observe ?? null, declaredAt: r.spec.declaredAt ?? null
    } : null,
    declaredMetrics: isObj(r.declaredMetrics) ? { primary: r.declaredMetrics.primary ?? null, secondary: Array.isArray(r.declaredMetrics.secondary) ? r.declaredMetrics.secondary : [] } : null,
    outcome: isObj(r.outcome) ? {
      label: String(r.outcome.label),
      observeOutcome: r.outcome.observeOutcome ?? null,
      rewardTotal: r.outcome.rewardTotal ?? null,
      components: isObj(r.outcome.components) ? r.outcome.components : {},
      confounders: Array.isArray(r.outcome.confounders) ? r.outcome.confounders.map((c: R) => ({ kind: String(c.kind ?? 'unknown'), detail: c.detail ?? null, hard: c.hard ?? null })) : [],
      causalConfidence: r.outcome.causalConfidence ?? null,
      causalStatement: r.outcome.causalStatement ?? null,
      learningApplied: !!r.outcome.learningApplied,
      inconclusiveReason: r.outcome.inconclusiveReason ?? null,
      observedAt: r.outcome.observedAt ?? null,
      evaluatedAt: r.outcome.evaluatedAt ?? null
    } : null,
    override: isObj(r.override) ? { overridden: !!r.override.overridden, policyAction: r.override.policyAction ?? null, executedAction: r.override.executedAction ?? null, reason: r.override.reason ?? null, by: r.override.by ?? null } : null,
    verification: isObj(r.verification) ? {
      executedAt: r.verification.executedAt ?? null, eligibleAt: r.verification.eligibleAt ?? null, windowEnd: r.verification.windowEnd ?? null,
      delayHours: r.verification.delayHours ?? null, isOpen: r.verification.isOpen ?? null, rules: Array.isArray(r.verification.rules) ? r.verification.rules : []
    } : null,
    rewardData: rw ? { total: rw.total ?? null, components: rw.components ?? null, weights: rw.weights ?? null, computedAt: rw.computedAt ?? null } : null,
    verificationWindowStart: sum.verificationWindowStart ?? null,
    verificationWindowEnd: sum.verificationWindowEnd ?? null,
    executedAt: sum.executedAt ?? null,
    evaluatedAt: sum.evaluatedAt ?? null,
    timeline: unwrapList<R>(r.timeline).map((t) => ({ at: t.at ?? null, event: t.event, actor: t.actor ?? null })),
    awaitingReward: !!r.awaitingReward,
    protection: normProtection(r.protection)
  }
}

const CAP_STATES: CapabilityState[] = ['healthy', 'degraded', 'unavailable']
function capState(v: unknown): CapabilityState {
  const s = typeof v === 'string' ? v.toLowerCase() : ''
  return (CAP_STATES as string[]).includes(s) ? (s as CapabilityState) : 'unavailable'
}

/** /api/system/capabilities → {capabilities: {key: {key,label,state,detail,lastSuccess,lastError}}, lastIngestion, ...} */
export function normCapabilities(raw: unknown): Capability[] {
  const r: R = isObj(raw) ? raw : {}
  const src = r.capabilities ?? {}
  const entries: Array<[string, R]> = Array.isArray(src) ? src.map((c: R) => [c.key ?? c.name, c]) : Object.entries(src as R).map(([k, v]) => [k, isObj(v) ? v : { state: v }])
  return entries.map(([k, c]) => ({
    name: c.key ?? k,
    label: c.label ?? null,
    state: capState(c.state),
    lastSuccessAt: c.lastSuccess ?? null,
    lastError: c.lastError ?? null,
    detail: c.detail ?? null
  }))
}

export function normHealth(health: unknown, caps: unknown): SystemHealth {
  const h: R = isObj(health) ? health : {}
  const c: R = isObj(caps) ? caps : {}
  return {
    status: h.status ?? null,
    overall: c.overall ?? null,
    capabilities: normCapabilities(caps),
    executors: normCapabilities({ capabilities: c.executors ?? {} }),
    profoundState: h.profoundState ?? null,
    neo4jState: h.neo4jState ?? null,
    lastIngestionAt: c.lastIngestion ?? null,
    lastInvestigationAt: c.lastInvestigation ?? null,
    lastPolicyUpdateAt: c.lastPolicyUpdate ?? null,
    nextIngestionAt: c.nextIngestion ?? null,
    policyVersion: (c.capabilities as R | undefined)?.policy?.meta?.version ?? null,
    modelArtifactVersion: c.modelArtifactVersion ?? null
  }
}

export function normOrganization(r: R): Organization {
  return {
    id: String(r.id),
    name: r.name ?? r.domain ?? '',
    domain: r.domain ?? '',
    competitorDomains: r.competitorDomains ?? [],
    canonicalDomains: r.canonicalDomains ?? [],
    personas: r.personas ?? [],
    topics: r.topics ?? []
  }
}

export function normPolicy(raw: unknown): PolicyInfo {
  const r: R = isObj(raw) ? raw : {}
  return {
    available: r.available ?? true,
    unavailableReason: r.unavailableReason ?? null,
    version: r.currentVersion ?? null,
    learningMode: r.learningMode ?? null,
    coldStart: r.coldStart ?? null,
    allowedActions: isObj(r.allowedActions) ? r.allowedActions : undefined,
    requiresApproval: r.humanApprovalRequired ?? null,
    minConfidence: r.minConfidence ?? null,
    nUpdates: r.nUpdates ?? null
  }
}

export function normIntegrations(raw: unknown): IntegrationStatus[] {
  const r: R = isObj(raw) ? raw : {}
  return unwrapList<R>(r.integrations).map((i) => ({
    key: i.key, label: i.label ?? humanize(i.key), connected: !!i.connected, state: capState(i.state),
    configuredFields: i.configuredFields ?? [], missingFields: i.missingFields ?? [], lastSuccess: i.lastSuccess ?? null, lastError: i.lastError ?? null, detail: i.detail ?? null,
    optional: !!i.optional, kind: i.kind ?? 'integration'
  }))
}


// ---- Change Guard -----------------------------------------------------------------------------------------------
const DECISIONS: GuardDecision[] = ['ALLOW', 'MERGE', 'DELAY', 'REQUIRE_REVIEW', 'BLOCK']
const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const strList = (v: unknown): string[] | null => (Array.isArray(v) ? v.map((x) => (isObj(x) ? String(x.name ?? x.check ?? x.key ?? '') : String(x))).filter(Boolean) : null)

/** Unknown / missing decision stays null (rendered "Unavailable"); the client never infers one. */
export function normDecision(v: unknown): GuardDecision | null {
  const d = typeof v === 'string' ? v.trim().toUpperCase() : ''
  return (DECISIONS as string[]).includes(d) ? (d as GuardDecision) : null
}

export function normFinding(r: R): GuardFinding {
  const refs: R = isObj(r.references) ? r.references : {}
  const overlap: R = isObj(r.overlap) ? r.overlap : {}
  return {
    type: str(r.type ?? r.check),
    severity: str(r.severity),
    decision: normDecision(r.decision),
    reason: str(r.reason ?? r.message),
    eligibleAfter: str(r.eligibleAfter),
    targetOverlapPct: num(r.targetOverlapPct ?? overlap.targetPct),
    promptClusterOverlapPct: num(r.promptClusterOverlapPct ?? r.promptOverlapPct ?? overlap.promptClusterPct),
    outcomeClass: str(r.outcomeClass ?? r.pairClass),
    experimentCode: str(refs.experimentCode ?? r.experimentCode),
    otherChangeId: str(refs.otherChangeId ?? refs.changeId ?? r.otherChangeId),
    canonicalClaimId: str(refs.canonicalClaimId ?? refs.claimId ?? r.canonicalClaimId)
  }
}

export function normChangeCheck(raw: unknown): ChangeCheck {
  const r: R = isObj(raw) ? raw : {}
  const agent: R = isObj(r.agent) ? r.agent : {}
  const sm = typeof r.sourceMode === 'string' ? r.sourceMode.toUpperCase() : ''
  const sem = r.semanticCheck
  const findings = Array.isArray(r.findings) ? r.findings.map((f: R) => normFinding(isObj(f) ? f : {})) : null
  const merged = r.mergedProposal ?? r.mergedChangeSet
  const codes = new Set<string>()
  for (const c of Array.isArray(r.experimentCodes) ? r.experimentCodes : []) if (typeof c === 'string') codes.add(c)
  for (const f of findings ?? []) if (f.experimentCode) codes.add(f.experimentCode)
  return {
    id: String(r.id ?? ''),
    changeSetId: str(r.changeSetId),
    decision: normDecision(r.decision),
    agentId: str(agent.id ?? r.agentId),
    agentName: str(agent.name ?? r.agentName),
    sourceMode: sm === 'LIVE' || sm === 'SIMULATED' ? sm : null,
    targetUrl: str(r.targetUrl ?? r.target),
    actionType: str(r.actionType),
    findings,
    eligibleAfter: str(r.eligibleAfter),
    semanticCheck: typeof sem === 'string' ? sem : isObj(sem) ? str(sem.state ?? sem.status) : null,
    checksRun: strList(r.checksRun),
    checksSkipped: strList(r.checksSkipped),
    mergedProposal: typeof merged === 'string' ? merged : isObj(merged) ? str(merged.text ?? merged.summary) ?? JSON.stringify(merged, null, 2) : null,
    digest: str(r.digest ?? r.actionDigest),
    evaluatedAt: str(r.evaluatedAt ?? r.createdAt),
    replayed: typeof r.replayed === 'boolean' ? r.replayed : null,
    experimentCodes: [...codes]
  }
}

export function normChangeCheckList(raw: unknown): ChangeCheckList {
  const r: R = isObj(raw) ? raw : {}
  return { items: unwrapList(raw, 'checks').map(normChangeCheck), total: num(r.total), limit: num(r.limit), offset: num(r.offset) }
}

/** `protection` absent => null (unavailable). Never inferred from the experiment status. */
export function normProtection(raw: unknown): ExperimentProtection | null {
  if (!isObj(raw)) return null
  return {
    protected: typeof raw.protected === 'boolean' ? raw.protected : null,
    until: str(raw.until ?? raw.eligibleAfter),
    targets: Array.isArray(raw.targets) ? raw.targets.map(String) : [],
    checksBlockedCount: num(raw.checksBlockedCount),
    recentChecks: unwrapList(raw.recentChecks).map(normChangeCheck)
  }
}

export function normCanonicalClaim(r: R): CanonicalClaim {
  return {
    id: String(r.id),
    key: str(r.key),
    statement: String(r.statement ?? ''),
    entities: Array.isArray(r.entities) ? r.entities.map(String) : [],
    scope: str(r.scope),
    validFrom: str(r.validFrom),
    source: str(r.source ?? r.provenance),
    status: str(r.status) ?? 'active',
    updatedAt: str(r.updatedAt)
  }
}

export function normCanonicalClaims(raw: unknown): CanonicalClaim[] {
  return unwrapList(raw, 'claims', 'canonicalClaims').map(normCanonicalClaim)
}

// ---- Graph API (docs/GRAPH_SPEC.md; N6). The ONE place where API field names are mapped. Payloads are camelized, except
// node `props` and feature dictionaries (kept snake_case, see utils/camelize.ts). Missing values stay null.
const bool = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null)
const mode = (v: unknown): GraphSourceMode | null => {
  const m = typeof v === 'string' ? v.toUpperCase() : ''
  return m === 'LIVE' || m === 'SIMULATED' || m === 'FIXTURE' || m === 'TEST' ? m : null
}
const isUnavailable = (r: R): boolean =>
  String(r.source ?? '').toLowerCase() === 'unavailable' || r.unavailable === true || r.available === false || String(r.state ?? '').toLowerCase() === 'unavailable'
const isStale = (r: R): boolean => r.graphStale === true || r.stale === true || r.graphContextStale === true

export function normGNode(raw: unknown): GNode | null {
  const r: R = isObj(raw) ? raw : {}
  if (r.id === undefined || r.id === null || r.id === '') return null
  const props: R = isObj(r.props) ? r.props : isObj(r.properties) ? r.properties : {}
  return {
    id: String(r.id),
    label: String(r.label ?? r.type ?? r.kind ?? 'Node'),
    props,
    source: str(props.source ?? r.source),
    sourceMode: mode(props.source_mode ?? props.sourceMode ?? r.sourceMode),
    occurredAt: str(props.occurred_at ?? props.occurredAt ?? r.occurredAt),
    recordedAt: str(props.recorded_at ?? props.recordedAt ?? r.recordedAt)
  }
}

export function normGEdge(raw: unknown): GEdge | null {
  const r: R = isObj(raw) ? raw : {}
  const source = r.source ?? r.src ?? r.from
  const target = r.target ?? r.dst ?? r.to
  if (source == null || target == null) return null
  const type = String(r.type ?? r.edgeType ?? r.rel ?? 'RELATED')
  return { id: String(r.id ?? `${source}|${type}|${target}`), source: String(source), target: String(target), type }
}

export function normHighlightPaths(raw: unknown): GHighlightPath[] {
  const out: GHighlightPath[] = []
  const list = Array.isArray(raw) ? raw : isObj(raw) ? Object.entries(raw).map(([k, v]) => (isObj(v) ? { kind: k, ...v } : { kind: k, nodeIds: v })) : []
  list.forEach((p: unknown, i: number) => {
    const r: R = Array.isArray(p) ? { nodeIds: p } : isObj(p) ? p : {}
    const nodeIds = (Array.isArray(r.nodeIds) ? r.nodeIds : Array.isArray(r.nodes) ? r.nodes : Array.isArray(r.path) ? r.path : [])
      .map((n: unknown) => (isObj(n) ? n.id : n)).filter((n: unknown) => n !== undefined && n !== null).map(String)
    if (!nodeIds.length) return
    const edgeIds = (Array.isArray(r.edgeIds) ? r.edgeIds : Array.isArray(r.edges) ? r.edges : []).map((e: unknown) => (isObj(e) ? e.id : e)).filter(Boolean).map(String)
    out.push({
      id: String(r.id ?? r.pathId ?? `path-${i}`),
      kind: String(r.kind ?? r.type ?? r.name ?? 'decision').toLowerCase(),
      decisionId: str(r.decisionId ?? r.anchorId ?? r.anchor),
      nodeIds, edgeIds
    })
  })
  return out
}

/** Lineage/neighbourhood view (changes/{id}/lineage, experiments/{id}/lineage). */
export function normGraphView(raw: unknown): GraphView {
  const r: R = isObj(raw) ? raw : {}
  const unavailable = isUnavailable(r)
  const nodes = unavailable ? [] : unwrapList(r.nodes).map(normGNode).filter((n): n is GNode => !!n)
  const ids = new Set(nodes.map((n) => n.id))
  const edges = unavailable ? [] : unwrapList(r.edges).map(normGEdge).filter((e): e is GEdge => !!e && ids.has(e.source) && ids.has(e.target))
  return {
    nodes, edges,
    focusId: str(r.focusId),
    generatedAt: str(r.generatedAt),
    asOf: str(r.asOf),
    truncated: bool(r.truncated),
    found: bool(r.found),
    highlightPaths: unavailable ? [] : normHighlightPaths(r.highlightPaths ?? r.paths),
    unavailable,
    stale: isStale(r),
    reason: str(r.reason ?? r.unavailableReason ?? r.staleReason),
    lastGoodAt: str(r.lastGoodAt ?? r.lastGoodTimestamp ?? r.lastProjectedAt)
  }
}

export function normGExplanation(raw: unknown): GExplanation {
  const r: R = isObj(raw) ? raw : {}
  return {
    changesetId: str(r.changesetId ?? r.changeSetId),
    found: bool(r.found),
    decision: str(r.decision),
    decisionId: str(r.decisionId),
    text: str(r.text),
    statements: unwrapList<R>(r.statements).map((s) => ({
      text: String(s.text ?? ''),
      nodeIds: (Array.isArray(s.nodeIds) ? s.nodeIds : []).map(String),
      edgeIds: (Array.isArray(s.edges) ? s.edges : Array.isArray(s.edgeIds) ? s.edgeIds : []).map(String)
    })).filter((s) => s.text),
    generatedAt: str(r.generatedAt),
    unavailable: isUnavailable(r),
    stale: isStale(r),
    reason: str(r.reason)
  }
}

function normFeatures(r: R): GFeature[] {
  const dict = isObj(r.features) ? r.features : isObj(r.graphFeatures) ? r.graphFeatures : null
  const missing = new Set<string>(Array.isArray(r.missing) ? r.missing.map(String) : [])
  if (dict) return Object.entries(dict).map(([name, v]) => ({ name, value: missing.has(name) ? null : num(v) }))
  const names: unknown[] = Array.isArray(r.names) ? r.names : []
  const vec: unknown[] = Array.isArray(r.vector) ? r.vector : []
  return names.length && names.length === vec.length ? names.map((n, i) => ({ name: String(n), value: missing.has(String(n)) ? null : num(vec[i]) })) : []
}

/** Policy + graph features + historical neighbours (changes/{id}/context). */
export function normGContext(raw: unknown): GContext {
  const r: R = isObj(raw) ? raw : {}
  const p: R | null = isObj(r.policy) ? r.policy : isObj(r.recommendation) ? r.recommendation : null
  const nb: R | null = isObj(r.similar) ? r.similar : isObj(r.similarContexts) ? r.similarContexts : isObj(r.historicalNeighbors) ? r.historicalNeighbors : isObj(r.neighbors) ? r.neighbors : null
  const rec = p ? (typeof p.recommendation === 'string' ? p.recommendation : isObj(p.recommendation) ? str(p.recommendation.action) : str(p.recommendedAction ?? p.action)) : null
  const eligible = p && Array.isArray(p.eligibleActions) ? p.eligibleActions.map(String) : null
  const modeStr = p ? str(p.mode ?? p.policyMode) : null
  const baselineActive = p ? (bool(p.baselineActive) ?? (modeStr ? modeStr.toLowerCase() === 'shadow' ? true : null : null)) : null
  const ver = p ? p.version ?? p.policyVersion : null
  return {
    policy: p ? {
      version: ver == null ? null : String(ver),
      recommendation: rec,
      selectedAction: str(p.selectedAction ?? p.selected),
      eligibleActions: eligible,
      mode: modeStr,
      baselineActive,
      graduated: bool(p.graduated),
      baselineAction: str(p.baselineAction ?? p.baselineDecision),
      fallbackReason: str(p.fallbackReason)
    } : null,
    features: normFeatures(r),
    featureVersion: str(r.featureVersion ?? r.version),
    snapshotTime: str(r.snapshotTime ?? r.asOf ?? r.generatedAt),
    neighbors: nb ? {
      count: num(nb.count ?? nb.candidatesConsidered ?? nb.historicalSimilarContextCount ?? (Array.isArray(nb.contexts) ? nb.contexts.length : null)),
      allowRate: num(nb.allowRate), blockRate: num(nb.blockRate), reviewRate: num(nb.reviewRate),
      byDecision: unwrapList<R>(nb.byDecision).map((d) => ({ decision: String(d.decision ?? ''), n: num(d.n), positiveRate: num(d.positiveRate) })).filter((d) => d.decision)
    } : null,
    unavailable: isUnavailable(r),
    stale: isStale(r) || (p ? p.stale === true : false),
    reason: str(r.reason ?? r.unavailableReason ?? (p ? p.fallbackReason : null))
  }
}

export function normGHealth(raw: unknown): GHealth {
  const r: R = isObj(raw) ? raw : {}
  return {
    state: str(r.state ?? r.status ?? r.neo4jState),
    latencyMs: num(r.latencyMs),
    projectionLagSeconds: num(r.projectionLagSeconds ?? r.lagSeconds),
    outboxBacklog: num(r.outboxBacklog ?? r.backlog),
    lastProjectedAt: str(r.lastProjectedAt ?? r.lastSuccessAt),
    reason: str(r.reason ?? r.error)
  }
}
