<script setup lang="ts">
/**
 * Primary live telemetry: Profound API → ingested signals in Postgres.
 * This is NOT Mixpanel product analytics.
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'

interface ProfoundLive {
  status?: string
  source?: string
  source_mode?: string
  signal_count?: number
  last_signal_at?: string | null
  metrics?: Record<string, number | null>
  delta_7d_pp?: { visibility?: number | null; citation_share?: number | null }
  delta_basis?: string
  campaign_effectiveness?: string
  attribution_note?: string
  organization_id?: string
}

const live = ref<ProfoundLive | null>(null)
const isLoading = ref(true)
const isSyncing = ref(false)
const error = ref<string | null>(null)
const lastPolledAt = ref<Date | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | null = null

const isLive = computed(() => live.value?.status === 'OK' && (live.value?.signal_count ?? 0) > 0)

const badgeLabel = computed(() => {
  if (isLoading.value && !live.value) return 'Checking Profound…'
  if (error.value) return 'Profound unreachable'
  if (isLive.value) return '● LIVE PROFOUND'
  if (live.value?.status === 'NO_SIGNALS') return '○ No signals yet'
  if (live.value?.status === 'NO_ORG') return '○ Org not mapped'
  return '○ Profound idle'
})

const badgeClass = computed(() => {
  if (isLive.value) return 'badge-live'
  if (error.value) return 'badge-error'
  return 'badge-off'
})

function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  if (Math.abs(v) <= 1.5) return `${(v * 100).toFixed(2)}%`
  return `${v.toFixed(2)}%`
}

function fmtDelta(v: number | null | undefined): string {
  if (v == null) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}pp`
}

async function fetchLive() {
  try {
    live.value = await apiFetch<ProfoundLive>('/api/integrations/profound/live')
    error.value = null
  } catch (e: unknown) {
    const err = e as { message?: string }
    error.value = err?.message ?? 'fetch failed'
  } finally {
    isLoading.value = false
    lastPolledAt.value = new Date()
  }
}

async function syncFromProfound() {
  isSyncing.value = true
  try {
    const res = await apiFetch<{ profound_live?: ProfoundLive }>('/api/integrations/profound/refresh', {
      method: 'POST',
    })
    if (res.profound_live) live.value = res.profound_live
    error.value = null
  } catch (e: unknown) {
    const err = e as { message?: string }
    error.value = err?.message ?? 'sync failed'
  } finally {
    isSyncing.value = false
    lastPolledAt.value = new Date()
  }
}

function scheduleNext() {
  pollTimer = setTimeout(async () => {
    await fetchLive()
    scheduleNext()
  }, 60_000)
}

onMounted(async () => {
  await fetchLive()
  scheduleNext()
})

onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer)
})
</script>

<template>
  <section class="profound-rail" aria-label="Profound AI Discovery Live Feed">
    <div class="rail-header">
      <div class="rail-title-group">
        <span :class="['badge', badgeClass]" role="status">{{ badgeLabel }}</span>
        <span class="rail-subtitle">api.tryprofound.com → ingested signals</span>
      </div>
      <div class="rail-actions">
        <button type="button" class="sync-btn" :disabled="isSyncing" @click="syncFromProfound">
          {{ isSyncing ? 'Syncing…' : 'Sync Profound' }}
        </button>
      </div>
    </div>

    <div v-if="isLive" class="metrics-grid">
      <div class="metric">
        <span class="metric-label">Signals</span>
        <span class="metric-val">{{ live?.signal_count?.toLocaleString() }}</span>
      </div>
      <div class="metric">
        <span class="metric-label">Visibility</span>
        <span class="metric-val">{{ fmtPct(live?.metrics?.visibility ?? null) }}</span>
      </div>
      <div class="metric">
        <span class="metric-label">Citation share</span>
        <span class="metric-val">{{ fmtPct(live?.metrics?.citation_share ?? null) }}</span>
      </div>
      <div class="metric">
        <span class="metric-label">Δ7d visibility</span>
        <span class="metric-val">{{ fmtDelta(live?.delta_7d_pp?.visibility ?? null) }}</span>
      </div>
      <div class="metric">
        <span class="metric-label">Δ7d citation</span>
        <span class="metric-val">{{ fmtDelta(live?.delta_7d_pp?.citation_share ?? null) }}</span>
      </div>
      <div class="metric">
        <span class="metric-label">Campaign pulse</span>
        <span :class="['effect-pill', `effect-${(live?.campaign_effectiveness || 'NO_DATA').toLowerCase()}`]">
          {{ live?.campaign_effectiveness }}
        </span>
      </div>
    </div>

    <p v-else-if="error" class="rail-note error">{{ error }}</p>
    <p v-else class="rail-note">
      Live AI discovery metrics come from the <strong>Profound API</strong>. Click
      <strong>Sync Profound</strong> to pull the latest signals into the backend.
    </p>

    <p v-if="live?.attribution_note" class="rail-footnote">{{ live.attribution_note }}</p>
  </section>
</template>

<style scoped>
.profound-rail {
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(16, 185, 129, 0.25);
  border-radius: 10px;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  backdrop-filter: blur(8px);
}
.rail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.rail-title-group {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.rail-subtitle {
  font-size: 11px;
  color: #64748b;
  letter-spacing: 0.02em;
}
.badge {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 3px 8px;
  border-radius: 5px;
}
.badge-live {
  background: rgba(16, 185, 129, 0.15);
  color: #34d399;
  border: 1px solid rgba(52, 211, 153, 0.35);
}
.badge-off {
  background: rgba(100, 116, 139, 0.15);
  color: #64748b;
  border: 1px solid rgba(100, 116, 139, 0.3);
}
.badge-error {
  background: rgba(239, 68, 68, 0.12);
  color: #f87171;
  border: 1px solid rgba(248, 113, 113, 0.3);
}
.sync-btn {
  font-size: 11px;
  font-weight: 700;
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid rgba(52, 211, 153, 0.4);
  background: rgba(16, 185, 129, 0.1);
  color: #34d399;
  cursor: pointer;
}
.sync-btn:disabled {
  opacity: 0.6;
  cursor: wait;
}
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
}
.metric {
  background: rgba(0, 0, 0, 0.25);
  border-radius: 8px;
  padding: 8px 10px;
}
.metric-label {
  display: block;
  font-size: 10px;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.metric-val {
  font-size: 14px;
  font-weight: 700;
  color: #e2e8f0;
  font-family: ui-monospace, monospace;
}
.effect-pill {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}
.effect-working { background: rgba(16, 185, 129, 0.15); color: #34d399; }
.effect-stable { background: rgba(100, 116, 139, 0.15); color: #94a3b8; }
.effect-watch { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.effect-at_risk { background: rgba(239, 68, 68, 0.15); color: #f87171; }
.effect-no_data { background: rgba(99, 102, 241, 0.12); color: #818cf8; }
.rail-note {
  font-size: 12px;
  color: #94a3b8;
  margin: 0;
  line-height: 1.5;
}
.rail-note.error { color: #fca5a5; }
.rail-footnote {
  font-size: 11px;
  color: #475569;
  margin: 0;
}
</style>
