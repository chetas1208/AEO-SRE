<script setup lang="ts">
import type { IntentEnvelope, MatchCandidate, IntentConstraint } from '~/types/agentmatch'
import KnowledgeGraphExplorer from '~/components/graph/KnowledgeGraphExplorer.vue'

const matches = useMatches()
const {
  intents,
  selectedIntentId,
  selectedIntent,
  selectedCandidates,
  selectedCandidateId,
  selectedCandidate,
  nodes,
  edges,
  selectIntent,
  selectCandidate
} = matches

// View mode: 3D WebGL vs Accessible Semantic DOM Tree
const viewMode = ref<'3d' | 'tree'>('3d')

// Active detail tab
const activeTab = ref<'why-matches' | 'why-ai-misses' | 'marketing-gap' | 'constraints'>('why-matches')

// Selected constraint for evidence drawer
const activeConstraint = ref<IntentConstraint | null>(null)
const evidenceDrawerOpen = ref(false)

function openEvidenceDrawer(c: IntentConstraint) {
  activeConstraint.value = c
  evidenceDrawerOpen.value = true
}

function closeEvidenceDrawer() {
  evidenceDrawerOpen.value = false
  activeConstraint.value = null
}

// Format countdown minutes
function formatCountdown(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return 'Expired'
  const m = Math.floor(seconds / 60)
  return `Expires in ${m}m`
}

function handleNodeSelect(id: string | null) {
  if (!id) return
  // If user clicks a constraint or product node, open matching section
  const c = selectedIntent.value?.constraints.find(item => item.id === id)
  if (c) {
    openEvidenceDrawer(c)
  }
}
</script>

