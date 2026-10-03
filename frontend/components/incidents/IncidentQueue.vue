<script setup lang="ts">
import type { SeverityFilter } from '~/stores/incidentSelection'

const route = useRoute()
const live = useLiveSystemStore()
const sel = useIncidentSelectionStore()
const { data, pending, error, refresh } = useIncidents()

const tabs: Array<{ key: SeverityFilter; label: string }> = [
  { key: 'all', label: 'All' }, { key: 'critical', label: 'Critical' }, { key: 'high', label: 'High' }, { key: 'medium', label: 'Medium' }
]

function count(key: SeverityFilter): number | null {
  const d = data.value
  if (!d) return null
  const fromApi = d.counts.bySeverity[key]
  if (fromApi != null) return fromApi
  return key === 'all' ? d.total : d.items.filter((i) => i.severity === key).length
}

const profound = computed(() => live.health?.capabilities.find((c) => c.name === 'profound') ?? null)

const emptyCopy = computed(() => {
  if (data.value?.items.length) return { title: 'No incidents match the current filter.', lines: [] as string[] }
  const state = profound.value?.state
  if (state === 'unavailable') {
    return {
      title: 'Profound is not pulling observations.',
      lines: [profound.value?.detail || 'The Profound connector is unavailable.', 'No observations are invented while it is unavailable.']
    }
  }
  if (!live.health?.lastIngestionAt) {
    const next = live.health?.nextIngestionAt
    return {
      title: 'Waiting for the first Profound sync.',
      lines: [next ? `Next scheduled ingest ${relativeTime(next)}.` : 'A sync runs four times a day, and immediately when an organization is created.']
    }
  }
  return {
    title: 'No actionable incidents detected.',
    lines: ['Monitoring is active.', `Last sync: ${relativeTime(live.health.lastIngestionAt)}`]
  }
})
const filtered = computed(() => {
  const q = sel.query.trim().toLowerCase()
  return (data.value?.items ?? []).filter((i) => {
    if (sel.severity !== 'all' && i.severity !== sel.severity) return false
    if (!q) return true
    return [i.title, i.topic, i.contextLabel, i.number != null ? `#${i.number}` : '', i.id].some((s) => s && String(s).toLowerCase().includes(q))
  })
})
watch(filtered, (items) => sel.setOrder(items.map((i) => i.id)), { immediate: true })
watch(() => route.params.id, (id) => sel.select(typeof id === 'string' ? id : null), { immediate: true })

function onKey(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null
  if (t && ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) return
  if (document.querySelector('dialog[open]')) return
  if (e.key === 'j' || e.key === 'k') {
    const id = sel.step(e.key === 'j' ? 1 : -1)
    if (id) navigateTo(`/incidents/${id}`)
  }
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <section class="queue stack sm" aria-label="Incident queue">
    <div class="row spread">
      <h2>Incident queue</h2>
      <DataFreshness v-if="data && live.health?.lastIngestionAt" :at="live.health.lastIngestionAt" prefix="Synced " />
    </div>
    <div class="tabs" role="tablist" aria-label="Severity filter">
      <button v-for="t in tabs" :key="t.key" type="button" role="tab" :aria-selected="sel.severity === t.key" @click="sel.severity = t.key">
        {{ t.label }}<span v-if="count(t.key) != null" class="faint"> {{ count(t.key) }}</span>
      </button>
    </div>
    <LoadingState v-if="pending && !data" message="Loading incidents…" />
    <ErrorState v-else-if="error && !data" :error="error" surface="Incident queue" @retry="refresh()" />
    <template v-else-if="data">
      <ErrorState v-if="error" :error="error" surface="Incident queue refresh" note="Showing the last successfully loaded incidents." @retry="refresh()" />
      <EmptyState
        v-if="!filtered.length"
        :title="emptyCopy.title"
        :lines="emptyCopy.lines"
      />
      <ul v-else class="queue-list">
        <li v-for="i in filtered" :key="i.id"><IncidentCard :incident="i" :selected="sel.selectedId === i.id" /></li>
      </ul>
    </template>
    <LoadingState v-else message="Loading incidents…" />
  </section>
</template>
