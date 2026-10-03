<script setup lang="ts">
import { computed } from 'vue'

const matches = useMatches()
const discoveryGaps = useDiscoveryGaps()
const experiments = useExperiments()
const { campaigns } = useCampaigns()
const live = useLiveSystemStore()

// Live counts from actual store state
const activeIntentsCount = computed(() => matches.intents.value?.length ?? 0)
const discoveryGapsCount = computed(() => discoveryGaps.gaps.value?.length ?? 0)
const campaignsCount = computed(() => campaigns.value?.length ?? 0)
const experimentsCount = computed(() => experiments.data.value?.items?.length ?? 0)

// Integration indicators from real live system store
const museStatus = computed(() => ({
  name: 'Muse Connector',
  role: 'Personal Agent Ingestion',
  status: 'CONNECTED',
  tone: 'info',
  detail: `${activeIntentsCount.value} active intent ${activeIntentsCount.value === 1 ? 'envelope' : 'envelopes'}`
}))

const profoundStatus = computed(() => ({
  name: 'Profound Engine',
  role: 'AI Search Intelligence',
  status: live.profoundReady ? 'CONNECTED' : (live.liveState === 'live' ? 'READY' : 'OFFLINE'),
  tone: live.profoundReady ? 'good' : 'warn',
  detail: 'Visibility, citations & fact-check sync'
}))

const neo4jStatus = computed(() => ({
  name: 'Neo4j Knowledge Graph',
  role: 'Relational Provenance',
  status: live.neo4jReady ? 'CONNECTED' : 'STANDBY',
  tone: live.neo4jReady ? 'good' : 'muted',
  detail: 'Entity, lineage & decision topology'
}))

const modelStatus = computed(() => ({
  name: 'Model Runtime',
  role: 'Cost-Aware Intelligence',
  status: 'ACTIVE',
  tone: 'policy',
  detail: 'Haiku 4.5 (fast) / Sonnet 4.6 (deep)'
}))

const pipelineSteps = [
  { step: '01', title: 'Muse Intent', subtitle: 'Personal Agent Ingestion', desc: 'Captures explicit buyer constraints and evaluation criteria directly from personal agents.', tone: 'info', link: '/matches' },
  { step: '02', title: 'Product Truth', subtitle: 'Verified Ground Truth', desc: 'Reconciles claims against canonical product documentation and verifiable security tiering.', tone: 'good', link: '/discovery-gaps' },
  { step: '03', title: 'AI Perception', subtitle: 'Profound Answer Engine Data', desc: 'Monitors answer engines (Perplexity, ChatGPT, Claude) to catch stale citations and omissions.', tone: 'warn', link: '/discovery-gaps' },
  { step: '04', title: 'Discovery Gap', subtitle: 'Market Discrepancy', desc: 'Identifies where your product is a verified fit but AI models fail to recommend it.', tone: 'bad', link: '/discovery-gaps' },
  { step: '05', title: 'CampaignGraph', subtitle: 'Lineage & Unit Economics', desc: 'Maps actual marketing investment—human hours, agent runs, model APIs—to produced assets.', tone: 'purple', link: '/campaigns' },
  { step: '06', title: 'Experiment', subtitle: 'Closed-Loop Verification', desc: 'Applies remediation with temporal protection and evaluates post-intervention outcomes.', tone: 'policy', link: '/experiments' }
]
</script>

