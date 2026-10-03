<script setup lang="ts">
import type { DiscoveryGapItem, GapType } from '~/types/agentmatch'
import KnowledgeGraphExplorer from '~/components/graph/KnowledgeGraphExplorer.vue'
import ExperimentCreateDrawer from '~/components/experiments/ExperimentCreateDrawer.vue'

const gapsState = useDiscoveryGaps()
const experimentDrawerOpen = ref(false)
const {
  gaps,
  selectedGapId,
  selectedGap,
  filterType,
  filteredGaps,
  criticalGapsCount,
  selectGap,
  approveGap,
  rejectGap,
  modifyGap
} = gapsState

const filterOptions: Array<{ label: string; value: GapType | 'ALL' }> = [
  { label: 'All Gaps', value: 'ALL' },
  { label: 'Wrong Tier / Pricing', value: 'WRONG_TIER_PRICING' },
  { label: 'Missing Capability', value: 'MISSING_CAPABILITY' },
  { label: 'Stale Information', value: 'STALE_INFORMATION' },
  { label: 'Missing Citation', value: 'MISSING_CITATION' }
]

// Modal / prompt for modify note
const modifyModalOpen = ref(false)
const modifyNote = ref('')

function openModifyModal() {
  modifyNote.value = ''
  modifyModalOpen.value = true
}

function submitModify() {
  if (selectedGap.value) {
    modifyGap(selectedGap.value.id, modifyNote.value)
    modifyModalOpen.value = false
  }
}
</script>

