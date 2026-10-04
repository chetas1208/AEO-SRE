<script setup lang="ts">
import { computed } from 'vue'
import type { AgentActivity, CampaignFinancialCard, ControlPlaneResponse } from '~/types'

const props = withDefaults(
  defineProps<{
    data: ControlPlaneResponse | null
    loading?: boolean
  }>(),
  { loading: false }
)

const emit = defineEmits<{
  (e: 'select-campaign', id: string): void
  (e: 'select-agent', agent: AgentActivity): void
}>()

function num(v: unknown): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function campaignCost(c: CampaignFinancialCard): number {
  return num(c.totalCost ?? c.total_cost)
}

function campaignReturn(c: CampaignFinancialCard): number | null {
  const r = c.attributedReturn ?? c.attributed_return
  return r == null ? null : num(r)
}

function campaignNet(c: CampaignFinancialCard): number | null {
  const n = c.netReturn ?? c.net_return
  if (n != null) return num(n)
  const ret = campaignReturn(c)
  if (ret == null) return null
  return ret - campaignCost(c)
}

function campaignRoi(c: CampaignFinancialCard): number | null {
  const roi = c.roiPct ?? c.roi_pct
  if (roi != null) return num(roi)
  const cost = campaignCost(c)
  const net = campaignNet(c)
  if (cost <= 0 || net == null) return null
  return (net / cost) * 100
}

const summary = computed(() => {
  const s = props.data?.summary
  return {
    activeAgents: num(s?.activeAgents ?? s?.active_agents),
    runningCampaigns: num(s?.runningCampaigns ?? s?.running_campaigns),
    modelCostToday: num(s?.modelCostToday ?? s?.model_cost_today),
    attributedReturn: num(s?.attributedReturn ?? s?.attributed_return),
    decisionsNeedingReview: num(s?.decisionsNeedingReview ?? s?.decisions_needing_review),
    experimentsMeasuring: num(s?.experimentsMeasuring ?? s?.experiments_measuring),
    totalAgentRuns: num(s?.totalAgentRuns ?? s?.total_agent_runs),
    totalSpend: num(s?.totalSpend ?? s?.total_spend),
    netReturn: s?.netReturn ?? s?.net_return,
    blendedRoiPct: s?.blendedRoiPct ?? s?.blended_roi_pct,
    decisionCostTotal: num(s?.decisionCostTotal ?? s?.decision_cost_total),
  }
})

const campaigns = computed(() => props.data?.campaigns ?? [])
const agents = computed(() => props.data?.agents ?? [])
const decisions = computed(() => props.data?.decisions ?? [])
const experiments = computed(() => props.data?.experiments ?? [])

const totals = computed(() => {
  const s = props.data?.summary
  const spendFromSummary = num(s?.totalSpend ?? s?.total_spend)
  const spend =
    spendFromSummary > 0
      ? spendFromSummary
      : campaigns.value.reduce((acc, c) => acc + campaignCost(c), 0)
  const returnsFromSummary = s?.attributedReturn ?? s?.attributed_return
  const returnsAgg = campaigns.value.reduce((acc, c) => {
    const r = campaignReturn(c)
    return r == null ? acc : acc + r
  }, 0)
  const hasReturn = campaigns.value.some(c => campaignReturn(c) != null) || returnsFromSummary != null
  const returns = hasReturn ? num(returnsFromSummary ?? returnsAgg) : null
  const agentModel = agents.value.reduce((acc, a) => acc + num(a.modelCost ?? a.model_cost), 0)
  const agentTotal = agents.value.reduce((acc, a) => acc + num(a.totalCost ?? a.total_cost), 0)
  const runs = agents.value.reduce((acc, a) => acc + num(a.runs), 0)
  const outputs = agents.value.reduce((acc, a) => acc + num(a.outputsProduced ?? a.outputs_produced), 0)
  const netSummary = s?.netReturn ?? s?.net_return
  const roiSummary = s?.blendedRoiPct ?? s?.blended_roi_pct
  const net = netSummary != null ? num(netSummary) : hasReturn && returns != null ? returns - spend : null
  const roiPct =
    roiSummary != null
      ? num(roiSummary)
      : spend > 0 && net != null
        ? (net / spend) * 100
        : null
  const returnShare = spend + returns > 0 && hasReturn ? returns / (spend + returns) : 0.5

  return {
    spend,
    returns: hasReturn ? returns : null,
    net,
    roiPct,
    agentModel,
    agentTotal,
    runs,
    outputs,
    returnShare,
    decisionCost: decisions.value.reduce((acc, d) => acc + num(d.cost), 0),
  }
})