<template>
  <div class="homepage">
    <!-- HERO SECTION -->
    <section class="hero-section" aria-labelledby="hero-title">
      <div class="hero-content">
        <div class="hero-pill-badge">
          <span class="pulse-dot tone-info" aria-hidden="true" />
          <span>Marketing Control Plane for AI Discovery</span>
        </div>

        <h1 id="hero-title" class="hero-title">
          Understand demand.<br>
          <span class="gradient-text">Fix AI perception.</span><br>
          Measure what marketing actually produces.
        </h1>

        <p class="hero-desc">
          AgentMatch connects personal agent intent, verified product truth, Profound AI-search intelligence,
          campaign economics, and protected experiments into one live marketing control plane.
        </p>

        <div class="hero-actions">
          <NuxtLink to="/matches" class="btn primary-action">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <circle cx="12" cy="12" r="10" />
              <circle cx="12" cy="12" r="6" />
              <circle cx="12" cy="12" r="2" />
            </svg>
            Open AgentMatch
          </NuxtLink>

          <NuxtLink to="/campaigns" class="btn secondary-action">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <line x1="12" y1="1" x2="12" y2="23" />
              <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
            </svg>
            Explore Campaigns
          </NuxtLink>
        </div>

        <!-- LIVE SYSTEM STATS STRIP -->
        <div class="live-stats-strip" aria-label="System Metrics">
          <NuxtLink to="/matches" class="stat-card interactive">
            <span class="stat-label">Active Intents</span>
            <div class="stat-row">
              <span class="stat-val">{{ activeIntentsCount }}</span>
              <span class="stat-tag tone-info">Muse</span>
            </div>
            <span class="stat-sub">Personal agent demand</span>
          </NuxtLink>

          <NuxtLink to="/discovery-gaps" class="stat-card interactive">
            <span class="stat-label">Discovery Gaps</span>
            <div class="stat-row">
              <span class="stat-val text-bad">{{ discoveryGapsCount }}</span>
              <span class="stat-tag tone-bad">AI Drift</span>
            </div>
            <span class="stat-sub">Misconceptions identified</span>
          </NuxtLink>

          <NuxtLink to="/campaigns" class="stat-card interactive">
            <span class="stat-label">Tracked Campaigns</span>
            <div class="stat-row">
              <span class="stat-val">{{ campaignsCount }}</span>
              <span class="stat-tag tone-purple">Lineage</span>
            </div>
            <span class="stat-sub">Provenance-backed ledgers</span>
          </NuxtLink>

          <NuxtLink to="/experiments" class="stat-card interactive">
            <span class="stat-label">Active Experiments</span>
            <div class="stat-row">
              <span class="stat-val">{{ experimentsCount }}</span>
              <span class="stat-tag tone-warn">Bandit</span>
            </div>
            <span class="stat-sub">Protected causal learning</span>
          </NuxtLink>
        </div>
      </div>

      <!-- LIVE SYSTEM PULSE: Causal Lineage Flow -->
      <aside class="hero-visual" aria-label="AgentMatch Causal Loop">
        <div class="pulse-panel">
          <div class="pulse-header">
            <div class="pulse-title-wrap">
              <span class="pulse-dot tone-good" aria-hidden="true" />
              <span class="pulse-title">Causal Lineage Loop</span>
            </div>
            <span class="pulse-mode-badge">LIVE TOPOLOGY</span>
          </div>

          <div class="pulse-flow" role="list">
            <NuxtLink
              v-for="step in pipelineSteps"
              :key="step.step"
              :to="step.link"
              role="listitem"
              :class="['pulse-step-card', `tone-${step.tone}`]"
            >
              <div class="step-num">{{ step.step }}</div>
              <div class="step-body">
                <div class="step-head">
                  <span class="step-title">{{ step.title }}</span>
                  <span class="step-sub">{{ step.subtitle }}</span>
                </div>
                <p class="step-desc">{{ step.desc }}</p>
              </div>
            </NuxtLink>
          </div>
        </div>
      </aside>
    </section>

    <!-- FOUR CORE CAPABILITIES GRID -->
    <section class="capabilities-section" aria-labelledby="capabilities-title">
      <div class="section-header">
        <h2 id="capabilities-title" class="section-title">Operational Capabilities</h2>
        <p class="section-subtitle">
          From capturing agent demand to measuring return on marketing investment.
        </p>
      </div>

      <div class="capabilities-grid">
        <!-- CAPABILITY 1: AGENTMATCH -->
        <NuxtLink to="/matches" class="capability-card interactive">
          <div class="card-icon-wrap tone-info">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <circle cx="12" cy="12" r="10" />
              <circle cx="12" cy="6" r="2" />
              <circle cx="6" cy="15" r="2" />
              <circle cx="18" cy="15" r="2" />
              <path d="M12 8v4l-4 2" />
              <path d="M12 12l4 2" />
            </svg>
          </div>
          <div class="card-content">
            <span class="card-question">What does this agent actually need?</span>
            <h3 class="card-name">AgentMatch Demand Engine</h3>
            <p class="card-desc">
              Ingests structured personal agent intent envelopes via Muse Connector, verifies 100% of hard constraints
              against canonical product truth, and reveals why buyers match.
            </p>
            <div class="card-link-row">
              <span class="card-link-text">Open AgentMatch →</span>
            </div>
          </div>
        </NuxtLink>

        <!-- CAPABILITY 2: DISCOVERY GAPS -->
        <NuxtLink to="/discovery-gaps" class="capability-card interactive">
          <div class="card-icon-wrap tone-bad">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </div>
          <div class="card-content">
            <span class="card-question">Where does AI perception diverge from truth?</span>
            <h3 class="card-name">Discovery Gaps & AI Intelligence</h3>
            <p class="card-desc">
              Detects where AI answer engines (Perplexity, ChatGPT, Claude) report stale pricing, missing tiers,
              or wrong capabilities despite verified canonical evidence.
            </p>
            <div class="card-link-row">
              <span class="card-link-text">View Discovery Gaps →</span>
            </div>
          </div>
        </NuxtLink>

        <!-- CAPABILITY 3: CAMPAIGNGRAPH ROI -->
        <NuxtLink to="/campaigns" class="capability-card interactive">
          <div class="card-icon-wrap tone-purple">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <line x1="12" y1="1" x2="12" y2="23" />
              <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
            </svg>
          </div>
          <div class="card-content">
            <span class="card-question">Where did marketing money and work go?</span>
            <h3 class="card-name">CampaignGraph Financial Ledger</h3>
            <p class="card-desc">
              A provenance-backed ledger tracking every human contributor, autonomous agent run, video revision,
              and distribution channel to verified business outcomes.
            </p>
            <div class="card-link-row">
              <span class="card-link-text">Inspect Campaigns →</span>
            </div>
          </div>
        </NuxtLink>

        <!-- CAPABILITY 4: EXPERIMENTS -->
        <NuxtLink to="/experiments" class="capability-card interactive">
          <div class="card-icon-wrap tone-warn">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path d="M10 2v7.31M14 2v7.31M8.5 2h7M14 9.3a6.5 6.5 0 1 1-4 0" />
              <path d="M5.52 16h12.96" />
            </svg>
          </div>
          <div class="card-content">
            <span class="card-question">Which interventions are actually learning?</span>
            <h3 class="card-name">Closed-Loop Experiments</h3>
            <p class="card-desc">
              Enforces a 9-step causal spine with temporal observation windows and contextual bandit reinforcement learning
              to discover which remediations reliably work.
            </p>
            <div class="card-link-row">
              <span class="card-link-text">Run Experiments →</span>
            </div>
          </div>
        </NuxtLink>
      </div>
    </section>

    <!-- INTEGRATION STRIP -->
    <section class="integrations-section" aria-labelledby="integrations-title">
      <div class="section-header">
        <h2 id="integrations-title" class="section-title">Integration & Substrate Telemetry</h2>
        <p class="section-subtitle">Real-time connectivity across perception, graph, demand, and inference backbones.</p>
      </div>

      <div class="integrations-grid">
        <div class="integration-item">
          <div class="integ-header">
            <span :class="['pulse-dot', `tone-${museStatus.tone}`]" aria-hidden="true" />
            <span class="integ-name">{{ museStatus.name }}</span>
            <span :class="['integ-badge', `tone-${museStatus.tone}`]">{{ museStatus.status }}</span>
          </div>
          <span class="integ-role">{{ museStatus.role }}</span>
          <p class="integ-detail">{{ museStatus.detail }}</p>
        </div>

        <div class="integration-item">
          <div class="integ-header">
            <span :class="['pulse-dot', `tone-${profoundStatus.tone}`]" aria-hidden="true" />
            <span class="integ-name">{{ profoundStatus.name }}</span>
            <span :class="['integ-badge', `tone-${profoundStatus.tone}`]">{{ profoundStatus.status }}</span>
          </div>
          <span class="integ-role">{{ profoundStatus.role }}</span>
          <p class="integ-detail">{{ profoundStatus.detail }}</p>
        </div>

        <div class="integration-item">
          <div class="integ-header">
            <span :class="['pulse-dot', `tone-${neo4jStatus.tone}`]" aria-hidden="true" />
            <span class="integ-name">{{ neo4jStatus.name }}</span>
            <span :class="['integ-badge', `tone-${neo4jStatus.tone}`]">{{ neo4jStatus.status }}</span>
          </div>
          <span class="integ-role">{{ neo4jStatus.role }}</span>
          <p class="integ-detail">{{ neo4jStatus.detail }}</p>
        </div>

        <div class="integration-item">
          <div class="integ-header">
            <span :class="['pulse-dot', `tone-${modelStatus.tone}`]" aria-hidden="true" />
            <span class="integ-name">{{ modelStatus.name }}</span>
            <span :class="['integ-badge', `tone-${modelStatus.tone}`]">{{ modelStatus.status }}</span>
          </div>
          <span class="integ-role">{{ modelStatus.role }}</span>
          <p class="integ-detail">{{ modelStatus.detail }}</p>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.homepage {
  display: flex;
  flex-direction: column;
  gap: 48px;
  padding: 32px 40px 64px;
  max-width: 1440px;
  margin: 0 auto;
  width: 100%;
}

