<script setup lang="ts">
import type { Campaign, CostLineageNode, CostCompositionItem, PersonCost, AgentCost, VideoCost, AssetLineageItem } from '~/types/campaign'
import KnowledgeGraphExplorer from '~/components/graph/KnowledgeGraphExplorer.vue'
import CampaignGraphScene from '~/components/visualization/CampaignGraphScene.vue'
import CampaignGraphTree from '~/components/visualization/CampaignGraphTree.vue'
import ExperimentCreateDrawer from '~/components/experiments/ExperimentCreateDrawer.vue'

const {
  campaigns,
  selectedCampaignId,
  selectedCampaign,
  graphData,
  highlightedPathNodeIds,
  highlightedPathEdgeIds,
  selectCampaign,
  highlightOutcomePath,
  highlightCostPath,
  clearHighlights,
  refreshProfoundLive,
  profoundRefreshing
} = useCampaigns()

const isExperimentDrawerOpen = ref(false)

// View mode for graph visualization: 3D Spatial (Three.js), Accessible DOM Tree, or Chronological Timeline
const vizMode = ref<'3d' | 'tree' | 'timeline'>('3d')

// Cost lineage expanded tree state
const lineageExpanded = ref(true)
const selectedCostItem = ref<CostLineageNode | null>(null)
const costDetailDrawerOpen = ref(false)

function inspectCostNode(node: CostLineageNode) {
  selectedCostItem.value = node
  costDetailDrawerOpen.value = true
}

function closeCostDetail() {
  costDetailDrawerOpen.value = false
  selectedCostItem.value = null
}
</script>

