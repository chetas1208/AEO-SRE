// Pure helpers for the graph views: layout semantics, highlight sets, time window params, display mapping.
// No decision logic lives here: highlight paths and explanation statements come from the API; this only displays them.
import type { GraphData, GraphEdge, GraphNode } from '~/types'
import type { GContext, GEdge, GExplanation, GFeature, GHighlightPath, GNode, GraphView, GraphWindow, PolicyPanelState } from '~/types/graph'

export const GRAPH_WINDOWS: Array<{ id: GraphWindow; label: string; seconds: number }> = [
  { id: '1h', label: 'Last hour', seconds: 3600 },
  { id: '24h', label: '24 hours', seconds: 86400 },
  { id: '7d', label: '7 days', seconds: 7 * 86400 }
]
export const DEFAULT_GRAPH_WINDOW: GraphWindow = '24h'

/** `since` request parameter (ISO-8601 UTC) for a window, computed from an injectable clock. */
export function sinceParam(window: GraphWindow | null | undefined, now: Date = new Date()): string | null {
  const w = GRAPH_WINDOWS.find((x) => x.id === window)
  if (!w) return null
  return new Date(now.getTime() - w.seconds * 1000).toISOString()
}

// ---- layout semantics: upstream Agent -> Run -> Event, middle ChangeSet -> Target/Claims, downstream Conflict -> Decision -> Experiment
const DEPTH: Record<string, number> = {
  Organization: -9, Agent: -8, AgentRun: -6, Event: -4, Incident: -4, Intervention: -3,
  ChangeSet: -2, PromptCluster: 0, Target: 0, Claim: 0,
  Conflict: 3, Decision: 5, Approval: 6, PolicyDecision: 6, Experiment: 8, Observation: 9, Outcome: 10, PolicyVersion: 11
}
const LAYER: Record<string, number> = {
  Organization: 0, Agent: 1, AgentRun: 2, Event: 3, Incident: 3, Intervention: 3, ChangeSet: 4,
  PromptCluster: 5, Target: 5, Claim: 5, Conflict: 6, Decision: 7, Approval: 8, PolicyDecision: 8, Experiment: 9, Observation: 10, Outcome: 11, PolicyVersion: 12
}
export const nodeDepth = (label: string): number => DEPTH[label] ?? 0
export const nodeLayer = (label: string): number => LAYER[label] ?? 5

/** Scene colour/type key understood by the existing 3D scene (EvidenceScene TYPE_COLORS) and the 2D fallback. */
export function sceneType(label: string): string {
  switch (label) {
    case 'Agent': case 'AgentRun': return 'agent'
    case 'ChangeSet': return 'changeset'
    case 'Target': return 'target'
    case 'Claim': return 'claim'
    case 'Conflict': return 'conflict'
    case 'Experiment': return 'experiment'
    case 'PromptCluster': return 'prompt_cluster'
    case 'Incident': return 'incident'
    case 'Intervention': return 'intervention'
    default: return label.toLowerCase()
  }
}

const short = (v: unknown) => { const s = String(v ?? ''); return s.length > 12 ? s.slice(0, 8) : s }
const prop = (n: GNode, ...keys: string[]): string | null => {
  for (const k of keys) { const v = n.props[k]; if (v !== undefined && v !== null && v !== '') return String(v) }
  return null
}

/** Human title from REAL node properties; falls back to label + short id. Never invents content. */
export function nodeTitle(n: GNode): string {
  switch (n.label) {
    case 'Decision': return prop(n, 'decision') ?? `Decision ${short(n.id)}`
    case 'Event': return prop(n, 'event_type', 'eventType') ?? `Event ${short(n.id)}`
    case 'ChangeSet': return `Change ${prop(n, 'number', 'ref', 'key', 'name') ?? short(n.id)}`
    case 'Experiment': return prop(n, 'code', 'number', 'ref', 'name') ?? `Experiment ${short(n.id)}`
    case 'Target': return prop(n, 'target_key', 'targetKey', 'url', 'name') ?? `Target ${short(n.id)}`
    case 'Agent': return prop(n, 'name', 'agent_name', 'agentName', 'agent_ref') ?? `Agent ${short(n.id)}`
    case 'Conflict': return prop(n, 'conflict_type', 'conflictType', 'type') ? `Conflict: ${prop(n, 'conflict_type', 'conflictType', 'type')}` : `Conflict ${short(n.id)}`
    case 'PolicyVersion': return prop(n, 'version', 'name') ? `Policy ${prop(n, 'version', 'name')}` : `Policy ${short(n.id)}`
    case 'Outcome': return prop(n, 'label', 'outcome', 'name') ?? `Outcome ${short(n.id)}`
    default: return prop(n, 'name', 'title', 'label', 'text', 'claim_text') ?? `${n.label} ${short(n.id)}`
  }
}

