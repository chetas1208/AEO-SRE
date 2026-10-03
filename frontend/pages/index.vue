<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ControlPlaneGraphNode, ExperimentDetail, DecisionCard } from '~/types'
import AgentControlGraphScene from '~/components/control-plane/AgentControlGraphScene.vue'
import ExperimentCreateDrawer from '~/components/experiments/ExperimentCreateDrawer.vue'

const { data, isLoading, error, refresh } = useControlPlane()

// Flight deck filter & selection state
const activeFilter = ref<'ALL' | 'agent' | 'campaign' | 'decision' | 'experiment'>('ALL')
const selectedNodeId = ref<string | null>(null)
const isCreateDrawerOpen = ref(false)
const activeTab = ref<'agents' | 'campaigns' | 'decisions' | 'experiments'>('agents')

// Decision action simulation / local state
const decisionOverrides = ref<Record<string, 'APPROVED' | 'REJECTED' | 'MODIFIED'>>({})

function handleDecisionAction(id: string, action: 'APPROVED' | 'REJECTED' | 'MODIFIED') {
  decisionOverrides.value[id] = action
}

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

// Filtered Graph Nodes & Edges
const allNodes = computed(() => data.value?.graph?.nodes ?? [])
const allEdges = computed(() => data.value?.graph?.edges ?? [])

const filteredNodes = computed(() => {
  if (activeFilter.value === 'ALL') return allNodes.value
  return allNodes.value.filter((n) => n.type === activeFilter.value)
})

const filteredEdges = computed(() => {
  const visibleNodeIds = new Set(filteredNodes.value.map((n) => n.id))
  return allEdges.value.filter((e) => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target))
})

const selectedNode = computed(() => {
  if (!selectedNodeId.value) return null
  return allNodes.value.find((n) => n.id === selectedNodeId.value) || null
})

function handleNodeSelect(node: ControlPlaneGraphNode) {
  selectedNodeId.value = node.id
}

function handleClearSelection() {
  selectedNodeId.value = null
}

function handleExperimentCreated(_detail: ExperimentDetail) {
  refresh()
}

