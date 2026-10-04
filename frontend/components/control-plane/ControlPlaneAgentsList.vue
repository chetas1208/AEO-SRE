<script setup lang="ts">
import { computed } from 'vue'
import type { AgentActivity } from '~/types'

const props = withDefaults(
  defineProps<{
    agents: AgentActivity[]
    selectedAgentId?: string | null
    loading?: boolean
  }>(),
  {
    selectedAgentId: null,
    loading: false,
  }
)

const emit = defineEmits<{
  (e: 'select', agent: AgentActivity): void
}>()

const sortedAgents = computed(() => {
  const order: Record<string, number> = {
    RUNNING: 0,
    REVIEW: 1,
    WAITING: 2,
    BLOCKED: 3,
    FAILED: 4,
    COMPLETED: 5,
  }
  return [...props.agents].sort((a, b) => {
    const oa = order[a.state] ?? 9
    const ob = order[b.state] ?? 9
    if (oa !== ob) return oa - ob
    return (a.name || '').localeCompare(b.name || '')
  })
})

function campaignOf(a: AgentActivity): string {
  return a.campaignName ?? a.campaign_name ?? '—'
}

function stateClass(state: AgentActivity['state']): string {
  switch (state) {
    case 'RUNNING':
      return 'state-running'
    case 'REVIEW':
      return 'state-review'
    case 'WAITING':
      return 'state-waiting'
    case 'BLOCKED':
    case 'FAILED':
      return 'state-bad'
    case 'COMPLETED':
      return 'state-done'
    default:
      return 'state-idle'
  }
}

function num(a: AgentActivity, ...keys: (keyof AgentActivity)[]): number {
  for (const k of keys) {
    const v = a[k]
    if (v != null && v !== '') return Number(v) || 0
  }
  return 0
}

function money(v: number): string {
  return `$${v.toFixed(2)}`
}
</script>

<template>
  <aside class="agents-panel" aria-label="Active agents ledger">
    <header class="panel-header">
      <div class="header-row">
        <h2 class="panel-title">Agent ledger</h2>
        <span class="count-pill">{{ agents.length }}</span>
      </div>
      <p class="panel-sub">All runs, costs, and outputs — select a row to highlight on the graph</p>
    </header>

    <div v-if="loading && !agents.length" class="panel-empty">Loading agents…</div>
    <div v-else-if="!agents.length" class="panel-empty">
      No agents in the live registry yet.
    </div>

    <div v-else class="table-wrap">
      <table class="agent-table">
        <thead>
          <tr>
            <th scope="col">Agent</th>
            <th scope="col">State</th>
            <th scope="col" class="num">Runs</th>
            <th scope="col" class="num">Model $</th>
            <th scope="col" class="num">Total $</th>
            <th scope="col" class="num">Out</th>
            <th scope="col" class="num">Acc</th>
            <th scope="col">Campaign</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="agent in sortedAgents"
            :key="agent.id"
            :class="{ selected: selectedAgentId === agent.id }"
            tabindex="0"
            role="button"
            @click="emit('select', agent)"
            @keydown.enter="emit('select', agent)"
          >
            <td class="name-cell">
              <span class="agent-name" :title="agent.name">{{ agent.name }}</span>
            </td>
            <td>
              <span :class="['state-badge', stateClass(agent.state)]">{{ agent.state }}</span>
            </td>
            <td class="num">{{ num(agent, 'runs') }}</td>
            <td class="num cost">{{ money(num(agent, 'modelCost', 'model_cost')) }}</td>
            <td class="num cost">{{ money(num(agent, 'totalCost', 'total_cost')) }}</td>
            <td class="num">{{ num(agent, 'outputsProduced', 'outputs_produced') }}</td>
            <td class="num">{{ num(agent, 'outputsAccepted', 'outputs_accepted') }}</td>
            <td class="camp-cell" :title="campaignOf(agent)">{{ campaignOf(agent) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </aside>
</template>

<style scoped>
.agents-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 480px;
  max-height: min(62vh, 720px);
  background: rgba(13, 19, 34, 0.92);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 14px);
  overflow: hidden;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.35);
}

.panel-header {
  padding: 12px 14px 10px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.header-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel-title {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  color: #f8fafc;
}

.count-pill {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(56, 189, 248, 0.15);
  color: #38bdf8;
  border: 1px solid rgba(56, 189, 248, 0.35);
}

.panel-sub {
  margin: 4px 0 0;
  font-size: 10px;
  color: var(--text-dim);
  line-height: 1.35;
}

.panel-empty {
  padding: 20px 14px;
  font-size: 12px;
  color: var(--text-dim);
}

.table-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 0 8px;
}

.agent-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}

.agent-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  text-align: left;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: #64748b;
  padding: 8px 10px;
  background: rgba(8, 12, 22, 0.98);
  border-bottom: 1px solid var(--border-subtle);
}

.agent-table th.num,
.agent-table td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.agent-table tbody tr {
  cursor: pointer;
  transition: background 0.12s;
}

.agent-table tbody tr:hover {
  background: rgba(79, 70, 229, 0.08);
}

.agent-table tbody tr.selected {
  background: rgba(79, 70, 229, 0.16);
}

.agent-table td {
  padding: 8px 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  vertical-align: middle;
  color: #cbd5e1;
}

.name-cell {
  max-width: 160px;
}

.agent-name {
  display: block;
  font-weight: 700;
  color: #f1f5f9;
  line-height: 1.25;
  word-break: break-word;
}

.camp-cell {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #64748b;
  font-size: 10px;
}

.cost {
  color: #c084fc;
  font-weight: 700;
}

.state-badge {
  display: inline-block;
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.03em;
  padding: 2px 5px;
  border-radius: 4px;
  white-space: nowrap;
}

.state-running { background: rgba(16, 185, 129, 0.2); color: #34d399; }
.state-review { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }
.state-waiting { background: rgba(56, 189, 248, 0.2); color: #38bdf8; }
.state-bad { background: rgba(239, 68, 68, 0.2); color: #f87171; }
.state-done { background: rgba(148, 163, 184, 0.2); color: #94a3b8; }
.state-idle { background: rgba(100, 116, 139, 0.25); color: #94a3b8; }
</style>
