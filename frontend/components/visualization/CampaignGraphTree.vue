<script setup lang="ts">
import type { CampaignGraphNode, CampaignGraphEdge } from '~/types/campaign'

const props = defineProps<{
  nodes: CampaignGraphNode[]
  edges: CampaignGraphEdge[]
  selectedNodeId?: string | null
  highlightNodeIds?: string[] | null
}>()

const emit = defineEmits<{
  selectNode: [id: string]
}>()

const LAYER_CONFIG = [
  { z: -12, title: 'Layer 1: Costs & Financial Inputs', description: 'Paid media, contractor fees, and compute/API costs', color: '#f59e0b' },
  { z: -8, title: 'Layer 2: Contributors (People & Agents)', description: 'Human team members, contractors, and autonomous agent runs', color: '#38bdf8' },
  { z: -3, title: 'Layer 3: Production Assets', description: 'Landing pages, technical whitepapers, videos, and collateral', color: '#22d3ee' },
  { z: 0, title: 'Layer 4: Campaign Nexus', description: 'Central marketing initiative and coordinated distribution', color: '#6366f1' },
  { z: 5, title: 'Layer 5: Distribution Channels', description: 'Web canonical indexes, syndication, and search visibility', color: '#60a5fa' },
  { z: 9, title: 'Layer 6: AI Engine Signals (Profound & Muse)', description: 'Observed visibility shifts, citations, and buyer agent shortlists', color: '#a855f7' },
  { z: 13, title: 'Layer 7: Business Outcomes & Revenue', description: 'Attributed pipeline, closed deals, and measured ROI', color: '#10b981' }
]

function getNodesInLayer(z: number): CampaignGraphNode[] {
  return props.nodes.filter(n => n.z === z)
}

function isDimmed(nodeId: string): boolean {
  if (!props.highlightNodeIds || props.highlightNodeIds.length === 0) return false
  return !props.highlightNodeIds.includes(nodeId)
}
</script>

<template>
  <div class="campaign-tree" role="tree" aria-label="Campaign Lineage Hierarchy">
    <div class="tree-header">
      <span class="tree-badge">Campaign Lineage (Accessible DOM Fallback)</span>
      <span class="tree-count">{{ nodes.length }} nodes · {{ edges.length }} lineage edges</span>
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
            :class="['tree-node-card', { active: selectedNodeId === node.id, dimmed: isDimmed(node.id) }]"
            :style="{ borderColor: selectedNodeId === node.id ? layer.color : 'rgba(255,255,255,0.08)' }"
            @click="emit('selectNode', node.id)"
            @keydown.enter="emit('selectNode', node.id)"
          >
            <div class="node-top">
              <span class="node-dot" :style="{ backgroundColor: node.color }" />
              <span class="node-label">{{ node.label }}</span>
            </div>
            <div class="node-meta">
              <span v-if="node.amount" class="node-amount">${{ Number(node.amount).toLocaleString() }}</span>
              <span v-if="node.confidence" :class="['node-conf', `conf-${node.confidence.toLowerCase()}`]">
                {{ node.confidence }}
              </span>
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
.campaign-tree {
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
  color: #6366f1;
  background: rgba(99, 102, 241, 0.15);
  padding: 3px 8px;
  border-radius: 4px;
}
.tree-count {
  font-size: 12px;
  color: #64748b;
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
  border-left: 3px solid #6366f1;
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
  background: rgba(49, 46, 129, 0.5);
}
.tree-node-card.dimmed {
  opacity: 0.25;
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
.node-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}
.node-amount {
  color: #34d399;
  font-family: var(--font-mono, monospace);
  font-weight: 600;
}
.node-conf {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 4px;
  border-radius: 3px;
}
.conf-direct {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
}
.conf-attributed {
  background: rgba(56, 189, 248, 0.2);
  color: #38bdf8;
}
.conf-modeled {
  background: rgba(245, 158, 11, 0.2);
  color: #fde68a;
}
.empty-layer {
  font-size: 11px;
  color: #64748b;
  font-style: italic;
  padding: 4px 8px;
}
</style>