// Helpers for semantic formatting
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
  <div class="flight-deck-page">
    <!-- TOP CONTROL STRIP / HEADER -->
    <header class="flight-deck-header" aria-label="Flight Deck Header">
      <div class="header-left">
        <div class="title-row">
          <h1 class="page-title">Agent Control Plane</h1>
          <span :class="['mode-badge', data?.sourceMode === 'TEST' || data?.source_mode === 'TEST' ? 'mode-test' : 'mode-live']">
            <span class="live-dot" aria-hidden="true" />
            {{ data?.sourceMode === 'TEST' || data?.source_mode === 'TEST' ? 'TEST SCENARIO' : 'LIVE TOPOLOGY' }}
          </span>
          <span v-if="isLoading" class="loading-indicator">Syncing...</span>
        </div>
        <p class="page-subtitle">
          Real-time agent coordination, campaign attribution, policy review & Change Guard verification
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
          class="btn-primary"
          @click="isCreateDrawerOpen = true"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          <span>+ New Experiment</span>
        </button>
      </div>
    </header>

    <!-- EXECUTIVE METRICS STRIP -->
    <section class="kpi-strip" aria-label="Executive Metrics Strip">
      <div class="kpi-card">
        <span class="kpi-label">Active Agents</span>
        <div class="kpi-val-row">
          <span class="kpi-val tone-cyan">{{ summary.activeAgents }}</span>
          <span class="kpi-tag tag-cyan">Autonomous</span>
        </div>
        <span class="kpi-sub">Running & waiting tasks</span>
      </div>

      <div class="kpi-card">
        <span class="kpi-label">Running Campaigns</span>
        <div class="kpi-val-row">
          <span class="kpi-val tone-indigo">{{ summary.runningCampaigns }}</span>
          <span class="kpi-tag tag-indigo">Lineage</span>
        </div>
        <span class="kpi-sub">Tracked initiatives</span>
      </div>

      <div class="kpi-card">
        <span class="kpi-label">Model Cost Today</span>
        <div class="kpi-val-row">
          <span class="kpi-val">{{ formatCost(summary.modelCostToday) }}</span>
          <span class="kpi-tag tag-purple">Cost Guard</span>
        </div>
        <span class="kpi-sub">Fast routing via Haiku 4.5</span>
      </div>

      <div class="kpi-card highlight-card">
        <span class="kpi-label">Attributed Return</span>
        <div class="kpi-val-row">
          <span class="kpi-val tone-emerald">{{ formatCurrency(summary.attributedReturn) }}</span>
          <span class="kpi-tag tag-emerald">Revenue</span>
        </div>
        <span class="kpi-sub">Provenance-backed outcomes</span>
      </div>

      <div class="kpi-card">
        <span class="kpi-label">Decisions for Review</span>
        <div class="kpi-val-row">
          <span :class="['kpi-val', summary.decisionsNeedingReview > 0 ? 'tone-amber' : '']">
            {{ summary.decisionsNeedingReview }}
          </span>
          <span class="kpi-tag tag-amber">Policy</span>
        </div>
        <span class="kpi-sub">Awaiting human sign-off</span>
      </div>

      <div class="kpi-card">
        <span class="kpi-label">Measuring Experiments</span>
        <div class="kpi-val-row">
          <span class="kpi-val tone-sky">{{ summary.experimentsMeasuring }}</span>
          <span class="kpi-tag tag-sky">Protected</span>
        </div>
        <span class="kpi-sub">Change Guard anti-collision</span>
      </div>
    </section>

    <!-- SPATIAL 3D FLIGHT DECK WORKSPACE -->
    <section class="spatial-workspace" aria-label="3D Operational Graph">
      <div class="spatial-container">
        <!-- Top Toolbar over 3D Viewport -->
        <div class="viewport-toolbar">
          <div class="filter-pills" role="radiogroup" aria-label="Graph Layer Filter">
            <button
              type="button"
              :class="['filter-pill', { active: activeFilter === 'ALL' }]"
              @click="activeFilter = 'ALL'"
            >
              All Layers ({{ allNodes.length }})
            </button>
            <button
              type="button"
              :class="['filter-pill', { active: activeFilter === 'agent' }]"
              @click="activeFilter = 'agent'"
            >
              Agents
            </button>
            <button
              type="button"
              :class="['filter-pill', { active: activeFilter === 'campaign' }]"
              @click="activeFilter = 'campaign'"
            >
              Campaigns
            </button>
            <button
              type="button"
              :class="['filter-pill', { active: activeFilter === 'decision' }]"
              @click="activeFilter = 'decision'"
            >
              Decisions
            </button>
            <button
              type="button"
              :class="['filter-pill', { active: activeFilter === 'experiment' }]"
              @click="activeFilter = 'experiment'"
            >
              Experiments
            </button>
          </div>

          <div class="viewport-actions">
            <span class="spatial-legend-item">
              <span class="legend-dot dot-emerald" /> Positive
            </span>
            <span class="spatial-legend-item">
              <span class="legend-dot dot-rose" /> Negative
            </span>
            <span class="spatial-legend-item">
              <span class="legend-dot dot-amber" /> Uncertain
            </span>
            <span class="spatial-legend-item">
              <span class="legend-dot dot-sky" /> Running
            </span>
          </div>
        </div>

        <!-- 3D Scene Viewport -->
        <div class="scene-wrapper">
          <AgentControlGraphScene
            :nodes="filteredNodes"
            :edges="filteredEdges"
            :selected-node-id="selectedNodeId"
            @select="handleNodeSelect"
            @clear-selection="handleClearSelection"
          />
        </div>

        <!-- Node Inspector Floating Panel -->
        <aside v-if="selectedNode" class="node-inspector" aria-label="Selected Entity Inspector">
          <div class="inspector-header">
            <div class="inspector-title-row">
              <span :class="['type-badge', `type-${selectedNode.type}`]">{{ selectedNode.type.toUpperCase() }}</span>
              <span :class="['status-pill', `status-${selectedNode.status}`]">{{ selectedNode.status }}</span>
            </div>
            <button
              type="button"
              class="btn-close-inspector"
              aria-label="Close inspector"
              @click="selectedNodeId = null"
            >
              ✕
            </button>
          </div>

          <h3 class="inspector-node-title">{{ selectedNode.label }}</h3>
          <div class="inspector-meta-list">
            <div class="meta-row">
              <span class="meta-lbl">Node ID:</span>
              <span class="meta-val mono">{{ selectedNode.id }}</span>
            </div>
            <div v-for="(val, key) in selectedNode.meta" :key="key" class="meta-row">
              <span class="meta-lbl">{{ String(key).replace(/_/g, ' ') }}:</span>
              <span class="meta-val">{{ typeof val === 'object' ? JSON.stringify(val) : String(val) }}</span>
            </div>
          </div>

          <div class="inspector-actions">
            <NuxtLink
              v-if="selectedNode.type === 'campaign'"
              to="/campaigns"
              class="btn-inspector-link"
            >
              Open Campaign Details →
            </NuxtLink>
            <NuxtLink
              v-else-if="selectedNode.type === 'experiment'"
              to="/experiments"
              class="btn-inspector-link"
            >
              Open Experiments Engine →
            </NuxtLink>
            <NuxtLink
              v-else-if="selectedNode.type === 'agent'"
              to="/matches"
              class="btn-inspector-link"
            >
              View Match Demands →
            </NuxtLink>
            <NuxtLink
              v-else
              to="/discovery-gaps"
              class="btn-inspector-link"
            >
              Inspect Discovery Gaps →
            </NuxtLink>
          </div>
        </aside>
      </div>
    </section>

    <!-- OPERATIONAL DATA PANELS (Lower Grid) -->
    <section class="operations-section" aria-label="Operational Data Panels">
      <!-- Section Tab Bar -->
      <div class="ops-tab-bar" role="tablist">
        <button
          type="button"
          role="tab"
          :aria-selected="activeTab === 'agents'"
          :class="['ops-tab', { active: activeTab === 'agents' }]"
          @click="activeTab = 'agents'"
        >
          Active Agents ({{ data?.agents?.length ?? 0 }})
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="activeTab === 'campaigns'"
          :class="['ops-tab', { active: activeTab === 'campaigns' }]"
          @click="activeTab = 'campaigns'"
        >
          Campaign Economics ({{ data?.campaigns?.length ?? 0 }})
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="activeTab === 'decisions'"
          :class="['ops-tab', { active: activeTab === 'decisions' }]"
          @click="activeTab = 'decisions'"
        >
          Policy Decisions ({{ data?.decisions?.length ?? 0 }})
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="activeTab === 'experiments'"
          :class="['ops-tab', { active: activeTab === 'experiments' }]"
          @click="activeTab = 'experiments'"
        >
          Protected Experiments ({{ data?.experiments?.length ?? 0 }})
        </button>
      </div>

      <!-- TAB 1: AGENTS TABLE -->
      <div v-if="activeTab === 'agents'" class="ops-panel" role="tabpanel">
        <div class="table-container">
          <table class="ops-table">
            <thead>
              <tr>
                <th>Agent</th>
                <th>Linked Campaign</th>
                <th>Current Task</th>
                <th class="text-right">Runs</th>
                <th class="text-right">Model Cost</th>
                <th class="text-right">Outputs (Acc/Prod)</th>
                <th>Attributed Outcome</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="agent in data?.agents ?? []" :key="agent.id">
                <td>
                  <div class="agent-name-cell">
                    <span class="agent-title">{{ agent.name }}</span>
                    <span class="agent-role">{{ agent.role }}</span>
                  </div>
                </td>
                <td>
                  <NuxtLink to="/campaigns" class="table-link">
                    {{ agent.campaignName || agent.campaign_name || 'Global' }}
                  </NuxtLink>
                </td>
                <td class="task-cell">{{ agent.currentTask || agent.current_task }}</td>
                <td class="text-right mono">{{ agent.runs }}</td>
                <td class="text-right mono">{{ formatCost(agent.modelCost ?? agent.model_cost) }}</td>
                <td class="text-right mono">
                  {{ agent.outputsAccepted ?? agent.outputs_accepted ?? 0 }} / {{ agent.outputsProduced ?? agent.outputs_produced ?? 0 }}
                </td>
                <td>
                  <span :class="['outcome-badge', `outcome-${(agent.attributedOutcome || agent.attributed_outcome || 'not_measurable').toLowerCase()}`]">
                    {{ agent.attributedOutcome || agent.attributed_outcome }}
                  </span>
                </td>
                <td>
                  <span :class="['state-badge', `state-${agent.state.toLowerCase()}`]">
                    {{ agent.state }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- TAB 2: CAMPAIGN ECONOMICS TABLE -->
      <div v-if="activeTab === 'campaigns'" class="ops-panel" role="tabpanel">
        <div class="table-container">
          <table class="ops-table">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Channel</th>
                <th class="text-right">Total Cost</th>
                <th class="text-right">Attributed Return</th>
                <th class="text-right">Net Return</th>
                <th class="text-right">ROI</th>
                <th>Confidence</th>
                <th>Return Source</th>
                <th class="text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="cmp in data?.campaigns ?? []" :key="cmp.id">
                <td>
                  <div class="cmp-title-cell">
                    <span class="cmp-name">{{ cmp.name }}</span>
                    <span class="cmp-status">{{ cmp.status }}</span>
                  </div>
                </td>
                <td>{{ cmp.primaryChannel || cmp.primary_channel }}</td>
                <td class="text-right mono">{{ formatCurrency(cmp.totalCost ?? cmp.total_cost) }}</td>
                <td class="text-right mono text-emerald">{{ formatCurrency(cmp.attributedReturn ?? cmp.attributed_return) }}</td>
                <td class="text-right mono text-emerald">{{ formatCurrency(cmp.netReturn ?? cmp.net_return) }}</td>
                <td class="text-right mono text-emerald font-bold">
                  {{ (cmp.roiPct ?? cmp.roi_pct) ? `${(cmp.roiPct ?? cmp.roi_pct)?.toFixed(1)}%` : '—' }}
                </td>
                <td>
                  <span :class="['conf-chip', `conf-${(cmp.measurementConfidence || cmp.measurement_confidence || 'medium').toLowerCase()}`]">
                    {{ cmp.measurementConfidence || cmp.measurement_confidence }}
                  </span>
                </td>
                <td>
                  <span class="source-tag">
                    {{ cmp.returnSource || cmp.return_source }}
                  </span>
                </td>
                <td class="text-right">
                  <NuxtLink to="/campaigns" class="btn-table-action">
                    Inspect Ledger →
                  </NuxtLink>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- TAB 3: POLICY DECISIONS TABLE -->
      <div v-if="activeTab === 'decisions'" class="ops-panel" role="tabpanel">
        <div class="table-container">
          <table class="ops-table">
            <thead>
              <tr>
                <th>Decision Action</th>
                <th>Recommended By</th>
                <th>Campaign</th>
                <th>Action Type</th>
                <th>Policy</th>
                <th class="text-right">Cost</th>
                <th>Outcome</th>
                <th>Review Status</th>
                <th class="text-right">Human Review</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="dec in data?.decisions ?? []" :key="dec.id">
                <td>
                  <div class="dec-title-cell">
                    <span class="dec-title">{{ dec.title }}</span>
                    <span class="dec-context">{{ dec.context }}</span>
                  </div>
                </td>
                <td><span class="agent-tag">{{ dec.recommendedBy || dec.recommended_by }}</span></td>
                <td>{{ dec.campaignName || dec.campaign_name }}</td>
                <td><span class="mono-code">{{ dec.actionType || dec.action_type }}</span></td>
                <td><span class="policy-version-tag">{{ dec.policyVersion || dec.policy_version }}</span></td>
                <td class="text-right mono">{{ formatCost(dec.cost) }}</td>
                <td>
                  <span :class="['outcome-badge', `outcome-${(dec.observedOutcome || dec.observed_outcome || 'pending').toLowerCase()}`]">
                    {{ dec.observedOutcome || dec.observed_outcome }}
                  </span>
                </td>
                <td>
                  <span :class="['status-pill', `status-${(decisionOverrides[dec.id] || dec.status).toLowerCase()}`]">
                    {{ decisionOverrides[dec.id] || dec.status }}
                  </span>
                </td>
                <td class="text-right">
                  <div class="review-actions">
                    <button
                      type="button"
                      class="btn-approve"
                      :disabled="decisionOverrides[dec.id] === 'APPROVED'"
                      @click="handleDecisionAction(dec.id, 'APPROVED')"
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      class="btn-modify"
                      :disabled="decisionOverrides[dec.id] === 'MODIFIED'"
                      @click="handleDecisionAction(dec.id, 'MODIFIED')"
                    >
                      Modify
                    </button>
                    <button
                      type="button"
                      class="btn-reject"
                      :disabled="decisionOverrides[dec.id] === 'REJECTED'"
                      @click="handleDecisionAction(dec.id, 'REJECTED')"
                    >
                      Reject
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- TAB 4: PROTECTED EXPERIMENTS TABLE -->
      <div v-if="activeTab === 'experiments'" class="ops-panel" role="tabpanel">
        <div class="table-container">
          <table class="ops-table">
            <thead>
              <tr>
                <th>Code</th>
                <th>Experiment Name</th>
                <th>Hypothesis</th>
                <th>Action</th>
                <th>Primary Metric</th>
                <th>Target Key</th>
                <th>Status</th>
                <th>Change Guard</th>
                <th class="text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="exp in data?.experiments ?? []" :key="exp.id">
                <td class="mono font-bold">{{ exp.code }}</td>
                <td class="font-medium text-slate-200">{{ exp.name }}</td>
                <td class="hypothesis-cell">{{ exp.hypothesis }}</td>
                <td><span class="mono-code">{{ exp.action }}</span></td>
                <td>{{ exp.primaryMetric || exp.primary_metric }}</td>
                <td class="mono text-xs text-slate-400">{{ exp.targetKey || exp.target_key || '—' }}</td>
                <td>
                  <span :class="['status-pill', `status-${exp.status.toLowerCase()}`]">
                    {{ exp.status }}
                  </span>
                </td>
                <td>
                  <span class="protection-badge" title="Change Guard collision protection active">
                    🛡️ Protected
                  </span>
                </td>
                <td class="text-right">
                  <NuxtLink to="/experiments" class="btn-table-action">
                    Inspect →
                  </NuxtLink>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- Contextual Experiment Creation Drawer -->
    <ExperimentCreateDrawer
      v-model="isCreateDrawerOpen"
      initial-trigger="direct"
      initial-name=""
      initial-hypothesis=""
      initial-action="update_existing_page"
      initial-primary-metric="visibility"
      @created="handleExperimentCreated"
    />
  </div>
</template>

<style scoped>
.flight-deck-page {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 24px 32px 48px;
  max-width: 1600px;
  margin: 0 auto;
  width: 100%;
}

/* HEADER */
.flight-deck-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.page-title {
  font-size: 26px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: #f8fafc;
  margin: 0;
}

.mode-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  padding: 3px 10px;
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
  box-shadow: 0 0 6px currentColor;
}

