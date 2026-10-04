<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', campaign: any): void
}>()

const apiBase = useApiBase()

const form = ref({
  name: '',
  objective: '',
  budget: 25000,
  primary_metric: 'visibility',
  channels: ['Search LLMs', 'Developer Docs'],
  agents: [] as string[],
  date_range: 'Oct 2026 – Dec 2026',
  owner: 'Sarah Jenkins (PMM Lead)',
  run_profound_agents: true,
})

const isSubmitting = ref(false)
const errorMessage = ref<string | null>(null)

const availableChannels = [
  'Search LLMs',
  'Developer Docs',
  'LinkedIn',
  'Trust Portal',
  'YouTube & Video'
]

const FALLBACK_AGENTS = [
  { id: 'agt-citation-recovery', label: 'Citation Recovery Agent (local preset)' },
  { id: 'agt-claim-verifier', label: 'Technical Claim Verification Agent (local preset)' },
  { id: 'agt-competitive-copilot', label: 'Competitive Differentiation Copilot (local preset)' },
  { id: 'agt-token-router', label: 'Model API Token Router (local preset)' },
]

const availableAgents = ref([...FALLBACK_AGENTS])
const agentsLoading = ref(false)
const generationEnabled = ref(false)
const agentsStatus = ref<string | null>(null)

async function loadProfoundAgents() {
  agentsLoading.value = true
  agentsStatus.value = null
  try {
    const res = await $fetch<{
      status?: string
      agents?: Array<{ id: string; name: string; status?: string }>
      generation_enabled?: boolean
      message?: string
    }>(`${apiBase}/api/integrations/profound/agents`)
    generationEnabled.value = Boolean(res.generation_enabled)
    const live = (res.agents || []).map(a => ({
      id: a.id,
      label: `${a.name}${a.status ? ` · ${a.status}` : ''}`,
    }))
    if (live.length) {
      availableAgents.value = live
      form.value.agents = live.slice(0, 2).map(a => a.id)
    } else {
      availableAgents.value = [...FALLBACK_AGENTS]
      if (!form.value.agents.length) {
        form.value.agents = ['agt-citation-recovery', 'agt-claim-verifier']
      }
      agentsStatus.value = res.message || 'No published Profound agents yet — using local presets; runs will auto-select when agents exist.'
    }
  } catch {
    availableAgents.value = [...FALLBACK_AGENTS]
    if (!form.value.agents.length) {
      form.value.agents = ['agt-citation-recovery', 'agt-claim-verifier']
    }
  } finally {
    agentsLoading.value = false
  }
}

onMounted(() => {
  if (props.open) loadProfoundAgents()
})

watch(() => props.open, (open) => {
  if (open) loadProfoundAgents()
})

function toggleChannel(ch: string) {
  const idx = form.value.channels.indexOf(ch)
  if (idx >= 0) {
    if (form.value.channels.length > 1) form.value.channels.splice(idx, 1)
  } else {
    form.value.channels.push(ch)
  }
}

function toggleAgent(id: string) {
  const idx = form.value.agents.indexOf(id)
  if (idx >= 0) {
    if (form.value.agents.length > 1) form.value.agents.splice(idx, 1)
  } else {
    form.value.agents.push(id)
  }
}

