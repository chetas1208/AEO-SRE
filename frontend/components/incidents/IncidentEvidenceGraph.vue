<script setup lang="ts">
import type { GraphData, GraphNode } from '~/types'

const props = defineProps<{ graph: GraphData }>()
const hidden = ref<Set<string>>(new Set())
const selected = ref<string | null>(null)
const viewMode = ref<'3d' | '2d'>(canRender3d() ? '3d' : '2d')

const typesPresent = computed(() => [...new Set(props.graph.nodes.map((n) => n.type))])
const filtered = computed<GraphData>(() => {
  const nodes = props.graph.nodes.filter((n) => !hidden.value.has(n.type))
  const ids = new Set(nodes.map((n) => n.id))
  return { nodes, edges: props.graph.edges.filter((e) => ids.has(e.src) && ids.has(e.dst)) }
})
const layout = computed(() => layoutGraph(filtered.value))
const pos = computed(() => new Map(layout.value.nodes.map((n) => [n.id, n])))
const inspected = computed<GraphNode | null>(() => props.graph.nodes.find((n) => n.id === selected.value) ?? null)
const titleOf = (id: string) => props.graph.nodes.find((n) => n.id === id)?.title ?? id
const colorOf = (t: string) => NODE_COLORS[t] ?? '#8892b0'

function toggle(t: string) {
  const s = new Set(hidden.value)
  if (s.has(t)) s.delete(t); else s.add(t)
  hidden.value = s
}
function clip(s: string, n = 24) { return s.length > n ? s.slice(0, n - 1) + '…' : s }
function edgePath(a: { x: number; y: number }, b: { x: number; y: number }) {
  const x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H, x2 = b.x + NODE_W / 2, y2 = b.y
  const my = (y1 + y2) / 2
  return `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`
}

function onSelect3dNode(id: string | null) {
  selected.value = id
}
</script>