<template>
  <div class="split-workspace">
    <!-- LEFT SIDEBAR: Campaign Queue -->
    <aside class="queue-pane" aria-label="Campaign Financial Queue">
      <div class="pane-header">
        <div class="header-row">
          <span class="pane-title">Campaigns</span>
          <span class="count-badge">{{ campaigns.length }} Tracked</span>
          <button
            type="button"
            class="profound-refresh-btn"
            :disabled="profoundRefreshing"
            title="Pull latest Profound signals and refresh campaign effectiveness"
            @click="refreshProfoundLive"
          >
            {{ profoundRefreshing ? 'Syncing…' : 'Profound sync' }}
          </button>
        </div>
        <p class="pane-subtitle">Provenance-backed cost & return ledger across every marketing initiative</p>
      </div>

      <div class="campaign-list" role="list">
        <button
          v-for="cmp in campaigns"
          :key="cmp.id"
          role="listitem"
          :class="['campaign-card', { active: selectedCampaignId === cmp.id }]"
          @click="selectCampaign(cmp.id)"
        >
          <div class="card-meta">
            <span :class="['status-pill', `status-${cmp.status.toLowerCase()}`]">{{ cmp.status }}</span>
            <span :class="['conf-badge', `conf-${cmp.measurement_confidence.toLowerCase()}`]">
              {{ cmp.measurement_confidence }} CONFIDENCE
            </span>
          </div>

          <h3 class="campaign-name">{{ cmp.name }}</h3>
          <div class="channel-hint">{{ cmp.primary_channel }}</div>

          <div v-if="cmp.campaign_effectiveness || cmp.profound_live" class="profound-effect-row">
            <span
              v-if="cmp.profound_live?.source_mode === 'LIVE'"
              :class="['effect-pill', `effect-${(cmp.campaign_effectiveness || cmp.profound_live?.campaign_effectiveness || 'NO_DATA').toLowerCase()}`]"
            >
              Profound · {{ cmp.campaign_effectiveness || cmp.profound_live?.campaign_effectiveness }}
            </span>
          </div>

          <div class="card-financials">
            <div class="fin-item">
              <span class="fin-label">Cost</span>
              <span class="fin-val">${{ (cmp.total_cost / 1000).toFixed(1) }}K</span>
            </div>
            <div class="fin-sep">→</div>
            <div class="fin-item">
              <span class="fin-label">Return</span>
              <span class="fin-val text-good">${{ (cmp.attributed_return / 1000).toFixed(1) }}K</span>
            </div>
            <div class="fin-sep">·</div>
            <div class="fin-item">
              <span class="fin-label">ROI</span>
              <span class="fin-val text-good">{{ cmp.roi ? `${cmp.roi}x` : '—' }}</span>
            </div>
          </div>
        </button>
      </div>
    </aside>

    <!-- MAIN CENTER WORKSPACE: Financial Control Center -->
    <main class="workspace-pane floating-safe">
      <template v-if="selectedCampaign">
        <!-- Compact Detail Header -->
        <header class="workspace-header">
          <div class="header-info">
            <div class="meta-line">
              <span class="owner-tag">Owner: {{ selectedCampaign.owner }}</span>
              <span class="divider">·</span>
              <span class="date-tag">{{ selectedCampaign.date_range }}</span>
              <span class="divider">·</span>
              <span class="channel-tag">{{ selectedCampaign.channels.join(', ') }}</span>
            </div>
            <h2 class="campaign-title">{{ selectedCampaign.name }}</h2>
          </div>

          <div class="header-badges">
            <button
              type="button"
              class="btn-campaign-experiment"
              @click="isExperimentDrawerOpen = true"
            >
              🧪 Test in Experiments Engine →
            </button>
            <span :class="['status-pill-lg', `status-${selectedCampaign.status.toLowerCase()}`]">
              {{ selectedCampaign.status }}
            </span>
            <span class="budget-tag">
              Budget: ${{ Number(selectedCampaign.budget).toLocaleString() }} {{ selectedCampaign.currency }}
            </span>
          </div>
        </header>

        <!-- TOP FINANCIAL STRIP (Max 5 High-Value Executive Metrics) -->
        <section class="financial-strip" aria-label="Top Financial Strip">
          <div class="metric-card" @click="highlightCostPath()">
            <span class="metric-label">Total Cost (Accounting)</span>
            <span class="metric-val">${{ Number(selectedCampaign.total_cost).toLocaleString() }}</span>
            <span class="metric-sub">Across 7 cost dimensions</span>
          </div>

          <div class="metric-card good-glow" @click="highlightOutcomePath()">
            <span class="metric-label">Attributed Return</span>
            <span class="metric-val text-good">${{ Number(selectedCampaign.attributed_return).toLocaleString() }}</span>
            <span class="metric-sub">Direct & modeled downstream</span>
          </div>

          <div class="metric-card" @click="highlightOutcomePath()">
            <span class="metric-label">Campaign ROI</span>
            <span class="metric-val text-good">{{ selectedCampaign.roi ? `${selectedCampaign.roi}x` : 'Not measurable yet' }}</span>
            <span class="metric-sub">Net: +${{ Number(selectedCampaign.operational_metrics.net_return).toLocaleString() }}</span>
          </div>

          <div class="metric-card">
            <span class="metric-label">Cost Completeness</span>
            <span class="metric-val tone-blue">{{ selectedCampaign.cost_completeness_pct }}%</span>
            <span class="metric-sub">Known invoices & hours</span>
          </div>

          <div class="metric-card">
            <span class="metric-label">Outcome Coverage</span>
            <span class="metric-val tone-purple">{{ selectedCampaign.outcome_coverage_pct }}%</span>
            <span class="metric-sub">Profound & Muse telemetry</span>
          </div>
        </section>

        <!-- OPERATIONAL UNIT METRICS STRIP -->
        <section class="operational-strip" aria-label="Operational Unit Metrics">
          <div class="op-item">
            <span class="op-label">Cost / Output:</span>
            <span class="op-val">${{ Number(selectedCampaign.operational_metrics.cost_per_output.toFixed(0)).toLocaleString() }}</span>
          </div>
          <div class="op-sep">·</div>
          <div class="op-item">
            <span class="op-label">Cost / Agent Run:</span>
            <span class="op-val">${{ selectedCampaign.operational_metrics.cost_per_agent_run.toFixed(2) }}</span>
          </div>
          <div class="op-sep">·</div>
          <div class="op-item">
            <span class="op-label">Cost / Lead:</span>
            <span class="op-val">${{ Number(selectedCampaign.operational_metrics.cost_per_lead.toFixed(0)).toLocaleString() }}</span>
          </div>
          <div class="op-sep">·</div>
          <div class="op-item">
            <span class="op-label">Cost / AI Visibility Point:</span>
            <span class="op-val text-good">${{ Number(selectedCampaign.operational_metrics.cost_per_ai_visibility_point?.toFixed(0) || 0).toLocaleString() }}/pp</span>
          </div>
          <div class="op-sep">·</div>
          <div class="op-item">
            <span class="op-label">Agent ROI:</span>
            <span class="op-val text-good">{{ selectedCampaign.operational_metrics.agent_roi.ratio }}x</span>
            <span class="op-hint">({{ selectedCampaign.operational_metrics.agent_roi.attribution_label.slice(0, 10) }}…)</span>
          </div>
        </section>

        <!-- SIGNATURE 3-COLUMN CORE: COST | OUTPUT | OUTCOME -->
        <section class="three-column-core" aria-label="Core Financial Columns">
          <!-- COLUMN 1: COST (Financial Accounting) -->
          <div class="core-column cost-column">
            <div class="column-header">
              <span class="col-pill col-pill-cost">COST</span>
              <h3 class="column-title">Financial Accounting</h3>
              <span class="column-amount">${{ Number(selectedCampaign.total_cost).toLocaleString() }}</span>
            </div>

            <!-- Horizontal Composition Bar -->
            <div class="composition-bar" aria-label="Cost Composition Breakdown">
              <div
                v-for="item in selectedCampaign.cost_composition"
                :key="item.category"
                class="bar-segment"
                :style="{ width: `${item.pct}%` }"
                :title="`${item.category}: $${item.amount.toLocaleString()} (${item.pct}%) · ${item.source}`"
              />
            </div>

            <!-- Cost Lineage Tree -->
            <div class="col-box">
              <div class="box-head" @click="lineageExpanded = !lineageExpanded">
                <span>{{ lineageExpanded ? '▼' : '▶' }} Cost Lineage Breakdown</span>
                <span class="text-warn font-mono">${{ Number(selectedCampaign.cost_lineage.amount).toLocaleString() }}</span>
              </div>
              <div v-if="lineageExpanded" class="col-lineage-list">
                <div
                  v-for="child in selectedCampaign.cost_lineage.children || []"
                  :key="child.id"
                  class="col-lineage-item"
                  @click="inspectCostNode(child)"
                >
                  <div class="lineage-row">
                    <span class="c-name">{{ child.name }}</span>
                    <span class="c-amt">${{ Number(child.amount).toLocaleString() }}</span>
                  </div>
                  <div v-if="child.children?.length" class="col-sub-items">
                    <div
                      v-for="sub in child.children"
                      :key="sub.id"
                      class="sub-item-row"
                      @click.stop="inspectCostNode(sub)"
                    >
                      <span>{{ sub.name }}</span>
                      <span class="font-mono">${{ Number(sub.amount).toLocaleString() }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Cost Contributors Summary -->
            <div class="col-box">
              <span class="box-title">Contributor Hours & Rates</span>
              <div class="mini-list">
                <div v-for="p in selectedCampaign.people" :key="p.id" class="mini-item">
                  <div class="mini-left">
                    <span class="p-name">{{ p.name }}</span>
                    <span class="p-role">{{ p.role }} ({{ p.hours }}h @ ${{ p.hourly_cost }}/h)</span>
                  </div>
                  <span class="p-total">${{ Number(p.total_cost).toLocaleString() }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- COLUMN 2: OUTPUT (Work & Assets) -->
          <div class="core-column output-column">
            <div class="column-header">
              <span class="col-pill col-pill-output">OUTPUT</span>
              <h3 class="column-title">Work & Production</h3>
              <span class="column-amount">{{ selectedCampaign.assets.length + selectedCampaign.videos.length }} Assets</span>
            </div>

            <!-- Assets & Videos -->
            <div class="col-box">
              <span class="box-title">Campaign Production Assets</span>
              <div class="assets-stack">
                <div v-for="ast in selectedCampaign.assets" :key="ast.id" class="asset-mini-card">
                  <div class="ast-meta-top">
                    <span class="ast-type-tag">{{ ast.type }}</span>
                    <span class="ast-by">By {{ ast.created_by }}</span>
                  </div>
                  <h4 class="ast-card-title">{{ ast.title }}</h4>
                  <div v-if="ast.generated_by_agent" class="ast-agent-note">
                    Agent Draft: <em>{{ ast.generated_by_agent }}</em>
                  </div>
                  <div class="ast-channels">Channels: {{ ast.distributed_on.join(', ') }}</div>
                </div>

                <div v-for="v in selectedCampaign.videos" :key="v.id" class="video-mini-card">
                  <div class="ast-meta-top">
                    <span class="ast-type-tag video-tag">Video ({{ v.versions_count }} revisions)</span>
                    <span class="text-good font-mono">${{ Number(v.total_cost).toLocaleString() }}</span>
                  </div>
                  <h4 class="ast-card-title">{{ v.title }}</h4>
                  <div class="video-sub-costs">
                    <span>Creator: ${{ v.creator_cost }}</span>
                    <span>Editing: ${{ v.editing_cost }}</span>
                    <span>AI Gen: ${{ v.ai_generation_cost }}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Agent Runs Efficiency -->
            <div class="col-box">
              <span class="box-title">Autonomous Agent Runs</span>
              <div class="mini-list">
                <div v-for="ag in selectedCampaign.agents" :key="ag.id" class="mini-item">
                  <div class="mini-left">
                    <span class="p-name">{{ ag.name }}</span>
                    <span class="p-role">{{ ag.runs }} runs ({{ ag.approved_outputs }} approved) · {{ (ag.tokens / 1000000).toFixed(1) }}M tok</span>
                  </div>
                  <span class="text-blue font-mono">${{ ag.total_cost }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- COLUMN 3: OUTCOME (Attribution & Returns) -->
          <div class="core-column outcome-column">
            <div class="column-header">
              <span class="col-pill col-pill-outcome">OUTCOME</span>
              <h3 class="column-title">Attributed Return</h3>
              <span class="column-amount text-good">${{ Number(selectedCampaign.attributed_return).toLocaleString() }}</span>
            </div>

            <!-- Return Sources Breakdown -->
            <div class="col-box">
              <span class="box-title">Return Sources (Quality Labeled)</span>
              <div class="return-sources-mini">
                <div class="ret-item">
                  <div class="ret-top">
                    <span class="source-tag-direct">DIRECT (Signed Deals)</span>
                    <span class="ret-amt text-good">${{ Number(selectedCampaign.return_sources.direct).toLocaleString() }}</span>
                  </div>
                  <span class="ret-desc">Enterprise contract commitments directly linked</span>
                </div>
                <div class="ret-item">
                  <div class="ret-top">
                    <span class="source-tag-attributed">ATTRIBUTED (Pipeline)</span>
                    <span class="ret-amt text-blue">${{ Number(selectedCampaign.return_sources.attributed).toLocaleString() }}</span>
                  </div>
                  <span class="ret-desc">Opportunities with verified campaign touchpoints</span>
                </div>
                <div class="ret-item">
                  <div class="ret-top">
                    <span class="source-tag-modeled">MODELED (LTV)</span>
                    <span class="ret-amt text-warn">${{ Number(selectedCampaign.return_sources.modeled).toLocaleString() }}</span>
                  </div>
                  <span class="ret-desc">Projected expansion and renewal value</span>
                </div>
              </div>
            </div>

            <!-- Profound AI Discovery Impact -->
            <div class="col-box">
              <div class="box-title-row">
                <span class="box-title">Profound AI Discovery Impact</span>
                <span
                  v-if="selectedCampaign.profound_impact?.data_source === 'LIVE_PROFOUND'"
                  class="live-profound-tag"
                >LIVE · 7d delta</span>
              </div>
              <div class="profound-mini-stats">
                <div class="p-stat">
                  <span class="p-stat-label">Visibility Δ7d</span>
                  <span
                    :class="['p-stat-val', (selectedCampaign.profound_impact.visibility_shift_pp ?? 0) >= 0 ? 'text-good' : 'text-bad']"
                  >
                    {{ (selectedCampaign.profound_impact.visibility_shift_pp ?? 0) >= 0 ? '+' : '' }}{{ selectedCampaign.profound_impact.visibility_shift_pp ?? '—' }}pp
                  </span>
                </div>
                <div class="p-stat">
                  <span class="p-stat-label">Citation Δ7d</span>
                  <span
                    :class="['p-stat-val', (selectedCampaign.profound_impact.citation_share_shift_pp ?? 0) >= 0 ? 'text-good' : 'text-bad']"
                  >
                    {{ (selectedCampaign.profound_impact.citation_share_shift_pp ?? 0) >= 0 ? '+' : '' }}{{ selectedCampaign.profound_impact.citation_share_shift_pp ?? '—' }}pp
                  </span>
                </div>
                <div class="p-stat">
                  <span class="p-stat-label">AI perception</span>
                  <span class="p-stat-val tone-blue">{{ selectedCampaign.profound_impact.ai_perception_status }}</span>
                </div>
              </div>
              <p class="profound-attribution-note">{{ selectedCampaign.profound_impact.attribution_note }}</p>
            </div>

            <!-- Muse Agent-Mediated Funnel -->
            <div class="col-box">
              <span class="box-title">Muse Agent-Mediated Funnel</span>
              <div class="muse-funnel-mini">
                <div class="m-step">
                  <span class="m-val">{{ selectedCampaign.muse_outcomes.matched_intents }}</span>
                  <span class="m-lbl">Demand</span>
                </div>
                <span class="m-arrow">→</span>
                <div class="m-step">
                  <span class="m-val text-blue">{{ selectedCampaign.muse_outcomes.shortlisted }}</span>
                  <span class="m-lbl">Shortlisted</span>
                </div>
                <span class="m-arrow">→</span>
                <div class="m-step">
                  <span class="m-val text-good">{{ selectedCampaign.muse_outcomes.converted }}</span>
                  <span class="m-lbl">Converted</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- WASTE MAP: Spend That Never Reached the Market -->
        <section class="waste-map-section" aria-label="Waste & Inefficiency Map">
          <div class="waste-headline-card">
            <div class="waste-lead-row">
              <div class="headline-block">
                <span class="waste-badge">Waste Map & Inefficiency Ledger</span>
                <h3 class="waste-headline">
                  “${{ Number(selectedCampaign.waste_breakdown.potential_inefficiency).toLocaleString() }} of this campaign never reached the market.”
                </h3>
              </div>
              <div class="health-scores-block">
                <div class="h-dim">
                  <span class="h-lbl">Cost Completeness</span>
                  <span class="h-val">{{ selectedCampaign.operational_metrics.health_dimensions.cost_completeness }}%</span>
                </div>
                <div class="h-dim">
                  <span class="h-lbl">Outcome Coverage</span>
                  <span class="h-val">{{ selectedCampaign.operational_metrics.health_dimensions.outcome_coverage }}%</span>
                </div>
                <div class="h-dim">
                  <span class="h-lbl">Attribution Quality</span>
                  <span class="h-val">{{ selectedCampaign.operational_metrics.health_dimensions.attribution_quality }}</span>
                </div>
              </div>
            </div>

            <div class="waste-items-grid">
              <div
                v-for="(w, i) in selectedCampaign.waste_breakdown.items"
                :key="i"
                class="waste-chip-card"
              >
                <div class="w-top">
                  <span :class="['w-cat', `cat-${w.category.toLowerCase()}`]">{{ w.category }}</span>
                  <span class="w-amt">${{ Number(w.amount).toLocaleString() }}</span>
                </div>
                <span class="w-label">{{ w.label }}</span>
              </div>
            </div>
          </div>
        </section>

        <!-- 3D CAMPAIGN GRAPH & TIMELINE SECTION -->
        <section class="graph-section">
          <div class="graph-toolbar">
            <div class="toolbar-title-block">
              <h3 class="section-title">3D Campaign Lineage Graph</h3>
              <span class="section-hint">z = -10 (Costs) → z = -5 (People/Agents) → z = 0 (Campaign) → z = +5 (Assets) → z = +10 (Signals) → z = +15 (Outcomes)</span>
            </div>

            <div class="toolbar-actions">
              <div v-if="highlightedPathNodeIds.length > 0" class="path-active-pill">
                <span>Path Highlighted</span>
                <button type="button" class="btn-clear-path" @click="clearHighlights">✕ Clear</button>
              </div>

              <div class="view-mode-toggle">
                <button
                  type="button"
                  :class="['mode-btn', { active: vizMode === '3d' }]"
                  @click="vizMode = '3d'"
                >
                  Knowledge Graph (2D/3D)
                </button>
                <button
                  type="button"
                  :class="['mode-btn', { active: vizMode === 'timeline' }]"
                  @click="vizMode = 'timeline'"
                >
                  Event Timeline
                </button>
              </div>
            </div>
          </div>

          <div class="graph-viewport">
            <KnowledgeGraphExplorer
              v-if="vizMode === '3d'"
              :initial-perspective="'campaign'"
              :initial-focus-id="selectedCampaignId"
              title="CampaignGraph Financial Lineage"
            />
            <!-- Chronological Timeline Mode -->
            <div v-else class="timeline-container">
              <div class="timeline-header">
                <span class="timeline-title">Chronological Campaign Milestones</span>
                <span class="timeline-count">{{ selectedCampaign.timeline.length }} verified events</span>
              </div>
              <div class="timeline-list">
                <div
                  v-for="(ev, idx) in selectedCampaign.timeline"
                  :key="idx"
                  class="timeline-item"
                >
                  <span class="timeline-time">{{ ev.time }}</span>
                  <span class="timeline-dot" />
                  <span class="timeline-event">{{ ev.event }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>
      </template>

      <div v-else class="empty-state">
        <p>No campaigns tracked yet. Ingest or create a campaign to start cost and outcome lineage.</p>
      </div>
    </main>

    <!-- COST ITEM DETAIL DRAWER -->
    <div v-if="costDetailDrawerOpen && selectedCostItem" class="drawer-backdrop" @click="closeCostDetail">
      <div class="cost-drawer" role="dialog" aria-label="Cost Lineage Detail" @click.stop>
        <div class="drawer-header">
          <span class="drawer-badge">Cost Lineage Node</span>
          <button class="drawer-close" type="button" @click="closeCostDetail">✕</button>
        </div>

        <h3 class="drawer-title">{{ selectedCostItem.name }}</h3>
        <div class="drawer-amount">${{ Number(selectedCostItem.amount).toLocaleString() }}</div>

        <div class="drawer-body">
          <div v-if="selectedCostItem.details" class="detail-box">
            <span class="detail-label">Provenance Details:</span>
            <p class="detail-text">{{ selectedCostItem.details }}</p>
          </div>
          <div class="detail-box">
            <span class="detail-label">Campaign Association:</span>
            <p class="detail-text">{{ selectedCampaign?.name }}</p>
          </div>
          <div class="detail-box">
            <span class="detail-label">Lineage Action:</span>
            <button type="button" class="btn-highlight-this" @click="highlightCostPath(selectedCostItem.name); closeCostDetail()">
              Highlight in 3D Lineage Graph →
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Contextual Experiment Creation Drawer -->
    <ExperimentCreateDrawer
      v-model="isExperimentDrawerOpen"
      initial-trigger="campaign"
      :initial-campaign-id="selectedCampaign?.id"
      :initial-name="selectedCampaign ? `Test Campaign Optimization: ${selectedCampaign.name}` : ''"
      :initial-hypothesis="selectedCampaign ? `Targeted content and attribution remediation for ${selectedCampaign.name} will improve search visibility.` : ''"
      :initial-target-url="selectedCampaign?.landing_url || 'https://profound.academy/pricing'"
      initial-action="update_existing_page"
      initial-primary-metric="visibility"
    />
  </div>
</template>

<style scoped>
.btn-campaign-experiment {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(99, 102, 241, 0.15);
  border: 1px solid rgba(99, 102, 241, 0.4);
  color: #c7d2fe;
  font-size: 11px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-campaign-experiment:hover {
  background: rgba(99, 102, 241, 0.25);
  border-color: #818cf8;
  color: #ffffff;
  transform: translateY(-1px);
}
.pane-header {
  padding: 16px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pane-title {
  font-size: 14px;
  font-weight: 700;
  color: #f1f5f9;
}
.count-badge {
  font-size: 11px;
  font-weight: 600;
  color: #6366f1;
  background: rgba(99, 102, 241, 0.15);
  padding: 2px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.pane-subtitle {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 4px;
  line-height: 1.4;
}
.campaign-list {
  display: flex;
  flex-direction: column;
  padding: 12px;
  gap: 10px;
}
.campaign-card {
  text-align: left;
  background: rgba(15, 21, 38, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 8px;
  padding: 14px;
  cursor: pointer;
  transition: all 0.2s ease;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.campaign-card:hover {
  background: rgba(23, 32, 56, 0.85);
  border-color: rgba(99, 102, 241, 0.3);
}
.campaign-card.active {
  background: rgba(30, 42, 74, 0.9);
  border-color: #6366f1;
  box-shadow: 0 0 16px rgba(99, 102, 241, 0.15);
}
.card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.status-pill {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}
.status-active {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
}
.status-completed {
  background: rgba(56, 189, 248, 0.2);
  color: #38bdf8;
}
.conf-badge {
  font-size: 9px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  padding: 2px 6px;
  border-radius: 4px;
}
.conf-medium {
  background: rgba(245, 158, 11, 0.2);
  color: #fde68a;
}
.conf-high {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
}
.campaign-name {
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
}
.channel-hint {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
}
.card-financials {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
  font-size: 11px;
}
.fin-item {
  display: flex;
  flex-direction: column;
}
.fin-label {
  font-size: 9px;
  color: #64748b;
  text-transform: uppercase;
}
.fin-val {
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #e2e8f0;
}
.fin-sep {
  color: #475569;
}

/* WORKSPACE */
.campaign-workspace-pane {
  display: flex;
  flex-direction: column;
  padding: 24px;
  gap: 20px;
  overflow-y: auto;
}
.workspace-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  background: rgba(15, 21, 38, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 10px;
  padding: 18px 20px;
}
.meta-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: #94a3b8;
  font-family: var(--font-mono, monospace);
}
.divider {
  color: #475569;
}
.campaign-title {
  font-size: 20px;
  font-weight: 700;
  color: #ffffff;
  margin-top: 4px;
}
.header-badges {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}
.status-pill-lg {
  font-size: 11px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 4px;
}
.budget-tag {
  font-size: 11px;
  color: #94a3b8;
  font-family: var(--font-mono, monospace);
}

/* TOP FINANCIAL STRIP */
.financial-strip {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}
@media (max-width: 1200px) {
  .financial-strip {
    grid-template-columns: repeat(3, 1fr);
  }
}
.metric-card {
  background: rgba(15, 21, 38, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.metric-card:hover {
  background: rgba(23, 32, 56, 0.95);
  border-color: rgba(99, 102, 241, 0.4);
}
.metric-card.good-glow {
  border-color: rgba(16, 185, 129, 0.3);
}
.metric-label {
  font-size: 11px;
  color: #94a3b8;
  font-weight: 600;
}
.metric-val {
  font-size: 22px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #ffffff;
}
.metric-sub {
  font-size: 10px;
  color: #64748b;
}

/* OPERATIONAL STRIP */
.operational-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  background: rgba(15, 21, 38, 0.5);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 6px;
  padding: 10px 16px;
  font-size: 12px;
}
.op-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.op-label {
  color: #94a3b8;
}
.op-val {
  font-family: var(--font-mono, monospace);
  font-weight: 700;
  color: #f1f5f9;
}
.op-hint {
  font-size: 10px;
  color: #64748b;
}
.op-sep {
  color: #475569;
}

/* 3-COLUMN CORE */
.three-column-core {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 16px;
}
@media (max-width: 1200px) {
  .three-column-core {
    grid-template-columns: 1fr;
  }
}
.core-column {
  background: rgba(15, 21, 38, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 10px;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.column-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  padding-bottom: 10px;
}
.col-pill {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.05em;
  padding: 2px 8px;
  border-radius: 4px;
}
.col-pill-cost {
  background: rgba(245, 158, 11, 0.2);
  color: #fde68a;
}
.col-pill-output {
  background: rgba(34, 211, 238, 0.2);
  color: #22d3ee;
}
.col-pill-outcome {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
}
.column-title {
  font-size: 13px;
  font-weight: 600;
  color: #f1f5f9;
  flex: 1;
  margin-left: 8px;
}
.column-amount {
  font-size: 14px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #ffffff;
}

.composition-bar {
  display: flex;
  height: 8px;
  border-radius: 999px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.06);
}
.bar-segment:nth-child(1) { background: #38bdf8; }
.bar-segment:nth-child(2) { background: #6366f1; }
.bar-segment:nth-child(3) { background: #a855f7; }
.bar-segment:nth-child(4) { background: #ec4899; }
.bar-segment:nth-child(5) { background: #10b981; }
.bar-segment:nth-child(6) { background: #f59e0b; }
.bar-segment:nth-child(7) { background: #64748b; }

.col-box {
  background: rgba(10, 15, 29, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 6px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.box-head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 600;
  color: #e2e8f0;
  cursor: pointer;
}
.box-title {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
}

.col-lineage-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 4px;
}
.col-lineage-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
  cursor: pointer;
}
.lineage-row {
  display: flex;
  justify-content: space-between;
  color: #cbd5e1;
}
.col-sub-items {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-left: 12px;
  font-size: 10px;
  color: #94a3b8;
}
.sub-item-row {
  display: flex;
  justify-content: space-between;
  padding: 1px 4px;
  border-radius: 2px;
}
.sub-item-row:hover {
  background: rgba(255, 255, 255, 0.05);
  color: #ffffff;
}

.mini-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.mini-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
}
.mini-left {
  display: flex;
  flex-direction: column;
}
.p-name {
  font-weight: 600;
  color: #e2e8f0;
}
.p-role {
  font-size: 10px;
  color: #64748b;
}
.p-total {
  font-family: var(--font-mono, monospace);
  color: #38bdf8;
}

/* OUTPUT COLUMN */
.assets-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.asset-mini-card, .video-mini-card {
  background: rgba(18, 26, 47, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 6px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ast-meta-top {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
}
.ast-type-tag {
  color: #22d3ee;
  font-weight: 700;
}
.video-tag {
  color: #a855f7;
}
.ast-by {
  color: #64748b;
}
.ast-card-title {
  font-size: 12px;
  font-weight: 600;
  color: #ffffff;
}
.ast-agent-note {
  font-size: 10px;
  color: #818cf8;
}
.ast-channels, .video-sub-costs {
  font-size: 10px;
  color: #94a3b8;
  display: flex;
  gap: 8px;
}

/* OUTCOME COLUMN */
.return-sources-mini {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ret-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ret-top {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
}
.source-tag-direct {
  font-weight: 700;
  color: #34d399;
}
.source-tag-attributed {
  font-weight: 700;
  color: #38bdf8;
}
.source-tag-modeled {
  font-weight: 700;
  color: #fde68a;
}
.ret-amt {
  font-family: var(--font-mono, monospace);
  font-weight: 700;
}
.ret-desc {
  font-size: 10px;
  color: #64748b;
}
.profound-refresh-btn {
  margin-left: auto;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid rgba(52, 211, 153, 0.35);
  background: rgba(16, 185, 129, 0.08);
  color: #34d399;
  cursor: pointer;
}
.profound-refresh-btn:disabled {
  opacity: 0.6;
  cursor: wait;
}

.profound-effect-row {
  margin: 6px 0 4px;
}
.effect-pill {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 2px 8px;
  border-radius: 4px;
}
.effect-working { background: rgba(16, 185, 129, 0.15); color: #34d399; }
.effect-stable { background: rgba(100, 116, 139, 0.15); color: #94a3b8; }
.effect-watch { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.effect-at_risk { background: rgba(239, 68, 68, 0.15); color: #f87171; }
.effect-no_data { background: rgba(99, 102, 241, 0.12); color: #818cf8; }
.box-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.live-profound-tag {
  font-size: 10px;
  font-weight: 700;
  color: #34d399;
  letter-spacing: 0.05em;
}
.text-bad { color: #f87171; }

.profound-mini-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  text-align: center;
}
.p-stat {
  background: rgba(18, 26, 47, 0.6);
  padding: 6px;
  border-radius: 4px;
  display: flex;
  flex-direction: column;
}
.p-stat-label {
  font-size: 9px;
  color: #64748b;
}
.p-stat-val {
  font-size: 14px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
}
.profound-attribution-note {
  font-size: 10px;
  color: #a855f7;
  margin-top: 4px;
}
.muse-funnel-mini {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.m-step {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.m-val {
  font-size: 14px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #ffffff;
}
.m-lbl {
  font-size: 9px;
  color: #64748b;
}
.m-arrow {
  color: #475569;
}

/* WASTE MAP */
.waste-map-section {
  display: flex;
  flex-direction: column;
}
.waste-headline-card {
  background: rgba(15, 21, 38, 0.85);
  border: 1px solid rgba(244, 63, 94, 0.3);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.waste-lead-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}
@media (max-width: 900px) {
  .waste-lead-row {
    flex-direction: column;
    gap: 12px;
  }
}
.waste-badge {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  color: #f43f5e;
  background: rgba(244, 63, 94, 0.15);
  padding: 2px 8px;
  border-radius: 4px;
}
.waste-headline {
  font-size: 18px;
  font-weight: 700;
  color: #ffffff;
  margin-top: 6px;
}
.health-scores-block {
  display: flex;
  gap: 16px;
}
.h-dim {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}
.h-lbl {
  font-size: 10px;
  color: #64748b;
}
.h-val {
  font-size: 14px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #38bdf8;
}

.waste-items-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
}
.waste-chip-card {
  background: rgba(18, 26, 47, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 6px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.w-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.w-cat {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
}
.cat-rework { background: rgba(245, 158, 11, 0.2); color: #fde68a; }
.cat-duplicate { background: rgba(244, 63, 94, 0.2); color: #fda4af; }
.cat-abandoned { background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }
.w-amt {
  font-size: 12px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #f43f5e;
}
.w-label {
  font-size: 11px;
  color: #cbd5e1;
}

/* 3D GRAPH SECTION */
.graph-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.graph-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #f8fafc;
}
.section-hint {
  font-size: 11px;
  color: #64748b;
  margin-left: 8px;
  font-family: var(--font-mono, monospace);
}
.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.path-active-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(99, 102, 241, 0.2);
  border: 1px solid rgba(99, 102, 241, 0.4);
  color: #818cf8;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
}
.btn-clear-path {
  background: transparent;
  border: 0;
  color: #cbd5e1;
  cursor: pointer;
  font-size: 11px;
}
.view-mode-toggle {
  display: flex;
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  padding: 2px;
  gap: 2px;
}
.mode-btn {
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
  background: transparent;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.mode-btn.active {
  background: rgba(99, 102, 241, 0.25);
  color: #818cf8;
}

/* TIMELINE MODE */
.timeline-container {
  background: rgba(10, 15, 29, 0.95);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 20px;
  max-height: 460px;
  overflow-y: auto;
}
.timeline-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  padding-bottom: 8px;
}
.timeline-title {
  font-size: 13px;
  font-weight: 700;
  color: #f1f5f9;
}
.timeline-count {
  font-size: 11px;
  color: #64748b;
  font-family: var(--font-mono, monospace);
}
.timeline-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.timeline-item {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
}
.timeline-time {
  font-family: var(--font-mono, monospace);
  color: #38bdf8;
  min-width: 90px;
}
.timeline-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #6366f1;
}
.timeline-event {
  color: #cbd5e1;
}

/* DRAWER */
.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(4px);
  z-index: var(--z-modal, 50);
  display: flex;
  justify-content: flex-end;
}
.cost-drawer {
  width: 440px;
  max-width: 90vw;
  height: 100%;
  background: #0d1424;
  border-left: 1px solid rgba(99, 102, 241, 0.3);
  box-shadow: -10px 0 30px rgba(0, 0, 0, 0.8);
  display: flex;
  flex-direction: column;
  padding: 24px;
  gap: 16px;
  overflow-y: auto;
}
.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.drawer-badge {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  color: #818cf8;
  background: rgba(99, 102, 241, 0.15);
  padding: 2px 8px;
  border-radius: 4px;
}
.drawer-close {
  background: transparent;
  border: 0;
  color: #94a3b8;
  font-size: 18px;
  cursor: pointer;
}
.drawer-title {
  font-size: 18px;
  font-weight: 700;
  color: #ffffff;
}
.drawer-amount {
  font-size: 24px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #f59e0b;
}
.drawer-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.detail-box {
  background: rgba(18, 26, 47, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.detail-label {
  font-size: 10px;
  color: #64748b;
  text-transform: uppercase;
  font-weight: 700;
}
.detail-text {
  font-size: 12px;
  color: #cbd5e1;
}
.btn-highlight-this {
  background: #6366f1;
  color: #ffffff;
  font-size: 11px;
  font-weight: 700;
  padding: 8px 12px;
  border-radius: 4px;
  border: 0;
  cursor: pointer;
}

/* COMMON */
.text-good { color: #34d399; }
.text-blue { color: #38bdf8; }
.text-warn { color: #f59e0b; }
.text-purple { color: #a855f7; }
.tone-blue { color: #38bdf8; }
.tone-purple { color: #c084fc; }
.font-mono { font-family: var(--font-mono, monospace); }
</style>