<template>
  <div class="split-workspace">
    <!-- LEFT SIDEBAR: Intent Envelopes Queue -->
    <aside class="queue-pane" aria-label="Personal Agent Intent Envelopes">
      <div class="pane-header">
        <div class="header-row">
          <span class="pane-title">Intent Envelopes</span>
          <span class="env-count-badge">{{ intents.length }} Active</span>
        </div>
        <p class="pane-subtitle">Personal agent demand captured via Muse Connector</p>
      </div>

      <div class="intent-list" role="list">
        <button
          v-for="env in intents"
          :key="env.id"
          role="listitem"
          :class="['intent-card', { active: selectedIntentId === env.id }]"
          @click="selectIntent(env.id)"
        >
          <div class="card-meta">
            <span :class="['source-pill', `source-${env.source.toLowerCase()}`]">{{ env.source }}</span>
            <span class="countdown-text">{{ formatCountdown(env.expiresInSeconds) }}</span>
          </div>

          <h3 class="intent-name">{{ env.intent }}</h3>
          <p class="intent-goal">{{ env.goal }}</p>

          <div class="card-footer">
            <span class="constraints-tag">{{ env.constraints.length }} constraints</span>
            <span v-if="env.discoveryGapsCount > 0" class="gap-pill">
              {{ env.discoveryGapsCount }} Discovery {{ env.discoveryGapsCount === 1 ? 'Gap' : 'Gaps' }}
            </span>
          </div>
        </button>
      </div>
    </aside>

    <!-- MAIN CENTER WORKSPACE: Match Evaluation & Graph -->
    <main class="workspace-pane floating-safe">
      <template v-if="selectedIntent">
        <!-- Top Intent Context Banner -->
        <header class="workspace-header">
          <div class="intent-info">
            <div class="meta-line">
              <span class="source-tag">Source: {{ selectedIntent.source }} Agent</span>
              <span class="divider">·</span>
              <span class="expires-tag">{{ formatCountdown(selectedIntent.expiresInSeconds) }}</span>
              <span class="divider">·</span>
              <span class="claims-tag">{{ selectedIntent.canonicalClaimsConsidered }} canonical claims evaluated</span>
            </div>
            <h2 class="workspace-title">{{ selectedIntent.intent }}</h2>
            <p class="workspace-goal">{{ selectedIntent.goal }}</p>
          </div>

          <!-- Candidate Product Selector Pills -->
          <div class="candidate-selector" role="tablist" aria-label="Evaluated Candidate Products">
            <button
              v-for="cand in selectedCandidates"
              :key="cand.id"
              role="tab"
              :aria-selected="selectedCandidateId === cand.id"
              :class="['candidate-btn', { active: selectedCandidateId === cand.id }]"
              @click="selectCandidate(cand.id)"
            >
              <div class="cand-name">{{ cand.productName }}</div>
              <div class="cand-scores">
                <span class="cand-actual">{{ cand.actualFitPct }}% Fit</span>
                <span v-if="cand.gapPp > 0" class="cand-gap">+{{ cand.gapPp }}pp Gap</span>
              </div>
            </button>
          </div>
        </header>

        <!-- Selected Candidate Overview & Fit Comparison Card -->
        <section v-if="selectedCandidate" class="comparison-card">
          <div class="scores-grid">
            <!-- Actual Fit Bar (Product Truth) -->
            <div class="score-block">
              <div class="score-label-row">
                <span class="score-label">Actual Product Truth Fit</span>
                <span class="score-number tone-good">{{ selectedCandidate.actualFitPct }}%</span>
              </div>
              <div class="progress-track">
                <div class="progress-fill tone-good" :style="{ width: `${selectedCandidate.actualFitPct}%` }" />
              </div>
              <div class="score-subtext">Verified against {{ selectedCandidate.satisfiedConstraintsCount }}/{{ selectedCandidate.totalConstraintsCount }} explicit constraints via Neo4j canonical claim graph.</div>
            </div>

            <!-- AI Perceived Fit Bar (Profound LLM Synthesis) -->
            <div class="score-block">
              <div class="score-label-row">
                <span class="score-label">AI Perceived Engine Fit</span>
                <span class="score-number tone-blue">{{ selectedCandidate.aiPerceivedFitPct }}%</span>
              </div>
              <div class="progress-track">
                <div class="progress-fill tone-blue" :style="{ width: `${selectedCandidate.aiPerceivedFitPct}%` }" />
              </div>
              <div class="score-subtext">Current LLM engine consensus measured by Profound across ChatGPT, Claude & Perplexity.</div>
            </div>

            <!-- The Discovery Gap Delta Callout -->
            <div class="gap-delta-block">
              <div class="gap-badge">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                  <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
                </svg>
                <span>+{{ selectedCandidate.gapPp }}pp Discovery Gap</span>
              </div>
              <p class="gap-explanation">
                Your product genuinely satisfies this buyer agent's requirements, but AI engines currently underrepresent your capabilities by <strong>{{ selectedCandidate.gapPp }} percentage points</strong>.
              </p>
            </div>
          </div>
        </section>

        <!-- Knowledge Graph Explorer (2D Analytical Default + 3D Spatial) -->
        <section class="graph-section" aria-label="AgentMatch Knowledge Graph Lineage">
          <KnowledgeGraphExplorer
            :initial-perspective="'intent'"
            :initial-focus-id="selectedCandidateId || selectedIntentId"
            title="AgentMatch Causal Lineage"
          />
        </section>

        <!-- Detail Tabs & Analysis Sections -->
        <section v-if="selectedCandidate" class="detail-section">
          <div class="tabs-header" role="tablist">
            <button
              role="tab"
              :aria-selected="activeTab === 'why-matches'"
              :class="['detail-tab', { active: activeTab === 'why-matches' }]"
              @click="activeTab = 'why-matches'"
            >
              Why It Matches
            </button>
            <button
              role="tab"
              :aria-selected="activeTab === 'why-ai-misses'"
              :class="['detail-tab', { active: activeTab === 'why-ai-misses' }]"
              @click="activeTab = 'why-ai-misses'"
            >
              Why AI Misses It
            </button>
            <button
              role="tab"
              :aria-selected="activeTab === 'marketing-gap'"
              :class="['detail-tab', { active: activeTab === 'marketing-gap' }]"
              @click="activeTab = 'marketing-gap'"
            >
              Marketing Gap & Action
            </button>
            <button
              role="tab"
              :aria-selected="activeTab === 'constraints'"
              :class="['detail-tab', { active: activeTab === 'constraints' }]"
              @click="activeTab = 'constraints'"
            >
              Verified Constraints ({{ selectedIntent.constraints.length }})
            </button>
          </div>

          <div class="tab-body">
            <!-- TAB 1: Why It Matches -->
            <div v-if="activeTab === 'why-matches'" class="tab-pane">
              <h4 class="pane-headline">Canonical Product Truth Alignment</h4>
              <p class="tab-narrative">{{ selectedCandidate.whyItMatches }}</p>

              <h5 class="sub-headline">Supporting Canonical Evidence</h5>
              <div class="evidence-cards-list">
                <div
                  v-for="(ev, idx) in selectedCandidate.supportingEvidence"
                  :key="idx"
                  class="evidence-card"
                >
                  <div class="ev-top">
                    <span :class="['status-badge', `status-${ev.status}`]">{{ ev.status }}</span>
                    <a v-if="ev.source" :href="ev.source" target="_blank" rel="noopener noreferrer" class="ev-link">
                      Source Proof ↗
                    </a>
                  </div>
                  <div class="ev-claim">{{ ev.claim }}</div>
                </div>
              </div>
            </div>

            <!-- TAB 2: Why AI Misses It -->
            <div v-if="activeTab === 'why-ai-misses'" class="tab-pane">
              <h4 class="pane-headline">AI Synthesizer & Perception Failure Analysis</h4>
              <p class="tab-narrative text-bad">{{ selectedCandidate.whyAiMissesIt }}</p>

              <div class="callout-box">
                <div class="callout-title">Source of Synthesizer Bias</div>
                <p class="callout-body">
                  Profound crawl inspection confirms LLM retrieval engines rely heavily on dated 2023–2024 competitor comparison charts and community forum answers rather than indexing your official canonical docs.
                </p>
              </div>
            </div>

            <!-- TAB 3: Marketing Gap & Action -->
            <div v-if="activeTab === 'marketing-gap'" class="tab-pane">
              <h4 class="pane-headline">Recommended Discovery Remediation</h4>
              <p class="tab-narrative">{{ selectedCandidate.marketingGap }}</p>

              <div class="action-card">
                <div class="action-icon">⚡</div>
                <div class="action-content">
                  <div class="action-title">Actionable Recommendation</div>
                  <div class="action-desc">{{ selectedCandidate.recommendedAction }}</div>
                  <div class="action-actions">
                    <NuxtLink to="/discovery-gaps" class="action-btn-primary">
                      View in Discovery Gaps Queue →
                    </NuxtLink>
                    <NuxtLink to="/experiments" class="action-btn-secondary">
                      Launch Controlled Experiment
                    </NuxtLink>
                  </div>
                </div>
              </div>
            </div>

            <!-- TAB 4: Verified Constraints -->
            <div v-if="activeTab === 'constraints'" class="tab-pane">
              <h4 class="pane-headline">Constraint Evaluation Matrix</h4>
              <div class="constraints-table">
                <div
                  v-for="c in selectedIntent.constraints"
                  :key="c.id"
                  class="constraint-row"
                  @click="openEvidenceDrawer(c)"
                >
                  <div class="c-status">
                    <span v-if="c.status === 'satisfied'" class="icon-good" title="Satisfied">✓</span>
                    <span v-else class="icon-bad" title="Contradicted">✕</span>
                  </div>
                  <div class="c-info">
                    <div class="c-statement">{{ c.statement }}</div>
                    <div class="c-reasons">{{ c.reasons[0] }}</div>
                  </div>
                  <div class="c-gap">
                    <span v-if="c.discoveryGap" class="badge-gap">Perception Gap</span>
                    <span v-else class="badge-aligned">Aligned</span>
                  </div>
                  <button type="button" class="c-btn" aria-label="View Evidence">
                    Details →
                  </button>
                </div>
              </div>
            </div>
          </div>
        </section>
      </template>

      <div v-else class="empty-state">
        <p>Select an intent envelope from the left sidebar to view matches and discovery gaps.</p>
      </div>
    </main>

    <!-- EVIDENCE DRAWER (Sliding / Floating Side Drawer) -->
    <div v-if="evidenceDrawerOpen && activeConstraint" class="evidence-drawer-backdrop" @click="closeEvidenceDrawer">
      <div class="evidence-drawer" role="dialog" aria-label="Constraint Evidence Proof" @click.stop>
        <div class="drawer-header">
          <div class="drawer-title-row">
            <span class="drawer-badge">Evidence Proof</span>
            <button class="drawer-close-btn" type="button" aria-label="Close Drawer" @click="closeEvidenceDrawer">✕</button>
          </div>
          <h3 class="drawer-headline">{{ activeConstraint.statement }}</h3>
        </div>

        <div class="drawer-body">
          <!-- Status Overview -->
          <div class="drawer-status-grid">
            <div class="status-box">
              <span class="status-sub">Product Truth</span>
              <span class="status-val text-good">{{ activeConstraint.status.toUpperCase() }}</span>
            </div>
            <div class="status-box">
              <span class="status-sub">AI Engine Perception</span>
              <span :class="['status-val', activeConstraint.aiPerception === 'contradicts' ? 'text-bad' : 'text-good']">
                {{ activeConstraint.aiPerception.toUpperCase() }}
              </span>
            </div>
          </div>

          <!-- Product Truth Basis -->
          <div class="drawer-section">
            <h4 class="drawer-section-title">Verified Canonical Basis</h4>
            <div v-for="(b, i) in activeConstraint.basis" :key="i" class="basis-item">
              <div v-if="b.key" class="basis-key">Key: {{ b.key }}</div>
              <p class="basis-stmt">{{ b.statement }}</p>
              <a v-if="b.source" :href="b.source" target="_blank" rel="noopener noreferrer" class="basis-link">
                Source Document: {{ b.source }} ↗
              </a>
            </div>
          </div>

          <!-- AI Perception Evidence -->
          <div v-if="activeConstraint.perceptionEvidence?.length" class="drawer-section">
            <h4 class="drawer-section-title text-bad">Identified AI Misconception / Counterevidence</h4>
            <div v-for="(pe, i) in activeConstraint.perceptionEvidence" :key="i" class="counter-item">
              <div class="counter-quote">“{{ pe }}”</div>
            </div>
          </div>
        </div>
      </div>
    </div>
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
  letter-spacing: -0.01em;
}
.env-count-badge {
  font-size: 11px;
  font-weight: 600;
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.15);
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
.intent-list {
  display: flex;
  flex-direction: column;
  padding: 12px;
  gap: 10px;
}
.intent-card {
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
.intent-card:hover {
  background: rgba(23, 32, 56, 0.85);
  border-color: rgba(56, 189, 248, 0.3);
}
.intent-card.active {
  background: rgba(30, 42, 74, 0.9);
  border-color: #38bdf8;
  box-shadow: 0 0 16px rgba(56, 189, 248, 0.15);
}
.card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.source-pill {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  text-transform: uppercase;
}
.source-muse {
  background: rgba(56, 189, 248, 0.2);
  color: #38bdf8;
}
.source-manual {
  background: rgba(168, 85, 247, 0.2);
  color: #c084fc;
}
.source-test {
  background: rgba(148, 163, 184, 0.2);
  color: #cbd5e1;
}
.countdown-text {
  font-size: 11px;
  color: #94a3b8;
  font-family: var(--font-mono, monospace);
}
.intent-name {
  font-size: 13px;
  font-weight: 600;
  color: #f8fafc;
  margin-bottom: 4px;
}
.intent-goal {
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
.constraints-tag {
  font-size: 11px;
  color: #64748b;
  font-family: var(--font-mono, monospace);
}
.gap-pill {
  font-size: 10px;
  font-weight: 700;
  background: rgba(244, 63, 94, 0.2);
  color: #fda4af;
  padding: 2px 6px;
  border-radius: 4px;
}

/* MAIN WORKSPACE */
.match-workspace-pane {
  display: flex;
  flex-direction: column;
  padding: 24px;
  gap: 20px;
  overflow-y: auto;
}
.workspace-header {
  display: flex;
  flex-direction: column;
  gap: 16px;
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
.workspace-title {
  font-size: 20px;
  font-weight: 700;
  color: #ffffff;
  margin-top: 4px;
}
.workspace-goal {
  font-size: 13px;
  color: #cbd5e1;
  margin-top: 4px;
}
.candidate-selector {
  display: flex;
  gap: 10px;
  margin-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  padding-top: 14px;
}
.candidate-btn {
  display: flex;
  flex-direction: column;
  text-align: left;
  background: rgba(18, 26, 47, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  padding: 8px 14px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.candidate-btn:hover {
  background: rgba(30, 41, 69, 0.8);
  border-color: rgba(56, 189, 248, 0.3);
}
.candidate-btn.active {
  background: rgba(30, 58, 110, 0.5);
  border-color: #38bdf8;
}
.cand-name {
  font-size: 13px;
  font-weight: 600;
  color: #f8fafc;
}
.cand-scores {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  margin-top: 2px;
}
.cand-actual {
  color: #34d399;
  font-weight: 600;
}
.cand-gap {
  color: #f43f5e;
  font-weight: 700;
}

/* SCORES & GAP COMPARISON */
.comparison-card {
  background: rgba(15, 21, 38, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 20px;
}
.scores-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1.2fr;
  gap: 20px;
}
@media (max-width: 1024px) {
  .scores-grid {
    grid-template-columns: 1fr;
  }
}
.score-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.score-label-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.score-label {
  font-size: 12px;
  font-weight: 600;
  color: #cbd5e1;
}
.score-number {
  font-size: 20px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
}
.progress-track {
  width: 100%;
  height: 8px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.5s ease;
}
.tone-good {
  color: #34d399;
}
.progress-fill.tone-good {
  background: linear-gradient(90deg, #10b981, #34d399);
}
.tone-blue {
  color: #38bdf8;
}
.progress-fill.tone-blue {
  background: linear-gradient(90deg, #6366f1, #38bdf8);
}
.score-subtext {
  font-size: 11px;
  color: #94a3b8;
  line-height: 1.4;
}
.gap-delta-block {
  background: rgba(244, 63, 94, 0.1);
  border: 1px solid rgba(244, 63, 94, 0.3);
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.gap-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #f43f5e;
  font-weight: 700;
  font-size: 14px;
}
.gap-explanation {
  font-size: 12px;
  color: #f1f5f9;
  line-height: 1.4;
}

/* GRAPH SECTION */
.graph-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.graph-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
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
.mode-toggle {
  display: flex;
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  padding: 2px;
  gap: 2px;
}
.mode-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
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
  background: rgba(56, 189, 248, 0.2);
  color: #38bdf8;
}

/* DETAIL TABS & BODY */
.detail-section {
  background: rgba(15, 21, 38, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 10px;
  overflow: hidden;
}
.tabs-header {
  display: flex;
  background: rgba(10, 15, 30, 0.8);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.detail-tab {
  padding: 12px 18px;
  font-size: 13px;
  font-weight: 600;
  color: #94a3b8;
  background: transparent;
  border: 0;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: all 0.15s ease;
}
.detail-tab:hover {
  color: #e2e8f0;
}
.detail-tab.active {
  color: #38bdf8;
  border-bottom-color: #38bdf8;
  background: rgba(56, 189, 248, 0.05);
}
.tab-body {
  padding: 20px;
}
.pane-headline {
  font-size: 14px;
  font-weight: 600;
  color: #f1f5f9;
  margin-bottom: 8px;
}
.tab-narrative {
  font-size: 13px;
  color: #cbd5e1;
  line-height: 1.6;
}
.sub-headline {
  font-size: 12px;
  font-weight: 600;
  color: #94a3b8;
  margin-top: 16px;
  margin-bottom: 8px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.evidence-cards-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 10px;
}
.evidence-card {
  background: rgba(18, 26, 47, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ev-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.status-badge {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  padding: 2px 6px;
  border-radius: 4px;
}
.status-verified {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
}
.ev-link {
  font-size: 11px;
  color: #38bdf8;
  text-decoration: none;
}
.ev-link:hover {
  text-decoration: underline;
}
.ev-claim {
  font-size: 12px;
  color: #f1f5f9;
  line-height: 1.4;
}

/* Callout Box */
.callout-box {
  margin-top: 14px;
  background: rgba(30, 41, 69, 0.4);
  border-left: 3px solid #6366f1;
  padding: 12px 16px;
  border-radius: 0 6px 6px 0;
}
.callout-title {
  font-size: 12px;
  font-weight: 600;
  color: #818cf8;
}
.callout-body {
  font-size: 12px;
  color: #cbd5e1;
  margin-top: 4px;
  line-height: 1.5;
}

/* Action Card */
.action-card {
  display: flex;
  gap: 16px;
  background: rgba(20, 28, 50, 0.7);
  border: 1px solid rgba(56, 189, 248, 0.3);
  border-radius: 8px;
  padding: 18px;
  margin-top: 14px;
}
.action-icon {
  font-size: 24px;
}
.action-title {
  font-size: 14px;
  font-weight: 600;
  color: #ffffff;
}
.action-desc {
  font-size: 13px;
  color: #cbd5e1;
  margin-top: 4px;
  line-height: 1.5;
}
.action-actions {
  display: flex;
  gap: 12px;
  margin-top: 14px;
}
.action-btn-primary {
  display: inline-flex;
  align-items: center;
  padding: 8px 16px;
  background: #38bdf8;
  color: #060911;
  font-size: 12px;
  font-weight: 700;
  border-radius: 6px;
  text-decoration: none;
  transition: background 0.15s ease;
}
.action-btn-primary:hover {
  background: #7dd3fc;
}
.action-btn-secondary {
  display: inline-flex;
  align-items: center;
  padding: 8px 16px;
  background: rgba(255, 255, 255, 0.08);
  color: #f1f5f9;
  font-size: 12px;
  font-weight: 600;
  border-radius: 6px;
  text-decoration: none;
  transition: background 0.15s ease;
}
.action-btn-secondary:hover {
  background: rgba(255, 255, 255, 0.15);
}

/* Constraints Table */
.constraints-table {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.constraint-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: rgba(18, 26, 47, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.constraint-row:hover {
  background: rgba(30, 41, 69, 0.8);
  border-color: rgba(56, 189, 248, 0.3);
}
.c-status {
  width: 20px;
  text-align: center;
  font-weight: 700;
}
.icon-good {
  color: #34d399;
}
.icon-bad {
  color: #f43f5e;
}
.c-info {
  flex: 1;
}
.c-statement {
  font-size: 13px;
  font-weight: 600;
  color: #f8fafc;
}
.c-reasons {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
}
.badge-gap {
  font-size: 10px;
  font-weight: 700;
  background: rgba(244, 63, 94, 0.2);
  color: #fda4af;
  padding: 2px 6px;
  border-radius: 4px;
}
.badge-aligned {
  font-size: 10px;
  font-weight: 600;
  background: rgba(16, 185, 129, 0.15);
  color: #34d399;
  padding: 2px 6px;
  border-radius: 4px;
}
.c-btn {
  background: transparent;
  border: 0;
  color: #38bdf8;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

/* EVIDENCE DRAWER MODAL */
.evidence-drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  z-index: var(--z-modal, 50);
  display: flex;
  justify-content: flex-end;
}
.evidence-drawer {
  width: 480px;
  max-width: 90vw;
  height: 100%;
  background: #0d1424;
  border-left: 1px solid rgba(56, 189, 248, 0.3);
  box-shadow: -10px 0 30px rgba(0, 0, 0, 0.8);
  display: flex;
  flex-direction: column;
  padding: 24px;
  gap: 20px;
  overflow-y: auto;
}
.drawer-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  padding-bottom: 16px;
}
.drawer-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.drawer-badge {
  font-size: 11px;
  font-weight: 700;
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.15);
  padding: 2px 8px;
  border-radius: 4px;
  text-transform: uppercase;
}
.drawer-close-btn {
  background: transparent;
  border: 0;
  color: #94a3b8;
  font-size: 18px;
  cursor: pointer;
}
.drawer-headline {
  font-size: 16px;
  font-weight: 700;
  color: #ffffff;
}
.drawer-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.drawer-status-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.status-box {
  background: rgba(18, 26, 47, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.status-sub {
  font-size: 11px;
  color: #94a3b8;
}
.status-val {
  font-size: 13px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
}
.drawer-section-title {
  font-size: 12px;
  font-weight: 700;
  color: #e2e8f0;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 8px;
}
.basis-item {
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.basis-key {
  font-size: 11px;
  font-family: var(--font-mono, monospace);
  color: #38bdf8;
}
.basis-stmt {
  font-size: 13px;
  color: #cbd5e1;
  line-height: 1.4;
}
.basis-link {
  font-size: 11px;
  color: #38bdf8;
  text-decoration: none;
  word-break: break-all;
}
.basis-link:hover {
  text-decoration: underline;
}
.counter-item {
  background: rgba(244, 63, 94, 0.08);
  border: 1px solid rgba(244, 63, 94, 0.2);
  border-radius: 6px;
  padding: 12px;
}
.counter-quote {
  font-size: 12px;
  color: #fda4af;
  font-style: italic;
  line-height: 1.5;
}
.text-good {
  color: #34d399;
}
.text-bad {
  color: #f43f5e;
}
</style>