<template>
  <div class="split-workspace">
    <!-- LEFT SIDEBAR: Discovery Gaps Queue -->
    <aside class="queue-pane" aria-label="Discovery Gaps Queue">
      <div class="pane-header">
        <div class="header-row">
          <span class="pane-title">Discovery Gaps</span>
          <span class="gap-count-badge">{{ filteredGaps.length }} Issues</span>
        </div>
        <p class="pane-subtitle">Where product truth wins but AI engines underrepresent you</p>
      </div>

      <!-- Filters -->
      <div class="filters-bar" role="tablist" aria-label="Gap Type Filters">
        <button
          v-for="opt in filterOptions"
          :key="opt.value"
          role="tab"
          :aria-selected="filterType === opt.value"
          :class="['filter-chip', { active: filterType === opt.value }]"
          @click="filterType = opt.value"
        >
          {{ opt.label }}
        </button>
      </div>

      <!-- Gaps Queue List -->
      <div class="gaps-list" role="list">
        <button
          v-for="gap in filteredGaps"
          :key="gap.id"
          role="listitem"
          :class="['gap-card', { active: selectedGapId === gap.id }]"
          @click="selectGap(gap.id)"
        >
          <div class="card-meta">
            <span :class="['severity-pill', `sev-${gap.severity}`]">{{ gap.severity }}</span>
            <span class="gap-pp-badge">+{{ gap.gapPp }}pp Gap</span>
          </div>

          <h3 class="gap-title">{{ gap.intentClass }}</h3>
          <div class="gap-product">{{ gap.productName }}</div>

          <div class="card-fits">
            <span class="fit-actual">Actual: {{ gap.actualFitPct }}%</span>
            <span class="fit-sep">vs</span>
            <span class="fit-perceived">AI: {{ gap.aiPerceivedFitPct }}%</span>
          </div>

          <div class="card-footer">
            <span class="clusters-tag">{{ gap.promptClustersCount }} Prompt Clusters</span>
            <span :class="['status-tag', `status-${gap.approvalStatus}`]">
              {{ gap.approvalStatus.toUpperCase() }}
            </span>
          </div>
        </button>
      </div>
    </aside>

    <!-- MAIN CENTER WORKSPACE: Side-by-side Truth vs Perception & Actions -->
    <main class="workspace-pane floating-safe">
      <template v-if="selectedGap">
        <!-- Top Gap Header -->
        <header class="workspace-header">
          <div class="header-top">
            <div class="badges-row">
              <span :class="['severity-pill', `sev-${selectedGap.severity}`]">{{ selectedGap.severity }} severity</span>
              <span class="gap-type-pill">{{ selectedGap.gapType }}</span>
              <span class="confidence-pill">{{ Math.round(selectedGap.confidence * 100) }}% Confidence</span>
            </div>
            <div class="gap-delta-tag">+{{ selectedGap.gapPp }}pp Perception Gap</div>
          </div>

          <h2 class="workspace-title">{{ selectedGap.intentClass }}</h2>
          <p class="workspace-subtitle">Target Product: <strong>{{ selectedGap.productName }}</strong></p>
        </header>

        <!-- SIDE-BY-SIDE TRUTH COMPARISON -->
        <section class="side-by-side-section">
          <div class="comparison-grid">
            <!-- Left: Verified Product Truth -->
            <div class="truth-card good-border">
              <div class="card-header">
                <div class="header-icon tone-good">✓</div>
                <div>
                  <h3 class="card-heading">Product Truth (Canonical)</h3>
                  <span class="card-subhead">Ground reality from your official documentation</span>
                </div>
              </div>

              <div class="card-body">
                <blockquote class="truth-statement">
                  “{{ selectedGap.productTruth.statement }}”
                </blockquote>

                <div class="truth-meta">
                  <div v-if="selectedGap.productTruth.canonicalKey" class="meta-row">
                    <span class="meta-label">Claim Key:</span>
                    <code class="meta-key">{{ selectedGap.productTruth.canonicalKey }}</code>
                  </div>
                  <div v-if="selectedGap.productTruth.canonicalSource" class="meta-row">
                    <span class="meta-label">Source Document:</span>
                    <a :href="selectedGap.productTruth.canonicalSource" target="_blank" rel="noopener noreferrer" class="source-link">
                      {{ selectedGap.productTruth.canonicalSource }} ↗
                    </a>
                  </div>
                  <div v-if="selectedGap.productTruth.verifiedAt" class="meta-row">
                    <span class="meta-label">Last Verified:</span>
                    <span class="meta-val">{{ selectedGap.productTruth.verifiedAt }}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Right: AI Perception / Stale Misconception -->
            <div class="perception-card bad-border">
              <div class="card-header">
                <div class="header-icon tone-bad">✕</div>
                <div>
                  <h3 class="card-heading">AI Engine Perception (Profound)</h3>
                  <span class="card-subhead">What buyers' agents read when querying LLMs</span>
                </div>
              </div>

              <div class="card-body">
                <blockquote class="perception-claim">
                  “{{ selectedGap.aiPerception.claim }}”
                </blockquote>

                <div class="perception-meta">
                  <div class="meta-row">
                    <span class="meta-label">Synthesized By:</span>
                    <div class="engines-list">
                      <span v-for="eng in selectedGap.aiPerception.engines" :key="eng" class="engine-tag">
                        {{ eng }}
                      </span>
                    </div>
                  </div>
                  <div v-if="selectedGap.aiPerception.likelySource" class="meta-row">
                    <span class="meta-label">Root Citation:</span>
                    <span class="likely-source text-bad">{{ selectedGap.aiPerception.likelySource }}</span>
                  </div>
                  <div v-if="selectedGap.aiPerception.citationsCount" class="meta-row">
                    <span class="meta-label">Total Citations:</span>
                    <span class="meta-val">{{ selectedGap.aiPerception.citationsCount }} references</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- KNOWLEDGE GRAPH LINEAGE (PERCEPTION PERSPECTIVE) -->
        <section class="graph-section" aria-label="AI Perception Knowledge Graph">
          <KnowledgeGraphExplorer
            :initial-perspective="'perception'"
            :initial-focus-id="selectedGapId"
            title="AI Perception Discrepancy Graph"
          />
        </section>

        <!-- COMPACT PROFOUND METRICS PANEL -->
        <section class="profound-metrics-section">
          <div class="metrics-header">
            <h3 class="section-heading">Profound AI Visibility & Perception Metrics</h3>
            <span class="metrics-source">Aggregated across top generative answer synthesizers</span>
          </div>

          <div class="metrics-grid">
            <div class="metric-box">
              <span class="metric-title">Brand Visibility</span>
              <span class="metric-num tone-blue">{{ selectedGap.profoundMetrics.visibilityPct }}%</span>
              <span class="metric-desc">Likelihood to appear in buyer queries</span>
            </div>
            <div class="metric-box">
              <span class="metric-title">Citation Share</span>
              <span class="metric-num tone-good">{{ selectedGap.profoundMetrics.citationSharePct }}%</span>
              <span class="metric-desc">Official domain citation weight</span>
            </div>
            <div class="metric-box">
              <span class="metric-title">Prompt Coverage</span>
              <span class="metric-num tone-warn">{{ selectedGap.profoundMetrics.promptCoveragePct }}%</span>
              <span class="metric-desc">Intent envelopes addressed</span>
            </div>
            <div class="metric-box">
              <span class="metric-title">Competitor Share</span>
              <span class="metric-num tone-bad">{{ selectedGap.profoundMetrics.competitorSharePct }}%</span>
              <span class="metric-desc">Share of voice taken by rivals</span>
            </div>
          </div>

          <div class="sources-callout">
            <span class="sources-label">Dominant Sources Driving Perception:</span>
            <div class="sources-tags">
              <span v-for="(src, idx) in selectedGap.profoundMetrics.topSources" :key="idx" class="source-bubble">
                {{ src }}
              </span>
            </div>
          </div>
        </section>

        <!-- RECOMMENDED MARKETING ACTION & APPROVAL -->
        <section class="action-decision-section">
          <div class="action-card-full">
            <div class="action-head">
              <div class="action-type-badge">{{ selectedGap.recommendedAction.type }}</div>
              <h3 class="action-headline">{{ selectedGap.recommendedAction.title }}</h3>
              <p class="action-desc">{{ selectedGap.recommendedAction.description }}</p>
            </div>

            <!-- Approval Controls -->
            <div class="action-bar">
              <div class="status-indicator">
                Current Status:
                <strong :class="['status-word', `status-${selectedGap.approvalStatus}`]">
                  {{ selectedGap.approvalStatus.toUpperCase() }}
                </strong>
              </div>

              <div class="buttons-group">
                <button
                  type="button"
                  class="btn-approve"
                  :disabled="selectedGap.approvalStatus === 'approved'"
                  @click="approveGap(selectedGap.id)"
                >
                  ✓ Approve Action
                </button>
                <button
                  type="button"
                  class="btn-modify"
                  @click="openModifyModal"
                >
                  ✎ Modify Plan
                </button>
                <button
                  type="button"
                  class="btn-reject"
                  :disabled="selectedGap.approvalStatus === 'rejected'"
                  @click="rejectGap(selectedGap.id)"
                >
                  ✕ Reject
                </button>
                <button
                  type="button"
                  class="btn-experiment"
                  data-testid="btn-create-gap-experiment"
                  @click="experimentDrawerOpen = true"
                >
                  🧪 Test in Experiments Engine →
                </button>
              </div>
            </div>
          </div>
        </section>
      </template>

      <div v-else class="empty-state">
        <p>Select a discovery gap to inspect the ground truth vs perception discrepancy.</p>
      </div>
    </main>

    <!-- MODIFY NOTE MODAL -->
    <div v-if="modifyModalOpen" class="modal-backdrop" @click="modifyModalOpen = false">
      <div class="modal-card" role="dialog" aria-label="Modify Action" @click.stop>
        <h3 class="modal-title">Modify Recommended Marketing Action</h3>
        <p class="modal-desc">Provide custom instructions or target guidelines for this discovery gap remediation:</p>
        <textarea
          v-model="modifyNote"
          class="modal-textarea"
          rows="4"
          placeholder="E.g. Also highlight SOC2 Type II audit report and add pricing calculator link..."
        />
        <div class="modal-actions">
          <button type="button" class="btn-cancel" @click="modifyModalOpen = false">Cancel</button>
          <button type="button" class="btn-submit" @click="submitModify">Save Modifications</button>
        </div>
      </div>
    </div>

    <!-- Real Experiment Creation Drawer from Discovery Gap -->
    <ExperimentCreateDrawer
      v-if="selectedGap"
      v-model="experimentDrawerOpen"
      initial-trigger="gap"
      :initial-name="`Remediate: ${selectedGap.title}`"
      :initial-hypothesis="selectedGap.actionPlan?.planDescription || `Updating canonical evidence will close the +${selectedGap.perceptionGap}% gap across answer engines.`"
      :initial-action="selectedGap.actionPlan?.actionType || 'update_existing_page'"
      :initial-target-url="selectedGap.productTruth?.sourceUrl || ''"
      initial-primary-metric="accuracy"
      :initial-incident-id="selectedGap.id"
    />
  </div>
