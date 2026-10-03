<script setup lang="ts">
import type { GraphNodeSemantic, GraphEdgeSemantic } from '~/types/agentmatch'

const props = defineProps<{
  nodes: GraphNodeSemantic[]
  edges: GraphEdgeSemantic[]
  selectedNodeId?: string | null
}>()

const emit = defineEmits<{
  selectNode: [id: string]
}>()

const LAYER_CONFIG = [
  { z: -8, title: 'Layer 1: Personal Agent Intent', description: 'Real-time demand envelope & explicit constraints', color: '#38bdf8' },
  { z: -4, title: 'Layer 2: Evaluated Constraints', description: 'Explicit requirements matched against truth', color: '#60a5fa' },
  { z: 0, title: 'Layer 3: Candidate Product', description: 'Evaluated product architecture & capability profile', color: '#a78bfa' },
  { z: 4, title: 'Layer 4: Verified Product Truth', description: 'Canonical facts & documentation proofs', color: '#34d399' },
  { z: 7, title: 'Layer 5: AI Engine Perception', description: 'Profound synthesized citations across LLMs', color: '#818cf8' },
  { z: 10, title: 'Layer 6: Discovery Gaps', description: 'Discrepancy where product wins but AI misrepresents', color: '#f43f5e' }
]

function getNodesInLayer(z: number): GraphNodeSemantic[] {
  return props.nodes.filter(n => n.z === z)
}
</script>

<template>
  <div class="tree-container" role="tree" aria-label="AgentMatch Semantic Graph Tree">
    <div class="tree-header">
      <span class="tree-badge">Semantic Layer Hierarchy (DOM Fallback)</span>
      <span class="tree-count">{{ nodes.length }} nodes · {{ edges.length }} causal relations</span>
    </div>

    <div class="layers-list">
      <div
        v-for="layer in LAYER_CONFIG"
        :key="layer.z"
        class="layer-group"
      >
        <div class="layer-header" :style="{ borderLeftColor: layer.color }">
          <div class="layer-meta">
            <span class="layer-z" :style="{ color: layer.color }">z = {{ layer.z > 0 ? `+${layer.z}` : layer.z }}</span>
            <span class="layer-title">{{ layer.title }}</span>
          </div>
          <p class="layer-desc">{{ layer.description }}</p>
        </div>

        <div class="layer-nodes">
          <div
            v-for="node in getNodesInLayer(layer.z)"
            :key="node.id"
            role="treeitem"
            :tabindex="0"
            :aria-selected="selectedNodeId === node.id"
            :class="['tree-node-card', { active: selectedNodeId === node.id }]"
            :style="{ borderColor: selectedNodeId === node.id ? layer.color : 'rgba(255,255,255,0.08)' }"
            @click="emit('selectNode', node.id)"
            @keydown.enter="emit('selectNode', node.id)"
          >
            <div class="node-top">
              <span class="node-dot" :style="{ backgroundColor: node.color }" />
              <span class="node-label">{{ node.label }}</span>
            </div>
            <div v-if="node.status" class="node-status">
              Status: <span class="status-text">{{ node.status }}</span>
            </div>
          </div>
          <div v-if="getNodesInLayer(layer.z).length === 0" class="empty-layer">
            No active nodes in this layer for current selection.
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tree-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: rgba(10, 15, 29, 0.95);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: var(--radius-md, 8px);
  padding: 16px;
  max-height: 480px;
  overflow-y: auto;
}
.tree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.tree-badge {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.12);
  padding: 3px 8px;
  border-radius: 4px;
}
.tree-count {
  font-size: 12px;
  color: var(--fg-dim, #64748b);
  font-family: var(--font-mono, monospace);
}
.layers-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.layer-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.layer-header {
  border-left: 3px solid #38bdf8;
  padding-left: 10px;
}
.layer-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}
.layer-z {
  font-size: 11px;
  font-family: var(--font-mono, monospace);
  font-weight: 700;
}
.layer-title {
  font-size: 13px;
  font-weight: 600;
  color: #f1f5f9;
}
.layer-desc {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
}
.layer-nodes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 8px;
  margin-left: 12px;
}
.tree-node-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  background: rgba(18, 26, 47, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.tree-node-card:hover, .tree-node-card:focus {
  background: rgba(30, 41, 69, 0.9);
  outline: none;
}
.tree-node-card.active {
  background: rgba(30, 58, 110, 0.5);
}
.node-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.node-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.node-label {
  font-size: 12px;
  font-weight: 500;
  color: #e2e8f0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.node-status {
  font-size: 11px;
  color: #94a3b8;
}
.status-text {
  color: #38bdf8;
  font-family: var(--font-mono, monospace);
}
.empty-layer {
  font-size: 11px;
  color: #64748b;
  font-style: italic;
  padding: 4px 8px;
}
</style>
