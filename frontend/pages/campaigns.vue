<script setup lang="ts">
import type { Campaign, CostLineageNode, CostCompositionItem, PersonCost, AgentCost, VideoCost, AssetLineageItem } from '~/types/campaign'
import CampaignGraphScene from '~/components/visualization/CampaignGraphScene.vue'
import CampaignGraphTree from '~/components/visualization/CampaignGraphTree.vue'

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
  clearHighlights
} = useCampaigns()

// Visualization mode: 3D graph, accessible DOM tree, or chronological timeline
const vizMode = ref<'3d' | 'tree' | 'timeline'>('3d')

// Detail drill-down tab
const detailTab = ref<'costs' | 'people' | 'agents' | 'videos' | 'assets' | 'profound' | 'muse' | 'waste'>('costs')

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
  <div class="campaigns-layout">
    <!-- LEFT SIDEBAR: Campaign Queue -->
    <aside class="campaign-queue-pane" aria-label="Campaign Financial Queue">
      <div class="pane-header">
        <div class="header-row">
          <span class="pane-title">Campaigns</span>
          <span class="count-badge">{{ campaigns.length }} Tracked</span>
        </div>
        <p class="pane-subtitle">Financial & outcome lineage across marketing investments</p>
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
    <main class="campaign-workspace-pane">
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
            <span :class="['status-pill-lg', `status-${selectedCampaign.status.toLowerCase()}`]">
              {{ selectedCampaign.status }}
            </span>
            <span class="budget-tag">
              Budget: ${{ Number(selectedCampaign.budget).toLocaleString() }} {{ selectedCampaign.currency }}
            </span>
          </div>
        </header>

        <!-- TOP FINANCIAL STRIP (Max 5 High-Value Metrics) -->
        <section class="financial-strip" aria-label="Top Financial Strip">
          <div class="metric-card" @click="highlightCostPath()">
            <span class="metric-label">Total Cost</span>
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
            <span class="metric-sub">Confidence: {{ selectedCampaign.measurement_confidence }}</span>
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

        <!-- ROI CONFIDENCE & ATTRIBUTION SOURCES PANEL -->
        <section class="confidence-panel">
          <div class="conf-header">
            <div class="conf-badge-row">
              <span :class="['conf-badge-lg', `conf-${selectedCampaign.measurement_confidence.toLowerCase()}`]">
                {{ selectedCampaign.measurement_confidence }} MEASUREMENT CONFIDENCE
              </span>
              <span class="conf-note">No isolated ROI figures: every return dollar is mapped to its evidentiary origin</span>
            </div>
          </div>

          <div class="return-sources-grid">
            <div class="source-card">
              <span class="source-type text-good">DIRECT</span>
              <span class="source-amount">${{ Number(selectedCampaign.return_sources.direct).toLocaleString() }}</span>
              <span class="source-desc">Signed contracts & closed won revenue</span>
            </div>
            <div class="source-card">
              <span class="source-type text-blue">ATTRIBUTED</span>
              <span class="source-amount">${{ Number(selectedCampaign.return_sources.attributed).toLocaleString() }}</span>
              <span class="source-desc">CRM pipeline with touchpoint attribution</span>
            </div>
            <div class="source-card">
              <span class="source-type text-warn">MODELED</span>
              <span class="source-amount">${{ Number(selectedCampaign.return_sources.modeled).toLocaleString() }}</span>
              <span class="source-desc">Estimated downstream lifetime value</span>
            </div>
            <div class="source-card">
              <span class="source-type text-purple">PROXY</span>
              <span class="source-amount">{{ selectedCampaign.return_sources.proxy }}</span>
              <span class="source-desc">Synthesizer visibility & citation gains</span>
            </div>
          </div>
        </section>

        <!-- SIGNATURE 3D CAMPAIGN GRAPH & TIMELINE SECTION -->
        <section class="graph-section">
          <div class="graph-toolbar">
            <div class="toolbar-title-block">
              <h3 class="section-title">Campaign Lineage Graph</h3>
              <span class="section-hint">Investment → People & Agents → Assets → Distribution → Profound & Muse → Outcomes</span>
            </div>

            <div class="toolbar-actions">
              <div v-if="highlightedPathNodeIds.length > 0" class="path-active-pill">
                <span>Path highlighted</span>
                <button type="button" class="btn-clear-path" @click="clearHighlights">✕ Clear</button>
              </div>

              <div class="view-mode-toggle">
                <button
                  type="button"
                  :class="['mode-btn', { active: vizMode === '3d' }]"
                  @click="vizMode = '3d'"
                >
                  3D Spatial (Three.js)
                </button>
                <button
                  type="button"
                  :class="['mode-btn', { active: vizMode === 'tree' }]"
                  @click="vizMode = 'tree'"
                >
                  Accessible DOM Tree
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
            <CampaignGraphScene
              v-if="vizMode === '3d'"
              :nodes="graphData?.nodes || []"
              :edges="graphData?.edges || []"
              :highlight-node-ids="highlightedPathNodeIds"
              :highlight-edge-ids="highlightedPathEdgeIds"
            />
            <CampaignGraphTree
              v-else-if="vizMode === 'tree'"
              :nodes="graphData?.nodes || []"
              :edges="graphData?.edges || []"
              :highlight-node-ids="highlightedPathNodeIds"
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

        <!-- COST COMPOSITION & HIERARCHICAL LINEAGE SECTION -->
        <section class="cost-section">
          <div class="section-top">
            <h3 class="section-title">Cost Composition & Lineage</h3>
            <span class="section-hint">Click branches to trace exact expenditures down to hours, runs, and invoices</span>
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

          <!-- Cost Lineage Interactive Tree -->
          <div class="lineage-tree-box">
            <div class="lineage-root-row" @click="lineageExpanded = !lineageExpanded">
              <div class="row-left">
                <span class="expand-icon">{{ lineageExpanded ? '▼' : '▶' }}</span>
                <strong class="lineage-name">{{ selectedCampaign.cost_lineage.name }}</strong>
              </div>
              <div class="row-right">
                <span class="lineage-amount">${{ Number(selectedCampaign.cost_lineage.amount).toLocaleString() }}</span>
                <button type="button" class="btn-inspect" @click.stop="highlightCostPath()">Highlight Path</button>
              </div>
            </div>

            <div v-if="lineageExpanded" class="lineage-children">
              <div
                v-for="child in selectedCampaign.cost_lineage.children || []"
                :key="child.id"
                class="lineage-branch"
              >
                <div class="branch-header" @click="inspectCostNode(child)">
                  <span class="branch-name">{{ child.name }}</span>
                  <span class="branch-amount">${{ Number(child.amount).toLocaleString() }}</span>
                </div>

                <div v-if="child.children?.length" class="sub-branches">
                  <div
                    v-for="sub in child.children"
                    :key="sub.id"
                    class="sub-branch-item"
                    @click="inspectCostNode(sub)"
                  >
                    <span class="sub-name">{{ sub.name }}</span>
                    <span v-if="sub.details" class="sub-details">{{ sub.details }}</span>
                    <span class="sub-amount">${{ Number(sub.amount).toLocaleString() }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- DRILL-DOWN SUB-SURFACES (People, Agents, Videos, Assets, Profound, Muse, Waste) -->
        <section class="detail-drilldown-section">
          <div class="drilldown-tabs" role="tablist">
            <button
              role="tab"
              :aria-selected="detailTab === 'costs'"
              :class="['tab-btn', { active: detailTab === 'costs' }]"
              @click="detailTab = 'costs'"
            >
              Cost Overview
            </button>
            <button
              role="tab"
              :aria-selected="detailTab === 'people'"
              :class="['tab-btn', { active: detailTab === 'people' }]"
              @click="detailTab = 'people'"
            >
              People ({{ selectedCampaign.people.length }})
            </button>
            <button
              role="tab"
              :aria-selected="detailTab === 'agents'"
              :class="['tab-btn', { active: detailTab === 'agents' }]"
              @click="detailTab = 'agents'"
            >
              Agents ({{ selectedCampaign.agents.length }})
            </button>
            <button
              role="tab"
              :aria-selected="detailTab === 'videos'"
              :class="['tab-btn', { active: detailTab === 'videos' }]"
              @click="detailTab = 'videos'"
            >
              Videos ({{ selectedCampaign.videos.length }})
            </button>
            <button
              role="tab"
              :aria-selected="detailTab === 'assets'"
              :class="['tab-btn', { active: detailTab === 'assets' }]"
              @click="detailTab = 'assets'"
            >
              Asset Lineage
            </button>
            <button
              role="tab"
              :aria-selected="detailTab === 'profound'"
              :class="['tab-btn', { active: detailTab === 'profound' }]"
              @click="detailTab = 'profound'"
            >
              Profound Impact
            </button>
            <button
              role="tab"
              :aria-selected="detailTab === 'muse'"
              :class="['tab-btn', { active: detailTab === 'muse' }]"
              @click="detailTab = 'muse'"
            >
              Muse Outcomes
            </button>
            <button
              role="tab"
              :aria-selected="detailTab === 'waste'"
              :class="['tab-btn', { active: detailTab === 'waste' }]"
              @click="detailTab = 'waste'"
            >
              Inefficiency Analysis
            </button>
          </div>

          <div class="drilldown-content">
            <!-- PEOPLE TAB -->
            <div v-if="detailTab === 'people'" class="pane-wrap">
              <h4 class="pane-title">Human Contributor Cost Model</h4>
              <p class="pane-desc">Time and hourly rates logged against specific campaign deliverables. Quality: MANUAL & ESTIMATED.</p>
              <div class="table-wrap">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>Contributor</th>
                      <th>Role</th>
                      <th>Hours</th>
                      <th>Rate</th>
                      <th>Total</th>
                      <th>Quality</th>
                      <th>Outputs</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="p in selectedCampaign.people" :key="p.id">
                      <td><strong>{{ p.name }}</strong></td>
                      <td>{{ p.role }}</td>
                      <td>{{ p.hours }} hrs</td>
                      <td>${{ p.hourly_cost }}/hr</td>
                      <td class="text-good font-mono">${{ Number(p.total_cost).toLocaleString() }}</td>
                      <td><span class="source-tag">{{ p.source_quality }}</span></td>
                      <td>{{ p.outputs.join(', ') }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <!-- AGENTS TAB -->
            <div v-if="detailTab === 'agents'" class="pane-wrap">
              <h4 class="pane-title">Autonomous Agent & Model API Costs</h4>
              <p class="pane-desc">Run counts, token consumption, and cost per approved output across autonomous agents.</p>
              <div class="table-wrap">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>Agent</th>
                      <th>Runs (Success / Fail)</th>
                      <th>Tokens</th>
                      <th>Model Cost</th>
                      <th>Tool Cost</th>
                      <th>Total Cost</th>
                      <th>Approved Outputs</th>
                      <th>Cost / Approved</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="ag in selectedCampaign.agents" :key="ag.id">
                      <td><strong>{{ ag.name }}</strong></td>
                      <td>{{ ag.successful_runs }} / {{ ag.runs }} ({{ ag.failed_runs }} failed)</td>
                      <td class="font-mono">{{ (ag.tokens / 1000000).toFixed(1) }}M</td>
                      <td class="font-mono">${{ ag.model_cost }}</td>
                      <td class="font-mono">${{ ag.tool_cost }}</td>
                      <td class="text-good font-mono">${{ ag.total_cost }}</td>
                      <td>{{ ag.approved_outputs }} ({{ ag.approval_rate_pct }}%)</td>
                      <td class="font-mono">${{ ag.cost_per_approved_output }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <!-- VIDEOS TAB -->
            <div v-if="detailTab === 'videos'" class="pane-wrap">
              <h4 class="pane-title">Video & Media Production Lineage</h4>
              <div v-if="selectedCampaign.videos.length === 0" class="empty-hint">No dedicated video assets for this campaign.</div>
              <div v-for="v in selectedCampaign.videos" :key="v.id" class="video-card">
                <div class="video-header">
                  <h5 class="video-name">{{ v.title }}</h5>
                  <span class="video-cost-tag">Total: ${{ Number(v.total_cost).toLocaleString() }}</span>
                </div>
                <div class="video-costs-row">
                  <span>Creator: ${{ v.creator_cost }}</span>
                  <span>Editing: ${{ v.editing_cost }}</span>
                  <span>AI Gen: ${{ v.ai_generation_cost }}</span>
                  <span>Distribution: ${{ v.distribution_cost }}</span>
                  <span>{{ v.versions_count }} revisions</span>
                </div>
                <div class="video-outcomes-row">
                  <span class="outcome-chip">Views: {{ v.views.toLocaleString() }}</span>
                  <span class="outcome-chip">Engagement: {{ v.engagement_pct }}%</span>
                  <span class="outcome-chip">Qualified Visits: {{ v.qualified_visits }}</span>
                  <span class="outcome-chip">Profound Citations: {{ v.profound_citations_observed }}</span>
                  <span class="outcome-chip">Muse Shortlists: {{ v.muse_shortlist_events }}</span>
                  <span class="outcome-chip text-good">Leads: {{ v.leads_generated }}</span>
                </div>
              </div>
            </div>

            <!-- ASSET LINEAGE TAB -->
            <div v-if="detailTab === 'assets'" class="pane-wrap">
              <h4 class="pane-title">Asset Lineage & Output Provenance</h4>
              <div class="assets-grid">
                <div v-for="ast in selectedCampaign.assets" :key="ast.id" class="asset-card">
                  <div class="ast-top">
                    <span class="ast-type">{{ ast.type }}</span>
                    <span class="ast-approved">Approved by {{ ast.approved_by }}</span>
                  </div>
                  <h5 class="ast-title">{{ ast.title }}</h5>
                  <div class="ast-lineage-list">
                    <div>Created by: <strong>{{ ast.created_by }}</strong></div>
                    <div v-if="ast.generated_by_agent">Generated by: <em>{{ ast.generated_by_agent }}</em></div>
                    <div v-if="ast.edited_by">Edited by: {{ ast.edited_by }}</div>
                    <div>Distributed on: {{ ast.distributed_on.join(', ') }}</div>
                  </div>
                  <div class="ast-downstream">
                    <span class="downstream-label">Linked Outcomes:</span>
                    <span class="downstream-text">{{ ast.downstream_outcomes.join(' · ') }}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- PROFOUND TAB -->
            <div v-if="detailTab === 'profound'" class="pane-wrap">
              <div class="profound-header-row">
                <h4 class="pane-title">Profound AI Discovery Impact</h4>
                <span class="profound-note">{{ selectedCampaign.profound_impact.attribution_note }}</span>
              </div>
              <div class="profound-metrics-grid">
                <div class="p-box">
                  <span class="p-label">Visibility Shift</span>
                  <span class="p-val text-good">+{{ selectedCampaign.profound_impact.visibility_shift_pp }}pp</span>
                  <span class="p-sub">Observed post-campaign</span>
                </div>
                <div class="p-box">
                  <span class="p-label">Citation Share Shift</span>
                  <span class="p-val text-good">+{{ selectedCampaign.profound_impact.citation_share_shift_pp }}pp</span>
                  <span class="p-sub">Official domain citations</span>
                </div>
                <div class="p-box">
                  <span class="p-label">Prompt Coverage</span>
                  <span class="p-val tone-blue">{{ selectedCampaign.profound_impact.prompt_coverage_pct }}%</span>
                  <span class="p-sub">Intent clusters addressed</span>
                </div>
                <div class="p-box">
                  <span class="p-label">Competitor Share</span>
                  <span class="p-val text-good">{{ selectedCampaign.profound_impact.competitor_share_shift_pp }}pp</span>
                  <span class="p-sub">Displaced competitor citations</span>
                </div>
              </div>
              <div class="perception-status-box">
                <strong>AI Perception Status:</strong> {{ selectedCampaign.profound_impact.ai_perception_status }} across {{ selectedCampaign.profound_impact.affected_clusters_count }} prompt clusters.
              </div>
            </div>

            <!-- MUSE TAB -->
            <div v-if="detailTab === 'muse'" class="pane-wrap">
              <h4 class="pane-title">Muse Agent-Mediated Funnel</h4>
              <p class="pane-desc">Telemetry from personal agents evaluating products against verified buyer constraints.</p>
              <div class="funnel-chain">
                <div
                  v-for="(step, i) in selectedCampaign.muse_outcomes.funnel"
                  :key="i"
                  class="funnel-step"
                >
                  <span class="step-num">{{ step.count }}</span>
                  <span class="step-name">{{ step.step }}</span>
                  <span v-if="i < selectedCampaign.muse_outcomes.funnel.length - 1" class="step-arrow">→</span>
                </div>
              </div>
            </div>

            <!-- WASTE / INEFFICIENCY TAB -->
            <div v-if="detailTab === 'waste'" class="pane-wrap">
              <div class="waste-header-row">
                <h4 class="pane-title">Marketing Inefficiency & Optimization Opportunities</h4>
                <span class="waste-total-badge">
                  Potential Inefficiency: ${{ Number(selectedCampaign.waste_breakdown.potential_inefficiency).toLocaleString() }}
                </span>
              </div>
              <div class="waste-list">
                <div
                  v-for="(w, i) in selectedCampaign.waste_breakdown.items"
                  :key="i"
                  class="waste-item"
                >
                  <span :class="['waste-category', `cat-${w.category.toLowerCase()}`]">{{ w.category }}</span>
                  <span class="waste-label">{{ w.label }}</span>
                  <span class="waste-amount">${{ Number(w.amount).toLocaleString() }}</span>
                </div>
              </div>
            </div>

            <!-- DEFAULT COSTS TAB -->
            <div v-if="detailTab === 'costs'" class="pane-wrap">
              <h4 class="pane-title">High-Level Cost Allocation</h4>
              <div class="cost-table-simple">
                <div
                  v-for="c in selectedCampaign.cost_composition"
                  :key="c.category"
                  class="cost-row-simple"
                >
                  <span class="c-cat">{{ c.category }}</span>
                  <span class="c-pct font-mono">{{ c.pct }}%</span>
                  <span class="c-amt font-mono">${{ Number(c.amount).toLocaleString() }}</span>
                  <span class="c-src">{{ c.source }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>
      </template>

      <div v-else class="empty-state">
        <p>No campaigns tracked yet. Create or ingest a campaign to start cost and outcome lineage.</p>
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
  </div>
</template>

<style scoped>
.campaigns-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  min-height: calc(100vh - 56px);
  background: var(--bg-0, #060911);
}
@media (max-width: 1024px) {
  .campaigns-layout {
    grid-template-columns: 1fr;
  }
}

/* QUEUE */
.campaign-queue-pane {
  background: rgba(10, 14, 26, 0.95);
  border-right: 1px solid var(--border, rgba(45, 58, 88, 0.55));
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}
.pane-header {
  padding: 16px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
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
}
.pane-subtitle {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 4px;
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
  margin-bottom: 6px;
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
  padding: 20px;
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

/* FINANCIAL STRIP */
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

/* ROI CONFIDENCE PANEL */
.confidence-panel {
  background: rgba(15, 21, 38, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 10px;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.conf-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.conf-badge-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.conf-badge-lg {
  font-size: 11px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 4px;
}
.conf-note {
  font-size: 12px;
  color: #94a3b8;
}
.return-sources-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
@media (max-width: 900px) {
  .return-sources-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
.source-card {
  background: rgba(18, 26, 47, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.source-type {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.source-amount {
  font-size: 18px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #ffffff;
}
.source-desc {
  font-size: 10px;
  color: #64748b;
}

/* GRAPH SECTION */
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
  font-size: 12px;
  color: #64748b;
  margin-left: 8px;
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

/* COST COMPOSITION & LINEAGE */
.cost-section {
  background: rgba(15, 21, 38, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.composition-bar {
  display: flex;
  height: 10px;
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

.lineage-tree-box {
  background: rgba(10, 15, 29, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 8px;
  overflow: hidden;
}
.lineage-root-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: rgba(18, 26, 47, 0.8);
  cursor: pointer;
}
.row-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.expand-icon {
  font-size: 10px;
  color: #6366f1;
}
.lineage-name {
  color: #ffffff;
  font-size: 13px;
}
.row-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.lineage-amount {
  font-size: 14px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #f59e0b;
}
.btn-inspect {
  background: rgba(99, 102, 241, 0.2);
  border: 1px solid rgba(99, 102, 241, 0.4);
  color: #818cf8;
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 4px;
  cursor: pointer;
}
.lineage-children {
  display: flex;
  flex-direction: column;
  padding: 10px 16px;
  gap: 8px;
}
.lineage-branch {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.branch-header {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 600;
  color: #e2e8f0;
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.branch-header:hover {
  background: rgba(255, 255, 255, 0.05);
}
.branch-amount {
  font-family: var(--font-mono, monospace);
  color: #cbd5e1;
}
.sub-branches {
  display: flex;
  flex-direction: column;
  margin-left: 20px;
  gap: 4px;
}
.sub-branch-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: #94a3b8;
  padding: 3px 8px;
  border-radius: 4px;
  cursor: pointer;
}
.sub-branch-item:hover {
  background: rgba(255, 255, 255, 0.04);
  color: #ffffff;
}
.sub-details {
  font-size: 10px;
  color: #64748b;
  font-family: var(--font-mono, monospace);
}
.sub-amount {
  font-family: var(--font-mono, monospace);
  color: #e2e8f0;
}

/* DRILLDOWN TABS */
.detail-drilldown-section {
  background: rgba(15, 21, 38, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 10px;
  overflow: hidden;
}
.drilldown-tabs {
  display: flex;
  background: rgba(10, 15, 30, 0.8);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  overflow-x: auto;
}
.tab-btn {
  padding: 12px 16px;
  font-size: 12px;
  font-weight: 600;
  color: #94a3b8;
  background: transparent;
  border: 0;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
}
.tab-btn:hover {
  color: #e2e8f0;
}
.tab-btn.active {
  color: #6366f1;
  border-bottom-color: #6366f1;
  background: rgba(99, 102, 241, 0.05);
}
.drilldown-content {
  padding: 20px;
}
.pane-title {
  font-size: 14px;
  font-weight: 600;
  color: #ffffff;
}
.pane-desc {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 4px;
  margin-bottom: 14px;
}
.table-wrap {
  overflow-x: auto;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  text-align: left;
}
.data-table th {
  padding: 8px 12px;
  color: #64748b;
  font-size: 10px;
  text-transform: uppercase;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.data-table td {
  padding: 10px 12px;
  color: #cbd5e1;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}
.source-tag {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.08);
}

/* VIDEOS */
.video-card {
  background: rgba(18, 26, 47, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.video-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.video-name {
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
}
.video-cost-tag {
  font-size: 12px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #38bdf8;
}
.video-costs-row {
  display: flex;
  gap: 14px;
  font-size: 11px;
  color: #94a3b8;
}
.video-outcomes-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.outcome-chip {
  font-size: 11px;
  background: rgba(255, 255, 255, 0.05);
  padding: 2px 8px;
  border-radius: 4px;
  color: #cbd5e1;
}

/* ASSETS */
.assets-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.asset-card {
  background: rgba(18, 26, 47, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ast-top {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: #38bdf8;
  font-family: var(--font-mono, monospace);
}
.ast-approved {
  color: #34d399;
}
.ast-title {
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
}
.ast-lineage-list {
  font-size: 11px;
  color: #94a3b8;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ast-downstream {
  border-top: 1px solid rgba(255, 255, 255, 0.05);
  padding-top: 6px;
  font-size: 11px;
  color: #34d399;
}
.downstream-label {
  color: #64748b;
  display: block;
  font-size: 10px;
}

/* PROFOUND */
.profound-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.profound-note {
  font-size: 12px;
  color: #a855f7;
}
.profound-metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
.p-box {
  background: rgba(18, 26, 47, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.p-label {
  font-size: 11px;
  color: #94a3b8;
}
.p-val {
  font-size: 20px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
}
.p-sub {
  font-size: 10px;
  color: #64748b;
}
.perception-status-box {
  margin-top: 14px;
  background: rgba(168, 85, 247, 0.1);
  border-left: 3px solid #a855f7;
  padding: 10px 14px;
  font-size: 12px;
  color: #e2e8f0;
  border-radius: 0 6px 6px 0;
}

/* MUSE */
.funnel-chain {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}
.funnel-step {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(18, 26, 47, 0.8);
  border: 1px solid rgba(45, 212, 191, 0.3);
  border-radius: 6px;
  padding: 8px 12px;
}
.step-num {
  font-size: 16px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #2dd4bf;
}
.step-name {
  font-size: 12px;
  color: #e2e8f0;
}
.step-arrow {
  color: #475569;
  font-weight: 700;
}

/* WASTE */
.waste-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.waste-total-badge {
  font-size: 12px;
  font-weight: 700;
  color: #f43f5e;
  background: rgba(244, 63, 94, 0.15);
  padding: 3px 10px;
  border-radius: 4px;
}
.waste-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.waste-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(18, 26, 47, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  padding: 10px 14px;
  font-size: 12px;
}
.waste-category {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}
.cat-rework {
  background: rgba(245, 158, 11, 0.2);
  color: #fde68a;
}
.cat-duplicate {
  background: rgba(244, 63, 94, 0.2);
  color: #fda4af;
}
.cat-abandoned {
  background: rgba(148, 163, 184, 0.2);
  color: #cbd5e1;
}
.waste-label {
  color: #e2e8f0;
  flex: 1;
  margin-left: 12px;
}
.waste-amount {
  font-family: var(--font-mono, monospace);
  font-weight: 700;
  color: #f43f5e;
}

/* SIMPLE COSTS */
.cost-table-simple {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cost-row-simple {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(18, 26, 47, 0.6);
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 12px;
}
.c-cat {
  font-weight: 600;
  color: #ffffff;
  min-width: 140px;
}
.c-pct {
  color: #94a3b8;
}
.c-amt {
  color: #38bdf8;
  font-weight: 700;
}
.c-src {
  font-size: 10px;
  color: #64748b;
  font-family: var(--font-mono, monospace);
}

/* DRAWER */
.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(4px);
  z-index: 100;
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
