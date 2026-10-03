import type { GraphData, GraphNode } from '~/types'

export interface PlacedNode extends GraphNode { x: number; y: number; w: number; h: number; layer: number }

export const NODE_W = 168
export const NODE_H = 44
const GAP_X = 24
const GAP_Y = 56

/** Layered layout for display only. Uses API `level` when present, else BFS depth from edge sources. */
export function layoutGraph(g: GraphData): { nodes: PlacedNode[]; width: number; height: number } {
  const byId = new Map(g.nodes.map((n) => [n.id, n]))
  const out = new Map<string, string[]>()
  const und = new Map<string, string[]>()
  const indeg = new Map<string, number>()
  for (const n of g.nodes) { out.set(n.id, []); und.set(n.id, []); indeg.set(n.id, 0) }
  for (const e of g.edges) {
    if (!byId.has(e.src) || !byId.has(e.dst)) continue
    out.get(e.src)!.push(e.dst)
    und.get(e.src)!.push(e.dst)
    und.get(e.dst)!.push(e.src)
    indeg.set(e.dst, (indeg.get(e.dst) ?? 0) + 1)
  }
  const layer = new Map<string, number>()
  const hasLevels = g.nodes.length > 0 && g.nodes.every((n) => n.level != null)
  if (hasLevels) {
    for (const n of g.nodes) layer.set(n.id, n.level as number)
  } else {
    const roots = g.nodes.filter((n) => (indeg.get(n.id) ?? 0) === 0).map((n) => n.id)
    const queue = roots.length ? [...roots] : g.nodes.slice(0, 1).map((n) => n.id)
    for (const r of queue) layer.set(r, 0)
    while (queue.length) {
      const id = queue.shift()!
      for (const nx of [...(out.get(id) ?? []), ...(roots.length ? [] : (und.get(id) ?? []))]) {
        if (!layer.has(nx)) { layer.set(nx, layer.get(id)! + 1); queue.push(nx) }
      }
    }
    for (const n of g.nodes) if (!layer.has(n.id)) layer.set(n.id, 0)
  }
  const layers = new Map<number, GraphNode[]>()
  for (const n of g.nodes) {
    const l = layer.get(n.id) ?? 0
    layers.set(l, [...(layers.get(l) ?? []), n])
  }
  const keys = [...layers.keys()].sort((a, b) => a - b)
  const maxCount = Math.max(1, ...keys.map((k) => layers.get(k)!.length))
  const width = maxCount * NODE_W + (maxCount - 1) * GAP_X + 24
  const nodes: PlacedNode[] = []
  keys.forEach((k, row) => {
    const items = layers.get(k)!
    const rowW = items.length * NODE_W + (items.length - 1) * GAP_X
    const x0 = (width - rowW) / 2
    items.forEach((n, col) => nodes.push({ ...n, x: x0 + col * (NODE_W + GAP_X), y: 12 + row * (NODE_H + GAP_Y), w: NODE_W, h: NODE_H, layer: k }))
  })
  return { nodes, width, height: keys.length * (NODE_H + GAP_Y) + 12 }
}

export const NODE_COLORS: Record<string, string> = {
  profound: '#6f7bff', agent: '#6366f1', changeset: '#38bdf8', target: '#10b981',
  claim: '#38bdf8', prompt_cluster: '#8b5cf6', experiment: '#3b82f6',
  canonical_truth: '#06b6d4', conflict: '#f43f5e', owned: '#2dd4bf',
  competitor: '#f2565f', external: '#f5b04a', inference: '#b18cff',
  hypothesis: '#a855f7', intervention: '#6366f1', incident: '#f43f5e'
}
export const NODE_TYPE_LABEL: Record<string, string> = {
  profound: 'Profound signal', agent: 'Profound Agent', changeset: 'Proposed ChangeSet',
  target: 'Target Surface', claim: 'Proposed Claim', prompt_cluster: 'Prompt Cluster',
  experiment: 'Active Experiment', canonical_truth: 'Canonical Truth', conflict: 'Detected Collision',
  owned: 'Owned content', competitor: 'Competitor content', external: 'External source',
  inference: 'Inference / hypothesis', hypothesis: 'Hypothesis', intervention: 'Proposed Intervention',
  incident: 'Incident'
}