/* HERO SECTION */
.hero-section {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(360px, 0.95fr);
  gap: 40px;
  align-items: start;
}

@media (max-width: 1100px) {
  .hero-section {
    grid-template-columns: 1fr;
  }
}

.hero-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.hero-pill-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  border-radius: 999px;
  background: rgba(56, 189, 248, 0.1);
  border: 1px solid rgba(56, 189, 248, 0.25);
  color: #38bdf8;
  font-size: 12px;
  font-weight: 600;
  width: fit-content;
}

.hero-title {
  font-size: 38px;
  line-height: 1.15;
  font-weight: 800;
  letter-spacing: -0.03em;
  color: #f8fafc;
}

.gradient-text {
  background: linear-gradient(135deg, #38bdf8 0%, #818cf8 50%, #c084fc 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.hero-desc {
  font-size: 15px;
  line-height: 1.6;
  color: #94a3b8;
  max-width: 620px;
}

.hero-actions {
  display: flex;
  gap: 14px;
  align-items: center;
  margin-top: 4px;
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  text-decoration: none;
  transition: all 0.18s ease;
}

.primary-action {
  background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
  color: #ffffff;
  border: 1px solid rgba(255, 255, 255, 0.15);
  box-shadow: 0 4px 20px rgba(99, 102, 241, 0.35);
}
.primary-action:hover {
  background: linear-gradient(135deg, #4338ca 0%, #4f46e5 100%);
  box-shadow: 0 6px 24px rgba(99, 102, 241, 0.45);
  transform: translateY(-1px);
}

.secondary-action {
  background: rgba(15, 21, 38, 0.8);
  border: 1px solid var(--border);
  color: #f1f5f9;
}
.secondary-action:hover {
  background: rgba(23, 32, 56, 0.9);
  border-color: var(--border-strong);
  color: #ffffff;
  transform: translateY(-1px);
}

/* LIVE STATS STRIP */
.live-stats-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-top: 16px;
}

@media (max-width: 768px) {
  .live-stats-strip {
    grid-template-columns: repeat(2, 1fr);
  }
}

.stat-card {
  background: rgba(13, 19, 34, 0.7);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-decoration: none;
  transition: all 0.15s ease;
}
.stat-card:hover {
  background: rgba(20, 28, 50, 0.85);
  border-color: rgba(99, 102, 241, 0.4);
  transform: translateY(-1px);
}

.stat-label {
  font-size: 11px;
  color: #64748b;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.stat-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.stat-val {
  font-size: 22px;
  font-weight: 700;
  color: #f8fafc;
  font-family: var(--font-mono, monospace);
}

.stat-tag {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 4px;
  text-transform: uppercase;
}
.stat-tag.tone-info { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
.stat-tag.tone-bad { background: rgba(244, 63, 94, 0.15); color: #fda4af; }
.stat-tag.tone-purple { background: rgba(168, 85, 247, 0.15); color: #c084fc; }
.stat-tag.tone-warn { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }

.stat-sub {
  font-size: 11px;
  color: #94a3b8;
}

/* HERO VISUAL / TOPOLOGY PULSE */
.hero-visual {
  min-width: 0;
}

.pulse-panel {
  background: rgba(10, 14, 26, 0.85);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.4);
}

.pulse-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  padding-bottom: 10px;
}

.pulse-title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pulse-title {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #f1f5f9;
}

.pulse-mode-badge {
  font-size: 10px;
  font-family: var(--font-mono, monospace);
  color: #34d399;
  font-weight: 700;
  background: rgba(16, 185, 129, 0.12);
  padding: 2px 7px;
  border-radius: 4px;
}

.pulse-flow {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pulse-step-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  background: rgba(15, 21, 38, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  padding: 10px 12px;
  text-decoration: none;
  transition: all 0.15s ease;
}
.pulse-step-card:hover {
  background: rgba(23, 32, 56, 0.8);
  border-color: rgba(99, 102, 241, 0.35);
  transform: translateX(2px);
}

.step-num {
  font-size: 11px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  color: #64748b;
  padding-top: 1px;
}

.step-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.step-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.step-title {
  font-size: 13px;
  font-weight: 700;
  color: #f8fafc;
}

.step-sub {
  font-size: 11px;
  color: #64748b;
}

.step-desc {
  font-size: 11px;
  color: #94a3b8;
  line-height: 1.4;
}

/* CAPABILITIES SECTION */
.capabilities-section, .integrations-section {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.section-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.section-title {
  font-size: 18px;
  font-weight: 700;
  color: #f8fafc;
  letter-spacing: -0.01em;
}

.section-subtitle {
  font-size: 13px;
  color: #94a3b8;
}

.capabilities-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px;
}

@media (max-width: 860px) {
  .capabilities-grid {
    grid-template-columns: 1fr;
  }
}

.capability-card {
  background: rgba(13, 19, 34, 0.7);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 22px;
  display: flex;
  gap: 16px;
  text-decoration: none;
  transition: all 0.2s ease;
}
.capability-card:hover {
  background: rgba(20, 28, 50, 0.85);
  border-color: rgba(99, 102, 241, 0.4);
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
}

.card-icon-wrap {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
}
.card-icon-wrap.tone-info { color: #38bdf8; background: rgba(56, 189, 248, 0.1); border-color: rgba(56, 189, 248, 0.25); }
.card-icon-wrap.tone-bad { color: #fda4af; background: rgba(244, 63, 94, 0.1); border-color: rgba(244, 63, 94, 0.25); }
.card-icon-wrap.tone-purple { color: #c084fc; background: rgba(168, 85, 247, 0.1); border-color: rgba(168, 85, 247, 0.25); }
.card-icon-wrap.tone-warn { color: #fbbf24; background: rgba(245, 158, 11, 0.1); border-color: rgba(245, 158, 11, 0.25); }

.card-content {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.card-question {
  font-size: 11px;
  font-weight: 600;
  color: #38bdf8;
  font-family: var(--font-mono, monospace);
  text-transform: uppercase;
}

.card-name {
  font-size: 16px;
  font-weight: 700;
  color: #ffffff;
}

.card-desc {
  font-size: 13px;
  color: #94a3b8;
  line-height: 1.5;
}

.card-link-row {
  margin-top: 8px;
}

.card-link-text {
  font-size: 12px;
  font-weight: 600;
  color: #818cf8;
}

/* INTEGRATION STRIP */
.integrations-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}

@media (max-width: 900px) {
  .integrations-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
@media (max-width: 540px) {
  .integrations-grid {
    grid-template-columns: 1fr;
  }
}

.integration-item {
  background: rgba(13, 19, 34, 0.6);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.integ-header {
  display: flex;
  align-items: center;
  gap: 6px;
}

.integ-name {
  font-size: 13px;
  font-weight: 700;
  color: #f8fafc;
  flex: 1;
}

.integ-badge {
  font-size: 9px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
  padding: 2px 6px;
  border-radius: 4px;
}
.integ-badge.tone-info { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
.integ-badge.tone-good { background: rgba(16, 185, 129, 0.15); color: #34d399; }
.integ-badge.tone-warn { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.integ-badge.tone-policy { background: rgba(168, 85, 247, 0.15); color: #c084fc; }
.integ-badge.tone-muted { background: rgba(100, 116, 139, 0.15); color: #94a3b8; }

.integ-role {
  font-size: 11px;
  color: #64748b;
}

.integ-detail {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 4px;
}

/* PULSE DOT */
.pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.pulse-dot.tone-info { background: #38bdf8; box-shadow: 0 0 8px rgba(56, 189, 248, 0.5); }
.pulse-dot.tone-good { background: #10b981; box-shadow: 0 0 8px rgba(16, 185, 129, 0.5); }
.pulse-dot.tone-warn { background: #f59e0b; box-shadow: 0 0 8px rgba(245, 158, 11, 0.5); }
.pulse-dot.tone-bad { background: #f43f5e; box-shadow: 0 0 8px rgba(244, 63, 94, 0.5); }
.pulse-dot.tone-policy { background: #a855f7; box-shadow: 0 0 8px rgba(168, 85, 247, 0.5); }
</style>