.loading-indicator {
  font-size: 12px;
  color: #94a3b8;
  font-style: italic;
}

.page-subtitle {
  font-size: 13px;
  color: #94a3b8;
  margin-top: 4px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-secondary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(15, 23, 42, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #cbd5e1;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-secondary:hover:not(:disabled) {
  background: rgba(30, 41, 59, 0.9);
  color: #ffffff;
  border-color: rgba(255, 255, 255, 0.25);
}

.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: 8px;
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(99, 102, 241, 0.3);
  transition: all 0.15s ease;
}

.btn-primary:hover {
  background: linear-gradient(135deg, #4338ca 0%, #4f46e5 100%);
  transform: translateY(-1px);
  box-shadow: 0 6px 18px rgba(99, 102, 241, 0.45);
}

/* KPI STRIP */
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
}

@media (max-width: 1200px) {
  .kpi-strip {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 640px) {
  .kpi-strip {
    grid-template-columns: repeat(2, 1fr);
  }
}

.kpi-card {
  background: rgba(15, 23, 42, 0.65);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.highlight-card {
  background: rgba(16, 185, 129, 0.06);
  border-color: rgba(16, 185, 129, 0.3);
}

.kpi-label {
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.kpi-val-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}

.kpi-val {
  font-size: 22px;
  font-weight: 800;
  color: #f8fafc;
  line-height: 1.1;
  font-feature-settings: "tnum";
}

.tone-cyan { color: #38bdf8; }
.tone-indigo { color: #818cf8; }
.tone-emerald { color: #34d399; }
.tone-amber { color: #fbbf24; }
.tone-sky { color: #38bdf8; }

.kpi-tag {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}

.tag-cyan { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
.tag-indigo { background: rgba(129, 140, 248, 0.15); color: #818cf8; }
.tag-purple { background: rgba(192, 132, 252, 0.15); color: #c084fc; }
.tag-emerald { background: rgba(16, 185, 129, 0.15); color: #34d399; }
.tag-amber { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.tag-sky { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }

.kpi-sub {
  font-size: 11px;
  color: #64748b;
  margin-top: 2px;
}

/* SPATIAL 3D WORKSPACE */
.spatial-workspace {
  position: relative;
  background: #080c16;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}

.spatial-container {
  position: relative;
  width: 100%;
  height: 520px;
  min-height: 520px;
}

.viewport-toolbar {
  position: absolute;
  top: 14px;
  left: 14px;
  right: 14px;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  pointer-events: none;
}

.filter-pills {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(10, 14, 26, 0.85);
  backdrop-filter: blur(8px);
  padding: 4px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  pointer-events: auto;
}

.filter-pill {
  background: transparent;
  border: none;
  color: #94a3b8;
  font-size: 11px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.filter-pill:hover {
  color: #ffffff;
}

.filter-pill.active {
  background: rgba(99, 102, 241, 0.25);
  color: #818cf8;
  border: 1px solid rgba(99, 102, 241, 0.5);
}

.viewport-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  background: rgba(10, 14, 26, 0.85);
  backdrop-filter: blur(8px);
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  pointer-events: auto;
}

.spatial-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #94a3b8;
  font-weight: 500;
}

.legend-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.dot-emerald { background: #10b981; box-shadow: 0 0 6px #10b981; }
.dot-rose { background: #ef4444; box-shadow: 0 0 6px #ef4444; }
.dot-amber { background: #f59e0b; box-shadow: 0 0 6px #f59e0b; }
.dot-sky { background: #38bdf8; box-shadow: 0 0 6px #38bdf8; }

.scene-wrapper {
  width: 100%;
  height: 100%;
}

/* NODE INSPECTOR DRAWER (Right Overlay) */
.node-inspector {
  position: absolute;
  top: 14px;
  right: 14px;
  width: 320px;
  max-height: calc(100% - 28px);
  background: rgba(15, 23, 42, 0.94);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(99, 102, 241, 0.35);
  border-radius: 10px;
  padding: 16px;
  z-index: 20;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.6);
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}

.inspector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.inspector-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.type-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}

.type-agent { background: rgba(56, 189, 248, 0.2); color: #38bdf8; }
.type-campaign { background: rgba(99, 102, 241, 0.2); color: #818cf8; }
.type-decision { background: rgba(168, 85, 247, 0.2); color: #c084fc; }
.type-experiment { background: rgba(6, 182, 212, 0.2); color: #22d3ee; }
.type-outcome { background: rgba(16, 185, 129, 0.2); color: #34d399; }
.type-asset { background: rgba(45, 212, 191, 0.2); color: #2dd4bf; }
.type-task { background: rgba(129, 140, 248, 0.2); color: #a5b4fc; }

.status-pill {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}

.status-positive { background: rgba(16, 185, 129, 0.2); color: #34d399; }
.status-negative { background: rgba(239, 68, 68, 0.2); color: #f87171; }
.status-uncertain { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }
.status-running { background: rgba(56, 189, 248, 0.2); color: #38bdf8; }
.status-neutral { background: rgba(100, 116, 139, 0.2); color: #94a3b8; }
.status-active { background: rgba(16, 185, 129, 0.2); color: #34d399; }
.status-pending_review { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }
.status-approved { background: rgba(16, 185, 129, 0.2); color: #34d399; }
.status-rejected { background: rgba(239, 68, 68, 0.2); color: #f87171; }
.status-modified { background: rgba(168, 85, 247, 0.2); color: #c084fc; }

.btn-close-inspector {
  background: transparent;
  border: none;
  color: #64748b;
  font-size: 14px;
  cursor: pointer;
  padding: 2px 6px;
}
.btn-close-inspector:hover { color: #f8fafc; }

.inspector-node-title {
  font-size: 15px;
  font-weight: 700;
  color: #f8fafc;
  line-height: 1.3;
}

.inspector-meta-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 10px;
}

.meta-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}

.meta-lbl {
  color: #94a3b8;
  text-transform: capitalize;
}

.meta-val {
  color: #e2e8f0;
  text-align: right;
  word-break: break-all;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.inspector-actions {
  margin-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 10px;
}

.btn-inspector-link {
  display: block;
  text-align: center;
  background: rgba(99, 102, 241, 0.2);
  border: 1px solid rgba(99, 102, 241, 0.4);
  color: #c7d2fe;
  font-size: 12px;
  font-weight: 600;
  padding: 6px 12px;
  border-radius: 6px;
  text-decoration: none;
  transition: all 0.15s ease;
}

.btn-inspector-link:hover {
  background: rgba(99, 102, 241, 0.35);
  color: #ffffff;
}

/* OPERATIONS PANELS */
.operations-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ops-tab-bar {
  display: flex;
  gap: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  padding-bottom: 6px;
}

.ops-tab {
  background: transparent;
  border: none;
  color: #94a3b8;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.ops-tab:hover {
  color: #f1f5f9;
}

.ops-tab.active {
  background: rgba(99, 102, 241, 0.15);
  color: #818cf8;
  border: 1px solid rgba(99, 102, 241, 0.3);
}

.ops-panel {
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  overflow: hidden;
}

.table-container {
  overflow-x: auto;
}

.ops-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  text-align: left;
}

.ops-table th {
  background: rgba(10, 14, 26, 0.6);
  color: #94a3b8;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.ops-table td {
  padding: 12px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  color: #cbd5e1;
  vertical-align: middle;
}

.ops-table tr:hover td {
  background: rgba(255, 255, 255, 0.02);
}

.text-right { text-align: right; }
.text-emerald { color: #34d399; }
.font-bold { font-weight: 700; }
.font-medium { font-weight: 500; }

.agent-name-cell {
  display: flex;
  flex-direction: column;
}

.agent-title {
  font-weight: 600;
  color: #f8fafc;
}

.agent-role {
  font-size: 11px;
  color: #94a3b8;
}

.table-link {
  color: #818cf8;
  text-decoration: none;
  font-weight: 500;
}
.table-link:hover { text-decoration: underline; }

.task-cell {
  max-width: 280px;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.4;
}

.outcome-badge {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}

.outcome-positive { background: rgba(16, 185, 129, 0.15); color: #34d399; }
.outcome-negative { background: rgba(239, 68, 68, 0.15); color: #f87171; }
.outcome-uncertain { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.outcome-not_measurable { background: rgba(100, 116, 139, 0.15); color: #94a3b8; }
.outcome-pending { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }

.state-badge {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}

.state-running { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
.state-waiting { background: rgba(100, 116, 139, 0.15); color: #94a3b8; }
.state-review { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.state-blocked { background: rgba(239, 68, 68, 0.15); color: #f87171; }
.state-completed { background: rgba(16, 185, 129, 0.15); color: #34d399; }
.state-failed { background: rgba(239, 68, 68, 0.15); color: #f87171; }

.cmp-title-cell {
  display: flex;
  flex-direction: column;
}

.cmp-name {
  font-weight: 600;
  color: #f8fafc;
}

.cmp-status {
  font-size: 10px;
  color: #94a3b8;
}

.conf-chip {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}

.conf-high { background: rgba(16, 185, 129, 0.15); color: #34d399; }
.conf-medium { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
.conf-low { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }

.source-tag {
  font-size: 11px;
  color: #c7d2fe;
  background: rgba(99, 102, 241, 0.12);
  padding: 2px 6px;
  border-radius: 4px;
}

.btn-table-action {
  font-size: 11px;
  font-weight: 600;
  color: #818cf8;
  text-decoration: none;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid rgba(99, 102, 241, 0.3);
  transition: all 0.15s ease;
}

.btn-table-action:hover {
  background: rgba(99, 102, 241, 0.2);
  color: #ffffff;
}

.dec-title-cell {
  display: flex;
  flex-direction: column;
  max-width: 260px;
}

.dec-title {
  font-weight: 600;
  color: #f8fafc;
}

.dec-context {
  font-size: 11px;
  color: #94a3b8;
}

.agent-tag {
  font-size: 11px;
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.1);
  padding: 2px 6px;
  border-radius: 4px;
}

.mono-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  color: #cbd5e1;
  background: rgba(255, 255, 255, 0.05);
  padding: 2px 4px;
  border-radius: 4px;
}

.policy-version-tag {
  font-size: 11px;
  color: #a855f7;
  font-weight: 600;
}

.review-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}

.btn-approve {
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.4);
  color: #34d399;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-approve:hover:not(:disabled) {
  background: rgba(16, 185, 129, 0.3);
}

.btn-modify {
  background: rgba(168, 85, 247, 0.15);
  border: 1px solid rgba(168, 85, 247, 0.4);
  color: #c084fc;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-modify:hover:not(:disabled) {
  background: rgba(168, 85, 247, 0.3);
}

.btn-reject {
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.4);
  color: #f87171;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-reject:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.3);
}

.hypothesis-cell {
  max-width: 260px;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.4;
}

.protection-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 600;
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.12);
  border: 1px solid rgba(56, 189, 248, 0.3);
  padding: 2px 6px;
  border-radius: 4px;
}
</style>