</template>

<style scoped>
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
.gap-count-badge {
  font-size: 11px;
  font-weight: 600;
  color: #f43f5e;
  background: rgba(244, 63, 94, 0.15);
  padding: 2px 8px;
  border-radius: 999px;
  white-space: nowrap;
}
.pane-subtitle {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 4px;
  line-height: 1.4;
}
.filters-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.filter-chip {
  font-size: 11px;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s ease;
}
.filter-chip:hover {
  background: rgba(30, 41, 69, 0.8);
  color: #e2e8f0;
}
.filter-chip.active {
  background: rgba(56, 189, 248, 0.2);
  border-color: #38bdf8;
  color: #38bdf8;
}
.gaps-list {
  display: flex;
  flex-direction: column;
  padding: 12px;
  gap: 10px;
}
.gap-card {
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
.gap-card:hover {
  background: rgba(23, 32, 56, 0.85);
  border-color: rgba(244, 63, 94, 0.3);
}
.gap-card.active {
  background: rgba(30, 42, 74, 0.9);
  border-color: #f43f5e;
  box-shadow: 0 0 16px rgba(244, 63, 94, 0.15);
}
.card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.severity-pill {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  padding: 2px 6px;
  border-radius: 4px;
}
.sev-critical {
  background: rgba(244, 63, 94, 0.25);
  color: #fda4af;
}
.sev-high {
  background: rgba(245, 158, 11, 0.25);
  color: #fde68a;
}
.sev-medium {
  background: rgba(56, 189, 248, 0.2);
  color: #38bdf8;
}
.gap-pp-badge {
  font-size: 12px;
  font-weight: 700;
  color: #f43f5e;
  font-family: var(--font-mono, monospace);
}
.gap-title {
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
}
.gap-product {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 2px;
}
.card-fits {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  font-size: 11px;
}
.fit-actual {
  color: #34d399;
  font-weight: 600;
}
.fit-sep {
  color: #64748b;
}
.fit-perceived {
  color: #38bdf8;
}
.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}
.clusters-tag {
  font-size: 11px;
  color: #64748b;
  font-family: var(--font-mono, monospace);
}
.status-tag {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}
.status-pending {
  background: rgba(245, 158, 11, 0.15);
  color: #fde68a;
}
.status-approved {
  background: rgba(16, 185, 129, 0.15);
  color: #34d399;
}
.status-rejected {
  background: rgba(244, 63, 94, 0.15);
  color: #fda4af;
}