async function handleSubmit() {
  if (!form.value.name.trim()) {
    errorMessage.value = 'Campaign name is required'
    return
  }

  isSubmitting.value = true
  errorMessage.value = null

  try {
    const url = `${apiBase}/api/campaigns`
    const res = await $fetch(url, {
      method: 'POST',
      body: {
        name: form.value.name.trim(),
        objective: form.value.objective.trim() || undefined,
        budget: Number(form.value.budget) || 10000,
        channels: form.value.channels,
        primary_channel: form.value.channels[0],
        agents: form.value.agents.length ? form.value.agents : ['agt-citation-recovery'],
        primary_metric: form.value.primary_metric,
        date_range: form.value.date_range,
        owner: form.value.owner,
        run_profound_agents: form.value.run_profound_agents,
      }
    })

    emit('created', res)
    emit('close')
    // Reset form
    form.value.name = ''
    form.value.objective = ''
  } catch (err: any) {
    errorMessage.value = err?.data?.detail || err?.message || 'Failed to create campaign'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div v-if="open" class="drawer-overlay" @click.self="$emit('close')">
    <div class="drawer-panel" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
      <header class="drawer-header">
        <div class="header-titles">
          <h2 id="drawer-title" class="drawer-title">New Marketing Campaign</h2>
          <span class="drawer-subtitle">Initialize an autonomous campaign initiative in the Control Plane</span>
        </div>
        <button type="button" class="btn-close" aria-label="Close" @click="$emit('close')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </header>

      <form class="drawer-body" @submit.prevent="handleSubmit">
        <div v-if="errorMessage" class="error-banner">
          {{ errorMessage }}
        </div>

        <div class="form-group">
          <label for="cmp-name" class="form-label">Campaign Name *</label>
          <input
            id="cmp-name"
            v-model="form.name"
            type="text"
            required
            placeholder="e.g. Q4 Autonomous AEO Expansion"
            class="input-text"
          >
        </div>

        <div class="form-group">
          <label for="cmp-objective" class="form-label">Core Objective</label>
          <textarea
            id="cmp-objective"
            v-model="form.objective"
            rows="2"
            placeholder="e.g. Displace stale competitor citations and establish verified SAML/SCIM schema authority"
            class="input-textarea"
          />
        </div>

        <div class="form-row">
          <div class="form-group flex-1">
            <label for="cmp-budget" class="form-label">Budget (USD)</label>
            <input
              id="cmp-budget"
              v-model.number="form.budget"
              type="number"
              min="0"
              step="500"
              class="input-text"
            >
          </div>

          <div class="form-group flex-1">
            <label for="cmp-metric" class="form-label">Primary Metric</label>
            <select id="cmp-metric" v-model="form.primary_metric" class="input-select">
              <option value="visibility">AI Search Visibility (Profound)</option>
              <option value="citations">Citation Share & Mentions</option>
              <option value="shortlists">Buyer Agent Shortlists (Muse)</option>
              <option value="pipeline">Attributed Pipeline Revenue</option>
            </select>
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">Distribution Channels</label>
          <div class="chips-container">
            <button
              v-for="ch in availableChannels"
              :key="ch"
              type="button"
              :class="['chip-btn', { active: form.channels.includes(ch) }]"
              @click="toggleChannel(ch)"
            >
              {{ ch }}
            </button>
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">Assigned Autonomous Agents</label>
          <p v-if="agentsLoading" class="hint-text">Loading Profound agents…</p>
          <p v-else-if="agentsStatus" class="hint-text">{{ agentsStatus }}</p>
          <label v-if="generationEnabled" class="checkbox-row">
            <input v-model="form.run_profound_agents" type="checkbox">
            Run Profound agents in the background after create (API key)
          </label>
          <div class="agents-list">
            <div
              v-for="agt in availableAgents"
              :key="agt.id"
              :class="['agent-choice-card', { active: form.agents.includes(agt.id) }]"
              @click="toggleAgent(agt.id)"
            >
              <div class="choice-checkbox">
                <input
                  type="checkbox"
                  :checked="form.agents.includes(agt.id)"
                  tabindex="-1"
                >
              </div>
              <div class="choice-info">
                <span class="choice-name">{{ agt.label }}</span>
                <span class="choice-id">{{ agt.id }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="form-row">
          <div class="form-group flex-1">
            <label for="cmp-range" class="form-label">Date Range</label>
            <input
              id="cmp-range"
              v-model="form.date_range"
              type="text"
              class="input-text"
            >
          </div>

          <div class="form-group flex-1">
            <label for="cmp-owner" class="form-label">Initiative Owner</label>
            <input
              id="cmp-owner"
              v-model="form.owner"
              type="text"
              class="input-text"
            >
          </div>
        </div>

        <footer class="drawer-footer">
          <button type="button" class="btn-cancel" @click="$emit('close')">
            Cancel
          </button>
          <button type="submit" class="btn-submit" :disabled="isSubmitting">
            <span v-if="isSubmitting">Creating...</span>
            <span v-else>Launch Campaign</span>
          </button>
        </footer>
      </form>
    </div>
  </div>
</template>

<style scoped>
.drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(4, 7, 15, 0.75);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  z-index: var(--z-modal, 50);
  display: flex;
  justify-content: flex-end;
}

.drawer-panel {
  width: min(540px, 94vw);
  height: 100%;
  background: var(--surface-card, #0d1322);
  border-left: 1px solid var(--border);
  box-shadow: -10px 0 30px rgba(0, 0, 0, 0.6);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: slide-in 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes slide-in {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}

.drawer-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 24px;
  border-bottom: 1px solid var(--border);
  background: rgba(15, 21, 38, 0.5);
}

.drawer-title {
  font-size: 18px;
  font-weight: 700;
  color: #ffffff;
  margin: 0;
}

.drawer-subtitle {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: 4px;
  display: block;
}

.btn-close {
  background: transparent;
  border: none;
  color: var(--text-faint);
  padding: 4px;
  cursor: pointer;
}
.btn-close:hover {
  color: var(--text-primary);
}

.drawer-body {
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  overflow-y: auto;
  flex: 1;
}

.error-banner {
  background: var(--bad-soft);
  border: 1px solid var(--bad);
  color: #fff;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-row {
  display: flex;
  gap: 14px;
}

.flex-1 { flex: 1; }

.form-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.input-text, .input-textarea, .input-select {
  background: rgba(10, 14, 26, 0.9);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  color: var(--text-primary);
  font-size: 13px;
  width: 100%;
}
.input-text:focus, .input-textarea:focus, .input-select:focus {
  border-color: var(--primary);
  outline: none;
}

.chips-container {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip-btn {
  background: rgba(15, 21, 38, 0.7);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 5px 12px;
  font-size: 12px;
  color: var(--text-dim);
  cursor: pointer;
  transition: all 0.15s ease;
}
.chip-btn:hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
}
.chip-btn.active {
  background: var(--primary-soft);
  border-color: var(--primary);
  color: #fff;
  font-weight: 600;
}

.hint-text {
  font-size: 12px;
  color: var(--text-dim);
  margin: 0 0 8px;
}

.checkbox-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-dim);
  margin-bottom: 10px;
  cursor: pointer;
}

.agents-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.agent-choice-card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: rgba(10, 14, 26, 0.6);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.agent-choice-card:hover {
  border-color: var(--border-strong);
}
.agent-choice-card.active {
  background: rgba(99, 102, 241, 0.1);
  border-color: var(--primary);
}

.choice-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  display: block;
}
.choice-id {
  font-size: 11px;
  font-family: monospace;
  color: var(--text-faint);
}

.drawer-footer {
  margin-top: auto;
  padding-top: 18px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.btn-cancel {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-dim);
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  font-weight: 500;
}
.btn-submit {
  background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #ffffff;
  padding: 8px 18px;
  border-radius: var(--radius-sm);
  font-weight: 600;
  box-shadow: 0 2px 10px var(--primary-glow);
}
.btn-submit:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