const lc = (s: string) => s.toLowerCase()

/** Adapter: API view -> the existing 3D scene's GraphData. Only API nodes/edges are mapped; nothing is added. */
export function toGraphData(view: GraphView): GraphData {
  const ids = new Set(view.nodes.map((n) => n.id))
  const nodes: GraphNode[] = view.nodes.map((n) => ({
    id: n.id,
    type: sceneType(n.label),
    role: n.label,
    title: nodeTitle(n),
    source: n.source,
    observedAt: n.occurredAt,
    status: prop(n, 'status'),
    level: nodeLayer(n.label),
    depth: nodeDepth(n.label)
  } as GraphNode))
  const edges: GraphEdge[] = view.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({
    id: e.id, src: e.source, dst: e.target, edgeType: lc(e.type), conflict: e.type === 'CONFLICTS_WITH'
  }))
  return { nodes, edges }
}

// ---- highlight ---------------------------------------------------------------------------------------------
export interface HighlightSet { nodes: Set<string>; edges: Set<string>; pathIds: string[] }

/** Union of the given API paths. Edge ids come from the path when reported, else from consecutive node pairs that exist in the view. */
export function highlightSet(view: GraphView, paths: GHighlightPath[]): HighlightSet {
  const nodes = new Set<string>()
  const edges = new Set<string>()
  const viewIds = new Set(view.nodes.map((n) => n.id))
  const byPair = new Map<string, string[]>()
  for (const e of view.edges) {
    for (const k of [`${e.source}>${e.target}`, `${e.target}>${e.source}`]) byPair.set(k, [...(byPair.get(k) ?? []), e.id])
  }
  for (const p of paths) {
    for (const id of p.nodeIds) if (viewIds.has(id)) nodes.add(id)
    for (const id of p.edgeIds) edges.add(id)
    if (!p.edgeIds.length) {
      for (let i = 0; i + 1 < p.nodeIds.length; i++) for (const id of byPair.get(`${p.nodeIds[i]}>${p.nodeIds[i + 1]}`) ?? []) edges.add(id)
    }
  }
  return { nodes, edges, pathIds: paths.map((p) => p.id) }
}

export type PathKind = 'decision' | 'outcome' | 'all'
const isOutcome = (p: GHighlightPath) => p.kind.includes('outcome')

/** API paths relevant to a clicked node (a path that is anchored at, or passes through, the node), optionally by kind. */
export function pathsForNode(view: GraphView, nodeId: string, kind: PathKind = 'all'): GHighlightPath[] {
  return view.highlightPaths.filter((p) => (p.decisionId === nodeId || p.nodeIds.includes(nodeId)) &&
    (kind === 'all' || (kind === 'outcome' ? isOutcome(p) : !isOutcome(p))))
}

export function hasOutcomePath(view: GraphView, nodeId: string): boolean {
  return pathsForNode(view, nodeId, 'outcome').length > 0
}

/** Nodes an explanation statement refers to, restricted to nodes that exist in the view (never highlights absent ids). */
export function statementHighlight(view: GraphView, statement: { nodeIds: string[]; edgeIds: string[] } | null): HighlightSet | null {
  if (!statement) return null
  const ids = new Set(view.nodes.map((n) => n.id))
  return { nodes: new Set(statement.nodeIds.filter((i) => ids.has(i))), edges: new Set(statement.edgeIds), pathIds: [] }
}