const topCampaigns = computed(() =>
  [...campaigns.value].sort((a, b) => campaignCost(b) - campaignCost(a))
)

const topAgentsByCost = computed(() =>
  [...agents.value].sort((a, b) => num(b.modelCost ?? b.model_cost) - num(a.modelCost ?? a.model_cost))
)

const pendingDecisions = computed(() =>
  decisions.value.filter(d => d.status === 'PENDING_REVIEW').slice(0, 5)
)

const activeExperiments = computed(() => experiments.value.slice(0, 5))

const generatedLabel = computed(() => {
  const raw = props.data?.generatedAt ?? props.data?.generated_at
  if (!raw) return '—'
  try {
    return new Date(raw).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  } catch {
    return raw
  }
})

const provenance = computed(() => {
  if (props.data?.dataProvenance === 'FIXTURE' || props.data?.data_provenance === 'FIXTURE') return 'Fixture demo'
  if (props.data?.sourceMode === 'TEST' || props.data?.source_mode === 'TEST') return 'Test scenario'
  return 'Live ledger'
})

function fmtMoney(v: number | null | undefined, opts?: { cents?: boolean }): string {
  if (v == null || Number.isNaN(v)) return '—'
  if (opts?.cents) return `$${v.toFixed(2)}`
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`
  if (Math.abs(v) >= 10_000) return `$${(v / 1_000).toFixed(1)}K`
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

function fmtRoi(v: number | null): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(1)}%`
}

function finStatusClass(c: CampaignFinancialCard): string {
  const st = c.financialStatus ?? c.financial_status ?? 'NOT_MEASURABLE'
  return `fin-${st.toLowerCase()}`
}

function agentCost(a: AgentActivity): number {
  return num(a.modelCost ?? a.model_cost)
}
</script>