<template>
  <section class="card stack sm" aria-label="Evidence graph" data-testid="evidence-graph">
    <div class="row spread">
      <div class="row" style="gap: 8px;">
        <h3>Evidence Graph</h3>
        <span class="meta faint">({{ filtered.nodes.length }} nodes)</span>
      </div>
      <div class="view-toggle">
        <button
          type="button"
          :class="['toggle-pill', { active: viewMode === '3d' }]"
          @click="viewMode = '3d'"
        >
          3D Spatial
        </button>
        <button
          type="button"
          :class="['toggle-pill', { active: viewMode === '2d' }]"
          @click="viewMode = '2d'"
        >
          2D Schema
        </button>
      </div>
    </div>

    <!-- Node filter chips -->
    <fieldset class="row wrap filter-row" style="border: 0; padding: 0; margin: 0">
      <legend class="sr-only">Filter node types</legend>
      <label v-for="t in typesPresent" :key="t" class="type-filter-chip" :class="{ disabled: hidden.has(t) }">
        <input type="checkbox" class="sr-only" :checked="!hidden.has(t)" @change="toggle(t)">
        <span class="filter-dot" :style="{ backgroundColor: colorOf(t) }" aria-hidden="true" />
        <span>{{ NODE_TYPE_LABEL[t] ?? humanize(t) }}</span>
      </label>
    </fieldset>

    <!-- 3D Spatial Visualization -->
    <div v-if="viewMode === '3d' && filtered.nodes.length" class="graph-3d-container">
      <ClientOnly>
        <EvidenceScene
          :graph="filtered"
          :selected-node-id="selected"
          @select-node="onSelect3dNode"
        />
        <template #fallback>
          <div class="loading-3d">
            <span class="dim">Initializing 3D spatial scene…</span>
          </div>
        </template>
      </ClientOnly>
    </div>

    <!-- 2D SVG Fallback Visualization -->
    <div v-else-if="viewMode === '2d' && layout.nodes.length" class="graph-wrap">
      <svg :width="layout.width" :height="layout.height" role="img" :aria-label="`Evidence graph with ${layout.nodes.length} nodes and ${filtered.edges.length} edges.`">
        <defs>
          <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="#6f7893" />
          </marker>
        </defs>
        <g>
          <path
            v-for="(e, k) in filtered.edges" :key="e.id ?? k"
            v-show="pos.get(e.src) && pos.get(e.dst)"
            :d="pos.get(e.src) && pos.get(e.dst) ? edgePath(pos.get(e.src)!, pos.get(e.dst)!) : ''"
            fill="none" :stroke="e.edgeType === 'contradicts' ? '#f43f5e' : '#6f7893'" stroke-width="1.5"
            :stroke-dasharray="e.edgeType === 'contradicts' || e.edgeType === 'associated_with' || e.backEdge ? '4 3' : undefined" marker-end="url(#arrow)"
          >
            <title>{{ titleOf(e.src) }} {{ humanize(e.edgeType) }} {{ titleOf(e.dst) }}</title>
          </path>
        </g>
        <g
          v-for="n in layout.nodes" :key="n.id" class="graph-node" tabindex="0" role="button"
          :aria-label="`${NODE_TYPE_LABEL[n.type] ?? n.type}: ${n.title}`"
          @click="selected = n.id" @keydown.enter="selected = n.id" @keydown.space.prevent="selected = n.id"
        >
          <rect :x="n.x" :y="n.y" :width="n.w" :height="n.h" rx="8" fill="#151922" :stroke="colorOf(n.type)" :stroke-width="selected === n.id ? 3 : 1.5" />
          <text :x="n.x + 10" :y="n.y + 19" fill="#e6e9f2" font-size="12">{{ clip(n.title) }}</text>
          <text :x="n.x + 10" :y="n.y + 34" :fill="colorOf(n.type)" font-size="10">{{ NODE_TYPE_LABEL[n.type] ?? humanize(n.type) }}</text>
          <title>{{ n.title }}</title>
        </g>
      </svg>
    </div>

    <EmptyState v-else title="The evidence graph has no nodes yet." :lines="['Run or wait for the investigation to collect evidence.']" />

    <!-- DOM-side Node Inspector Panel -->
    <aside v-if="inspected" class="card inspector-card" aria-label="Node inspector" data-testid="node-inspector">
      <div class="row spread">
        <strong>{{ inspected.title }}</strong>
        <button type="button" class="link" @click="selected = null">Close</button>
      </div>
      <dl class="kv">
        <dt>Type</dt><dd>{{ NODE_TYPE_LABEL[inspected.type] ?? humanize(inspected.type) }}</dd>
        <dt>Source</dt><dd>{{ inspected.source ?? 'not reported' }}</dd>
        <dt>Observed at</dt><dd>{{ absoluteTime(inspected.observedAt) }}</dd>
        <dt>Confidence</dt><dd><ConfidenceBadge :value="inspected.confidence" /></dd>
        <dt>Status</dt><dd>{{ inspected.status ? humanize(inspected.status) : 'not reported' }}</dd>
        <dt>Relevant extract</dt>
        <dd>
          <blockquote v-if="inspected.excerpt" class="extract" style="margin: 0">{{ inspected.excerpt }}</blockquote>
          <span v-else class="dim">none</span>
        </dd>
        <dt>Open source</dt>
        <dd>
          <a v-if="inspected.url" :href="inspected.url" target="_blank" rel="noopener noreferrer">{{ inspected.url }}</a>
          <span v-else class="dim">no URL</span>
        </dd>
      </dl>
    </aside>

    <p v-for="w in graph.warnings ?? []" :key="w" class="meta tone-warn" role="status">Graph integrity: {{ w }}</p>

    <!-- Accessible text alternative for screen readers -->
    <details>
      <summary class="meta">Text alternative ({{ graph.nodes.length }} nodes, {{ graph.edges.length }} edges)</summary>
      <ul style="margin: 4px 0 0; padding-left: 20px;">
        <li v-for="n in graph.nodes" :key="n.id"><strong>{{ n.title }}</strong> <span class="meta">({{ NODE_TYPE_LABEL[n.type] ?? n.type }})</span></li>
      </ul>
      <ul v-if="graph.edges.length" style="margin: 4px 0 0; padding-left: 20px;">
        <li v-for="(e, k) in graph.edges" :key="e.id ?? k">{{ titleOf(e.src) }} <em>{{ humanize(e.edgeType) }}</em> {{ titleOf(e.dst) }}</li>
      </ul>
    </details>
  </section>
</template>

<style scoped>
.view-toggle {
  display: flex;
  background: var(--bg-1);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 2px;
}
.toggle-pill {
  background: transparent;
  border: 0;
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-dim);
}
.toggle-pill.active {
  background: var(--surface-hover);
  color: var(--text-primary);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
}
.filter-row {
  display: flex;
  gap: 6px;
}
.type-filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 11px;
  background: rgba(15, 21, 38, 0.7);
  border: 1px solid var(--border);
  cursor: pointer;
  user-select: none;
  color: var(--text-dim);
  transition: all var(--motion-fast) var(--ease-calm);
}
.type-filter-chip:hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
}
.type-filter-chip.disabled {
  opacity: 0.4;
  text-decoration: line-through;
}
.filter-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.graph-3d-container {
  width: 100%;
  border-radius: var(--radius);
  overflow: hidden;
}
.loading-3d {
  height: 400px;
  display: grid;
  place-items: center;
  background: #070a13;
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.inspector-card {
  margin-top: 10px;
  background: var(--surface-card);
  border-color: var(--primary);
  box-shadow: 0 4px 20px rgba(99, 102, 241, 0.2);
}
</style>
