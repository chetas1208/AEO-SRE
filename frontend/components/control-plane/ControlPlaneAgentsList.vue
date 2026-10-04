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

function taskOf(a: AgentActivity): string {
  return a.currentTask ?? a.current_task ?? '—'
}

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

function formatState(state: string): string {
  return state.replace(/_/g, ' ')
}

function agentModelCost(a: AgentActivity): number {
  return Number(a.modelCost ?? a.model_cost ?? 0)
}

function formatCost(v: number): string {
  if (!v) return '$0.00'
  return `$${v.toFixed(2)}`
}
</script>

<template>
  <aside class="agents-panel" aria-label="Active agents">
    <header class="panel-header">
      <div class="header-title">
        <h2 class="panel-title">Agents</h2>
        <span class="count-pill">{{ agents.length }}</span>
      </div>
      <p class="panel-sub">
        Profound &amp; control-plane registry — select to highlight on the graph
      </p>
    </header>

    <div v-if="loading && !agents.length" class="panel-empty">
      Loading agents…
    </div>
    <div v-else-if="!agents.length" class="panel-empty">
      No agents in the live registry yet. Bootstrap agents from Campaigns or refresh after Profound publish.
    </div>

    <ul v-else class="agent-list" role="list">
      <li v-for="agent in sortedAgents" :key="agent.id">
        <button
          type="button"
          class="agent-row"
          :class="{ selected: selectedAgentId === agent.id }"
          @click="emit('select', agent)"
        >
          <div class="row-top">
            <span class="agent-name">{{ agent.name }}</span>
            <span :class="['state-badge', stateClass(agent.state)]">{{ formatState(agent.state) }}</span>
          </div>
          <p class="agent-role">{{ agent.role }}</p>
          <p class="agent-task">{{ taskOf(agent) }}</p>
          <div class="row-meta">
            <span class="meta-item">{{ campaignOf(agent) }}</span>
            <span v-if="agent.runs" class="meta-item">{{ agent.runs }} run{{ agent.runs === 1 ? '' : 's' }}</span>
            <span class="meta-item meta-cost">{{ formatCost(agentModelCost(agent)) }} model</span>
          </div>
        </button>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.agents-panel {
  display: flex;
  flex-direction: column;
  background: rgba(13, 19, 34, 0.85);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 14px);
  overflow: hidden;
  height: calc(100vh - 520px);
  min-height: 420px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.35);
}

.panel-header {
  padding: 14px 16px 12px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel-title {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: #f8fafc;
  letter-spacing: -0.01em;
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
  margin: 6px 0 0;
  font-size: 11px;
  line-height: 1.4;
  color: var(--text-dim);
}

.panel-empty {
  padding: 24px 16px;
  font-size: 12px;
  color: var(--text-dim);
  line-height: 1.5;
}

.agent-list {
  list-style: none;
  margin: 0;
  padding: 8px;
  overflow-y: auto;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.agent-row {
  width: 100%;
  text-align: left;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  background: rgba(15, 21, 38, 0.6);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.agent-row:hover {
  background: var(--surface-hover);
  border-color: var(--border);
}

.agent-row.selected {
  border-color: rgba(99, 102, 241, 0.55);
  background: rgba(79, 70, 229, 0.12);
}

.row-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.agent-name {
  font-size: 12px;
  font-weight: 700;
  color: #f1f5f9;
  line-height: 1.3;
}

.state-badge {
  flex-shrink: 0;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 2px 6px;
  border-radius: 4px;
  text-transform: uppercase;
}

.state-running {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
}
.state-review {
  background: rgba(245, 158, 11, 0.2);
  color: #fbbf24;
}
.state-waiting {
  background: rgba(56, 189, 248, 0.2);
  color: #38bdf8;
}
.state-bad {
  background: rgba(239, 68, 68, 0.2);
  color: #f87171;
}
.state-done {
  background: rgba(148, 163, 184, 0.2);
  color: #94a3b8;
}
.state-idle {
  background: rgba(100, 116, 139, 0.25);
  color: #94a3b8;
}

.agent-role {
  margin: 4px 0 0;
  font-size: 10px;
  color: #64748b;
}

.agent-task {
  margin: 6px 0 0;
  font-size: 11px;
  color: var(--text-dim);
  line-height: 1.35;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.row-meta {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  font-size: 10px;
  color: #64748b;
}

.meta-item {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta-cost {
  color: #c084fc;
  font-weight: 700;
}

@media (max-width: 1100px) {
  .agents-panel {
    height: auto;
    min-height: 280px;
    max-height: 360px;
  }
}
</style>