export function mergeHighlight(a: HighlightSet | null, b: HighlightSet | null): HighlightSet | null {
  if (!a && !b) return null
  return { nodes: new Set([...(a?.nodes ?? []), ...(b?.nodes ?? [])]), edges: new Set([...(a?.edges ?? []), ...(b?.edges ?? [])]), pathIds: [...(a?.pathIds ?? []), ...(b?.pathIds ?? [])] }
}

// ---- graph state -------------------------------------------------------------------------------------------
export type GraphStatus = 'ok' | 'stale' | 'unavailable' | 'empty'

export function graphStatus(view: GraphView | null | undefined): GraphStatus {
  if (!view) return 'unavailable'
  if (view.unavailable) return 'unavailable'
  if (view.stale) return 'stale'
  if (!view.nodes.length) return 'empty'
  return 'ok'
}

const REASONS: Record<string, string> = {
  graph_context_stale: 'The graph projection is behind Postgres.',
  graph_stale: 'The graph projection is behind Postgres.',
  NOT_CONFIGURED: 'Neo4j is not configured for this deployment.',
  AUTH_FAILED: 'Neo4j rejected the configured credentials.',
  DEGRADED: 'Neo4j is unreachable or degraded.',
  changeset_not_projected: 'This change has not been projected into the graph yet.'
}
export function reasonText(reason: string | null | undefined): string {
  if (!reason) return 'No reason reported.'
  const key = reason.replace(/^graph_unavailable:/, '')
  return REASONS[reason] ?? REASONS[key] ?? reason
}

// ---- policy panel ------------------------------------------------------------------------------------------
/** Panel state from API flags only. No client-side decision logic. */
export function policyPanelState(ctx: GContext | null | undefined): PolicyPanelState {
  if (!ctx || ctx.unavailable || !ctx.policy) return 'unavailable'
  if (ctx.stale) return 'stale'
  if (ctx.policy.graduated === false || ctx.policy.baselineActive === true || ctx.policy.mode?.toLowerCase() === 'shadow') return 'not_graduated'
  return 'active'
}

export function policyHeadline(ctx: GContext | null | undefined): string {
  const p = ctx?.policy
  if (!p) return 'Policy unavailable'
  const v = p.version ? `Policy ${/^v/i.test(p.version) ? p.version : `v${p.version}`}` : 'Policy (version unavailable)'
  return `${v} — Recommendation: ${p.recommendation ?? 'unavailable'}`
}

export function shadowLabel(ctx: GContext | null | undefined): string | null {
  const p = ctx?.policy
  if (!p) return null
  if (p.baselineActive === true || p.graduated === false || p.mode?.toLowerCase() === 'shadow') return 'Shadow: baseline active'
  if (p.graduated === true || p.baselineActive === false) return 'Graduated: graph policy active'
  return null
}

export const fmtFeature = (f: GFeature): string => f.value === null ? 'unavailable' : Number.isInteger(f.value) ? String(f.value) : f.value.toFixed(3)
export const fmtRate = (v: number | null): string => v === null ? 'unavailable' : `${(v * 100).toFixed(0)}%`

// ---- inspector ---------------------------------------------------------------------------------------------
export function inspectorRows(n: GNode): Array<{ k: string; v: string }> {
  const rows: Array<{ k: string; v: string }> = [
    { k: 'Type', v: n.label }, { k: 'Id', v: n.id },
    { k: 'Occurred at', v: n.occurredAt ?? 'not reported' },
    { k: 'Source', v: n.source ?? 'not reported' },
    { k: 'Source mode', v: n.sourceMode ?? 'not reported' }
  ]
  if (n.recordedAt) rows.push({ k: 'Recorded at', v: n.recordedAt })
  for (const [k, v] of Object.entries(n.props)) {
    if (['occurred_at', 'recorded_at', 'source', 'source_mode', 'occurredAt', 'recordedAt', 'sourceMode'].includes(k)) continue
    if (v === null || typeof v === 'object') continue
    rows.push({ k, v: String(v) })
  }
  return rows
}

export function explanationNodeSet(view: GraphView, e: GExplanation | null): Set<string> {
  const ids = new Set(view.nodes.map((n) => n.id))
  const out = new Set<string>()
  for (const s of e?.statements ?? []) for (const i of s.nodeIds) if (ids.has(i)) out.add(i)
  return out
}

export type { GEdge }
