<script setup lang="ts">
import type { GraphData } from '~/types'
import type { HighlightSet } from '~/utils/graph'
import { layoutGraph, NODE_COLORS, NODE_H, NODE_TYPE_LABEL, NODE_W } from '~/utils/graphLayout'

// 2D/SVG rendering of the same normalized lineage (used when WebGL is unavailable, under automation, or by choice).
const props = defineProps<{ graph: GraphData; highlight: HighlightSet | null; selectedId: string | null }>()
const emit = defineEmits<{ select: [id: string | null] }>()
const layout = computed(() => layoutGraph(props.graph))
const pos = computed(() => new Map(layout.value.nodes.map((n) => [n.id, n])))
const colorOf = (t: string) => NODE_COLORS[t] ?? '#8892b0'
const dimmedNode = (id: string) => !!props.highlight && !props.highlight.nodes.has(id)
const dimmedEdge = (id?: string) => !!props.highlight && !(id && props.highlight.edges.has(id))
const clip = (s: string, n = 22) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)
function edgePath(a: { x: number; y: number }, b: { x: number; y: number }) {
  const x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H, x2 = b.x + NODE_W / 2, y2 = b.y
  const my = (y1 + y2) / 2
  return `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`
}
</script>

<template>
  <div class="graph-wrap" style="overflow: auto" data-testid="graph-2d">
    <svg :width="layout.width" :height="layout.height" role="img" :aria-label="`Lineage graph with ${layout.nodes.length} nodes and ${graph.edges.length} edges.`">
      <defs>
        <marker id="g-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#6f7893" /></marker>
      </defs>
      <g>
        <path
          v-for="(e, k) in graph.edges" :key="e.id ?? k" v-show="pos.get(e.src) && pos.get(e.dst)"
          :d="pos.get(e.src) && pos.get(e.dst) ? edgePath(pos.get(e.src)!, pos.get(e.dst)!) : ''" fill="none"
          :stroke="e.conflict ? '#f43f5e' : highlight && !dimmedEdge(e.id) ? '#38bdf8' : '#6f7893'" :stroke-width="highlight && !dimmedEdge(e.id) ? 3 : 1.5"
          :opacity="dimmedEdge(e.id) ? 0.12 : 1" :stroke-dasharray="e.conflict ? '4 3' : undefined" marker-end="url(#g-arrow)"
          :data-edge-id="e.id" :data-dimmed="dimmedEdge(e.id)"
        />
      </g>
      <g
        v-for="n in layout.nodes" :key="n.id" class="graph-node" tabindex="0" role="button" :opacity="dimmedNode(n.id) ? 0.25 : 1"
        :aria-label="`${n.role}: ${n.title}`" :data-node-id="n.id" :data-node-label="n.role" :data-dimmed="dimmedNode(n.id)"
        :data-highlighted="!!highlight && !dimmedNode(n.id)"
        @click="emit('select', n.id)" @keydown.enter="emit('select', n.id)" @keydown.space.prevent="emit('select', n.id)"
      >
        <rect :x="n.x" :y="n.y" :width="n.w" :height="n.h" rx="8" fill="#151922" :stroke="colorOf(n.type)" :stroke-width="selectedId === n.id || (highlight && !dimmedNode(n.id)) ? 3 : 1.5" />
        <text :x="n.x + 10" :y="n.y + 19" fill="#e6e9f2" font-size="12">{{ clip(n.title) }}</text>
        <text :x="n.x + 10" :y="n.y + 34" :fill="colorOf(n.type)" font-size="10">{{ n.role ?? NODE_TYPE_LABEL[n.type] ?? n.type }}</text>
        <title>{{ n.title }}</title>
      </g>
    </svg>
  </div>
</template>