<template>
  <section class="flight-deck" aria-label="Control plane financial and operations overview">
    <div class="deck-head">
      <div>
        <h2 class="deck-title">Flight deck</h2>
        <p class="deck-meta">
          {{ provenance }} · Updated {{ generatedLabel }}
          <span v-if="loading" class="deck-sync">· refreshing</span>
        </p>
      </div>
      <div class="deck-head-stats">
        <span class="head-chip">{{ summary.runningCampaigns }} campaigns</span>
        <span class="head-chip">{{ agents.length }} agents</span>
        <span class="head-chip">{{ summary.experimentsMeasuring }} measuring</span>
      </div>
    </div>

    <!-- Hero KPIs -->
    <div class="kpi-grid">
      <article class="kpi-card kpi-spend">
        <span class="kpi-label">Total spend</span>
        <p class="kpi-value">{{ fmtMoney(totals.spend) }}</p>
        <p class="kpi-sub">Across {{ campaigns.length }} initiative{{ campaigns.length === 1 ? '' : 's' }}</p>
      </article>

      <article class="kpi-card kpi-return">
        <span class="kpi-label">Attributed return</span>
        <p class="kpi-value tone-good">{{ fmtMoney(totals.returns ?? summary.attributedReturn) }}</p>
        <p class="kpi-sub">Verified &amp; attributed outcomes</p>
      </article>

      <article class="kpi-card kpi-net" :class="{ negative: totals.net != null && totals.net < 0 }">
        <span class="kpi-label">Net margin</span>
        <p class="kpi-value">{{ fmtMoney(totals.net) }}</p>
        <p class="kpi-sub">Return minus fully loaded spend</p>
      </article>

      <article class="kpi-card kpi-roi">
        <span class="kpi-label">Blended ROI</span>
        <p class="kpi-value">{{ fmtRoi(totals.roiPct) }}</p>
        <p class="kpi-sub">Portfolio-level efficiency</p>
      </article>

      <article class="kpi-card kpi-model">
        <span class="kpi-label">Model cost (today)</span>
        <p class="kpi-value">{{ fmtMoney(summary.modelCostToday, { cents: true }) }}</p>
        <p class="kpi-sub">
          Agents ledger {{ fmtMoney(totals.agentModel, { cents: true }) }}
          <span class="kpi-pill">Haiku-first</span>
        </p>
      </article>

      <article class="kpi-card kpi-runs">
        <span class="kpi-label">Agent execution</span>
        <p class="kpi-value">{{ (summary.totalAgentRuns || totals.runs).toLocaleString() }} <span class="kpi-unit">runs</span></p>
        <p class="kpi-sub">{{ totals.outputs.toLocaleString() }} outputs · {{ fmtMoney(totals.agentTotal, { cents: true }) }} loaded</p>
      </article>
    </div>

    <!-- Spend vs return bar -->
    <div v-if="totals.returns != null && totals.spend > 0" class="flow-bar-wrap">
      <div class="flow-labels">
        <span>Cost {{ fmtMoney(totals.spend) }}</span>
        <span>Return {{ fmtMoney(totals.returns) }}</span>
      </div>
      <div class="flow-bar" role="img" :aria-label="`Spend ${fmtMoney(totals.spend)} versus return ${fmtMoney(totals.returns)}`">
        <div class="flow-segment spend" :style="{ flex: 1 - totals.returnShare }" />
        <div class="flow-segment return" :style="{ flex: totals.returnShare }" />
      </div>
    </div>

    <!-- Detail panels -->
    <div class="panel-grid">
      <article class="panel">
        <header class="panel-header">
          <h3>Campaign economics</h3>
          <NuxtLink to="/campaigns" class="panel-link">Full ledger →</NuxtLink>
        </header>
        <div v-if="!topCampaigns.length" class="panel-empty">No campaigns yet — create one to track cost &amp; return.</div>
        <ul v-else class="ledger-list">
          <li v-for="c in topCampaigns" :key="c.id">
            <button type="button" class="ledger-row" @click="emit('select-campaign', c.id)">
              <div class="ledger-main">
                <span class="ledger-name">{{ c.name }}</span>
                <span class="ledger-channel">{{ c.primaryChannel ?? c.primary_channel ?? 'Multi-channel' }}</span>
              </div>
              <div class="ledger-nums">
                <span class="ledger-spend">{{ fmtMoney(campaignCost(c)) }}</span>
                <span class="ledger-ret">{{ fmtMoney(campaignReturn(c)) }}</span>
                <span :class="['ledger-badge', finStatusClass(c)]">{{ fmtRoi(campaignRoi(c)) }}</span>
              </div>
              <div class="ledger-foot">
                <span>{{ c.activeAgentsCount ?? c.active_agents_count ?? 0 }} agents</span>
                <span>{{ c.activeExperimentsCount ?? c.active_experiments_count ?? 0 }} experiments</span>
                <span :class="['conf', (c.measurementConfidence ?? c.measurement_confidence ?? 'MEDIUM').toLowerCase()]">
                  {{ c.measurementConfidence ?? c.measurement_confidence }} conf
                </span>
              </div>
            </button>
          </li>
        </ul>
      </article>

      <article class="panel">
        <header class="panel-header">
          <h3>Agent cost &amp; throughput</h3>
        </header>
        <div v-if="!topAgentsByCost.length" class="panel-empty">Profound agents appear here when runs are live.</div>
        <ul v-else class="agent-cost-list">
          <li v-for="a in topAgentsByCost" :key="a.id">
            <button type="button" class="agent-cost-row" @click="emit('select-agent', a)">
              <div class="ac-top">
                <span class="ac-name">{{ a.name }}</span>
                <span class="ac-cost">{{ fmtMoney(agentCost(a), { cents: true }) }}</span>
              </div>
              <div class="ac-meta">
                <span>{{ a.state }}</span>
                <span>{{ a.runs }} runs</span>
                <span>{{ a.outputsProduced ?? a.outputs_produced ?? 0 }} out</span>
              </div>
            </button>
          </li>
        </ul>
      </article>

      <article class="panel">
        <header class="panel-header">
          <h3>Decisions &amp; experiments</h3>
        </header>
        <div class="ops-block">
          <div class="ops-summary">
            <div class="ops-stat">
              <span class="ops-num tone-warn">{{ summary.decisionsNeedingReview }}</span>
              <span class="ops-lbl">reviews pending</span>
            </div>
            <div class="ops-stat">
              <span class="ops-num">{{ fmtMoney(totals.decisionCost, { cents: true }) }}</span>
              <span class="ops-lbl">decision cost est.</span>
            </div>
          </div>
          <ul v-if="pendingDecisions.length" class="mini-queue">
            <li v-for="d in pendingDecisions" :key="d.id">
              <span class="mq-title">{{ d.title.slice(0, 72) }}{{ d.title.length > 72 ? '…' : '' }}</span>
              <span class="mq-meta">{{ d.recommendedBy ?? d.recommended_by ?? 'System' }}</span>
            </li>
          </ul>
          <p v-else class="panel-empty inline">No pending Laya / Change Guard reviews.</p>
        </div>
        <div class="ops-block experiments-block">
          <h4 class="ops-subhead">Experiments</h4>
          <ul v-if="activeExperiments.length" class="mini-queue">
            <li v-for="e in activeExperiments" :key="e.id">
              <NuxtLink :to="`/experiments/${e.id}`" class="exp-link">
                <span class="mq-code">{{ e.code }}</span>
                <span class="mq-meta">{{ e.status }} · {{ e.primaryMetric ?? e.primary_metric }}</span>
                <span v-if="e.protectionActive ?? e.protection_active" class="mq-guard">Change Guard</span>
              </NuxtLink>
            </li>
          </ul>
          <p v-else class="panel-empty inline">No experiments in flight.</p>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.flight-deck {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px 20px 20px;
  border-radius: var(--radius-lg, 14px);
  border: 1px solid var(--border);
  background:
    radial-gradient(ellipse 120% 80% at 10% -20%, rgba(79, 70, 229, 0.12), transparent 55%),
    radial-gradient(ellipse 80% 60% at 90% 0%, rgba(16, 185, 129, 0.08), transparent 50%),
    rgba(8, 12, 22, 0.92);
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.35);
}