/* MAIN WORKSPACE */
.gaps-workspace-pane {
  display: flex;
  flex-direction: column;
  padding: 24px;
  gap: 20px;
  overflow-y: auto;
}
.workspace-header {
  background: rgba(15, 21, 38, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 10px;
  padding: 20px;
}
.header-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.badges-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.gap-type-pill {
  font-size: 11px;
  font-family: var(--font-mono, monospace);
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.1);
  padding: 2px 8px;
  border-radius: 4px;
}
.confidence-pill {
  font-size: 11px;
  color: #94a3b8;
  font-family: var(--font-mono, monospace);
}
.gap-delta-tag {
  font-size: 16px;
  font-weight: 800;
  color: #f43f5e;
  font-family: var(--font-mono, monospace);
  background: rgba(244, 63, 94, 0.15);
  padding: 4px 12px;
  border-radius: 6px;
  border: 1px solid rgba(244, 63, 94, 0.3);
}
.workspace-title {
  font-size: 22px;
  font-weight: 700;
  color: #ffffff;
  margin-top: 10px;
}
.workspace-subtitle {
  font-size: 13px;
  color: #cbd5e1;
  margin-top: 4px;
}

/* SIDE BY SIDE */
.side-by-side-section {
  display: flex;
  flex-direction: column;
}
.comparison-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}
@media (max-width: 900px) {
  .comparison-grid {
    grid-template-columns: 1fr;
  }
}
.truth-card {
  background: rgba(15, 21, 38, 0.85);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.good-border {
  border: 1px solid rgba(16, 185, 129, 0.3);
}
.perception-card {
  background: rgba(15, 21, 38, 0.85);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.bad-border {
  border: 1px solid rgba(244, 63, 94, 0.3);
}
.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}
.tone-good {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
}
.tone-bad {
  background: rgba(244, 63, 94, 0.2);
  color: #f43f5e;
}
.card-heading {
  font-size: 14px;
  font-weight: 700;
  color: #f8fafc;
}
.card-subhead {
  font-size: 11px;
  color: #94a3b8;
}
.truth-statement {
  background: rgba(16, 185, 129, 0.05);
  border-left: 3px solid #10b981;
  padding: 12px 14px;
  font-size: 13px;
  color: #e2e8f0;
  line-height: 1.5;
  border-radius: 0 6px 6px 0;
}
.perception-claim {
  background: rgba(244, 63, 94, 0.05);
  border-left: 3px solid #f43f5e;
  padding: 12px 14px;
  font-size: 13px;
  color: #fda4af;
  line-height: 1.5;
  border-radius: 0 6px 6px 0;
}
.truth-meta, .perception-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12px;
}
.meta-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.meta-label {
  color: #94a3b8;
  font-weight: 600;
  min-width: 100px;
}
.meta-key {
  font-family: var(--font-mono, monospace);
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.1);
  padding: 2px 6px;
  border-radius: 4px;
}
.source-link {
  color: #38bdf8;
  text-decoration: none;
}
.source-link:hover {
  text-decoration: underline;
}
.meta-val {
  color: #e2e8f0;
  font-family: var(--font-mono, monospace);
}
.engines-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.engine-tag {
  font-size: 11px;
  background: rgba(129, 140, 248, 0.15);
  color: #818cf8;
  padding: 2px 6px;
  border-radius: 4px;
}
.likely-source {
  font-size: 12px;
}

