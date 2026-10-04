<script setup lang="ts">
import { computed } from 'vue'
import type { ControlPlaneResponse } from '~/types'

const props = defineProps<{
  data: ControlPlaneResponse | null
  graphNodeCount?: number
  graphEdgeCount?: number
}>()

function n(v: unknown): number {
  const x = Number(v)
  return Number.isFinite(x) ? x : 0
}

const items = computed(() => {
  const s = props.data?.summary
  const agents = props.data?.agents ?? []
  const outputs = agents.reduce((acc, a) => acc + n(a.outputsProduced ?? a.outputs_produced), 0)
  const accepted = agents.reduce((acc, a) => acc + n(a.outputsAccepted ?? a.outputs_accepted), 0)
  const agentModel = agents.reduce((acc, a) => acc + n(a.modelCost ?? a.model_cost), 0)

  return [
    { label: 'Active agents', value: String(n(s?.activeAgents ?? s?.active_agents)), tone: 'cyan' },
    { label: 'Agents listed', value: String(agents.length), tone: 'cyan' },
    { label: 'Campaigns running', value: String(n(s?.runningCampaigns ?? s?.running_campaigns)), tone: 'indigo' },
    { label: 'Total spend', value: fmtMoney(n(s?.totalSpend ?? s?.total_spend)), tone: 'amber' },
    { label: 'Attributed return', value: fmtMoney(n(s?.attributedReturn ?? s?.attributed_return)), tone: 'good' },
    { label: 'Net margin', value: fmtMoney(s?.netReturn ?? s?.net_return), tone: 'good' },
    { label: 'Blended ROI', value: fmtPct(s?.blendedRoiPct ?? s?.blended_roi_pct), tone: 'violet' },
    { label: 'Model cost (today)', value: fmtMoney(n(s?.modelCostToday ?? s?.model_cost_today), true), tone: 'purple' },
    { label: 'Agent model Σ', value: fmtMoney(agentModel, true), tone: 'purple' },
    { label: 'Agent runs', value: String(n(s?.totalAgentRuns ?? s?.total_agent_runs)), tone: 'sky' },
    { label: 'Outputs', value: String(outputs), tone: 'sky' },
    { label: 'Accepted', value: String(accepted), tone: 'sky' },
    { label: 'Reviews pending', value: String(n(s?.decisionsNeedingReview ?? s?.decisions_needing_review)), tone: 'warn' },
    { label: 'Decision cost Σ', value: fmtMoney(n(s?.decisionCostTotal ?? s?.decision_cost_total), true), tone: 'muted' },
    { label: 'Experiments measuring', value: String(n(s?.experimentsMeasuring ?? s?.experiments_measuring)), tone: 'teal' },
    { label: 'Decisions', value: String(props.data?.decisions?.length ?? 0), tone: 'muted' },
    { label: 'Experiments', value: String(props.data?.experiments?.length ?? 0), tone: 'muted' },
    { label: 'Graph nodes', value: String(props.graphNodeCount ?? 0), tone: 'muted' },
    { label: 'Graph edges', value: String(props.graphEdgeCount ?? 0), tone: 'muted' },
  ]
})

function fmtMoney(v: unknown, cents = false): string {
  if (v == null || v === '') return '—'
  const x = Number(v)
  if (!Number.isFinite(x)) return '—'
  if (cents) return `$${x.toFixed(2)}`
  if (Math.abs(x) >= 10_000) return `$${(x / 1000).toFixed(1)}K`
  return `$${x.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

function fmtPct(v: unknown): string {
  if (v == null || v === '') return '—'
  const x = Number(v)
  if (!Number.isFinite(x)) return '—'
  return `${x >= 0 ? '+' : ''}${x.toFixed(1)}%`
}
</script>

<template>
  <section class="ops-strip" aria-label="Control plane metrics">
    <article v-for="(m, i) in items" :key="i" :class="['ops-cell', `tone-${m.tone}`]">
      <span class="ops-label">{{ m.label }}</span>
      <span class="ops-value">{{ m.value }}</span>
    </article>
  </section>
</template>

<style scoped>
.ops-strip {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));
  gap: 8px;
  padding: 12px 14px;
  border-radius: var(--radius-lg, 14px);
  border: 1px solid var(--border);
  background: rgba(10, 14, 26, 0.85);
}

.ops-cell {
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.05);
  background: rgba(15, 21, 38, 0.55);
  min-width: 0;
}

.ops-label {
  display: block;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: #64748b;
  line-height: 1.2;
}

.ops-value {
  display: block;
  margin-top: 4px;
  font-size: 15px;
  font-weight: 800;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  color: #f1f5f9;
  line-height: 1.1;
}

.tone-good .ops-value { color: #34d399; }
.tone-cyan .ops-value { color: #38bdf8; }
.tone-indigo .ops-value { color: #818cf8; }
.tone-amber .ops-value { color: #fbbf24; }
.tone-violet .ops-value { color: #a78bfa; }
.tone-purple .ops-value { color: #c084fc; }
.tone-sky .ops-value { color: #7dd3fc; }
.tone-warn .ops-value { color: #fbbf24; }
.tone-teal .ops-value { color: #2dd4bf; }
.tone-muted .ops-value { color: #cbd5e1; }
</style>
