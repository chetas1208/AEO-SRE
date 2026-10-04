<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ControlPlaneGraphNode, ExperimentDetail } from '~/types'
import ControlPlaneGraph from '~/components/control-plane/ControlPlaneGraph.vue'
import ControlPlaneAgentsList from '~/components/control-plane/ControlPlaneAgentsList.vue'
import ControlPlaneFlightDeck from '~/components/control-plane/ControlPlaneFlightDeck.vue'
import CampaignCreateDrawer from '~/components/campaigns/CampaignCreateDrawer.vue'
import ExperimentCreateDrawer from '~/components/experiments/ExperimentCreateDrawer.vue'
import type { AgentActivity } from '~/types'

const { data, isLoading, error, refresh } = useControlPlane()

// Drawers & Modals
const isCampaignDrawerOpen = ref(false)
const isExperimentDrawerOpen = ref(false)
const selectedCampaignIdForExperiment = ref<string | undefined>(undefined)

// Selected node in control plane
const selectedNodeId = ref<string | null>(null)

// Graph Nodes & Edges
const graphNodes = computed(() => data.value?.graph?.nodes ?? [])
const graphEdges = computed(() => data.value?.graph?.edges ?? [])
const agents = computed(() => data.value?.agents ?? [])

function selectGraphNodeId(id: string | null) {
  selectedNodeId.value = id
}

function handleAgentSelect(agent: AgentActivity) {
  const byId = graphNodes.value.find(n => n.id === agent.id)
  if (byId) {
    selectGraphNodeId(byId.id)
    return
  }
  const byLabel = graphNodes.value.find(
    n => n.type === 'agent' && n.label.toLowerCase() === agent.name.toLowerCase()
  )
  selectGraphNodeId(byLabel?.id ?? agent.id)
}

function handleCampaignSelect(campaignId: string) {
  const node = graphNodes.value.find(n => n.id === campaignId && n.type === 'campaign')
  selectGraphNodeId(node?.id ?? campaignId)
}

function handleNodeSelect(node: ControlPlaneGraphNode) {
  selectedNodeId.value = node.id
}

function handleClearSelection() {
  selectGraphNodeId(null)
}

function handleOpenCreateExperiment(campaignId?: string) {
  selectedCampaignIdForExperiment.value = campaignId
  isExperimentDrawerOpen.value = true
}

function handleCampaignCreated(_campaign: any) {
  refresh()
}

function handleExperimentCreated(_detail: ExperimentDetail) {
  refresh()
}

</script>

<template>
  <div class="control-plane-page" data-testid="control-plane-page">
    <!-- PAGE HEADER -->
    <header class="page-header" aria-label="Control Plane Header">
      <div class="header-left">
        <div class="title-row">
          <h1 class="page-title">Agent Control Plane</h1>
          <span :class="['mode-badge', data?.sourceMode === 'TEST' || data?.source_mode === 'TEST' ? 'mode-test' : 'mode-live']">
            <span class="live-dot" aria-hidden="true" />
            {{
              data?.sourceMode === 'TEST' || data?.source_mode === 'TEST'
                ? 'TEST SCENARIO'
                : (data?.dataProvenance === 'FIXTURE' || data?.data_provenance === 'FIXTURE' ? 'FIXTURE DEMO' : 'LIVE TOPOLOGY')
            }}
          </span>
          <span v-if="isLoading" class="sync-indicator">Syncing...</span>
        </div>
        <p class="page-subtitle">
          Cost, return, agents, and experiments in one live flight deck
        </p>
      </div>

      <div class="header-actions">
        <button
          type="button"
          class="btn-secondary"
          :disabled="isLoading"
          title="Refresh telemetry"
          @click="refresh()"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
          </svg>
          <span>Refresh</span>
        </button>

        <button
          type="button"
          class="btn-secondary"
          @click="isCampaignDrawerOpen = true"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          <span>+ Campaign</span>
        </button>

        <button
          type="button"
          class="btn-primary"
          @click="isExperimentDrawerOpen = true"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          <span>+ Experiment</span>
        </button>
      </div>
    </header>

    <div v-if="error" class="page-error" role="alert">
      {{ error.message }}
    </div>

    <ControlPlaneFlightDeck
      :data="data"
      :loading="isLoading"
      @select-campaign="handleCampaignSelect"
      @select-agent="handleAgentSelect"
    />

    <!-- Graph + live agent registry -->
    <main class="control-plane-centerpiece" aria-label="Control plane graph and agents">
      <ControlPlaneAgentsList
        :agents="agents"
        :selected-agent-id="selectedNodeId"
        :loading="isLoading"
        @select="handleAgentSelect"
      />
      <ControlPlaneGraph
        :nodes="graphNodes"
        :edges="graphEdges"
        :selected-node-id="selectedNodeId"
        @select="handleNodeSelect"
        @clear-selection="handleClearSelection"
        @open-create-experiment="handleOpenCreateExperiment"
      />
    </main>

    <!-- DRAWERS -->
    <CampaignCreateDrawer
      :open="isCampaignDrawerOpen"
      @close="isCampaignDrawerOpen = false"
      @created="handleCampaignCreated"
    />

    <ExperimentCreateDrawer
      v-model="isExperimentDrawerOpen"
      :initial-campaign-id="selectedCampaignIdForExperiment"
      initial-trigger="campaign"
      @created="handleExperimentCreated"
    />
  </div>
</template>

<style scoped>
.control-plane-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 24px 32px;
  max-width: 1680px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

/* PAGE HEADER */
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding-bottom: 2px;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.page-title {
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: #f8fafc;
  margin: 0;
  line-height: 1.2;
}

.mode-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
  padding: 2px 8px;
  border-radius: 999px;
}
.mode-live {
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.4);
  color: #34d399;
}
.mode-test {
  background: rgba(245, 158, 11, 0.15);
  border: 1px solid rgba(245, 158, 11, 0.4);
  color: #fbbf24;
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  animation: pulse-glow 2s infinite ease-in-out;
}

.sync-indicator {
  font-size: 11px;
  color: var(--text-faint);
  font-weight: 500;
}

.page-subtitle {
  font-size: 13px;
  color: var(--text-dim);
  margin: 0;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.btn-secondary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(15, 21, 38, 0.85);
  border: 1px solid var(--border);
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-secondary:hover:not(:disabled) {
  background: var(--surface-hover);
  border-color: var(--border-strong);
}

.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #ffffff;
  font-size: 12px;
  font-weight: 600;
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  box-shadow: 0 2px 10px var(--primary-glow);
  transition: all 0.15s ease;
}
.btn-primary:hover {
  background: linear-gradient(135deg, #4338ca 0%, #4f46e5 100%);
  box-shadow: 0 4px 14px var(--primary-glow);
}

.page-error {
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.1);
  color: #fca5a5;
  font-size: 12px;
}

/* Graph + agents side-by-side */
.control-plane-centerpiece {
  width: 100%;
  flex: 1;
  display: grid;
  grid-template-columns: minmax(280px, 320px) minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
}

@media (max-width: 1100px) {
  .control-plane-centerpiece {
    grid-template-columns: 1fr;
  }
}
</style>