/* PROFOUND METRICS */
.profound-metrics-section {
  background: rgba(15, 21, 38, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.metrics-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.section-heading {
  font-size: 14px;
  font-weight: 600;
  color: #f8fafc;
}
.metrics-source {
  font-size: 12px;
  color: #64748b;
}
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
@media (max-width: 900px) {
  .metrics-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
.metric-box {
  background: rgba(18, 26, 47, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.metric-title {
  font-size: 11px;
  color: #94a3b8;
  font-weight: 600;
}
.metric-num {
  font-size: 22px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
}
.metric-desc {
  font-size: 10px;
  color: #64748b;
}
.tone-blue {
  color: #38bdf8;
}
.tone-warn {
  color: #f59e0b;
}
.sources-callout {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 10px;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
}
.sources-label {
  font-size: 11px;
  color: #94a3b8;
  font-weight: 600;
}
.sources-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.source-bubble {
  font-size: 11px;
  background: rgba(30, 41, 69, 0.6);
  color: #cbd5e1;
  padding: 2px 8px;
  border-radius: 4px;
}

/* RECOMMENDED ACTION & APPROVAL */
.action-decision-section {
  display: flex;
  flex-direction: column;
}
.action-card-full {
  background: rgba(15, 21, 38, 0.85);
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.action-type-badge {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.15);
  padding: 2px 8px;
  border-radius: 4px;
  margin-bottom: 6px;
}
.action-headline {
  font-size: 16px;
  font-weight: 700;
  color: #ffffff;
}
.action-desc {
  font-size: 13px;
  color: #cbd5e1;
  margin-top: 6px;
  line-height: 1.5;
}
.action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
@media (max-width: 900px) {
  .action-bar {
    flex-direction: column;
    gap: 12px;
    align-items: flex-start;
  }
}
.status-indicator {
  font-size: 12px;
  color: #94a3b8;
}
.status-word {
  margin-left: 4px;
  font-family: var(--font-mono, monospace);
}
.buttons-group {
  display: flex;
  align-items: center;
  gap: 10px;
}
.btn-approve {
  background: #10b981;
  color: #060911;
  font-size: 12px;
  font-weight: 700;
  padding: 8px 16px;
  border-radius: 6px;
  border: 0;
  cursor: pointer;
  transition: background 0.15s ease;
}
.btn-approve:hover:not(:disabled) {
  background: #34d399;
}
.btn-approve:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-modify {
  background: rgba(56, 189, 248, 0.15);
  border: 1px solid rgba(56, 189, 248, 0.4);
  color: #38bdf8;
  font-size: 12px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-modify:hover {
  background: rgba(56, 189, 248, 0.25);
}
.btn-reject {
  background: rgba(244, 63, 94, 0.15);
  border: 1px solid rgba(244, 63, 94, 0.4);
  color: #fda4af;
  font-size: 12px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: 6px;
  cursor: pointer;
}
.btn-reject:hover:not(:disabled) {
  background: rgba(244, 63, 94, 0.25);
}
.btn-reject:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-experiment {
  background: rgba(255, 255, 255, 0.08);
  color: #f1f5f9;
  font-size: 12px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: 6px;
  text-decoration: none;
  transition: background 0.15s ease;
}
.btn-experiment:hover {
  background: rgba(255, 255, 255, 0.15);
}

/* MODAL */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(4px);
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal-card {
  width: 520px;
  max-width: 90vw;
  background: #0d1424;
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 10px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.modal-title {
  font-size: 16px;
  font-weight: 700;
  color: #ffffff;
}
.modal-desc {
  font-size: 12px;
  color: #cbd5e1;
}
.modal-textarea {
  background: rgba(15, 21, 38, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  padding: 10px;
  color: #ffffff;
  font-size: 13px;
  resize: vertical;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 6px;
}
.btn-cancel {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #94a3b8;
  font-size: 12px;
  padding: 6px 14px;
  border-radius: 4px;
  cursor: pointer;
}
.btn-submit {
  background: #38bdf8;
  color: #060911;
  font-size: 12px;
  font-weight: 700;
  padding: 6px 14px;
  border-radius: 4px;
  border: 0;
  cursor: pointer;
}
</style>