.deck-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.deck-title {
  margin: 0;
  font-size: 15px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: #f8fafc;
}

.deck-meta {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-dim);
}

.deck-sync {
  color: #38bdf8;
}

.deck-head-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.head-chip {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--border-subtle);
  color: #94a3b8;
  background: rgba(15, 21, 38, 0.6);
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
}

@media (max-width: 1400px) {
  .kpi-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.kpi-card {
  padding: 14px 14px 12px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(13, 19, 34, 0.65);
  min-height: 96px;
}

.kpi-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #64748b;
}

.kpi-value {
  margin: 6px 0 0;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.03em;
  color: #f1f5f9;
  line-height: 1.1;
}

.kpi-unit {
  font-size: 13px;
  font-weight: 600;
  color: #94a3b8;
}

.kpi-sub {
  margin: 6px 0 0;
  font-size: 10px;
  line-height: 1.35;
  color: #64748b;
}

.kpi-pill {
  display: inline-block;
  margin-left: 4px;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 9px;
  font-weight: 700;
  background: rgba(168, 85, 247, 0.2);
  color: #c084fc;
}

.kpi-spend { border-color: rgba(245, 158, 11, 0.25); }
.kpi-return { border-color: rgba(16, 185, 129, 0.3); }
.kpi-net.negative .kpi-value { color: #f87171; }
.kpi-net:not(.negative) .kpi-value { color: #34d399; }
.kpi-roi .kpi-value { color: #818cf8; }
.kpi-model .kpi-value { color: #c084fc; }

.tone-good { color: #34d399 !important; }

.flow-bar-wrap {
  padding: 0 2px;
}

.flow-labels {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  font-weight: 600;
  color: #64748b;
  margin-bottom: 6px;
}

.flow-bar {
  display: flex;
  height: 8px;
  border-radius: 999px;
  overflow: hidden;
  background: rgba(15, 21, 38, 0.8);
  border: 1px solid var(--border-subtle);
}

.flow-segment.spend {
  background: linear-gradient(90deg, #f59e0b, #d97706);
}
.flow-segment.return {
  background: linear-gradient(90deg, #10b981, #34d399);
}

.panel-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr 1fr;
  gap: 12px;
}

@media (max-width: 1200px) {
  .panel-grid {
    grid-template-columns: 1fr;
  }
}

.panel {
  border-radius: 12px;
  border: 1px solid var(--border-subtle);
  background: rgba(10, 14, 26, 0.75);
  padding: 12px 14px 14px;
  min-height: 200px;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.panel-header h3 {
  margin: 0;
  font-size: 12px;
  font-weight: 700;
  color: #e2e8f0;
}

.panel-link {
  font-size: 10px;
  font-weight: 600;
  color: #38bdf8;
  text-decoration: none;
}
.panel-link:hover {
  text-decoration: underline;
}

.panel-empty {
  font-size: 11px;
  color: #64748b;
  line-height: 1.45;
  padding: 8px 0;
}
.panel-empty.inline {
  padding: 4px 0;
}

.ledger-list,
.agent-cost-list,
.mini-queue {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 240px;
  overflow-y: auto;
}

.ledger-row,
.agent-cost-row {
  width: 100%;
  text-align: left;
  padding: 10px 10px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: rgba(15, 21, 38, 0.5);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.ledger-row:hover,
.agent-cost-row:hover {
  border-color: rgba(99, 102, 241, 0.4);
  background: rgba(79, 70, 229, 0.08);
}

.ledger-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ledger-name {
  font-size: 12px;
  font-weight: 700;
  color: #f1f5f9;
}

.ledger-channel {
  font-size: 10px;
  color: #64748b;
}

.ledger-nums {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.ledger-spend { color: #fbbf24; }
.ledger-ret { color: #34d399; }

.ledger-badge {
  margin-left: auto;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
}
.fin-positive { background: rgba(16, 185, 129, 0.2); color: #34d399; }
.fin-negative { background: rgba(239, 68, 68, 0.2); color: #f87171; }
.fin-uncertain { background: rgba(148, 163, 184, 0.2); color: #94a3b8; }
.fin-not_measurable { background: rgba(100, 116, 139, 0.2); color: #64748b; }

.ledger-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
  font-size: 9px;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.conf.high { color: #34d399; }
.conf.medium { color: #fbbf24; }
.conf.low { color: #94a3b8; }

.ac-top {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.ac-name {
  font-size: 11px;
  font-weight: 700;
  color: #e2e8f0;
}

.ac-cost {
  font-size: 11px;
  font-weight: 800;
  color: #c084fc;
  font-variant-numeric: tabular-nums;
}

.ac-meta {
  display: flex;
  gap: 10px;
  margin-top: 4px;
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #64748b;
}

.ops-block {
  margin-bottom: 12px;
}

.ops-summary {
  display: flex;
  gap: 16px;
  margin-bottom: 10px;
}

.ops-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ops-num {
  font-size: 18px;
  font-weight: 800;
  color: #f1f5f9;
}

.ops-lbl {
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
}

.tone-warn { color: #fbbf24 !important; }

.ops-subhead {
  margin: 0 0 8px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #64748b;
}

.mq-title {
  display: block;
  font-size: 11px;
  font-weight: 600;
  color: #cbd5e1;
}

.mq-meta {
  font-size: 9px;
  color: #64748b;
}

.mq-code {
  font-size: 11px;
  font-weight: 700;
  color: #38bdf8;
}

.mq-guard {
  font-size: 8px;
  font-weight: 700;
  color: #34d399;
  text-transform: uppercase;
}

.exp-link {
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-decoration: none;
  padding: 6px 8px;
  border-radius: 6px;
  transition: background 0.15s;
}
.exp-link:hover {
  background: rgba(56, 189, 248, 0.08);
}
</style>
