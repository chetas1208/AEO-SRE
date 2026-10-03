<script setup lang="ts">
const route = useRoute()
const live = useLiveSystemStore()
const sel = useIncidentSelectionStore()
defineEmits<{ toggleNav: [] }>()
const searchEl = ref<HTMLInputElement | null>(null)

const headerMeta = computed(() => {
  const p = route.path
  if (p.startsWith('/experiments')) {
    return {
      title: 'Experiments',
      subtitle: 'Every intervention becomes evidence for the next decision.'
    }
  }
  if (p.startsWith('/settings')) {
    return {
      title: 'Settings',
      subtitle: 'System configuration & platform integration capabilities.'
    }
  }
  return {
    title: 'Incidents',
    subtitle: 'High-signal changes in your AI discovery performance'
  }
})

const pill = computed(() => {
  if (live.liveState === 'live') {
    const age = live.heartbeatAgeSeconds
    return { label: age != null ? `Live · ${age}s` : 'Live', tone: 'good', active: true }
  }
  if (live.liveState === 'reconnecting') {
    return { label: 'Reconnecting', tone: 'warn', active: false }
  }
  if (live.liveState === 'degraded') {
    return { label: 'Degraded', tone: 'warn', active: false }
  }
  return { label: 'Offline', tone: 'muted', active: false }
})

function onKey(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null
  if (e.key === '/' && t && !['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) {
    e.preventDefault()
    searchEl.value?.focus()
  }
}
onMounted(() => { window.addEventListener('keydown', onKey); live.startGlobalStream() })
onBeforeUnmount(() => { window.removeEventListener('keydown', onKey); live.stopGlobalStream() })
</script>

<template>
  <header class="topbar">
    <button class="nav-toggle" type="button" aria-label="Toggle navigation" @click="$emit('toggleNav')">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <line x1="3" y1="12" x2="21" y2="12" />
        <line x1="3" y1="6" x2="21" y2="6" />
        <line x1="3" y1="18" x2="21" y2="18" />
      </svg>
    </button>

    <div class="title-block">
      <h1 class="title">{{ headerMeta.title }}</h1>
      <span class="subtitle">{{ headerMeta.subtitle }}</span>
    </div>

    <div class="search-wrap">
      <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <circle cx="11" cy="11" r="8" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
      <label class="sr-only" for="global-search">Search</label>
      <input
        id="global-search"
        ref="searchEl"
        v-model="sel.query"
        type="search"
        placeholder="Search incidents, topics, competitors…"
      >
      <kbd class="kbd-hint">/</kbd>
    </div>

    <span :class="['live-pill', `tone-${pill.tone}`, { active: pill.active }]" role="status" data-testid="live-pill" :data-state="live.liveState" :title="live.lastHeartbeatAt ? `Last heartbeat ${live.heartbeatAgeSeconds}s ago` : 'No heartbeat received'">
      <span class="pulse-dot" aria-hidden="true" />
      <span>{{ pill.label }}</span>
    </span>

    <div class="date-range-wrap">
      <svg class="calendar-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
        <line x1="16" y1="2" x2="16" y2="6" />
        <line x1="8" y1="2" x2="8" y2="6" />
        <line x1="3" y1="10" x2="21" y2="10" />
      </svg>
      <label class="sr-only" for="range">Time range</label>
      <select id="range" v-model="sel.range" class="date-select">
        <option value="24h">Last 24 hours</option>
        <option value="7d">Last 7 days</option>
        <option value="30d">Last 30 days</option>
      </select>
    </div>
  </header>
</template>

<style scoped>
.live-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(13, 19, 34, 0.8);
  border: 1px solid var(--border);
  color: var(--text-dim);
}
.live-pill.tone-good {
  background: rgba(16, 185, 129, 0.12);
  border-color: rgba(16, 185, 129, 0.35);
  color: #34d399;
}
.live-pill .pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.live-pill.active .pulse-dot {
  animation: pulse-glow 2s infinite ease-in-out;
  box-shadow: 0 0 8px rgba(16, 185, 129, 0.5);
}
.date-range-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.calendar-icon {
  position: absolute;
  left: 10px;
  color: var(--text-faint);
  pointer-events: none;
}
.date-select {
  padding-left: 30px;
  background: rgba(15, 21, 38, 0.85);
  font-size: 13px;
  font-weight: 500;
}
</style>
