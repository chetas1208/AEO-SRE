// View-models for the Neo4j-backed graph API (docs/GRAPH_SPEC.md). The browser never talks to Neo4j; these are the
// normalized shapes produced by utils/normalize.ts (norm*Graph*). Unknown values are null, never defaulted.

export type GraphSourceMode = 'LIVE' | 'SIMULATED' | 'FIXTURE' | 'TEST'
export type GraphWindow = '1h' | '24h' | '7d'

export interface GNode {
  id: string
  label: string // Neo4j label as reported: Agent | AgentRun | Event | ChangeSet | Target | Claim | Conflict | Decision | ...
  props: Record<string, unknown>
  source: string | null
  sourceMode: GraphSourceMode | null
  occurredAt: string | null
  recordedAt: string | null
}

export interface GEdge {
  id: string
  source: string
  target: string
  type: string
}

export interface GHighlightPath {
  id: string
  kind: string // 'decision' | 'outcome' | whatever the API names it (kept verbatim, lowercased)
  decisionId: string | null
  nodeIds: string[]
  edgeIds: string[]
}

export interface GraphView {
  nodes: GNode[]
  edges: GEdge[]
  focusId: string | null
  generatedAt: string | null
  asOf: string | null
  truncated: boolean | null
  found: boolean | null
  highlightPaths: GHighlightPath[]
  /** true when the API says the graph source is unavailable (no nodes are shown) */
  unavailable: boolean
  /** true when the API says the projection is stale (graph_stale / graph_context_stale) */
  stale: boolean
  reason: string | null
  lastGoodAt: string | null
}

export interface GStatement { text: string; nodeIds: string[]; edgeIds: string[] }

export interface GExplanation {
  changesetId: string | null
  found: boolean | null
  decision: string | null
  decisionId: string | null
  text: string | null
  statements: GStatement[]
  generatedAt: string | null
  unavailable: boolean
  stale: boolean
  reason: string | null
}

export interface GFeature { name: string; value: number | null }

export interface GNeighborStats {
  count: number | null
  allowRate: number | null
  blockRate: number | null
  reviewRate: number | null
  byDecision: Array<{ decision: string; n: number | null; positiveRate: number | null }>
}

export interface GPolicy {
  version: string | null
  recommendation: string | null
  selectedAction: string | null
  eligibleActions: string[] | null
  mode: string | null // 'shadow' | 'active' | ... as reported
  baselineActive: boolean | null
  graduated: boolean | null
  baselineAction: string | null
  fallbackReason: string | null
}

export interface GContext {
  policy: GPolicy | null
  features: GFeature[]
  featureVersion: string | null
  snapshotTime: string | null
  neighbors: GNeighborStats | null
  unavailable: boolean
  stale: boolean
  reason: string | null
}

export interface GHealth {
  state: string | null // NOT_CONFIGURED | READY | DEGRADED | AUTH_FAILED as reported
  latencyMs: number | null
  projectionLagSeconds: number | null
  outboxBacklog: number | null
  lastProjectedAt: string | null
  reason: string | null
}

/** Display model handed to the 3D scene (maps to GraphData in ~/types) plus the 2D fallback. */
export type PolicyPanelState = 'unavailable' | 'stale' | 'not_graduated' | 'active'
