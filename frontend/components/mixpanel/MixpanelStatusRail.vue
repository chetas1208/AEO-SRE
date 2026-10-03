<script setup lang="ts">
/**
 * MixpanelStatusRail — shows the live Mixpanel integration status + recent events.
 * Source badge = "LIVE MIXPANEL" only when real events from the project exist.
 * Degrades gracefully to NOT_CONFIGURED / CONFIGURED_NO_DATA states.
 * Never fabricates events. Never calls its polling "streaming."
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'

interface MixpanelEvent {
  id: string
  event: string
  occurred_at: string | null
  campaign_id: string | null
  agent_id: string | null
  experiment_id: string | null
  correlation_method: string
  correlation_confidence: string
  tenant_resolution: string
  source: string
}

interface FeedResponse {
  badge: string
  detail: string
  configured: boolean
  last_sync_at: string | null
  lag_seconds: number | null
  events: MixpanelEvent[]
  total_in_window: number
  hours_back: number
  fetched_at: string
}

const feed = ref<FeedResponse | null>(null)
const isLoading = ref(true)
const error = ref<string | null>(null)
const lastPolledAt = ref<Date | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | null = null

const badge = computed(() => feed.value?.badge ?? 'NOT_CONFIGURED')
const detail = computed(() => feed.value?.detail ?? 'Checking...')
const events = computed(() => feed.value?.events ?? [])
const hasLiveEvents = computed(() => badge.value === 'LIVE MIXPANEL' && events.value.length > 0)

const badgeClass = computed(() => {
  switch (badge.value) {
    case 'LIVE MIXPANEL': return 'badge-live'
    case 'DEGRADED': return 'badge-degraded'
    case 'AUTH FAILED': return 'badge-error'
    case 'RATE LIMITED': return 'badge-warn'
    case 'CONFIGURED_NO_DATA': return 'badge-wait'
    default: return 'badge-off'
  }
})

const badgeLabel = computed(() => {
  switch (badge.value) {
    case 'LIVE MIXPANEL': return '● LIVE MIXPANEL'
    case 'DEGRADED': return '◐ DEGRADED'
    case 'AUTH FAILED': return '✕ AUTH FAILED'
    case 'RATE LIMITED': return '⚡ RATE LIMITED'
    case 'CONFIGURED_NO_DATA': return '◌ SYNCING'
    default: return '○ NOT CONFIGURED'
  }
})

const secondsSincePoll = computed(() => {
  if (!lastPolledAt.value) return null
  return Math.round((Date.now() - lastPolledAt.value.getTime()) / 1000)
})

function relativeTime(iso: string | null): string {
  if (!iso) return '—'
  const diff = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`
  return `${Math.round(diff / 3600)}h ago`
}

function correlationDot(method: string): string {
  switch (method) {
    case 'EXACT_ID': return '●'
    case 'SESSION': return '◉'
    case 'TEMPORAL': return '◎'
    case 'HEURISTIC': return '○'
    default: return '·'
  }
}

function confidenceClass(c: string): string {
  switch (c) {
    case 'HIGH': return 'conf-high'
    case 'MEDIUM': return 'conf-med'
    default: return 'conf-low'
  }
}

async function fetchFeed() {
  try {
    feed.value = await apiFetch<FeedResponse>('/api/integrations/mixpanel/live-feed', {
      query: { limit: 8, hours_back: 24 },
    })
    error.value = null
  } catch (e: unknown) {
    const err = e as { message?: string }
    error.value = err?.message ?? 'fetch failed'
  } finally {
    isLoading.value = false
    lastPolledAt.value = new Date()
  }
}

function scheduleNext() {
  // Poll every 60s — matches backend cron cadence. Not "streaming."
  pollTimer = setTimeout(async () => {
    await fetchFeed()
    scheduleNext()
  }, 60_000)
}

onMounted(async () => {
  await fetchFeed()
  scheduleNext()
})

onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer)
})
</script>

<template>
  <section class="mixpanel-rail" aria-label="Mixpanel Behavioral Event Feed">
    <!-- Header row -->
    <div class="rail-header">
      <div class="rail-title-group">
        <span :class="['badge', badgeClass]" role="status" :aria-label="badgeLabel">
          {{ badgeLabel }}
        </span>
        <span class="rail-detail">{{ detail }}</span>
      </div>
      <div class="rail-meta">
        <span v-if="secondsSincePoll !== null" class="sync-age">
          Polled {{ secondsSincePoll }}s ago · ~60s cadence
        </span>
        <span v-if="isLoading" class="sync-spinner" aria-label="Loading">⟳</span>
      </div>
    </div>

    <!-- NOT_CONFIGURED state: clear actionable guidance -->
    <div v-if="badge === 'NOT_CONFIGURED'" class="state-panel state-unconfigured">
      <div class="state-icon">⬡</div>
      <div class="state-body">
        <p class="state-title">Mixpanel not connected</p>
        <p class="state-desc">
          Add your Profound Mixpanel service account credentials to <code>.env</code> and restart the backend:
        </p>
        <pre class="state-code">MIXPANEL_ENABLED=true
MIXPANEL_PROJECT_ID=&lt;project-id&gt;
MIXPANEL_SERVICE_ACCOUNT_USERNAME=&lt;sa@….mixpanel.com&gt;
MIXPANEL_SERVICE_ACCOUNT_SECRET=&lt;secret&gt;</pre>
        <p class="state-footnote">
          Get credentials → Mixpanel → Organization Settings → Service Accounts
        </p>
      </div>
    </div>

    <!-- CONFIGURED_NO_DATA: configured but no events yet -->
    <div v-else-if="badge === 'CONFIGURED_NO_DATA'" class="state-panel state-waiting">
      <div class="state-icon spin-slow">◌</div>
      <div class="state-body">
        <p class="state-title">Connected · Waiting for first sync</p>
        <p class="state-desc">Worker runs every 60 seconds. Events will appear here automatically.</p>
        <p v-if="feed?.last_sync_at" class="state-footnote">Last cursor: {{ relativeTime(feed.last_sync_at) }}</p>
      </div>
    </div>

    <!-- LIVE MIXPANEL or DEGRADED: show real events table -->
    <div v-else-if="hasLiveEvents || badge === 'DEGRADED'" class="events-table-wrap">
      <p v-if="events.length === 0" class="no-events">No events in last 24h — project may be inactive.</p>
      <table v-else class="events-table" aria-label="Recent Mixpanel Events">
        <thead>
          <tr>
            <th>Event</th>
            <th>When</th>
            <th>Campaign</th>
            <th>Correlation</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ev in events" :key="ev.id" class="event-row">
            <td class="ev-name">{{ ev.event }}</td>
            <td class="ev-when">{{ relativeTime(ev.occurred_at) }}</td>
            <td class="ev-campaign">
              <span v-if="ev.campaign_id" class="campaign-chip">{{ ev.campaign_id.slice(0, 12) }}…</span>
              <span v-else class="no-link">—</span>
            </td>
            <td class="ev-corr">
              <span :title="ev.correlation_method" class="corr-dot">{{ correlationDot(ev.correlation_method) }}</span>
              {{ ev.correlation_method }}
            </td>
            <td class="ev-conf">
              <span :class="['conf-pill', confidenceClass(ev.correlation_confidence)]">
                {{ ev.correlation_confidence }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="table-footer">
        Showing {{ events.length }} of last 24h · Source: {{ feed?.total_in_window }} real MIXPANEL events in DB
      </p>
    </div>

    <!-- Error state -->
    <div v-else-if="error" class="state-panel state-error">
      <span class="state-icon">✕</span>
      <div class="state-body">
        <p class="state-title">Feed error</p>
        <p class="state-desc">{{ error }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.mixpanel-rail {
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(99, 102, 241, 0.2);
  border-radius: 10px;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  backdrop-filter: blur(8px);
}

/* Header */
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
.rail-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Badges */
.badge {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 3px 8px;
  border-radius: 5px;
  white-space: nowrap;
}
.badge-live   { background: rgba(16,185,129,0.15); color: #34d399; border: 1px solid rgba(52,211,153,0.35); }
.badge-off    { background: rgba(100,116,139,0.15); color: #64748b; border: 1px solid rgba(100,116,139,0.3); }
.badge-wait   { background: rgba(99,102,241,0.12); color: #818cf8; border: 1px solid rgba(129,140,248,0.3); }
.badge-degraded { background: rgba(245,158,11,0.12); color: #fbbf24; border: 1px solid rgba(251,191,36,0.3); }
.badge-error  { background: rgba(239,68,68,0.12); color: #f87171; border: 1px solid rgba(248,113,113,0.3); }
.badge-warn   { background: rgba(234,179,8,0.12); color: #facc15; border: 1px solid rgba(250,204,21,0.3); }

.rail-detail {
  font-size: 12px;
  color: #94a3b8;
}
.sync-age {
  font-size: 11px;
  color: #475569;
}
.sync-spinner {
  font-size: 13px;
  color: #6366f1;
  animation: spin 1s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* State panels */
.state-panel {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  padding: 12px;
  border-radius: 8px;
}
.state-unconfigured { background: rgba(99,102,241,0.06); border: 1px dashed rgba(99,102,241,0.2); }
.state-waiting { background: rgba(99,102,241,0.06); border: 1px dashed rgba(129,140,248,0.2); }
.state-error { background: rgba(239,68,68,0.06); border: 1px dashed rgba(248,113,113,0.2); }

.state-icon {
  font-size: 20px;
  opacity: 0.6;
  flex-shrink: 0;
  padding-top: 2px;
}
.spin-slow { animation: spin 3s linear infinite; }

.state-body { display: flex; flex-direction: column; gap: 6px; }
.state-title { font-size: 13px; font-weight: 700; color: #e2e8f0; margin: 0; }
.state-desc  { font-size: 12px; color: #94a3b8; margin: 0; line-height: 1.5; }
.state-footnote { font-size: 11px; color: #475569; margin: 0; }

.state-code {
  font-family: 'Fira Code', 'Cascadia Code', monospace;
  font-size: 11px;
  background: rgba(0,0,0,0.35);
  border: 1px solid rgba(99,102,241,0.2);
  border-radius: 5px;
  padding: 8px 10px;
  color: #a5b4fc;
  margin: 2px 0;
  white-space: pre;
  overflow-x: auto;
}
.state-code code { font-size: inherit; }

/* Events table */
.events-table-wrap { overflow-x: auto; }
.events-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.events-table th {
  text-align: left;
  color: #475569;
  font-weight: 600;
  padding: 4px 8px;
  border-bottom: 1px solid rgba(99,102,241,0.15);
  white-space: nowrap;
}
.event-row td {
  padding: 5px 8px;
  border-bottom: 1px solid rgba(255,255,255,0.04);
  color: #cbd5e1;
  vertical-align: middle;
}
.event-row:hover td { background: rgba(99,102,241,0.06); }
.ev-name { font-weight: 600; color: #e2e8f0; }
.ev-when { color: #64748b; white-space: nowrap; }
.campaign-chip {
  font-size: 10px;
  padding: 2px 6px;
  background: rgba(99,102,241,0.15);
  border-radius: 4px;
  color: #818cf8;
  font-family: monospace;
}
.no-link { color: #334155; }
.corr-dot { font-size: 10px; margin-right: 4px; }
.conf-pill {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}
.conf-high { background: rgba(16,185,129,0.15); color: #34d399; }
.conf-med  { background: rgba(245,158,11,0.15); color: #fbbf24; }
.conf-low  { background: rgba(100,116,139,0.12); color: #64748b; }

.no-events { color: #475569; font-size: 12px; padding: 8px 0; margin: 0; }
.table-footer {
  font-size: 11px;
  color: #334155;
  margin: 6px 0 0;
  text-align: right;
}
</style>
