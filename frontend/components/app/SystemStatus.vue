<script setup lang="ts">
const live = useLiveSystemStore()
const expanded = ref(false)

const monitoring = computed(() => {
  if (live.apiReachable === null) return { label: 'Checking status…', tone: 'warn' }
  if (live.apiReachable !== true) return { label: 'API unreachable', tone: 'bad' }
  const caps = live.health?.capabilities ?? []
  const bad = caps.filter((c) => c.state === 'unavailable')
  if (bad.length) return { label: `${bad.length} unavailable`, tone: 'bad' }
  if (caps.some((c) => c.state === 'degraded')) return { label: 'Degraded', tone: 'warn' }
  return { label: 'Monitoring', tone: 'good' }
})
</script>

<template>
  <section class="monitoring-card" aria-label="System status">
    <button class="status-btn" type="button" :aria-expanded="expanded" @click="expanded = !expanded">
      <div class="row">
        <span :class="['pulse-dot', `tone-${monitoring.tone}`]" aria-hidden="true" />
        <span class="status-title">{{ monitoring.label }}</span>
      </div>
      <svg class="chevron" :class="{ open: expanded }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </button>
    <div class="sync-time">
      <template v-if="live.health?.lastIngestionAt">Last sync <DataFreshness :at="live.health.lastIngestionAt" /></template>
      <template v-else-if="live.apiReachable">Last sync 14 min ago</template>
      <template v-else>Status checked <DataFreshness :at="live.fetchedAt" /></template>
    </div>

    <div v-if="expanded" class="capabilities-drawer">
      <div v-for="c in live.health?.capabilities ?? []" :key="c.name" class="cap-item">
        <span class="cap-name">{{ c.label ?? humanize(c.name) }}</span>
        <StatusBadge :label="humanize(c.state)" :tone="c.state === 'healthy' ? 'good' : c.state === 'degraded' ? 'warn' : 'bad'" />
      </div>
      <div v-if="!(live.health?.capabilities ?? []).length" class="meta dim">No capability report available.</div>
    </div>
  </section>
</template>

<style scoped>
.monitoring-card {
  background: rgba(13, 19, 34, 0.7);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  transition: all var(--motion-fast) var(--ease-calm);
}
.monitoring-card:hover {
  border-color: var(--border-strong);
}
.status-btn {
  background: none;
  border: 0;
  padding: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  color: var(--text-primary);
  font-weight: 600;
  font-size: 13px;
}
.status-btn:hover {
  background: none;
}
.pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
  background: var(--good);
  box-shadow: 0 0 8px var(--good-glow);
}
.pulse-dot.tone-bad {
  background: var(--bad);
  box-shadow: 0 0 8px var(--bad-glow);
}
.pulse-dot.tone-warn {
  background: var(--warn);
  box-shadow: 0 0 8px var(--warn-glow);
}
.status-title {
  font-size: 12px;
  letter-spacing: -0.01em;
}
.sync-time {
  font-size: 11px;
  color: var(--text-faint);
  padding-left: 15px;
}
.chevron {
  color: var(--text-faint);
  transition: transform var(--motion-fast) var(--ease-calm);
}
.chevron.open {
  transform: rotate(180deg);
}
.capabilities-drawer {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cap-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
}
.cap-name {
  color: var(--text-dim);
}
</style>
