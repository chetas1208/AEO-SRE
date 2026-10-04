<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ControlPlaneGraphNode, ExperimentDetail } from '~/types'
import ControlPlaneGraph from '~/components/control-plane/ControlPlaneGraph.vue'
import CampaignCreateDrawer from '~/components/campaigns/CampaignCreateDrawer.vue'
import ExperimentCreateDrawer from '~/components/experiments/ExperimentCreateDrawer.vue'
import ProfoundLiveRail from '~/components/profound/ProfoundLiveRail.vue'
import MixpanelStatusRail from '~/components/mixpanel/MixpanelStatusRail.vue'

const showOptionalMixpanel = ref(false)

const { data, isLoading, error, refresh } = useControlPlane()

// Drawers & Modals
const isCampaignDrawerOpen = ref(false)
const isExperimentDrawerOpen = ref(false)
const selectedCampaignIdForExperiment = ref<string | undefined>(undefined)

// Selected node in control plane
const selectedNodeId = ref<string | null>(null)

// Normalized Summary Values
const summary = computed(() => {
  const s = data.value?.summary
  return {
    activeAgents: s?.activeAgents ?? s?.active_agents ?? 0,
    runningCampaigns: s?.runningCampaigns ?? s?.running_campaigns ?? 0,
    modelCostToday: s?.modelCostToday ?? s?.model_cost_today ?? 0,
    attributedReturn: s?.attributedReturn ?? s?.attributed_return ?? 0,
    decisionsNeedingReview: s?.decisionsNeedingReview ?? s?.decisions_needing_review ?? 0,
    experimentsMeasuring: s?.experimentsMeasuring ?? s?.experiments_measuring ?? 0,
  }
})

// Graph Nodes & Edges
const graphNodes = computed(() => data.value?.graph?.nodes ?? [])
const graphEdges = computed(() => data.value?.graph?.edges ?? [])

function handleNodeSelect(node: ControlPlaneGraphNode) {
  selectedNodeId.value = node.id
}

function handleClearSelection() {
  selectedNodeId.value = null
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

function formatCurrency(val?: number | null): string {
  if (val === null || val === undefined) return '—'
  return `$${Number(val).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
}

function formatCost(val?: number | null): string {
  if (val === null || val === undefined) return '$0.00'
  return `$${Number(val).toFixed(2)}`
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
          Live autonomous marketing operations · Dynamic knowledge graph · Laya decision prior
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

    <!-- COMPACT LIVE TELEMETRY RAIL (SLEEK & NON-INTRUSIVE) -->
    <section class="telemetry-rail" aria-label="Live Telemetry Rail">
      <div class="rail-item">
        <span class="rail-dot dot-cyan" />
        <span class="rail-label">Agents:</span>
        <strong class="rail-val tone-cyan">{{ summary.activeAgents }}</strong>
      </div>

      <div class="rail-separator" aria-hidden="true" />

      <div class="rail-item">
        <span class="rail-dot dot-indigo" />
        <span class="rail-label">Campaigns:</span>
        <strong class="rail-val tone-indigo">{{ summary.runningCampaigns }}</strong>
      </div>

      <div class="rail-separator" aria-hidden="true" />

      <div class="rail-item">
        <span class="rail-label">Model Cost:</span>
        <strong class="rail-val">{{ formatCost(summary.modelCostToday) }}</strong>
        <span class="rail-pill pill-purple">Haiku-first</span>
      </div>

      <div class="rail-separator" aria-hidden="true" />

      <div class="rail-item">
        <span class="rail-label">Attributed Return:</span>
        <strong class="rail-val tone-emerald">{{ formatCurrency(summary.attributedReturn) }}</strong>
        <span class="rail-pill pill-emerald">Verified</span>
      </div>

      <div class="rail-separator" aria-hidden="true" />

      <div class="rail-item">
        <span class="rail-label">Reviews:</span>
        <strong :class="['rail-val', summary.decisionsNeedingReview > 0 ? 'tone-amber' : '']">
          {{ summary.decisionsNeedingReview }}
        </strong>
      </div>

      <div class="rail-separator" aria-hidden="true" />

      <div class="rail-item">
        <span class="rail-label">Measuring Experiments:</span>
        <strong class="rail-val tone-sky">{{ summary.experimentsMeasuring }}</strong>
        <span class="rail-pill pill-sky">Change Guard</span>
      </div>
    </section>

    <!-- LIVE DATA: Profound API (primary) -->
    <ProfoundLiveRail />

    <!-- Optional: Mixpanel product analytics (separate from Profound; not required for live discovery) -->
    <details class="optional-mixpanel" @toggle="showOptionalMixpanel = ($event.target as HTMLDetailsElement).open">
      <summary>Optional Mixpanel export (not your live Profound feed)</summary>
      <MixpanelStatusRail v-if="showOptionalMixpanel" />
    </details>

    <!-- CENTERPIECE: LIVING CONTROL PLANE GRAPH (60-70% OF VIEWPORT) -->
    <main class="control-plane-centerpiece" aria-label="Control Plane Knowledge Graph">
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
.optional-mixpanel {
  font-size: 12px;
  color: #64748b;
}
.optional-mixpanel summary {
  cursor: pointer;
  padding: 8px 4px;
  list-style: none;
}
.optional-mixpanel summary::-webkit-details-marker {
  display: none;
}
.optional-mixpanel[open] summary {
  margin-bottom: 8px;
  color: #94a3b8;
}

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

/* COMPACT TELEMETRY RAIL */
.telemetry-rail {
  display: flex;
  align-items: center;
  gap: 14px;
  background: rgba(13, 19, 34, 0.75);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 16px;
  overflow-x: auto;
  box-sizing: border-box;
}

.rail-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  white-space: nowrap;
}

.rail-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.dot-cyan { background: #38bdf8; }
.dot-indigo { background: #6366f1; }

.rail-label {
  color: var(--text-dim);
}

.rail-val {
  color: var(--text-primary);
  font-weight: 700;
}

.rail-pill {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  text-transform: uppercase;
}
.pill-purple { background: rgba(168, 85, 247, 0.2); color: #c084fc; }
.pill-emerald { background: rgba(16, 185, 129, 0.2); color: #34d399; }
.pill-sky { background: rgba(56, 189, 248, 0.2); color: #38bdf8; }

.rail-separator {
  width: 1px;
  height: 14px;
  background: var(--border-subtle);
  flex-shrink: 0;
}

.tone-cyan { color: #38bdf8; }
.tone-indigo { color: #818cf8; }
.tone-emerald { color: #34d399; }
.tone-amber { color: #f59e0b; }
.tone-sky { color: #38bdf8; }

/* CENTERPIECE GRAPH */
.control-plane-centerpiece {
  width: 100%;
  flex: 1;
}
</style>
